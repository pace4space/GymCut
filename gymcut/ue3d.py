"""3D replays through Unreal Engine (MetaHuman Animator markerless body solve).

One background job per attempt:
  ensure editor -> export frames -> body solve -> (ghost = personal best, solved too)
  -> build stage per camera angle -> Movie Render Queue -> labelled MP4 in the exports.
The editor is driven through Epic's Python remote execution (gymcut.unreal_bridge).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import library as lib
from .config import EXPORTS_DIR, FFMPEG, settings
from .jobs import JobCancelled
from .unreal_bridge import UE_ROOT, UnrealEditor

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ANGLE_LABELS = {"side": "Side view", "front": "Front view", "high": "High view", "orbit": "Orbit"}
# athlete colours in the 3D replay (picked by name so an athlete always has the same one)
PALETTE = [(0.16, 0.62, 0.52), (0.95, 0.55, 0.12), (0.30, 0.50, 0.95), (0.85, 0.75, 0.20),
           (0.55, 0.35, 0.90), (0.20, 0.75, 0.30), (0.90, 0.30, 0.30), (0.25, 0.75, 0.85)]
FREEZE_S = 0.6          # seconds of source time held at the peak (doubled by the ½-speed playback)


def athlete_color(name: str | None) -> tuple:
    if not name:
        return PALETTE[0]
    return PALETTE[sum(ord(c) * (i + 1) for i, c in enumerate(name.lower())) % len(PALETTE)]


def project_file() -> Path:
    from .config import ROOT
    p = settings.get().get("ue_project")
    default = ROOT / "unreal" / "GymCutUE" / "GymCutUE.uproject"
    # older settings pointed at F:\Code\GymCutUE (before the project moved into this repo)
    return Path(p) if p and Path(p).exists() else default


def scripts_dir() -> Path:
    return project_file().parent / "Scripts"


def movies_dir() -> Path:
    return project_file().parent / "Content" / "Movies" / "GymCut"


def take_name(vid: str, seg_id: int) -> str:
    return "GC_" + re.sub(r"[^A-Za-z0-9]", "_", vid) + f"_s{seg_id}"


# ---------------------------------------------------------------- editor control
def ping() -> bool:
    try:
        with UnrealEditor(timeout=3) as ue:
            return bool(ue.run("print('ok')").get("success"))
    except Exception:
        return False


def ensure_editor(job=None, timeout: float = 900) -> None:
    if ping():
        return
    exe = UE_ROOT / "Engine/Binaries/Win64/UnrealEditor.exe"
    if job:
        job.update("Starting Unreal Editor", None, "first start compiles shaders – can take a few minutes")
    subprocess.Popen([str(exe), str(project_file())], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    t0 = time.time()
    while time.time() - t0 < timeout:
        if job:
            job.update(None, None)          # raises if cancelled
        if ping():
            time.sleep(5)                   # let the editor finish its first ticks
            return
        time.sleep(5)
    raise RuntimeError("Unreal Editor did not start (is UE 5.8 installed with the MetaHuman body tracker?)")


def run_script(script: str, **params) -> str:
    code = "".join(f"{k} = {v!r}\n" for k, v in params.items())
    code += (scripts_dir() / script).read_text("utf-8")
    with UnrealEditor(timeout=30) as ue:
        res = ue.run(code)
    out = "\n".join(o.get("output", "") for o in res.get("output", []))
    if not res.get("success"):
        raise RuntimeError(f"{script} failed: {res.get('result')}\n{out[-500:]}")
    return out


# ---------------------------------------------------------------- takes
def stage_take(job, vid: str, seg_id: int) -> str:
    """Export the attempt's athlete-centred frames into the UE project (skipped if current)."""
    from .render import export_unreal
    take = take_name(vid, seg_id)
    dest = movies_dir() / take
    a_mtime = (lib.vdir(vid) / "analysis.json").stat().st_mtime
    marker = dest / "gymcut_attempt.json"
    if marker.exists() and json.loads(marker.read_text("utf-8")).get("analysis_mtime") == a_mtime:
        return take
    job.update(f"Exporting frames ({take})")
    export_unreal(job, vid, seg_id=seg_id, opts={"dest_dir": str(dest)})
    info = json.loads(marker.read_text("utf-8"))
    info["analysis_mtime"] = a_mtime
    marker.write_text(json.dumps(info, indent=2, default=str), "utf-8")
    (dest / "solved.json").unlink(missing_ok=True)
    return take


