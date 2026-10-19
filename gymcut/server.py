"""FastAPI app: REST API, media streaming, live WebSockets and the web UI."""
from __future__ import annotations

import asyncio
import gzip
import json
import os
import secrets
import struct
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import __version__
from . import library as lib
from .certs import lan_ips
from .config import EXPORTS_DIR, FFMPEG, FFPROBE, WEB_DIR, settings
from .jobs import manager as jobs
from .live import manager as live
from .media import nvenc_available
from .pose import KNOWN_MODELS, gpu_info

app = FastAPI(title="GymCut", version=__version__, docs_url="/api/docs", redoc_url=None)
RUNTIME = {"http_port": 8000, "https_port": 8443, "lan": True}


def access_key() -> str:
    k = settings.get().get("access_key")
    if not k:
        k = secrets.token_urlsafe(9)
        settings.update({"access_key": k})
    return k


def _is_local(host: str | None) -> bool:
    return host in ("127.0.0.1", "::1", "localhost", None)


_PROXY_HEADERS = ("cf-connecting-ip", "cf-ray", "x-forwarded-for", "x-real-ip", "forwarded")


def _from_this_pc(conn) -> bool:
    """True only for the browser on this PC. Internet requests come through the Cloudflare tunnel,
    which connects from 127.0.0.1 - its forwarding headers mark them as remote (access key needed)."""
    host = conn.client.host if conn.client else None
    return _is_local(host) and not any(h in conn.headers for h in _PROXY_HEADERS)


@app.middleware("http")
async def no_stale_assets(request: Request, call_next):
    """Always revalidate UI files (ETag makes it cheap) so updates show up immediately."""
    resp = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path == "/":
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.middleware("http")
async def guard(request: Request, call_next):
    """LAN clients need the access key (in the QR link) - the library is private."""
    if not _from_this_pc(request):
        key = access_key()
        q = request.query_params.get("k")
        if q != key and request.cookies.get("gk") != key:
            if request.url.path.startswith("/static/"):
                return await call_next(request)
            return HTMLResponse("<h2>GymCut</h2><p>Scan the QR code shown in GymCut on the PC "
                                "(it contains the access key).</p>", status_code=401)
        resp = await call_next(request)
        if q == key:
            resp.set_cookie("gk", key, max_age=3600 * 24 * 365, samesite="lax", httponly=True,
                            secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")
        return resp
    return await call_next(request)


def _ws_allowed(ws: WebSocket) -> bool:
    if _from_this_pc(ws):
        return True
    key = access_key()
    return ws.query_params.get("k") == key or ws.cookies.get("gk") == key


def _vid(vid: str) -> str:
    if not lib.valid_id(vid) or not (lib.vdir(vid) / "meta.json").exists():
        raise HTTPException(404, "video not found")
    return vid


# ----------------------------------------------------------------------------- pages
_NO_CACHE = {"Cache-Control": "no-cache"}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "index.html", headers=_NO_CACHE)


@app.get("/live", include_in_schema=False)
def live_page():
    return FileResponse(WEB_DIR / "live.html", headers=_NO_CACHE)


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.on_event("startup")
def resume_unfinished():
    """Jobs live in memory: re-queue analyses that were pending when the server stopped."""
    for m in lib.list_all():
        if m.get("status") in ("queued", "processing"):
            src = lib.vdir(m["id"]) / m["source_file"]
            if src.exists() and src.stat().st_size > 0:
                _queue_analysis(m["id"])


# ----------------------------------------------------------------------------- info / settings
@app.get("/api/info")
def info(request: Request):
    ips = lan_ips()
    key = access_key()
    base = [f"https://{ip}:{RUNTIME['https_port']}" for ip in ips] if RUNTIME["lan"] else []
    return {
        "version": __version__, "gpu": gpu_info(), "nvenc": nvenc_available(),
        "ffmpeg": bool(FFMPEG and FFPROBE), "models": KNOWN_MODELS,
        "lan": RUNTIME["lan"], "ips": ips,
        "phone_url": (base[0] + f"/live?k={key}") if base else None,
        "phone_app_url": (base[0] + f"/?k={key}") if base else None,
        "lan_url": (f"http://{ips[0]}:{RUNTIME['http_port']}/?k={key}") if (RUNTIME["lan"] and ips) else None,
        "local": _from_this_pc(request),
        "internet": _internet_info(key),
    }


