"""Real-time capture: pose on streamed frames + automatic skill clipping.

Frames arrive as JPEGs (phone browser over WebSocket) or are pulled from a
network camera / webcam. Each frame: track people, pick the athlete, update
causal kinematics, update the auto-record state machine. Recorded clips keep
the original JPEG bytes (no re-encode until the clip is written) and are
imported into the library and queued for full offline analysis.
"""
from __future__ import annotations

import collections
import secrets
import struct
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from . import library as lib
from .camera_motion import MotionEstimator
from .config import settings
from .kinematics import center_of_mass
from .media import write_jpeg_sequence
from .pose import PoseEngine


class OneEuro:
    """One-Euro filter over an array of values (NaN-tolerant)."""

    def __init__(self, min_cutoff=1.5, beta=0.04, d_cutoff=1.0):
        self.mc, self.beta, self.dc = min_cutoff, beta, d_cutoff
        self.x = None
        self.dx = None
        self.t = None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * np.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: np.ndarray, t: float, scale: float = 1.0) -> np.ndarray:
        x = np.asarray(x, float)
        if self.x is None or self.t is None or t - self.t > 0.5:
            self.x, self.dx, self.t = x.copy(), np.zeros_like(x), t
            return x
        dt = max(1e-3, t - self.t)
        self.t = t
        prev = np.where(np.isfinite(self.x), self.x, x)
        dx = (x - prev) / dt
        a_d = self._alpha(self.dc, dt)
        self.dx = np.where(np.isfinite(dx), a_d * dx + (1 - a_d) * np.nan_to_num(self.dx), 0)
        cutoff = self.mc + self.beta * np.abs(self.dx) / max(scale, 1e-6)
        a = self._alpha(cutoff, dt)
        out = np.where(np.isfinite(x), a * x + (1 - a) * prev, prev)
        self.x = out
        return out


