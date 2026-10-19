"""Two-camera 3D: fuse two videos of the same moment into one triangulated skeleton.

Neither phone is calibrated, so the geometry is recovered from the athlete herself with a
bundle adjustment in the frame of camera A (the reference):
  unknowns   12 body joints per frame (shoulders, elbows, wrists, hips, knees, ankles),
             camera B's pose - ONE pose if B is on a tripod, one per frame if it moves -
             and both focal lengths (held near a phone's main lens)
  residuals  reprojection of every joint into both views (robust, confidence weighted),
             constant bone lengths, smooth joint motion, smooth camera-B path
It starts from clip A's single-camera Unreal solve placed in camera A by PnP; depth then comes
from the second view.

Honest limits: with two tripods this is well posed (6 camera unknowns for the whole clip). A
hand-held camera adds 6 unknowns per frame that the 12 joints of one athlete barely constrain,
so every result gets a confidence verdict (reprojection error, camera path plausibility,
agreement with the single-camera hip heights) and weak fusions are flagged, not shown as fact.

Output is moved back into the space of clip A's solve (cm, z up, floor ~ 0) for display.
"""
from __future__ import annotations

import json

import cv2
import numpy as np

from . import library as lib
from . import ue3d
from .pose3d import JOINTS, _array, _ground, _load

# COCO-17 keypoint -> MetaHuman joint (both use the person's own left/right)
COCO_TO_MH = {5: "upperarm_l", 6: "upperarm_r", 7: "lowerarm_l", 8: "lowerarm_r", 9: "hand_l", 10: "hand_r",
              11: "thigh_l", 12: "thigh_r", 13: "calf_l", 14: "calf_r", 15: "foot_l", 16: "foot_r"}
KP = list(COCO_TO_MH)
MH = [JOINTS.index(COCO_TO_MH[k]) for k in KP]
BONES = [("upperarm_l", "lowerarm_l"), ("lowerarm_l", "hand_l"), ("upperarm_r", "lowerarm_r"), ("lowerarm_r", "hand_r"),
         ("thigh_l", "calf_l"), ("calf_l", "foot_l"), ("thigh_r", "calf_r"), ("calf_r", "foot_r"),
         ("thigh_l", "thigh_r")]
# only truly rigid segments: shoulder width (shrugging) and shoulder-hip (spine bending) change
# by 9-18 cm in a tuck - holding those constant bends the reconstruction
CONF = 0.35
_KI = {v: KP.index(k) for k, v in COCO_TO_MH.items()}
BI = [(_KI[a], _KI[b]) for a, b in BONES]
PHONE_FOCAL = 0.75              # focal / longer image side for a ~26 mm-equivalent main camera


# ---------------------------------------------------------------- geometry helpers
def _K(f: float, w: int, h: int) -> np.ndarray:
    return np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1]], float)


def _pnp(P3: np.ndarray, p2: np.ndarray, K: np.ndarray, guess=None):
    """Camera pose from >= 6 points. Returns (rvec, tvec, rms px) or None."""
    ok = np.isfinite(P3).all(1) & np.isfinite(p2).all(1)
    if ok.sum() < 6:
        return None
    P3, p2 = P3[ok].astype(np.float64), p2[ok].astype(np.float64)
    try:
        if guess is not None:
            r, rv, tv = cv2.solvePnP(P3, p2, K, None, guess[0].copy(), guess[1].copy(), True, cv2.SOLVEPNP_ITERATIVE)
        else:
            r, rv, tv = cv2.solvePnP(P3, p2, K, None, flags=cv2.SOLVEPNP_SQPNP)
            if r:
                r, rv, tv = cv2.solvePnP(P3, p2, K, None, rv, tv, True, cv2.SOLVEPNP_ITERATIVE)
    except cv2.error:
        return None
    if not r:
        return None
    proj, _ = cv2.projectPoints(P3, rv, tv, K, None)
    rms = float(np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - p2) ** 2, 1))))
    R, _ = cv2.Rodrigues(rv)
    if ((P3 @ R.T + tv.reshape(1, 3))[:, 2] <= 0).mean() > 0.5:   # points behind the camera
        return None
    return rv, tv, rms


def _kabsch(src: np.ndarray, dst: np.ndarray):
    """Rotation + translation taking src points onto dst (least squares)."""
    ok = np.isfinite(src).all(1) & np.isfinite(dst).all(1)
    src, dst = src[ok], dst[ok]
    cs, cd = src.mean(0), dst.mean(0)
    U, _, Vt = np.linalg.svd((src - cs).T @ (dst - cd))
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1, 1, d]) @ U.T
    return R, cd - R @ cs


