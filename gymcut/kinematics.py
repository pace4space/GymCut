"""Keypoint cleaning and per-frame kinematic signals for the athlete.

All spatial signals are computed in camera-compensated "world" coordinates
(the shot's reference frame) and normalised by body length BL - the
shoulder-to-ankle length of the athlete (~0.78 x standing height) - so they are
independent of zoom and distance to the camera.
"""
from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.ndimage import gaussian_filter1d
from scipy.signal import savgol_filter

from .camera_motion import apply_transform

GROUPS = {
    "face": [(1, 2), (3, 4)],
    "arms": [(5, 6), (7, 8), (9, 10)],
    "legs": [(11, 12), (13, 14), (15, 16)],
}
BL_PER_HEIGHT = 0.78  # shoulder->ankle length as a fraction of standing height
G = 9.81


# ----------------------------------------------------------------------------- helpers
def odd(n: int) -> int:
    n = int(max(3, n))
    return n if n % 2 else n + 1


def interp_gaps(x: np.ndarray, max_gap: int) -> tuple[np.ndarray, np.ndarray]:
    """Linearly fill internal NaN runs up to max_gap long. Returns (filled, filled_mask)."""
    x = np.asarray(x, np.float64).copy()
    ok = np.isfinite(x)
    filled = np.zeros(len(x), bool)
    if ok.sum() < 2:
        return x, filled
    idx = np.arange(len(x))
    lin = np.interp(idx, idx[ok], x[ok])
    first, last = idx[ok][0], idx[ok][-1]
    i = first
    while i <= last:
        if not ok[i]:
            j = i
            while j <= last and not ok[j]:
                j += 1
            if j - i <= max_gap:
                x[i:j] = lin[i:j]
                filled[i:j] = True
            i = j
        else:
            i += 1
    return x, filled


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index pairs of True runs."""
    m = np.concatenate([[False], np.asarray(mask, bool), [False]])
    d = np.diff(m.astype(np.int8))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def rolling(x: np.ndarray, half: int, fn) -> np.ndarray:
    pad = np.pad(np.asarray(x, np.float64), half, mode="constant", constant_values=np.nan)
    win = sliding_window_view(pad, 2 * half + 1)
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            return fn(win, axis=1)


def nan_gauss(x: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian smoothing that ignores NaNs (normalised convolution)."""
    x = np.asarray(x, np.float64)
    ok = np.isfinite(x)
    if sigma <= 0 or ok.sum() == 0:
        return x
    num = gaussian_filter1d(np.where(ok, x, 0.0), sigma, mode="nearest")
    den = gaussian_filter1d(ok.astype(np.float64), sigma, mode="nearest")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = num / den
    out[den < 0.2] = np.nan
    return out


