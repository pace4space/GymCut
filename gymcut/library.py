"""On-disk video library: one folder per video with meta.json + derived files."""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

from .config import VIDEOS_DIR, EXPORTS_DIR

_lock = threading.RLock()
_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")


def new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def valid_id(vid: str) -> bool:
    return bool(_ID_RE.match(vid or ""))


def vdir(vid: str) -> Path:
    if not valid_id(vid):
        raise KeyError(vid)
    return VIDEOS_DIR / vid


def safe_name(s: str, maxlen: int = 60) -> str:
    s = re.sub(r"[^\w\-. ]+", "_", s or "").strip(" ._")
    return (s or "video")[:maxlen]


def _np_default(o):
    import numpy as np
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o) if np.isfinite(o) else None
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def dumps(data) -> str:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False, default=_np_default)


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(dumps(data), "utf-8")
    os.replace(tmp, path)


def create(name: str, source: str, ext: str) -> dict:
    vid = new_id()
    d = VIDEOS_DIR / vid
    d.mkdir(parents=True)
    ext = re.sub(r"[^a-zA-Z0-9]", "", ext or "mp4")[:5].lower() or "mp4"
    meta = {"id": vid, "name": name, "source": source, "source_file": f"source.{ext}",
            "created": time.time(), "status": "new", "error": None, "athlete_height_cm": None,
            "segments": 0, "labels": [], "tags": []}
    _write_json(d / "meta.json", meta)
    return meta


def get(vid: str) -> dict:
    with _lock:
        p = vdir(vid) / "meta.json"
        if not p.exists():
            raise KeyError(vid)
        return json.loads(p.read_text("utf-8"))


def update(vid: str, **patch) -> dict:
    with _lock:
        m = get(vid)
        m.update(patch)
        _write_json(vdir(vid) / "meta.json", m)
        return m


def recorded_date(m: dict) -> str:
    """Session date: from phone-style file names (VID_20211126_..., IMG_20230106...) or upload time."""
    for key in ("name", "original_name"):
        mm = re.search(r"(20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)", m.get(key) or "")
        if mm and 1 <= int(mm.group(2)) <= 12 and 1 <= int(mm.group(3)) <= 31:
            return f"{mm.group(1)}-{mm.group(2)}-{mm.group(3)}"
    return time.strftime("%Y-%m-%d", time.localtime(m.get("created", time.time())))


def list_all() -> list[dict]:
    out = []
    for d in VIDEOS_DIR.iterdir():
        if d.is_dir() and (d / "meta.json").exists():
            try:
                m = json.loads((d / "meta.json").read_text("utf-8"))
                m["has_thumb"] = (d / "thumb.jpg").exists()
                m.setdefault("athlete", None)
                m["recorded"] = recorded_date(m)
                out.append(m)
            except Exception:
                continue
    out.sort(key=lambda m: -m.get("created", 0))
    return out


def delete(vid: str) -> None:
    with _lock:
        shutil.rmtree(vdir(vid), ignore_errors=True)
        shutil.rmtree(EXPORTS_DIR / vid, ignore_errors=True)


def read_json(vid: str, name: str):
    p = vdir(vid) / name
    if not p.exists():
        return None
    return json.loads(p.read_text("utf-8"))


def write_json(vid: str, name: str, data) -> None:
    _write_json(vdir(vid) / name, data)
