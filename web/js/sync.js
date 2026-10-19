// Motion sync: line up two clips by matching the shape of their motion curves.
// Used by Compare (two attempts) and Compare angles (two cameras of the same moment).

export const MATCH_SIGNALS = ["inversion", "com_h", "activity", "knee", "hip"];
const R = 30;   // matching resolution (samples per second)

// z-normalised curves of one clip between frames lo..hi, resampled to R Hz
function motionSeries(A, lo, hi) {
  const n = Math.max(2, Math.floor(((hi - lo) / A.fps) * R));
  const get = (key, f) => {
    const sig = A.signals;
    if (key === "knee" || key === "hip") {
      const l = sig[`${key}_l`]?.[f], r = sig[`${key}_r`]?.[f];
      return l == null ? r : r == null ? l : Math.min(l, r);
    }
    return sig[key]?.[f];
  };
  return MATCH_SIGNALS.map((key) => {
    const out = new Float64Array(n);
    let last = null;
    for (let i = 0; i < n; i++) {
      const v = get(key, Math.min(A.n_frames - 1, Math.round(lo + (i / R) * A.fps)));
      if (v != null && isFinite(v)) last = v;
      out[i] = last ?? 0;
    }
    let m = 0; out.forEach((v) => (m += v)); m /= n;
    let sd = 0; out.forEach((v) => (sd += (v - m) ** 2)); sd = Math.sqrt(sd / n);
    return sd > 1e-6 ? out.map((v) => (v - m) / sd) : null;
  });
}

// a, b: { A: analysis, lo, hi } (frame ranges to match). Slides b over a (both directions,
// overlap >= 55 % of the shorter range) and returns the VIDEO offset
// (b video time = a video time + offset) and a match score (-1..1, correlation).
export function matchMotion(a, b) {
  const sa = motionSeries(a.A, a.lo, a.hi), sb = motionSeries(b.A, b.lo, b.hi);
  const use = sa.map((x, i) => x && sb[i]).map((ok, i) => (ok ? i : -1)).filter((i) => i >= 0);
  if (!use.length) return null;
  const na = sa[use[0]].length, nb = sb[use[0]].length;
  const minOv = Math.max(R, Math.floor(0.55 * Math.min(na, nb)));
  let best = { score: -Infinity, lag: 0 };
  for (let lag = -(na - minOv); lag <= nb - minOv; lag++) {            // b sample = a sample + lag
    const i0 = Math.max(0, -lag), i1 = Math.min(na, nb - lag);
    if (i1 - i0 < minOv) continue;
    let sum = 0;
    for (const c of use) {
      const x = sa[c], y = sb[c];
      for (let i = i0; i < i1; i++) sum += x[i] * y[i + lag];
    }
    const sc = sum / ((i1 - i0) * use.length);
    if (sc > best.score) best = { score: sc, lag };
  }
  return { offset: b.lo / b.A.fps - a.lo / a.A.fps + best.lag / R, score: best.score };
}

// the element of `b` (list of {frame0}) closest to where element `fa` of a lands after the sync
export function pairElement(aFps, bFps, offset, fa, bEls) {
  const tb = (fa / aFps + offset) * bFps;
  let k = 0, d = Infinity;
  bEls.forEach((e, i) => { const x = Math.abs(e.frame0 - tb); if (x < d) { d = x; k = i; } });
  return k;
}