def joint_angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Angle ABC in degrees (180 = straight)."""
    v1, v2 = a - b, c - b
    n = np.linalg.norm(v1, axis=-1) * np.linalg.norm(v2, axis=-1)
    with np.errstate(invalid="ignore", divide="ignore"):
        cos = np.sum(v1 * v2, axis=-1) / n
    return np.degrees(np.arccos(np.clip(cos, -1, 1)))


def vec_angle(v1: np.ndarray, v2: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v1, axis=-1) * np.linalg.norm(v2, axis=-1)
    with np.errstate(invalid="ignore", divide="ignore"):
        cos = np.sum(v1 * v2, axis=-1) / n
    return np.degrees(np.arccos(np.clip(cos, -1, 1)))


def wrap180(a):
    return (np.asarray(a) + 180.0) % 360.0 - 180.0


def pose_scale(kps: np.ndarray, conf_thr: float = 0.2) -> np.ndarray:
    """Per-frame size proxy: diagonal of confident keypoints, NaN-filled by nearest."""
    xy = np.where((kps[..., 2:3] >= conf_thr), kps[..., :2], np.nan)
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            ext = np.nanmax(xy, 1) - np.nanmin(xy, 1)
    d = np.hypot(ext[:, 0], ext[:, 1])
    ok = np.isfinite(d) & (d > 1)
    if ok.sum() == 0:
        return np.full(len(kps), 100.0)
    idx = np.arange(len(d))
    return np.interp(idx, idx[ok], d[ok])


# ----------------------------------------------------------------------------- cleaning
def fix_left_right(kps: np.ndarray, conf_thr: float = 0.2, prior: float = 0.06) -> np.ndarray:
    """Resolve per-limb-group left/right label flips with a 2-state Viterbi.

    Pairwise cost = motion of the group between consecutive observations (so a
    sudden label swap is expensive, a real turn is not); unary prior slightly
    favours the model's own labelling, which fixes the global convention.
    """
    T = len(kps)
    out = kps.copy()
    scale = pose_scale(kps)
    for pairs in GROUPS.values():
        a = [p[0] for p in pairs]
        b = [p[1] for p in pairs]
        st = [kps[:, a + b, :], kps[:, b + a, :]]
        cost = None
        last = -1
        link = np.full(T, -1)
        back = np.zeros((T, 2), int)
        obs = []
        for t in range(T):
            X = st[0][t]
            if (np.isfinite(X[:, 0]) & (X[:, 2] >= conf_thr)).sum() < 2:
                continue
            unary = np.array([0.0, prior])
            if cost is None:
                cost = unary
            else:
                pw = np.zeros((2, 2))
                for i in range(2):
                    for j in range(2):
                        P, Q = st[i][last], st[j][t]
                        ok = (P[:, 2] >= conf_thr) & (Q[:, 2] >= conf_thr) & np.isfinite(P[:, 0]) & np.isfinite(Q[:, 0])
                        pw[i, j] = (np.mean(np.linalg.norm(P[ok, :2] - Q[ok, :2], axis=1)) / scale[t]) if ok.any() else 0.0
                tot = cost[:, None] + pw
                back[t] = np.argmin(tot, 0)
                cost = tot.min(0) + unary
                link[t] = last
            last = t
            obs.append(t)
        if cost is None:
            continue
        s = int(np.argmin(cost))
        t = last
        while t >= 0:
            if s == 1:
                out[t, a + b] = kps[t, b + a]
            prev = link[t]
            s = int(back[t][s]) if prev >= 0 else s
            t = prev
    return out


def reject_outliers(kps: np.ndarray, conf_thr: float = 0.2, k: int = 2, thresh: float = 0.12) -> np.ndarray:
    """Hampel-style spike removal: drop points far from their temporal median."""
    out = kps.copy()
    scale = pose_scale(kps)
    for j in range(17):
        for d in range(2):
            x = np.where(kps[:, j, 2] >= conf_thr, kps[:, j, d], np.nan)
            med = rolling(x, k, np.nanmedian)
            bad = np.abs(x - med) > thresh * scale
            out[bad, j, 2] = 0.0
    return out


def clean_keypoints(kps: np.ndarray, fps: float, conf_thr: float = 0.25, max_gap_s: float = 0.3):
    """L/R fix -> spike rejection -> gap fill -> Savitzky-Golay smoothing.

    Returns (smoothed kps [T,17,3], interpolated mask [T,17]).
    """
    T = len(kps)
    k = fix_left_right(kps, conf_thr=conf_thr)
    k = reject_outliers(k, conf_thr=conf_thr)
    out = np.full_like(k, np.nan)
    interp_mask = np.zeros((T, 17), bool)
    win = odd(round(fps * 0.16))
    gap = max(1, int(round(max_gap_s * fps)))
    for j in range(17):
        ok = np.isfinite(k[:, j, 0]) & (k[:, j, 2] >= conf_thr)
        if ok.sum() < 2:
            continue
        cx = np.where(ok, k[:, j, 0], np.nan)
        cy = np.where(ok, k[:, j, 1], np.nan)
        fx, m = interp_gaps(cx, gap)
        fy, _ = interp_gaps(cy, gap)
        for s, e in runs(np.isfinite(fx)):
            if e - s >= win:
                fx[s:e] = savgol_filter(fx[s:e], win, 2, mode="interp")
                fy[s:e] = savgol_filter(fy[s:e], win, 2, mode="interp")
        out[:, j, 0], out[:, j, 1] = fx, fy
        out[:, j, 2] = np.where(ok, k[:, j, 2], np.where(np.isfinite(fx), 0.35, 0.0))
        interp_mask[:, j] = m
    return out, interp_mask


# ----------------------------------------------------------------------------- signals
def unwrap_orientation(phi: np.ndarray, fps: float, shot: np.ndarray) -> np.ndarray:
    """Continuous (unwrapped) body orientation in degrees, robust to flips and gaps.

    1. drop isolated outliers (e.g. one frame where the pose model swapped head and
       feet) by comparing each angle with a rolling circular median;
    2. unwrap all valid samples of a shot together (shortest path across gaps), so
       separate runs can never end up with inconsistent 360-degree offsets;
    3. bridge short gaps linearly in the unwrapped domain.
    """
    T = len(phi)
    phi = np.asarray(phi, np.float64).copy()
    k = max(3, int(round(0.1 * fps)))
    c = np.cos(np.radians(phi))
    s = np.sin(np.radians(phi))
    mc = rolling(c, k, np.nanmedian)
    ms = rolling(s, k, np.nanmedian)
    med = np.degrees(np.arctan2(ms, mc))
    dev = np.abs(wrap180(phi - med))
    phi[np.isfinite(dev) & (dev > 60)] = np.nan
    out = np.full(T, np.nan)
    gap = int(round(0.25 * fps))
    for sid in np.unique(shot):
        idx = np.where(shot == sid)[0]
        seg = phi[idx]
        ok = np.isfinite(seg)
        if ok.sum() < 1:
            continue
        un = np.full(len(seg), np.nan)
        un[ok] = np.degrees(np.unwrap(np.radians(seg[ok])))
        un, _ = interp_gaps(un, gap)
        out[idx] = un
    return out


_COM_SEGMENTS = [  # (proximal, distal or None, mass fraction, com position from proximal)
    ("head", 0.081), ("trunk", 0.497),
    ((5, 7), 0.028, 0.436), ((6, 8), 0.028, 0.436),
    ((7, 9), 0.022, 0.682), ((8, 10), 0.022, 0.682),
    ((11, 13), 0.100, 0.433), ((12, 14), 0.100, 0.433),
    ((13, 15), 0.061, 0.606), ((14, 16), 0.061, 0.606),
]


def center_of_mass(kw: np.ndarray, conf: np.ndarray, thr: float = 0.2) -> np.ndarray:
    """Segmental (Dempster) 2D centre of mass; falls back to hip centre."""
    T = len(kw)
    sh = (kw[:, 5] + kw[:, 6]) / 2
    hp = (kw[:, 11] + kw[:, 12]) / 2
    num = np.zeros((T, 2))
    den = np.zeros(T)
    for seg in _COM_SEGMENTS:
        if seg[0] == "head":
            p = np.where((conf[:, 0] >= thr)[:, None], kw[:, 0], sh + (sh - hp) * 0.25)
            m = seg[1]
        elif seg[0] == "trunk":
            p = sh + (hp - sh) * 0.63
            m = seg[1]
        else:
            (a, b), m, r = seg
            p = kw[:, a] + (kw[:, b] - kw[:, a]) * r
            okab = (conf[:, a] >= thr) & (conf[:, b] >= thr)
            p = np.where(okab[:, None], p, np.nan)
        good = np.isfinite(p).all(1)
        num[good] += m * p[good]
        den[good] += m
    with np.errstate(invalid="ignore", divide="ignore"):
        com = num / den[:, None]
    com = np.where((den > 0.6)[:, None], com, hp)
    return com


def compute_signals(kps: np.ndarray, fps: float, W: np.ndarray, shot: np.ndarray,
                    conf_thr: float = 0.2) -> dict:
    """Per-frame kinematic signals for the (cleaned) athlete keypoints."""
    T = len(kps)
    conf = np.nan_to_num(kps[..., 2], nan=0.0)
    xy = np.where((conf >= conf_thr)[..., None], kps[..., :2], np.nan)
    kw = apply_transform(W, xy)                        # world coords [T,17,2]
    valid = (np.isfinite(kw[:, [5, 6, 11, 12], 0]).all(1))

    sh = (kw[:, 5] + kw[:, 6]) / 2
    hp = (kw[:, 11] + kw[:, 12]) / 2
    an = (kw[:, 15] + kw[:, 16]) / 2
    torso_v = sh - hp
    torso_len = np.linalg.norm(torso_v, axis=1)

    # Body length: robust high percentile of segment lengths (foreshortening only shrinks them)
    thigh = np.nanmean(np.stack([np.linalg.norm(kw[:, 13] - kw[:, 11], axis=1),
                                 np.linalg.norm(kw[:, 14] - kw[:, 12], axis=1)]), 0)
    shank = np.nanmean(np.stack([np.linalg.norm(kw[:, 15] - kw[:, 13], axis=1),
                                 np.linalg.norm(kw[:, 16] - kw[:, 14], axis=1)]), 0)
    half = max(3, int(round(1.5 * fps)))
    p90 = lambda w, axis: np.nanpercentile(w, 90, axis=axis)
    bl_t = rolling(torso_len, half, p90) + rolling(thigh, half, p90) + rolling(shank, half, p90)
    bl_global = float(np.nanmedian(bl_t)) if np.isfinite(bl_t).any() else 100.0
    bl = np.where(np.isfinite(bl_t), bl_t, bl_global)
    bl = np.maximum(bl, 0.25 * bl_global)

    # Orientation (gravity-referenced in the shot's reference frame)
    phi = np.degrees(np.arctan2(torso_v[:, 0], -torso_v[:, 1]))
    phi = np.where(valid & (torso_len > 0.08 * bl), phi, np.nan)
    phi_u = unwrap_orientation(phi, fps, shot)
    inversion = np.abs(wrap180(phi_u))

    com = center_of_mass(kw, np.where(np.isfinite(kw[..., 0]), conf, 0.0))
    com = np.where(valid[:, None], com, np.nan)

    def deriv(x):
        d = np.full_like(x, np.nan)
        d[1:-1] = (x[2:] - x[:-2]) * fps / 2.0
        same = np.zeros(T, bool)
        same[1:-1] = (shot[2:] == shot[:-2])
        d[~same] = np.nan
        return d

    sig_s = 0.05 * fps
    vx = nan_gauss(deriv(com[:, 0]), sig_s) / bl
    vy = -nan_gauss(deriv(com[:, 1]), sig_s) / bl   # up positive
    speed = np.hypot(vx, vy)

    # Limb motion relative to the body (captures on-the-spot skills, swings, leaps)
    rel_pts = kw[:, [7, 8, 9, 10, 13, 14, 15, 16]] - com[:, None, :]
    rv = np.stack([np.hypot(deriv(rel_pts[:, i, 0]), deriv(rel_pts[:, i, 1])) for i in range(8)], 1)
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            rel = nan_gauss(np.nanmean(rv, 1), sig_s) / bl

    # ---- Ground contact --------------------------------------------------------
    # Contact points are low joints that are (a) among the lowest body points,
    # (b) stationary in world coordinates and (c) not at a local *high* point
    # (which rules out the apex of a jump, where feet are also momentarily still).
    # The support level is interpolated between such contact samples, which is
    # robust to perspective (athlete moving toward the camera) and pan/tilt drift.
    cand = np.array([9, 10, 13, 14, 15, 16])
    off = np.array([0.02, 0.02, 0.04, 0.04, 0.05, 0.05])  # joint centre height above floor (BL)
    ys = kw[:, cand, 1] + off[None, :] * bl[:, None]
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            y_low = np.nanmax(ys, 1)
    low_joint = np.full(T, -1)
    okl = np.isfinite(y_low)
    low_joint[okl] = cand[np.nanargmax(np.where(np.isfinite(ys[okl]), ys[okl], -np.inf), 1)]
    jv = np.stack([np.hypot(deriv(kw[:, j, 0]), deriv(kw[:, j, 1])) for j in cand], 1) / bl[:, None]
    jv = np.stack([nan_gauss(jv[:, i], 0.03 * fps) for i in range(len(cand))], 1)
    kk = max(2, int(round(0.15 * fps)))
    ys_prev = np.full_like(ys, np.nan)
    ys_next = np.full_like(ys, np.nan)
    ys_prev[kk:] = ys[:-kk]
    ys_next[:-kk] = ys[kk:]
    tol = 0.035 * bl[:, None]
    with np.errstate(invalid="ignore"):
        not_apex = ~((ys_prev - ys > tol) & (ys_next - ys > tol))   # image y down: apex = smaller y
        lowish = ys >= (y_low[:, None] - 0.10 * bl[:, None])
        still = jv < 0.9
    contact_j = lowish & still & not_apex & np.isfinite(ys)
    # require >= ~0.07 s of stillness
    mlen = max(2, int(round(0.07 * fps)))
    for i in range(len(cand)):
        for s, e in runs(contact_j[:, i]):
            if e - s < mlen:
                contact_j[s:e, i] = False
    contact = contact_j.any(1)
    samp = np.where(contact, np.nanmax(np.where(contact_j, ys, -np.inf), 1), np.nan)
    samp[~np.isfinite(samp)] = np.nan
    ground = np.full(T, np.nan)
    for sid in np.unique(shot):
        m = np.where(shot == sid)[0]
        g = samp[m]
        ok = np.isfinite(g)
        if ok.sum() >= 1:
            g_s = rolling(g, max(1, int(round(0.1 * fps))), np.nanmedian)
            ok2 = np.isfinite(g_s)
            ground[m] = np.interp(np.arange(len(m)), np.where(ok2)[0], g_s[ok2])
        else:
            # no clear contact anywhere in the shot: fall back to a high percentile
            ground[m] = np.nanpercentile(y_low[m], 95) if np.isfinite(y_low[m]).any() else np.nan
    elev = (ground - y_low) / bl
    elev = np.where(contact, np.minimum(elev, 0.02), elev)
    com_h = (ground - com[:, 1]) / bl
    hands_low = np.isin(low_joint, [9, 10])
    # wrists near the support level (for hand-support detection)
    wr_y = np.fmax(kw[:, 9, 1], kw[:, 10, 1]) + 0.02 * bl
    hands_down = ((ground - wr_y) / bl) < 0.09

    # Joint angles (image plane)
    ang = {
        "knee_l": joint_angle(kw[:, 11], kw[:, 13], kw[:, 15]),
        "knee_r": joint_angle(kw[:, 12], kw[:, 14], kw[:, 16]),
        "hip_l": joint_angle(kw[:, 5], kw[:, 11], kw[:, 13]),
        "hip_r": joint_angle(kw[:, 6], kw[:, 12], kw[:, 14]),
        "elbow_l": joint_angle(kw[:, 5], kw[:, 7], kw[:, 9]),
        "elbow_r": joint_angle(kw[:, 6], kw[:, 8], kw[:, 10]),
        "shoulder_l": joint_angle(kw[:, 11], kw[:, 5], kw[:, 7]),
        "shoulder_r": joint_angle(kw[:, 12], kw[:, 6], kw[:, 8]),
        "split": vec_angle(kw[:, 15] - kw[:, 11], kw[:, 16] - kw[:, 12]),
        "body_line": joint_angle(sh, hp, an),
    }
    for k in ang:
        ang[k] = nan_gauss(ang[k], 0.03 * fps)

    # Facing direction in the body frame (+1 = toward body-right) and signed shoulder width
    u = torso_v / np.maximum(torso_len, 1e-6)[:, None]
    rgt = np.stack([-u[:, 1], u[:, 0]], 1)
    ear_c = np.where(np.isfinite(kw[:, 3]).all(1)[:, None] & np.isfinite(kw[:, 4]).all(1)[:, None],
                     (kw[:, 3] + kw[:, 4]) / 2, sh)
    head_dir = np.where(np.isfinite(kw[:, 0]).all(1)[:, None], kw[:, 0] - ear_c, np.nan)
    facing = np.sum(head_dir * rgt, 1) / np.maximum(torso_len, 1e-6)
    sh_w = np.sum((kw[:, 5] - kw[:, 6]) * rgt, 1) / np.maximum(torso_len, 1e-6)
    # Knees only bend one way: a flexed knee protrudes toward the front of the body.
    kf = []
    for hi, ki, ai, ang_key in ((11, 13, 15, "knee_l"), (12, 14, 16, "knee_r")):
        prot = np.sum((kw[:, ki] - (kw[:, hi] + kw[:, ai]) / 2) * rgt, 1) / np.maximum(torso_len, 1e-6)
        w = np.clip((168 - ang[ang_key]) / 35, 0, 1)
        kf.append(np.where(np.isfinite(prot) & np.isfinite(w), prot * w, np.nan))
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            facing_knee = np.nanmean(np.stack(kf), 0)

    return {
        "valid": valid, "kw": kw, "com": com, "bl": bl, "bl_global": bl_global,
        "phi": wrap180(phi_u), "phi_u": phi_u, "inversion": inversion,
        "vx": vx, "vy": vy, "speed": speed, "rel": rel,
        "y_low": y_low, "low_joint": low_joint, "ground": ground, "elev": elev, "com_h": com_h,
        "contact": contact,
        "hands_low": hands_low, "hands_down": hands_down,
        "angles": ang, "facing": nan_gauss(facing, 0.05 * fps), "sh_w": nan_gauss(sh_w, 0.03 * fps),
        "facing_knee": nan_gauss(facing_knee, 0.05 * fps),
        "torso_len": torso_len,
    }
