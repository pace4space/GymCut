"""Offline processing of one video, end to end.

  1. working copy (CFR H.264)          -> work.mp4
  2. tracking pass + camera motion     -> persons.npz, motion.npz
  3. athlete selection + refinement    -> tracks.json, athlete.npz
  4. cleaning, signals, segments       -> analysis.json (what the UI loads)
"""
from __future__ import annotations

import threading
from pathlib import Path
import time
from queue import Queue

import cv2
import numpy as np

from . import library as lib
from .analysis import PHASE_CHARS, analyze_segment, detect_phases, metres_per_bl
from .camera_motion import MotionEstimator, chain_transforms
from .config import settings
from .jobs import JobCancelled
from .kinematics import center_of_mass, clean_keypoints, compute_signals
from .media import make_working_copy, probe, thumbnail
from .pose import PoseEngine
from .refine import refine_athlete
from .segmentation import PRESETS, activity_score, detect_segments
from .tracking import PersonTable, athlete_series, stitch, summarize_tracks, tracks_payload

_engines: dict[str, PoseEngine] = {}
_engine_lock = threading.Lock()


def get_engine(name: str) -> PoseEngine:
    """Engines for the offline (single GPU worker) path."""
    with _engine_lock:
        if name not in _engines:
            if len(_engines) >= 2:
                _engines.clear()
            _engines[name] = PoseEngine(name, half=settings.get().get("half", True))
        return _engines[name]


class FrameReader:
    """Decode frames on a background thread so decoding overlaps GPU inference."""

    def __init__(self, path, maxq: int = 48):
        self.cap = cv2.VideoCapture(str(path))
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open video {path}")
        self.q: Queue = Queue(maxq)
        self._stop = False
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self):
        while not self._stop:
            ok, f = self.cap.read()
            self.q.put(f if ok else None)
            if not ok:
                break
        self.cap.release()

    def __iter__(self):
        while True:
            f = self.q.get()
            if f is None:
                return
            yield f

    def close(self):
        self._stop = True
        while not self.q.empty():
            self.q.get_nowait()


# ----------------------------------------------------------------------------- JSON helpers
def _arr(x, nd: int = 2):
    x = np.asarray(x, dtype=np.float64)
    r = np.round(x, nd)
    out = r.tolist()
    if not np.isfinite(r).all():
        def fix(v):
            if isinstance(v, list):
                return [fix(u) for u in v]
            return v if (v is not None and np.isfinite(v)) else None
        out = fix(out)
    return out


def _encode_kps(k: np.ndarray) -> list:
    out = []
    for t in range(len(k)):
        if not np.isfinite(k[t, :, 0]).any():
            out.append(None)
            continue
        f = np.round(k[t], 1)
        f[:, 2] = np.round(np.nan_to_num(k[t, :, 2], nan=0.0), 2)
        f[~np.isfinite(f)] = -1
        out.append(f.reshape(-1).tolist())
    return out


# ----------------------------------------------------------------------------- stages
ANALYSIS_KEYS = ("pose_model", "detect_size", "refine", "refine_size")


def analysis_settings(vid: str) -> dict:
    """Global settings, overridden by per-video choices made in "Re-analyse"."""
    s = settings.get()
    o = lib.get(vid).get("analysis_opts") or {}
    s.update({k: v for k, v in o.items() if k in ANALYSIS_KEYS and v is not None})
    return s


def stage_prepare(job, vid: str, p0=0.0, p1=0.12) -> dict:
    meta = lib.get(vid)
    d = lib.vdir(vid)
    src = d / meta["source_file"]
    work = d / "work.mp4"
    job.update("Preparing video", p0, "probing")
    info = probe(src)
    if not work.exists():
        make_working_copy(src, work, info, progress=lambda f: job.update("Preparing video", p0 + (p1 - p0) * f))
    winfo = probe(work)
    try:
        thumbnail(work, min(winfo["duration"] * 0.35, 3.0), d / "thumb.jpg")
    except Exception:
        pass
    lib.update(vid, width=winfo["width"], height=winfo["height"], fps=winfo["fps"],
               duration=winfo["duration"], has_audio=winfo["has_audio"], n_frames=winfo["nb_frames"],
               original={k: info[k] for k in ("width", "height", "fps", "codec", "duration")})
    return winfo