def _internet_info(key: str) -> dict:
    from . import tunnel
    st = tunnel.status()
    url = st.get("url")
    return {**st, "phone_url": f"{url}/live?k={key}" if url else None, "app_url": f"{url}/?k={key}" if url else None}


@app.post("/api/tunnel")
async def tunnel_ctl(request: Request):
    """Start / stop internet access (Cloudflare quick tunnel) - only from the PC itself."""
    if not _from_this_pc(request):
        raise HTTPException(403, "only on the GymCut PC")
    from . import tunnel
    body = await request.json()
    st = tunnel.start(RUNTIME["http_port"]) if body.get("on") else tunnel.stop()
    return {**st, **_internet_info(access_key())}


@app.get("/api/qr.svg")
def qr(text: str):
    import io
    import segno
    buf = io.BytesIO()
    # full SVG document (with xmlns) - required when the SVG is loaded via <img>
    segno.make(text, error="m").save(buf, kind="svg", scale=6, border=2, dark="#111", light="#fff")
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-cache"})


@app.get("/api/settings")
def get_settings():
    s = settings.get()
    s.pop("access_key", None)
    return s


@app.put("/api/settings")
async def put_settings(request: Request):
    if not _from_this_pc(request):
        raise HTTPException(403, "settings can only be changed on the GymCut PC")
    patch = await request.json()
    patch.pop("access_key", None)
    s = settings.update(patch)
    s.pop("access_key", None)
    return s


# ----------------------------------------------------------------------------- videos
@app.get("/api/videos")
def list_videos():
    vids = lib.list_all()
    active = {j["video_id"]: j for j in jobs.list(active_only=True) if j["video_id"]}
    for v in vids:
        if v["id"] in active:
            v["job"] = active[v["id"]]
    return vids


@app.get("/api/videos/{vid}")
def get_video(vid: str):
    m = lib.get(_vid(vid))
    m["jobs"] = jobs.active_for(vid)
    return m


@app.patch("/api/videos/{vid}")
async def patch_video(vid: str, request: Request):
    _vid(vid)
    body = await request.json()
    allowed = {k: body[k] for k in ("name", "athlete_height_cm", "tags", "notes", "athlete") if k in body}
    if "athlete" in allowed:
        allowed["athlete"] = (str(allowed["athlete"]).strip()[:60] or None) if allowed["athlete"] else None
    m = lib.update(vid, **allowed)
    if "athlete_height_cm" in allowed and (lib.vdir(vid) / "athlete.npz").exists():
        prev = lib.read_json(vid, "analysis.json") or {}
        p = prev.get("segment_params", {})
        from .pipeline import build_analysis
        if p.get("manual"):
            await run_in_threadpool(build_analysis, vid, None, prev.get("segments"))
        else:
            await run_in_threadpool(build_analysis, vid, {k: v for k, v in p.items() if k != "manual"})
    return m


@app.delete("/api/videos/{vid}")
def delete_video(vid: str):
    _vid(vid)
    for j in jobs.active_for(vid):
        jobs.cancel(j["id"])
    lib.delete(vid)
    return {"ok": True}


def _queue_analysis(vid: str, from_stage: str = "prepare", forced_tid=None):
    from .pipeline import job_process
    lib.update(vid, status="queued")
    name = lib.get(vid).get("name", vid)
    return jobs.submit("gpu", "analyze", f"Analyse {name}", vid, job_process, vid,
                       from_stage=from_stage, forced_tid=forced_tid)


