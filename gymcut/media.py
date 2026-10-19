"""ffmpeg/ffprobe helpers: probing, working copies, cutting and pipe writers."""
from __future__ import annotations

import functools
import json
import subprocess
import tempfile
import threading
from fractions import Fraction
from pathlib import Path
from typing import Callable

import numpy as np

from .config import FFMPEG, FFPROBE, settings

# Never pop up console windows on Windows when spawning ffmpeg.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class MediaError(RuntimeError):
    pass


def _run(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          creationflags=_NO_WINDOW, encoding="utf-8", errors="replace")


def _parse_rate(s: str | None) -> Fraction | None:
    if not s or s in ("0/0", "0"):
        return None
    try:
        f = Fraction(s)
        return f if f > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def probe(path: str | Path) -> dict:
    """Return basic stream info. Width/height are *display* dims (rotation applied)."""
    if not FFPROBE:
        raise MediaError("ffprobe not found - install ffmpeg and add it to PATH")
    r = _run([FFPROBE, "-v", "error", "-print_format", "json", "-show_streams",
              "-show_format", str(path)], timeout=60)
    if r.returncode != 0:
        raise MediaError(f"ffprobe failed: {r.stderr.strip()[:400]}")
    data = json.loads(r.stdout or "{}")
    vstreams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"
                and s.get("disposition", {}).get("attached_pic", 0) == 0]
    if not vstreams:
        raise MediaError("no video stream found")
    v = vstreams[0]
    rotation = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(round(float(sd["rotation"])))
    if "rotate" in (v.get("tags") or {}):
        rotation = int(v["tags"]["rotate"])
    w, h = int(v["width"]), int(v["height"])
    if abs(rotation) % 180 == 90:
        w, h = h, w
    rate = _parse_rate(v.get("avg_frame_rate")) or _parse_rate(v.get("r_frame_rate")) or Fraction(30)
    duration = float(v.get("duration") or data.get("format", {}).get("duration") or 0.0)
    nb = int(v["nb_frames"]) if str(v.get("nb_frames", "")).isdigit() else int(round(duration * float(rate)))
    return {
        "width": w, "height": h, "rotation": rotation,
        "fps": float(rate), "fps_str": f"{rate.numerator}/{rate.denominator}",
        "duration": duration, "nb_frames": nb, "codec": v.get("codec_name"),
        "pix_fmt": v.get("pix_fmt"),
        "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
    }


@functools.lru_cache(maxsize=1)
def nvenc_available() -> bool:
    if not FFMPEG:
        return False
    r = _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
              "color=black:s=256x256:d=0.2", "-c:v", "h264_nvenc", "-f", "null", "-"], timeout=30)
    return r.returncode == 0


def encoder_args(quality: str = "high") -> list[str]:
    """H.264 encoder arguments (browser-playable). quality: high | medium | fast."""
    choice = settings.get().get("encoder", "auto")
    use_nvenc = choice == "nvenc" or (choice == "auto" and nvenc_available())
    if use_nvenc:
        cq = {"high": "19", "medium": "23", "fast": "26"}[quality]
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr",
                "-cq", cq, "-b:v", "0", "-pix_fmt", "yuv420p"]
    crf = {"high": "18", "medium": "22", "fast": "26"}[quality]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", crf, "-pix_fmt", "yuv420p"]


def even(x: float) -> int:
    return max(2, int(round(x / 2.0)) * 2)


def working_size(w: int, h: int, max_side: int) -> tuple[int, int]:
    s = min(1.0, max_side / max(w, h))
    return even(w * s), even(h * s)


