// Compare angles: two videos of the same moment filmed from different places, synced
// automatically (motion signals) and played on one shared clock with their skeletons and
// what each camera detected, so tracking and analysis can be compared view by view.
import { api, h, icon, toast, fmtTime, drawSkeleton, containTransform, setupHiDPI } from "./common.js";
import { LineChart } from "./charts.js";
import { matchMotion, pairElement } from "./sync.js";

const LS = {
  get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};
const PH = { A: ["AIR", "#6aa8ff"], H: ["HANDS", "#ffb347"], F: ["FEET", "#3a4350"] };

const SIGNALS = [["inversion", "Inversion"], ["com_h", "Hip height (BL)"], ["activity", "Activity"], ["knee_l", "Left knee"], ["knee_r", "Right knee"],
  ["hip_l", "Left hip"], ["hip_r", "Right hip"], ["elbow_l", "Left elbow"], ["elbow_r", "Right elbow"], ["speed", "Speed (BL/s)"]];
const COL_A = "#36c2a0", COL_B = "#ff9628";

// ---------------------------------------------------------------- page
export function mountAngles(root, query) {
  let alive = true;
  const qs = new URLSearchParams(query || "");
  let ids = [qs.get("a"), qs.get("b")];
  if (!ids[0] || !ids[1]) {
    root.append(h("div", { class: "page" }, h("div", { class: "empty" }, h("h3", {}, "Pick two videos"),
      h("p", {}, "In the Library press Select, tick two videos of the same moment from different angles, then Compare angles."))));
    return () => {};
  }
  const sides = ids.map(() => ({}));
  let offset = 0, score = 0, T = 0, playing = false, rate = 0.5, tMin = 0, tMax = 1, lastTs = 0;
  // sync: base offset from the chosen mode + a fine nudge in B frames (B video time = A video time + offset)
  let mode = "auto", keyEls = [0, 0], nudge = 0, base = 0, syncLabel = "", sharedOnly = true, loopOn = true;
  let sigKey = "inversion", chartFps = 30, got3d = null;

  const offEl = h("span", { class: "mono" });
  const timeEl = h("span", { class: "mono dim" });
  const scrub = h("input", { type: "range", min: 0, max: 1000, value: 0, class: "grow" });
  const playBtn = h("button", { class: "btn icon", title: "Play / pause (Space)" }, icon("play"));
  const speed = h("div", { class: "seg-ctl" }, ...[[0.25, "¼×"], [0.5, "½×"], [1, "1×"]].map(([v, t]) =>
    h("button", { class: v === rate ? "on" : "", onclick: (e) => { rate = v; [...speed.children].forEach((b) => b.classList.toggle("on", b === e.currentTarget)); syncVideos(true); } }, t)));
  const players = h("div", { class: "angles-players" });
  const box3d = h("div", { class: "coach-3d angles-3d hidden" });
  const info3d = h("div", { class: "angles-3dinfo hidden" });
  const btn3d = h("button", { class: "btn sm", title: "Both 3D reconstructions in one scene, synced – how well do the two camera angles agree?" }, icon("cube"), "3D");
  let v3 = null, d3 = null, on3d = false, fusedOn = false, fused = null;
  const fuseBtn = h("button", { class: "btn sm hidden", title: "Triangulate both cameras together (bundle adjustment) – gold figure" }, "Two-camera 3D");
  const fuseInfo = h("div", { class: "angles-fuse hidden" });
  const lanes = h("div", { class: "angles-lanes" });
  const modeSel = h("select", { style: { height: "30px" } },
    ...[["auto", "Sync: match motion (auto)"], ["key", "Sync: match key elements…"]].map(([v, t]) => h("option", { value: v }, t)));
  const keyBox = h("span", { class: "row hidden", style: { gap: "6px" } });
  const syncChip = h("span", { class: "chip", title: "How the two cameras are lined up" });
  const sharedBtn = h("button", { class: "btn sm on", title: "Play only the part both cameras recorded, so both sides always move together" }, "Shared part");
  const loopBtn = h("button", { class: "btn sm on", title: "Loop the slow motion" }, icon("loop"), "Loop");
  const sigSel = h("select", { style: { height: "30px" } }, ...SIGNALS.map(([k, t]) => h("option", { value: k }, t)));
  const chartCanvas = h("canvas");
  const chartCard = h("div", { class: "card", style: { marginTop: "12px" } },
    h("div", { class: "card-h" }, h("h3", {}, "Motion pattern"),
      h("span", { class: "dim", style: { fontSize: "12px" } }, "both cameras on the shared timeline – matching shapes = good sync · click to seek"),
      h("span", { class: "spacer" }), sigSel),
    h("div", { class: "card-b chart-box" }, chartCanvas));
  const chart = new LineChart(chartCanvas, { height: 200, onSeek: (i) => { T = tMin + i / chartFps; syncVideos(true); } });
  const setNudge = (n) => { nudge = n; applySync(); };
  const head = h("div", { class: "page-head" },
    h("div", { class: "grow" }, h("h1", {}, "Compare angles"),
      h("p", {}, "The same moment from two cameras, synced automatically. Compare what each view tracks and detects.")),
    h("button", { class: "btn", title: "Swap left and right", onclick: () => { location.hash = `#/angles?a=${ids[1]}&b=${ids[0]}`; } }, icon("flip"), "Swap"));
  const bar = h("div", { class: "card angles-bar" }, h("div", { class: "card-b row wrap" },
    playBtn,
    h("button", { class: "btn icon", title: "Back one frame (,)", onclick: () => step(-1) }, icon("prev")),
    h("button", { class: "btn icon", title: "Forward one frame (.)", onclick: () => step(1) }, icon("next")),
    speed, loopBtn, sharedBtn, timeEl, scrub,
    h("span", { class: "sep" }),
    modeSel, keyBox, syncChip,
    h("button", { class: "btn sm", title: "Right video one frame earlier (Shift+←) – line up the same key frame by eye", onclick: () => setNudge(nudge - 1) }, "B −1f"),
    offEl,
    h("button", { class: "btn sm", title: "Right video one frame later (Shift+→)", onclick: () => setNudge(nudge + 1) }, "B +1f"),
    h("button", { class: "btn sm ghost", title: "Match the motion again (clears the fine tuning)", onclick: () => { mode = "auto"; modeSel.value = "auto"; nudge = 0; applySync(); toast(`Synced: ${offset >= 0 ? "+" : ""}${offset.toFixed(2)} s · ${syncLabel}`, "ok"); } }, icon("refresh"), "Auto"),
    h("span", { class: "sep" }), btn3d, fuseBtn));
  root.append(h("div", { class: "page angles" }, head, players, bar, info3d, fuseInfo, box3d, lanes, chartCard));
  modeSel.addEventListener("change", () => {
    mode = modeSel.value;
    nudge = 0;
    if (mode === "key") {                          // start from the elements the motion sync pairs up
      const m = autoMatch();
      const ka = Math.max(0, sides[0].els.findIndex((e) => e.type !== "hurdle"));
      keyEls = [ka, sides[0].els[ka] ? pairElement(sides[0].A.fps, sides[1].A.fps, m.offset, sides[0].els[ka].frame0, sides[1].els) : 0];
    }
    applySync();
  });
  sharedBtn.addEventListener("click", () => { sharedOnly = !sharedOnly; sharedBtn.classList.toggle("on", sharedOnly); applySync(); });
  loopBtn.addEventListener("click", () => { loopOn = !loopOn; loopBtn.classList.toggle("on", loopOn); saveSettings(); });
  sigSel.addEventListener("change", () => { sigKey = sigSel.value; renderChart(); });
  fuseBtn.addEventListener("click", () => toggleFused());
  async function toggleFused() {
    fusedOn = !fusedOn;
    fuseBtn.classList.toggle("on", fusedOn);
    fuseInfo.classList.toggle("hidden", !fusedOn);
    if (!fusedOn) { v3?.removeExtras(); return; }
    if (!fused) {
      fuseInfo.replaceChildren(h("span", { class: "dim" }, "Triangulating both cameras… (a few seconds)"));
      try {
        fused = await api.get(`/api/fuse3d?a=${ids[0]}&b=${ids[1]}&offset=${offset.toFixed(3)}`);
      } catch (e) {
        fuseInfo.replaceChildren(h("span", { style: { color: "var(--bad)" } }, e.message));
        return;
      }
    }
    const q = fused.quality;
    const col = { high: "var(--good)", medium: "var(--warn)", low: "var(--bad)" }[q.confidence];
    fuseInfo.replaceChildren(
      h("div", { class: "row wrap", style: { gap: "8px" } },
        h("span", { class: "v3d-chip", style: { color: "#ffd94d", borderColor: "rgba(255,217,77,.6)" } }, "two-camera"),
        h("b", { style: { color: col } }, `confidence: ${q.confidence}`),
        h("span", { class: "dim" }, `second camera ${q.camera_b} · fit ${q.reproj_a_px} / ${q.reproj_b_px} px · athlete ${q.athlete_px_a} / ${q.athlete_px_b} px tall · hips ${q.hip_peak_cm} cm (single cameras: ${q.hip_peak_single_cm.join(", ")})`)),
      q.reasons.length ? h("ul", { class: "fuse-reasons" }, ...q.reasons.map((r) => h("li", {}, r))) : null,
      q.confidence !== "high" ? h("div", { class: "dim", style: { fontSize: "12.5px" } },
        "Best results: both phones on tripods (or one held very still), the athlete at least ~200 px tall in each, filmed from clearly different directions (60–120° apart).") : null);
    if (v3 && d3) {
      v3.removeExtras();
      const shift = fused.reference === ids[0] ? 0 : (d3.video_t0 + offset - fused.video_t0);
      v3.addExtra({ frames: fused.frames, fps: fused.fps, shift, color: 0xffd94d, opacity: 1 });
    }
  }
  btn3d.addEventListener("click", () => toggle3d());

  // ---------------------------------------------------------------- both 3D solves in one scene
  async function toggle3d() {
    on3d = !on3d;
    btn3d.classList.toggle("on", on3d);
    box3d.classList.toggle("hidden", !on3d);
    info3d.classList.toggle("hidden", !on3d);
    if (!on3d) return;
    if (!d3) {
      const segOf = (s) => (s.A.segments[0] || {}).id;
      const got = await Promise.all(sides.map((s) => api.get(`/api/videos/${s.meta.id}/pose3d/${segOf(s)}?ghost=0`).catch(() => null)));
      if (got.some((g) => !g)) {
        info3d.replaceChildren(h("span", {}, "3D needs both clips solved. "),
          ...sides.map((s, i) => got[i] ? null : h("button", { class: "btn sm", onclick: async () => {
            await api.post(`/api/videos/${s.meta.id}/replay3d`, { segment: segOf(s), ghost: true });
            toast(`3D solve queued for ${s.meta.name} – a few minutes`, "ok");
          } }, icon("cube"), `Make 3D: ${s.meta.name}`)));
        box3d.classList.add("hidden");
        on3d = false; btn3d.classList.remove("on");
        info3d.classList.remove("hidden");
        return;
      }
      got3d = got;
      d3 = combine(got[0], got[1]);
    }
    if (!v3) {
      const { createView3D } = await import("./view3d.js");
      v3 = createView3D(box3d);
      v3.setData(d3, { screenDir: 1 });
      v3.setHud([h("span", { class: "v3d-chip a" }, sides[0].meta.name), h("span", { class: "v3d-chip g" }, sides[1].meta.name)]);
    }
    fuseBtn.classList.remove("hidden");
    info3d.replaceChildren(h("b", {}, "3D agreement: "),
      h("span", {}, `median joint difference ${d3.agree.median.toFixed(0)} cm (90% within ${d3.agree.p90.toFixed(0)} cm)`),
      h("span", { class: "dim" }, ` · knee angle difference median ${d3.agree.knee.toFixed(0)}° · teal = ${sides[0].meta.name}, pink = ${sides[1].meta.name}`));
  }

  // B's solve moved into A's space: same heading of the pass, same pelvis at a common moment;
  // time from the video sync (both cameras filmed the same moment)
  function combine(a, b) {
    const J = a.joints.length;
    // anim time b = anim time a + shift   (video_t = video_t0 + anim_t * rate)
    const shift = (a.video_t0 + offset - b.video_t0);
    const yaw = (a.heading || 0) - (b.heading || 0);
    const c = Math.cos(yaw), s = Math.sin(yaw);
    const at = (fr, fps, t) => fr[Math.max(0, Math.min(fr.length - 1, Math.round(t * fps)))];
    const tRef = a.align || 0;
    const pa = at(a.frames, a.fps, tRef), pb = at(b.frames, b.fps, tRef + shift);
    const rot = (x, y) => [x * c - y * s, x * s + y * c];
    const [rbx, rby] = rot(pb[0], pb[1]);
    const dx = pa[0] - rbx, dy = pa[1] - rby;
    const frames = b.frames.map((f) => {
      const o = f.slice();
      for (let j = 0; j < J; j++) {
        if (f[j * 3] == null) continue;
        const [x, y] = rot(f[j * 3], f[j * 3 + 1]);
        o[j * 3] = x + dx; o[j * 3 + 1] = y + dy;
      }
      return o;
    });
    // agreement over the overlapping time (pelvis-relative so travel errors don't dominate)
    const diffs = [], knees = [];
    const ang = (f, i0, i1, i2) => {
      const v = (i, k) => f[i * 3 + k];
      const u = [0, 1, 2].map((k) => v(i0, k) - v(i1, k)), w = [0, 1, 2].map((k) => v(i2, k) - v(i1, k));
      const d = u[0] * w[0] + u[1] * w[1] + u[2] * w[2];
      return (Math.acos(Math.max(-1, Math.min(1, d / (Math.hypot(...u) * Math.hypot(...w) + 1e-9)))) * 180) / Math.PI;
    };
    const ix = (n) => a.joints.indexOf(n);
    for (let k = 0; k < a.frames.length; k++) {
      const t = k / a.fps, fb = at(frames, b.fps, t + shift);
      const kb = Math.round((t + shift) * b.fps);
      if (kb < 0 || kb >= frames.length) continue;
      const fa = a.frames[k];
      for (let j = 1; j < J; j++) {
        if (fa[j * 3] == null || fb[j * 3] == null) continue;
        const d = [0, 1, 2].map((q) => (fa[j * 3 + q] - fa[q]) - (fb[j * 3 + q] - fb[q]));
        diffs.push(Math.hypot(...d));
      }
      for (const [h0, k0, f0] of [["thigh_l", "calf_l", "foot_l"], ["thigh_r", "calf_r", "foot_r"]]) {
        knees.push(Math.abs(ang(fa, ix(h0), ix(k0), ix(f0)) - ang(fb, ix(h0), ix(k0), ix(f0))));
      }
    }
    const pct = (arr, p) => { const s2 = [...arr].sort((x, y) => x - y); return s2.length ? s2[Math.floor(s2.length * p)] : NaN; };
    return {
      ...a,
      ghost: { frames, fps: b.fps, shift, from: 0, peak: b.peak, label: sides[1].meta.name, recorded: sides[1].meta.name, tag: sides[1].meta.name },
      agree: { median: pct(diffs, 0.5), p90: pct(diffs, 0.9), knee: pct(knees, 0.5) },
    };
  }

  const key = () => `gc_angles_${ids[0]}_${ids[1]}`;          // final offset (also used by Compare / two-camera 3D)
  const setKey = () => `gc_anglesx_${ids[0]}_${ids[1]}`;      // how it was made
  const saveSettings = () => LS.set(setKey(), { mode, keyEls, nudge, sharedOnly, loopOn });
  let autoCache = null;
  function autoMatch() {
    if (!autoCache) {
      autoCache = matchMotion({ A: sides[0].A, lo: 0, hi: sides[0].A.n_frames }, { A: sides[1].A, lo: 0, hi: sides[1].A.n_frames })
        || { offset: 0, score: 0 };
    }
    return autoCache;
  }
  // base offset from the mode, fine nudge on top; then everything that depends on it
  function applySync() {
    const [a, b] = sides;
    if (mode === "key" && a.els[keyEls[0]] && b.els[keyEls[1]]) {
      const ea = a.els[keyEls[0]], eb = b.els[keyEls[1]];
      base = eb.frame0 / b.A.fps - ea.frame0 / a.A.fps;
      syncLabel = `${ea.name} ↔ ${eb.name}`;
    } else {
      mode = "auto";
      const m = autoMatch();
      base = m.offset; score = m.score;
      syncLabel = `motion match ${Math.round(Math.max(0, score) * 100)}%`;
    }
    offset = base + nudge / b.A.fps;
    LS.set(key(), offset);
    saveSettings();
    renderKeyBox();
    computeRange();
    renderOffset();
    T = Math.max(tMin, Math.min(tMax, T));
    renderLanes();
    renderChart();
    syncVideos(true);
    refresh3d();
  }
  function renderKeyBox() {
    modeSel.value = mode;
    keyBox.classList.toggle("hidden", mode !== "key");
    if (mode !== "key") return;
    const sel = (i) => {
      const el = h("select", { style: { height: "30px", maxWidth: "190px" } },
        ...sides[i].els.map((e, k) => h("option", { value: k, selected: k === keyEls[i] }, `${sides[i].meta.name}: ${e.name}`)));
      el.addEventListener("change", () => { keyEls[i] = +el.value; nudge = 0; applySync(); });
      return el;
    };
    keyBox.replaceChildren(sel(0), h("span", { class: "dim" }, "↔"), sel(1));
  }
  function renderOffset() {
    const fb = sides[1].A?.fps || 30;
    offEl.textContent = `${offset >= 0 ? "+" : ""}${offset.toFixed(3)} s${nudge ? ` (${nudge > 0 ? "+" : ""}${nudge}f)` : ""}`;
    offEl.title = `Right video time = left video time ${offset >= 0 ? "+" : "−"} ${Math.abs(offset).toFixed(3)} s · fine tuning ${nudge} frame(s) of ${Math.round(fb)} fps`;
    const shared = sharedOnly && tMax - tMin >= 0.6 ? ` · shared ${(tMax - tMin).toFixed(1)} s` : "";
    syncChip.textContent = syncLabel + shared;
    syncChip.style.color = mode === "auto" && score < 0.5 ? "var(--warn)" : "";
  }
  function refresh3d() {
    if (got3d) {
      d3 = combine(got3d[0], got3d[1]);
      if (v3) {
        v3.setData(d3, { screenDir: 1 });
        v3.setHud([h("span", { class: "v3d-chip a" }, sides[0].meta.name), h("span", { class: "v3d-chip g" }, sides[1].meta.name)]);
      }
      if (on3d) info3d.replaceChildren(h("b", {}, "3D agreement: "),
        h("span", {}, `median joint difference ${d3.agree.median.toFixed(0)} cm (90% within ${d3.agree.p90.toFixed(0)} cm)`),
        h("span", { class: "dim" }, ` · knee angle difference median ${d3.agree.knee.toFixed(0)}° · teal = ${sides[0].meta.name}, pink = ${sides[1].meta.name}`));
    }
    if (fused) {                                  // the fusion depends on the sync: redo it
      fused = null;
      if (fusedOn) { fusedOn = false; toggleFused(); }
    }
  }
  // both cameras' curves on the shared timeline
  function renderChart() {
    if (!sides[1].A) return;
    chartFps = Math.max(sides[0].A.fps, sides[1].A.fps) > 45 ? 60 : 30;
    const N = Math.max(2, Math.round((tMax - tMin) * chartFps) + 1);
    const series = sides.map((s, idx) => {
      const src = s.A.signals[sigKey] || [];
      const data = new Array(N);
      for (let i = 0; i < N; i++) {
        const f = Math.round((tOfAt(idx, tMin + i / chartFps)) * s.A.fps);
        const v = f >= 0 && f < s.A.n_frames ? src[f] : null;
        data[i] = v == null ? null : v;
      }
      return { name: idx ? "B" : "A", color: idx ? COL_B : COL_A, data, dash: idx ? [6, 4] : [] };
    });
    const dec = ["com_h", "speed", "activity"].includes(sigKey) ? 2 : 0;
    chart.set({ series, range: [0, N], fps: chartFps, unit: dec ? "" : "°", decimals: dec });
  }
  // shared timeline is in the LEFT video's time; the right video plays at T + offset
  const tOfAt = (i, t) => (i === 0 ? t : t + offset);
  const tOf = (i) => tOfAt(i, T);
  function computeRange() {
    const d = sides.map((s) => s.A.n_frames / s.A.fps);
    const inter = [Math.max(0, -offset), Math.min(d[0], d[1] - offset)];
    if (sharedOnly && inter[1] - inter[0] >= 0.6) [tMin, tMax] = inter;
    else [tMin, tMax] = [Math.min(0, -offset), Math.max(d[0], d[1] - offset)];
  }

  function buildSide(i, meta, A) {
    const video = h("video", { muted: true, playsinline: true, preload: "auto", src: `/api/videos/${meta.id}/media` });
    const ov = h("canvas", { class: "ov" });
    const hud = h("div", { class: "hud" });
    const idle = h("div", { class: "angles-idle hidden" }, "not recording yet");
    const box = h("div", { class: "player angles-player" }, video, ov, hud, idle);
    const q = A.quality || {};
    const cov = A.segments.length ? Math.round(100 * Math.min(...A.segments.map((s) => s.metrics?.pose_coverage ?? 1))) : null;
    const info = h("div", { class: "angles-info" },
      h("a", { class: "angles-name", href: `#/v/${meta.id}` }, meta.name),
      h("span", { class: "dim" }, `${A.width}×${A.height} · ${Math.round(A.fps)} fps · ${(A.n_frames / A.fps).toFixed(1)} s`),
      cov != null ? h("span", { class: `chip ${cov >= 95 ? "accent" : ""}` }, `pose found ${cov}%`) : null,
      A.cam || A.com_w ? h("span", { class: "chip" }, "moving camera") : h("span", { class: "chip" }, "fixed camera"),
      q.athlete_px ? h("span", { class: "chip" }, `athlete ≈ ${Math.round(q.athlete_px)} px`) : null);
    players.append(h("div", { class: "angles-col" }, info, box));
    Object.assign(sides[i], { meta, A, video, ov, hud, idle, box, ctx: null, els: A.segments.flatMap((sg) => sg.elements || []) });
    new ResizeObserver(() => { const r = box.getBoundingClientRect(); sides[i].ctx = setupHiDPI(ov, r.width, r.height); }).observe(box);
  }

  // what each camera detected, on the shared timeline
  function renderLanes() {
    if (!sides[1].A) return;
    const span = tMax - tMin || 1;
    const pct = (t) => `${((t - tMin) / span) * 100}%`;
    lanes.replaceChildren(...sides.map((s, i) => {
      const sh = i === 0 ? 0 : -offset;                  // own time -> shared time
      const A = s.A;
      const ph = [];
      let cur = null, t0 = 0;
      for (let f = 0; f <= A.phases.length; f++) {
        const p = A.phases[f] || null;
        if (p !== cur) {
          if (cur && PH[cur] && cur !== "F") ph.push(h("i", { class: "ph", style: { left: pct(t0 / A.fps + sh), width: `${((f - t0) / A.fps / span) * 100}%`, background: PH[cur][1] } }));
          cur = p; t0 = f;
        }
      }
      const els = A.segments.flatMap((sg) => (sg.elements || []).map((e) =>
        h("div", { class: `el ${e.type === "hurdle" ? "hurdle" : ""}`, title: `${e.name} · ${((e.frame1 - e.frame0) / A.fps).toFixed(2)} s`,
          style: { left: pct(e.frame0 / A.fps + sh), width: `${Math.max(0.6, ((e.frame1 - e.frame0) / A.fps / span) * 100)}%` } }, e.name)));
      const lands = A.segments.filter((sg) => sg.landing?.frame != null).map((sg) =>
        h("div", { class: "land", style: { left: pct(sg.landing.frame / A.fps + sh) }, title: `Landing: ${sg.landing.result}` }, "▼ " + (sg.landing.result || "")));
      return h("div", { class: "lane" }, h("div", { class: "lane-name" }, s.meta.name),
        h("div", { class: "lane-track" }, h("div", { class: "ph-row" }, ...ph), ...els, ...lands));
    }), h("div", { class: "lane" }, h("div", { class: "lane-name" }), h("div", { class: "lane-track cursor-row" }, h("div", { class: "cursor", style: { left: pct(T) } }))),
    h("div", { class: "dim", style: { fontSize: "12px", marginTop: "6px" } },
      "Blue = in the air · orange = on the hands. Bars are the elements each camera recognised; ▼ = landing verdict. Click the lanes to jump."));
    lanes.onclick = (e) => {
      const r = lanes.querySelector(".lane-track").getBoundingClientRect();
      T = tMin + ((e.clientX - r.left) / r.width) * span;
      syncVideos(true);
    };
  }

  // ---------------------------------------------------------------- shared clock
  function syncVideos(force) {
    for (let i = 0; i < 2; i++) {
      const s = sides[i];
      if (!s.video) continue;
      const t = tOf(i), dur = s.A.n_frames / s.A.fps;
      const inside = t >= 0 && t <= dur;
      s.idle.classList.toggle("hidden", inside);
      s.idle.textContent = t < 0 ? "not recording yet" : "recording ended";
      if (!inside) { if (!s.video.paused) s.video.pause(); continue; }
      s.video.playbackRate = rate;
      if (force || Math.abs(s.video.currentTime - t) > 0.06) s.video.currentTime = t;
      if (playing && s.video.paused) s.video.play().catch(() => {});
      if (!playing && !s.video.paused) s.video.pause();
    }
  }
  function setPlaying(p) {
    playing = p;
    playBtn.replaceChildren(icon(p ? "pause" : "play"));
    if (p && T >= tMax - 0.02) T = tMin;
    syncVideos(true);
  }
  playBtn.addEventListener("click", () => setPlaying(!playing));
  function step(d) {
    setPlaying(false);
    T += d / Math.max(sides[0].A?.fps || 30, sides[1].A?.fps || 30);
    syncVideos(true);
  }
  scrub.addEventListener("input", () => { T = tMin + (scrub.value / 1000) * (tMax - tMin); syncVideos(true); });

  function draw() {
    for (let i = 0; i < 2; i++) {
      const s = sides[i];
      if (!s.ctx) continue;
      const r = s.box.getBoundingClientRect();
      s.ctx.clearRect(0, 0, r.width, r.height);
      const t = tOf(i), A = s.A;
      const f = Math.round(t * A.fps);
      if (f < 0 || f >= A.n_frames) { s.hud.replaceChildren(); continue; }
      const tf = containTransform(A.width, A.height, r.width, r.height);
      drawSkeleton(s.ctx, A.kps[f], tf);
      const el = A.segments.flatMap((sg) => sg.elements || []).find((e) => f >= e.frame0 && f <= e.frame1);
      const p = PH[A.phases[f]];
      s.hud.replaceChildren(h("div", { class: "el" }, el ? el.name : "–"),
        h("div", { class: "st" }, p ? h("span", { class: `phase-badge ${A.phases[f]}` }, p[0]) : null,
          A.kps[f] ? null : h("span", { style: { color: "var(--bad)" } }, "no pose")));
    }
  }

  function loop(ts) {
    if (!alive) return;
    const dt = lastTs ? Math.min(0.1, (ts - lastTs) / 1000) : 0;
    lastTs = ts;
    if (playing) {
      T += dt * rate;
      if (T >= tMax) {
        if (loopOn) { T = tMin; syncVideos(true); } else { T = tMax; setPlaying(false); }
      }
      syncVideos(false);
    }
    scrub.value = Math.round(((T - tMin) / (tMax - tMin || 1)) * 1000);
    timeEl.textContent = `${fmtTime(Math.max(0, T - tMin))} / ${fmtTime(tMax - tMin)}`;
    const cur = lanes.querySelector(".cursor");
    if (cur) cur.style.left = `${((T - tMin) / (tMax - tMin || 1)) * 100}%`;
    chart.setFrame(Math.round((T - tMin) * chartFps));
    draw();
    if (on3d && v3 && d3) v3.setTime((T - d3.video_t0) / d3.rate);
    requestAnimationFrame(loop);
  }

  function onKey(e) {
    if (e.target.closest?.("input, select, textarea")) return;
    if (e.key === " ") { e.preventDefault(); setPlaying(!playing); }
    else if (e.key === "ArrowLeft" && e.shiftKey) { e.preventDefault(); setNudge(nudge - 1); }
    else if (e.key === "ArrowRight" && e.shiftKey) { e.preventDefault(); setNudge(nudge + 1); }
    else if (e.key === "," || e.key === "ArrowLeft") { e.preventDefault(); step(-1); }
    else if (e.key === "." || e.key === "ArrowRight") { e.preventDefault(); step(1); }
  }
  document.addEventListener("keydown", onKey);

  (async () => {
    try {
      const metas = await Promise.all(ids.map((id) => api.get(`/api/videos/${id}`)));
      const anas = await Promise.all(ids.map((id) => api.get(`/api/videos/${id}/analysis`)));
      metas.forEach((m, i) => buildSide(i, m, anas[i]));
      const st = LS.get(setKey());
      if (st) {
        ({ mode = "auto", keyEls = [0, 0], nudge = 0 } = st);
        sharedOnly = st.sharedOnly ?? true; loopOn = st.loopOn ?? true;
      } else {
        const legacy = LS.get(key());                // an offset saved by the earlier version: keep it as fine tuning
        if (typeof legacy === "number") {
          const n = Math.round((legacy - autoMatch().offset) * sides[1].A.fps);
          nudge = Math.abs(n) <= sides[1].A.fps / 2 ? n : 0;
        }
      }
      sharedBtn.classList.toggle("on", sharedOnly);
      loopBtn.classList.toggle("on", loopOn);
      applySync();
      // start where both cameras are recording, at the first element either one saw
      const firstEl = Math.min(...sides.map((s, i) => {
        const e = s.A.segments.flatMap((sg) => sg.elements || [])[0];
        return e ? e.frame0 / s.A.fps - (i === 0 ? 0 : offset) : Infinity;
      }));
      T = isFinite(firstEl) ? Math.max(tMin, Math.min(tMax, firstEl - 0.8)) : tMin;
      syncVideos(true);
      requestAnimationFrame(loop);
    } catch (e) { toast(e.message, "err"); }
  })();

  return () => { alive = false; document.removeEventListener("keydown", onKey); sides.forEach((s) => s.video?.pause()); v3?.dispose(); };
}
