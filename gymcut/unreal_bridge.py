"""Drive a running Unreal Editor from GymCut via Epic's Python remote-execution protocol.

The project (unreal/GymCutUE in this repo) enables remote execution bound to 127.0.0.1, so this
only reaches editors on this PC. Usage:

    python -m gymcut.unreal_bridge ping
    python -m gymcut.unreal_bridge run "import unreal; print(unreal.SystemLibrary.get_engine_version())"
    python -m gymcut.unreal_bridge file unreal\\GymCutUE\\Scripts\\some_script.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

UE_ROOT = Path(os.environ.get("GYMCUT_UE_ROOT", r"F:\Gamez\Epic\UE_5.8"))
_RE_PATH = UE_ROOT / "Engine/Plugins/Experimental/PythonScriptPlugin/Content/Python/remote_execution.py"


def _load_remote_execution():
    spec = importlib.util.spec_from_file_location("ue_remote_execution", _RE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class UnrealEditor:
    def __init__(self, timeout: float = 8.0):
        re = _load_remote_execution()
        cfg = re.RemoteExecutionConfig()
        cfg.multicast_bind_address = "127.0.0.1"
        cfg.multicast_ttl = 0
        self._re = re
        self.remote = re.RemoteExecution(cfg)
        self.remote.start()
        t0 = time.time()
        while not self.remote.remote_nodes and time.time() - t0 < timeout:
            time.sleep(0.2)
        if not self.remote.remote_nodes:
            self.remote.stop()
            raise ConnectionError("No Unreal Editor found. Is GymCutUE open (and finished loading)?")
        self.node = self.remote.remote_nodes[0]
        self.remote.open_command_connection(self.node["node_id"])

    def run(self, code: str, as_file: bool = True) -> dict:
        mode = self._re.MODE_EXEC_FILE if as_file else self._re.MODE_EXEC_STATEMENT
        return self.remote.run_command(code, unattended=True, exec_mode=mode)

    def close(self):
        try:
            self.remote.close_command_connection()
        finally:
            self.remote.stop()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _print_result(res: dict) -> bool:
    for o in res.get("output", []):
        print(o.get("output", "").rstrip())
    if not res.get("success"):
        print("FAILED:", res.get("result"))
    return bool(res.get("success"))


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cmd = argv[0] if argv else "ping"
    with UnrealEditor() as ue:
        if cmd == "ping":
            ok = _print_result(ue.run("import unreal\nprint('Unreal', unreal.SystemLibrary.get_engine_version(), '| project', unreal.Paths.get_project_file_path())"))
        elif cmd == "run":
            ok = _print_result(ue.run(argv[1]))
        elif cmd == "file":
            ok = _print_result(ue.run(Path(argv[1]).read_text("utf-8")))
        else:
            raise SystemExit(f"unknown command {cmd}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