@app.post("/api/upload")
async def upload(request: Request, name: str = "video.mp4", source: str = "upload"):
    p = Path(name)
    ext = p.suffix.lstrip(".") or "mp4"
    if ext.lower() not in ("mp4", "mov", "m4v", "webm", "mkv", "avi", "ogv", "ogg", "3gp", "mts", "ts", "mpg", "mpeg", "wmv"):
        raise HTTPException(400, f"unsupported file type .{ext}")
    meta = lib.create(lib.safe_name(p.stem, 80), source if source in ("upload", "live-hq") else "upload", ext)
    dst = lib.vdir(meta["id"]) / meta["source_file"]
    size = 0
    try:
        with open(dst, "wb") as fh:
            async for chunk in request.stream():
                fh.write(chunk)
                size += len(chunk)
    except Exception:
        lib.delete(meta["id"])
        raise HTTPException(400, "upload interrupted")
    if size == 0:
        lib.delete(meta["id"])
        raise HTTPException(400, "empty upload")
    lib.update(meta["id"], size=size)
    job = _queue_analysis(meta["id"])
    return {"video": lib.get(meta["id"]), "job": job.to_dict()}


@app.post("/api/videos/{vid}/reprocess")
async def reprocess(vid: str, request: Request):
    _vid(vid)
    body = await request.json() if (await request.body()) else {}
    stage = body.get("from", "track")
    if stage not in ("prepare", "track", "athlete"):
        raise HTTPException(400, "bad stage")
    opts = {k: body[k] for k in ("pose_model", "detect_size", "refine", "refine_size") if k in body}
    if opts.get("pose_model") and opts["pose_model"] not in KNOWN_MODELS:
        raise HTTPException(400, "unknown model")
    targets = [m["id"] for m in lib.list_all() if m.get("status") in ("ready", "error")] if body.get("all") else [vid]
    jobs_out = []
    for v in targets:
        if opts:
            lib.update(v, analysis_opts={**(lib.get(v).get("analysis_opts") or {}), **opts})
        if stage == "prepare":
            (lib.vdir(v) / "work.mp4").unlink(missing_ok=True)
        jobs_out.append(_queue_analysis(v, stage).to_dict())
    return jobs_out[0] if not body.get("all") else {"queued": len(jobs_out)}


@app.post("/api/videos/{vid}/athlete")
async def set_athlete(vid: str, request: Request):
    _vid(vid)
    body = await request.json()
    tid = int(body["track_id"])
    return _queue_analysis(vid, "athlete", forced_tid=tid).to_dict()


def _range_file(path: Path, request: Request, media_type: str) -> Response:
    """Serve a file with HTTP Range support (needed for video seeking)."""
    size = path.stat().st_size
    rng = request.headers.get("range")
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-cache"}
    if rng and rng.startswith("bytes="):
        try:
            a, b = rng[6:].split(",")[0].split("-")
            if a == "":
                start, end = max(0, size - int(b)), size - 1
            else:
                start = int(a)
                end = int(b) if b else size - 1
            end = min(end, size - 1, start + 8 * 1024 * 1024 - 1)
            if start > end or start >= size:
                raise ValueError
        except ValueError:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        with open(path, "rb") as fh:
            fh.seek(start)
            data = fh.read(end - start + 1)
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return Response(data, status_code=206, media_type=media_type, headers=headers)
    return FileResponse(path, media_type=media_type, headers=headers)


@app.get("/api/videos/{vid}/media")
def media(vid: str, request: Request):
    p = lib.vdir(_vid(vid)) / "work.mp4"
    if not p.exists():
        raise HTTPException(404, "not prepared yet")
    return _range_file(p, request, "video/mp4")


@app.get("/api/videos/{vid}/thumb.jpg")
def thumb(vid: str):
    p = lib.vdir(_vid(vid)) / "thumb.jpg"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "max-age=60"})


_gz_cache: dict[str, tuple[float, bytes]] = {}


