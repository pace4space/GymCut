"""Pose model wrapper (Ultralytics YOLO-pose) with tracking and batched crop inference."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from .config import DATA_DIR, MODELS_DIR

KP_NAMES = ["nose", "l_eye", "r_eye", "l_ear", "r_ear", "l_shoulder", "r_shoulder",
            "l_elbow", "r_elbow", "l_wrist", "r_wrist", "l_hip", "r_hip",
            "l_knee", "r_knee", "l_ankle", "r_ankle"]
NOSE, LEYE, REYE, LEAR, REAR, LSH, RSH, LEL, REL, LWR, RWR, LHIP, RHIP, LKNE, RKNE, LANK, RANK = range(17)
LR_PAIRS = [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10), (11, 12), (13, 14), (15, 16)]
SKELETON = [(5, 7), (7, 9), (6, 8), (8, 10), (5, 6), (5, 11), (6, 12), (11, 12),
            (11, 13), (13, 15), (12, 14), (14, 16), (0, 5), (0, 6)]
KNOWN_MODELS = [f"yolo26{s}-pose.pt" for s in "nsmlx"] + [f"yolo11{s}-pose.pt" for s in "nsmlx"]


@dataclass
class Detections:
    boxes: np.ndarray    # [N, 4] xyxy
    scores: np.ndarray   # [N]
    kps: np.ndarray      # [N, 17, 3] x, y, conf
    ids: np.ndarray      # [N] track ids (-1 if untracked)

    @staticmethod
    def empty() -> "Detections":
        return Detections(np.zeros((0, 4), np.float32), np.zeros(0, np.float32),
                          np.zeros((0, 17, 3), np.float32), np.zeros(0, np.int64))

    def __len__(self) -> int:
        return len(self.scores)


_download_lock = threading.Lock()


def model_path(name: str) -> Path:
    """Resolve a model name to a local file, downloading official weights if needed."""
    p = Path(name)
    if p.is_absolute() and p.exists():
        return p
    local = MODELS_DIR / p.name
    if local.exists():
        return local
    with _download_lock:
        if not local.exists():
            from ultralytics.utils.downloads import attempt_download_asset
            got = Path(attempt_download_asset(local))
            if got.exists() and got.resolve() != local.resolve():
                got.replace(local)
    if not local.exists():
        raise FileNotFoundError(f"could not obtain pose model {name}")
    return local


def tracker_config() -> str:
    """ByteTrack tuned for gymnastics: keep lost tracks longer (flips break detections)."""
    import ultralytics
    src = Path(ultralytics.__file__).parent / "cfg" / "trackers" / "bytetrack.yaml"
    cfg = yaml.safe_load(src.read_text("utf-8"))
    cfg.update({"track_high_thresh": 0.3, "track_low_thresh": 0.08, "new_track_thresh": 0.35,
                "track_buffer": 90, "match_thresh": 0.85, "fuse_score": True})
    out = DATA_DIR / "gym_bytetrack.yaml"
    out.write_text(yaml.safe_dump(cfg), "utf-8")
    return str(out)


def _device() -> tuple[object, bool]:
    try:
        import torch
        if torch.cuda.is_available():
            return 0, True
    except Exception:
        pass
    return "cpu", False


def gpu_info() -> dict:
    try:
        import torch
        if torch.cuda.is_available():
            p = torch.cuda.get_device_properties(0)
            return {"cuda": True, "name": p.name, "vram_gb": round(p.total_memory / 2**30, 1),
                    "torch": torch.__version__}
        return {"cuda": False, "name": "CPU", "torch": torch.__version__}
    except Exception as e:  # pragma: no cover
        return {"cuda": False, "name": f"unavailable ({e})"}


class PoseEngine:
    """One model instance = one tracker state. Not thread-safe; use one per job/session."""

    def __init__(self, model_name: str, half: bool = True):
        from ultralytics import YOLO
        self.name = Path(model_name).name
        self.model = YOLO(str(model_path(model_name)), task="pose")
        self.device, cuda = _device()
        self.half = bool(half and cuda)
        self.quant = 16 if self.half else None
        self._tracker_cfg = tracker_config()

    @staticmethod
    def _to_dets(r) -> Detections:
        if r.boxes is None or len(r.boxes) == 0 or r.keypoints is None:
            return Detections.empty()
        boxes = r.boxes.xyxy.float().cpu().numpy()
        scores = r.boxes.conf.float().cpu().numpy()
        kd = r.keypoints.data.float().cpu().numpy()
        if kd.shape[-1] == 2:
            kd = np.concatenate([kd, np.ones(kd.shape[:-1] + (1,), np.float32)], -1)
        ids = (r.boxes.id.int().cpu().numpy().astype(np.int64) if r.boxes.id is not None
               else np.full(len(scores), -1, np.int64))
        return Detections(boxes, scores, kd, ids)

    def track(self, frame: np.ndarray, imgsz: int = 960, conf: float = 0.1) -> Detections:
        res = self.model.track(frame, persist=True, tracker=self._tracker_cfg, imgsz=imgsz,
                               conf=conf, iou=0.6, classes=[0], quantize=self.quant,
                               device=self.device, verbose=False)
        return self._to_dets(res[0])

    def predict(self, images: list[np.ndarray], imgsz: int = 384, conf: float = 0.12) -> list[Detections]:
        if not images:
            return []
        res = self.model.predict(images, imgsz=imgsz, conf=conf, iou=0.6, classes=[0],
                                 quantize=self.quant, device=self.device, verbose=False)
        return [self._to_dets(r) for r in res]

    def reset_tracker(self) -> None:
        pred = getattr(self.model, "predictor", None)
        if pred is not None and getattr(pred, "trackers", None):
            for t in pred.trackers:
                t.reset()
        try:
            from ultralytics.trackers.basetrack import BaseTrack
            BaseTrack.reset_id()
        except Exception:
            pass

    def warmup(self) -> None:
        self.predict([np.zeros((384, 384, 3), np.uint8)], imgsz=384)
