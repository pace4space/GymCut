// Ghost overlay: draw another attempt's skeleton on top of this one, time-aligned.
//
// modes
//   "pose" – the ghost is locked to the athlete's hips every frame and scaled to
//            their body size: pure technique comparison (shape, angles, timing).
//   "path" – anchored once at the alignment point, then follows its own
//            trajectory: shows differences in height and distance travelled.
// Alignment: takeoff (first element), segment start or landing. If the
// reference travels the other way it is mirrored so both face the same way.
//
// 2D for now; the same interface will take 3D-lifted poses (camera-independent
// alignment and rotation) when the 3D layer lands.

import { api } from "./common.js";

const cache = new Map();
export async function loadAnalysis(vid) {
  if (!cache.has(vid)) cache.set(vid, api.get(`/api/videos/${vid}/analysis`));
  return cache.get(vid);
}
export function forgetAnalysis(vid) { cache.delete(vid); }

export function alignFrame(seg, mode = "takeoff") {
  const els = (seg.elements || []).filter((e) => e.type !== "hurdle");
  if (mode === "start") return seg.start;
  if (mode === "landing") return seg.landing?.frame ?? (els.at(-1)?.frame1 ?? seg.end);
  return els[0]?.frame0 ?? seg.start;
}

function invAffine(m) {
  const [a, b, tx, c, d, ty] = m;
  const det = a * d - b * c || 1;
  const ia = d / det, ib = -b / det, ic = -c / det, id = a / det;
  return [ia, ib, -(ia * tx + ib * ty), ic, id, -(ic * tx + id * ty)];
}
const apply = (m, x, y) => [m[0] * x + m[1] * y + m[2], m[3] * x + m[4] * y + m[5]];

function hip(k) {
  if (!k || k[11 * 3 + 2] < 0.2 || k[12 * 3 + 2] < 0.2) return null;
  return [(k[33] + k[36]) / 2, (k[34] + k[37]) / 2];
}

// body length (shoulder→ankle) in image pixels during this attempt: 90th percentile of
// torso + thigh + shank, so foreshortened frames don't shrink it
function localBL(A, seg) {
  const L = [];
  const d = (k, a, b) => (k[a * 3 + 2] >= 0.3 && k[b * 3 + 2] >= 0.3 ? Math.hypot(k[a * 3] - k[b * 3], k[a * 3 + 1] - k[b * 3 + 1]) : null);
  for (let f = seg.start; f < seg.end; f++) {
    const k = A.kps[f];
    if (!k) continue;
    const torso = d(k, 5, 11) ?? d(k, 6, 12), thigh = d(k, 11, 13) ?? d(k, 12, 14), shank = d(k, 13, 15) ?? d(k, 14, 16);
    if (torso && thigh && shank) L.push(torso + thigh + shank);
  }
  if (!L.length) return A.bl_px || 1;
  L.sort((a, b) => a - b);
  return L[Math.floor(L.length * 0.9)];
}

export function travelDir(A, seg) {
  const src = A.com_w || A.com;
  let a = null, b = null;
  for (let f = seg.start; f < seg.end; f++) if (src[f]) { a = src[f]; break; }
  for (let f = seg.end - 1; f >= seg.start; f--) if (src[f]) { b = src[f]; break; }
  return a && b && Math.abs(b[0] - a[0]) > 5 ? Math.sign(b[0] - a[0]) : 0;
}

function lastHip(A, f, lo) {
  for (let i = f; i >= lo; i--) { const h = hip(A.kps[i]); if (h) return [h, i]; }
  return [null, f];
}

// cur/ref: { A (analysis payload), seg }   → returns ghostAt(f) -> flat kps in cur image coords
export function makeGhost(cur, ref, { mode = "pose", align = "takeoff", alignFrames = null } = {}) {
  // alignFrames: [cur frame, ref frame] that show the same moment (e.g. from Compare's motion sync)
  const [ca, ra] = alignFrames || [alignFrame(cur.seg, align), alignFrame(ref.seg, align)];
  const scale = localBL(cur.A, cur.seg) / localBL(ref.A, ref.seg);
  const dc = travelDir(cur.A, cur.seg), dr = travelDir(ref.A, ref.seg);
  const mirror = dc && dr && dc !== dr ? -1 : 1;
  const curInv = cur.A.cam ? cur.A.cam.map(invAffine) : null;
  const toWorldRef = (fr, x, y) => (ref.A.cam ? apply(ref.A.cam[fr], x, y) : [x, y]);
  const toWorldCur = (f, x, y) => (cur.A.cam ? apply(cur.A.cam[f], x, y) : [x, y]);
  const toImgCur = (f, x, y) => (curInv ? apply(curInv[f], x, y) : [x, y]);

  // path-mode anchors (world coordinates at the alignment frames)
  const [rh0, rf0] = lastHip(ref.A, ra, ref.seg.start);
  const [ch0, cf0] = lastHip(cur.A, ca, cur.seg.start);
  const rAnchor = rh0 ? toWorldRef(rf0, ...rh0) : null;
  const cAnchor = ch0 ? toWorldCur(cf0, ...ch0) : null;

  return function ghostAt(f) {
    const tau = (f - ca) / cur.A.fps;
    const fr = Math.round(ra + tau * ref.A.fps);
    if (fr < ref.seg.start || fr >= ref.seg.end || fr >= ref.A.kps.length) return null;
    const rk = ref.A.kps[fr];
    if (!rk) return null;
    const out = rk.slice();
    if (mode === "pose") {
      const rh = hip(rk);
      const [ch] = lastHip(cur.A, f, Math.max(0, f - 10));
      if (!rh || !ch) return null;
      for (let j = 0; j < 17; j++) {
        out[j * 3] = ch[0] + mirror * scale * (rk[j * 3] - rh[0]);
        out[j * 3 + 1] = ch[1] + scale * (rk[j * 3 + 1] - rh[1]);
      }
    } else {
      if (!rAnchor || !cAnchor) return null;
      for (let j = 0; j < 17; j++) {
        const [wx, wy] = toWorldRef(fr, rk[j * 3], rk[j * 3 + 1]);
        const gx = cAnchor[0] + mirror * scale * (wx - rAnchor[0]);
        const gy = cAnchor[1] + scale * (wy - rAnchor[1]);
        [out[j * 3], out[j * 3 + 1]] = toImgCur(f, gx, gy);
      }
    }
    // mirrored left/right labels swap visually; keep the label order consistent
    return out;
  };
}
