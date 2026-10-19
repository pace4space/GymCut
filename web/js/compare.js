// Side-by-side comparison of two attempts on one shared timeline, synced by matching their motion
// (or at chosen key elements / start / landing), with frame-by-frame fine tuning.
import { api, h, icon, toast, modal, fmtNum, fmtTime, drawSkeleton, drawAngles, containTransform, setupHiDPI, COLORS } from "./common.js";
import { LineChart } from "./charts.js";
import { makeGhost } from "./ghost.js";
import { rateAttempt, skillKey, LIGHT } from "./rating.js";
import { matchMotion } from "./sync.js";

const SIGNALS = [
  ["knee_l", "Left knee"], ["knee_r", "Right knee"], ["hip_l", "Left hip"], ["hip_r", "Right hip"],
  ["elbow_l", "Left elbow"], ["elbow_r", "Right elbow"], ["split", "Leg split"], ["body_line", "Body line"],
  ["rotation", "Rotation"], ["inversion", "Inversion"], ["com_h", "COM height (BL)"], ["speed", "Speed (BL/s)"],
];
const COL_A = "#36c2a0", COL_B = "#ff9628";

export function mountCompare(root, query) {
  let alive = true;
  const qs = new URLSearchParams(query);
  const parse = (v) => { const [vid, seg] = (v || "").split(":"); return { vid, segId: +seg }; };
  const sides = [parse(qs.get("a")), parse(qs.get("b"))];
  const pairKey = `gc_cmp_${qs.get("a")}_${qs.get("b")}`;
  const LSg = (k) => { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } };
  const LSs = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } };
  const saved = LSg(pairKey) || {};
  let align = qs.get("align") || saved.align || "auto";
  let nudge = saved.nudge || 0;            // extra B frames on top of the sync (fine key-frame tuning)
  let keyEls = saved.keyEls || [0, 0];
  let sharedOnly = saved.shared ?? true, loop = saved.loop ?? true;
  let syncInfo = "";
  let sigKey = "inversion";
  let showAngles = false;
  let ghostOn = false, ghostAB = null, ghostBA = null;
  const chartCanvas = h("canvas");
  const tbl = h("div");
  const alignSel = h("select", { style: { height: "30px" } },
    ...[["auto", "Sync: match motion (auto)"], ["key", "Sync: match key elements…"], ["takeoff", "Sync: first element"], ["start", "Sync: segment start"], ["landing", "Sync: landing"]]
      .map(([v, t]) => h("option", { value: v, selected: v === align }, t)));
  const keyBox = h("span", { class: "row hidden", style: { gap: "6px" } });
  const syncChip = h("span", { class: "chip", title: "How the two attempts are lined up" });
  const nudgeEl = h("span", { class: "mono dim", style: { minWidth: "64px", textAlign: "center" } });
  const sharedBtn = h("button", { class: `btn sm ${sharedOnly ? "on" : ""}`, title: "Play only the part both clips cover, so both sides always move together" }, "Shared part");
  const loopBtn = h("button", { class: `btn sm ${loop ? "on" : ""}`, title: "Loop the slow motion" }, icon("loop"), "Loop");
  const sigSel = h("select", { style: { height: "30px" } }, ...SIGNALS.map(([k, t]) => h("option", { value: k, selected: k === sigKey }, t)));
  const speedSel = h("select", { style: { height: "30px" } }, ...[[0.25, "0.25×"], [0.5, "0.5×"], [1, "1×"]].map(([v, t]) => h("option", { value: v, selected: v === 0.5 }, t)));
  const playBtn = h("button", { class: "btn icon", title: "Play/pause (Space)" }, icon("play"));
  const timeEl = h("span", { class: "time mono" }, "");
  const grid = h("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" } });

  root.append(h("div", { class: "page", style: { maxWidth: "1700px" } },
    h("div", { class: "page-head" },
      h("a", { class: "btn sm ghost", href: "javascript:history.back()" }, icon("prev"), "Back"),
      h("div", { class: "grow" }, h("h1", {}, "Compare"), h("p", {}, "Two attempts side by side on one shared timeline. A = green, B = orange."))),
    grid,
    h("div", { class: "controls", style: { margin: "12px 0", flexWrap: "wrap" } },
      h("button", { class: "btn icon", onclick: () => step(-1), title: "Back 1 frame (←)" }, icon("prev")), playBtn,
      h("button", { class: "btn icon", onclick: () => step(1), title: "Forward 1 frame (→)" }, icon("next")),
      timeEl, speedSel, loopBtn, sharedBtn,
      h("span", { class: "sep" }),
      alignSel, keyBox, syncChip,
      h("button", { class: "btn sm", title: "B one frame earlier (Shift+←) – line up the same key frame by eye", onclick: () => setNudge(nudge - 1) }, "B −1f"),
      nudgeEl,
      h("button", { class: "btn sm", title: "B one frame later (Shift+→)", onclick: () => setNudge(nudge + 1) }, "B +1f"),
      h("span", { class: "sep" }),
      h("button", { class: "btn sm", onclick: (e) => { showAngles = !showAngles; e.currentTarget.classList.toggle("on", showAngles); draw(); } }, "Angles"),
      h("button", { class: "btn sm", title: "Overlay each attempt's skeleton on the other video (hip-locked, scaled)", onclick: (e) => { ghostOn = !ghostOn; e.currentTarget.classList.toggle("on", ghostOn); buildGhosts(); draw(); } }, "Ghost overlay"),
      h("span", { class: "spacer" }),
      h("button", { class: "btn sm", onclick: () => swap() }, icon("flip"), "Swap")),
    h("div", { style: { display: "grid", gridTemplateColumns: "minmax(0,1.6fr) minmax(0,1fr)", gap: "12px" } },
      h("div", { class: "card" }, h("div", { class: "card-h" }, h("h3", {}, "Kinematics"),
        h("span", { class: "dim", style: { fontSize: "12px" } }, "click to seek · both curves on the shared timeline"), h("span", { class: "spacer" }), sigSel),
        h("div", { class: "card-b chart-box" }, chartCanvas)),
      h("div", { class: "card" }, h("div", { class: "card-h" }, h("h3", {}, "Metrics")), h("div", { class: "card-b" }, tbl)))));

  const chart = new LineChart(chartCanvas, { height: 220, onSeek: (i) => seekTau(tauMin + i / chartFps) });
  let tauMin = 0, tauMax = 1, chartFps = 30;
  const save = () => LSs(pairKey, { align, nudge, keyEls, shared: sharedOnly, loop });

  async function load() {
    for (const s of sides) {
      s.A = await api.get(`/api/videos/${s.vid}/analysis`);
      s.meta = await api.get(`/api/videos/${s.vid}`);
      s.seg = s.A.segments.find((x) => x.id === s.segId) || s.A.segments[0];
      if (!s.seg) throw new Error(`no segment in ${s.meta.name}`);
      s.fps = s.A.fps;
      // usable range: the segment plus a little context either side (within the video)
      const pad = Math.round(0.5 * s.fps);
      s.lo = Math.max(0, s.seg.start - pad);
      s.hi = Math.min(s.A.n_frames, s.seg.end + pad);
      s.els = (s.seg.elements || []);
    }
    build();
  }

  // B frame for an A frame, from the chosen sync
  function syncFrames() {
    const [a, b] = sides;
    if (align === "auto") {
      // two cameras of the same moment, already synced in Compare angles? use that exactly
      const same = LSg(`gc_angles_${a.vid}_${b.vid}`) ?? (LSg(`gc_angles_${b.vid}_${a.vid}`) != null ? -LSg(`gc_angles_${b.vid}_${a.vid}`) : null);
      let offset, label;
      if (same != null) { offset = same; label = "same moment (Compare angles sync)"; }
      else {
        const m = matchMotion({ A: a.A, lo: a.lo, hi: a.hi }, { A: b.A, lo: b.lo, hi: b.hi });
        if (!m) return fallback("takeoff");
        offset = m.offset;
        label = `motion match ${Math.round(Math.max(0, m.score) * 100)}%`;
      }
      const aAl = a.seg.start;
      return { aAl, bAl: Math.round((aAl / a.fps + offset) * b.fps), label };
    }
    if (align === "key") {
      const ea = a.els[keyEls[0]], eb = b.els[keyEls[1]];
      if (!ea || !eb) return fallback("takeoff");
      return { aAl: ea.frame0, bAl: eb.frame0, label: `${ea.name} ↔ ${eb.name}` };
    }
    return fallback(align);
  }
  function fallback(mode) {
    const f = (s) => mode === "start" ? s.seg.start
      : mode === "landing" ? (s.seg.landing?.frame ?? (s.els.at(-1)?.frame1 ?? s.seg.end))
        : (s.els.find((e) => e.type !== "hurdle")?.frame0 ?? s.seg.start);
    return { aAl: f(sides[0]), bAl: f(sides[1]), label: { start: "segment start", landing: "landing", takeoff: "first element" }[mode] || mode };
  }

  function renderKeyBox() {
    keyBox.classList.toggle("hidden", align !== "key");
    if (align !== "key") return;
    const sel = (i) => {
      const el = h("select", { style: { height: "30px", maxWidth: "190px" } },
        ...sides[i].els.map((e, k) => h("option", { value: k, selected: k === keyEls[i] }, `${i ? "B" : "A"}: ${e.name}`)));
      el.addEventListener("change", () => { keyEls[i] = +el.value; nudge = 0; save(); retime(); seekTau(0); });
      return el;
    };
    keyBox.replaceChildren(sel(0), h("span", { class: "dim" }, "↔"), sel(1));
  }

  function build() {
    grid.innerHTML = "";
    sides.forEach((s, idx) => {
      s.video = h("video", { playsinline: true, muted: true, preload: "auto", src: `/api/videos/${s.vid}/media` });
      s.canvas = h("canvas", { class: "ov" });
      s.idle = h("div", { class: "angles-idle hidden" });
      s.player = h("div", { class: "player", style: { position: "relative" } }, s.video, s.canvas, s.idle);
      grid.append(h("div", { class: "col", style: { gap: "6px", minWidth: 0 } },
        h("div", { class: "row" }, h("span", { class: "chip", style: { color: idx ? COL_B : COL_A, borderColor: idx ? COL_B : COL_A } }, idx ? "B" : "A"),
          h("b", { style: { whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" } }, s.seg.label),
          h("span", { class: "dim", style: { whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" } }, `· ${s.meta.name}`),
          h("span", { class: "spacer" }), h("a", { class: "btn sm ghost", href: `#/v/${s.vid}` }, "Open")),
        s.player));
      s.video.addEventListener("loadedmetadata", () => { resize(s); seekTau(tau); });
      new ResizeObserver(() => resize(s)).observe(s.player);
    });
    renderKeyBox();
    retime();
    seekTau(0);
    renderTable();
  }

  function buildGhosts() {
    ghostAB = ghostBA = null;
    if (!ghostOn || !sides[0].A || !sides[1].A) return;
    const a = { A: sides[0].A, seg: sides[0].seg }, b = { A: sides[1].A, seg: sides[1].seg };
    ghostAB = makeGhost(a, b, { mode: "pose", alignFrames: [sides[0].al, sides[1].al] });   // B drawn on A's video
    ghostBA = makeGhost(b, a, { mode: "pose", alignFrames: [sides[1].al, sides[0].al] });   // A drawn on B's video
  }

  function retime() {
    const sf = syncFrames();
    sides[0].al = sf.aAl;
    sides[1].al = sf.bAl + nudge;
    syncInfo = sf.label;
    // each side's usable span on the shared clock (tau = 0 at A's sync frame)
    const span = sides.map((s) => [(s.lo - s.al) / s.fps, (s.hi - 1 - s.al) / s.fps]);
    const inter = [Math.max(span[0][0], span[1][0]), Math.min(span[0][1], span[1][1])];
    const shared = inter[1] - inter[0] >= 0.6;
    [tauMin, tauMax] = sharedOnly && shared ? inter : [Math.min(span[0][0], span[1][0]), Math.max(span[0][1], span[1][1])];
    syncChip.textContent = `${syncInfo}${sharedOnly && shared ? ` · shared ${(tauMax - tauMin).toFixed(1)} s` : ""}`;
    syncChip.style.color = /motion match (\d+)/.test(syncInfo) && +syncInfo.match(/(\d+)%/)[1] < 50 ? "var(--warn)" : "";
    nudgeEl.textContent = nudge ? `${nudge > 0 ? "+" : ""}${nudge}f (${(nudge / sides[1].fps).toFixed(2)} s)` : "±0f";
    buildGhosts();
    chartFps = Math.max(sides[0].fps, sides[1].fps) > 45 ? 60 : 30;
    renderChart();
  }

  function setNudge(n) {
    nudge = n;
    save();
    const keep = tau;
    retime();
    seekTau(keep);
  }

  const rawFrame = (s, t) => s.al + t * s.fps;
  const frameOf = (s, t) => Math.max(s.lo, Math.min(s.hi - 1, Math.round(rawFrame(s, t))));
  const inRange = (s, t) => { const f = rawFrame(s, t); return f >= s.lo && f <= s.hi - 1; };
  let tau = 0;
  function seekTau(t) {
    tau = Math.max(tauMin, Math.min(tauMax, t));
    for (const s of sides) if (s.video) s.video.currentTime = (frameOf(s, tau) + 0.3) / s.fps;
    draw();
  }
  function step(d) { pause(); seekTau(tau + d / Math.max(sides[0].fps, sides[1].fps)); }
  let playing = false;
  function pause() { playing = false; sides.forEach((s) => s.video?.pause()); playBtn.replaceChildren(icon("play")); }
  function play() {
    if (tau >= tauMax - 1e-3) seekTau(tauMin);
    playing = true;
    const rate = +speedSel.value;
    sides.forEach((s) => { s.video.playbackRate = rate; if (inRange(s, tau)) s.video.play().catch(() => {}); });
    playBtn.replaceChildren(icon("pause"));
  }
  playBtn.addEventListener("click", () => (playing ? pause() : play()));
  speedSel.addEventListener("change", () => sides.forEach((s) => (s.video.playbackRate = +speedSel.value)));
  alignSel.addEventListener("change", () => {
    align = alignSel.value;
    nudge = 0;
    if (align === "key") {                       // start from the elements the motion sync pairs up
      const m = (() => { const a0 = align; align = "auto"; const r = syncFrames(); align = a0; return r; })();
      const bAt = (fa) => m.bAl + ((fa - m.aAl) / sides[0].fps) * sides[1].fps;
      const ka = Math.max(0, sides[0].els.findIndex((e) => e.type !== "hurdle"));
      const fa = sides[0].els[ka]?.frame0 ?? sides[0].seg.start;
      let kb = 0, bestD = Infinity;
      sides[1].els.forEach((e, k) => { const d = Math.abs(e.frame0 - bAt(fa)); if (d < bestD) { bestD = d; kb = k; } });
      keyEls = [ka, kb];
    }
    save(); pause(); renderKeyBox(); retime(); seekTau(0);
  });
  sharedBtn.addEventListener("click", () => { sharedOnly = !sharedOnly; sharedBtn.classList.toggle("on", sharedOnly); save(); const k = tau; retime(); seekTau(k); });
  loopBtn.addEventListener("click", () => { loop = !loop; loopBtn.classList.toggle("on", loop); save(); });
  sigSel.addEventListener("change", () => { sigKey = sigSel.value; renderChart(); });

  // master clock = wall time while playing; both videos are nudged to stay in sync
  let lastT = performance.now();
  const timer = setInterval(() => {
    if (!alive || !sides[0].video) return;
    const now = performance.now();
    if (playing) {
      tau += ((now - lastT) / 1000) * +speedSel.value;
      if (tau >= tauMax) {
        if (loop) { tau = tauMin; sides.forEach((s) => { s.video.currentTime = (frameOf(s, tau) + 0.3) / s.fps; }); }
        else { tau = tauMax; pause(); }
      }
      for (const s of sides) {
        const target = (frameOf(s, tau) + 0.3) / s.fps;
        if (!inRange(s, tau)) { if (!s.video.paused) s.video.pause(); if (Math.abs(s.video.currentTime - target) > 0.04) s.video.currentTime = target; }
        else {
          if (s.video.paused) s.video.play().catch(() => {});
          if (Math.abs(s.video.currentTime - target) > 0.08) s.video.currentTime = target;
        }
      }
      draw();
    }
    lastT = now;
  }, 33);

  function resize(s) {
    const r = s.player.getBoundingClientRect();
    s.ctx = setupHiDPI(s.canvas, r.width, r.height);
    s.box = r;
    draw();
  }

  function draw() {
    timeEl.textContent = `${tau >= 0 ? "+" : "−"}${Math.abs(tau).toFixed(2)} s`;
    for (const s of sides) {
      if (!s.ctx || !s.A) continue;
      const r = s.box;
      s.ctx.clearRect(0, 0, r.width, r.height);
      const live = inRange(s, tau);
      s.idle.classList.toggle("hidden", live);
      s.idle.textContent = rawFrame(s, tau) < s.lo ? "starts later" : "already finished";
      const f = frameOf(s, tau);
      const k = s.A.kps[f];
      const tf = containTransform(s.A.width, s.A.height, r.width, r.height);
      const g = s === sides[0] ? ghostAB : ghostBA;
      if (g) { const gk = g(f); if (gk) drawSkeleton(s.ctx, gk, tf, { color: s === sides[0] ? COL_B : COL_A, alpha: 0.55 }); }
      if (k) { drawSkeleton(s.ctx, k, tf); if (showAngles) drawAngles(s.ctx, k, tf); }
      const ph = s.A.phases[f];
      if (ph && ph !== "-") {
        s.ctx.font = "700 12px Inter, system-ui";
        s.ctx.fillStyle = { A: "#6aa8ff", H: "#ffb347", F: "#3a4350" }[ph];
        s.ctx.fillRect(10, 10, 58, 20);
        s.ctx.fillStyle = ph === "F" ? "#dfe6ee" : "#111";
        s.ctx.fillText({ A: "AIR", H: "HANDS", F: "FEET" }[ph], 16, 25);
      }
      const el = s.els.find((e) => f >= e.frame0 && f <= e.frame1);
      if (el) {
        s.ctx.font = "700 13px Inter, system-ui";
        s.ctx.fillStyle = "rgba(0,0,0,.55)";
        const w = s.ctx.measureText(el.name).width + 12;
        s.ctx.fillRect(10, 34, w, 20);
        s.ctx.fillStyle = "#fff";
        s.ctx.fillText(el.name, 16, 49);
      }
    }
    chart.setFrame(Math.round((tau - tauMin) * chartFps));
  }

  function renderChart() {
    const N = Math.max(2, Math.round((tauMax - tauMin) * chartFps) + 1);
    const series = sides.map((s, idx) => {
      const src = s.A.signals[sigKey];
      const r0 = sigKey === "rotation" ? (s.A.signals.rotation[s.seg.start] ?? 0) : 0;
      const data = new Array(N);
      for (let i = 0; i < N; i++) {
        const f = Math.round(rawFrame(s, tauMin + i / chartFps));
        const v = f >= s.lo && f < s.hi ? src[f] : null;
        data[i] = v === null || v === undefined ? null : sigKey === "rotation" ? Math.abs(v - r0) : v;
      }
      return { name: idx ? "B" : "A", color: idx ? COL_B : COL_A, data, dash: idx ? [6, 4] : [] };
    });
    const dec = ["com_h", "speed"].includes(sigKey) ? 2 : 0;
    chart.set({ series, range: [0, N], fps: chartFps, unit: dec ? "" : "°", decimals: dec });
    chart.setFrame(Math.round((tau - tauMin) * chartFps));
  }

  function renderTable() {
    const [a, b] = sides.map((s) => s.seg.metrics || {});
    const rows = [
      ["Duration", "duration", 2, "s"], ["Rotation", "rotation_total_deg", 0, "°", true], ["Max flight", "max_flight_time", 2, "s"],
      ["Height", "max_height_m", 2, "m"], ["Max split", "max_split", 0, "°"], ["Top speed", "max_speed_bl", 1, "BL/s"],
      ["Approach", "approach_speed_bl", 1, "BL/s"], ["Max inversion", "max_inversion", 0, "°"],
    ];
    const t = h("table", { style: { width: "100%", borderCollapse: "collapse", fontSize: "13px" } },
      h("tr", { class: "dim" }, h("th", { style: { textAlign: "left", fontWeight: 500 } }, ""),
        h("th", { style: { color: COL_A, textAlign: "right" } }, "A"), h("th", { style: { color: COL_B, textAlign: "right" } }, "B"),
        h("th", { style: { textAlign: "right", fontWeight: 500 } }, "B − A")));
    for (const [label, key, nd, unit, abs] of rows) {
      let va = a[key], vb = b[key];
      if (abs) { va = va == null ? va : Math.abs(va); vb = vb == null ? vb : Math.abs(vb); }
      const d = va != null && vb != null ? vb - va : null;
      t.append(h("tr", { style: { borderTop: "1px solid var(--line)" } },
        h("td", { style: { padding: "6px 0" } }, label),
        h("td", { class: "mono", style: { textAlign: "right" } }, fmtNum(va, nd, " " + unit)),
        h("td", { class: "mono", style: { textAlign: "right" } }, fmtNum(vb, nd, " " + unit)),
        h("td", { class: "mono", style: { textAlign: "right", color: d > 0 ? "var(--good)" : d < 0 ? "var(--warn)" : "" } },
          d == null ? "–" : `${d > 0 ? "+" : ""}${d.toFixed(nd)}`)));
    }
    const land = sides.map((s) => s.seg.landing?.result || "–");
    t.append(h("tr", { style: { borderTop: "1px solid var(--line)" } }, h("td", { style: { padding: "6px 0" } }, "Landing"),
      h("td", { style: { textAlign: "right" } }, land[0]), h("td", { style: { textAlign: "right" } }, land[1]), h("td")));
    const notes = sides.map((s) => (s.seg.notes || []).filter((n) => n.level === "warn" || n.level === "bad").length);
    t.append(h("tr", { style: { borderTop: "1px solid var(--line)" } }, h("td", { style: { padding: "6px 0" } }, "Form notes"),
      h("td", { class: "mono", style: { textAlign: "right" } }, notes[0]), h("td", { class: "mono", style: { textAlign: "right" } }, notes[1]),
      h("td", { class: "mono", style: { textAlign: "right" } }, `${notes[1] - notes[0] > 0 ? "+" : ""}${notes[1] - notes[0]}`)));
    tbl.replaceChildren(t);
  }

  function swap() {
    const q = new URLSearchParams({ a: `${sides[1].vid}:${sides[1].seg.id}`, b: `${sides[0].vid}:${sides[0].seg.id}`, align });
    location.hash = `#/compare?${q}`;
  }

  function onKey(e) {
    if (e.target.closest?.("input, select, textarea")) return;
    if (e.key === " ") { e.preventDefault(); playing ? pause() : play(); }
    else if (e.key === "ArrowLeft" && e.shiftKey) { e.preventDefault(); setNudge(nudge - 1); }
    else if (e.key === "ArrowRight" && e.shiftKey) { e.preventDefault(); setNudge(nudge + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); step(-1); }
    else if (e.key === "ArrowRight") { e.preventDefault(); step(1); }
  }
  document.addEventListener("keydown", onKey);

  load().catch((e) => toast(e.message, "err"));
  return () => { alive = false; clearInterval(timer); document.removeEventListener("keydown", onKey); sides.forEach((s) => s.video?.pause()); };
}

// Picker: choose another skill to compare with the given one
// Picker: choose another attempt to compare with - thumbnail grid, same skill first
export async function comparePicker(vid, segId) {
  const all = await api.get("/api/segments");
  const me = all.find((s) => s.vid === vid && s.id === segId);
  const others = all.filter((s) => !(s.vid === vid && s.id === segId));
  const sameAth = (s) => (s.athlete || "") === (me?.athlete || "");
  const search = h("input", { type: "text", placeholder: "Filter by skill, video, athlete or date…", style: { width: "100%" } });
  const box = h("div", { style: { maxHeight: "62vh", overflow: "auto", marginTop: "6px" } });
  const card = (s) => {
    const r = rateAttempt(s, all.filter((x) => (x.athlete || "") === (s.athlete || "")));
    return h("a", { class: "film", href: `#/compare?a=${vid}:${segId}&b=${s.vid}:${s.id}`, onclick: () => m.close(), style: { textDecoration: "none" }, title: `${s.label} · ${s.video}` },
      h("div", { class: "film-img", style: { backgroundImage: `url(/api/videos/${s.vid}/segthumb/${s.id}.jpg)` } },
        r.overall ? h("span", { class: "film-dot", style: { background: LIGHT[r.overall] } }) : null),
      h("div", { class: "film-lbl" }, s.label),
      h("div", { class: "film-sub" }, `${s.athlete || "Unassigned"} · ${s.recorded}`),
      h("div", { class: "film-sub", style: { paddingTop: 0 } }, [s.metrics?.max_flight_time ? `air ${s.metrics.max_flight_time.toFixed(2)}s` : `${(s.t1 - s.t0).toFixed(1)}s`, s.landing?.result].filter(Boolean).join(" · ")));
  };
  const render = () => {
    const q = search.value.toLowerCase();
    const items = others.filter((s) => !q || [s.label, s.video, s.athlete || "", s.recorded].join(" ").toLowerCase().includes(q));
    const same = items.filter((s) => me && skillKey(s) === skillKey(me));
    const rest = items.filter((s) => !same.includes(s)).sort((a, b) => (sameAth(b) - sameAth(a)) || (a.recorded < b.recorded ? 1 : -1));
    box.replaceChildren(...[
      same.length ? h("div", { class: "pick-sec" }, `Same skill (${same.length})`) : null,
      same.length ? h("div", { class: "pick-grid" }, ...same.map(card)) : null,
      rest.length ? h("div", { class: "pick-sec" }, same.length ? "Other attempts" : "All attempts") : null,
      rest.length ? h("div", { class: "pick-grid" }, ...rest.slice(0, 120).map(card)) : null,
      !items.length ? h("div", { class: "dim" }, "No matching attempts.") : null].filter(Boolean));
  };
  search.addEventListener("input", render);
  const m = modal(`Compare “${me?.label || "this attempt"}” with…`, h("div", {}, search, box));
  m.el.style.width = "min(980px, 100%)";
  render();
  search.focus();
}
