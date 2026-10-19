"""Internet access through a Cloudflare quick tunnel (no router setup, no account).

`cloudflared tunnel --url http://127.0.0.1:<port>` gives a random https://<words>.trycloudflare.com
address that forwards to GymCut, with HTTPS (phones need it for the camera) and WebSockets.
The address changes every time the tunnel starts. All tunnel traffic needs the access key
(see server.guard). While the tunnel runs, the PC is kept awake so the phone can reach it.
"""
from __future__ import annotations

import ctypes
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001

STATE = {"state": "off", "url": None, "error": None, "started": None}
_proc: subprocess.Popen | None = None
_lock = threading.Lock()


def cloudflared_path() -> str | None:
    p = shutil.which("cloudflared")
    if p:
        return p
    for c in (r"C:\Program Files (x86)\cloudflared\cloudflared.exe", r"C:\Program Files\cloudflared\cloudflared.exe"):
        if Path(c).exists():
            return c
    return None


def _keep_awake(on: bool) -> None:
    """Ask Windows not to sleep while the tunnel is up (like a video player does); released on stop."""
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception:  # noqa: BLE001 - not Windows
        pass


def _run(port: int) -> None:
    global _proc
    exe = cloudflared_path()
    if not exe:
        STATE.update(state="error", error="cloudflared is not installed (winget install Cloudflare.cloudflared)")
        return
    _keep_awake(True)
    try:
        while STATE["state"] in ("starting", "on"):
            _proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
            for line in _proc.stdout:
                m = _URL.search(line)
                if m and STATE["url"] != m.group(0):
                    STATE.update(state="on", url=m.group(0), error=None, started=time.time())
                    print(f"  Internet: {m.group(0)}  (Cloudflare quick tunnel)")
            _proc.wait()
            if STATE["state"] == "off":
                break
            # cloudflared exited on its own (network change...): restart; the address will change
            STATE.update(state="starting", url=None, error="tunnel dropped - reconnecting")
            time.sleep(3)
    finally:
        _keep_awake(False)


def start(port: int) -> dict:
    with _lock:
        if STATE["state"] in ("starting", "on"):
            return status()
        STATE.update(state="starting", url=None, error=None)
        threading.Thread(target=_run, args=(port,), daemon=True).start()
    return status()


def stop() -> dict:
    global _proc
    with _lock:
        STATE.update(state="off", url=None)
        if _proc and _proc.poll() is None:
            _proc.terminate()
            try:
                _proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _proc.kill()
        _proc = None
    return status()


def status() -> dict:
    return {**STATE, "installed": bool(cloudflared_path())}
