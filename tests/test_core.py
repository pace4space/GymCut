"""Ground-truth tests of the analysis core on synthetic gymnasts (no GPU / video needed).

Run:  python -m pytest -q tests
"""
from __future__ import annotations

import numpy as np
import pytest

from gymcut.analysis import AIR, analyze_segment, detect_phases, find_elements
from gymcut.camera_motion import apply_transform, chain_transforms
from gymcut.kinematics import clean_keypoints, compute_signals, fix_left_right
from gymcut.live import AutoRecorder, OneEuro
from gymcut.segmentation import activity_score, detect_segments
from tests.synth import back_tuck, idle, straight_jump

FPS = 30.0


def _analyse(kps, fps=FPS, W=None):
    T = len(kps)
    if W is None:
        W = np.repeat(np.eye(3)[None], T, 0)
    shot = np.zeros(T, int)
    k, _ = clean_keypoints(kps, fps)
    sig = compute_signals(k, fps, W, shot)
    ph = detect_phases(sig, fps)
    return sig, ph, shot


def _air_runs(ph):
    from gymcut.kinematics import runs
    return runs(ph == AIR)


@pytest.mark.parametrize("fps", [30.0, 60.0, 120.0])
def test_flight_time_and_height(fps):
    kps, gt = back_tuck(fps=fps, flight=0.6)
    sig, ph, _ = _analyse(kps, fps)
    air = _air_runs(ph)
    assert len(air) == 1, f"expected one flight, got {air}"
    s, e = air[0]
    measured = (e - s) / fps
    # elevation threshold trims the very start/end of the flight; allow ~2 frames + 60 ms
    assert abs(measured - gt["flight"]) < 2 / fps + 0.06, measured
    els = find_elements(sig, ph, fps, 0, len(ph))
    salto = [e for e in els if e["type"] == "salto"]
    assert salto, els
    assert salto[0]["height_est_m"] == pytest.approx(9.81 * measured ** 2 / 8, abs=0.01)


@pytest.mark.parametrize("facing", [1, -1])
def test_back_salto_recognition(facing):
    kps, gt = back_tuck(facing=facing)
    sig, ph, _ = _analyse(kps)
    res = analyze_segment(sig, ph, FPS, 0, len(ph), {"m_per_bl": None})
    salto = [e for e in res["elements"] if e["type"] == "salto"]
    assert len(salto) == 1, res["elements"]
    el = salto[0]
    assert el["rotations"] == 1.0
    assert el["direction"] == "backward", el
    assert el["shape"] == "tuck", el
    assert "back salto" in el["name"].lower()
    assert res["landing"].get("result") in ("stuck", "small adjustment", "1 step")


def test_mirrored_video_gives_same_answer():
    kps, _ = back_tuck(facing=1)
    mirrored = kps.copy()
    mirrored[..., 0] = 1920 - mirrored[..., 0]
    # mirroring an image swaps anatomical left/right labels as models see them
    for a, b in [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10), (11, 12), (13, 14), (15, 16)]:
        mirrored[:, [a, b]] = mirrored[:, [b, a]]
    a1 = analyze_segment(*_analyse(kps)[:2], FPS, 0, len(kps), {"m_per_bl": None})
    a2 = analyze_segment(*_analyse(mirrored)[:2], FPS, 0, len(kps), {"m_per_bl": None})
    assert a1["label"] == a2["label"]
    assert abs(a1["metrics"]["rotation_total_deg"]) == pytest.approx(abs(a2["metrics"]["rotation_total_deg"]), abs=5)


def test_left_right_swaps_are_repaired():
    # split leap: legs are far apart, so a label swap is a large, visible error
    kps, _ = straight_jump(split=True, flight=0.5)
    rng = np.random.default_rng(1)
    noisy = kps.copy()
    swapped = (rng.random(len(kps)) < 0.2)
    for t in np.where(swapped)[0]:
        noisy[t, [11, 12, 13, 14, 15, 16]] = noisy[t, [12, 11, 14, 13, 16, 15]]
    fixed = fix_left_right(noisy)
    legs = slice(11, 17)
    apart = np.linalg.norm(kps[:, 15, :2] - kps[:, 16, :2], axis=1) > 40
    assert apart.sum() >= 8
    err_before = np.mean(np.linalg.norm(noisy[apart, legs, :2] - kps[apart, legs, :2], axis=-1))
    err_after = np.mean(np.linalg.norm(fixed[apart, legs, :2] - kps[apart, legs, :2], axis=-1))
    assert err_before > 10
    assert err_after < 0.1 * err_before, (err_before, err_after)