@app.get("/api/videos/{vid}/analysis")
def analysis(vid: str, request: Request):
    p = lib.vdir(_vid(vid)) / "analysis.json"
    if not p.exists():
        raise HTTPException(404, "not analysed yet")
    mt = p.stat().st_mtime
    hit = _gz_cache.get(vid)
    if not hit or hit[0] != mt:
        hit = (mt, gzip.compress(p.read_bytes(), 5))
        _gz_cache[vid] = hit
        if len(_gz_cache) > 12:
            _gz_cache.pop(next(iter(_gz_cache)))
    if "gzip" in request.headers.get("accept-encoding", ""):
        return Response(hit[1], media_type="application/json",
                        headers={"Content-Encoding": "gzip", "Cache-Control": "no-cache"})
    return FileResponse(p, media_type="application/json")


@app.get("/api/videos/{vid}/tracks")
def tracks(vid: str):
    t = lib.read_json(_vid(vid), "tracks.json")
    if t is None:
        raise HTTPException(404)
    return t


def _resp_segments(vid):
    a = lib.read_json(vid, "analysis.json")
    return {"segments": a["segments"], "segment_params": a["segment_params"], "scale": a["scale"]}


@app.post("/api/videos/{vid}/segments/auto")
async def auto_segments(vid: str, request: Request):
    _vid(vid)
    params = await request.json()
    params = {k: params[k] for k in ("preset", "sensitivity", "pre_pad", "post_pad", "min_duration") if k in params}
    from .pipeline import build_analysis
    await run_in_threadpool(build_analysis, vid, params)
    return _resp_segments(vid)


@app.put("/api/videos/{vid}/segments")
async def put_segments(vid: str, request: Request):
    _vid(vid)
    body = await request.json()
    segs = body.get("segments", [])
    a = lib.read_json(vid, "analysis.json")
    n = a["n_frames"]
    clean = []
    for s in segs:
        st, en = max(0, int(s["start"])), min(n, int(s["end"]))
        if en - st >= 2:
            clean.append({"start": st, "end": en, "label": s.get("label"), "custom_label": bool(s.get("custom_label"))})
    clean.sort(key=lambda s: s["start"])
    from .pipeline import build_analysis
    await run_in_threadpool(build_analysis, vid, None, clean)
    return _resp_segments(vid)


_seg_index: dict[str, tuple[float, list]] = {}
_EL_KEYS = ("type", "name", "frame0", "frame1", "flight_time", "height_est_m", "rotation_deg", "rotations",
            "shape", "knee_min", "hip_min", "split_max", "elbow_min", "body_line", "support_time", "direction")


def _attempts_of(m: dict) -> list:
    p = lib.vdir(m["id"]) / "analysis.json"
    if not p.exists():
        return []
    mt = p.stat().st_mtime
    hit = _seg_index.get(m["id"])
    if not hit or hit[0] != mt:
        a = json.loads(p.read_text("utf-8"))
        segs = [{"id": s["id"], "label": s["label"], "t0": s["t0"], "t1": s["t1"], "start": s["start"], "end": s["end"],
                 "metrics": s.get("metrics", {}), "landing": {k: (s.get("landing") or {}).get(k) for k in ("result", "knee_min", "t")},
                 "elements": [{k: e.get(k) for k in _EL_KEYS} for e in s.get("elements", [])],
                 "notes": [n for n in s.get("notes", []) if n["level"] in ("warn", "bad")]}
                for s in a.get("segments", [])]
        hit = (mt, segs)
        _seg_index[m["id"]] = hit
    return hit[1]


@app.get("/api/segments")
def all_segments(athlete: str | None = None):
    """Every analysed skill (attempt) in the library, with the data the coach views need."""
    from .ue3d import movies_dir, take_name as ue3d_take
    ue3d_movies = movies_dir()
    out = []
    for m in lib.list_all():
        if m.get("status") != "ready":
            continue
        if athlete is not None and (m.get("athlete") or "") != athlete:
            continue
        for s in _attempts_of(m):
            rep = (m.get("replays3d") or {}).get(str(s["id"]))
            m3d = (m.get("metrics3d") or {}).get(str(s["id"]))
            solved = (ue3d_movies / ue3d_take(m["id"], s["id"]) / "solved.json").exists()
            out.append({"vid": m["id"], "video": m["name"], "created": m["created"], "athlete": m.get("athlete"),
                        "recorded": m["recorded"], "fps": m.get("fps"), **s,
                        "replay3d": f"/api/exports/{m['id']}/{rep['file']}" if rep else None, "m3d": m3d,
                        "solved3d": solved})
    out.sort(key=lambda x: (x["recorded"], x["created"], x["t0"]))
    return out