class LiveKinematics:
    """Causal version of the offline signals, enough to drive auto-recording."""

    def __init__(self, w: int, h: int):
        self.me = MotionEstimator(w, h)
        self.W = np.eye(3)
        self.hist: collections.deque = collections.deque()  # (t, kw[17,2], conf[17])
        self.seglens: collections.deque = collections.deque()  # (t, torso+thigh+shank)
        self.ground_samples: collections.deque = collections.deque()  # (t, y)
        self.lows: collections.deque = collections.deque()  # (t, y_low)
        self.act = 0.0
        self.t_last = None

    def _bl(self, now):
        while self.seglens and now - self.seglens[0][0] > 4.0:
            self.seglens.popleft()
        if not self.seglens:
            return None
        return float(np.percentile([s for _, s in self.seglens], 90))

    def update(self, t: float, frame: np.ndarray, boxes, kps: np.ndarray | None) -> dict:
        A, ok, cut, _ = self.me.update(frame, boxes)
        if cut:
            self.W = np.eye(3)
            self.hist.clear()
        else:
            try:
                self.W = self.W @ np.linalg.inv(A)
            except np.linalg.LinAlgError:
                pass
        out = {"valid": False, "act": self.act, "inv": None, "phase": "-", "speed": None, "elev": None}
        dt = (t - self.t_last) if self.t_last else 0.05
        self.t_last = t
        tau = 0.15
        decay = float(np.exp(-dt / tau))
        if kps is None:
            self.act *= decay
            out["act"] = self.act
            return out
        conf = kps[:, 2]
        xy = np.where(conf[:, None] >= 0.25, kps[:, :2], np.nan)
        kw = xy @ self.W[:2, :2].T + self.W[:2, 2]
        if not np.isfinite(kw[[5, 6, 11, 12]]).all():
            self.act *= decay
            out["act"] = self.act
            return out
        sh, hp = (kw[5] + kw[6]) / 2, (kw[11] + kw[12]) / 2
        torso = np.linalg.norm(sh - hp)
        thigh = np.nanmean([np.linalg.norm(kw[13] - kw[11]), np.linalg.norm(kw[14] - kw[12])])
        shank = np.nanmean([np.linalg.norm(kw[15] - kw[13]), np.linalg.norm(kw[16] - kw[14])])
        tot = torso + (thigh if np.isfinite(thigh) else torso * 0.85) + (shank if np.isfinite(shank) else torso * 0.85)
        self.seglens.append((t, tot))
        bl = self._bl(t) or tot
        v = sh - hp
        phi = float(np.degrees(np.arctan2(v[0], -v[1])))
        inv = abs(phi)
        com = center_of_mass(kw[None], np.where(np.isfinite(kw[:, 0]), conf, 0)[None])[0]
        self.hist.append((t, kw, com))
        while self.hist and t - self.hist[0][0] > 3.0:
            self.hist.popleft()
        # velocities over ~0.15 s
        past = None
        for ht, hkw, hcom in reversed(self.hist):
            if t - ht >= 0.15:
                past = (ht, hkw, hcom)
                break
        speed = rel = 0.0
        if past is not None:
            pdt = t - past[0]
            if np.isfinite(com).all() and np.isfinite(past[2]).all():
                speed = float(np.linalg.norm(com - past[2]) / pdt / bl)
            limbs = [7, 8, 9, 10, 13, 14, 15, 16]
            d = (kw[limbs] - com) - (past[1][limbs] - past[2])
            n = np.linalg.norm(d, axis=1)
            if np.isfinite(n).any():
                rel = float(np.nanmean(n) / pdt / bl)
        # ground: contact samples (lowest joint nearly still) else rolling percentile of the lowest point
        cand = [9, 10, 13, 14, 15, 16]
        offs = np.array([0.02, 0.02, 0.04, 0.04, 0.05, 0.05]) * bl
        ys = kw[cand, 1] + offs
        elev = 0.0
        if np.isfinite(ys).any():
            j = int(np.nanargmax(ys))
            y_low = float(ys[j])
            self.lows.append((t, y_low))
            while self.lows and t - self.lows[0][0] > 3.0:
                self.lows.popleft()
            if past is not None and np.isfinite(past[1][cand[j]]).all():
                jv = np.linalg.norm(kw[cand[j]] - past[1][cand[j]]) / (t - past[0]) / bl
                if jv < 0.8:
                    self.ground_samples.append((t, y_low))
            while self.ground_samples and t - self.ground_samples[0][0] > 2.5:
                self.ground_samples.popleft()
            if self.ground_samples:
                ground = float(np.median([g for _, g in self.ground_samples]))
            else:
                ground = float(np.percentile([y for _, y in self.lows], 92))
            elev = (ground - y_low) / bl
            wr = np.nanmax([kw[9, 1], kw[10, 1]]) + 0.02 * bl if np.isfinite(kw[[9, 10], 1]).any() else np.nan
            hands_down = np.isfinite(wr) and (ground - wr) / bl < 0.09
        else:
            hands_down = False
        s1 = np.clip((speed - 0.9) / 2.2, 0, 1)
        s2 = np.clip((rel - 1.0) / 3.0, 0, 1)
        s3 = np.clip((inv - 50) / 70, 0, 1)
        s4 = np.clip((elev - 0.06) / 0.2, 0, 1)
        raw = float(np.clip(0.35 * s1 + 0.35 * s2 + 0.55 * s3 + 0.5 * s4, 0, 1))
        a = 1 - decay
        self.act = a * raw + (1 - a) * self.act if raw < self.act else 0.6 * raw + 0.4 * self.act
        phase = "A" if elev > 0.1 else ("H" if (inv > 60 and hands_down) else "F")
        out.update(valid=True, act=self.act, inv=inv, phase=phase, speed=speed, elev=elev, rel=rel)
        return out