def _run_with_progress(cmd: list[str], duration: float, progress: Callable[[float], None] | None):
    """Run ffmpeg with -progress pipe:1 and report fraction done."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            creationflags=_NO_WINDOW, encoding="utf-8", errors="replace")
    err_chunks: list[str] = []
    t = threading.Thread(target=lambda: err_chunks.append(proc.stderr.read()), daemon=True)
    t.start()
    for line in proc.stdout:
        if progress and line.startswith("out_time_us=") and duration > 0:
            try:
                us = int(line.split("=", 1)[1])
                progress(min(1.0, us / 1e6 / duration))
            except ValueError:
                pass
    proc.wait()
    t.join(timeout=5)
    if proc.returncode != 0:
        raise MediaError("ffmpeg failed: " + "".join(err_chunks).strip()[-800:])


def make_working_copy(src: Path, dst: Path, info: dict, progress=None) -> dict:
    """Transcode to constant-frame-rate H.264 so frame index == round(t * fps) exactly.

    Phones record variable frame rate and often HEVC; browsers and frame-accurate
    analysis both need CFR H.264. Keyframe every second keeps seeking snappy.
    """
    max_side = int(settings.get().get("work_max_side", 1920))
    w, h = working_size(info["width"], info["height"], max_side)
    rate = Fraction(info["fps_str"]).limit_denominator(1001)
    if rate > 240:
        rate = Fraction(240)
    gop = max(1, int(round(float(rate))))
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
           "-map", "0:v:0", "-map", "0:a:0?",
           "-vf", f"scale={w}:{h}:flags=lanczos,setsar=1",
           "-fps_mode", "cfr", "-r", f"{rate.numerator}/{rate.denominator}",
           *encoder_args("high"), "-g", str(gop),
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
           "-progress", "pipe:1", "-nostats", str(dst)]
    _run_with_progress(cmd, info["duration"], progress)
    return probe(dst)


def thumbnail(src: Path, t: float, dst: Path, width: int = 480) -> None:
    r = _run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}",
              "-i", str(src), "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(dst)],
             timeout=60)
    if r.returncode != 0:
        raise MediaError(r.stderr[-400:])


def cut_clip(src: Path, start: float, end: float, dst: Path, speed: float = 1.0,
             audio: bool = True, progress=None) -> None:
    """Frame-accurate re-encoded cut, optionally slowed down/sped up."""
    dur = max(0.05, end - start)
    vf = []
    if abs(speed - 1.0) > 1e-3:
        vf.append(f"setpts=PTS/{speed:.6f}")
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.4f}",
           "-i", str(src), "-t", f"{dur:.4f}", "-map", "0:v:0"]
    keep_audio = audio and 0.5 <= speed <= 2.0
    if keep_audio:
        cmd += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "128k"]
        if abs(speed - 1.0) > 1e-3:
            cmd += ["-filter:a", f"atempo={speed:.4f}"]
    else:
        cmd += ["-an"]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += [*encoder_args("high"), "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(dst)]
    _run_with_progress(cmd, dur / speed, progress)


class FrameWriter:
    """Pipe raw BGR frames into ffmpeg -> H.264 mp4."""

    def __init__(self, path: Path, width: int, height: int, fps: float, quality: str = "high",
                 audio_from: tuple[Path, float, float] | None = None):
        self.path = Path(path)
        self.size = (int(width), int(height))
        self._err = tempfile.TemporaryFile()
        cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
               "-r", f"{fps:.6f}", "-i", "-"]
        if audio_from:
            a_src, a_start, a_dur = audio_from
            cmd += ["-ss", f"{a_start:.4f}", "-t", f"{a_dur:.4f}", "-i", str(a_src),
                    "-map", "0:v:0", "-map", "1:a:0?", "-c:a", "aac", "-b:a", "128k", "-shortest"]
        cmd += [*encoder_args(quality), "-movflags", "+faststart", str(self.path)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=self._err, creationflags=_NO_WINDOW)

    def write(self, frame: np.ndarray) -> None:
        if (frame.shape[1], frame.shape[0]) != self.size:
            import cv2
            frame = cv2.resize(frame, self.size, interpolation=cv2.INTER_AREA)
        self.proc.stdin.write(np.ascontiguousarray(frame).tobytes())

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        rc = self.proc.wait()
        if rc != 0:
            self._err.seek(0)
            msg = self._err.read().decode("utf-8", "replace")[-800:]
            self._err.close()
            raise MediaError(f"ffmpeg writer failed: {msg}")
        self._err.close()


def write_jpeg_sequence(frames: list[tuple[float, bytes]], path: Path, fps: float | None = None) -> float:
    """Encode timestamped JPEG frames (live capture) to a CFR mp4.

    Live frames arrive at whatever rate inference keeps up with, so we resample
    onto a constant grid (repeating/dropping frames) to keep real-time speed.
    Returns the fps used.
    """
    if not frames:
        raise MediaError("no frames")
    ts = np.array([f[0] for f in frames], dtype=np.float64)
    span = max(1e-3, ts[-1] - ts[0])
    if fps is None:
        measured = (len(frames) - 1) / span if len(frames) > 1 else 15.0
        fps = float(min(30, max(10, round(measured))))
    n_out = max(1, int(round(span * fps)) + 1)
    grid = ts[0] + np.arange(n_out) / fps
    idx = np.clip(np.searchsorted(ts, grid, side="right") - 1, 0, len(frames) - 1)
    import cv2
    first = cv2.imdecode(np.frombuffer(frames[0][1], np.uint8), cv2.IMREAD_COLOR)
    h, w = first.shape[:2]
    err = tempfile.TemporaryFile()
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "image2pipe",
           "-c:v", "mjpeg", "-framerate", f"{fps:.3f}", "-i", "-",
           "-vf", f"scale={even(w)}:{even(h)}", *encoder_args("high"),
           "-movflags", "+faststart", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err,
                            creationflags=_NO_WINDOW)
    try:
        for i in idx:
            proc.stdin.write(frames[i][1])
    finally:
        proc.stdin.close()
        rc = proc.wait()
    if rc != 0:
        err.seek(0)
        raise MediaError("clip encode failed: " + err.read().decode("utf-8", "replace")[-600:])
    err.close()
    return fps
