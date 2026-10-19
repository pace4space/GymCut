"""Rotation-aware, crop-based re-estimation of the athlete's pose.

Off-the-shelf pose models are trained on mostly upright people. Gymnasts spend
much of a skill inverted or sideways, where predictions degrade (crossed limbs,
head/feet confusion). For every athlete frame we run the model on a tight,
upscaled crop rotated by several angles (0/90/180/270 + the estimated body
orientation), map keypoints back, and pick the best candidate sequence with a
Viterbi pass that trades off keypoint confidence against temporal continuity.
It also recovers frames where the full-frame detector lost the athlete.
"""
from __future__ import annotations

from typing import Callable

import cv2
import numpy as np

from .pose import LR_PAIRS, PoseEngine

BODY = np.arange(5, 17)
_SWAP = np.arange(17)
for _a, _b in LR_PAIRS:
    _SWAP[_a], _SWAP[_b] = _b, _a


def torso_angle(kps: np.ndarray, conf_thr: float = 0.3) -> np.ndarray:
    """Body orientation in degrees: 0 = upright, +90 = head to the right, 180 = inverted."""
    sh = (kps[..., 5, :2] + kps[..., 6, :2]) / 2
    hp = (kps[..., 11, :2] + kps[..., 12, :2]) / 2
    v = sh - hp
    ang = np.degrees(np.arctan2(v[..., 0], -v[..., 1]))
    c = np.minimum(np.minimum(kps[..., 5, 2], kps[..., 6, 2]), np.minimum(kps[..., 11, 2], kps[..., 12, 2]))
    ang = np.where(c >= conf_thr, ang, np.nan)
    return ang


def _interp_nan(x: np.ndarray, max_gap: int | None = None) -> np.ndarray:
    x = x.astype(np.float64).copy()
    ok = np.isfinite(x)
    if ok.sum() == 0:
        return x
    idx = np.arange(len(x))
    filled = np.interp(idx, idx[ok], x[ok])
    if max_gap is not None:
        # only fill internal gaps up to max_gap
        bad = ~ok
        run_start = None
        for i in range(len(x) + 1):
            if i < len(x) and bad[i]:
                if run_start is None:
                    run_start = i
            elif run_start is not None:
                L = i - run_start
                internal = run_start > 0 and i < len(x)
                if internal and L <= max_gap:
                    x[run_start:i] = filled[run_start:i]
                run_start = None
        return x
    return filled


def _interp_angles(a: np.ndarray, max_gap: int) -> np.ndarray:
    ok = np.isfinite(a)
    if ok.sum() == 0:
        return a
    un = a.copy()
    un[ok] = np.degrees(np.unwrap(np.radians(a[ok])))
    return _interp_nan(un, max_gap)


def _interp_conf(x: np.ndarray, max_gap: int):
    out = _interp_nan(x, max_gap)
    return out, np.isfinite(out) & ~np.isfinite(x)


def prepare_boxes(box: np.ndarray, fps: float, max_gap_s: float = 0.7) -> np.ndarray:
    """Interpolate athlete boxes over short gaps and stabilise centre/size."""
    T = len(box)
    gap = int(round(max_gap_s * fps))
    c = np.stack([(box[:, 0] + box[:, 2]) / 2, (box[:, 1] + box[:, 3]) / 2], 1)
    wh = np.stack([box[:, 2] - box[:, 0], box[:, 3] - box[:, 1]], 1)
    out_c = np.stack([_interp_nan(c[:, i], gap) for i in range(2)], 1)
    out_wh = np.stack([_interp_nan(wh[:, i], gap) for i in range(2)], 1)
    # crop size: rolling max of the box diagonal over +-0.2 s keeps the crop from pumping
    diag = np.hypot(out_wh[:, 0], out_wh[:, 1])
    k = max(1, int(round(0.2 * fps)))
    sm = np.full(T, np.nan)
    for t in range(T):
        seg = diag[max(0, t - k): t + k + 1]
        seg = seg[np.isfinite(seg)]
        if len(seg) and np.isfinite(diag[t]):
            sm[t] = seg.max()
    was_missing = ~np.isfinite(box[:, 0]) & np.isfinite(out_c[:, 0])
    sm = np.where(was_missing, sm * 1.25, sm)
    return np.concatenate([out_c, sm[:, None]], 1)  # [T, 3] cx, cy, side