class AutoRecorder:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.pre: collections.deque = collections.deque()
        self.frames: list[tuple[float, bytes]] = []
        self.state = "idle"
        self.t_above = None
        self.t_below = None
        self.t_start = None
        self.manual = False

    def thresholds(self):
        k = 1.45 - 0.9 * float(np.clip(self.cfg.get("sensitivity", 0.5), 0, 1))
        return 0.42 * k, 0.20 * k

    def push(self, t: float, jpeg: bytes, act: float, armed: bool):
        """Returns a finished clip (list of frames) or None."""
        on, off = self.thresholds()
        pre_roll = float(self.cfg.get("pre_roll", 2.0))
        post_roll = float(self.cfg.get("post_roll", 1.5))
        if self.state == "idle":
            self.pre.append((t, jpeg))
            while self.pre and t - self.pre[0][0] > pre_roll:
                self.pre.popleft()
            trigger = self.manual or (armed and act >= on)
            if trigger:
                self.t_above = self.t_above or t
                if self.manual or t - self.t_above >= 0.12:
                    self.state = "rec"
                    self.frames = list(self.pre)
                    self.pre.clear()
                    self.t_start = self.frames[0][0] if self.frames else t
                    self.t_below = None
            else:
                self.t_above = None
            return None
        # recording
        self.frames.append((t, jpeg))
        if self.manual:
            stop = False
        else:
            if act < off:
                self.t_below = self.t_below or t
            else:
                self.t_below = None
            stop = (self.t_below is not None and t - self.t_below >= post_roll) or not armed
        if t - self.t_start > float(self.cfg.get("max_clip", 90)):
            stop = True
        if stop:
            return self.finish()
        return None

    def finish(self):
        frames, self.frames = self.frames, []
        self.state = "idle"
        self.t_above = self.t_below = None
        self.manual = False
        if len(frames) < 3:
            return None
        if frames[-1][0] - frames[0][0] < float(self.cfg.get("min_clip", 1.0)):
            return None
        return frames

    @property
    def rec_seconds(self) -> float:
        if self.state != "rec" or not self.frames:
            return 0.0
        return self.frames[-1][0] - self.frames[0][0]