def solve_take(job, take: str, p0: float, p1: float) -> None:
    dest = movies_dir() / take
    if (dest / "solved.json").exists():
        return
    info = json.loads((dest / "gymcut_attempt.json").read_text("utf-8"))
    frames = int(info["frames"])
    run_script("gymcut_take.py", TAKE=take, STEP="setup")
    run_script("gymcut_take.py", TAKE=take, STEP="start")
    t0 = time.time()
    est = max(60.0, frames * 2.0)          # ~2 s per frame on an RTX 3070
    while True:
        time.sleep(10)
        elapsed = time.time() - t0
        job.update(f"Solving 3D body ({take})", p0 + (p1 - p0) * min(0.95, elapsed / est),
                   f"{elapsed / 60:.1f} min · ~{max(0, est - elapsed) / 60:.0f} min left")
        if "processing=False" in run_script("gymcut_take.py", TAKE=take, STEP="status"):
            break
    out = run_script("gymcut_take.py", TAKE=take, STEP="export")
    if "exported: None" in out:
        raise RuntimeError(f"body solve produced no animation for {take}")
    run_script("dump_anim_pose.py", TAKE=take)
    (dest / "solved.json").write_text(json.dumps({"solved": time.time(), "seconds": time.time() - t0}), "utf-8")


def saved_dir() -> Path:
    return project_file().parent / "Saved" / "GymCut"


def store_metrics3d(vid: str, seg_id: int, take: str) -> dict | None:
    """3D coaching measurements from the solved take -> video meta (metrics3d[seg_id])."""
    from .metrics3d import compute_take
    try:
        res = compute_take(saved_dir(), movies_dir(), take)
    except FileNotFoundError:
        return None
    res["take"] = take
    m3d = dict(lib.get(vid).get("metrics3d") or {})
    m3d[str(seg_id)] = res
    lib.update(vid, metrics3d=m3d)
    return res


def takeoff_seconds(take: str) -> float:
    info = json.loads((movies_dir() / take / "gymcut_attempt.json").read_text("utf-8"))
    els = [e for e in info.get("elements") or [] if e.get("type") != "hurdle"]
    # align on the flight element (salto / jump) - that is what the coach compares; round-off
    # speed differs between attempts and would put the two saltos out of step
    flight = [e for e in els if e.get("type") in ("salto", "jump") and (e.get("flight_time") or 0) > 0]
    key = flight[0] if flight else (els[0] if els else None)
    return (key["frame0"] / info["frame_rate"]) if key else 0.0


# ---------------------------------------------------------------- personal best
def _skill_key(label: str) -> str:
    return re.sub(r"\s*\+\s*~[\d.]+ twist", "", label or "").strip()


def best_attempt(vid: str, seg: dict) -> tuple[str, int] | None:
    """The athlete's best other attempt of the same skill (longest flight, stuck landing bonus)."""
    athlete = lib.get(vid).get("athlete") or ""
    key = _skill_key(seg["label"])
    best, best_score = None, -1e9
    for m in lib.list_all():
        if m.get("status") != "ready" or (m.get("athlete") or "") != athlete:
            continue
        a = lib.read_json(m["id"], "analysis.json") or {}
        for s in a.get("segments", []):
            if (m["id"], s["id"]) == (vid, seg["id"]) or _skill_key(s["label"]) != key:
                continue
            score = (s["metrics"].get("max_flight_time") or 0) + (0.2 if (s.get("landing") or {}).get("result") == "stuck" else 0) \
                - 0.05 * len([n for n in s.get("notes", []) if n["level"] in ("warn", "bad")])
            if score > best_score:
                best, best_score = (m["id"], s["id"]), score
    return best


# ---------------------------------------------------------------- render + encode
def render_angle(job, suffix: str, angle: str, n_frames: int, p: float) -> Path:
    out = project_file().parent / "Saved" / "GymCut" / "Renders" / suffix / angle
    run_script("render_sequence.py", SUFFIX=suffix, ANGLE=angle)
    t0 = time.time()
    while time.time() - t0 < 1800:
        time.sleep(4)
        n = len(list(out.glob("frame.*.jp*g")))
        job.update(f"Rendering {ANGLE_LABELS.get(angle, angle)}", p, f"{n}/{n_frames} frames")
        if n >= n_frames - 1 and "False" in run_script_inline("import unreal\nprint(unreal.get_editor_subsystem(unreal.MoviePipelineQueueSubsystem).is_rendering())"):
            return out
    raise RuntimeError(f"render of {angle} timed out")


def run_script_inline(code: str) -> str:
    with UnrealEditor(timeout=30) as ue:
        res = ue.run(code)
    return "\n".join(o.get("output", "") for o in res.get("output", []))


