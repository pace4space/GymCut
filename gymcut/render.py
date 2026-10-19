"""Exports: trimmed clips, skeleton overlays, auto-reframe, sequence photos, CSV."""
from __future__ import annotations

import csv
import math
import subprocess
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from . import library as lib
from .camera_motion import apply_transform
from .config import EXPORTS_DIR, FFMPEG
from .draw import ACCENT, draw_angle, draw_skeleton, draw_text, draw_trail
from .kinematics import nan_gauss
from .media import FrameWriter, cut_clip, even
from .pipeline import _signals
from .pose import KP_NAMES

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class SegmentFrames:
    """Frame-accurate decode of frames [start, end) via ffmpeg (works for any codec/GOP)."""

    def __init__(self, path: Path, start: int, end: int, fps: float, w: int, h: int):
        self.n = max(0, end - start)
        self.w, self.h = w, h
        t0 = max(0.0, (start - 0.5) / fps) if start > 0 else 0.0
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", f"{t0:.6f}", "-i", str(path),
               "-frames:v", str(self.n), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     creationflags=_NO_WINDOW, bufsize=w * h * 3 * 4)

    def __iter__(self):
        size = self.w * self.h * 3
        for _ in range(self.n):
            buf = self.proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(self.h, self.w, 3).copy()
        self.close()

    def close(self):
        try:
            self.proc.stdout.close()
        except Exception:
            pass
        self.proc.wait(timeout=10)


_tls = threading.local()


def _out_path(vid: str, label: str, suffix: str, ext: str) -> Path:
    d = EXPORTS_DIR / vid
    d.mkdir(parents=True, exist_ok=True)
    meta = lib.get(vid)
    base = f"{lib.safe_name(meta.get('name', vid), 40)}_{lib.safe_name(label, 40)}_{suffix}"
    p = d / f"{base}.{ext}"
    if getattr(_tls, "replace", False):     # batch exports overwrite instead of piling up _2, _3 …
        return p
    i = 2
    while p.exists():
        p = d / f"{base}_{i}.{ext}"
        i += 1
    return p


def _segment(vid: str, seg_id: int | None, start: int | None, end: int | None):
    a = lib.read_json(vid, "analysis.json")
    if a is None:
        raise RuntimeError("video is not analysed yet")
    if seg_id is not None:
        seg = next((s for s in a["segments"] if s["id"] == seg_id), None)
        if seg is None:
            raise KeyError(f"segment {seg_id} not found")
        return a, seg, int(seg["start"]), int(seg["end"])
    s = int(start or 0)
    e = int(end if end is not None else a["n_frames"])
    return a, None, s, e


def _active_element(seg: dict | None, t: int):
    if not seg:
        return None
    for el in seg["elements"]:
        if el["frame0"] - 2 <= t <= el["frame1"] + 2:
            return el
    return None


# ----------------------------------------------------------------------------- overlay
class OverlayPainter:
    def __init__(self, vid: str, a: dict, seg: dict | None, opts: dict):
        b = _signals(vid)
        self.b, self.a, self.seg, self.opts = b, a, seg, opts
        self.kps = b["kps"]
        self.W = b["W"]
        self.com_w = b["sig"]["com"]
        self.fps = b["fps"]
        self.phases = b["phases"]
        self.rot = a["signals"]["rotation"]
        self.start = seg["start"] if seg else 0

    def paint(self, img: np.ndarray, t: int, hud: bool = True) -> np.ndarray:
        o = self.opts
        if t >= len(self.kps):
            return img
        k = self.kps[t]
        if o.get("trail", True) and np.isfinite(self.com_w[t]).all():
            lo = max(self.start, t - int(1.2 * self.fps))
            pts = self.com_w[lo:t + 1]
            try:
                inv = np.linalg.inv(self.W[t])
                pts = (pts @ inv[:2, :2].T) + inv[:2, 2]
                draw_trail(img, pts)
            except np.linalg.LinAlgError:
                pass
        if o.get("skeleton", True):
            draw_skeleton(img, k, conf_thr=0.3)
        if o.get("angles", False) and np.isfinite(k[:, 0]).any():
            for (p, q, r) in ((11, 13, 15), (12, 14, 16), (5, 11, 13), (6, 12, 14), (5, 7, 9), (6, 8, 10)):
                draw_angle(img, k, p, q, r)
        if hud:
            self.hud(img, t)
        return img

    def hud(self, img: np.ndarray, t: int) -> np.ndarray:
        o = self.opts
        if t >= len(self.kps):
            return img
        if o.get("hud", True):
            h, w = img.shape[:2]
            y = int(28 * max(1.0, min(w, h) / 540.0))
            el = _active_element(self.seg, t)
            title = (el["name"] if el else (self.seg["label"] if self.seg else ""))
            if title:
                draw_text(img, title, (12, y), 0.7, color=(255, 255, 255))
            ph = {0: "", 1: "FEET", 2: "HANDS", 3: "AIR"}[int(self.phases[t])]
            rot = self.rot[t] - (self.rot[self.start] or 0) if self.rot[t] is not None and self.rot[self.start] is not None else None
            info = f"t {(t - self.start) / self.fps:5.2f}s"
            if rot is not None:
                info += f"   rot {abs(rot):4.0f}°".replace("°", " deg")
            if ph:
                info += f"   {ph}"
            draw_text(img, info, (12, y * 2), 0.5, color=(220, 230, 255))
        return img


