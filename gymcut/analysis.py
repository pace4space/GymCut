"""Gymnastics-specific phase detection, element recognition and metrics.

Everything here is 2D (single camera), so values are image-plane estimates:
best accuracy when the camera is side-on to the direction of travel. Flight
time is the most robust quantity (independent of camera geometry) and gives
jump height physically via h = g*T^2/8.
"""
from __future__ import annotations

import numpy as np

from .kinematics import BL_PER_HEIGHT, G, runs, wrap180

NONE, FEET, HANDS, AIR = 0, 1, 2, 3
PHASE_CHARS = {NONE: "-", FEET: "F", HANDS: "H", AIR: "A"}


def _f(x, nd=2):
    """JSON-friendly float rounding (None for NaN)."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, nd) if np.isfinite(x) else None


def _nanmin(x):
    x = np.asarray(x, float)
    return float(np.nanmin(x)) if np.isfinite(x).any() else np.nan


def _nanmax(x):
    x = np.asarray(x, float)
    return float(np.nanmax(x)) if np.isfinite(x).any() else np.nan


def _nanmed(x):
    x = np.asarray(x, float)
    return float(np.nanmedian(x)) if np.isfinite(x).any() else np.nan


# ----------------------------------------------------------------------------- phases
def detect_phases(sig: dict, fps: float) -> np.ndarray:
    T = len(sig["valid"])
    ph = np.where(sig["valid"], FEET, NONE).astype(np.int8)
    elev = np.nan_to_num(sig["elev"], nan=0.0)
    # hysteresis on elevation of the lowest body point
    air = np.zeros(T, bool)
    on, off = 0.09, 0.045
    state = False
    contact = sig.get("contact", np.zeros(T, bool))
    for t in range(T):
        if not sig["valid"][t] or contact[t]:
            state = False
        elif not state and elev[t] > on:
            state = True
        elif state and elev[t] < off:
            state = False
        air[t] = state
    # extend each air run backwards/forwards to the off threshold (takeoff/touchdown frames)
    min_len = max(2, int(round(0.06 * fps)))
    for s, e in runs(air):
        if e - s < min_len:
            air[s:e] = False
            continue
        while s > 0 and sig["valid"][s - 1] and elev[s - 1] > off:
            s -= 1
            air[s] = True
    ph[air] = AIR
    inv = np.nan_to_num(sig["inversion"], nan=0.0)
    hands = (~air) & sig["valid"] & sig["hands_down"] & (inv > 60)
    for s, e in runs(hands):
        if e - s < max(2, int(round(0.08 * fps))):   # real hand contacts last > 80 ms
            hands[s:e] = False
    ph[hands] = HANDS
    # Physical constraints: nobody stands on their feet while upside down, and a brief
    # "flight" blip in the middle of an inverted hand support is keypoint noise.
    inverted = sig["valid"] & (inv > 120)
    ph[inverted & (ph == FEET)] = HANDS
    blip = max(2, int(round(0.12 * fps)))
    for s, e in runs(ph == AIR):
        if e - s <= blip and s > 0 and e < T and ph[s - 1] == HANDS and ph[e] == HANDS \
                and np.nanmean(inv[s:e]) > 120:
            ph[s:e] = HANDS
    return ph


# ----------------------------------------------------------------------------- scale
def metres_per_bl(sig: dict, phases: np.ndarray, fps: float, athlete_height_cm: float | None) -> dict:
    if athlete_height_cm:
        m = athlete_height_cm / 100.0 * BL_PER_HEIGHT
        return {"m_per_bl": m, "source": "athlete height", "height_m": athlete_height_cm / 100.0}
    est = []
    for s, e in runs(phases == AIR):
        Tf = (e - s) / fps
        if Tf < 0.3:
            continue
        com_h = sig["com_h"][max(0, s - 1): e + 1]
        if not np.isfinite(com_h).sum() > 3:
            continue
        rise = _nanmax(com_h) - np.nanmean([com_h[0], com_h[-1]])
        if rise > 0.05:
            est.append((G * Tf ** 2 / 8.0) / rise)
    if est:
        m = float(np.median(est))
        h = m / BL_PER_HEIGHT
        if 1.0 < h < 2.15:
            return {"m_per_bl": m, "source": "flight physics", "height_m": h}
    return {"m_per_bl": None, "source": None, "height_m": None}


# ----------------------------------------------------------------------------- elements
def _rotation(sig, s, e):
    """Signed rotation (deg) between frame s and e using the unwrapped torso angle."""
    pu = sig["phi_u"]
    a = pu[s] if np.isfinite(pu[s]) else np.nan
    b = pu[e] if np.isfinite(pu[e]) else np.nan
    if not (np.isfinite(a) and np.isfinite(b)):
        seg = pu[s:e + 1]
        ok = np.where(np.isfinite(seg))[0]
        if len(ok) < 2:
            return 0.0
        a, b = seg[ok[0]], seg[ok[-1]]
    return float(b - a)


def _dir_word(direction: str | None) -> str:
    return {"forward": "Front", "backward": "Back"}.get(direction or "", "")


def _facing_score(sig, lo: int, hi: int) -> float:
    """Signed facing in the body frame from head + flexed-knee cues (positive = body-right)."""
    lo, hi = max(0, lo), max(lo + 1, hi)
    f_head = _nanmed(sig["facing"][lo:hi])
    fk = sig.get("facing_knee")
    f_knee = _nanmed(fk[lo:hi]) if fk is not None else np.nan
    score = 0.0
    if np.isfinite(f_head):
        score += 0.6 * np.clip(f_head / 0.15, -1, 1)
    if np.isfinite(f_knee):
        score += 1.2 * np.clip(f_knee / 0.08, -1, 1)
    return float(score)


def _turned(sig, s: int, e: int, fps: float) -> bool:
    """Facing reversed across an element (half turn, as in a round-off)."""
    before = _facing_score(sig, s - int(0.5 * fps), s)
    after = _facing_score(sig, e, e + int(0.4 * fps))
    # facing is measured in the body frame; after a 360 rotation the frame is comparable again
    return abs(before) >= 0.3 and abs(after) >= 0.3 and np.sign(before) != np.sign(after)


def _direction(sig, s, rot, fps: float = 30.0):
    """forward/backward: compare the athlete's facing before takeoff with the rotation sense.

    Facing combines two cues in the body frame: the head (nose ahead of the ears)
    and - more robust - the direction flexed knees point (anatomically forward).
    Gymnasts often throw the head back at takeoff, so the knee cue gets more weight.
    """
    if abs(rot) < 90:
        return None
    score = _facing_score(sig, s - int(0.45 * fps), s + max(1, int(0.08 * fps)))
    if abs(score) < 0.25:
        return None
    return "forward" if np.sign(rot) == np.sign(score) else "backward"


def _shape(sig, s, e):
    a = sig["angles"]
    mid = slice(s + int((e - s) * 0.2), max(s + int((e - s) * 0.8), s + 1))
    knee = np.nanmean([_nanmed(a["knee_l"][mid]), _nanmed(a["knee_r"][mid])])
    hip = np.nanmean([_nanmed(a["hip_l"][mid]), _nanmed(a["hip_r"][mid])])
    knee_min = np.nanmin([_nanmin(a["knee_l"][s:e]), _nanmin(a["knee_r"][s:e])])
    hip_min = np.nanmin([_nanmin(a["hip_l"][s:e]), _nanmin(a["hip_r"][s:e])])
    split = _nanmax(a["split"][s:e])
    if not np.isfinite(knee) or not np.isfinite(hip):
        shape = None
    elif knee_min < 115 and hip_min < 115:
        shape = "tuck"
    elif hip_min < 125 and knee >= 140:
        shape = "pike"
    elif knee >= 145 and hip >= 145:
        shape = "straight"
    else:
        shape = "open"
    return shape, knee, hip, knee_min, hip_min, split


def _twists(sig, s, e):
    w = sig["sh_w"][s:e]
    sgn, flips = 0, 0
    for x in w:
        if not np.isfinite(x):
            continue
        if x > 0.3:
            if sgn == -1:
                flips += 1
            sgn = 1
        elif x < -0.3:
            if sgn == 1:
                flips += 1
            sgn = -1
    return flips / 2.0


def _flight_metrics(sig, s, e, fps):
    Tf = (e - s) / fps
    com_h = sig["com_h"][max(0, s - 1): e + 1]
    rise = _nanmax(com_h) - np.nanmean([com_h[0], com_h[-1]]) if np.isfinite(com_h).any() else np.nan
    return {
        "flight_time": _f(Tf, 3),
        "height_est_m": _f(G * Tf ** 2 / 8.0, 2),
        "peak_elev_bl": _f(_nanmax(sig["elev"][s:e])),
        "com_rise_bl": _f(rise),
    }


def find_elements(sig: dict, phases: np.ndarray, fps: float, s0: int, e0: int) -> list[dict]:
    """Recognise elements within frames [s0, e0)."""
    T = len(phases)
    rr = []
    for ph in (AIR, HANDS):
        for s, e in runs(phases[s0:e0] == ph):
            rr.append((s + s0, e + s0, ph))
    rr.sort()
    # one support element per skill: a round-off/cartwheel places the hands one after the
    # other, which can show up as two hand phases separated by a brief non-flight gap
    merged = []
    short = int(0.2 * fps)
    for r in rr:
        if merged and r[2] == HANDS and merged[-1][2] == HANDS and r[0] - merged[-1][1] <= short:
            merged[-1] = (merged[-1][0], r[1], HANDS)
        elif (len(merged) >= 2 and r[2] == HANDS and merged[-1][2] == AIR and merged[-2][2] == HANDS
              and merged[-1][1] - merged[-1][0] <= short and r[0] - merged[-2][1] <= 2 * short
              and not np.any(phases[merged[-2][1]:r[0]] == FEET)):
            # hands -> brief "air" -> hands with no foot contact = one support (keypoint noise)
            merged.pop()
            merged[-1] = (merged[-1][0], r[1], HANDS)
        else:
            merged.append(r)
    rr = merged
    used = set()
    elements: list[dict] = []
    gap_ok = max(2, int(round(0.12 * fps)))
    for i, (s, e, ph) in enumerate(rr):
        if i in used or ph != HANDS:
            continue
        pre = i - 1 if i > 0 and rr[i - 1][2] == AIR and s - rr[i - 1][1] <= gap_ok and (i - 1) not in used else None
        post = i + 1 if i + 1 < len(rr) and rr[i + 1][2] == AIR and rr[i + 1][0] - e <= gap_ok else None
        es = rr[pre][0] if pre is not None else s
        ee = rr[post][1] if post is not None else e
        lo, hi = max(0, es - int(0.25 * fps)), min(T - 1, ee + int(0.15 * fps))
        rot = _rotation(sig, lo, hi)
        dur = (e - s) / fps
        a = sig["angles"]
        el_both = np.concatenate([a["elbow_l"][s:e], a["elbow_r"][s:e]])
        elbow_min = float(np.nanpercentile(el_both, 25)) if np.isfinite(el_both).sum() >= 3 else np.nan
        inv_max = _nanmax(sig["inversion"][s:e])
        line = _nanmed(a["body_line"][s:e][sig["inversion"][s:e] > 150]) if np.any(sig["inversion"][s:e] > 150) else np.nan
        shw = np.abs(sig["sh_w"][s:e])
        frontal = bool(np.isfinite(shw).sum() >= 3 and np.nanpercentile(shw, 75) > 0.42)
        turned = _turned(sig, es, ee, fps)
        speed = _nanmed(sig["speed"][s:e])
        direction = _direction(sig, es, rot, fps)
        if abs(rot) >= 240:
            if turned:
                # a half turn during the hand support = round-off (handsprings and walkovers
                # keep facing). Hurdle flight before and snap-down flight after are normal.
                kind, name = "roundoff", "Round-off"
            elif pre is not None or post is not None:
                kind = "handspring"
                name = f"{_dir_word(direction)} handspring".strip().capitalize()
                if frontal and pre is None:
                    kind, name = "roundoff", "Round-off / cartwheel"
            elif frontal or turned:
                kind = "roundoff" if turned else "cartwheel"
                name = "Round-off" if turned else "Cartwheel / round-off"
            else:
                kind = "walkover"
                name = f"{_dir_word(direction)} walkover".strip().capitalize()
        elif dur >= 0.5 and inv_max > 140 and (not np.isfinite(speed) or speed < 0.9):
            kind, name = "handstand", "Handstand"
        else:
            kind, name = "support", "Hand support"
        el = {"type": kind, "name": name, "start": s, "end": e, "t0": _f(es / fps, 3), "t1": _f(ee / fps, 3),
              "frame0": int(es), "frame1": int(ee), "direction": direction,
              "rotation_deg": _f(rot, 0), "support_time": _f(dur, 3),
              "elbow_min": _f(elbow_min, 0), "inversion_max": _f(inv_max, 0), "body_line": _f(line, 0),
              "frontal": frontal, "turned": turned}
        if pre is not None:
            el["pre_flight"] = _f((rr[pre][1] - rr[pre][0]) / fps, 3)
            used.add(pre)
        if post is not None:
            el["post_flight"] = _f((rr[post][1] - rr[post][0]) / fps, 3)
            used.add(post)
        used.add(i)
        elements.append(el)

    for i, (s, e, ph) in enumerate(rr):
        if i in used or ph != AIR:
            continue
        lo, hi = max(0, s - int(0.2 * fps)), min(T - 1, e + int(0.1 * fps))
        # don't reach back into the previous hand support (e.g. a round-off's snap-down)
        prev_hands = [r[1] for r in rr if r[2] == HANDS and r[1] <= s]
        if prev_hands:
            lo = max(lo, prev_hands[-1])
        rot = _rotation(sig, lo, hi)
        shape, knee, hip, knee_min, hip_min, split = _shape(sig, s, e)
        fm = _flight_metrics(sig, s, e, fps)
        n_rot = round(abs(rot) / 180.0) / 2.0
        if e < T and phases[min(T - 1, e)] == FEET:
            # landed on the feet => a whole number of saltos (extra lean at takeoff/landing
            # inflates the 2D angle; real 1½ saltos end on the front/back, not the feet)
            n_rot = float(max(1, int(abs(rot) / 360.0 + 0.3)))
        direction = _direction(sig, s, rot, fps)
        twists = _twists(sig, s, e)
        if abs(rot) >= 240:
            kind = "salto"
            mult = {1.0: "", 1.5: "1½ ", 2.0: "Double ", 2.5: "2½ ", 3.0: "Triple "}.get(n_rot, f"{n_rot:g}x ")
            name = f"{mult}{_dir_word(direction).lower()} salto".strip().capitalize()
            if shape in ("tuck", "pike", "straight"):
                name += f" ({'layout' if shape == 'straight' else shape})"
            if twists >= 1.0:   # 2D twist estimates below one full twist are unreliable
                name += f" + ~{twists:g} twist"
        elif abs(rot) < 120:
            if np.isfinite(split) and split >= 120:
                kind, name = "leap", "Split leap / jump"
            elif shape == "tuck":
                kind, name = "jump", "Tuck jump"
            elif shape == "pike":
                kind, name = "jump", "Pike / straddle jump"
            else:
                kind, name = "jump", "Straight jump" if twists < 0.5 else f"Jump with ~{twists:g} turn"
        else:
            kind, name = "flight", "Flight phase"
        elements.append({"type": kind, "name": name, "start": s, "end": e, "t0": _f(s / fps, 3), "t1": _f(e / fps, 3),
                         "frame0": int(s), "frame1": int(e), "direction": direction,
                         "rotation_deg": _f(rot, 0), "rotations": n_rot if kind == "salto" else None,
                         "shape": shape, "knee_mid": _f(knee, 0), "hip_mid": _f(hip, 0),
                         "knee_min": _f(knee_min, 0), "hip_min": _f(hip_min, 0), "split_max": _f(split, 0),
                         "twists_est": twists, **fm})
    elements.sort(key=lambda d: d["frame0"])
    hand_types = ("roundoff", "cartwheel", "handspring", "walkover", "support", "handstand", "salto")
    for i, el in enumerate(elements[:-1]):
        nxt = elements[i + 1]
        if el["type"] in ("jump", "flight") and nxt["type"] in hand_types                 and nxt["frame0"] - el["frame1"] <= int(0.5 * fps) and abs(el.get("rotation_deg") or 0) < 120:
            el["type"], el["name"] = "hurdle", "Hurdle"
    for i, el in enumerate(elements[:-1]):
        nxt = elements[i + 1]
        quick = nxt["frame0"] - el["frame1"] <= int(0.6 * fps)
        if el["type"] in ("cartwheel", "roundoff") and quick and nxt["type"] in ("handspring", "salto", "walkover"):
            # a round-off reverses the athlete: what follows immediately rotates backward
            el["type"], el["name"] = "roundoff", "Round-off"
            if nxt.get("direction") != "backward":
                nxt["direction"] = "backward"
                nxt["name"] = _rename_direction(nxt)
    return elements


def _rename_direction(el: dict) -> str:
    n = el["name"]
    for a, b in (("Front ", "Back "), ("front ", "back ")):
        if a in n:
            return n.replace(a, b)
    if el["type"] == "handspring":
        return "Back handspring"
    if el["type"] == "walkover":
        return "Back walkover"
    if el["type"] == "salto" and "back" not in n.lower():
        return n.replace("salto", "back salto", 1) if n.lower().startswith(("salto", "double", "triple", "1")) else "Back " + n[0].lower() + n[1:]
    return n


# ----------------------------------------------------------------------------- landing
def landing_analysis(sig: dict, phases: np.ndarray, fps: float, land: int, e0: int) -> dict:
    T = len(phases)
    w_end = min(T, land + int(1.3 * fps), e0 + int(0.5 * fps))
    if w_end - land < int(0.3 * fps):
        return {}
    a = sig["angles"]
    early = slice(land, min(w_end, land + int(0.5 * fps)))
    knee_min = np.nanmin([_nanmin(a["knee_l"][early]), _nanmin(a["knee_r"][early])])
    settle = land + int(0.25 * fps)
    kw = sig["kw"]
    bl = np.nanmedian(sig["bl"][land:w_end])
    travel = 0.0
    steps = 0
    from .kinematics import nan_gauss
    for j in (15, 16):
        x = nan_gauss(kw[settle:w_end, j, 0], 0.04 * fps)
        if np.isfinite(x).sum() > 3:
            v = np.abs(np.gradient(x)) * fps / bl            # foot speed, BL/s
            v = np.nan_to_num(v)
            travel += float(np.nansum(np.abs(np.diff(x[np.isfinite(x)]))) / bl)
            moving = v > 0.6
            # merge flickers shorter than 0.1 s, then count moves that cover real distance
            for r0, r1 in runs(~moving):
                if 0 < r0 and r1 < len(moving) and (r1 - r0) < 0.1 * fps:
                    moving[r0:r1] = True
            for r0, r1 in runs(moving):
                seg = x[r0:r1 + 1]
                seg = seg[np.isfinite(seg)]
                if len(seg) >= 2 and abs(seg[-1] - seg[0]) / bl > 0.08:
                    steps += 1
    hands = bool(np.any(phases[land:w_end] == HANDS)) or bool(np.any(sig["hands_down"][land:w_end] & (sig["inversion"][land:w_end] > 45)))
    com_h = sig["com_h"][land:w_end]
    low_sit = np.isfinite(com_h).any() and _nanmin(com_h) < 0.25
    sway = float(np.nanstd(sig["com"][settle:w_end, 0]) / bl) if np.isfinite(sig["com"][settle:w_end, 0]).sum() > 3 else np.nan
    if hands or low_sit:
        result = "fall / hands down"
    elif travel < 0.15 and steps == 0:
        result = "stuck"
    else:
        result = f"{max(1, steps)} step{'s' if steps > 1 else ''}" if steps else "small adjustment"
    return {"frame": int(land), "t": _f(land / fps, 3), "result": result, "knee_min": _f(knee_min, 0),
            "foot_travel_bl": _f(travel), "sway_bl": _f(sway, 3), "steps": steps}


# ----------------------------------------------------------------------------- notes
def form_notes(elements: list[dict], landing: dict, sig: dict, fps: float) -> list[dict]:
    notes = []

    def add(level, t, text):
        notes.append({"level": level, "t": _f(t, 3), "text": text})

    a = sig["angles"]
    for el in elements:
        s, e = el["start"], el["end"]
        if el["type"] in ("salto", "jump", "leap"):
            if el.get("shape") in ("pike", "straight") and el.get("knee_min") is not None and el["knee_min"] < 155:
                k = np.nanmin(np.stack([a["knee_l"][s:e], a["knee_r"][s:e]]), 0)
                t = (s + int(np.nanargmin(k))) / fps if np.isfinite(k).any() else s / fps
                add("warn", t, f"Bent knees in {el['shape']} shape ({el['knee_min']:.0f}° at worst, target ≥ 165°)")
            kw = sig["kw"]
            mid = slice(s + (e - s) // 4, max(s + 3 * (e - s) // 4, s + 1))
            sep = np.linalg.norm(kw[mid, 15] - kw[mid, 16], axis=1) / sig["bl"][mid]
            if np.isfinite(sep).any() and np.nanmedian(sep) > 0.28 and el["type"] == "salto":
                add("warn", (s + e) / 2 / fps, f"Legs apart during salto (ankles {np.nanmedian(sep):.2f} BL apart)")
            if el["type"] == "leap" and el.get("split_max") is not None:
                lvl = "good" if el["split_max"] >= 170 else "warn"
                add(lvl, s / fps, f"Split reached {el['split_max']:.0f}° (target 180°)")
            if el.get("height_est_m") is not None and el["type"] == "salto":
                add("info", s / fps, f"Flight {el['flight_time']:.2f}s ≈ {el['height_est_m']*100:.0f} cm height")
        if el["type"] in ("handspring", "handstand", "walkover", "support", "roundoff", "cartwheel"):
            if el.get("elbow_min") is not None and el["elbow_min"] < 150:
                add("warn", s / fps, f"Bent arms in hand support ({el['elbow_min']:.0f}°, target ≥ 165°)")
            if el["type"] == "handstand" and el.get("body_line") is not None and abs(180 - el["body_line"]) > 15:
                add("warn", s / fps, f"Handstand line {abs(180 - el['body_line']):.0f}° off straight (arch/pike)")
            if el["type"] == "handstand":
                add("info", s / fps, f"Handstand held {el['support_time']:.2f}s")
    if landing:
        t = landing["t"]
        if landing["result"] == "stuck":
            add("good", t, "Stuck landing")
        elif landing["result"].startswith("fall"):
            add("bad", t, "Fall / hands down on landing")
        else:
            add("warn", t, f"Landing: {landing['result']}")
        if landing.get("knee_min") is not None and landing["knee_min"] < 90:
            add("warn", t, f"Deep landing (knees {landing['knee_min']:.0f}°)")
    notes.sort(key=lambda n: n["t"] or 0)
    return notes


# ----------------------------------------------------------------------------- segment
def analyze_segment(sig: dict, phases: np.ndarray, fps: float, s0: int, e0: int, scale: dict) -> dict:
    s0, e0 = int(s0), int(min(e0, len(phases)))
    elements = find_elements(sig, phases, fps, s0, e0)
    landing = {}
    if elements and elements[-1]["type"] not in ("handstand", "support"):
        last = elements[-1]
        land = last["frame1"]
        # first frame back on the feet after the last element
        while land < e0 - 1 and phases[land] not in (FEET,):
            land += 1
        landing = landing_analysis(sig, phases, fps, land, e0)
    notes = form_notes(elements, landing, sig, fps)

    sl = slice(s0, e0)
    m_bl = scale.get("m_per_bl")
    spd = sig["speed"][sl]
    first = elements[0]["frame0"] if elements else None
    approach = _nanmed(sig["speed"][max(s0, first - int(1.0 * fps)):first]) if first else np.nan
    rot_total = _rotation(sig, s0, max(s0, e0 - 1))
    flights = [v for el in elements for v in (el.get("flight_time"), el.get("pre_flight"), el.get("post_flight")) if v]
    heights = [el["height_est_m"] for el in elements if el.get("height_est_m")]
    metrics = {
        "duration": _f((e0 - s0) / fps, 2),
        "elements": len(elements),
        "rotation_total_deg": _f(rot_total, 0),
        "max_inversion": _f(_nanmax(sig["inversion"][sl]), 0),
        "max_speed_bl": _f(_nanmax(spd)),
        "approach_speed_bl": _f(approach),
        "max_flight_time": _f(max(flights), 3) if flights else None,
        "max_height_m": _f(max(heights), 2) if heights else None,
        "max_split": _f(_nanmax(sig["angles"]["split"][sl]), 0),
        "pose_coverage": _f(float(np.mean(sig["valid"][sl])), 2),
    }
    if m_bl:
        metrics["max_speed_ms"] = _f(metrics["max_speed_bl"] * m_bl if metrics["max_speed_bl"] else None)
        metrics["approach_speed_ms"] = _f(metrics["approach_speed_bl"] * m_bl if metrics["approach_speed_bl"] else None)
    names = [el["name"] for el in elements if el["type"] not in ("flight", "hurdle")]
    label = " → ".join(names[:4]) + (" …" if len(names) > 4 else "") if names else (
        "Run / movement" if (metrics["max_speed_bl"] or 0) > 2 else "Movement")
    return {"label": label, "elements": elements, "landing": landing, "notes": notes, "metrics": metrics}
