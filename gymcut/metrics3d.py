"""Coaching measurements from the Unreal 3D body solve.

Input: the per-take joint dump written by GymCutUE/Scripts/dump_anim_pose.py
(world-space joint centres in cm, z up) plus the take's gymcut_attempt.json (element
windows in take frames). Unlike the 2D pose these are true 3D angles, so they do not
depend on the camera angle - and twist (rotation about the body's long axis) becomes
measurable at all.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

FEET = ("ball_l", "ball_r", "foot_l", "foot_r")
HANDS = ("hand_l", "hand_r")
AIR_CM = 12.0            # every foot and hand this far above the floor = airborne


def _arr(frames: list[dict], bone: str) -> np.ndarray:
    return np.array([f.get(bone) or [np.nan] * 3 for f in frames], dtype=float)


def _angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Angle at b (degrees) for every frame."""
    u, v = a - b, c - b
    cos = (u * v).sum(1) / (np.linalg.norm(u, axis=1) * np.linalg.norm(v, axis=1) + 1e-9)
    return np.degrees(np.arccos(np.clip(cos, -1, 1)))


def _unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-9)


def somersault_deg(trunk: np.ndarray) -> float:
    """Rotation of the trunk in its main plane of rotation (unwrapped, absolute degrees)."""
    u = _unit(trunk)
    if len(u) < 3:
        return 0.0
    w, vecs = np.linalg.eigh(u.T @ u)
    e1, e2 = vecs[:, 2], vecs[:, 1]               # plane of the two largest spreads
    ang = np.unwrap(np.arctan2(u @ e2, u @ e1))
    return float(np.degrees(abs(ang[-1] - ang[0])))


def twist_deg(trunk: np.ndarray, across: np.ndarray) -> float:
    """Rotation about the long axis: the shoulder+hip line, parallel-transported along the
    trunk direction frame to frame so somersaulting itself does not count as twist."""
    a = _unit(trunk)
    total = 0.0
    for k in range(len(a) - 1):
        s0 = across[k] - (across[k] @ a[k]) * a[k]
        s1 = across[k + 1] - (across[k + 1] @ a[k + 1]) * a[k + 1]
        if np.linalg.norm(s0) < 1e-6 or np.linalg.norm(s1) < 1e-6:
            continue
        # rotate s0 by the minimal rotation taking a[k] onto a[k+1] (Rodrigues)
        axis = np.cross(a[k], a[k + 1])
        sn = np.linalg.norm(axis)
        if sn > 1e-9:
            axis /= sn
            th = np.arctan2(sn, a[k] @ a[k + 1])
            s0 = s0 * np.cos(th) + np.cross(axis, s0) * np.sin(th) + axis * (axis @ s0) * (1 - np.cos(th))
        s0, s1 = _unit(s0), _unit(s1)
        total += np.degrees(np.arctan2(np.cross(s0, s1) @ a[k + 1], s0 @ s1))
    return float(total)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    out, start = [], None
    for i, m in enumerate(list(mask) + [False]):
        if m and start is None:
            start = i
        elif not m and start is not None:
            out.append((start, i))
            start = None
    return out


def _shape(knee: float, hip: float) -> str:
    if hip < 100 and knee < 110:
        return "tuck"
    if hip < 120:
        return "pike"
    return "straight"