def _speed_plan(fps: float, speed: float) -> tuple[float, int]:
    """Output fps and frame repeat so slow motion stays >= 24 fps."""
    out = fps * speed
    rep = 1
    while out * rep < 23.5 and rep < 8:
        rep += 1
    return out * rep, rep


def export_overlay(job, vid: str, seg_id=None, start=None, end=None, opts: dict | None = None) -> dict:
    opts = opts or {}
    a, seg, s, e = _segment(vid, seg_id, start, end)
    meta = lib.get(vid)
    fps, w, h = float(a["fps"]), int(a["width"]), int(a["height"])
    speed = float(opts.get("speed", 1.0))
    out_fps, rep = _speed_plan(fps, speed)
    label = seg["label"] if seg else f"{s / fps:.1f}-{e / fps:.1f}s"
    out = _out_path(vid, label, f"overlay{'_slow' if speed < 1 else ''}", "mp4")
    painter = OverlayPainter(vid, a, seg, opts)
    work = lib.vdir(vid) / "work.mp4"
    audio = (work, s / fps, (e - s) / fps) if (abs(speed - 1) < 1e-3 and meta.get("has_audio")) else None
    wr = FrameWriter(out, w, h, out_fps, audio_from=audio)
    try:
        for i, frame in enumerate(SegmentFrames(work, s, e, fps, w, h)):
            painter.paint(frame, s + i)
            for _ in range(rep):
                wr.write(frame)
            if i % 10 == 0:
                job.update("Rendering overlay", i / max(1, e - s))
    finally:
        wr.close()
    return {"file": out.name, "kind": "video"}


# ----------------------------------------------------------------------------- reframe
def _athlete_boxes(kps: np.ndarray) -> np.ndarray:
    xy = np.where(kps[..., 2:3] >= 0.25, kps[..., :2], np.nan)
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            lo = np.nanmin(xy, 1)
            hi = np.nanmax(xy, 1)
    return np.concatenate([lo, hi], 1)


ASPECTS = {"9:16": 9 / 16, "4:5": 4 / 5, "1:1": 1.0, "16:9": 16 / 9}


def _action_range(seg: dict | None, s: int, e: int, fps: float) -> tuple[int, int]:
    """Trim to the action itself: from just before the first element to the landing hold."""
    if not seg or not seg.get("elements"):
        return s, e
    els = seg["elements"]
    a0 = els[0]["frame0"] - int(0.7 * fps)
    land = (seg.get("landing") or {}).get("frame") or els[-1]["frame1"]
    a1 = land + int(1.0 * fps)
    return max(s, a0), min(e, max(a1, a0 + int(1.0 * fps)))