def stage_track(job, vid: str, p0=0.12, p1=0.62) -> None:
    s = analysis_settings(vid)
    meta = lib.get(vid)
    d = lib.vdir(vid)
    eng = get_engine(s["pose_model"])
    eng.reset_tracker()
    me = MotionEstimator(meta["width"], meta["height"])
    per, A, cuts, oks = [], [], [], []
    N = max(1, meta.get("n_frames") or 1)
    # High-fps footage (phone slow-mo): track on every 2nd/4th frame. The refinement
    # stage re-estimates the athlete's pose on *every* frame from interpolated crops,
    # so temporal resolution is kept while the expensive full-frame pass is 2-4x faster.
    fps = float(meta.get("fps") or 30)
    stride = 4 if fps > 100 else 2 if fps > 45 else 1
    from .pose import Detections
    reader = FrameReader(d / "work.mp4")
    t0 = time.time()
    last_boxes = None
    try:
        for i, frame in enumerate(reader):
            if i % stride == 0:
                dets = eng.track(frame, imgsz=int(s["detect_size"]))
                last_boxes = dets.boxes
            else:
                dets = Detections.empty()
            a, ok, cut, _ = me.update(frame, dets.boxes if len(dets) else last_boxes)
            per.append(dets)
            A.append(a)
            cuts.append(cut)
            oks.append(ok)
            if i % 10 == 0:
                fps_run = (i + 1) / max(1e-3, time.time() - t0)
                job.update("Tracking people", p0 + (p1 - p0) * min(1.0, i / N),
                           f"frame {i}/{N} · {fps_run:.0f} fps" + (f" · stride {stride}" if stride > 1 else ""))
    finally:
        reader.close()
    if not per:
        raise RuntimeError("could not decode any frames")
    A = np.array(A)
    cuts = np.array(cuts, bool)
    W, shot = chain_transforms(A, cuts)
    PersonTable.from_lists(per).save(d / "persons.npz")
    np.savez_compressed(d / "motion.npz", A=A, W=W, shot=shot, cuts=cuts, ok=np.array(oks, bool))
    lib.update(vid, n_frames=len(per))


def stage_athlete(job, vid: str, forced_tid: int | None = None, p0=0.62, p1=0.95) -> None:
    s = analysis_settings(vid)
    meta = lib.get(vid)
    d = lib.vdir(vid)
    T, fps = int(meta["n_frames"]), float(meta["fps"])
    pt = PersonTable.load(d / "persons.npz")
    mo = np.load(d / "motion.npz")
    W, shot = mo["W"], mo["shot"]
    job.update("Selecting athlete", p0)
    tracks = summarize_tracks(pt, W, T, meta["height"], fps)
    if not tracks:
        raise RuntimeError("No person was detected in this video")
    ids = [t.tid for t in tracks]
    main = forced_tid if forced_tid in ids else tracks[0].tid
    assign = stitch(pt, tracks, main, W, shot, fps)
    chain = sorted({int(pt.tid[i]) for i in assign.values()}, key=lambda t: t != main)
    lib.write_json(vid, "tracks.json", tracks_payload(pt, tracks, chain))
    box, kps1, _, _ = athlete_series(pt, assign, T)
    if s.get("refine", True):
        kps2, ang, _ = refine_athlete(d / "work.mp4", T, box, kps1, fps, get_engine(s["pose_model"]),
                                      crop_size=int(s["refine_size"]),
                                      progress=lambda f: job.update("Refining athlete pose (rotation-aware)",
                                                                    p0 + (p1 - p0) * f))
    else:
        kps2, ang = kps1.copy(), np.full(T, np.nan)
    np.savez_compressed(d / "athlete.npz", box=box, kps1=kps1, kps2=kps2, ang=ang,
                        chain=np.array(chain), main=np.array(main), forced=np.array(forced_tid is not None))


# ----------------------------------------------------------------------------- analysis
_cache: dict[str, tuple] = {}
_cache_lock = threading.Lock()


