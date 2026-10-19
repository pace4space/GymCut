"""Athlete selection among tracked people and stitching of broken track fragments.

Trackers lose people during flips (motion blur, drastic box shape changes), so
the athlete usually ends up as several track ids. We pick the most
"gymnastic" track (big, present, moving a lot, often inverted) and then chain
compatible fragments before/after it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .camera_motion import apply_transform


@dataclass
class PersonTable:
    """All tracked detections of a video, flattened."""
    frame: np.ndarray  # [M] int
    tid: np.ndarray    # [M] int
    box: np.ndarray    # [M, 4]
    score: np.ndarray  # [M]
    kps: np.ndarray    # [M, 17, 3]

    @staticmethod
    def from_lists(per_frame: list) -> "PersonTable":
        fr, ti, bo, sc, kp = [], [], [], [], []
        for f, d in enumerate(per_frame):
            for j in range(len(d)):
                fr.append(f); ti.append(int(d.ids[j])); bo.append(d.boxes[j])
                sc.append(float(d.scores[j])); kp.append(d.kps[j])
        if not fr:
            return PersonTable(np.zeros(0, int), np.zeros(0, int), np.zeros((0, 4)),
                               np.zeros(0), np.zeros((0, 17, 3)))
        return PersonTable(np.array(fr), np.array(ti), np.array(bo, np.float32),
                           np.array(sc, np.float32), np.array(kp, np.float32))

    def save(self, path) -> None:
        np.savez_compressed(path, frame=self.frame, tid=self.tid, box=self.box,
                            score=self.score, kps=self.kps)

    @staticmethod
    def load(path) -> "PersonTable":
        z = np.load(path)
        return PersonTable(z["frame"], z["tid"], z["box"], z["score"], z["kps"])


@dataclass
class TrackInfo:
    tid: int
    idx: np.ndarray          # indices into PersonTable, sorted by frame
    start: int
    end: int
    n: int
    med_h: float
    score: float = 0.0
    stats: dict = field(default_factory=dict)


def _inverted_fraction(kps: np.ndarray) -> float:
    sh = (kps[:, 5, :2] + kps[:, 6, :2]) / 2
    hp = (kps[:, 11, :2] + kps[:, 12, :2]) / 2
    ok = (np.minimum(kps[:, 5:7, 2].min(1), kps[:, 11:13, 2].min(1)) > 0.3)
    if ok.sum() == 0:
        return 0.0
    return float(np.mean(sh[ok, 1] > hp[ok, 1]))


def summarize_tracks(pt: PersonTable, W: np.ndarray, n_frames: int, frame_h: int, fps: float) -> list[TrackInfo]:
    out = []
    for tid in np.unique(pt.tid[pt.tid >= 0]):
        idx = np.where(pt.tid == tid)[0]
        idx = idx[np.argsort(pt.frame[idx])]
        f = pt.frame[idx]
        b = pt.box[idx].astype(np.float64)
        h = b[:, 3] - b[:, 1]
        w = b[:, 2] - b[:, 0]
        c = np.stack([(b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2], 1)
        cw = apply_transform(W[f], c[:, None, :])[:, 0]
        med_h = float(np.median(h))
        travel = float(np.sum(np.linalg.norm(np.diff(cw, axis=0), axis=1))) / max(med_h, 1) if len(f) > 1 else 0
        spread = float(np.ptp(cw[:, 0]) + np.ptp(cw[:, 1])) / max(med_h, 1)
        aspect_var = float(np.std(w / np.maximum(h, 1)))
        inv = _inverted_fraction(pt.kps[idx])
        presence = len(f) / max(1, n_frames)
        size = med_h / max(1, frame_h)
        dur_s = len(f) / fps
        activity = 0.25 + min(spread, 6) * 0.35 + min(travel / max(dur_s, 0.5), 6) * 0.15 \
            + aspect_var * 4 + inv * 6
        # Gymnastics evidence (shape change, inversion, travel) dominates; size and screen
        # time matter less, so a coach walking close to the camera doesn't win.
        score = (presence ** 0.3) * (size ** 0.3) * activity * float(np.mean(pt.score[idx]))
        out.append(TrackInfo(int(tid), idx, int(f[0]), int(f[-1]), len(f), med_h, score,
                             {"spread": round(spread, 2), "travel": round(travel, 2),
                              "aspect_var": round(aspect_var, 3), "inverted": round(inv, 3),
                              "presence": round(presence, 3), "size": round(size, 3)}))
    out.sort(key=lambda t: -t.score)
    return out


def _end_state(pt, tr, W, at_end=True, k=5):
    sel = tr.idx[-k:] if at_end else tr.idx[:k]
    f = pt.frame[sel]
    b = pt.box[sel].astype(np.float64)
    c = np.stack([(b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2], 1)
    cw = apply_transform(W[f], c[:, None, :])[:, 0]
    h = float(np.median(b[:, 3] - b[:, 1]))
    if len(f) >= 2 and f[-1] != f[0]:
        v = (cw[-1] - cw[0]) / (f[-1] - f[0])
    else:
        v = np.zeros(2)
    pos = cw[-1] if at_end else cw[0]
    return pos, v, h, int(f[-1] if at_end else f[0])


def _center_w(pt: PersonTable, i: int, W: np.ndarray) -> np.ndarray:
    b = pt.box[i].astype(np.float64)
    c = np.array([[(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]])
    return apply_transform(W[pt.frame[i]][None], c[None])[0, 0]


def stitch(pt: PersonTable, tracks: list[TrackInfo], main_tid: int, W: np.ndarray, shot: np.ndarray,
           fps: float, max_gap_s: float = 1.2) -> dict[int, int]:
    """Grow the athlete's identity from the main track by absorbing runs of other tracks.

    Trackers fragment gymnasts (flips, blur) and sometimes resurrect an older id
    after a skill, so fragments can interleave in time. A run of another track
    is absorbed if (1) that track never co-exists with the athlete in the same
    frames (co-existing = a different person, e.g. a coach) and (2) it continues
    the athlete's trajectory at its boundaries. Returns {frame: detection index}.
    """
    by_id = {t.tid: t for t in tracks}
    if main_tid not in by_id:
        return {}
    assign: dict[int, int] = {}
    for i in by_id[main_tid].idx:
        assign[int(pt.frame[i])] = int(i)
    max_gap = int(round(max_gap_s * fps))

    def neighbour(f0: int, direction: int):
        keys = sorted(assign)
        if direction < 0:
            cands = [k for k in keys if k < f0]
            return cands[-1] if cands else None
        cands = [k for k in keys if k > f0]
        return cands[0] if cands else None

    def velocity(f: int):
        ks = sorted(k for k in assign if abs(k - f) <= max(2, int(0.2 * fps)))
        if len(ks) < 2:
            return np.zeros(2)
        a, b = ks[0], ks[-1]
        return (_center_w(pt, assign[b], W) - _center_w(pt, assign[a], W)) / max(1, b - a)

    def continuous(f_known: int, i_new: int) -> bool:
        i_known = assign[f_known]
        f_new = int(pt.frame[i_new])
        gap = f_new - f_known
        if abs(gap) > max_gap or shot[f_new] != shot[f_known]:
            return False
        h_known = float(pt.box[i_known, 3] - pt.box[i_known, 1])
        h_new = float(pt.box[i_new, 3] - pt.box[i_new, 1])
        if not (0.35 < h_new / max(h_known, 1) < 2.8):
            return False
        pred = _center_w(pt, i_known, W) + velocity(f_known) * gap
        d = np.linalg.norm(_center_w(pt, i_new, W) - pred) / max(h_known, 1)
        return d < 1.0 + 0.03 * abs(gap)

    changed = True
    rounds = 0
    while changed and rounds < 12:
        changed = False
        rounds += 1
        for t in tracks:
            if t.tid == main_tid:
                continue
            frames = pt.frame[t.idx]
            co = np.isin(frames, list(assign.keys())).sum()
            if co > 3 and co > 0.1 * t.n:
                continue  # seen at the same time as the athlete -> someone else
            free = [(int(f), int(i)) for f, i in zip(frames, t.idx) if int(f) not in assign]
            if not free:
                continue
            # split into runs of near-consecutive frames
            run_list, cur = [], [free[0]]
            for fi in free[1:]:
                if fi[0] - cur[-1][0] <= 3:
                    cur.append(fi)
                else:
                    run_list.append(cur)
                    cur = [fi]
            run_list.append(cur)
            for run in run_list:
                f0, i0 = run[0]
                f1, i1 = run[-1]
                p = neighbour(f0, -1)
                q = neighbour(f1, +1)
                ok_p = continuous(p, i0) if p is not None and f0 - p <= max_gap else None
                ok_q = continuous(q, i1) if q is not None and q - f1 <= max_gap else None
                if (ok_p or ok_q) and ok_p is not False and ok_q is not False:
                    for f, i in run:
                        assign[f] = i
                    changed = True
    return assign


def athlete_series(pt: PersonTable, assign: dict[int, int], n_frames: int):
    """Per-frame athlete box/kps/score from the stitched assignment (NaN where absent)."""
    box = np.full((n_frames, 4), np.nan)
    kps = np.full((n_frames, 17, 3), np.nan)
    score = np.zeros(n_frames)
    tid = np.full(n_frames, -1)
    for f, i in assign.items():
        if 0 <= f < n_frames:
            score[f] = pt.score[i]
            box[f] = pt.box[i]
            kps[f] = pt.kps[i]
            tid[f] = pt.tid[i]
    return box, kps, score, tid


def tracks_payload(pt: PersonTable, tracks: list[TrackInfo], chain: list[int], max_tracks: int = 40) -> dict:
    """Compact per-track box lists for the UI athlete picker."""
    out = []
    for t in tracks[:max_tracks]:
        f = pt.frame[t.idx]
        b = np.round(pt.box[t.idx], 1)
        out.append({"id": t.tid, "start": t.start, "end": t.end, "n": t.n,
                    "score": round(t.score, 4), "athlete": t.tid in chain, "stats": t.stats,
                    "frames": f.tolist(), "boxes": b.tolist()})
    return {"tracks": out, "chain": chain}