def _virtual_camera(b: dict, s: int, e: int, W: int, H: int, fps: float, ar: float, zoom: float, dynamic: bool):
    """Per-frame crop window (cx, cy, cw, ch) that follows the athlete.

    dynamic=True: the window also zooms with the body's extent (tight on a tuck,
    wider on a layout or split). Extents use a rolling max over +-0.4 s so limbs are
    never cut, then heavy Gaussian smoothing gives a calm, broadcast-like camera.
    """
    boxes = _athlete_boxes(b["kps"][s:e])
    n = e - s
    cx = (boxes[:, 0] + boxes[:, 2]) / 2
    cy = (boxes[:, 1] + boxes[:, 3]) / 2
    bh = boxes[:, 3] - boxes[:, 1]
    bw = boxes[:, 2] - boxes[:, 0]
    if not np.isfinite(cx).any():
        raise RuntimeError("no athlete pose in this range")
    idx = np.arange(n)

    def fill(x):
        ok = np.isfinite(x)
        return np.interp(idx, idx[ok], x[ok]) if ok.any() else np.full(n, np.nan)

    need = np.maximum(fill(bh) * 1.9, fill(bw) * 1.9 / ar) / zoom      # crop height that fits the body
    if dynamic:
        k = max(1, int(0.4 * fps))
        pad = np.pad(need, k, mode="edge")
        need = np.array([pad[i:i + 2 * k + 1].max() for i in range(n)])
        ch = nan_gauss(need, 0.45 * fps)
    else:
        ch = np.full(n, np.nanpercentile(need, 95))
    ch = np.clip(ch, H * 0.25, H)
    cw = ch * ar
    over = cw > W
    cw[over] = W
    ch[over] = W / ar
    sig = max(1.0, 0.35 * fps)
    cx = fill(nan_gauss(cx, sig))
    cy = fill(nan_gauss(cy - 0.05 * ch, sig))
    cx = np.clip(cx, cw / 2, W - cw / 2)
    cy = np.clip(cy, ch / 2, H - ch / 2)
    return cx, cy, cw, ch


def export_reframe(job, vid: str, seg_id=None, start=None, end=None, opts: dict | None = None) -> dict:
    """Focus video: a virtual camera that follows (and optionally zooms on) the athlete,
    trimmed to the skill - or to the action only."""
    opts = opts or {}
    a, seg, s, e = _segment(vid, seg_id, start, end)
    fps, W, H = float(a["fps"]), int(a["width"]), int(a["height"])
    if opts.get("action_only"):
        s, e = _action_range(seg, s, e, fps)
    b = _signals(vid)
    ar = (W / H) if opts.get("aspect") == "source" else ASPECTS.get(opts.get("aspect", "9:16"), 9 / 16)
    cx, cy, cw, ch = _virtual_camera(b, s, e, W, H, fps, ar, float(opts.get("zoom", 1.0)), bool(opts.get("dynamic", True)))
    oh = even(int(min(1920, max(640, round(float(np.median(ch)) * 1.4)))))
    ow = even(oh * ar)
    speed = float(opts.get("speed", 1.0))
    out_fps, rep = _speed_plan(fps, speed)
    label = seg["label"] if seg else f"{s / fps:.1f}-{e / fps:.1f}s"
    out = _out_path(vid, label, f"focus_{opts.get('aspect', '9:16').replace(':', 'x')}", "mp4")
    painter = OverlayPainter(vid, a, seg, opts) if opts.get("overlay") else None
    work = lib.vdir(vid) / "work.mp4"
    meta = lib.get(vid)
    audio = (work, s / fps, (e - s) / fps) if (abs(speed - 1) < 1e-3 and meta.get("has_audio")) else None
    wr = FrameWriter(out, ow, oh, out_fps, audio_from=audio)
    try:
        for i, frame in enumerate(SegmentFrames(work, s, e, fps, W, H)):
            if painter:
                painter.paint(frame, s + i, hud=False)
            crop = cv2.getRectSubPix(frame, (int(round(cw[i])), int(round(ch[i]))), (float(cx[i]), float(cy[i])))
            crop = cv2.resize(crop, (ow, oh), interpolation=cv2.INTER_CUBIC if ow > cw[i] else cv2.INTER_AREA)
            if painter:
                painter.hud(crop, s + i)
            for _ in range(rep):
                wr.write(crop)
            if i % 10 == 0:
                job.update("Rendering focus video", i / max(1, e - s))
    finally:
        wr.close()
    return {"file": out.name, "kind": "video"}


