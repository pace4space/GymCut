"""Solve, export and dump poses for several takes in the open Unreal Editor, one after another.

    python -m gymcut.ue_batch Take01_RoundoffBackTuck Take03_Handspring ...
"""
import re
import sys
import time
from pathlib import Path

from gymcut.unreal_bridge import UnrealEditor

_SCRIPTS = Path(__file__).resolve().parent.parent / "unreal" / "GymCutUE" / "Scripts"
SCRIPT = str(_SCRIPTS / "gymcut_take.py")
DUMP = str(_SCRIPTS / "dump_anim_pose.py")


def run(code: str) -> str:
    with UnrealEditor(timeout=30) as ue:
        res = ue.run(code)
    out = "\n".join(o.get("output", "") for o in res.get("output", []))
    if not res.get("success"):
        out += f"\nFAILED: {res.get('result')}"
    return out


def step(take, name):
    return run(f'TAKE = "{take}"\nSTEP = "{name}"\n' + open(SCRIPT, encoding="utf-8").read())


def solve(take):
    t0 = time.time()
    st = step(take, "status")
    if "processing=True" not in st:
        print(take, "|", step(take, "setup").strip().splitlines()[-1], flush=True)
        print(take, "|", step(take, "start").strip(), flush=True)
    while True:
        time.sleep(15)
        st = step(take, "status").strip()
        if "processing=False" in st:
            break
    secs = time.time() - t0
    print(take, f"| solved in {secs / 60:.1f} min (from this script's start)", flush=True)
    print(take, "|", step(take, "export").strip().splitlines()[-1], flush=True)
    print(take, "|", run(f'TAKE = "{take}"\n' + open(DUMP, encoding="utf-8").read()).strip(), flush=True)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    for take in sys.argv[1:]:
        try:
            solve(take)
        except Exception as e:  # noqa: BLE001
            print(take, "| ERROR", e, flush=True)
    print("ALL DONE", flush=True)