@app.get("/api/athletes")
def athletes():
    agg: dict[str, dict] = {}
    for m in lib.list_all():
        name = m.get("athlete") or ""
        a = agg.setdefault(name, {"name": name or None, "videos": 0, "attempts": 0, "sessions": set(), "last": ""})
        a["videos"] += 1
        a["attempts"] += m.get("segments") or 0
        a["sessions"].add(m["recorded"])
        a["last"] = max(a["last"], m["recorded"])
    out = [{**v, "sessions": len(v["sessions"])} for v in agg.values()]
    out.sort(key=lambda a: (a["name"] is None, -(a["attempts"])))
    return out


@app.get("/api/videos/{vid}/segthumb/{seg_id}.jpg")
def seg_thumb(vid: str, seg_id: int):
    """Athlete-centred thumbnail of an attempt at its most telling moment (mid-flight / inversion)."""
    import cv2
    import numpy as np
    d = lib.vdir(_vid(vid))
    pa = d / "analysis.json"
    if not pa.exists():
        raise HTTPException(404)
    cache = d / "thumbs" / f"seg{seg_id}_{int(pa.stat().st_mtime)}.jpg"
    if not cache.exists():
        a = json.loads(pa.read_text("utf-8"))
        seg = next((x for x in a["segments"] if x["id"] == seg_id), None)
        if seg is None:
            raise HTTPException(404)
        els = [e for e in seg.get("elements", []) if e["type"] not in ("hurdle",)]
        pick = next((e for e in els if e["type"] == "salto"), els[0] if els else None)
        f = (pick["frame0"] + pick["frame1"]) // 2 if pick else (seg["start"] + seg["end"]) // 2
        cap = cv2.VideoCapture(str(d / "work.mp4"))
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, f))
        ok, img = cap.read()
        cap.release()
        if not ok:
            raise HTTPException(404)
        H, W = img.shape[:2]
        k = a["kps"][f] if f < len(a["kps"]) else None
        cx, cy, side = W / 2, H / 2, min(W, H)
        if k:
            pts = np.array(k, float).reshape(17, 3)
            pts = pts[pts[:, 2] >= 0.3]
            if len(pts) >= 4:
                x1, y1 = pts[:, :2].min(0)
                x2, y2 = pts[:, :2].max(0)
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                side = max(x2 - x1, (y2 - y1) * 4 / 3) * 1.7
        w = min(W, max(side, 120))
        h = min(H, w * 3 / 4)
        w = h * 4 / 3
        x0 = int(np.clip(cx - w / 2, 0, W - w))
        y0 = int(np.clip(cy - h / 2, 0, H - h))
        crop = cv2.resize(img[y0:y0 + int(h), x0:x0 + int(w)], (320, 240), interpolation=cv2.INTER_AREA)
        cache.parent.mkdir(exist_ok=True)
        for old in cache.parent.glob(f"seg{seg_id}_*.jpg"):
            old.unlink(missing_ok=True)
        cv2.imwrite(str(cache), crop, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return FileResponse(cache, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


# ----------------------------------------------------------------------------- exports
@app.post("/api/videos/{vid}/export")
async def export(vid: str, request: Request):
    _vid(vid)
    body = await request.json()
    from .render import EXPORTERS, run_export
    mode = body.get("mode", "clip")
    if mode not in EXPORTERS:
        raise HTTPException(400, "bad mode")
    seg = body.get("segment")
    title = {"clip": "Trim clip", "overlay": "Overlay video", "reframe": "Focus video",
             "sequence": "Sequence photo", "csv": "Data CSV", "unreal": "Unreal image sequence"}[mode]
    job = jobs.submit("cpu", "export", title, vid, run_export, vid, mode,
                      seg_id=int(seg) if seg is not None else None,
                      start=body.get("start"), end=body.get("end"), opts=body.get("opts") or {})
    return job.to_dict()


@app.post("/api/export-all")
async def export_all(request: Request):
    """Queue one export per skill segment - for given videos (vids / vid) or the whole library."""
    body = await request.json()
    from .render import EXPORTERS, run_export
    mode = body.get("mode", "reframe")
    if mode not in EXPORTERS:
        raise HTTPException(400, "bad mode")
    vids = body.get("vids") or ([body["vid"]] if body.get("vid") else
                                [m["id"] for m in lib.list_all() if m.get("status") == "ready"])
    opts = {**(body.get("opts") or {}), "replace": True}
    n = 0
    for vid in vids:
        a = lib.read_json(_vid(vid), "analysis.json") or {}
        for seg in a.get("segments", []):
            jobs.submit("cpu", "export", f"Export {lib.get(vid)['name']} #{seg['id']}", vid, run_export, vid, mode,
                        seg_id=int(seg["id"]), opts=opts)
            n += 1
    return {"queued": n, "folder": str(EXPORTS_DIR)}


def _queue_replay3d(vid: str, seg_id: int, ghost: bool = True, angles=("side", "front", "orbit")) -> dict:
    for j in jobs.active_for(vid):
        if j["kind"] == "replay3d" and j.get("result") is None and j["title"].startswith(f"3D replay #{seg_id} "):
            return j
    from .ue3d import job_replay3d
    return jobs.submit("ue", "replay3d", f"3D replay #{seg_id} · {lib.get(vid).get('name')}", vid, job_replay3d, vid, seg_id,
                       ghost=ghost, angles=tuple(angles)).to_dict()


@app.post("/api/videos/{vid}/replay3d")
async def replay3d(vid: str, request: Request):
    """Queue a 3D replay (Unreal body solve + render) for one attempt."""
    _vid(vid)
    body = await request.json()
    angles = [a for a in body.get("angles", ["side", "front", "orbit"]) if a in ("side", "front", "high", "orbit")] or ["side"]
    return _queue_replay3d(vid, int(body["segment"]), bool(body.get("ghost", True)), angles)


@app.get("/api/fuse3d")
async def fuse3d(a: str, b: str, offset: float):
    """Two-camera 3D: both clips' joints triangulated together (bundle adjustment) + confidence."""
    _vid(a), _vid(b)
    from .triangulate import fuse_cached
    try:
        return await run_in_threadpool(fuse_cached, a, b, offset)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"two-camera fusion failed: {e}")


@app.get("/api/videos/{vid}/pose3d/{seg_id}")
def pose3d_of(vid: str, seg_id: int, ghost: int = 1):
    """Solved 3D joints of one attempt (+ aligned personal-best ghost) for the browser 3D view."""
    _vid(vid)
    from .pose3d import attempt
    data = attempt(vid, seg_id, bool(ghost))
    if data is None:
        raise HTTPException(404, "no 3D solve for this attempt yet")
    return data


@app.post("/api/replay3d-batch")
async def replay3d_batch(request: Request):
    """Queue 3D replays for every skill of the given videos (skips attempts that already have one)."""
    body = await request.json()
    queued = skipped = 0
    for vid in body.get("vids") or []:
        m = lib.get(_vid(vid))
        have = m.get("replays3d") or {}
        for seg in (lib.read_json(vid, "analysis.json") or {}).get("segments", []):
            if str(seg["id"]) in have and not body.get("force"):
                skipped += 1
                continue
            _queue_replay3d(vid, int(seg["id"]), bool(body.get("ghost", True)))
            queued += 1
    return {"queued": queued, "skipped": skipped}


_KINDS = [("_3d", "3d", "3D replay"), ("_focus", "focus", "Focus video"), ("_reframe", "focus", "Focus video"),
          ("_overlay", "overlay", "Skeleton overlay"), ("_clip", "clip", "Trimmed clip"), ("_sequence", "photo", "Sequence photo"),
          ("_data", "csv", "Data (CSV)"), ("_unreal", "unreal", "Unreal sequence")]


def _export_kind(name: str) -> tuple[str, str, str]:
    """(kind, kind label, skill part of the file name)."""
    stem = name.rsplit(".", 1)[0]
    best = None
    for key, kind, label in _KINDS:
        i = stem.rfind(key)
        if i >= 0 and (best is None or i > best[0]):
            best = (i, kind, label)
    return (best[1], best[2], stem[:best[0]]) if best else ("other", "File", stem)


@app.get("/api/exports")
def all_exports():
    """Every exported file in the library, newest first, with its video and kind."""
    metas = {m["id"]: m for m in lib.list_all()}
    out = []
    for d in EXPORTS_DIR.iterdir() if EXPORTS_DIR.exists() else []:
        m = metas.get(d.name)
        if not d.is_dir() or not m:
            continue
        prefix = lib.safe_name(m.get("name", d.name), 40) + "_"
        segs = (lib.read_json(d.name, "analysis.json") or {}).get("segments", [])
        by_safe = {lib.safe_name(sg["label"], 40): sg for sg in segs}
        for p in d.iterdir():
            if not p.is_file() or p.name.endswith(".tmp") or p.suffix == ".json":
                continue
            kind, label, head = _export_kind(p.name)
            if kind == "unreal":
                continue
            part = head[len(prefix):] if head.startswith(prefix) else head
            sg = by_safe.get(part)
            skill = sg["label"] if sg else part.replace(" _ ", " → ").replace("_", " ").strip()
            st = p.stat()
            out.append({"vid": d.name, "video": m.get("name"), "athlete": m.get("athlete"), "recorded": m.get("recorded"),
                        "file": p.name, "skill": skill, "seg": sg["id"] if sg else None, "kind": kind, "kind_label": label, "size": st.st_size, "mtime": st.st_mtime,
                        "url": f"/api/exports/{d.name}/{p.name}"})
    out.sort(key=lambda x: -x["mtime"])
    return {"folder": str(EXPORTS_DIR), "files": out}


@app.post("/api/exports/open-folder")
async def open_exports_folder(request: Request):
    """Open the exports folder in Explorer - only for the PC GymCut runs on."""
    if not _from_this_pc(request):
        raise HTTPException(403, "only on the GymCut PC")
    body = await request.json()
    d = EXPORTS_DIR / _vid(body["vid"]) if body.get("vid") else EXPORTS_DIR
    target = d / body["file"] if body.get("file") and (d / body["file"]).exists() else None
    import subprocess
    if target:
        subprocess.Popen(["explorer", "/select,", str(target)])
    else:
        os.startfile(str(d if d.exists() else EXPORTS_DIR))
    return {"ok": True}


@app.get("/api/unreal")
def unreal_status():
    from .ue3d import ping, project_file
    return {"project": str(project_file()), "project_exists": project_file().exists(), "editor_running": ping()}


@app.get("/api/videos/{vid}/exports")
def list_exports(vid: str):
    d = EXPORTS_DIR / _vid(vid)
    if not d.exists():
        return []
    out = []
    for p in sorted(d.iterdir(), key=lambda p: -p.stat().st_mtime):
        if p.is_file() and not p.name.endswith(".tmp"):
            out.append({"file": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime,
                        "url": f"/api/exports/{vid}/{p.name}"})
    return out


def _export_path(vid: str, name: str) -> Path:
    _vid(vid)
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400)
    p = (EXPORTS_DIR / vid / name).resolve()
    if p.parent != (EXPORTS_DIR / vid).resolve() or not p.exists():
        raise HTTPException(404)
    return p


