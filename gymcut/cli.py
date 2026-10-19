"""Command line: python -m gymcut.cli analyze VIDEO [...]  |  list  |  reanalyze ID"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path


class ConsoleJob:
    def __init__(self):
        self._last = ("", -1)

    def update(self, stage=None, progress=None, message=None):
        key = (stage, int((progress or 0) * 20))
        if key != self._last:
            self._last = key
            print(f"  [{(progress or 0) * 100:5.1f}%] {stage or ''} {message or ''}", flush=True)


def _summary(vid: str) -> None:
    from . import library as lib
    a = lib.read_json(vid, "analysis.json")
    if not a:
        return
    print(f"\n{vid}: {a['n_frames']} frames @ {a['fps']:.2f} fps · scale: {a['scale']}")
    print("phases:", a["phases"][:400])
    for s in a["segments"]:
        m = s["metrics"]
        print(f"\n  #{s['id']} {s['t0']:.2f}-{s['t1']:.2f}s  {s['label']}")
        print(f"     metrics: {json.dumps(m)}")
        for el in s["elements"]:
            keys = ("type", "name", "t0", "t1", "rotation_deg", "flight_time", "height_est_m", "shape",
                    "split_max", "knee_min", "elbow_min", "support_time")
            print("     el:", {k: el.get(k) for k in keys if el.get(k) is not None})
        if s.get("landing"):
            print("     landing:", s["landing"])
        for n in s["notes"]:
            print(f"     [{n['level']}] {n['t']}s {n['text']}")


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="gymcut")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze", help="import and analyse video files")
    a.add_argument("videos", nargs="+")
    r = sub.add_parser("reanalyze", help="re-run athlete + analysis for a library id")
    r.add_argument("id")
    r.add_argument("--from", dest="stage", default="athlete", choices=["prepare", "track", "athlete", "analysis"])
    r.add_argument("--athlete", type=int, default=None)
    sub.add_parser("list")
    s = sub.add_parser("show")
    s.add_argument("id")
    args = ap.parse_args(argv)

    from . import library as lib
    from .pipeline import build_analysis, job_process

    if args.cmd == "list":
        for m in lib.list_all():
            print(m["id"], m["status"], m.get("name"), m.get("segments"), m.get("labels"))
        return
    if args.cmd == "show":
        _summary(args.id)
        return
    if args.cmd == "reanalyze":
        t = time.time()
        if args.stage == "analysis":
            build_analysis(args.id)
        else:
            job_process(ConsoleJob(), args.id, from_stage=args.stage, forced_tid=args.athlete)
        print(f"done in {time.time() - t:.1f}s")
        _summary(args.id)
        return
    for v in args.videos:
        p = Path(v)
        meta = lib.create(p.stem, "upload", p.suffix.lstrip("."))
        shutil.copy2(p, lib.vdir(meta["id"]) / meta["source_file"])
        print(f"{p.name} -> {meta['id']}")
        t = time.time()
        job_process(ConsoleJob(), meta["id"])
        print(f"done in {time.time() - t:.1f}s")
        _summary(meta["id"])


if __name__ == "__main__":
    sys.exit(main())
