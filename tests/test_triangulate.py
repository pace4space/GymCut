"""Two-camera bundle adjustment on synthetic data: a real solved motion filmed by two virtual
phones; start from a wrong (single-camera-like) depth and check the 3D is recovered."""
import json
from pathlib import Path

import numpy as np
import pytest

from gymcut import triangulate as T

POSE = Path(__file__).resolve().parent.parent / "unreal" / "GymCutUE" / "Saved" / "GymCut" / "Take04_RoundoffDoubleBackTuck_pose.json"


def _look(C, target):
    """Rotation (world -> camera, OpenCV axes) of a camera at C looking at target, z up in world."""
    f = target - C
    f /= np.linalg.norm(f)
    r = np.cross(f, [0, 0, 1.0])
    r /= np.linalg.norm(r)
    d = np.cross(f, r)
    return np.stack([r, d, f])


def _scene(moving_b: bool, seed=0):
    rng = np.random.default_rng(seed)
    d = json.loads(POSE.read_text())
    W = np.array([[f[j] for j in T.JOINTS] for f in d["frames"]], float)[:, T.MH]   # (n, 12, 3) cm, z up
    n = len(W)
    mid = W.reshape(-1, 3).mean(0)
    CA = mid + np.array([-150.0, -900.0, 60.0])
    RA = _look(CA, mid)
    size = (720, 1280)
    f = 0.75 * 1280

    def proj(X, R, C):
        P = (X - C) @ R.T
        return np.stack([f * P[..., 0] / P[..., 2] + size[0] / 2, f * P[..., 1] / P[..., 2] + size[1] / 2], -1)

    xa = proj(W, RA, CA) + rng.normal(0, 2, (n, 12, 2))
    if moving_b:
        xb = np.zeros_like(xa)
        for i in range(n):
            pel = W[i].mean(0)
            CB = pel + np.array([-350.0, 250.0, 40.0]) + np.array([20 * np.sin(i / 15), 0, 10 * np.cos(i / 20)])
            xb[i] = proj(W[i][None], _look(CB, pel), CB)[0]
        xb += rng.normal(0, 2, xb.shape)
    else:
        CB = mid + np.array([700.0, 300.0, 40.0])
        xb = proj(W, _look(CB, mid), CB) + rng.normal(0, 2, xa.shape)
    # ground truth in camera-A coordinates
    XA_true = (W - CA) @ RA.T
    # single-camera-like start: depth along A's rays wrong by a smooth +-25 %
    s = 1 + 0.25 * np.sin(np.arange(n) / 9.0)[:, None, None]
    X0 = XA_true * s + rng.normal(0, 3, XA_true.shape)
    L0 = np.array([np.median(np.linalg.norm(W[:, q] - W[:, p], axis=1)) for p, q in T.BI])
    c = np.ones((n, 12))
    return xa, c, xb, c.copy(), X0, L0, size, XA_true


def _err(X, Xt):
    return float(np.mean(np.linalg.norm(X - Xt, axis=-1)))


@pytest.mark.skipif(not POSE.exists(), reason="needs a solved take")
def test_two_tripods_recover_3d():
    xa, ca, xb, cb, X0, L0, size, Xt = _scene(moving_b=False)
    res = T.bundle(xa, ca, xb, cb, X0, L0, size, size, fixed_b=True)
    assert _err(res["X"], Xt) < 0.15 * _err(X0, Xt)     # much closer to the truth than the start
    assert _err(res["X"], Xt) < 18                       # cm (start: ~150 cm off)
    assert np.median(res["reproj_a"]) < 4 and np.median(res["reproj_b"]) < 4


@pytest.mark.skipif(not POSE.exists(), reason="needs a solved take")
def test_handheld_improves_on_single_camera():
    xa, ca, xb, cb, X0, L0, size, Xt = _scene(moving_b=True)
    res = T.bundle(xa, ca, xb, cb, X0, L0, size, size, fixed_b=False)
    assert _err(res["X"], Xt) < 18                       # cm (start: ~150 cm off)
    assert np.median(res["reproj_a"]) < 4 and np.median(res["reproj_b"]) < 4