@app.get("/api/exports/{vid}/{name}")
def get_export(vid: str, name: str, request: Request, download: int = 0):
    p = _export_path(vid, name)
    mt = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".csv": "text/csv", ".png": "image/png",
          ".json": "application/json"}.get(p.suffix, "application/octet-stream")
    if download:
        return FileResponse(p, media_type=mt, filename=p.name)
    return _range_file(p, request, mt)


@app.delete("/api/exports/{vid}/{name}")
def delete_export(vid: str, name: str):
    p = _export_path(vid, name)
    seq = p.with_suffix(".seq")
    if p.suffix == ".json" and seq.is_dir():
        import shutil
        shutil.rmtree(seq, ignore_errors=True)
    p.unlink()
    return {"ok": True}


# ----------------------------------------------------------------------------- jobs
@app.get("/api/jobs")
def list_jobs(active: int = 0):
    return jobs.list(active_only=bool(active))


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404)
    return j.to_dict()


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str):
    return {"ok": jobs.cancel(jid)}


# ----------------------------------------------------------------------------- live
@app.get("/api/live/sessions")
def live_sessions():
    return live.list()


@app.post("/api/live/camera")
async def live_camera(request: Request):
    body = await request.json()
    url = str(body.get("url", "")).strip()
    if not url:
        raise HTTPException(400, "url required")
    s = await run_in_threadpool(live.start_camera, url, body.get("name"))
    return s.summary()


