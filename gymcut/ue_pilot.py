"""Run a gymcut_take.py step for a take inside the open Unreal Editor."""
import sys
from pathlib import Path

from gymcut.unreal_bridge import UnrealEditor, _print_result

SCRIPT = str(Path(__file__).resolve().parent.parent / "unreal" / "GymCutUE" / "Scripts" / "gymcut_take.py")


def step(take: str, name: str) -> bool:
    code = open(SCRIPT, encoding="utf-8").read()
    with UnrealEditor() as ue:
        return _print_result(ue.run(f'TAKE = "{take}"\nSTEP = "{name}"\n' + code))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ok = all(step(sys.argv[1], s) for s in sys.argv[2:])
    sys.exit(0 if ok else 1)
