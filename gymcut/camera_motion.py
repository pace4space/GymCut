"""Global camera motion (pan/zoom) estimation and hard-cut detection.

Phone footage is usually hand-held or panning to follow the athlete, so raw
pixel motion of the athlete is meaningless. We track background corners
(people masked out) with Lucas-Kanade and fit a similarity transform per frame,
then chain them so every frame can be mapped into its shot's reference frame.
"""
from __future__ import annotations

import cv2
import numpy as np

PROC_W = 320


class MotionEstimator:
    def __init__(self, full_w: int, full_h: int):
        self.full_w, self.full_h = full_w, full_h
        self.s = PROC_W / float(full_w)
        self.proc_size = (PROC_W, max(2, int(round(full_h * self.s))))
        self.prev_gray: np.ndarray | None = None
        self.prev_hist: np.ndarray | None = None
        self.prev_mask: np.ndarray | None = None

    def _mask(self, boxes: np.ndarray | None) -> np.ndarray:
        w, h = self.proc_size
        m = np.full((h, w), 255, np.uint8)
        if boxes is not None:
            for x1, y1, x2, y2 in np.asarray(boxes, dtype=np.float64).reshape(-1, 4):
                bw, bh = (x2 - x1) * 0.15, (y2 - y1) * 0.15
                cv2.rectangle(m, (int((x1 - bw) * self.s), int((y1 - bh) * self.s)),
                              (int((x2 + bw) * self.s), int((y2 + bh) * self.s)), 0, -1)
        return m

    def update(self, frame: np.ndarray, boxes: np.ndarray | None = None):
        """Returns (A, ok, cut, hist_dist). A is 3x3 mapping prev-frame px -> this-frame px."""
        small = cv2.resize(frame, self.proc_size, interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256])
        cv2.normalize(hist, hist, 1.0, 0.0, cv2.NORM_L1)
        mask = self._mask(boxes)

        A = np.eye(3)
        ok, cut, hdist = True, False, 0.0
        if self.prev_gray is not None:
            hdist = float(cv2.compareHist(self.prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA))
            pts = cv2.goodFeaturesToTrack(self.prev_gray, maxCorners=300, qualityLevel=0.01,
                                          minDistance=7, mask=self.prev_mask, blockSize=7)
            inliers = 0
            if pts is not None and len(pts) >= 12:
                nxt, st, _ = cv2.calcOpticalFlowPyrLK(self.prev_gray, gray, pts, None,
                                                      winSize=(21, 21), maxLevel=3)
                good = st.reshape(-1) == 1
                p0, p1 = pts.reshape(-1, 2)[good], nxt.reshape(-1, 2)[good]
                if len(p0) >= 10:
                    M, inl = cv2.estimateAffinePartial2D(p0, p1, method=cv2.RANSAC,
                                                         ransacReprojThreshold=1.5,
                                                         maxIters=500, confidence=0.99)
                    if M is not None and inl is not None:
                        inliers = int(inl.sum())
                        if inliers >= 10 and inliers >= 0.3 * len(p0):
                            sc = np.hypot(M[0, 0], M[1, 0])
                            # Treat sub-pixel jitter as a static camera to avoid drift.
                            if abs(M[0, 2]) < 0.12 and abs(M[1, 2]) < 0.12 and abs(sc - 1) < 0.002:
                                M = np.array([[1, 0, 0], [0, 1, 0]], dtype=np.float64)
                            A = np.vstack([M, [0, 0, 1]])
                            A[0, 2] /= self.s
                            A[1, 2] /= self.s
                        else:
                            ok = False
                    else:
                        ok = False
                else:
                    ok = False
            else:
                ok = False
            cut = hdist > 0.55 or (hdist > 0.35 and not ok)
            if cut:
                A = np.eye(3)
        self.prev_gray, self.prev_hist, self.prev_mask = gray, hist, mask
        return A, ok, cut, hdist


def chain_transforms(A: np.ndarray, cuts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A[t] maps frame t-1 -> t. Returns W[t] (frame t px -> shot reference px) and shot ids."""
    T = len(A)
    W = np.zeros((T, 3, 3))
    shot = np.zeros(T, dtype=np.int32)
    cur = np.eye(3)
    sid = 0
    for t in range(T):
        if t == 0 or cuts[t]:
            cur = np.eye(3)
            if t > 0:
                sid += 1
        else:
            try:
                cur = cur @ np.linalg.inv(A[t])
            except np.linalg.LinAlgError:
                pass
        W[t] = cur
        shot[t] = sid
    return W, shot


def apply_transform(W: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Map points [T, ..., 2] with per-frame 3x3 transforms W[T]. NaNs propagate."""
    shp = pts.shape
    p = pts.reshape(shp[0], -1, 2)
    out = np.einsum("tij,tkj->tki", W[:, :2, :2], p) + W[:, None, :2, 2]
    return out.reshape(shp)


def transform_scale(W: np.ndarray) -> np.ndarray:
    """Per-frame zoom factor of the frame->reference transform."""
    return np.sqrt(np.abs(W[:, 0, 0] * W[:, 1, 1] - W[:, 0, 1] * W[:, 1, 0]))