@app.post("/api/live/sessions/{sid}")
async def live_control(sid: str, request: Request):
    s = live.get(sid)
    if not s:
        raise HTTPException(404)
    body = await request.json()
    s.configure(body)
    if "record" in body:
        s.manual_record(bool(body["record"]))
    if "view3d" in body:
        s.set_view3d(bool(body["view3d"]))
    if "ue3d" in body:
        from .ue3d import live_start, live_stop
        try:
            live_start(s) if body["ue3d"] else live_stop(s)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
    if "lock" in body:
        if body["lock"] is None:
            s.lock_id = None
        else:
            s.lock_at(float(body["lock"]["x"]), float(body["lock"]["y"]))
    return s.summary()


@app.delete("/api/live/sessions/{sid}")
def live_stop(sid: str):
    live.stop(sid)
    return {"ok": True}


def _handle_control(sess, data: dict):
    t = data.get("type")
    if t == "config":
        sess.configure(data)
    elif t == "record":
        sess.manual_record(bool(data.get("on")))
    elif t == "lock":
        if data.get("x") is None:
            sess.lock_id = None
        else:
            sess.lock_at(float(data["x"]), float(data["y"]))


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    if not _ws_allowed(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    sess = live.create(ws.query_params.get("name") or "Phone", "browser")
    await ws.send_text(json.dumps({"type": "hello", "session": sess.id, "name": sess.name,
                                   "cfg": {k: sess.cfg.get(k) for k in ("sensitivity", "pre_roll", "post_roll", "auto_analyze")}}))
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes"):
                b = msg["bytes"]
                if len(b) < 16:
                    continue
                ts = struct.unpack("<d", b[:8])[0]
                res = await run_in_threadpool(sess.process_jpeg, b[8:], ts)
                await ws.send_text(lib.dumps(res))
                for ev in sess.pop_events():
                    await ws.send_text(lib.dumps(ev))
            elif msg.get("text"):
                try:
                    data = json.loads(msg["text"])
                except ValueError:
                    continue
                if data.get("type") == "ping":
                    await ws.send_text(json.dumps({"type": "pong", "t": data.get("t")}))
                else:
                    await run_in_threadpool(_handle_control, sess, data)
                    await ws.send_text(lib.dumps({"type": "state", **sess.summary()}))
    except WebSocketDisconnect:
        pass
    finally:
        await run_in_threadpool(sess.close)


@app.websocket("/ws/live/view/{sid}")
async def ws_view(ws: WebSocket, sid: str):
    if not _ws_allowed(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    sess = live.get(sid)
    if not sess:
        await ws.send_text(json.dumps({"type": "error", "message": "no such session"}))
        await ws.close()
        return
    last = 0
    try:
        while True:
            pkt = sess.pack_latest(last)
            if pkt:
                last, data = pkt
                await ws.send_bytes(data)
                await asyncio.sleep(0.04)
            else:
                await asyncio.sleep(0.02)
            if sess.closed and not pkt:
                await ws.send_text(json.dumps({"type": "closed"}))
                break
    except (WebSocketDisconnect, RuntimeError):
        pass