def export_unreal(job, vid: str, seg_id=None, start=None, end=None, opts: dict | None = None) -> dict:
    """Image sequence for Unreal's MetaHuman Animator markerless body solve.

    Athlete-centred, fixed-size crop (a steady virtual camera - the solver prefers
    it), trimmed to the action, JPG frames at an integer frame rate plus a
    gymcut_attempt.json with the analysis (elements, takeoff/landing frames, metrics).
    Cropping also keeps coaches and other gymnasts out of the solve.
    """
    import json as _json
    opts = opts or {}
    a, seg, s, e = _segment(vid, seg_id, start, end)
    fps, W, H = float(a["fps"]), int(a["width"]), int(a["height"])
    s, e = _action_range(seg, s, e, fps)
    b = _signals(vid)
    crop = opts.get("crop", True)
    if crop:
        cx, cy, cw, ch = _virtual_camera(b, s, e, W, H, fps, W / H, float(opts.get("zoom", 0.8)), False)
        ow, oh = even(min(W, max(960, cw[0]))), 0
        oh = even(ow * H / W)
    label = seg["label"] if seg else f"{s / fps:.1f}-{e / fps:.1f}s"
    if opts.get("dest_dir"):
        folder = Path(opts["dest_dir"])
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("frame_*.jpg"):
            old.unlink()
    else:
        folder = _out_path(vid, label, "unreal", "seq")
        folder.mkdir(parents=True)
    # Unreal wants a standard rate; phone clips are often 59.4 / 29.7 fps
    rate = min((24, 25, 30, 50, 60, 120, 240), key=lambda r: abs(r - fps))
    for i, frame in enumerate(SegmentFrames(lib.vdir(vid) / "work.mp4", s, e, fps, W, H)):
        img = frame
        if crop:
            img = cv2.getRectSubPix(frame, (int(round(cw[i])), int(round(ch[i]))), (float(cx[i]), float(cy[i])))
            img = cv2.resize(img, (ow, oh), interpolation=cv2.INTER_CUBIC if ow > cw[i] else cv2.INTER_AREA)
        cv2.imwrite(str(folder / f"frame_{i + 1:05d}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if i % 10 == 0:
            job.update("Writing image sequence", i / max(1, e - s))
    meta = lib.get(vid)
    info = {
        "source_video": meta.get("name"), "athlete": meta.get("athlete"), "recorded": lib.recorded_date(meta),
        "skill": label, "frame_rate": rate, "source_fps": fps, "frames": e - s, "first_source_frame": s,
        "cropped": bool(crop),
        "elements": [{k: el.get(k) for k in ("name", "type", "flight_time", "rotations", "shape")} |
                     {"frame0": el["frame0"] - s, "frame1": el["frame1"] - s} for el in (seg or {}).get("elements", [])],
        "landing": (seg or {}).get("landing"), "metrics": (seg or {}).get("metrics"),
    }
    (folder / "gymcut_attempt.json").write_text(_json.dumps(info, indent=2, default=str), "utf-8")
    if opts.get("dest_dir"):
        return {"file": None, "kind": "data", "folder": str(folder)}
    manifest = folder.with_suffix(".json")
    manifest.write_text(_json.dumps({**info, "image_sequence_folder": str(folder)}, indent=2, default=str), "utf-8")
    return {"file": manifest.name, "kind": "data", "folder": str(folder)}


# ----------------------------------------------------------------------------- trim
def export_clip(job, vid: str, seg_id=None, start=None, end=None, opts: dict | None = None) -> dict:
    opts = opts or {}
    a, seg, s, e = _segment(vid, seg_id, start, end)
    fps = float(a["fps"])
    speed = float(opts.get("speed", 1.0))
    label = seg["label"] if seg else f"{s / fps:.1f}-{e / fps:.1f}s"
    out = _out_path(vid, label, "clip" if speed == 1 else "clip_slow", "mp4")
    cut_clip(lib.vdir(vid) / "work.mp4", s / fps, e / fps, out, speed=speed,
             progress=lambda f: job.update("Cutting clip", f))
    return {"file": out.name, "kind": "video"}


# ----------------------------------------------------------------------------- sequence photo
def _pick_frames(b: dict, s: int, e: int, seg: dict | None, n_max: int = 9) -> list[int]:
    sig, fps = b["sig"], b["fps"]
    lo, hi = s, e
    if seg and seg["elements"]:
        lo = max(s, seg["elements"][0]["frame0"] - int(0.25 * fps))
        hi = min(e, seg["elements"][-1]["frame1"] + int(0.35 * fps))
    com = sig["com"]
    bl = np.nanmedian(sig["bl"][lo:hi]) if np.isfinite(sig["bl"][lo:hi]).any() else 100
    picks, last = [], None
    min_gap = max(2, int((hi - lo) / (n_max * 1.6)))
    for t in range(lo, hi):
        if not sig["valid"][t]:
            continue
        if last is None:
            picks.append(t)
            last = t
            continue
        d = np.linalg.norm(com[t] - com[last]) / bl if np.isfinite(com[t]).all() and np.isfinite(com[last]).all() else 0
        if (t - last >= min_gap and d >= 0.45) or (t - last) >= 3 * min_gap:
            picks.append(t)
            last = t
    if len(picks) > n_max:
        sel = np.linspace(0, len(picks) - 1, n_max).round().astype(int)
        picks = [picks[i] for i in sel]
    return picks


def export_sequence(job, vid: str, seg_id=None, start=None, end=None, opts: dict | None = None) -> dict:
    """Chronophotography: athlete at several instants composited over one background.

    Each sampled frame is warped into a common reference frame using the camera
    motion, a median background is built, and athlete pixels are cut out by
    background difference restricted to the (dilated) pose region.
    """
    opts = opts or {}
    a, seg, s, e = _segment(vid, seg_id, start, end)
    b = _signals(vid)
    fps, W, H = float(a["fps"]), int(a["width"]), int(a["height"])
    picks = _pick_frames(b, s, e, seg, int(opts.get("count", 9)))
    if len(picks) < 2:
        raise RuntimeError("not enough athlete frames for a sequence photo")
    job.update("Collecting frames", 0.05)
    # background samples: spread across a wider window
    span_lo, span_hi = max(0, s - int(1.0 * fps)), min(int(a["n_frames"]), e + int(1.0 * fps))
    bg_idx = sorted(set(np.linspace(span_lo, span_hi - 1, 24).round().astype(int).tolist()))
    need = sorted(set(picks) | set(bg_idx))
    frames = {}
    for i, f in enumerate(SegmentFrames(lib.vdir(vid) / "work.mp4", need[0], need[-1] + 1, fps, W, H)):
        t = need[0] + i
        if t in need:
            frames[t] = f
    job.update("Building background", 0.4)
    ref = picks[len(picks) // 2]
    Wm = b["W"]
    Wref_inv = np.linalg.inv(Wm[ref])
    mats = {t: (Wref_inv @ Wm[t]) for t in frames}
    corners = np.array([[0, 0, 1], [W, 0, 1], [W, H, 1], [0, H, 1]], float).T
    pts = np.concatenate([(mats[t] @ corners)[:2].T for t in picks])
    x0, y0 = np.floor(pts.min(0)).astype(int)
    x1, y1 = np.ceil(pts.max(0)).astype(int)
    if (x1 - x0) > 3.5 * W or (y1 - y0) > 2.5 * H:
        x0, y0, x1, y1 = 0, 0, W, H
    cw, ch = int(x1 - x0), int(y1 - y0)
    T0 = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], float)

    def warp(img, t, interp=cv2.INTER_LINEAR):
        M = (T0 @ mats[t])[:2]
        return cv2.warpAffine(img, M, (cw, ch), flags=interp, borderMode=cv2.BORDER_CONSTANT, borderValue=0)

    stack, valid = [], []
    for t in sorted(frames):
        wimg = warp(frames[t], t)
        vm = warp(np.full((H, W), 255, np.uint8), t, cv2.INTER_NEAREST) > 0
        stack.append(wimg)
        valid.append(vm)
    stack = np.stack(stack).astype(np.float32)
    valid = np.stack(valid)
    stack[~valid] = np.nan
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            bg = np.nanmedian(stack, 0)
    bg = np.nan_to_num(bg, nan=0).astype(np.uint8)
    canvas = bg.copy()
    job.update("Compositing athlete", 0.7)
    kps = b["kps"]
    n = len(picks)
    for j, t in enumerate(picks):
        wimg = warp(frames[t], t)
        k = kps[t].copy()
        ok = k[:, 2] >= 0.25
        if ok.sum() < 5:
            continue
        M = (T0 @ mats[t])
        k_xy = (np.c_[k[:, :2], np.ones(17)] @ M.T)[:, :2]
        region = np.zeros((ch, cw), np.uint8)
        bl = float(np.nanmedian(b["sig"]["bl"][s:e])) if np.isfinite(b["sig"]["bl"][s:e]).any() else 100.0
        rad = max(6, int(bl * 0.18))
        for (p, q) in ((5, 7), (7, 9), (6, 8), (8, 10), (5, 6), (5, 11), (6, 12), (11, 12),
                       (11, 13), (13, 15), (12, 14), (14, 16), (0, 5), (0, 6)):
            if ok[p] and ok[q]:
                cv2.line(region, tuple(k_xy[p].astype(int)), tuple(k_xy[q].astype(int)), 255, rad * 2)
        if ok[0]:
            cv2.circle(region, tuple(k_xy[0].astype(int)), int(rad * 1.5), 255, -1)
        region = cv2.dilate(region, np.ones((rad, rad), np.uint8))
        diff = cv2.absdiff(wimg, bg).max(2)
        fg = ((diff > 28) & (region > 0)).astype(np.uint8) * 255
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        if fg.sum() < 255 * 50:  # background subtraction failed (static athlete) -> use pose region
            fg = cv2.erode(region, np.ones((rad // 2 + 1, rad // 2 + 1), np.uint8))
        alpha = cv2.GaussianBlur(fg, (0, 0), 1.5).astype(np.float32)[..., None] / 255.0
        canvas = (canvas * (1 - alpha) + wimg * alpha).astype(np.uint8)
        if opts.get("skeleton"):
            kk = k.copy()
            kk[:, :2] = k_xy
            draw_skeleton(canvas, kk, 0.3)
        if opts.get("numbers", True):
            top = k_xy[ok][np.argmin(k_xy[ok][:, 1])]
            draw_text(canvas, str(j + 1), (top[0] - 6, top[1] - rad * 1.5), 0.5)
    label = seg["label"] if seg else f"{s / fps:.1f}-{e / fps:.1f}s"
    out = _out_path(vid, label, "sequence", "jpg")
    cv2.imwrite(str(out), canvas, [cv2.IMWRITE_JPEG_QUALITY, 93])
    return {"file": out.name, "kind": "image"}


# ----------------------------------------------------------------------------- data
def export_csv(job, vid: str, seg_id=None, start=None, end=None, opts: dict | None = None) -> dict:
    a, seg, s, e = _segment(vid, seg_id, start, end)
    b = _signals(vid)
    fps = b["fps"]
    sig = b["sig"]
    label = seg["label"] if seg else f"{s / fps:.1f}-{e / fps:.1f}s"
    out = _out_path(vid, label, "data", "csv")
    ang_keys = list(sig["angles"].keys())
    with open(out, "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        head = ["frame", "time_s", "phase", "inversion_deg", "rotation_deg", "speed_bl_s", "elevation_bl",
                "com_x", "com_y"] + ang_keys
        for n in KP_NAMES:
            head += [f"{n}_x", f"{n}_y", f"{n}_conf"]
        wr.writerow(head)
        r0 = sig["phi_u"][s] if np.isfinite(sig["phi_u"][s]) else 0
        for t in range(s, e):
            def f(x, nd=2):
                return "" if x is None or not np.isfinite(x) else round(float(x), nd)
            row = [t, round((t - s) / fps, 4), "-FHA"[int(b["phases"][t])], f(sig["inversion"][t], 1),
                   f(sig["phi_u"][t] - r0, 1), f(sig["speed"][t]), f(sig["elev"][t], 3),
                   f(b["kps"][t, 11, 0] * 0 + sig["com"][t, 0], 1), f(sig["com"][t, 1], 1)]
            row += [f(sig["angles"][k][t], 1) for k in ang_keys]
            for j in range(17):
                row += [f(b["kps"][t, j, 0], 1), f(b["kps"][t, j, 1], 1), f(b["kps"][t, j, 2], 2)]
            wr.writerow(row)
    return {"file": out.name, "kind": "data"}


EXPORTERS = {"clip": export_clip, "overlay": export_overlay, "reframe": export_reframe,
             "sequence": export_sequence, "csv": export_csv, "unreal": export_unreal}


def run_export(job, vid: str, mode: str, seg_id=None, start=None, end=None, opts=None) -> dict:
    fn = EXPORTERS.get(mode)
    if fn is None:
        raise ValueError(f"unknown export mode {mode}")
    t = time.time()
    _tls.replace = bool((opts or {}).get("replace"))
    try:
        res = fn(job, vid, seg_id=seg_id, start=start, end=end, opts=opts or {})
    finally:
        _tls.replace = False
    res["vid"] = vid
    res["url"] = f"/api/exports/{vid}/{res['file']}"
    res["seconds"] = round(time.time() - t, 1)
    return res