def _crop_affine(cx, cy, side, angle, out):
    M = cv2.getRotationMatrix2D((float(cx), float(cy)), float(angle), out / float(side))
    M[0, 2] += out / 2.0 - cx
    M[1, 2] += out / 2.0 - cy
    return M


def _pick_detection(dets, out):
    if len(dets) == 0:
        return None
    b = dets.boxes
    c = np.stack([(b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2], 1)
    d = np.linalg.norm(c - out / 2.0, axis=1) / out
    area = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1]) / float(out * out)
    s = dets.scores * np.exp(-(d ** 2) / (2 * 0.18 ** 2)) * np.clip(area / 0.08, 0.2, 1.0)
    j = int(np.argmax(s))
    return j if s[j] > 0.02 else None


def _pose_dist(a: np.ndarray, b: np.ndarray, scale: float) -> float:
    """Median joint distance (L/R-swap invariant) normalised by body scale."""
    def one(p, q):
        ok = (p[BODY, 2] > 0.2) & (q[BODY, 2] > 0.2)
        if ok.sum() < 4:
            return np.nan
        return float(np.median(np.linalg.norm(p[BODY][ok, :2] - q[BODY][ok, :2], axis=1)))
    d1, d2 = one(a, b), one(a, b[_SWAP])
    d = np.nanmin([d1, d2]) if np.isfinite([d1, d2]).any() else np.nan
    return d / max(scale, 1.0)


def _upright_prior(phi_crop: float) -> float:
    """Pose models are most reliable on people that look upright in their input.

    A candidate where the model predicted an inverted body *inside its own crop*
    is less trustworthy (that is where head/foot swaps happen)."""
    if not np.isfinite(phi_crop):
        return 0.05
    a = abs(((phi_crop + 180) % 360) - 180)
    return 0.15 * float(np.clip((a - 35) / 110, 0, 1))


