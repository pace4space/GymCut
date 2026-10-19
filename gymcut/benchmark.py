"""Compare pose models on your own clips without hand labels.

    python -m gymcut.benchmark                      # default models, up to 8 analysed clips
    python -m gymcut.benchmark --models yolo26s-pose.pt yolo26x-pose.pt --clips 4

For every model the athlete is tracked + refined exactly like the real pipeline,
then scored on ground-truth-free proxies of accuracy, measured only inside the
detected skill segments:

  coverage   share of skill frames with a confident athlete pose        (higher = better)
  jitter     keypoint noise: |2nd difference| per frame, in % of body length (lower)
  bone_cv    variation of limb lengths (bones don't stretch)            (lower)
  flips      frames where body orientation jumps > 90 deg (head/feet swap) (lower)
  agree      share of skill labels identical to the majority of models  (higher)
  fps        tracking-pass speed on this GPU                            (higher)

Results go to data/benchmarks/<time>.json and .md.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter

import cv2
import numpy as np

from . import library as lib
from .analysis import analyze_segment, detect_phases, metres_per_bl
from .config import DATA_DIR, settings
from .kinematics import clean_keypoints, compute_signals, wrap180
from .pose import PoseEngine
from .refine import refine_athlete, torso_angle
from .segmentation import activity_score, detect_segments
from .tracking import PersonTable, athlete_series, stitch, summarize_tracks

DEFAULT_MODELS = ["yolo26n-pose.pt", "yolo26s-pose.pt", "yolo26m-pose.pt", "yolo26l-pose.pt",
                  "yolo26x-pose.pt", "yolo11m-pose.pt", "yolo11x-pose.pt"]
BONES = [(5, 7), (6, 8), (7, 9), (8, 10), (11, 13), (12, 14), (13, 15), (14, 16)]


def _track(eng: PoseEngine, path, fps: float, n: int, imgsz: int):
    eng.reset_tracker()
    stride = 4 if fps > 100 else 2 if fps > 45 else 1
    from .pose import Detections
    cap = cv2.VideoCapture(str(path))
    per = []
    t0 = time.perf_counter()
    i = 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        per.append(eng.track(f, imgsz=imgsz) if i % stride == 0 else Detections.empty())
        i += 1
    cap.release()
    secs = time.perf_counter() - t0
    return per, (len(per) / stride) / max(secs, 1e-6)


def evaluate(vid: str, model: str, eng: PoseEngine) -> dict:
    s = settings.get()
    meta = lib.get(vid)
    d = lib.vdir(vid)
    fps = float(meta["fps"])
    mo = np.load(d / "motion.npz")
    W, shot = mo["W"], mo["shot"]
    per, track_fps = _track(eng, d / "work.mp4", fps, meta["n_frames"], int(s["detect_size"]))
    T = len(per)
    W, shot = W[:T], shot[:T]
    pt = PersonTable.from_lists(per)
    tracks = summarize_tracks(pt, W, T, meta["height"], fps)
    if not tracks:
        return {"error": "no person"}
    assign = stitch(pt, tracks, tracks[0].tid, W, shot, fps)
    box, kps1, _, _ = athlete_series(pt, assign, T)
    kps2, _, _ = refine_athlete(d / "work.mp4", T, box, kps1, fps, eng, crop_size=int(s["refine_size"]))

    # evaluation mask: the skill segments found by the main analysis (model independent)
    a = lib.read_json(vid, "analysis.json") or {}
    mask = np.zeros(T, bool)
    for seg in a.get("segments", []):
        mask[seg["start"]:min(T, seg["end"])] = True
    if not mask.any():
        mask[:] = True

    conf_ok = np.isfinite(kps2[:, 0, 0]) & (np.nan_to_num(kps2[:, 5:, 2]).mean(1) > 0.3)
    coverage = float(conf_ok[mask].mean())
    k = np.where((np.nan_to_num(kps2[..., 2]) >= 0.3)[..., None], kps2[..., :2], np.nan)
    sh, hp = (k[:, 5] + k[:, 6]) / 2, (k[:, 11] + k[:, 12]) / 2
    bl = np.nanmedian(np.linalg.norm(sh - hp, axis=1)) / 0.372 if np.isfinite(sh).any() else np.nan
    acc = np.linalg.norm(k[2:] - 2 * k[1:-1] + k[:-2], axis=-1)[:, 5:]          # [T-2, 12]
    m2 = mask[1:-1]
    # scale noise to 30 fps so different frame rates compare fairly
    jitter = float(np.nanmedian(acc[m2]) / bl * 100 ) if np.isfinite(acc[m2]).any() else np.nan
    cvs = []
    for a_, b_ in BONES:
        L = np.linalg.norm(k[mask, a_] - k[mask, b_], axis=1)
        L = L[np.isfinite(L)]
        if len(L) > 10:
            q = np.percentile(L, 90)
            cvs.append(np.std(np.clip(L, 0, q)) / max(np.mean(np.clip(L, 0, q)), 1e-6))
    bone_cv = float(np.mean(cvs)) if cvs else np.nan
    phi = torso_angle(kps2, 0.3)
    dphi = np.abs(wrap180(np.diff(phi)))
    flips = float(np.nanmean(dphi[mask[1:]] > 90)) if np.isfinite(dphi[mask[1:]]).any() else np.nan

    # skill labels with this model's poses
    kc, _ = clean_keypoints(kps2, fps)
    sig = compute_signals(kc, fps, W, shot)
    ph = detect_phases(sig, fps)
    act = activity_score(sig, fps)
    sc = metres_per_bl(sig, ph, fps, None)
    p = s["segment"]
    segs = detect_segments(sig, ph, act, fps, shot, preset=p["preset"], sensitivity=p["sensitivity"],
                           pre_pad=p["pre_pad"], post_pad=p["post_pad"], min_duration=p["min_duration"])
    labels = [analyze_segment(sig, ph, fps, s0, e0, sc)["label"] for s0, e0 in segs]
    return {"coverage": coverage, "jitter": jitter, "bone_cv": bone_cv, "flips": flips,
            "fps": track_fps, "labels": labels}


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="gymcut.benchmark")
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--clips", type=int, default=8, help="how many analysed clips to use")
    ap.add_argument("--ids", nargs="*", help="specific library ids")
    args = ap.parse_args(argv)

    vids = args.ids or [m["id"] for m in lib.list_all()
                        if m.get("status") == "ready" and (m.get("segments") or 0) > 0
                        and (lib.vdir(m["id"]) / "motion.npz").exists()][: args.clips]
    names = {v: lib.get(v)["name"] for v in vids}
    print(f"Benchmarking {len(args.models)} models on {len(vids)} clips\n")
    res: dict = {}
    for model in args.models:
        try:
            eng = PoseEngine(model, half=settings.get().get("half", True))
            eng.warmup()
        except Exception as e:  # noqa: BLE001
            print(f"{model}: cannot load ({e})")
            continue
        res[model] = {}
        for v in vids:
            t = time.time()
            try:
                res[model][v] = evaluate(v, model, eng)
            except Exception as e:  # noqa: BLE001
                res[model][v] = {"error": str(e)}
            r = res[model][v]
            print(f"  {model:<18} {names[v][:28]:<28} {time.time() - t:5.1f}s  "
                  + ("error: " + r["error"] if "error" in r else
                     f"cov {r['coverage']:.2f} jit {r['jitter']:.2f} bone {r['bone_cv']:.3f} flips {r['flips']:.3f} | {' | '.join(r['labels'])}"))
        del eng
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass

    # agreement with the majority label per clip
    majority = {}
    for v in vids:
        votes = Counter(" || ".join(res[m][v].get("labels", [])) for m in res if "error" not in res[m].get(v, {"error": 1}))
        majority[v] = votes.most_common(1)[0][0] if votes else ""
    rows = []
    for m in res:
        ok = [res[m][v] for v in vids if "error" not in res[m][v]]
        if not ok:
            continue
        agree = np.mean([" || ".join(res[m][v]["labels"]) == majority[v] for v in vids if "error" not in res[m][v]])
        rows.append({"model": m, "coverage": np.nanmean([r["coverage"] for r in ok]),
                     "jitter": np.nanmean([r["jitter"] for r in ok]), "bone_cv": np.nanmean([r["bone_cv"] for r in ok]),
                     "flips": np.nanmean([r["flips"] for r in ok]), "agree": float(agree),
                     "fps": np.nanmean([r["fps"] for r in ok])})
    # overall rank: average of per-metric ranks
    keys = {"coverage": -1, "jitter": 1, "bone_cv": 1, "flips": 1, "agree": -1}
    for k_, sgn in keys.items():
        order = sorted(rows, key=lambda r: sgn * r[k_])
        for i, r in enumerate(order):
            r.setdefault("ranks", []).append(i + 1)
    for r in rows:
        r["score"] = float(np.mean(r.pop("ranks")))
    rows.sort(key=lambda r: r["score"])

    lines = ["| rank | model | coverage ↑ | jitter (%BL) ↓ | bone CV ↓ | flips ↓ | label agreement ↑ | tracking fps ↑ |",
             "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows):
        lines.append(f"| {i + 1} | {r['model']} | {r['coverage']:.3f} | {r['jitter']:.2f} | {r['bone_cv']:.3f} | "
                     f"{r['flips']:.4f} | {r['agree']:.2f} | {r['fps']:.0f} |")
    table = "\n".join(lines)
    print("\n" + table)
    out = DATA_DIR / "benchmarks"
    out.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (out / f"{stamp}.json").write_text(lib.dumps({"models": res, "summary": rows, "majority": majority,
                                                   "clips": names}), "utf-8")
    (out / f"{stamp}.md").write_text("# Pose model benchmark\n\nClips: " + ", ".join(names.values())
                                     + "\n\n" + table + "\n", "utf-8")
    print(f"\nSaved {out / (stamp + '.md')}")


if __name__ == "__main__":
    main()
