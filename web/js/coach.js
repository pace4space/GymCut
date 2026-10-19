// Coach mode: courtside big-screen view for talking an athlete through each attempt.
// Hands-free: follows new attempts as they are analysed (uploads or live phone clips),
// loops them in slow motion with the skeleton and a ghost of the athlete's best
// attempt of the same skill, and shows traffic-light tiles to talk about.
import { api, h, icon, toast, fmtNum, drawSkeleton, containTransform, setupHiDPI, previewFile } from "./common.js";
import { rateAttempt, bestOf, skillKey, LIGHT } from "./rating.js";
import { makeGhost, loadAnalysis, travelDir, alignFrame } from "./ghost.js";
import { LineChart } from "./charts.js";
import { matchMotion } from "./sync.js";

const GRAPH_SIGNALS = [["inversion", "Inversion"], ["com_h", "Hip height (BL)"], ["knee_l", "Left knee"], ["knee_r", "Right knee"],
  ["hip_l", "Left hip"], ["hip_r", "Right hip"], ["elbow_l", "Left elbow"], ["elbow_r", "Right elbow"], ["speed", "Speed (BL/s)"]];

const GHOST_COLOR = "#ff4fd8";
const LS = { get: (k) => { try { return localStorage.getItem(k); } catch { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* */ } } };

