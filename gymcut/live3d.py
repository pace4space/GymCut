"""Live 3D: lift the live athlete to 3D and drive a MetaHuman in the Unreal editor.

  phone frame -> (athlete box from the live YOLO tracker) -> MediaPipe pose landmarker
  (3D world landmarks, metres, hip-centred) -> retarget onto the MetaHuman reference
  skeleton (component-space bone rotations) -> UDP JSON -> GymCutUE/Scripts/live_puppet.py

Travel and height come from the 2D track (static camera assumed), so the puppet really
leaves the floor in a salto instead of being pinned to it. Quaternions are (x, y, z, w)
in Unreal's component space (X = character left, Y = forward, Z = up for the MetaHuman).
"""
from __future__ import annotations

import collections
import json
import socket
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from .config import MODELS_DIR

PORT = 54321            # Python puppet (live_puppet.py)
# joints for the browser 3D view (same order as gymcut.pose3d.JOINTS)
VIEW_JOINTS = ["pelvis", "spine_03", "spine_05", "neck_01", "head",
               "clavicle_l", "upperarm_l", "lowerarm_l", "hand_l", "clavicle_r", "upperarm_r", "lowerarm_r", "hand_r",
               "thigh_l", "calf_l", "foot_l", "ball_l", "thigh_r", "calf_r", "foot_r", "ball_r"]


def ref_skeleton_path() -> Path:
    """MetaHuman reference skeleton: the one Unreal last exported, else the copy bundled with GymCut."""
    from .ue3d import saved_dir
    p = saved_dir() / "mh_ref_skeleton.json"
    return p if p.exists() else Path(__file__).with_name("assets") / "mh_ref_skeleton.json"


LIVELINK_PORT = 54322   # GymCutLiveLink plugin (real Live Link source, subject "GymCut")
# MediaPipe landmark indices
NOSE, EAR_L, EAR_R = 0, 7, 8
SH_L, SH_R, EL_L, EL_R, WR_L, WR_R = 11, 12, 13, 14, 15, 16
PINKY_L, PINKY_R, INDEX_L, INDEX_R = 17, 18, 19, 20
HIP_L, HIP_R, KN_L, KN_R, AN_L, AN_R = 23, 24, 25, 26, 27, 28
TOE_L, TOE_R = 31, 32


# ---------------------------------------------------------------- quaternion helpers (x, y, z, w)
def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array([aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw,
                     aw * bw - ax * bx - ay * by - az * bz])


def qconj(q):
    return np.array([-q[0], -q[1], -q[2], q[3]])


def qrot(q, v):
    u, w = q[:3], q[3]
    return 2 * (u @ v) * u + (w * w - u @ u) * v + 2 * w * np.cross(u, v)


def q_from_to(a, b):
    a = a / (np.linalg.norm(a) + 1e-9)
    b = b / (np.linalg.norm(b) + 1e-9)
    c = np.cross(a, b)
    d = float(a @ b)
    if d < -0.9999:                                   # opposite: any perpendicular axis
        axis = np.cross(a, [1, 0, 0]) if abs(a[0]) < 0.9 else np.cross(a, [0, 1, 0])
        axis /= np.linalg.norm(axis)
        return np.array([*axis, 0.0])
    q = np.array([*c, 1.0 + d])
    return q / np.linalg.norm(q)