def test_split_leap_detected():
    kps, _ = straight_jump(split=True, flight=0.5)
    sig, ph, _ = _analyse(kps)
    els = find_elements(sig, ph, FPS, 0, len(ph))
    leaps = [e for e in els if e["type"] == "leap"]
    assert leaps and leaps[0]["split_max"] >= 150, els


def test_segmentation_finds_each_skill_and_trims_idle():
    a, _ = back_tuck()
    b, _ = back_tuck(x0=900)
    kps = np.concatenate([idle(3), a, idle(4), b, idle(3)])
    sig, ph, shot = _analyse(kps)
    act = activity_score(sig, FPS)
    segs = detect_segments(sig, ph, act, FPS, shot, preset="skills")
    assert len(segs) == 2, segs
    for s, e in segs:
        assert (e - s) / FPS < 5.0          # idle time mostly trimmed away
    # merging preset joins them
    routine = detect_segments(sig, ph, act, FPS, shot, preset="routine")
    assert len(routine) == 1


def test_idle_produces_no_segments():
    kps = idle(8)
    sig, ph, shot = _analyse(kps)
    act = activity_score(sig, FPS)
    assert detect_segments(sig, ph, act, FPS, shot, preset="skills") == []
    assert float(np.max(act)) < 0.15


def test_camera_pan_is_compensated():
    kps, _ = back_tuck()
    T = len(kps)
    # camera pans right 6 px/frame: content moves left in the image
    A = np.repeat(np.eye(3)[None], T, 0)
    A[1:, 0, 2] = -6.0
    W, shot = chain_transforms(A, np.zeros(T, bool))
    panned = kps.copy()
    shift = -6.0 * np.arange(T)
    panned[:, :, 0] += shift[:, None]
    back = apply_transform(W, panned[..., :2])
    assert np.allclose(back, kps[..., :2], atol=1e-6)
    sig, ph, _ = _analyse(panned, W=W)
    res = analyze_segment(sig, ph, FPS, 0, T, {"m_per_bl": None})
    assert any(e["type"] == "salto" for e in res["elements"])
    assert res["metrics"]["max_speed_bl"] < 6       # not inflated by the pan


def test_auto_recorder_preroll_and_cut():
    rec = AutoRecorder({"sensitivity": 0.5, "pre_roll": 1.0, "post_roll": 0.5, "min_clip": 0.5, "max_clip": 60})
    clips = []
    t = 0.0
    for i in range(300):                       # 10 s @ 30 fps; active from 3 s to 5 s
        act = 0.9 if 3.0 <= t < 5.0 else 0.0
        c = rec.push(t, b"jpg", act, armed=True)
        if c:
            clips.append(c)
        t += 1 / 30
    assert len(clips) == 1
    c = clips[0]
    assert c[0][0] == pytest.approx(3.0 + 0.12 - 1.0, abs=0.1)  # pre-roll kept
    assert c[-1][0] == pytest.approx(5.0 + 0.5, abs=0.1)        # post-roll then cut


def test_one_euro_smooths_jitter_but_follows_motion():
    f = OneEuro()
    rng = np.random.default_rng(0)
    xs, ys = [], []
    for i in range(120):
        t = i / 30
        true = 100 + (200 if i > 60 else 0)
        xs.append(true)
        ys.append(f(np.array([true + rng.normal(0, 3)]), t)[0])
    ys = np.array(ys)
    assert np.std(ys[20:60] - 100) < 2.0        # jitter reduced
    assert abs(ys[-1] - 300) < 3                 # caught up after the jump


def test_orientation_unwrap_survives_flips_and_gaps():
    from gymcut.kinematics import unwrap_orientation, wrap180
    fps = 60.0
    true = np.linspace(0, 720, 240)                 # two full rotations
    phi = wrap180(true).astype(float)
    phi[50:58] = np.nan                             # pose lost for a few frames
    phi[120] = wrap180(true[120] + 180)             # one head/feet-swapped frame
    phi[121] = np.nan
    phi[180:184] = wrap180(true[180:184] + 175)     # short burst of flips
    out = unwrap_orientation(phi, fps, np.zeros(240, int))
    assert abs(out[-1] - out[0] - 720) < 15
    assert np.nanmax(np.abs(np.diff(out))) < 30     # no 360-degree jumps