class LiveSession:
    def __init__(self, name: str, kind: str = "browser"):
        self.id = secrets.token_hex(4)
        self.name = name or f"Live {time.strftime('%H:%M')}"
        self.kind = kind
        self.created = time.time()
        cfg = settings.get()["live"]
        self.cfg = dict(cfg)
        self.engine: PoseEngine | None = None
        self.kin: LiveKinematics | None = None
        self.rec = AutoRecorder(self.cfg)
        self.armed = True
        self.lock_id: int | None = None
        self.lock_seen = 0.0
        self.athlete_id: int | None = None
        self.scores: dict[int, float] = {}
        self.prev_boxes: dict[int, np.ndarray] = {}
        self.filters: dict[int, OneEuro] = {}
        self.events: collections.deque = collections.deque(maxlen=50)
        self.clips: list[dict] = []
        self.latest: tuple[int, bytes, dict] | None = None
        self.version = 0
        self.frames_in = 0
        self.fps = 0.0
        self.infer_ms = 0.0
        self._t_fps = time.time()
        self._n_fps = 0
        self._lock = threading.Lock()
        self.closed = False
        self.last_seen = time.time()
        self.t0_client = None
        self.lifter = None                 # live3d.Lifter while the 3D view or "3D in Unreal" is on
        self.view3d = False

    # -- setup -------------------------------------------------------------------------
    def _ensure(self, w, h):
        if self.engine is None:
            self.engine = PoseEngine(self.cfg.get("model", "yolo26s-pose.pt"),
                                     half=settings.get().get("half", True))
        if self.kin is None or (self.kin.me.full_w, self.kin.me.full_h) != (w, h):
            self.kin = LiveKinematics(w, h)

    # -- live 3D ------------------------------------------------------------------------------
    def ensure_lifter(self):
        if self.lifter is None:
            from .live3d import Lifter, ref_skeleton_path
            self.lifter = Lifter(ref_skeleton_path(), variant=self.cfg.get("lift3d_model", "heavy"))
            self.lifter.start()
        return self.lifter

    def release_lifter(self):
        """Stop lifting when neither the browser 3D view nor Unreal needs it."""
        if self.lifter is not None and not self.view3d and not self.lifter.to_ue:
            self.lifter.stop()
            self.lifter = None

    def set_view3d(self, on: bool):
        self.view3d = bool(on)
        if self.view3d:
            self.ensure_lifter()
        else:
            self.release_lifter()

    def configure(self, patch: dict):
        for k in ("sensitivity", "pre_roll", "post_roll", "min_clip", "max_clip", "auto_analyze"):
            if k in patch:
                self.cfg[k] = patch[k]
        self.rec.cfg = self.cfg
        if "armed" in patch:
            self.armed = bool(patch["armed"])
        if "name" in patch and patch["name"]:
            self.name = str(patch["name"])[:60]
        if "athlete" in patch:
            self.athlete = (str(patch["athlete"]).strip()[:60] or None) if patch["athlete"] else None

    # -- athlete selection ---------------------------------------------------------------
    def _choose(self, dets, t) -> int | None:
        ids = [int(i) for i in dets.ids]
        if not ids or ids[0] < 0:
            return None
        h_img = max(1.0, float(np.max(dets.boxes[:, 3])) if len(dets) else 1.0)
        for j, tid in enumerate(ids):
            b = dets.boxes[j]
            bh = max(1.0, b[3] - b[1])
            c = np.array([(b[0] + b[2]) / 2, (b[1] + b[3]) / 2])
            mot = 0.0
            if tid in self.prev_boxes:
                pb = self.prev_boxes[tid]
                pc = np.array([(pb[0] + pb[2]) / 2, (pb[1] + pb[3]) / 2])
                mot = np.linalg.norm(c - pc) / bh + abs((b[2] - b[0]) / bh - (pb[2] - pb[0]) / max(1, pb[3] - pb[1]))
            self.prev_boxes[tid] = b
            s = (bh / h_img) * (0.4 + 6.0 * mot) * float(dets.scores[j])
            self.scores[tid] = 0.85 * self.scores.get(tid, s) + 0.15 * s
        for tid in list(self.scores):
            if tid not in ids:
                self.scores[tid] *= 0.9
                if self.scores[tid] < 1e-3:
                    self.scores.pop(tid, None)
                    self.prev_boxes.pop(tid, None)
                    self.filters.pop(tid, None)
        if self.lock_id is not None:
            if self.lock_id in ids:
                self.lock_seen = t
                return self.lock_id
            if t - self.lock_seen > 2.0:
                self.lock_id = None
        best = max(ids, key=lambda i: self.scores.get(i, 0))
        cur = self.athlete_id
        if cur is None or cur not in ids or self.scores.get(best, 0) > 1.6 * self.scores.get(cur, 0):
            self.athlete_id = best
        return self.athlete_id

    def lock_at(self, x: float, y: float):
        """Lock the athlete to the person under normalised point (x, y) of the last frame."""
        if not self.latest:
            return
        info = self.latest[2]
        w, h = info["w"], info["h"]
        px, py = x * w, y * h
        best, best_a = None, 1e18
        for p in info["people"]:
            b = p["box"]
            if b[0] <= px <= b[2] and b[1] <= py <= b[3]:
                area = (b[2] - b[0]) * (b[3] - b[1])
                if area < best_a:
                    best, best_a = p["id"], area
        self.lock_id = best
        self.lock_seen = time.time()
        if best is not None:
            self.athlete_id = best

    # -- frames ----------------------------------------------------------------------------
    def process_jpeg(self, jpeg: bytes, client_ts: float | None = None) -> dict:
        frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return {"type": "error", "message": "bad frame"}
        # The phone's capture clock gives true frame spacing (network jitter excluded)
        if client_ts:
            if self.t0_client is None:
                self.t0_client = (client_ts / 1000.0, time.time())
            t = self.t0_client[1] + (client_ts / 1000.0 - self.t0_client[0])
        else:
            t = time.time()
        return self._process(frame, jpeg, t, client_ts)

    def process_bgr(self, frame: np.ndarray, t: float | None = None) -> dict:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, int(self.cfg.get("jpeg_quality", 85))])
        return self._process(frame, buf.tobytes(), t or time.time(), None)

    def _process(self, frame, jpeg: bytes, t: float, client_ts) -> dict:
        with self._lock:
            self.last_seen = time.time()
            h, w = frame.shape[:2]
            self._ensure(w, h)
            t1 = time.perf_counter()
            dets = self.engine.track(frame, imgsz=int(self.cfg.get("detect_size", 640)), conf=0.15)
            self.infer_ms = 0.8 * self.infer_ms + 0.2 * (time.perf_counter() - t1) * 1000
            aid = self._choose(dets, t)
            people = []
            ath_kps = None
            for j in range(len(dets)):
                tid = int(dets.ids[j])
                k = dets.kps[j].astype(float)
                if tid >= 0:
                    f = self.filters.setdefault(tid, OneEuro())
                    scale = max(1.0, float(dets.boxes[j, 3] - dets.boxes[j, 1]))
                    xy = f(np.where(k[:, 2:3] >= 0.2, k[:, :2], np.nan), t, scale)
                    k[:, :2] = np.where(np.isfinite(xy), xy, k[:, :2])
                item = {"id": tid, "box": [round(float(v), 1) for v in dets.boxes[j]],
                        "score": round(float(dets.scores[j]), 2)}
                if tid == aid:
                    ath_kps = k
                    item["kps"] = np.round(k, 1).reshape(-1).tolist()
                people.append(item)
            if self.lifter is not None and ath_kps is not None:
                j = [int(i) for i in dets.ids].index(aid)
                self.lifter.submit(frame, dets.boxes[j], ath_kps, t)
            kin = self.kin.update(t, frame, dets.boxes, ath_kps)
            clip = self.rec.push(t, jpeg, kin["act"], self.armed)
            if clip:
                self._save_clip_async(clip)
            self.frames_in += 1
            self._n_fps += 1
            if t - self._t_fps >= 1.0:
                self.fps = self._n_fps / (t - self._t_fps)
                self._t_fps, self._n_fps = t, 0
            info = {"type": "pose", "ts": client_ts, "w": w, "h": h, "people": people, "athlete": aid,
                    "locked": self.lock_id is not None, "act": round(kin["act"], 3),
                    "on": round(self.rec.thresholds()[0], 3), "off": round(self.rec.thresholds()[1], 3),
                    "phase": kin["phase"], "inv": None if kin["inv"] is None else round(kin["inv"]),
                    "state": self.rec.state, "rec_t": round(self.rec.rec_seconds, 1), "armed": self.armed,
                    "clips": len(self.clips), "fps": round(self.fps, 1), "ms": round(self.infer_ms, 1)}
            lv = self.lifter.view if self.lifter is not None else None
            if self.view3d and lv and time.time() - lv[0] < 0.6:
                info["p3d"] = lv[1]
            self.latest = (self.version + 1, jpeg, info)
            self.version += 1
            return info

    # -- manual control / clips --------------------------------------------------------------
    def manual_record(self, on: bool):
        with self._lock:
            if on and self.rec.state == "idle":
                self.rec.manual = True
            elif not on and self.rec.state == "rec":
                clip = self.rec.finish()
                if clip:
                    self._save_clip_async(clip)

    def _save_clip_async(self, frames):
        threading.Thread(target=self._save_clip, args=(frames,), daemon=True).start()

    def _save_clip(self, frames):
        try:
            name = f"{self.name} {time.strftime('%H-%M-%S', time.localtime(frames[0][0]))}"
            meta = lib.create(name, "live", "mp4")
            vid = meta["id"]
            dst = lib.vdir(vid) / meta["source_file"]
            fps = write_jpeg_sequence(frames, dst)
            dur = frames[-1][0] - frames[0][0]
            lib.update(vid, status="queued", live_session=self.id, athlete=getattr(self, "athlete", None))
            info = {"type": "clip", "id": vid, "name": name, "duration": round(dur, 2), "fps": fps}
            self.clips.append(info)
            self.events.append(info)
            if self.cfg.get("auto_analyze", True):
                from .jobs import manager
                from .pipeline import job_process
                manager.submit("gpu", "analyze", f"Analyse {name}", vid, job_process, vid)
        except Exception as e:  # noqa: BLE001
            self.events.append({"type": "error", "message": f"clip save failed: {e}"})

    def pop_events(self) -> list[dict]:
        out = []
        while self.events:
            out.append(self.events.popleft())
        return out

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.lifter is not None:
            from .ue3d import live_stop
            self.view3d = False
            threading.Thread(target=live_stop, args=(self,), daemon=True).start()
        with self._lock:
            if self.rec.state == "rec":
                clip = self.rec.finish()
                if clip:
                    self._save_clip_async(clip)

    def summary(self) -> dict:
        info = self.latest[2] if self.latest else {}
        return {"id": self.id, "name": self.name, "kind": self.kind, "created": self.created,
                "armed": self.armed, "state": self.rec.state, "fps": round(self.fps, 1),
                "clips": self.clips[-20:], "frames": self.frames_in, "act": info.get("act"),
                "closed": self.closed, "cfg": {k: self.cfg.get(k) for k in ("sensitivity", "pre_roll", "post_roll")},
                "ue3d": _ue3d_status(self), "view3d": self.view3d}

    def pack_latest(self, since: int) -> tuple[int, bytes] | None:
        """Binary viewer packet: [u32 json_len][json][jpeg]."""
        lt = self.latest
        if not lt or lt[0] <= since:
            return None
        import json
        head = json.dumps(lt[2]).encode()
        return lt[0], struct.pack("<I", len(head)) + head + lt[1]