export function mountCoach(root, query) {
  let alive = true;
  const qs = new URLSearchParams(query || "");
  let athlete = qs.has("athlete") ? qs.get("athlete") : (LS.get("gc_coach_athlete") ?? "__all");
  let date = qs.get("date") || "__latest";
  let follow = true, ghostOn = true, speed = 0.5;
  let all = [], list = [], idx = -1, cur = null, A = null, ghostFn = null, ghostLabel = "";
  let lastCount = 0, holdUntil = 0;

  const athSel = h("select", { style: { height: "34px" } });
  const dateSel = h("select", { style: { height: "34px" } });
  const followBtn = h("button", { class: "btn on", title: "Jump to each new attempt automatically" }, icon("live"), "Follow new");
  const ghostBtn = h("button", { class: "btn on", title: "Overlay the athlete's best attempt of this skill (G)" }, "Ghost of best");
  const btn3d = h("button", { class: "btn", title: "Interactive 3D view next to the video: pause, slow down and orbit around the athlete (3)" }, "3D view");
  const layoutBtn = h("button", { class: "btn hidden", title: "Split video | 3D, or a big 3D view (L)" }, "3D only");
  let view3d = false, big3d = false, v3 = null, v3key = null;
  const box3d = h("div", { class: "coach-3d hidden" });
  // motion pattern: this attempt vs the personal best, synced by matching their motion
  let graphOn = LS.get("gc_coach_graph") !== "0";
  let gSig = LS.get("gc_coach_graph_sig") || "inversion";
  let refSync = null;              // { RA, rseg, aAl, rAl, label }
  let ghostToken = 0;
  const graphBtn = h("button", { class: `btn ${graphOn ? "on" : ""}`, title: "Motion pattern graph: this attempt vs the personal best (M)" }, "Graph");
  const gSigSel = h("select", { style: { height: "30px" } }, ...GRAPH_SIGNALS.map(([k, t]) => h("option", { value: k, selected: k === gSig }, t)));
  const gChip = h("span", { class: "chip" });
  const gCanvas = h("canvas");
  const graphCard = h("div", { class: `coach-graph ${graphOn ? "" : "hidden"}` },
    h("div", { class: "row", style: { gap: "10px", marginBottom: "4px" } },
      h("b", {}, "Motion pattern"),
      h("span", { class: "legend-a" }, "this attempt"), h("span", { class: "legend-b" }, "best"),
      gChip, h("span", { class: "spacer" }), h("span", { class: "dim", style: { fontSize: "12px" } }, "click to jump"), gSigSel),
    h("div", { class: "chart-box" }, gCanvas));
  const gChart = new LineChart(gCanvas, { height: 130, onSeek: (i) => seekGraph(i) });
  const pose3d = new Map();          // "vid:seg" -> promise of /pose3d data
  const speedCtl = h("div", { class: "seg-ctl" }, ...[[0.25, "¼×"], [0.5, "½×"], [1, "1×"]].map(([v, t]) =>
    h("button", { class: v === speed ? "on" : "", onclick: (e) => { speed = v; video.playbackRate = v; [...speedCtl.children].forEach((b) => b.classList.toggle("on", b === e.currentTarget)); } }, t)));
  const video = h("video", { playsinline: true, muted: true, preload: "auto" });
  const ov = h("canvas", { class: "ov" });
  const hud = h("div", { class: "hud" });
  const player = h("div", { class: "player" }, video, ov, hud);
  const side = h("div", { class: "coach-side" });
  const strip = h("div", { class: "filmstrip" });

  root.append(h("div", { class: "coach" },
    h("div", { class: "coach-bar" },
      h("span", { class: "title" }, "Coach"), athSel, dateSel, followBtn, ghostBtn, speedCtl, btn3d, layoutBtn, graphBtn,
      h("span", { class: "spacer" }),
      h("span", { class: "dim", style: { fontSize: "12.5px" } }, "Space pause · , . step · ←/→ attempts · G ghost · M graph · 3 3D · F full screen"),
      h("button", { class: "btn icon", title: "Full screen (F)", onclick: () => fullscreen() }, icon("skipf")),
      h("a", { class: "btn", href: "#/" }, "Exit")),
    h("div", { class: "coach-left" }, h("div", { class: "coach-stage" }, player, box3d), graphCard), side, strip));
  graphBtn.addEventListener("click", () => toggleGraph());
  gSigSel.addEventListener("change", () => { gSig = gSigSel.value; LS.set("gc_coach_graph_sig", gSig); renderGraph(); });
  function toggleGraph() {
    graphOn = !graphOn;
    LS.set("gc_coach_graph", graphOn ? "1" : "0");
    graphBtn.classList.toggle("on", graphOn);
    graphCard.classList.toggle("hidden", !graphOn);
    renderGraph();
  }
  function seekGraph(i) {
    if (!A || !cur?.seg) return;
    paused = true;
    holdUntil = 0;
    video.pause();
    video.currentTime = (cur.seg.start + i + 0.3) / A.fps;
  }
  function renderGraph() {
    if (!graphOn || !A || !cur?.seg) return;
    const sg = cur.seg, N = Math.max(2, sg.end - sg.start);
    const mine = A.signals[gSig] || [];
    const series = [{ name: "this", color: "#36c2a0", data: Array.from({ length: N }, (_, i) => mine[sg.start + i] ?? null) }];
    if (refSync) {
      const { RA, rseg, aAl, rAl } = refSync;
      const src = RA.signals[gSig] || [];
      const pad = Math.round(0.5 * RA.fps);
      series.push({ name: "best", color: GHOST_COLOR, dash: [6, 4], data: Array.from({ length: N }, (_, i) => {
        const rf = Math.round(rAl + ((sg.start + i - aAl) / A.fps) * RA.fps);
        return rf >= rseg.start - pad && rf < rseg.end + pad ? src[rf] ?? null : null;
      }) });
    }
    const dec = ["com_h", "speed"].includes(gSig) ? 2 : 0;
    gChart.set({ series, range: [0, N], fps: A.fps, unit: dec ? "" : "°", decimals: dec });
    gChip.textContent = refSync ? refSync.label : "first attempt of this skill – no best yet";
    gChip.style.color = refSync && /match (\d+)/.test(refSync.label) && +refSync.label.match(/(\d+)%/)[1] < 50 ? "var(--warn)" : "";
  }

  followBtn.addEventListener("click", () => { follow = !follow; followBtn.classList.toggle("on", follow); });
  ghostBtn.addEventListener("click", () => toggleGhost());
  athSel.addEventListener("change", () => { athlete = athSel.value; LS.set("gc_coach_athlete", athlete); date = "__latest"; idx = -1; refresh(true); });
  dateSel.addEventListener("change", () => { date = dateSel.value; idx = -1; refresh(true); });
  btn3d.addEventListener("click", () => toggle3d());
  layoutBtn.addEventListener("click", () => { big3d = !big3d; apply3d(); });
  async function toggle3d() {
    if (!cur) return;
    if (!cur.solved3d) {
      const j = await api.post(`/api/videos/${cur.vid}/replay3d`, { segment: cur.id, ghost: true });
      toast(`3D solve queued (${j.state}) – Unreal reconstructs the body, a few minutes per attempt`, "ok");
      btn3d.textContent = "3D (queued…)";
      return;
    }
    view3d = !view3d;
    apply3d();
  }
  function getPose3d(s) {
    const key = `${s.vid}:${s.id}`;
    if (!pose3d.has(key)) pose3d.set(key, api.get(`/api/videos/${s.vid}/pose3d/${s.id}`).catch(() => null));
    return pose3d.get(key);
  }
  async function apply3d() {
    const on = view3d && !!cur?.solved3d;
    btn3d.classList.toggle("on", on);
    btn3d.textContent = cur?.solved3d ? "3D view" : "Make 3D";
    layoutBtn.classList.toggle("hidden", !on);
    layoutBtn.textContent = big3d ? "Split view" : "3D only";
    box3d.classList.toggle("hidden", !on);
    const stage = root.querySelector(".coach-stage");
    stage.classList.toggle("split", on && !big3d);
    stage.classList.toggle("big3d", on && big3d);
    if (!on) return;
    if (!v3) {
      const { createView3D } = await import("./view3d.js");
      v3 = createView3D(box3d);
    }
    const key = `${cur.vid}:${cur.id}`;
    if (v3key === key) return;
    v3key = key;
    const d = await getPose3d(cur);
    if (!d || v3key !== key) return;
    v3.setData(d, { screenDir: travelDir(A, cur.seg) || 1 });
    v3.setGhost(ghostOn);
    v3.setHud([
      h("span", { class: "v3d-chip a" }, "athlete"),
      d.ghost ? h("span", { class: "v3d-chip g" }, `best · ${d.ghost.recorded || ""}`) : null,
      cur.replay3d ? h("button", { class: "btn sm ghost", title: "The Unreal-rendered replay video", onclick: () => previewFile({ url: cur.replay3d }) }, "Unreal render") : null]);
  }
  function toggleGhost() { ghostOn = !ghostOn; ghostBtn.classList.toggle("on", ghostOn); setupGhost(); v3?.setGhost(ghostOn); }
  function fullscreen() { document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen?.(); }

  async function refresh(force = false) {
    if (!alive) return;
    try {
      const [ath, segs] = await Promise.all([api.get("/api/athletes"), api.get("/api/segments")]);
      all = segs;
      athSel.replaceChildren(h("option", { value: "__all" }, "All athletes"),
        ...ath.filter((a) => a.name).map((a) => h("option", { value: a.name }, `${a.name} (${a.attempts})`)),
        ...(ath.some((a) => !a.name) ? [h("option", { value: "" }, "Unassigned")] : []));
      athSel.value = athlete;
      if (athSel.value !== athlete) { athlete = "__all"; athSel.value = "__all"; }
      const mine = all.filter((s) => athlete === "__all" || (s.athlete || "") === athlete);
      const dates = [...new Set(mine.map((s) => s.recorded))].sort().reverse();
      dateSel.replaceChildren(...dates.map((d, i) => h("option", { value: d }, i === 0 ? `${d} (latest)` : d)), h("option", { value: "__every" }, "All sessions"));
      const want = date === "__latest" ? dates[0] : date;
      dateSel.value = want || "__every";
      list = mine.filter((s) => date === "__every" || s.recorded === (want || s.recorded));
      const grew = list.length > lastCount && lastCount > 0;
      lastCount = list.length;
      if (cur) {
        const fresh = list.find((x) => x.vid === cur.vid && x.id === cur.id);
        if (fresh && fresh.replay3d && !cur.replay3d) cur.replay3d = fresh.replay3d;
        if (fresh && fresh.solved3d && !cur.solved3d) { cur.solved3d = true; btn3d.textContent = "3D view"; toast("3D ready – press 3", "ok"); }
      }
      renderStrip();
      if (!list.length) { side.replaceChildren(h("div", { class: "empty" }, h("h3", {}, "No attempts yet"), h("p", {}, "Upload videos or stream from a phone – attempts appear here automatically."))); }
      else if (force || idx < 0) select(list.length - 1);
      else if (grew && follow) { select(list.length - 1); toast("New attempt", "ok"); }
    } catch (e) { /* server busy */ }
  }

  function renderStrip() {
    strip.replaceChildren(...list.map((s, i) => {
      const r = rateAttempt(s, all.filter((x) => (x.athlete || "") === (s.athlete || "")));
      return h("button", { class: `film ${i === idx ? "sel" : ""}`, onclick: () => { follow = false; followBtn.classList.remove("on"); select(i); } },
        h("div", { class: "film-img", style: { backgroundImage: `url(/api/videos/${s.vid}/segthumb/${s.id}.jpg)` } },
          h("span", { class: "film-num" }, i + 1), r.overall ? h("span", { class: "film-dot", style: { background: LIGHT[r.overall] } }) : null),
        h("div", { class: "film-lbl" }, s.label), h("div", { class: "film-sub" }, s.landing?.result || `${fmtNum(s.metrics?.duration, 1)}s`));
    }));
    strip.querySelector(".film.sel")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  async function select(i) {
    if (i < 0 || i >= list.length) return;
    idx = i;
    const s = list[i];
    const sameVid = cur && cur.vid === s.vid;
    cur = s;
    A = await loadAnalysis(s.vid);
    cur.seg = A.segments.find((x) => x.id === s.id) || A.segments[0];
    if (!sameVid) video.src = `/api/videos/${s.vid}/media`;
    else start();
    renderStrip(); renderSide(); setupGhost(); apply3d();
  }
  video.addEventListener("loadedmetadata", () => { resize(); start(); });
  function start() {
    if (!cur?.seg) return;
    video.currentTime = (cur.seg.start + 0.3) / A.fps;
    video.playbackRate = speed;
    video.play().catch(() => {});
  }

  async function setupGhost() {
    const token = ++ghostToken;
    ghostFn = null; ghostLabel = ""; refSync = null;
    if (!cur?.seg) return;
    const pool = all.filter((x) => (x.athlete || "") === (cur.athlete || ""));
    const ref = bestOf(cur, pool);
    if (!ref) { renderGraph(); return; }
    const RA = await loadAnalysis(ref.vid);
    const rseg = RA.segments.find((x) => x.id === ref.id);
    if (token !== ghostToken || !rseg) { if (token === ghostToken) renderGraph(); return; }
    // same motion sync as Compare: match the curves of both attempts (segment + a little context)
    const win = (an, sg) => ({ A: an, lo: Math.max(0, sg.start - Math.round(0.5 * an.fps)), hi: Math.min(an.n_frames, sg.end + Math.round(0.5 * an.fps)) });
    const m = matchMotion(win(A, cur.seg), win(RA, rseg));
    if (m && m.score >= 0.3) {
      const aAl = cur.seg.start;
      refSync = { RA, rseg, aAl, rAl: Math.round((aAl / A.fps + m.offset) * RA.fps), label: `best ${ref.recorded} · motion match ${Math.round(m.score * 100)}%` };
    } else {
      refSync = { RA, rseg, aAl: alignFrame(cur.seg, "takeoff"), rAl: alignFrame(rseg, "takeoff"), label: `best ${ref.recorded} · aligned at takeoff` };
    }
    if (ghostOn) {
      ghostFn = makeGhost({ A, seg: cur.seg }, { A: RA, seg: rseg }, { mode: "pose", alignFrames: [refSync.aAl, refSync.rAl] });
      ghostLabel = `best: ${ref.recorded}${ref.metrics?.max_flight_time ? ` · air ${ref.metrics.max_flight_time.toFixed(2)}s` : ""}`;
    }
    renderGraph();
  }

  function tile(label, value, unit, level, sub) {
    return h("div", { class: "tile", style: { borderLeftColor: level ? LIGHT[level] : "var(--line-2)" } },
      h("div", { class: "k" }, label), h("div", { class: "v" }, value ?? "–", unit && value != null ? h("small", {}, unit) : null),
      sub ? h("div", { class: "s" }, sub) : null);
  }

  function renderSide() {
    const s = cur;
    const pool = all.filter((x) => (x.athlete || "") === (s.athlete || ""));
    const r = rateAttempt(s, pool);
    const get = (k) => r.items.find((it) => it.key === k);
    const m = s.metrics || {};
    const same = pool.filter((p) => skillKey(p) === skillKey(s));
    const others = same.filter((p) => !(p.vid === s.vid && p.id === s.id));
    const bestFlight = Math.max(0, ...others.map((p) => p.metrics?.max_flight_time || 0));
    const tech = r.items.filter((it) => ["knees", "arms", "split", "tuck", "line", "chest", "kneegap", "twist"].includes(it.key))
      .sort((a, b) => ({ bad: 0, warn: 1, good: 2 }[a.level] - { bad: 0, warn: 1, good: 2 }[b.level]));
    const salto = (s.elements || []).find((e) => e.type === "salto");
    side.replaceChildren(...[
      h("div", {}, h("div", { class: "coach-skill" }, s.label),
        h("div", { class: "coach-when" }, `${s.athlete || "Unassigned"} · ${s.recorded} · attempt ${idx + 1} of ${list.length} · ${same.length} of this skill`)),
      r.overall ? h("div", { class: "coach-overall" }, h("span", { class: "light", style: { background: LIGHT[r.overall] } }),
        { good: "Clean", warn: "Work on it", bad: "Needs fixing" }[r.overall]) : null,
      h("div", { class: "tiles" },
        tile("Flight", m.max_flight_time ? m.max_flight_time.toFixed(2) : null, "s", get("flight")?.level,
          bestFlight ? `personal best ${bestFlight.toFixed(2)} s` : "first of this skill"),
        s.m3d?.hip_peak_cm ? tile("Hip height · 3D", Math.round(s.m3d.hip_peak_cm), "cm", get("height")?.level, get("height")?.hint)
          : tile("Height", m.max_height_m ? Math.round(m.max_height_m * 100) : null, "cm", get("height")?.level),
        tile("Rotation", salto ? `${salto.rotations ?? (Math.abs(salto.rotation_deg) / 360).toFixed(1)}` : `${Math.round(Math.abs(m.rotation_total_deg || 0))}°`,
          salto ? "rot" : "", null, salto?.shape ? `${salto.shape} shape` : null),
        tile("Landing", s.landing?.result || null, "", get("landing")?.level, s.landing?.knee_min ? `knees ${s.landing.knee_min}°` : null),
        ...tech.slice(0, 4).map((it) => tile(it.d3 ? `${it.label} · 3D` : it.label, it.value, it.unit, it.level, it.hint))),
      (s.notes || []).length ? h("div", { class: "coach-notes" }, ...s.notes.slice(0, 4).map((n) =>
        h("div", { class: "light-row", style: { fontSize: "15px" } }, h("span", { class: "light", style: { background: n.level === "bad" ? LIGHT.bad : LIGHT.warn } }), n.text))) : null].filter(Boolean));
  }

  // ---------- drawing / looping ----------
  let ctx = null;
  function resize() { const r = player.getBoundingClientRect(); ctx = setupHiDPI(ov, r.width, r.height); }
  new ResizeObserver(resize).observe(player);
  const tick = setInterval(() => {
    if (!alive || !A || !cur?.seg || !ctx) return;
    const d3 = v3?.data;
    if (view3d && d3 && v3key === `${cur.vid}:${cur.id}`) v3.setTime((video.currentTime - d3.video_t0) / d3.rate);
    const s = cur.seg;
    const f = Math.max(0, Math.min(A.n_frames - 1, Math.floor(video.currentTime * A.fps + 0.01)));
    if (graphOn) gChart.setFrame(f - s.start);
    if (f >= s.end - 1 && !video.paused) {      // hold the last frame briefly, then loop
      video.pause();
      holdUntil = performance.now() + 900;
    }
    if (holdUntil && performance.now() > holdUntil && !paused) { holdUntil = 0; start(); }
    const r = player.getBoundingClientRect();
    ctx.clearRect(0, 0, r.width, r.height);
    const tf = containTransform(A.width, A.height, r.width, r.height);
    if (ghostFn) { const g = ghostFn(f); if (g) drawSkeleton(ctx, g, tf, { color: GHOST_COLOR, alpha: 0.6 }); }
    const k = A.kps[f];
    if (k) drawSkeleton(ctx, k, tf);
    const el = (s.elements || []).find((e) => f >= e.frame0 - 1 && f <= e.frame1 + 1);
    const ph = A.phases[f];
    hud.replaceChildren(...[h("div", { class: "el" }, el ? el.name : s.label),
      h("div", { class: "st" }, ph && ph !== "-" ? h("span", { class: `phase-badge ${ph}` }, { A: "AIR", H: "HANDS", F: "FEET" }[ph]) : null,
        h("span", {}, `${((f - s.start) / A.fps).toFixed(2)} s`), paused ? h("span", {}, "paused") : null),
      ghostFn ? h("div", { class: "st", style: { color: GHOST_COLOR } }, ghostLabel) : null].filter(Boolean));
  }, 33);

  let paused = false;
  function togglePause() { paused = !paused; if (paused) video.pause(); else { holdUntil = 0; video.play().catch(() => {}); } }
  player.addEventListener("click", togglePause);
  function onKey(e) {
    if (e.target.closest("input, select, textarea")) return;
    if (e.key === " ") { e.preventDefault(); togglePause(); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); follow = false; followBtn.classList.remove("on"); select(Math.max(0, idx - 1)); }
    else if (e.key === "ArrowRight") { e.preventDefault(); select(Math.min(list.length - 1, idx + 1)); }
    else if (e.key === "g" || e.key === "G") toggleGhost();
    else if (e.key === "m" || e.key === "M") toggleGraph();
    else if (e.key === "3") toggle3d();
    else if ((e.key === "l" || e.key === "L") && view3d) { big3d = !big3d; apply3d(); }
    else if (e.key === "," || e.key === ".") {           // frame step (pauses)
      e.preventDefault();
      if (!paused) togglePause();
      video.currentTime = Math.max(0, video.currentTime + (e.key === "." ? 1 : -1) / (A?.fps || 30));
    }
    else if (e.key === "f" || e.key === "F") fullscreen();
  }
  document.addEventListener("keydown", onKey);

  refresh(true);
  const poll = setInterval(() => refresh(false), 3000);
  return () => { alive = false; clearInterval(tick); clearInterval(poll); document.removeEventListener("keydown", onKey); video.pause(); v3?.dispose(); };
}
