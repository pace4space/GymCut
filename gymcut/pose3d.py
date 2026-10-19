"""3D joint data of solved attempts for the browser 3D viewer (Coach mode).

Source: the Unreal body solve's joint dump (Saved/GymCut/<take>_pose.json, world-space joint
centres in cm, z up) plus the take's gymcut_attempt.json for the video time mapping.
The personal-best ghost is aligned exactly like the Unreal replay stage: same pelvis position
at the flight element's takeoff and the same direction of travel for the whole pass.
"""
from __future__ import annotations

import json
import math

import numpy as np

from . import library as lib
from . import ue3d

JOINTS = ["pelvis", "spine_03", "spine_05", "neck_01", "head",
          "clavicle_l", "upperarm_l", "lowerarm_l", "hand_l", "clavicle_r", "upperarm_r", "lowerarm_r", "hand_r",
          "thigh_l", "calf_l", "foot_l", "ball_l", "thigh_r", "calf_r", "foot_r", "ball_r"]


def _load(take: str) -> tuple[dict, dict] | None:
    pp = ue3d.saved_dir() / f"{take}_pose.json"
    ip = ue3d.movies_dir() / take / "gymcut_attempt.json"
    if not pp.exists() or not ip.exists():
        return None
    return json.loads(pp.read_text()), json.loads(ip.read_text("utf-8"))


def _array(pose: dict) -> np.ndarray:
    """(frames, joints, 3) in cm; missing joints -> nan."""
    return np.array([[f.get(j) or [np.nan] * 3 for j in JOINTS] for f in pose["frames"]], float)


def _at(X: np.ndarray, fps: float, t: float) -> np.ndarray:
    return X[int(np.clip(round(t * fps), 0, len(X) - 1))]


def _pass_heading(X: np.ndarray, fps: float, info: dict, t_align: float) -> float:
    rate = float(info["frame_rate"])
    els = info.get("elements") or []
    if els:
        a = _at(X, fps, max(0.0, els[0]["frame0"] / rate - 0.3))[0]
        b = _at(X, fps, els[-1]["frame1"] / rate + 0.2)[0]
        if math.hypot(b[0] - a[0], b[1] - a[1]) > 60:
            return math.atan2(b[1] - a[1], b[0] - a[0])
    a, b = _at(X, fps, t_align - 0.4)[0], _at(X, fps, t_align + 0.4)[0]
    return math.atan2(b[1] - a[1], b[0] - a[0])


def _ground(X: np.ndarray) -> float:
    feet = X[:, [JOINTS.index(j) for j in ("ball_l", "ball_r", "foot_l", "foot_r")], 2]
    return float(np.nanpercentile(np.nanmin(feet, axis=1), 3)) - 3.0


def _elements(info: dict) -> list[dict]:
    rate = float(info["frame_rate"])
    return [{"name": e.get("name"), "type": e.get("type"), "t0": e["frame0"] / rate, "t1": e["frame1"] / rate}
            for e in info.get("elements") or []]


def _flight_peak(z: np.ndarray, fps: float, t_align: float) -> int:
    """Frame of the highest hips during the flight element (takeoff .. +1.2 s)."""
    lo, hi = max(0, int((t_align - 0.1) * fps)), min(len(z), int((t_align + 1.2) * fps) + 1)
    if hi - lo < 2:
        lo, hi = 0, len(z)
    return lo + int(np.nanargmax(z[lo:hi]))


def _pack(X: np.ndarray) -> list[list[float]]:
    return [[None if not np.isfinite(v) else round(float(v), 1) for v in f.reshape(-1)] for f in X]


def attempt(vid: str, seg_id: int, ghost: bool = True) -> dict | None:
    take = ue3d.take_name(vid, seg_id)
    got = _load(take)
    if not got:
        return None
    pose, info = got
    fps = float(pose["fps"])
    X = _array(pose)
    g = _ground(X)
    X[:, :, 2] -= g
    t_align = ue3d.takeoff_seconds(take)
    head_a = _pass_heading(X, fps, info, t_align)
    pelvis = X[:, 0, 2]
    k_peak = _flight_peak(pelvis, fps, t_align)
    out = {
        "joints": JOINTS, "fps": fps, "frames": _pack(X),
        "video_t0": info["first_source_frame"] / float(info["source_fps"]),   # video time of anim t = 0
        "rate": float(info["frame_rate"]) / float(info["source_fps"]),        # video seconds per anim second
        "align": t_align, "heading": head_a, "elements": _elements(info),
        "peak": {"t": k_peak / fps, "hip_cm": round(float(pelvis[k_peak]), 1)},
        "label": info.get("skill"), "ghost": None,
    }
    if not ghost:
        return out
    seg = next((s for s in (lib.read_json(vid, "analysis.json") or {}).get("segments", []) if s["id"] == seg_id), None)
    ref = ue3d.best_attempt(vid, seg) if seg else None
    got_b = _load(ue3d.take_name(*ref)) if ref else None
    if not got_b:
        return out
    pose_b, info_b = got_b
    fps_b = float(pose_b["fps"])
    Y = _array(pose_b)
    Y[:, :, 2] -= _ground(Y)
    tb = ue3d.takeoff_seconds(ue3d.take_name(*ref))
    yaw = head_a - _pass_heading(Y, fps_b, info_b, tb)
    c, s_ = math.cos(yaw), math.sin(yaw)
    R = np.array([[c, -s_, 0], [s_, c, 0], [0, 0, 1]])
    Yr = Y @ R.T
    off = _at(X, fps, t_align)[0] - _at(Yr, fps_b, tb)[0]
    off[2] = 0.0
    Yr += off
    pb = Yr[:, 0, 2]
    kb = _flight_peak(pb, fps_b, tb)
    rate_b = float(info_b["frame_rate"])
    els_b = info_b.get("elements") or []
    m = lib.get(ref[0])
    out["ghost"] = {
        "frames": _pack(Yr), "fps": fps_b,
        "shift": tb - t_align,                       # ghost anim time = athlete anim time + shift
        "from": max(0.0, els_b[0]["frame0"] / rate_b - 0.3) if els_b else 0.0,
        "peak": {"t": kb / fps_b, "hip_cm": round(float(pb[kb]), 1)},
        "label": f"{m.get('name')} #{ref[1]}", "recorded": lib.recorded_date(m),
    }
    return out
