"""Paths, external tools and persistent user settings."""
from __future__ import annotations

import copy
import json
import os
import shutil
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
DATA_DIR = Path(os.environ.get("GYMCUT_DATA", ROOT / "data")).resolve()
VIDEOS_DIR = DATA_DIR / "videos"
EXPORTS_DIR = DATA_DIR / "exports"
CERT_DIR = DATA_DIR / "certs"
MODELS_DIR = Path(os.environ.get("GYMCUT_MODELS", ROOT / "models")).resolve()
SETTINGS_PATH = DATA_DIR / "settings.json"

for _d in (VIDEOS_DIR, EXPORTS_DIR, CERT_DIR, MODELS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _find_tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for base in (r"C:\Program Files\ffmpeg\bin", r"C:\ffmpeg\bin"):
        cand = Path(base) / f"{name}.exe"
        if cand.exists():
            return str(cand)
    if name == "ffmpeg":
        try:  # bundled binary from the imageio-ffmpeg wheel, if installed
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None
    return None


FFMPEG = os.environ.get("GYMCUT_FFMPEG") or _find_tool("ffmpeg")
FFPROBE = os.environ.get("GYMCUT_FFPROBE") or _find_tool("ffprobe")

DEFAULTS: dict = {
    # Offline analysis
    "pose_model": "yolo26x-pose.pt",
    "detect_size": 960,          # inference size for the full-frame tracking pass
    "refine": True,              # rotation-aware crop refinement of the athlete
    "refine_size": 384,          # crop inference size
    "half": True,                # FP16 on CUDA
    "work_max_side": 1920,       # working copy is scaled so its longer side <= this
    "encoder": "auto",           # auto | nvenc | x264
    "athlete_height_cm": None,   # default athlete height for metric conversions
    # Segmentation (auto nip-n-cut)
    "segment": {
        "preset": "skills",      # skills | passes | routine | activity
        "sensitivity": 0.5,      # 0 = only big skills, 1 = any movement
        "pre_pad": 0.5,
        "post_pad": 0.9,
        "min_duration": 0.8,
    },
    # Automatically render a focus video (zoomed on the athlete, trimmed) for every new attempt
    "auto_focus": False,
    # Unreal Engine 3D replays (MetaHuman markerless body solve)
    "ue_project": str(ROOT / "unreal" / "GymCutUE" / "GymCutUE.uproject"),
    # Live capture
    "live": {
        "model": "yolo26s-pose.pt",
        "detect_size": 640,
        "sensitivity": 0.5,
        "pre_roll": 2.0,
        "post_roll": 1.5,
        "min_clip": 1.0,
        "max_clip": 90.0,
        "auto_analyze": True,
        "jpeg_quality": 80,
        "lift3d_model": "heavy",       # MediaPipe pose landmarker for "3D in Unreal": heavy | full
    },
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    """Thread-safe JSON settings store merged over DEFAULTS."""

    def __init__(self, path: Path = SETTINGS_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._data = copy.deepcopy(DEFAULTS)
        if path.exists():
            try:
                self._data = _deep_merge(DEFAULTS, json.loads(path.read_text("utf-8")))
            except Exception:
                pass

    def get(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def update(self, patch: dict) -> dict:
        with self._lock:
            self._data = _deep_merge(self._data, patch)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2), "utf-8")
            os.replace(tmp, self.path)
            return copy.deepcopy(self._data)


settings = Settings()