def _smooth(Y: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian smoothing over time that ignores missing samples."""
    n, r = len(Y), int(3 * sigma) + 1
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    out = np.full_like(Y, np.nan)
    for i in range(n):
        lo, hi = max(0, i - r), min(n, i + r + 1)
        w = k[lo - i + r:hi - i + r].reshape(-1, *([1] * (Y.ndim - 1))) * np.isfinite(Y[lo:hi])
        s = np.nansum(np.nan_to_num(Y[lo:hi]) * w, 0)
        ws = w.sum(0)
        out[i] = np.where(ws > 0.2, s / np.maximum(ws, 1e-9), np.nan)
    return out


def _rot(rv: np.ndarray) -> np.ndarray:
    """Rodrigues for many vectors at once: (n, 3) -> (n, 3, 3)."""
    th = np.linalg.norm(rv, axis=1, keepdims=True)
    k = rv / np.maximum(th, 1e-12)
    K = np.zeros((len(rv), 3, 3))
    K[:, 0, 1], K[:, 0, 2], K[:, 1, 0] = -k[:, 2], k[:, 1], k[:, 2]
    K[:, 1, 2], K[:, 2, 0], K[:, 2, 1] = -k[:, 0], -k[:, 1], k[:, 0]
    th = th[:, :, None]
    return np.eye(3)[None] + np.sin(th) * K + (1 - np.cos(th)) * (K @ K)


def _project(X, R, t, f, w, h):
    P = X if R is None else X @ np.swapaxes(R, 1, 2) + t[:, None, :]
    z = np.maximum(P[..., 2], 1.0)
    return np.stack([f * P[..., 0] / z + w / 2, f * P[..., 1] / z + h / 2], -1)


def _two_view_init(xa, ca, xb, cb, L0, size_a, size_b, fa, fb):
    """Relative pose of two fixed cameras from all joint correspondences over time."""
    (wa, ha), (wb, hb) = size_a, size_b
    seen = (ca > 0) & (cb > 0)
    if seen.sum() < 30:
        return None
    na = (xa[seen] - [wa / 2, ha / 2]) / fa                  # normalised image coordinates
    nb = (xb[seen] - [wb / 2, hb / 2]) / fb
    E, mask = cv2.findEssentialMat(na, nb, np.eye(3), method=cv2.RANSAC, prob=0.999, threshold=3.0 / fa)
    if E is None or E.shape != (3, 3):
        return None
    _, R, t, _ = cv2.recoverPose(E, na, nb, np.eye(3), mask=mask)
    Pa = np.hstack([np.eye(3), np.zeros((3, 1))])
    Pb = np.hstack([R, t])
    n, J = xa.shape[:2]
    X = np.full((n, J, 3), np.nan)
    ia, ja = np.where(seen)
    Xh = cv2.triangulatePoints(Pa, Pb, na.T, nb.T)
    X[ia, ja] = (Xh[:3] / Xh[3]).T
    # scale: unit baseline -> cm via the rigid bones
    bl = np.stack([np.linalg.norm(X[:, q] - X[:, p], axis=1) for p, q in BI], 1)
    ratio = L0[None] / bl
    s = float(np.nanmedian(ratio[np.isfinite(ratio)])) if np.isfinite(ratio).any() else None
    if not s or s <= 0:
        return None
    X *= s
    X = _smooth(X, 1.0)
    have = np.where(np.isfinite(X).all((1, 2)))[0]
    if not len(have):
        return None
    for i in range(n):                                       # fill joints/frames one camera missed
        miss = ~np.isfinite(X[i]).all(1)
        if miss.any():
            X[i, miss] = X[have[np.argmin(np.abs(have - i))], miss]
    rv, _ = cv2.Rodrigues(R)
    return X, rv.reshape(1, 3), (t.ravel() * s).reshape(1, 3)


# ---------------------------------------------------------------- core bundle adjustment
def bundle(xa, ca, xb, cb, X0, L0, size_a, size_b, fixed_b: bool, focal_prior: float = 3000.0, max_nfev: int = 400) -> dict:
    """xa/xb (n, J, 2) pixels, ca/cb (n, J) weights (0 = unseen), X0 (n, J, 3) initial joints in
    camera-A coordinates (cm), L0 bone lengths. Returns joints, camera-B path, focals, errors."""
    from scipy.optimize import least_squares
    from scipy.sparse import lil_matrix

    (wa, ha), (wb, hb) = size_a, size_b
    n, J = xa.shape[:2]
    f0a, f0b = PHONE_FOCAL * max(wa, ha), PHONE_FOCAL * max(wb, hb)
    Kb = _K(f0b, wb, hb)
    # camera B start: PnP of the initial skeleton (median pose if B is on a tripod)
    rB, tB, gb = np.zeros((n, 3)), np.zeros((n, 3)), None
    for i in range(n):
        r = _pnp(X0[i], np.where(cb[i, :, None] > 0, xb[i], np.nan), Kb, gb)
        if r:
            gb = (r[0], r[1])
        if gb:
            rB[i], tB[i] = gb[0].ravel(), gb[1].ravel()
    if fixed_b:
        # two tripods: every joint at every moment is a point both cameras see -> classic two-view
        # geometry (essential matrix) for the relative pose, triangulate, scale by bone lengths
        init = _two_view_init(xa, ca, xb, cb, L0, (wa, ha), (wb, hb), f0a, f0b)
        if init is not None:
            X0, rB, tB = init
        else:
            seen = cb > 0
            r = _pnp(X0[seen], xb[seen], Kb)
            rB, tB = (r[0].reshape(1, 3), r[1].reshape(1, 3)) if r else (np.median(rB, 0, keepdims=True), np.median(tB, 0, keepdims=True))
    nc = len(rB)                                     # 1 (tripod) or n (moving)
    nX = n * J * 3
    WS, WB, WR, WC = 0.6, 3.0, 25.0, 2.0             # joint smoothness, bones, camera rotation / centre smoothness

    def unpack(z):
        return (z[:nX].reshape(n, J, 3), z[nX:nX + nc * 3].reshape(nc, 3), z[nX + nc * 3:nX + nc * 6].reshape(nc, 3),
                z[-2], z[-1])

    def cams(rb, tb):
        R = _rot(rb)
        return (np.repeat(R, n, 0), np.repeat(tb, n, 0)) if nc == 1 else (R, tb)

    def resid(z):
        X, rb, tb, fa, fb = unpack(z)
        R, t = cams(rb, tb)
        pa = _project(X, None, None, fa, wa, ha)
        pb = _project(X, R, t, fb, wb, hb)
        bl = np.stack([np.linalg.norm(X[:, q] - X[:, p], axis=1) for p, q in BI], 1)
        out = [((pa - xa) * ca[..., None]).ravel(), ((pb - xb) * cb[..., None]).ravel(),
               (WB * (bl - L0[None])).ravel(), (WS * (X[:-2] - 2 * X[1:-1] + X[2:])).ravel()]
        if nc > 1:                                   # a hand-held camera moves smoothly
            C = -np.einsum("nji,nj->ni", _rot(rb), tb)
            out += [(WR * (rb[:-2] - 2 * rb[1:-1] + rb[2:])).ravel(), (WC * (C[:-2] - 2 * C[1:-1] + C[2:])).ravel()]
        out.append(np.array([(fa / f0a - 1) * focal_prior, (fb / f0b - 1) * focal_prior]))
        return np.concatenate(out)

    z0 = np.concatenate([X0.ravel(), rB.ravel(), tB.ravel(), [f0a, f0b]])
    m = len(resid(z0))
    S = lil_matrix((m, len(z0)), dtype=np.int8)
    row = 0
    xi = lambda i, j: (i * J + j) * 3
    ci = (lambda i: 0) if nc == 1 else (lambda i: i)
    for i in range(n):
        for j in range(J):
            for _ in range(2):
                S[row, xi(i, j):xi(i, j) + 3] = 1
                S[row, len(z0) - 2] = 1
                row += 1
    for i in range(n):
        for j in range(J):
            for _ in range(2):
                S[row, xi(i, j):xi(i, j) + 3] = 1
                S[row, nX + ci(i) * 3:nX + ci(i) * 3 + 3] = 1
                S[row, nX + nc * 3 + ci(i) * 3:nX + nc * 3 + ci(i) * 3 + 3] = 1
                S[row, len(z0) - 1] = 1
                row += 1
    for i in range(n):
        for p, q in BI:
            S[row, xi(i, p):xi(i, p) + 3] = 1
            S[row, xi(i, q):xi(i, q) + 3] = 1
            row += 1
    for i in range(n - 2):
        for j in range(J):
            for c in range(3):
                for d in range(3):
                    S[row, xi(i + d, j) + c] = 1
                row += 1
    if nc > 1:
        for i in range(n - 2):                       # rotation smoothness
            for c in range(3):
                for d in range(3):
                    S[row, nX + (i + d) * 3 + c] = 1
                row += 1
        for i in range(n - 2):                       # centre smoothness (depends on r and t)
            for c in range(3):
                for d in range(3):
                    S[row, nX + (i + d) * 3:nX + (i + d) * 3 + 3] = 1
                    S[row, nX + nc * 3 + (i + d) * 3:nX + nc * 3 + (i + d) * 3 + 3] = 1
                row += 1
    S[row, len(z0) - 2] = 1
    S[row + 1, len(z0) - 1] = 1
    lb = np.full(len(z0), -np.inf)
    ub = np.full(len(z0), np.inf)
    lb[-2:], ub[-2:] = [0.35 * max(wa, ha), 0.35 * max(wb, hb)], [3.0 * max(wa, ha), 3.0 * max(wb, hb)]
    sol = least_squares(resid, z0, jac_sparsity=S.tocsr(), method="trf", loss="soft_l1", f_scale=6.0,
                        x_scale="jac", max_nfev=max_nfev, bounds=(lb, ub))
    X, rb, tb, fa, fb = unpack(sol.x)
    R, t = cams(rb, tb)
    ea = np.linalg.norm(_project(X, None, None, fa, wa, ha) - xa, axis=-1)[ca > 0]
    eb = np.linalg.norm(_project(X, R, t, fb, wb, hb) - xb, axis=-1)[cb > 0]
    C = -np.einsum("nji,nj->ni", R, t)
    return {"X": X, "cam_b_centre": C, "fa": float(fa), "fb": float(fb), "reproj_a": ea, "reproj_b": eb,
            "converged": bool(sol.success), "nfev": int(sol.nfev)}


# ---------------------------------------------------------------- videos -> fused skeleton
def _solve(vid: str, seg_id: int):
    got = _load(ue3d.take_name(vid, seg_id))
    if not got:
        raise FileNotFoundError(f"no 3D solve for {vid} #{seg_id}")
    pose, info = got
    return _array(pose), float(pose["fps"]), info


def _moving(an: dict) -> bool:
    return bool(an.get("cam") or an.get("com_w"))


def fuse(vid_a: str, vid_b: str, offset: float, seg_a: int = 1, seg_b: int = 1) -> dict:
    """offset: B video time = A video time + offset (from the Compare angles sync)."""
    Aan, Ban = lib.read_json(vid_a, "analysis.json"), lib.read_json(vid_b, "analysis.json")
    # reference = a camera on a tripod if there is one
    if _moving(Aan) and not _moving(Ban):
        out = fuse(vid_b, vid_a, -offset, seg_b, seg_a)
        out["swapped"] = True
        return out
    XA, fpsA, infoA = _solve(vid_a, seg_a)
    t0 = infoA["first_source_frame"] / infoA["source_fps"]
    vt = t0 + np.arange(len(XA)) / fpsA * infoA["frame_rate"] / infoA["source_fps"]

    def kp(an, f):
        if f < 0 or f >= len(an["kps"]) or not an["kps"][f]:
            return np.full((len(KP), 3), np.nan)
        return np.array(an["kps"][f], float).reshape(-1, 3)[KP]

    kA = np.stack([kp(Aan, f) for f in np.round(vt * Aan["fps"]).astype(int)])
    kB = np.stack([kp(Ban, f) for f in np.round((vt + offset) * Ban["fps"]).astype(int)])
    wa, ha, wb, hb = Aan["width"], Aan["height"], Ban["width"], Ban["height"]
    ca, cb = np.nan_to_num(kA[..., 2]), np.nan_to_num(kB[..., 2])
    ca[ca < CONF] = 0
    cb[cb < CONF] = 0
    xa, xb = np.nan_to_num(kA[..., :2]), np.nan_to_num(kB[..., :2])

    # initial joints: clip A's solve placed into camera A by PnP, smoothed, gaps filled
    Ka = _K(PHONE_FOCAL * max(wa, ha), wa, ha)
    Y0 = XA[:, MH]
    X0 = np.full(Y0.shape, np.nan)
    ga = None
    for i in range(len(XA)):
        r = _pnp(Y0[i], np.where(ca[i, :, None] > 0, xa[i], np.nan), Ka, ga)
        if r:
            ga = (r[0], r[1])
            R, _ = cv2.Rodrigues(r[0])
            X0[i] = Y0[i] @ R.T + r[1].ravel()
    X0 = _smooth(X0, 1.0)
    have = np.where(np.isfinite(X0).all((1, 2)))[0]
    if not len(have):
        raise RuntimeError("camera A did not see enough of the athlete")
    for i in range(len(X0)):
        if not np.isfinite(X0[i]).all():
            X0[i] = X0[have[np.argmin(np.abs(have - i))]]
    L0 = np.array([np.nanmedian(np.linalg.norm(Y0[:, q] - Y0[:, p], axis=1)) for p, q in BI])

    fixed_b = not _moving(Ban)
    res = bundle(xa, ca, xb, cb, X0, L0, (wa, ha), (wb, hb), fixed_b)
    X = res["X"]

    # back into the solve's world (z up, floor ~ 0)
    R, t = _kabsch(X.reshape(-1, 3), Y0.reshape(-1, 3))
    Xw = X @ R.T + t
    full = XA.copy()
    tor_mh = [JOINTS.index(j) for j in ("upperarm_l", "upperarm_r", "thigh_l", "thigh_r")]
    tor = [_KI[j] for j in ("upperarm_l", "upperarm_r", "thigh_l", "thigh_r")]
    for i in range(len(full)):
        Ri, ti = _kabsch(XA[i, tor_mh], Xw[i, tor])
        full[i] = XA[i] @ Ri.T + ti
        full[i, MH] = Xw[i]
        for foot, ball in (("foot_l", "ball_l"), ("foot_r", "ball_r")):
            fi_, bi_ = JOINTS.index(foot), JOINTS.index(ball)
            full[i, bi_] = full[i, fi_] + Ri @ (XA[i, bi_] - XA[i, fi_])
    full[:, :, 2] -= _ground(full)

    # ---- confidence verdict
    height = lambda x, c: float(np.nanmedian(np.ptp(np.where(c[..., None] > 0, x, np.nan)[..., 1], axis=1)))
    hpx_a, hpx_b = height(xa, ca), height(xb, cb)
    ra, rb_ = float(np.median(res["reproj_a"])), float(np.median(res["reproj_b"]))
    C = res["cam_b_centre"]
    jump = float(np.max(np.linalg.norm(np.diff(C, axis=0), axis=1))) if len(C) > 1 else 0.0   # cm per sample
    fps_s = fpsA
    hip_fused = float(np.nanmax(full[:, JOINTS.index("pelvis"), 2]))
    hips_single = [m.get("hip_peak_cm") for m in ((lib.get(v).get("metrics3d") or {}).get(str(s)) or {} for v, s in ((vid_a, seg_a), (vid_b, seg_b)))]
    hips_single = [h for h in hips_single if h]
    reasons = []
    if ra > 0.06 * hpx_a or rb_ > 0.06 * hpx_b:
        reasons.append("joints don't line up well with what the cameras saw")
    if not fixed_b and jump > 120 / fps_s * 1.0:          # > ~1.2 m/s of sudden camera movement
        reasons.append(f"the hand-held camera's path jumps (up to {jump:.0f} cm between samples) – its position can't be pinned down")
    if hips_single and abs(hip_fused - np.mean(hips_single)) > 35:
        reasons.append(f"hip height {hip_fused:.0f} cm disagrees with the single-camera solves ({', '.join(f'{h:.0f}' for h in hips_single)} cm)")
    if min(hpx_a, hpx_b) < 120:
        reasons.append(f"the athlete is small in one view ({min(hpx_a, hpx_b):.0f} px tall)")
    level = "high" if not reasons else "low" if len(reasons) >= 2 or any("jumps" in r or "disagrees" in r for r in reasons) else "medium"
    med = lambda e: round(float(np.median(e)), 1) if len(e) else None
    return {
        "joints": JOINTS, "fps": fpsA,
        "frames": [[None if not np.isfinite(v) else round(float(v), 1) for v in f.reshape(-1)] for f in full],
        "video_t0": t0, "rate": infoA["frame_rate"] / infoA["source_fps"], "reference": vid_a,
        "quality": {
            "confidence": level, "reasons": reasons,
            "camera_b": "tripod" if fixed_b else "hand-held",
            "focal_a": round(res["fa"] / max(wa, ha), 2), "focal_b": round(res["fb"] / max(wb, hb), 2),
            "reproj_a_px": med(res["reproj_a"]), "reproj_b_px": med(res["reproj_b"]),
            "athlete_px_a": round(hpx_a), "athlete_px_b": round(hpx_b),
            "hip_peak_cm": round(hip_fused), "hip_peak_single_cm": [round(h) for h in hips_single],
            "seen_by_both": round(float(((ca > 0) & (cb > 0)).mean()), 2), "converged": res["converged"],
        },
    }


def fuse_cached(vid_a: str, vid_b: str, offset: float) -> dict:
    p = lib.vdir(vid_a) / f"fused3d_{vid_b}.json"
    key = f"{offset:.3f}|2"
    if p.exists():
        d = json.loads(p.read_text())
        if d.get("offset") == key:
            return d
    d = fuse(vid_a, vid_b, offset)
    d["offset"] = key
    p.write_text(json.dumps(d))
    return d
