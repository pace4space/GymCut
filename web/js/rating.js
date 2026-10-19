// Traffic-light ratings for an attempt (one detected skill).
//
// Two layers:
//  • technique – absolute targets drawn from gymnastics execution standards
//    (straight knees in layout/pike, straight arms in support, 180° splits,
//    stuck landings, tight tuck);
//  • personal – flight time and height against the athlete's own history for
//    the same skill (needs at least one earlier attempt).
// Each item is good / warn / bad; the attempt's overall light is its worst item.
// When an Unreal 3D solve exists (att.m3d) its camera-independent joint angles replace
// the 2D estimates (items marked d3), and 3D-only checks are added: knees together in
// the tuck, chest up at landing, twist.

const LVL = { good: 0, warn: 1, bad: 2 };
const worst = (a, b) => (LVL[a] >= LVL[b] ? a : b);

function band(v, good, warn, higherIsBetter = true) {
  if (v === null || v === undefined || !isFinite(v)) return null;
  if (higherIsBetter) return v >= good ? "good" : v >= warn ? "warn" : "bad";
  return v <= good ? "good" : v <= warn ? "warn" : "bad";
}

export function skillKey(att) {
  return (att.label || "").replace(/\s*\+\s*~[\d.]+ twist/g, "").trim();
}

// history: earlier attempts of the same athlete (any skill) as returned by /api/segments
export function rateAttempt(att, history = []) {
  const items = [];
  const els = (att.elements || []).filter((e) => e.type !== "hurdle");
  const add = (key, label, value, unit, level, hint, d3 = false) => { if (level) items.push({ key, label, value, unit, level, hint, d3 }); };
  const els3 = (att.m3d?.elements || []).filter((e) => e.type !== "hurdle");

  // landing
  const L = att.landing?.result;
  if (L) {
    const lvl = L === "stuck" ? "good" : /fall/.test(L) ? "bad" : /^1 step|small/.test(L) ? "warn" : "bad";
    add("landing", "Landing", L, "", lvl, "Stuck = no foot movement after landing");
  }
  els.forEach((e, i) => {
    const e3 = els3[i] && els3[i].type === e.type ? els3[i] : null;
    const pick = (k) => (e3 && e3[k] != null ? [Math.round(e3[k]), true] : [e[k], false]);
    if (e.type === "salto" || e.type === "jump") {
      if (e.shape === "straight" || e.shape === "pike") {
        const [v, d3] = pick("knee_min");
        add("knees", `Knees (${e.shape})`, v, "°", band(v, 165, 150), "Legs should be straight: ≥ 165°", d3);
      } else if (e.shape === "tuck") {
        const [v, d3] = pick("hip_min");
        if (v) add("tuck", "Tuck tightness (hip)", v, "°", band(v, 70, 90, false), "Tighter tuck = faster rotation: hip ≤ 70°", d3);
        if (e3?.knee_gap_cm != null) add("kneegap", "Knees together", Math.round(e3.knee_gap_cm), "cm", band(e3.knee_gap_cm, 25, 35, false), "Knees closed in the tuck: ≤ 25 cm apart", true);
      }
      if (e3?.land_trunk_deg != null && e.type === "salto") {
        add("chest", "Chest up at landing", Math.round(e3.land_trunk_deg), "° lean", band(e3.land_trunk_deg, 25, 45, false), "Trunk ≤ 25° from vertical at touchdown – more lean = under-rotated", true);
      }
      if (e3?.twist_deg != null && Math.abs(e3.twist_deg) >= 90 && e.type === "salto") {
        add("twist", "Twist", (Math.abs(e3.twist_deg) / 360).toFixed(2), "turns", "good", "Rotation about the long axis (3D only)", true);
      }
    }
    if (e.type === "leap") add("split", "Split", e.split_max, "°", band(e.split_max, 170, 150), "Full split = 180°");
    if (["handspring", "roundoff", "walkover", "cartwheel", "support", "handstand"].includes(e.type) && (e.elbow_min || e3?.elbow_min)) {
      const [v, d3] = pick("elbow_min");
      add("arms", `Arms (${e.name.toLowerCase()})`, Math.round(v), "°", band(v, 165, 150), "Straight arms in support: ≥ 165°", d3);
    }
    if (e.type === "handstand" && e.body_line) {
      const dev = Math.abs(180 - e.body_line);
      add("line", "Handstand line", Math.round(dev), "° off", band(dev, 10, 20, false), "Straight line shoulder–hip–ankle");
    }
  });
  // personal history for the same skill
  const key = skillKey(att);
  const same = history.filter((h) => skillKey(h) === key && !(h.vid === att.vid && h.id === att.id));
  const flight = att.metrics?.max_flight_time;
  if (flight && same.length) {
    const best = Math.max(...same.map((h) => h.metrics?.max_flight_time || 0));
    if (best > 0) add("flight", "Flight vs best", flight, "s", band(flight / best, 0.97, 0.88), `Personal best ${best.toFixed(2)} s`);
  }
  const hip3 = att.m3d?.hip_peak_cm;
  const same3 = same.filter((h) => h.m3d?.hip_peak_cm);
  if (hip3 && same3.length) {
    const best = Math.max(...same3.map((h) => h.m3d.hip_peak_cm));
    add("height", "Hip height vs best", Math.round(hip3), "cm", band(hip3 / best, 0.95, 0.85), `Personal best ${Math.round(best)} cm (3D)`, true);
  }
  const height = att.metrics?.max_height_m;
  if (height && same.length && !(hip3 && same3.length)) {
    const best = Math.max(...same.map((h) => h.metrics?.max_height_m || 0));
    if (best > 0) add("height", "Height vs best", Math.round(height * 100), "cm", band(height / best, 0.95, 0.85), `Personal best ${Math.round(best * 100)} cm`);
  }
  const overall = items.length ? items.reduce((w, it) => worst(w, it.level), "good") : null;
  return { overall, items, sameCount: same.length };
}

export const LIGHT = { good: "var(--good)", warn: "var(--warn)", bad: "var(--bad)" };

// best earlier attempt of the same skill (for ghost reference): longest flight, then stuck landing
export function bestOf(att, pool) {
  const key = skillKey(att);
  const same = pool.filter((p) => skillKey(p) === key && !(p.vid === att.vid && p.id === att.id));
  if (!same.length) return null;
  const score = (p) => (p.metrics?.max_flight_time || 0) + (p.landing?.result === "stuck" ? 0.2 : 0)
    - 0.05 * (p.notes?.length || 0);
  return same.reduce((a, b) => (score(b) > score(a) ? b : a));
}