def _signals(vid: str):
    d = lib.vdir(vid)
    key = (d / "athlete.npz").stat().st_mtime, lib.get(vid).get("athlete_height_cm")
    with _cache_lock:
        hit = _cache.get(vid)
        if hit and hit[0] == key:
            return hit[1]
    meta = lib.get(vid)
    fps = float(meta["fps"])
    z = np.load(d / "athlete.npz")
    mo = np.load(d / "motion.npz")
    kps_c, interp = clean_keypoints(z["kps2"], fps)
    sig = compute_signals(kps_c, fps, mo["W"], mo["shot"])
    phases = detect_phases(sig, fps)
    act = activity_score(sig, fps)
    height = meta.get("athlete_height_cm") or settings.get().get("athlete_height_cm")
    scale = metres_per_bl(sig, phases, fps, height)
    bundle = {"kps": kps_c, "interp": interp, "sig": sig, "phases": phases, "act": act, "scale": scale,
              "shot": mo["shot"], "cuts": mo["cuts"], "W": mo["W"], "chain": z["chain"].tolist(),
              "main": int(z["main"]), "fps": fps}
    with _cache_lock:
        _cache[vid] = (key, bundle)
        while len(_cache) > 6:
            _cache.pop(next(iter(_cache)))
    return bundle


def _analyze_segments(b: dict, segs: list[tuple[int, int]], labels: dict | None = None) -> list[dict]:
    out = []
    for i, (s, e) in enumerate(segs):
        r = analyze_segment(b["sig"], b["phases"], b["fps"], s, e, b["scale"])
        item = {"id": i + 1, "start": int(s), "end": int(e), "t0": round(s / b["fps"], 3),
                "t1": round(e / b["fps"], 3), **r}
        if labels and (s, e) in labels and labels[(s, e)]:
            item["label"] = labels[(s, e)]
            item["custom_label"] = True
        out.append(item)
    return out


def _quality(vid: str, b: dict, segments: list[dict], cam_static: bool) -> list[dict]:
    """Capture-quality hints shown in the studio (what limits accuracy, how to film better)."""
    out = []
    fps = b["fps"]
    z = np.load(lib.vdir(vid) / "athlete.npz")
    h = z["box"][:, 3] - z["box"][:, 1]
    med_h = float(np.nanmedian(h)) if np.isfinite(h).any() else 0.0
    if 0 < med_h < 110:
        out.append({"level": "warn", "text": f"The athlete is small in the frame (~{med_h:.0f} px tall). "
                    "Zoom in or film closer – pose accuracy drops below ~150 px."})
    if segments:
        cov = np.mean([s["metrics"].get("pose_coverage") or 0 for s in segments])
        if cov < 0.75:
            out.append({"level": "warn", "text": f"Pose found in only {cov * 100:.0f}% of skill frames "
                        "(occlusion, blur or a small athlete). Check the athlete selection."})
    if fps < 24:
        out.append({"level": "warn", "text": f"Low frame rate ({fps:.0f} fps): flight times are only "
                    f"accurate to ±{1000 / fps:.0f} ms. Film at 60 fps or more for skills."})
    elif fps >= 90:
        out.append({"level": "good", "text": f"High-speed video ({fps:.0f} fps) – excellent timing precision."})
    if not cam_static:
        out.append({"level": "info", "text": "Camera movement detected and compensated. "
                    "A fixed, side-on camera gives the most accurate angles."})
    return out


