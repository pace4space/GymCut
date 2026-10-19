"""Automatic "nip-n-cut": find the parts of a video where the athlete performs.

activity(t) combines camera-compensated body speed, limb motion relative to the
body, inversion and elevation (all in body-length units, so it does not depend
on zoom). Segments come from hysteresis thresholds, get extended back over the
run-up, padded, merged and filtered by evidence of an actual skill.
"""
from __future__ import annotations

import numpy as np

from .analysis import AIR, HANDS
from .kinematics import nan_gauss, runs

PRESETS = {
    # on/off thresholds, merge gap (s), require skill evidence
    "skills":   {"on": 0.42, "off": 0.20, "merge_gap": 0.45, "evidence": True,  "label": "Each skill / pass"},
    "passes":   {"on": 0.40, "off": 0.18, "merge_gap": 1.6,  "evidence": True,  "label": "Tumbling passes (combos merged)"},
    "routine":  {"on": 0.35, "off": 0.14, "merge_gap": 7.0,  "evidence": True,  "label": "Whole routine"},
    "activity": {"on": 0.30, "off": 0.14, "merge_gap": 1.0,  "evidence": False, "label": "Any movement"},
}


def activity_score(sig: dict, fps: float) -> np.ndarray:
    spd = np.nan_to_num(sig["speed"], nan=0.0)
    rel = np.nan_to_num(sig["rel"], nan=0.0)
    inv = np.nan_to_num(sig["inversion"], nan=0.0)
    elev = np.nan_to_num(sig["elev"], nan=0.0)
    s1 = np.clip((spd - 0.9) / 2.2, 0, 1)    # travelling: walk ~1 BL/s, sprint ~4-5 BL/s
    s2 = np.clip((rel - 1.0) / 3.0, 0, 1)    # limbs moving fast relative to the body
    s3 = np.clip((inv - 50) / 70, 0, 1)      # inverted
    s4 = np.clip((elev - 0.06) / 0.2, 0, 1)  # off the ground
    a = np.clip(0.35 * s1 + 0.35 * s2 + 0.55 * s3 + 0.5 * s4, 0, 1)
    a = np.where(sig["valid"], a, 0.0)
    return np.clip(nan_gauss(a, 0.10 * fps), 0, 1)


def detect_segments(sig: dict, phases: np.ndarray, act: np.ndarray, fps: float, shot: np.ndarray,
                    preset: str = "skills", sensitivity: float = 0.5, pre_pad: float = 0.5,
                    post_pad: float = 0.9, min_duration: float = 0.8) -> list[tuple[int, int]]:
    P = PRESETS.get(preset, PRESETS["skills"])
    T = len(act)
    k = 1.45 - 0.9 * float(np.clip(sensitivity, 0, 1))   # 0.5 -> 1.0
    on, off = P["on"] * k, P["off"] * k

    core = np.zeros(T, bool)
    state = False
    for t in range(T):
        if t > 0 and shot[t] != shot[t - 1]:
            state = False
        if not state and act[t] >= on:
            state = True
        elif state and act[t] < off:
            state = False
        core[t] = state
    segs = []
    spd = np.nan_to_num(sig["speed"], nan=0.0)
    max_runup = int(5.0 * fps)
    for s, e in runs(core):
        # walk back over the run-up / preparation while still moving
        lim = max(0, s - max_runup)
        while s > lim and shot[s - 1] == shot[s] and (act[s - 1] >= off * 0.5 or spd[s - 1] > 1.3):
            s -= 1
        segs.append([s, e])

    # evidence filter
    if P["evidence"]:
        keep = []
        min_air = max(2, int(round(0.1 * fps)))
        for s, e in segs:
            ph = phases[s:e]
            air_runs = [b - a for a, b in runs(ph == AIR)]
            ev = (np.nanmax(np.nan_to_num(sig["inversion"][s:e])) > 100
                  or (air_runs and max(air_runs) >= min_air)
                  or np.any(ph == HANDS)
                  or np.nanmax(np.nan_to_num(sig["rel"][s:e])) > 3.5)
            if ev:
                keep.append([s, e])
        segs = keep

    # pad, clamp to shot, merge
    pre, post = int(round(pre_pad * fps)), int(round(post_pad * fps))
    padded = []
    for s, e in segs:
        sid = shot[s]
        idx = np.where(shot == sid)[0]
        padded.append([max(idx[0], s - pre), min(idx[-1] + 1, e + post)])
    padded.sort()
    merged: list[list[int]] = []
    gap = int(round(P["merge_gap"] * fps))
    for s, e in padded:
        if merged and s - merged[-1][1] <= gap and shot[s] == shot[merged[-1][0]]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    mind = int(round(min_duration * fps))
    return [(int(s), int(e)) for s, e in merged if e - s >= mind]