def _esc(t: str) -> str:
    return t.replace("→", "›").replace(":", "\\:").replace("'", "").replace(",", "\\,")


def encode(parts: list[tuple], rate: int, out: Path, title: str) -> None:
    """Half-speed clips per angle with a caption, concatenated into one MP4.
    parts: (frames folder, label, freeze) - freeze = (frame, caption) holds that frame briefly."""
    tmp = out.parent / (out.stem + "_parts")
    tmp.mkdir(exist_ok=True)
    font = "C\\:/Windows/Fonts/segoeui.ttf"
    listing = []
    for i, (folder, label, freeze) in enumerate(parts):
        clip = tmp / f"part{i}.mp4"
        txt = _esc(f"{title}  ·  {label}  ·  ½ speed")
        vf = ""
        if freeze:
            f0, cap = freeze
            n = int(FREEZE_S * rate)
            vf = (f"loop=loop={n}:size=1:start={f0},setpts=N/({rate}*TB),"
                  f"drawtext=fontfile='{font}':text='{_esc(cap)}':x=(w-text_w)/2:y=36:fontsize=34:fontcolor=0xffd94d:"
                  f"box=1:boxcolor=black@0.5:boxborderw=12:enable='between(n\\,{f0}\\,{f0 + n})',")
        vf += (f"setpts=2*PTS,drawtext=fontfile='{font}':text='{txt}':x=24:y=h-48:fontsize=26:"
               f"fontcolor=white:box=1:boxcolor=black@0.45:boxborderw=10")
        cmd = [FFMPEG, "-y", "-loglevel", "error", "-framerate", str(rate), "-start_number", "0",
               "-i", str(folder / "frame.%04d.jpeg"), "-vf", vf, "-r", str(rate),
               "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "20", "-pix_fmt", "yuv420p", str(clip)]
        r = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW)
        if r.returncode != 0:
            cmd[cmd.index("h264_nvenc")] = "libx264"
            r = subprocess.run([c for c in cmd if c not in ("-preset", "p5", "-cq", "20")], capture_output=True, text=True, creationflags=_NO_WINDOW)
            if r.returncode != 0:
                raise RuntimeError("encode failed: " + r.stderr[-400:])
        listing.append(f"file '{clip.as_posix()}'")
    (tmp / "list.txt").write_text("\n".join(listing), "utf-8")
    r = subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"),
                        "-c", "copy", "-movflags", "+faststart", str(out)], capture_output=True, text=True, creationflags=_NO_WINDOW)
    shutil.rmtree(tmp, ignore_errors=True)
    if r.returncode != 0:
        raise RuntimeError("concat failed: " + r.stderr[-400:])


# ---------------------------------------------------------------- live 3D (Unreal editor puppet)
LIVE = {"state": "off", "session": None, "error": None, "message": "", "mode": None}
_live_lock = threading.Lock()


def _material_for(athlete: str | None) -> str:
    rgb = athlete_color(athlete)
    return "/Game/GymCut/Materials/Stage/M_Athlete_" + "".join(f"{int(c * 255):02x}" for c in rgb)


def live_status(session=None) -> dict:
    st = dict(LIVE)
    lf = getattr(session, "lifter", None) if session else None
    if lf is not None:
        st.update(lf.stats())
    st["mine"] = bool(session) and LIVE["session"] == getattr(session, "id", None)
    return st


def live_start(session) -> None:
    """Start the Unreal puppet and stream this live session's athlete to it (background thread)."""
    from .jobs import manager
    with _live_lock:
        if LIVE["state"] in ("starting", "on"):
            if LIVE["session"] == session.id:
                return
            raise RuntimeError("Live 3D is already running for another session")
        busy = [j for j in manager.list(active_only=True) if j["kind"] == "replay3d" and j["state"] == "running"]
        if busy:
            raise RuntimeError("Unreal is rendering a 3D replay – live 3D can start when it finishes")
        LIVE.update(state="starting", session=session.id, error=None, message="starting Unreal…")

    def run():
        try:
            if not ping():
                LIVE["message"] = "starting Unreal Editor (first start can take a few minutes)…"
                ensure_editor()
            LIVE["message"] = "building the live stage…"
            run_script("live_puppet.py", STEP="stop")         # ends a previous Simulate session (async)
            for _ in range(40):
                if "simulating=False" in run_script("live_puppet.py", STEP="status"):
                    break
                time.sleep(0.25)
            out = run_script("live_puppet.py", STEP="start", MATERIAL=_material_for(getattr(session, "athlete", None)))
            LIVE["mode"] = "livelink" if "live link source running" in out else "puppet"
            session.ensure_lifter().to_ue = True
            LIVE.update(state="on", message="")
        except Exception as e:  # noqa: BLE001
            LIVE.update(state="error", error=str(e)[:300], message="", session=None)

    threading.Thread(target=run, daemon=True).start()