def build_analysis(vid: str, seg_params: dict | None = None, manual: list | None = None) -> dict:
    meta = lib.get(vid)
    b = _signals(vid)
    sig, fps = b["sig"], b["fps"]
    params = {**settings.get()["segment"], **(seg_params or {})}
    labels = None
    if manual is not None:
        segs = [(int(m["start"]), int(m["end"])) for m in manual if int(m["end"]) > int(m["start"])]
        labels = {(int(m["start"]), int(m["end"])): m.get("label") for m in manual if m.get("custom_label")}
        params["manual"] = True
    else:
        segs = detect_segments(sig, b["phases"], b["act"], fps, b["shot"], preset=params["preset"],
                               sensitivity=params["sensitivity"], pre_pad=params["pre_pad"],
                               post_pad=params["post_pad"], min_duration=params["min_duration"])
        params["manual"] = False
    segments = _analyze_segments(b, segs, labels)

    kps = b["kps"]
    com_img = center_of_mass(kps[..., :2], np.nan_to_num(kps[..., 2]))
    com_img[~sig["valid"]] = np.nan
    first = np.where(np.isfinite(sig["phi_u"]))[0]
    rot = sig["phi_u"] - (sig["phi_u"][first[0]] if len(first) else 0)
    A = np.load(lib.vdir(vid) / "motion.npz")["A"]
    cam_static = bool(np.nanmax(np.abs(A[:, :2, 2])) < 1.0) if len(A) else True
    quality = _quality(vid, b, segments, cam_static)
    ang = sig["angles"]
    payload = {
        "version": 2,
        "fps": fps, "n_frames": int(len(kps)), "width": meta["width"], "height": meta["height"],
        "kps": _encode_kps(kps),
        "com": _arr(com_img, 1),
        "signals": {
            "activity": _arr(b["act"], 3),
            "inversion": _arr(sig["inversion"], 1),
            "rotation": _arr(rot, 1),
            "speed": _arr(sig["speed"], 2),
            "elev": _arr(sig["elev"], 3),
            "com_h": _arr(sig["com_h"], 3),
            **{k: _arr(v, 1) for k, v in ang.items()},
        },
        "phases": "".join(PHASE_CHARS[int(p)] for p in b["phases"]),
        "cuts": np.where(b["cuts"])[0].tolist(),
        "segments": segments,
        "segment_params": params,
        "presets": {k: v["label"] for k, v in PRESETS.items()},
        "scale": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in b["scale"].items()},
        "bl_px": round(float(sig["bl_global"]), 1),
        "athlete": {"chain": b["chain"], "main": b["main"]},
        "camera": {"static": cam_static},
        "quality": quality,
        "generated": time.time(),
    }
    if not cam_static:
        # per-frame frame->world affine [a, b, tx, c, d, ty] + world COM, so the UI can
        # draw a trail that stays glued to the floor while the camera pans
        Wm = b["W"][:, :2, :].reshape(len(kps), 6)
        payload["cam"] = [[round(float(v), 5) if i % 3 != 2 else round(float(v), 1) for i, v in enumerate(r)] for r in Wm]
        payload["com_w"] = _arr(sig["com"], 1)
    lib.write_json(vid, "analysis.json", payload)
    lib.update(vid, segments=len(segments), labels=[s["label"] for s in segments][:8])
    return payload


# ----------------------------------------------------------------------------- jobs
def queue_focus_exports(vid: str, segments: list) -> int:
    from .jobs import manager
    from .render import run_export
    for seg in segments:
        manager.submit("cpu", "export", f"Focus video #{seg['id']}", vid, run_export, vid, "reframe",
                       seg_id=int(seg["id"]),
                       opts={"aspect": "source", "zoom": 1.0, "dynamic": True, "action_only": True, "overlay": False})
    return len(segments)


def job_process(job, vid: str, from_stage: str = "prepare", forced_tid: int | None = None):
    lib.update(vid, status="processing", error=None)
    try:
        if from_stage == "prepare":
            stage_prepare(job, vid)
        if from_stage in ("prepare", "track"):
            stage_track(job, vid)
        stage_athlete(job, vid, forced_tid=forced_tid,
                      p0=0.62 if from_stage != "athlete" else 0.0, p1=0.95)
        job.update("Analysing skills", 0.96)
        prev = lib.read_json(vid, "analysis.json")
        params = prev.get("segment_params") if prev else None
        if params:
            params = {k: v for k, v in params.items() if k != "manual"}
        res = build_analysis(vid, seg_params=params)
        s = analysis_settings(vid)
        lib.update(vid, status="ready", processed=time.time(),
                   model_used=Path(s["pose_model"]).stem.replace("-pose", ""))
        if settings.get().get("auto_focus"):
            queue_focus_exports(vid, res["segments"])
        return {"segments": len(res["segments"])}
    except JobCancelled:
        lib.update(vid, status="cancelled")
        raise
    except Exception as e:
        lib.update(vid, status="error", error=f"{type(e).__name__}: {e}")
        raise