def q_from_matrix(m):
    t = m[0, 0] + m[1, 1] + m[2, 2]
    if t > 0:
        s = np.sqrt(t + 1.0) * 2
        q = [(m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, 0.25 * s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = [0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s, (m[2, 1] - m[1, 2]) / s]
    elif m[1, 1] > m[2, 2]:
        s = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = [(m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s, (m[0, 2] - m[2, 0]) / s]
    else:
        s = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = [(m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s, (m[1, 0] - m[0, 1]) / s]
    q = np.array(q)
    return q / np.linalg.norm(q)


def _basis(primary, secondary):
    x = primary / (np.linalg.norm(primary) + 1e-9)
    y = secondary - (secondary @ x) * x
    y /= np.linalg.norm(y) + 1e-9
    return np.stack([x, y, np.cross(x, y)], 1)


def q_frame(ref_p, ref_s, obs_p, obs_s):
    """Rotation taking the reference frame (primary, secondary) onto the observed one."""
    return q_from_matrix(_basis(obs_p, obs_s) @ _basis(ref_p, ref_s).T)


def qslerp(a, b, t):
    if a @ b < 0:
        b = -b
    q = a + (b - a) * t
    return q / np.linalg.norm(q)


# ---------------------------------------------------------------- retarget
class Retarget:
    """MediaPipe world landmarks -> MetaHuman component-space bone rotations."""

    def __init__(self, ref_path: Path):
        d = json.loads(Path(ref_path).read_text())
        B = d["bones"]
        self.order = d["order"]
        self.parent = {b: (B[b]["parent"] if B[b]["parent"] in B else None) for b in self.order}
        self.t = {b: np.array(B[b]["t"], float) for b in self.order}
        self.q = {b: np.array(B[b]["q"], float) for b in self.order}
        self.loc_q = {b: (qmul(qconj(self.q[p]), self.q[b]) if (p := self.parent[b]) else self.q[b]) for b in self.order}
        self.loc_t = {b: (qrot(qconj(self.q[p]), self.t[b] - self.t[p]) if (p := self.parent[b]) else self.t[b]) for b in self.order}
        t = self.t
        self.ref_up = t["neck_01"] - t["pelvis"]
        self.ref_hips = t["thigh_l"] - t["thigh_r"]
        self.ref_sh = t["upperarm_l"] - t["upperarm_r"]
        self.ref_neck = t["head"] - t["neck_01"]
        # limb bone -> (child bone for the reference direction, landmark pair for the observed direction)
        self.limbs = {"upperarm_l": ("lowerarm_l", (SH_L, EL_L)), "lowerarm_l": ("hand_l", (EL_L, WR_L)),
                      "upperarm_r": ("lowerarm_r", (SH_R, EL_R)), "lowerarm_r": ("hand_r", (EL_R, WR_R)),
                      "thigh_l": ("calf_l", (HIP_L, KN_L)), "calf_l": ("foot_l", (KN_L, AN_L)), "foot_l": ("ball_l", (AN_L, TOE_L)),
                      "thigh_r": ("calf_r", (HIP_R, KN_R)), "calf_r": ("foot_r", (KN_R, AN_R)), "foot_r": ("ball_r", (AN_R, TOE_R))}

    @property
    def parent_idx(self) -> list[int]:
        return [self.order.index(p) if p else -1 for p in (self.parent[b] for b in self.order)]

    def local_transforms(self, q: dict[str, np.ndarray], pelvis: np.ndarray) -> list[list[float]]:
        """[tx, ty, tz, qx, qy, qz, qw] per bone, relative to its parent (root bone: component space)."""
        out = []
        for b in self.order:
            p = self.parent[b]
            if p is None:
                t, r = pelvis, q[b]
            else:
                t, r = self.loc_t[b], qmul(qconj(q[p]), q[b])
            out.append([round(float(v), 3) for v in t] + [round(float(v), 5) for v in r])
        return out

    @staticmethod
    def to_ue(world: np.ndarray) -> np.ndarray:
        """MediaPipe camera frame (x right, y down, z away; metres) -> UE (cm) for a viewer
        standing on -Y looking +Y: screen-right = -X, away = +Y, up = +Z."""
        return np.stack([-world[:, 0], world[:, 2], -world[:, 1]], 1) * 100.0

    def solve(self, P: np.ndarray) -> dict[str, np.ndarray]:
        mid = lambda a, b: (P[a] + P[b]) / 2
        hip, sh, ear = mid(HIP_L, HIP_R), mid(SH_L, SH_R), mid(EAR_L, EAR_R)
        up = sh - hip
        R_p = q_frame(self.ref_hips, self.ref_up, P[HIP_L] - P[HIP_R], up)
        R_c = q_frame(self.ref_up, self.ref_sh, up, P[SH_L] - P[SH_R])
        R_h = q_frame(self.ref_neck, self.ref_sh, ear - sh, P[EAR_L] - P[EAR_R])
        out: dict[str, np.ndarray] = {}
        spine_w = {"spine_01": 0.2, "spine_02": 0.4, "spine_03": 0.6, "spine_04": 0.8, "spine_05": 1.0}
        for b in self.order:
            p = self.parent[b]
            if b == "pelvis":
                out[b] = qmul(R_p, self.q[b])
            elif b in spine_w:
                out[b] = qmul(qslerp(R_p, R_c, spine_w[b]), self.q[b])
            elif b in ("neck_01", "neck_02", "head"):
                out[b] = qmul(qslerp(R_c, R_h, {"neck_01": 0.4, "neck_02": 0.7, "head": 1.0}[b]), self.q[b])
            else:
                before = qmul(out[p], self.loc_q[b]) if p in out else self.q[b]
                if b in self.limbs:
                    child, (j0, j1) = self.limbs[b]
                    d_cur = qrot(before, qrot(qconj(self.q[b]), self.t[child] - self.t[b]))
                    d_obs = P[j1] - P[j0]
                    if np.isfinite(d_obs).all() and np.linalg.norm(d_obs) > 1e-3:
                        before = qmul(q_from_to(d_cur, d_obs), before)
                out[b] = before
        return out

    def fk(self, q: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        """Component-space joint positions relative to the pelvis for the solved rotations."""
        pos = {}
        for b in self.order:
            p = self.parent[b]
            pos[b] = np.zeros(3) if p is None else pos[p] + qrot(q[p], self.loc_t[b])
        return pos


# ---------------------------------------------------------------- root motion from the 2D track
class RootTracker:
    """Pelvis travel / height (cm) from the 2D hips & ankles of a static camera."""

    def __init__(self):
        self.ankle_y = collections.deque(maxlen=240)
        self.px_per_cm = collections.deque(maxlen=240)
        self.x0 = None

    def update(self, kps: np.ndarray | None, P: np.ndarray, t: float):
        if kps is None or len(kps) < 17:
            return None
        ok = kps[:, 2] >= 0.3
        if not (ok[11] and ok[12]):
            return None
        hip = (kps[11, :2] + kps[12, :2]) / 2
        if ok[15] or ok[16]:
            self.ankle_y.append((t, max(kps[i, 1] for i in (15, 16) if ok[i])))
        # scale: longest 2D shoulder-hip-knee-ankle chain vs its 3D length (foreshortening only shortens 2D)
        segs = [(5, 11, SH_L, HIP_L), (6, 12, SH_R, HIP_R), (11, 13, HIP_L, KN_L), (12, 14, HIP_R, KN_R),
                (13, 15, KN_L, AN_L), (14, 16, KN_R, AN_R)]
        l2 = sum(np.linalg.norm(kps[a, :2] - kps[b, :2]) for a, b, _, _ in segs if ok[a] and ok[b])
        l3 = sum(np.linalg.norm(P[c] - P[d]) for a, b, c, d in segs if ok[a] and ok[b])
        if l3 > 50:
            self.px_per_cm.append(l2 / l3)
        if len(self.px_per_cm) < 5 or not self.ankle_y:
            return None
        ppc = float(np.percentile(self.px_per_cm, 85))
        recent = [y for (tt, y) in self.ankle_y if t - tt < 4.0] or [self.ankle_y[-1][1]]
        ground = float(np.percentile(recent, 90))
        if self.x0 is None:
            self.x0 = float(hip[0])
        travel = -(float(hip[0]) - self.x0) / ppc            # screen-right = -X in the UE view
        height = (ground - float(hip[1])) / ppc + 8.0         # ankles sit ~8 cm above the floor
        return travel, height


# ---------------------------------------------------------------- worker
class Lifter(threading.Thread):
    """Runs on the latest athlete frame only (drops frames when busy) and streams to Unreal."""

    def __init__(self, ref_path: Path, variant: str = "heavy", host: str = "127.0.0.1", port: int = PORT):
        super().__init__(daemon=True)
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision
        self._mp = mp
        opts = vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(MODELS_DIR / f"pose_landmarker_{variant}.task")),
            running_mode=vision.RunningMode.VIDEO, min_pose_detection_confidence=0.3,
            min_pose_presence_confidence=0.3, min_tracking_confidence=0.3)
        self.lm = vision.PoseLandmarker.create_from_options(opts)
        self.rt = Retarget(ref_path)
        self.root = RootTracker()
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._latest = None
        self._ev = threading.Event()
        self._stop = False
        self._ts = 0
        self.fps = 0.0
        self.ms = 0.0
        self.sent = 0
        self.ok_ratio = 1.0
        self.to_ue = False            # stream to Unreal (puppet / Live Link) - the browser view always gets joints
        self.view = None              # (time, flat joint list in UE cm) for the browser 3D view
        self._n, self._t = 0, time.time()

    def submit(self, frame: np.ndarray, box, kps, t: float):
        self._latest = (frame, None if box is None else np.asarray(box, float), None if kps is None else np.asarray(kps, float), t)
        self._ev.set()

    def stop(self):
        self._stop = True
        self._ev.set()

    def _crop(self, frame, box):
        H, W = frame.shape[:2]
        if box is None:
            return frame, 0, 0
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        s = max(box[2] - box[0], box[3] - box[1]) * 1.45
        x0, y0 = int(max(0, cx - s / 2)), int(max(0, cy - s / 2))
        x1, y1 = int(min(W, cx + s / 2)), int(min(H, cy + s / 2))
        return frame[y0:y1, x0:x1], x0, y0

    def step(self, frame, box, kps, t) -> dict | None:
        t0 = time.perf_counter()
        crop, _, _ = self._crop(frame, box)
        if crop.size == 0:
            return None
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        self._ts = max(self._ts + 1, int(t * 1000))
        res = self.lm.detect_for_video(self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)), self._ts)
        self.ok_ratio = 0.9 * self.ok_ratio + 0.1 * (1.0 if res.pose_world_landmarks else 0.0)
        if not res.pose_world_landmarks:
            return None
        W = np.array([[p.x, p.y, p.z] for p in res.pose_world_landmarks[0]], float)
        P = self.rt.to_ue(W)
        q = self.rt.solve(P)
        fk = self.rt.fk(q)
        # lowest body point relative to the pelvis joint (hips/back count too: sitting falls, back rolls)
        lowest = min([fk[b][2] for b in ("ball_l", "ball_r", "foot_l", "foot_r", "hand_l", "hand_r", "head", "calf_l", "calf_r")]
                     + [fk[b][2] - 12.0 for b in ("pelvis", "spine_03", "spine_05")])
        root = self.root.update(kps, P, t)
        z_floor = -lowest + 1.0                                 # nothing below the floor
        if root:
            travel, hip_h = root
            z = max(z_floor, hip_h + 3.0)                       # pelvis joint sits ~3 cm above the hip line
        else:
            travel, z = 0.0, z_floor
        pelvis = np.array([travel, 0.0, z])
        pkt = {"t": round(t, 3), "p": [round(travel, 1), 0.0, round(z, 1)],
               "q": {b: [round(float(v), 5) for v in q[b]] for b in self.rt.order},
               # Live Link (Animation role): bone list + parent-local transforms
               "subject": "GymCut", "bones": self.rt.order, "parents": self.rt.parent_idx,
               "tr": self.rt.local_transforms(q, pelvis)}
        self.view = (time.time(), [round(float(v), 1) for b in VIEW_JOINTS for v in (fk[b] + pelvis)])
        if self.to_ue:
            data = json.dumps(pkt, separators=(",", ":")).encode()
            try:
                self.sock.sendto(data, self.addr)
                self.sock.sendto(data, (self.addr[0], LIVELINK_PORT))
            except OSError:                                  # nobody listening on one of the ports
                pass
        self.sent += 1
        self.ms = 0.8 * self.ms + 0.2 * (time.perf_counter() - t0) * 1000
        return pkt

    def run(self):
        while not self._stop:
            self._ev.wait(0.5)
            self._ev.clear()
            item, self._latest = self._latest, None
            if item is None:
                continue
            try:
                self.step(*item)
            except Exception:                                   # noqa: BLE001 - keep the live loop alive
                pass
            self._n += 1
            if time.time() - self._t >= 1.0:
                self.fps = self._n / (time.time() - self._t)
                self._n, self._t = 0, time.time()
        self.lm.close()
        self.sock.close()

    def stats(self) -> dict:
        return {"fps": round(self.fps, 1), "ms": round(self.ms), "sent": self.sent, "tracking": round(self.ok_ratio, 2)}