def live_stop(session=None) -> None:
    lf = getattr(session, "lifter", None) if session else None
    if lf is not None:
        lf.to_ue = False
        session.release_lifter()          # keeps lifting if the browser 3D view is still open
    if LIVE["state"] in ("on", "starting"):
        try:
            run_script("live_puppet.py", STEP="stop")
        except Exception:  # noqa: BLE001 - editor may already be closed
            pass
    LIVE.update(state="off", session=None, message="")


# ---------------------------------------------------------------- job
def job_replay3d(job, vid: str, seg_id: int, ghost: bool = True, angles=("side", "front", "orbit")) -> dict:
    a = lib.read_json(vid, "analysis.json")
    seg = next((s for s in a["segments"] if s["id"] == seg_id), None)
    if seg is None:
        raise KeyError(f"segment {seg_id} not found")
    while LIVE["state"] in ("starting", "on"):            # Unreal is busy with live 3D (Simulate mode)
        job.update("Waiting – Unreal is running live 3D", 0.01, "stop “3D in Unreal” on the Live page to continue")
        time.sleep(5)
    ensure_editor(job)
    take = stage_take(job, vid, seg_id)
    ghost_take, ghost_label = None, None
    ref = best_attempt(vid, seg) if ghost else None
    span = 0.55 if ref else 0.7
    solve_take(job, take, 0.05, span * (0.5 if ref else 1.0) + 0.05)
    store_metrics3d(vid, seg_id, take)
    if ref:
        ghost_take = stage_take(job, ref[0], ref[1])
        solve_take(job, ghost_take, span * 0.5 + 0.05, span + 0.05)
        store_metrics3d(ref[0], ref[1], ghost_take)
        gm = lib.get(ref[0])
        ghost_label = f"{gm.get('name')} #{ref[1]}"
    info = json.loads((movies_dir() / take / "gymcut_attempt.json").read_text("utf-8"))
    rate = int(info["frame_rate"])
    suffix = take + (f"_vs_{ghost_take}" if ghost_take else "")
    parts = []
    for i, angle in enumerate(angles):
        p = 0.75 + 0.2 * i / max(1, len(angles))
        job.update(f"Building 3D stage ({ANGLE_LABELS.get(angle, angle)})", p)
        params = dict(TAKE=take, CAMERA=angle, COLOR=athlete_color(lib.get(vid).get("athlete")))
        if ghost_take:
            params.update(GHOST=ghost_take, ALIGN_A=takeoff_seconds(take), ALIGN_B=takeoff_seconds(ghost_take))
        out = run_script("build_stage.py", **params)
        m = re.search(r"\| ([\d.]+)s @ (\d+) fps", out)
        n_frames = int(float(m.group(1)) * int(m.group(2))) if m else 1
        pk = re.search(r"^peak ([\d.]+) ([\d.]+)", out, re.M)
        gp = re.search(r"^ghost_peak ([\d.]+)", out, re.M)
        freeze = None
        if pk and angle in ("side", "front"):
            cap = f"PEAK  ·  hips {float(pk.group(2)) / 100:.2f} m"
            if gp:
                cap += f"   (best {float(gp.group(1)) / 100:.2f} m)"
            freeze = (min(n_frames - 1, int(float(pk.group(1)) * rate)), cap)
        parts.append((render_angle(job, suffix, angle, n_frames, p), ANGLE_LABELS.get(angle, angle), freeze))
    job.update("Encoding video", 0.97)
    d = EXPORTS_DIR / vid
    d.mkdir(parents=True, exist_ok=True)
    name = f"{lib.safe_name(lib.get(vid).get('name', vid), 40)}_{lib.safe_name(seg['label'], 40)}_3d.mp4"
    out = d / name
    title = seg["label"] + (f"  vs ghost: best ({ghost_label})" if ghost_label else "")
    encode(parts, rate, out, title)
    meta = lib.get(vid)
    rep = dict(meta.get("replays3d") or {})
    rep[str(seg_id)] = {"file": out.name, "ghost": ghost_label, "created": time.time()}
    lib.update(vid, replays3d=rep)
    return {"file": out.name, "kind": "video", "url": f"/api/exports/{vid}/{out.name}", "ghost": ghost_label}