def _ue3d_status(session) -> dict:
    from .ue3d import live_status
    return live_status(session)


class CameraSource(threading.Thread):
    """Pull frames from a URL (RTSP/HTTP MJPEG, e.g. Android 'IP Webcam') or a webcam index."""

    def __init__(self, session: LiveSession, url: str):
        super().__init__(daemon=True)
        self.session, self.url = session, url
        self.stop_flag = False
        self.error = None
        self._latest = None
        self._cond = threading.Condition()

    def _open(self):
        src = int(self.url) if self.url.strip().isdigit() else self.url
        if isinstance(src, int):
            cap = cv2.VideoCapture(src, cv2.CAP_DSHOW)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        else:
            cap = cv2.VideoCapture(src, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _grab(self, cap):
        while not self.stop_flag:
            ok, f = cap.read()
            if not ok:
                self.error = "stream ended or unreachable"
                break
            with self._cond:
                self._latest = f
                self._cond.notify()

    def run(self):
        cap = self._open()
        if not cap.isOpened():
            self.error = f"cannot open {self.url}"
            self.session.events.append({"type": "error", "message": self.error})
            self.session.close()
            return
        g = threading.Thread(target=self._grab, args=(cap,), daemon=True)
        g.start()
        while not self.stop_flag and not self.session.closed:
            with self._cond:
                self._cond.wait(timeout=1.0)
                f, self._latest = self._latest, None
            if f is None:
                if self.error:
                    break
                continue
            mx = 1280
            if max(f.shape[:2]) > mx:
                s = mx / max(f.shape[:2])
                f = cv2.resize(f, (int(f.shape[1] * s), int(f.shape[0] * s)), interpolation=cv2.INTER_AREA)
            self.session.process_bgr(f)
        self.stop_flag = True
        cap.release()
        self.session.close()


class LiveManager:
    def __init__(self):
        self.sessions: dict[str, LiveSession] = {}
        self.sources: dict[str, CameraSource] = {}
        self._lock = threading.Lock()

    def create(self, name: str, kind: str = "browser") -> LiveSession:
        s = LiveSession(name, kind)
        with self._lock:
            self.sessions[s.id] = s
            # drop closed sessions older than 2 h
            for sid in [k for k, v in self.sessions.items() if v.closed and time.time() - v.last_seen > 7200]:
                self.sessions.pop(sid, None)
        return s

    def start_camera(self, url: str, name: str | None = None) -> LiveSession:
        s = self.create(name or f"Camera {url}", "camera")
        src = CameraSource(s, url)
        self.sources[s.id] = src
        src.start()
        return s

    def get(self, sid: str) -> LiveSession | None:
        return self.sessions.get(sid)

    def stop(self, sid: str):
        src = self.sources.pop(sid, None)
        if src:
            src.stop_flag = True
        s = self.sessions.get(sid)
        if s:
            s.close()

    def list(self) -> list[dict]:
        return [s.summary() for s in sorted(self.sessions.values(), key=lambda s: -s.created)]


manager = LiveManager()
