"""OpenCV drawing primitives shared by exports, live previews and debugging."""
from __future__ import annotations

import cv2
import numpy as np

# BGR. Left side cool, right side warm - matches the web UI.
LEFT = (235, 200, 40)
RIGHT = (40, 150, 255)
MID = (240, 240, 240)
ACCENT = (120, 255, 140)

_LIMBS = [  # (a, b, side)
    (5, 7, "l"), (7, 9, "l"), (11, 13, "l"), (13, 15, "l"), (5, 11, "l"),
    (6, 8, "r"), (8, 10, "r"), (12, 14, "r"), (14, 16, "r"), (6, 12, "r"),
    (5, 6, "m"), (11, 12, "m"),
]


def _col(side: str):
    return LEFT if side == "l" else RIGHT if side == "r" else MID


def draw_skeleton(img: np.ndarray, kps: np.ndarray, conf_thr: float = 0.3,
                  scale: float | None = None, dim: bool = False) -> None:
    """Draw one 17-keypoint COCO skeleton in place. kps: [17, 3]."""
    if kps is None or not np.isfinite(kps[:, :2]).any():
        return
    h, w = img.shape[:2]
    s = scale or max(1.0, min(w, h) / 540.0)
    th = max(1, int(round(3 * s)))
    r = max(2, int(round(4 * s)))
    ok = np.isfinite(kps[:, 0]) & (kps[:, 2] >= conf_thr)
    for a, b, side in _LIMBS:
        if ok[a] and ok[b]:
            c = _col(side)
            if dim:
                c = tuple(int(v * 0.45) for v in c)
            pa = (int(round(kps[a, 0])), int(round(kps[a, 1])))
            pb = (int(round(kps[b, 0])), int(round(kps[b, 1])))
            cv2.line(img, pa, pb, (0, 0, 0), th + 2, cv2.LINE_AA)
            cv2.line(img, pa, pb, c, th, cv2.LINE_AA)
    # head: neck to nose
    if ok[5] and ok[6] and ok[0]:
        neck = ((kps[5, :2] + kps[6, :2]) / 2).astype(int)
        cv2.line(img, tuple(neck), (int(kps[0, 0]), int(kps[0, 1])), MID, th, cv2.LINE_AA)
    for i in range(17):
        if not ok[i] or (0 < i < 5):
            continue
        side = "m" if i == 0 else ("l" if i % 2 == 1 else "r")
        p = (int(round(kps[i, 0])), int(round(kps[i, 1])))
        cv2.circle(img, p, r + 1, (0, 0, 0), -1, cv2.LINE_AA)
        cv2.circle(img, p, r, _col(side), -1, cv2.LINE_AA)


def draw_text(img, text, org, scale=0.6, color=(255, 255, 255), bg=(0, 0, 0), thick=1, pad=4):
    s = max(0.4, scale * max(1.0, min(img.shape[:2]) / 540.0))
    t = max(1, int(round(thick * s / scale)))
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, s, t)
    x, y = int(org[0]), int(org[1])
    if bg is not None:
        overlay = img.copy()
        cv2.rectangle(overlay, (x - pad, y - th - pad), (x + tw + pad, y + base + pad), bg, -1)
        cv2.addWeighted(overlay, 0.6, img, 0.4, 0, img)
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, s, color, t, cv2.LINE_AA)
    return tw, th


def draw_angle(img, kps, a, b, c, conf_thr=0.3, color=(255, 255, 255)) -> None:
    """Label the joint angle at b (a-b-c)."""
    if min(kps[a, 2], kps[b, 2], kps[c, 2]) < conf_thr:
        return
    v1, v2 = kps[a, :2] - kps[b, :2], kps[c, :2] - kps[b, :2]
    n = np.linalg.norm(v1) * np.linalg.norm(v2)
    if n < 1e-6:
        return
    ang = np.degrees(np.arccos(np.clip(np.dot(v1, v2) / n, -1, 1)))
    draw_text(img, f"{ang:.0f}", (kps[b, 0] + 8, kps[b, 1] - 8), scale=0.45, color=color)


def draw_trail(img, pts: np.ndarray, color=ACCENT, max_len: int = 90) -> None:
    """Fading polyline of recent points [N, 2] (NaN rows skipped)."""
    pts = pts[-max_len:]
    n = len(pts)
    s = max(1.0, min(img.shape[:2]) / 540.0)
    for i in range(1, n):
        if not (np.isfinite(pts[i]).all() and np.isfinite(pts[i - 1]).all()):
            continue
        a = i / n
        c = tuple(int(v * (0.25 + 0.75 * a)) for v in color)
        cv2.line(img, tuple(pts[i - 1].astype(int)), tuple(pts[i].astype(int)), c,
                 max(1, int(round(2.5 * s * a + 1))), cv2.LINE_AA)