def compute(pose: dict, info: dict) -> dict:
    frames = pose["frames"]
    fps = float(pose["fps"])
    J = {b: _arr(frames, b) for b in frames[0]}
    n = len(frames)
    feet_z = np.nanmin(np.stack([J[b][:, 2] for b in FEET]), axis=0)
    hands_z = np.nanmin(np.stack([J[b][:, 2] for b in HANDS]), axis=0)
    ground = float(np.nanpercentile(feet_z, 3)) - 3.0       # ball/ankle joints sit a few cm above the floor
    air = (feet_z > ground + AIR_CM) & (hands_z > ground + AIR_CM)
    knee = np.stack([_angle(J["thigh_l"], J["calf_l"], J["foot_l"]), _angle(J["thigh_r"], J["calf_r"], J["foot_r"])])
    hip = np.stack([_angle(J["neck_01"], J["thigh_l"], J["calf_l"]), _angle(J["neck_01"], J["thigh_r"], J["calf_r"])])
    elbow = np.stack([_angle(J["upperarm_l"], J["lowerarm_l"], J["hand_l"]), _angle(J["upperarm_r"], J["lowerarm_r"], J["hand_r"])])
    trunk = J["neck_01"] - J["pelvis"]
    across = (J["upperarm_r"] - J["upperarm_l"]) + (J["thigh_r"] - J["thigh_l"])
    pelvis_h = J["pelvis"][:, 2] - ground
    rate = float(info["frame_rate"])
    idx = lambda f: int(np.clip(round(f / rate * fps), 0, n - 1))

    out_els = []
    last_end = 0
    for e in info.get("elements") or []:
        i0, i1 = idx(e["frame0"]), idx(e["frame1"])
        if i1 <= i0:
            i1 = min(n - 1, i0 + 1)
        last_end = max(last_end, i1)
        sl = slice(i0, i1 + 1)
        r = {"name": e.get("name"), "type": e.get("type"), "t0": round(i0 / fps, 3), "t1": round(i1 / fps, 3)}
        if e.get("type") == "hurdle":
            out_els.append(r)
            continue
        # flight: the longest airborne run overlapping the element (allow a little slack either side)
        lo, hi = max(0, i0 - int(0.15 * fps)), min(n, i1 + int(0.15 * fps) + 1)
        runs = [(a + lo, b + lo) for a, b in _runs(air[lo:hi])]
        if runs:
            a, b = max(runs, key=lambda q: q[1] - q[0])
            r["flight_s"] = round((b - a) / fps, 3)
            fl = slice(a, b)
        else:
            r["flight_s"] = 0.0
            fl = sl
        r["knee_min"] = round(float(np.nanmin(knee[:, fl])), 1)
        r["hip_min"] = round(float(np.nanmin(hip[:, fl])), 1)
        r["shape3d"] = _shape(r["knee_min"], r["hip_min"])
        r["rotation_deg"] = round(somersault_deg(trunk[sl]))
        r["twist_deg"] = round(twist_deg(trunk[sl], across[sl]))
        r["hip_peak_cm"] = round(float(np.nanmax(pelvis_h[sl])), 1)
        if runs and e.get("type") in ("salto", "jump"):
            # chest-up check: trunk angle from vertical just after touchdown (under-rotation shows here)
            k = min(n - 1, fl.stop + 1)
            r["land_trunk_deg"] = round(float(np.degrees(np.arccos(np.clip(_unit(trunk[k])[2], -1, 1)))), 1)
        tucked = np.nanmax(knee[:, fl], axis=0) < 110
        if tucked.sum() >= 2:
            gap = np.linalg.norm(J["calf_l"][fl] - J["calf_r"][fl], axis=1)[tucked]
            r["knee_gap_cm"] = round(float(np.nanmedian(gap)), 1)
        # support: arms that are on the floor during the element
        sup = []
        for s, hb in ((0, "hand_l"), (1, "hand_r")):
            on = J[hb][sl, 2] < ground + 18
            if on.any():
                sup.append(elbow[s, sl][on])
        if sup:
            r["elbow_min"] = round(float(np.nanmin(np.concatenate(sup))), 1)
        out_els.append(r)

    land = slice(last_end, min(n, last_end + int(0.6 * fps) + 1))
    res = {
        "source": "unreal",
        "fps": fps,
        "ground_cm": round(ground, 1),
        "hip_peak_cm": round(float(np.nanmax(pelvis_h)), 1),
        "flight_s": round(max([(b - a) / fps for a, b in _runs(air)] or [0.0]), 3),
        "elements": out_els,
    }
    if land.stop > land.start:
        res["landing_knee_min"] = round(float(np.nanmin(knee[:, land])), 1)
    return res


def compute_take(saved_dir: Path, movies_dir: Path, take: str) -> dict:
    pose = json.loads((saved_dir / f"{take}_pose.json").read_text("utf-8"))
    info = json.loads((movies_dir / take / "gymcut_attempt.json").read_text("utf-8"))
    return compute(pose, info)