def _cand_score(k: np.ndarray) -> float:
    c = k[BODY, 2]
    return float(0.8 * c.mean() + 0.2 * np.sort(c)[: len(c) // 3].mean())


def refine_athlete(video_path: str, n_frames: int, box: np.ndarray, kps1: np.ndarray, fps: float,
                   engine: PoseEngine, crop_size: int = 384, batch_frames: int = 8,
                   progress: Callable[[float], None] | None = None):
    """Returns (kps_refined [T,17,3], chosen angle [T] (nan = full-frame pass), crop boxes)."""
    T = n_frames
    geo = prepare_boxes(box, fps)
    phi = _interp_angles(torso_angle(kps1), int(round(0.5 * fps)))
    has = np.isfinite(geo[:, 0]) & np.isfinite(geo[:, 2])
    # confidence of the full-frame pass, carried over frames the tracker skipped (high-fps stride)
    conf1 = np.array([_cand_score(kps1[t]) if np.isfinite(kps1[t, 0, 0]) else np.nan for t in range(T)])
    conf1, _ = _interp_conf(conf1, max(1, int(round(0.15 * fps))))

    # candidates[t] = list of (kps[17,3], score, angle)
    cands: list[list] = [[] for _ in range(T)]
    for t in range(T):
        if np.isfinite(kps1[t, 0, 0]):
            ph = float(torso_angle(kps1[t][None], 0.2)[0])
            cands[t].append((kps1[t].copy(), _cand_score(kps1[t]) - 0.03 - _upright_prior(ph), np.nan, ph))

    cap = cv2.VideoCapture(str(video_path))
    pending: list[tuple[int, np.ndarray, float]] = []  # (t, M, angle)
    crops: list[np.ndarray] = []

    def flush():
        if not crops:
            return
        results = engine.predict(crops, imgsz=crop_size, conf=0.1)
        for (t, M, ang), dets in zip(pending, results):
            j = _pick_detection(dets, crop_size)
            if j is None:
                continue
            k = dets.kps[j].astype(np.float64).copy()
            phi_crop = float(torso_angle(k[None], 0.2)[0])   # how the model saw the body in its crop
            Minv = cv2.invertAffineTransform(M)
            xy = np.concatenate([k[:, :2], np.ones((17, 1))], 1) @ Minv.T
            k[:, :2] = xy
            phi_img = float(torso_angle(k[None], 0.2)[0])
            cands[t].append((k, _cand_score(k) - _upright_prior(phi_crop), ang, phi_img))
        pending.clear()
        crops.clear()

    t = 0
    frames_in_batch = 0
    while t < T:
        ok, frame = cap.read()
        if not ok:
            break
        if has[t]:
            cx, cy, side = geo[t]
            side = max(side * 1.08, 48.0)
            base_conf = conf1[t] if np.isfinite(conf1[t]) else 0.0
            upright = np.isfinite(phi[t]) and abs(((phi[t] + 180) % 360) - 180) < 30
            if upright and base_conf > 0.75:
                angles = [0.0]
            else:
                angles = [0.0, 90.0, 180.0, 270.0]
                if np.isfinite(phi[t]):
                    a = float(np.round(phi[t] / 15.0) * 15.0) % 360
                    if min(abs((a - b + 180) % 360 - 180) for b in angles) >= 20:
                        angles.append(a)
            for ang in angles:
                M = _crop_affine(cx, cy, side, ang, crop_size)
                crops.append(cv2.warpAffine(frame, M, (crop_size, crop_size), flags=cv2.INTER_LINEAR,
                                            borderMode=cv2.BORDER_CONSTANT, borderValue=(114, 114, 114)))
                pending.append((t, M, ang))
            frames_in_batch += 1
            if frames_in_batch >= batch_frames:
                flush()
                frames_in_batch = 0
        t += 1
        if progress and t % 10 == 0:
            progress(t / T)
    flush()
    cap.release()

    # --- Viterbi over candidates --------------------------------------------------
    scale = np.where(has, geo[:, 2] / 1.4, np.nan)  # approx body height from crop diagonal
    out = np.full((T, 17, 3), np.nan)
    chosen_angle = np.full(T, np.nan)
    lam = 0.9
    lam_phi = 1.0
    max_step = max(20.0, 1400.0 / max(fps, 1.0))   # fastest real rotations ~1400 deg/s
    t = 0
    while t < T:
        if not cands[t]:
            t += 1
            continue
        # contiguous run of frames with candidates
        s = t
        while t < T and cands[t]:
            t += 1
        e = t
        cost = [np.array([-c[1] for c in cands[s]])]
        back = [None]
        for u in range(s + 1, e):
            sc = scale[u] if np.isfinite(scale[u]) else 200.0
            prev, cur = cands[u - 1], cands[u]
            D = np.zeros((len(prev), len(cur)))
            for i, p in enumerate(prev):
                for j, q in enumerate(cur):
                    d = _pose_dist(p[0], q[0], sc)
                    D[i, j] = lam * (min(d, 1.2) if np.isfinite(d) else 0.6)
                    if np.isfinite(p[3]) and np.isfinite(q[3]):
                        dphi = abs(((q[3] - p[3] + 180) % 360) - 180)
                        D[i, j] += lam_phi * float(np.clip((dphi - max_step) / 90.0, 0, 1.5))
            tot = cost[-1][:, None] + D
            back.append(np.argmin(tot, 0))
            cost.append(tot.min(0) - np.array([c[1] for c in cur]))
        j = int(np.argmin(cost[-1]))
        for u in range(e - 1, s - 1, -1):
            k, _, ang, _ = cands[u][j]
            out[u] = k
            chosen_angle[u] = ang
            if back[u - s] is not None:
                j = int(back[u - s][j])
    return out, chosen_angle, geo
