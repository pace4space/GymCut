// Video studio: playback with pose overlay, editable skill segments, metrics, charts, exports.
import { api, h, $, icon, toast, modal, fmtTime, fmtNum, fmtBytes, drawSkeleton, drawAngles, containTransform, setupHiDPI, COLORS, previewFile } from "./common.js";
import { rateAttempt, bestOf, skillKey, LIGHT } from "./rating.js";
import { makeGhost, loadAnalysis as loadRefAnalysis } from "./ghost.js";
import { Timeline, LineChart } from "./charts.js";
import { comparePicker } from "./compare.js";

const CHARTS = {
  knees: { name: "Knees", series: [["knee_l", "L knee", COLORS.left], ["knee_r", "R knee", COLORS.right]], unit: "°", yMin: 40, yMax: 180 },
  hips: { name: "Hips", series: [["hip_l", "L hip", COLORS.left], ["hip_r", "R hip", COLORS.right]], unit: "°", yMin: 40, yMax: 180 },
  elbows: { name: "Arms", series: [["elbow_l", "L elbow", COLORS.left], ["elbow_r", "R elbow", COLORS.right]], unit: "°", yMin: 40, yMax: 180 },
  shoulders: { name: "Shoulders", series: [["shoulder_l", "L shoulder", COLORS.left], ["shoulder_r", "R shoulder", COLORS.right]], unit: "°", yMin: 0, yMax: 180 },
  split: { name: "Split & line", series: [["split", "Leg split", "#c58bff"], ["body_line", "Body line", "#e8ecf1"]], unit: "°", yMin: 0, yMax: 180 },
  rotation: { name: "Rotation", series: [["rotation", "Rotation", "#36c2a0"], ["inversion", "Inversion", "#ffb347"]], unit: "°" },
  height: { name: "Height", series: [["com_h", "COM height", "#6aa8ff"], ["elev", "Lowest point", "#c58bff"]], unit: " BL", decimals: 2 },
  speed: { name: "Speed", series: [["speed", "Speed", "#ff7b7b"]], unit: " BL/s", decimals: 1 },
};

const EXPORTS = [
  ["clip", "Trimmed clip", "Original video, cut to the segment"],
  ["overlay", "Skeleton overlay", "Pose, trail and skill HUD burned in"],
  ["reframe", "Focus video", "Camera follows & zooms on the athlete, trimmed"],
  ["sequence", "Sequence photo", "Every phase of the skill in one image"],
  ["csv", "Data (CSV)", "Per-frame keypoints, angles and signals"],
  ["unreal", "Unreal (MetaHuman)", "Athlete image sequence for the 3D body solve"],
  ["replay3d", "3D replay (Unreal)", "3D body solve: side, front & orbit + ghost of best"],
];

const MODELS = [
  ["yolo26x-pose.pt", "YOLO26-x · most accurate (benchmark #1)"],
  ["yolo26l-pose.pt", "YOLO26-l · nearly as accurate, a bit faster"],
  ["yolo26m-pose.pt", "YOLO26-m · balanced"],
  ["yolo26s-pose.pt", "YOLO26-s · fast"],
  ["yolo26n-pose.pt", "YOLO26-n · fastest, least accurate"],
  ["yolo11x-pose.pt", "YOLO11-x · previous generation"],
  ["yolo11m-pose.pt", "YOLO11-m · previous generation"],
];

const LS = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

// Collapsible card: header click toggles; open/closed state remembered per section.
function section(key, title, body, { open = false, extra = null } = {}) {
  const isOpen = LS.get(`gc_sec_${key}`, open);
  const chev = h("span", { class: "chev" }, "▸");
  const card = h("div", { class: `card sec ${isOpen ? "open" : ""}` },
    h("div", { class: "card-h sec-h", role: "button", tabindex: 0 }, chev, h("h3", {}, title), h("span", { class: "spacer" }), extra),
    h("div", { class: "sec-b" }, body));
  const toggle = () => { const o = !card.classList.contains("open"); card.classList.toggle("open", o); LS.set(`gc_sec_${key}`, o); };
  card.firstChild.addEventListener("click", (e) => { if (!e.target.closest("button, select, input, a")) toggle(); });
  card.firstChild.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
  return card;
}

export const GHOST_COLOR = "#ff4fd8";

export function mountStudio(root, vid, params = {}) {
  let alive = true;
  let pool = [];                       // analysed attempts of this athlete (ratings / ghost)
  let ghostSrc = "off", ghostMode = "pose", ghostAlign = "takeoff", ghostPick = null, ghostFn = null, ghostLabel = "";
  let wantSeg = params.seg != null ? +params.seg : null;
  let meta = null, A = null, fps = 30, nF = 0;
  let segs = [], sel = -1, frame = 0;
  let tl = null, chart = null, chartKey = "knees";
  let tracks = null, picking = false;
  const opts = { skeleton: true, angles: false, trail: true, loop: false, others: false };
  let pollTimer = null, saveTimer = null, autoTimer = null;
  let camInv = null;

  // ---------- skeleton of the page ----------
  const video = h("video", { playsinline: true, preload: "auto", muted: false });
  const ov = h("canvas", { class: "ov" });
  const hud = h("div", { class: "hud" });
  const player = h("div", { class: "player" }, video, ov, hud);
  const title = h("h1", { title: "Click to rename" }, "…");
  const statusChip = h("span", { class: "chip" }, "");
  const banner = h("div", { class: "banner hidden" });
  const qualityBox = h("div", { class: "notes hidden", style: { padding: "2px 2px 0" } });
  const timeEl = h("span", { class: "time" }, "0:00.00");
  const speedSel = h("select", { title: "Playback speed", style: { height: "30px" } },
    ...[[0.1, "0.1×"], [0.25, "0.25×"], [0.5, "0.5×"], [0.75, "0.75×"], [1, "1×"], [1.5, "1.5×"], [2, "2×"]].map(([v, t]) => h("option", { value: v, selected: v === 1 }, t)));
  const playBtn = h("button", { class: "btn icon", title: "Play/pause (Space)" }, icon("play"));
  const tlCanvas = h("canvas");
  const presetSel = h("select", { style: { height: "30px" } });
  const sens = h("input", { type: "range", min: 0, max: 1, step: 0.05, value: 0.5 });
  const sensVal = h("span", { class: "mono dim" }, "0.50");
  const detail = h("div", { class: "card-b col", style: { gap: "14px" } });
  const chartTabs = h("div", { class: "chart-tabs" });
  const chartCanvas = h("canvas");
  const exportsList = h("div", { class: "exports-list" });
  const expSpeed = h("select", { style: { height: "30px" } }, ...[[1, "1× speed"], [0.5, "½ slow-mo"], [0.25, "¼ slow-mo"]].map(([v, t]) => h("option", { value: v }, t)));
  const expAspect = h("select", { style: { height: "30px" }, title: "Aspect ratio of focus video" },
    ...[["9:16", "9:16 vertical"], ["4:5", "4:5"], ["1:1", "1:1"], ["16:9", "16:9"], ["source", "Original"]].map(([a, t]) => h("option", { value: a }, t)));
  const expZoom = h("select", { style: { height: "30px" }, title: "How tightly the virtual camera frames the athlete" },
    ...[[0.75, "Wide"], [1.0, "Normal"], [1.4, "Tight"]].map(([v, t]) => h("option", { value: v, selected: v === 1.0 }, t)));
  const expAngles = h("input", { type: "checkbox" });
  const expDynamic = h("input", { type: "checkbox", checked: true });
  const expAction = h("input", { type: "checkbox", checked: true });
  const expOverlayReframe = h("input", { type: "checkbox", checked: true });
  const heightIn = h("input", { type: "number", min: 90, max: 220, placeholder: "cm", style: { width: "90px", height: "30px" } });
  const scaleInfo = h("span", { class: "dim", style: { fontSize: "12.5px" } });

  const athleteIn = h("input", { type: "text", list: "athlete-list", placeholder: "Athlete", title: "Who is this? (groups attempts, enables personal bests)", style: { height: "30px", width: "150px" } });
  const athleteList = h("datalist", { id: "athlete-list" });
  const ghostSel = h("select", { style: { height: "30px" }, title: "Overlay another attempt's skeleton" },
    ...[["off", "Ghost: off"], ["best", "Ghost: best of this skill"], ["prev", "Ghost: previous attempt"], ["pick", "Ghost: choose…"]].map(([v, t]) => h("option", { value: v }, t)));
  const ghostModeSel = h("select", { style: { height: "30px" }, title: "Pose = locked to the hips (technique); Path = shows height/distance differences" },
    h("option", { value: "pose" }, "pose"), h("option", { value: "path" }, "path"));
  const ghostAlignSel = h("select", { style: { height: "30px" }, title: "Synchronise the ghost at…" },
    ...[["takeoff", "at takeoff"], ["start", "at start"], ["landing", "at landing"]].map(([v, t]) => h("option", { value: v }, t)));
  const strip = h("div", { class: "filmstrip" });
  const jobRows = h("div", { class: "col", style: { gap: "6px", marginTop: "8px" } });
  const modelChip = h("span", { class: "chip", title: "Pose model used for this analysis" }, "");
  const panelBtn = h("button", { class: "btn sm", title: "Show / hide the side panel" }, "Panel");
  const btn3d = h("button", { class: "btn sm btn3d", title: "Unreal 3D replay of the selected skill: side, front & orbit views + ghost of the personal best" }, icon("cube"), h("span", {}, "3D replay"));
  let job3d = null;   // {segId, pct}
  btn3d.addEventListener("click", () => {
    const s = segs[sel];
    if (!s) { toast("Pick a skill in the strip below the video first"); return; }
    const rep = meta?.replays3d?.[String(s.id)];
    if (rep) {
      const m = previewFile({ url: `/api/exports/${vid}/${rep.file}`, file: rep.file }, [
        h("button", { class: "btn ghost", title: "Solve and render again (e.g. after re-analysing)", onclick: () => { m.close(); doExport("replay3d"); } }, icon("refresh"), "Make again")]);
    } else if (job3d && job3d.segId === s.id) toast("3D replay is already being made – progress is on this button and in Exports");
    else doExport("replay3d");
  });
  function render3dBtn() {
    const s = segs[sel];
    const rep = s && meta?.replays3d?.[String(s.id)];
    const busy = job3d && s && job3d.segId === s.id;
    btn3d.classList.toggle("has", !!rep);
    btn3d.lastChild.textContent = busy ? `3D… ${job3d.pct}%` : rep ? "Watch 3D" : "Make 3D replay";
  }
  const ratingBox = h("div", { class: "rating" });

  const toggle = (key, label, ic) => {
    const b = h("button", { class: `btn sm ${opts[key] ? "on" : ""}`, title: label }, ic ? icon(ic) : null, label);
    b.addEventListener("click", () => { opts[key] = !opts[key]; b.classList.toggle("on", opts[key]); drawOverlay(); });
    return b;
  };

  const controls = h("div", { class: "controls" },
    h("button", { class: "btn icon", title: "Previous segment (↑)", onclick: () => selectSeg(Math.max(0, sel - 1), true) }, icon("skipb")),
    h("button", { class: "btn icon", title: "Back 1 frame (←)", onclick: () => step(-1) }, icon("prev")),
    playBtn,
    h("button", { class: "btn icon", title: "Forward 1 frame (→)", onclick: () => step(1) }, icon("next")),
    h("button", { class: "btn icon", title: "Next segment (↓)", onclick: () => selectSeg(Math.min(segs.length - 1, sel + 1), true) }, icon("skipf")),
    timeEl, speedSel, h("span", { class: "sep" }),
    toggle("skeleton", "Skeleton", "bones"), toggle("angles", "Angles"), toggle("trail", "Trail"), toggle("loop", "Loop", "loop"),
    h("span", { class: "sep" }), ghostSel, ghostModeSel, ghostAlignSel,
    h("span", { class: "spacer" }),
    h("button", { class: "btn sm", title: "Choose which person in the video is the athlete", onclick: () => startPicking() }, icon("person"), "Pick person"));

  const segToolbar = h("div", { class: "seg-toolbar" },
    h("b", {}, "Auto-cut"), presetSel,
    h("label", { class: "sens" }, "Sensitivity", sens, sensVal),
    h("button", { class: "btn sm", onclick: () => autoDetect() }, icon("wand"), "Re-detect"),
    h("span", { class: "spacer" }),
    h("button", { class: "btn sm", title: "New 2 s segment at playhead (N)", onclick: () => addSegAtPlayhead() }, icon("plus"), "Segment"),
    h("button", { class: "btn sm", title: "Set start at playhead ([)", onclick: () => setEdge("start") }, "[ start"),
    h("button", { class: "btn sm", title: "Set end at playhead (])", onclick: () => setEdge("end") }, "end ]"),
    h("button", { class: "btn sm danger", title: "Delete segment (Del)", onclick: () => deleteSeg() }, icon("trash")));

  const legend = h("div", { class: "tl-legend" },
    h("span", {}, h("i", { style: { background: "rgba(54,194,160,.7)" } }), "activity"),
    h("span", {}, h("i", { style: { background: "#6aa8ff" } }), "airborne"),
    h("span", {}, h("i", { style: { background: "#ffb347" } }), "hands"),
    h("span", {}, h("i", { style: { background: "#3a4350" } }), "feet"),
    h("span", {}, h("i", { style: { background: "#ff5d5d" } }), "scene cut"),
    h("span", { class: "spacer" }),
    h("span", {}, "drag on the timeline to add a segment · drag edges to trim · wheel to zoom"));

  const exportBtns = h("div", { class: "export-grid" },
    ...EXPORTS.map(([mode, t, d]) => h("button", { class: "btn export-opt", onclick: () => doExport(mode) }, h("b", {}, t), h("span", {}, d))));

  const main = h("div", { class: "studio-main" },
    h("div", { class: "studio-head" },
      h("a", { class: "btn sm ghost", href: "#/" }, icon("prev"), "Library"), title, statusChip,
      athleteIn, athleteList,
      h("span", { class: "spacer" }),
      modelChip, btn3d,
      h("a", { class: "btn sm", href: "#/coach", title: "Big-screen courtside view" }, icon("live"), "Coach mode"),
      h("button", { class: "btn sm ghost", title: "Run the analysis again, optionally with another pose model", onclick: () => reprocessDialog() }, icon("refresh"), "Re-analyse"),
      panelBtn),
    banner, qualityBox, player, controls, strip,
    h("div", { class: "timeline-wrap" }, tlCanvas), legend,
    h("div", { class: "card" }, h("div", { class: "card-b" }, segToolbar)),
    section("kinematics", "Kinematics", h("div", { class: "card-b chart-box" }, chartCanvas), { open: false, extra: chartTabs }));
  const side = h("div", { class: "studio-side" },
    section("rating", "Rating", ratingBox, { open: true, extra: h("span", { class: "dim", style: { fontSize: "12px" } }, "technique + personal best") }),
    section("analysis", "Analysis", detail),
    section("export", "Export",
      h("div", { class: "card-b" },
        h("div", { class: "export-settings" }, expSpeed,
          h("label", { class: "check" }, expAngles, "angles"),
          h("label", { class: "check", title: "Focus video: also draw the skeleton" }, expOverlayReframe, "skeleton"),
          h("label", { class: "check", title: "Focus video: zoom in/out with the body (tight on a tuck, wider on a layout)" }, expDynamic, "dynamic zoom"),
          h("label", { class: "check", title: "Focus video: trim to the action (hurdle → landing hold)" }, expAction, "action only"),
          h("span", { class: "row" }, expAspect, expZoom)),
        exportBtns, jobRows,
        h("button", { class: "btn sm", style: { marginTop: "8px", width: "100%" }, onclick: () => exportAll() },
          icon("download"), "Export all skills as focus videos"),
        exportsList)),
    section("scale", "Athlete & scale",
      h("div", { class: "card-b col" },
        h("div", { class: "row" }, h("span", { class: "muted" }, "Athlete height"), heightIn,
          h("button", { class: "btn sm", onclick: () => saveHeight() }, "Apply")),
        scaleInfo)));
  const studioEl = h("div", { class: `studio ${LS.get("gc_side_hidden", false) ? "side-hidden" : ""}` }, main, side);
  root.append(studioEl);
  panelBtn.classList.toggle("on", !studioEl.classList.contains("side-hidden"));
  panelBtn.addEventListener("click", () => {
    const hidden = studioEl.classList.toggle("side-hidden");
    LS.set("gc_side_hidden", hidden);
    panelBtn.classList.toggle("on", !hidden);
    setTimeout(resizeOverlay, 50);
  });

  // ---------- data loading ----------
  async function loadMeta() {
    meta = await api.get(`/api/videos/${vid}`);
    title.textContent = meta.name;
    if (document.activeElement !== athleteIn) athleteIn.value = meta.athlete || "";
    modelChip.textContent = meta.model_used || "";
    modelChip.classList.toggle("hidden", !meta.model_used);
    heightIn.value = meta.athlete_height_cm ?? "";
    const job = (meta.jobs || []).find((j) => j.kind === "analyze");
    statusChip.textContent = meta.status;
    if (job || ["queued", "processing", "new"].includes(meta.status)) {
      banner.className = "banner processing";
      banner.innerHTML = "";
      banner.append(h("b", {}, job ? job.stage || "Queued" : "Queued"),
        h("div", { class: "bar" }, h("i", { style: { width: `${(job?.progress || 0) * 100}%` } })),
        h("span", { class: "mono dim" }, job?.message || ""),
        job ? h("button", { class: "btn sm ghost", onclick: async () => { await api.post(`/api/jobs/${job.id}/cancel`); } }, "Cancel") : null);
      return false;
    }
    if (meta.status === "error") {
      banner.className = "banner error";
      banner.innerHTML = "";
      banner.append(h("b", {}, "Analysis failed"), h("span", { class: "grow muted" }, meta.error || ""),
        h("button", { class: "btn sm", onclick: () => reprocessDialog() }, "Retry"));
      return false;
    }
    banner.className = "banner hidden";
    return meta.status === "ready";
  }

  async function poll() {
    if (!alive) return;
    let ready = false;
    try { ready = await loadMeta(); } catch (e) { toast(e.message, "err"); }
    if (ready) {
      if (!A || A.generated < (meta.processed || 0)) await loadAnalysis();
    } else {
      if (meta && meta.status !== "error" && !video.src && meta.width) video.src = `/api/videos/${vid}/media`;
      pollTimer = setTimeout(poll, 1000);
    }
  }

  async function loadAnalysis() {
    A = await api.get(`/api/videos/${vid}/analysis`);
    fps = A.fps; nF = A.n_frames;
    if (!video.src) video.src = `/api/videos/${vid}/media`;
    camInv = null;
    if (A.cam) camInv = A.cam.map((m) => invAffine(m));
    presetSel.innerHTML = "";
    for (const [k, label] of Object.entries(A.presets)) presetSel.append(h("option", { value: k, selected: k === A.segment_params.preset }, label));
    sens.value = A.segment_params.sensitivity; sensVal.textContent = (+sens.value).toFixed(2);
    segs = A.segments;
    if (!tl) {
      tl = new Timeline(tlCanvas, {
        fps, n: nF, activity: A.signals.activity, phases: A.phases, cuts: A.cuts,
        onSeek: (f) => seek(f),
        onChange: (s, committed) => { segs = s.map((x, i) => ({ ...(segs[i] || {}), ...x })); if (committed) scheduleSave(); },
        onSelect: (i) => selectSeg(i, false),
      });
    }
    tl.setSegments(segs, sel);
    updateThreshold();
    if (!chart) chart = new LineChart(chartCanvas, { onSeek: (f) => seek(f) });
    renderChartTabs();
    if (sel < 0 && segs.length) selectSeg(0, true); else selectSeg(Math.min(sel, segs.length - 1), false);
    await loadPool();
    if (!jobRows.childElementCount) resumeJobs();
    if (wantSeg != null) {
      const i = segs.findIndex((x) => x.id === wantSeg);
      wantSeg = null;
      if (i >= 0) selectSeg(i, true);
    }
    renderSegList();
    renderScale();
    renderQuality();
    loadExports();
  }

  async function loadPool() {
    try {
      pool = await api.get(`/api/segments${meta?.athlete ? `?athlete=${encodeURIComponent(meta.athlete)}` : ""}`);
      const ath = await api.get("/api/athletes");
      athleteList.replaceChildren(...ath.filter((a) => a.name).map((a) => h("option", { value: a.name })));
    } catch { pool = []; }
    renderSegList(); renderRating(); setupGhost();
  }

  athleteIn.addEventListener("change", async () => {
    meta = await api.patch(`/api/videos/${vid}`, { athlete: athleteIn.value.trim() || null });
    toast(meta.athlete ? `Athlete set: ${meta.athlete}` : "Athlete cleared", "ok");
    loadPool();
  });

  // ---------- ghost ----------
  async function setupGhost() {
    ghostFn = null; ghostLabel = "";
    const s = segs[sel];
    if (!s || ghostSrc === "off" || !A) { drawOverlay(); return; }
    const me = { ...s, vid };
    let ref = null;
    if (ghostSrc === "best") ref = bestOf(me, pool);
    else if (ghostSrc === "prev") {
      const same = pool.filter((p) => skillKey(p) === skillKey(me) && !(p.vid === vid && p.id === s.id));
      const idx = pool.findIndex((p) => p.vid === vid && p.id === s.id);
      const before = same.filter((p) => pool.indexOf(p) < idx);
      ref = before.at(-1) || same.at(-1) || null;
    } else if (ghostSrc === "pick") ref = ghostPick;
    if (!ref) {
      if (ghostSrc !== "pick") toast("No other attempt of this skill yet – choose one manually");
      drawOverlay(); return;
    }
    const RA = ref.vid === vid ? A : await loadRefAnalysis(ref.vid);
    const rseg = RA.segments.find((x) => x.id === ref.id);
    if (!rseg) { drawOverlay(); return; }
    ghostFn = makeGhost({ A, seg: s }, { A: RA, seg: rseg }, { mode: ghostMode, align: ghostAlign });
    ghostLabel = `${rseg.label} · ${ref.video || "this video"}${ref.recorded ? " · " + ref.recorded : ""}`;
    drawOverlay();
  }
  function pickGhost() {
    const s = segs[sel];
    const same = pool.filter((p) => s && skillKey(p) === skillKey(s) && !(p.vid === vid && p.id === s.id));
    const rest = pool.filter((p) => !same.includes(p) && !(p.vid === vid && p.id === s?.id));
    const card = (p) => h("button", { class: "film", onclick: () => { ghostPick = p; m.close(); setupGhost(); } },
      h("div", { class: "film-img", style: { backgroundImage: `url(/api/videos/${p.vid}/segthumb/${p.id}.jpg)` } }),
      h("div", { class: "film-lbl" }, p.label), h("div", { class: "film-sub" }, `${p.video} · ${p.recorded}`));
    const body = h("div", { style: { maxHeight: "62vh", overflow: "auto" } }, ...[
      same.length ? h("div", { class: "pick-sec" }, `Same skill (${same.length})`) : null,
      same.length ? h("div", { class: "pick-grid" }, ...same.map(card)) : null,
      rest.length ? h("div", { class: "pick-sec" }, "Other attempts") : null,
      rest.length ? h("div", { class: "pick-grid" }, ...rest.map(card)) : null,
      !same.length && !rest.length ? h("div", { class: "dim" }, "No other analysed attempts yet.") : null].filter(Boolean));
    const m = modal("Ghost: choose an attempt", body);
    m.el.style.width = "min(980px, 100%)";
  }
  ghostSel.addEventListener("change", () => { ghostSrc = ghostSel.value; if (ghostSrc === "pick") pickGhost(); else setupGhost(); });
  ghostModeSel.addEventListener("change", () => { ghostMode = ghostModeSel.value; setupGhost(); });
  ghostAlignSel.addEventListener("change", () => { ghostAlign = ghostAlignSel.value; setupGhost(); });

  function renderQuality() {
    qualityBox.innerHTML = "";
    const q = A.quality || [];
    qualityBox.classList.toggle("hidden", !q.length);
    for (const item of q) {
      qualityBox.append(h("div", { class: `note ${item.level}`, style: { gridTemplateColumns: "14px 1fr", cursor: "default" } },
        h("span", { class: "ic" }), h("span", { class: "muted" }, item.text)));
    }
  }

  function invAffine(m) {
    // m = [a, b, tx, c, d, ty] (frame -> world); return inverse in the same layout
    const [a, b, tx, c, d, ty] = m;
    const det = a * d - b * c || 1;
    const ia = d / det, ib = -b / det, ic = -c / det, id = a / det;
    return [ia, ib, -(ia * tx + ib * ty), ic, id, -(ic * tx + id * ty)];
  }

  // ---------- playback & overlay ----------
  const frameAt = (t) => Math.max(0, Math.min(nF - 1, Math.floor(t * fps + 0.01)));
  function seek(f) {
    f = Math.max(0, Math.min((nF || 1) - 1, f));
    frame = f;
    video.currentTime = (f + 0.3) / fps;
    onFrame(f);
  }
  function step(d) { video.pause(); seek(frame + d); }
  function togglePlay() {
    if (video.paused) {
      const s = segs[sel];
      if (opts.loop && s && (frame >= s.end - 1 || frame < s.start)) seek(s.start);
      video.play();
    } else video.pause();
  }
  playBtn.addEventListener("click", togglePlay);
  video.addEventListener("click", () => { if (!picking) togglePlay(); });
  video.addEventListener("play", () => { playBtn.replaceChildren(icon("pause")); });
  video.addEventListener("pause", () => { playBtn.replaceChildren(icon("play")); });
  speedSel.addEventListener("change", () => (video.playbackRate = +speedSel.value));
  video.addEventListener("loadedmetadata", () => { resizeOverlay(); seek(frame); });

  // Frame-exact sync via requestVideoFrameCallback; a timer fallback keeps the
  // overlay/timeline in sync when the browser throttles painting callbacks.
  const useRVFC = "requestVideoFrameCallback" in HTMLVideoElement.prototype;
  let lastCb = 0;
  function rvfc(now, md) {
    if (!alive) return;
    lastCb = performance.now();
    onFrame(frameAt(md.mediaTime));
    video.requestVideoFrameCallback(rvfc);
  }
  if (useRVFC) video.requestVideoFrameCallback(rvfc);
  const syncTimer = setInterval(() => {
    if (!alive || video.paused || !nF) return;
    if (performance.now() - lastCb > 120) {
      const f = frameAt(video.currentTime);
      if (f !== frame) onFrame(f);
    }
  }, 33);
  video.addEventListener("seeked", () => onFrame(frameAt(video.currentTime)));
  video.addEventListener("pause", () => onFrame(frameAt(video.currentTime)));

  function onFrame(f) {
    frame = f;
    const s = segs[sel];
    if (opts.loop && s && !video.paused && f >= s.end - 1) { seek(s.start); video.play(); return; }
    timeEl.textContent = `${fmtTime(f / fps)}  #${f}`;
    tl?.setFrame(f);
    chart?.setFrame(f);
    drawOverlay();
  }

  let ovCtx = null;
  function resizeOverlay() {
    const r = player.getBoundingClientRect();
    ovCtx = setupHiDPI(ov, r.width, r.height);
    drawOverlay();
  }
  new ResizeObserver(resizeOverlay).observe(player);

  function drawOverlay() {
    if (!ovCtx) return;
    const r = player.getBoundingClientRect();
    ovCtx.clearRect(0, 0, r.width, r.height);
    hud.innerHTML = "";
    if (!A) return;
    const tf = containTransform(A.width, A.height, r.width, r.height);
    const f = frame;
    if (picking && tracks) {
      for (const t of tracks.tracks) {
        const i = t.frames.indexOf(f);
        if (i < 0) continue;
        const [x1, y1, x2, y2] = t.boxes[i];
        ovCtx.strokeStyle = t.athlete ? "#36c2a0" : "#ffffff";
        ovCtx.lineWidth = t.athlete ? 3 : 1.5;
        ovCtx.strokeRect(x1 * tf.sx + tf.ox, y1 * tf.sy + tf.oy, (x2 - x1) * tf.sx, (y2 - y1) * tf.sy);
        ovCtx.fillStyle = "rgba(0,0,0,.7)";
        ovCtx.fillRect(x1 * tf.sx + tf.ox, y1 * tf.sy + tf.oy - 18, 64, 18);
        ovCtx.fillStyle = "#fff"; ovCtx.font = "600 12px Inter, system-ui";
        ovCtx.fillText(`#${t.id}${t.athlete ? " ★" : ""}`, x1 * tf.sx + tf.ox + 5, y1 * tf.sy + tf.oy - 5);
      }
      return;
    }
    const k = A.kps[f];
    if (opts.trail && A.com) {
      const s = segs[sel];
      const lo = Math.max(s && f >= s.start && f < s.end ? s.start : 0, f - Math.round(fps * 1.2));
      ovCtx.lineCap = "round";
      let prev = null;
      for (let i = lo; i <= f; i++) {
        let p = A.com[i];
        if (!p) { prev = null; continue; }
        if (camInv && A.com_w) {
          const w = A.com_w[i], m = camInv[f];
          if (!w || !m) { prev = null; continue; }
          p = [m[0] * w[0] + m[1] * w[1] + m[2], m[3] * w[0] + m[4] * w[1] + m[5]];
        }
        const x = p[0] * tf.sx + tf.ox, y = p[1] * tf.sy + tf.oy;
        if (prev) {
          const a = (i - lo) / Math.max(1, f - lo);
          ovCtx.strokeStyle = `rgba(120,255,140,${0.15 + 0.8 * a})`;
          ovCtx.lineWidth = 1 + 3 * a;
          ovCtx.beginPath(); ovCtx.moveTo(prev[0], prev[1]); ovCtx.lineTo(x, y); ovCtx.stroke();
        }
        prev = [x, y];
      }
    }
    if (ghostFn) {
      const g = ghostFn(f);
      if (g) drawSkeleton(ovCtx, g, tf, { color: GHOST_COLOR, alpha: 0.6 });
    }
    if (k && opts.skeleton) drawSkeleton(ovCtx, k, tf);
    if (k && opts.angles) drawAngles(ovCtx, k, tf);
    // HUD
    const s = segs[sel];
    const el = s ? (s.elements || []).find((e) => f >= e.frame0 - 1 && f <= e.frame1 + 1) : null;
    const ph = A.phases[f];
    const rot = A.signals.rotation[f], r0 = s ? A.signals.rotation[s.start] : null;
    const inv = A.signals.inversion[f];
    if (el || (s && f >= s.start && f < s.end)) hud.append(h("div", { class: "el" }, el ? el.name : s.label));
    hud.append(h("div", { class: "st" },
      ph && ph !== "-" ? h("span", { class: `phase-badge ${ph}` }, { A: "AIR", H: "HANDS", F: "FEET" }[ph]) : null,
      inv !== null && inv !== undefined ? h("span", {}, `inv ${Math.round(inv)}°`) : null,
      s && rot !== null && r0 !== null && f >= s.start && f < s.end ? h("span", {}, `rot ${Math.round(Math.abs(rot - r0))}°`) : null));
    if (ghostFn) hud.append(h("div", { class: "st", style: { color: GHOST_COLOR } }, `ghost: ${ghostLabel}`));
  }

  // ---------- athlete picking ----------
  async function startPicking() {
    if (!tracks) tracks = await api.get(`/api/videos/${vid}/tracks`);
    picking = true;
    player.classList.add("picking");
    video.pause();
    const hint = h("div", { class: "pick-hint" }, "Click the athlete (scrub to a frame where they are visible) · Esc to cancel");
    player.append(hint);
    drawOverlay();
    const done = () => { picking = false; player.classList.remove("picking"); hint.remove(); ov.removeEventListener("click", onClick); drawOverlay(); };
    async function onClick(e) {
      const r = player.getBoundingClientRect();
      const tf = containTransform(A.width, A.height, r.width, r.height);
      const x = (e.clientX - r.left - tf.ox) / tf.sx, y = (e.clientY - r.top - tf.oy) / tf.sy;
      let best = null, area = Infinity;
      for (const t of tracks.tracks) {
        const i = t.frames.indexOf(frame);
        if (i < 0) continue;
        const [x1, y1, x2, y2] = t.boxes[i];
        if (x >= x1 && x <= x2 && y >= y1 && y <= y2 && (x2 - x1) * (y2 - y1) < area) { best = t; area = (x2 - x1) * (y2 - y1); }
      }
      if (!best) { toast("No tracked person there – scrub to another frame"); return; }
      done();
      await api.post(`/api/videos/${vid}/athlete`, { track_id: best.id });
      toast(`Re-analysing with person #${best.id} as the athlete…`, "ok");
      tracks = null; A = null; poll();
    }
    ov.addEventListener("click", onClick);
    const esc = (e) => { if (e.key === "Escape") { done(); document.removeEventListener("keydown", esc); } };
    document.addEventListener("keydown", esc);
  }

  // ---------- segments ----------
  function selectSeg(i, seekTo) {
    if (i < 0 || i >= segs.length) { sel = -1; renderDetail(); tl?.setSegments(segs, -1); renderChart(); return; }
    sel = i;
    tl?.setSegments(segs, sel);
    renderSegList(); renderDetail(); renderChart(); renderRating(); setupGhost(); render3dBtn();
    if (seekTo) { video.pause(); seek(segs[i].start); tl?.zoomTo(segs[i].start, segs[i].end); }
  }

  function kpiChips(s) {
    const m = s.metrics || {};
    const chips = [h("span", { class: "chip" }, `${fmtNum(m.duration, 1)} s`)];
    const rot = Math.abs(m.rotation_total_deg || 0);
    if (rot >= 180) chips.push(h("span", { class: "chip" }, `${(rot / 360).toFixed(1)} rot`));
    if (m.max_flight_time) chips.push(h("span", { class: "chip" }, `air ${m.max_flight_time.toFixed(2)} s`));
    const warn = (s.notes || []).filter((n) => n.level === "warn" || n.level === "bad").length;
    if (warn) chips.push(h("span", { class: "chip", style: { color: "var(--warn)" } }, `${warn} note${warn > 1 ? "s" : ""}`));
    return chips;
  }

  function renderSegList() {
    strip.innerHTML = "";
    if (!segs.length) {
      strip.append(h("div", { class: "dim", style: { padding: "10px 4px" } },
        "No skills detected. Raise the sensitivity, switch preset to “Any movement”, or drag on the timeline to add one."));
      return;
    }
    const ver = Math.floor(A?.generated || 0);
    segs.forEach((s, i) => {
      const r = rateAttempt({ ...s, vid }, pool);
      const m = s.metrics || {};
      strip.append(h("button", { class: `film ${i === sel ? "sel" : ""}`, onclick: () => selectSeg(i, true), title: s.label },
        h("div", { class: "film-img", style: { backgroundImage: `url(/api/videos/${vid}/segthumb/${s.id}.jpg?v=${ver})` } },
          h("span", { class: "film-num" }, i + 1),
          r.overall ? h("span", { class: "film-dot", style: { background: LIGHT[r.overall] } }) : null),
        h("div", { class: "film-lbl" }, s.label || "Segment"),
        h("div", { class: "film-sub" }, m.max_flight_time ? `air ${m.max_flight_time.toFixed(2)}s` : `${fmtNum(m.duration, 1)}s`,
          s.landing?.result ? ` · ${s.landing.result}` : "")));
    });
    strip.querySelector(".film.sel")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  function renderRating() {
    ratingBox.innerHTML = "";
    const s = segs[sel];
    if (!s) { ratingBox.append(h("div", { class: "card-b dim" }, "Select a skill.")); return; }
    const r = rateAttempt({ ...s, vid, m3d: meta?.metrics3d?.[String(s.id)] }, pool);
    if (!r.items.length) { ratingBox.append(h("div", { class: "card-b dim" }, "Nothing to rate for this movement yet.")); return; }
    const list = h("div", { class: "card-b col", style: { gap: "6px" } });
    for (const it of r.items) {
      list.append(h("div", { class: "light-row", title: it.hint || "" },
        h("span", { class: "light", style: { background: LIGHT[it.level] } }),
        h("span", { class: "grow" }, it.label, it.d3 ? h("span", { class: "tag3d", title: "Measured on the Unreal 3D body solve (camera-independent)" }, "3D") : null),
        h("b", { class: "mono" }, typeof it.value === "number" ? `${it.value}${it.unit ? " " + it.unit : ""}` : it.value)));
    }
    if (!r.sameCount) list.append(h("div", { class: "dim", style: { fontSize: "12px" } },
      meta?.athlete ? "First attempt of this skill – personal-best lights appear from the next one." : "Set the athlete (top) to compare against personal bests."));
    ratingBox.append(list);
  }

  const M = (k, v, unit = "") => h("div", { class: "metric" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v ?? "–", unit && v !== "–" ? h("small", {}, unit) : null));

  function renderDetail() {
    detail.innerHTML = "";
    const s = segs[sel];
    if (!s) { detail.append(h("div", { class: "dim" }, "Select a skill to see its analysis.")); return; }
    const m = s.metrics || {};
    const lbl = h("input", { type: "text", value: s.label || "", style: { width: "100%", fontWeight: 650 } });
    lbl.addEventListener("change", () => { s.label = lbl.value; s.custom_label = true; renderSegList(); scheduleSave(0); });
    detail.append(h("div", { class: "col", style: { gap: "4px" } }, lbl,
      h("div", { class: "row" },
        h("span", { class: "dim mono grow", style: { fontSize: "12px" } }, `${fmtTime(s.start / fps)} → ${fmtTime(s.end / fps)} · ${s.end - s.start} frames`),
        h("button", { class: "btn sm", title: "Compare this skill with another attempt", onclick: () => comparePicker(vid, s.id) }, "Compare…"))));
    const ms = A.scale && A.scale.m_per_bl;
    const grid = h("div", { class: "metrics" },
      M("Duration", fmtNum(m.duration, 2), "s"),
      M("Elements", m.elements ?? 0),
      M("Rotation", fmtNum(Math.abs(m.rotation_total_deg || 0), 0), "°"),
      M("Max flight", m.max_flight_time ? fmtNum(m.max_flight_time, 2) : "–", "s"),
      M("Height (flight)", m.max_height_m ? fmtNum(m.max_height_m * 100, 0) : "–", "cm"),
      M("Max split", fmtNum(m.max_split, 0), "°"),
      ms ? M("Top speed", fmtNum(m.max_speed_ms, 1), "m/s") : M("Top speed", fmtNum(m.max_speed_bl, 1), "BL/s"),
      ms ? M("Approach", fmtNum(m.approach_speed_ms, 1), "m/s") : M("Approach", fmtNum(m.approach_speed_bl, 1), "BL/s"),
      M("Pose coverage", fmtNum((m.pose_coverage || 0) * 100, 0), "%"));
    detail.append(grid);
    if ((s.elements || []).length) {
      const els = h("div", { class: "elements" });
      for (const e of s.elements) {
        const air = ["salto", "jump", "leap", "flight", "hurdle"].includes(e.type);
        const facts = [];
        const F = (k, v) => { if (v !== null && v !== undefined && v !== "") facts.push(h("span", {}, `${k} `, h("b", {}, v))); };
        if (e.rotation_deg !== null && Math.abs(e.rotation_deg) >= 45) F("rotation", `${Math.abs(e.rotation_deg)}°`);
        if (e.flight_time) F("flight", `${e.flight_time.toFixed(2)} s`);
        if (e.height_est_m) F("height", `${Math.round(e.height_est_m * 100)} cm`);
        if (e.shape) F("shape", e.shape);
        if (e.split_max && e.type === "leap") F("split", `${e.split_max}°`);
        if (e.knee_min && air) F("min knee", `${e.knee_min}°`);
        if (e.support_time) F("support", `${e.support_time.toFixed(2)} s`);
        if (e.pre_flight) F("1st flight", `${e.pre_flight.toFixed(2)} s`);
        if (e.post_flight) F("2nd flight", `${e.post_flight.toFixed(2)} s`);
        if (e.elbow_min && !air) F("arms", `${Math.round(e.elbow_min)}°`);
        if (e.twists_est >= 0.5) F("twist ≈", e.twists_est);
        els.append(h("div", { class: "element", onclick: () => { video.pause(); seek(e.frame0); } },
          h("div", { class: "h" }, h("span", { class: `tag ${air ? "air" : "hands"}` }, e.type), e.name,
            h("span", { class: "spacer" }), h("span", { class: "dim mono", style: { fontSize: "12px" } }, fmtTime(e.frame0 / fps))),
          h("div", { class: "facts" }, ...facts)));
      }
      detail.append(els);
    }
    if (s.landing && s.landing.result) {
      const L = s.landing;
      detail.append(h("div", { class: "element", onclick: () => { video.pause(); seek(L.frame); } },
        h("div", { class: "h" }, h("span", { class: "tag" }, "landing"), L.result,
          h("span", { class: "spacer" }), h("span", { class: "dim mono", style: { fontSize: "12px" } }, fmtTime(L.frame / fps))),
        h("div", { class: "facts" },
          L.knee_min ? h("span", {}, "knee depth ", h("b", {}, `${L.knee_min}°`)) : null,
          L.foot_travel_bl !== null ? h("span", {}, "foot travel ", h("b", {}, `${L.foot_travel_bl} BL`)) : null,
          L.sway_bl !== null ? h("span", {}, "sway ", h("b", {}, `${L.sway_bl} BL`)) : null)));
    }
    if ((s.notes || []).length) {
      const notes = h("div", { class: "notes" });
      for (const n of s.notes) {
        notes.append(h("div", { class: `note ${n.level}`, onclick: () => { video.pause(); seek(Math.round(n.t * fps)); } },
          h("span", { class: "ic" }), h("span", { class: "t" }, fmtTime(n.t)), h("span", {}, n.text)));
      }
      detail.append(h("div", { class: "col", style: { gap: "6px" } }, h("b", { style: { fontSize: "13px" } }, "Form notes"), notes));
    }
  }

  function scheduleSave(delay = 350) {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      try {
        const body = segs.map((s) => ({ start: s.start, end: s.end, label: s.custom_label ? s.label : null, custom_label: !!s.custom_label }));
        const r = await api.put(`/api/videos/${vid}/segments`, { segments: body });
        segs = r.segments;
        A.segments = segs; A.segment_params = r.segment_params;
        if (sel >= segs.length) sel = segs.length - 1;
        tl.setSegments(segs, sel);
        renderSegList(); renderDetail(); renderChart();
      } catch (e) { toast("Saving segments failed: " + e.message, "err"); }
    }, delay);
  }

  function addSegAtPlayhead() {
    const s = Math.max(0, frame - Math.round(fps * 0.5)), e = Math.min(nF, s + Math.round(fps * 2));
    segs.push({ start: s, end: e, label: null });
    segs.sort((a, b) => a.start - b.start);
    sel = segs.findIndex((x) => x.start === s);
    tl.setSegments(segs, sel);
    scheduleSave(0);
  }
  function setEdge(edge) {
    const s = segs[sel]; if (!s) return;
    if (edge === "start") s.start = Math.min(frame, s.end - 2); else s.end = Math.max(frame + 1, s.start + 2);
    tl.setSegments(segs, sel); scheduleSave(0);
  }
  function deleteSeg() {
    if (sel < 0) return;
    segs.splice(sel, 1);
    sel = Math.min(sel, segs.length - 1);
    tl.setSegments(segs, sel); scheduleSave(0);
  }

  function updateThreshold() {
    // mirror of segmentation.py: on = preset_on * (1.45 - 0.9 * sensitivity)
    const on = { skills: 0.42, passes: 0.40, routine: 0.35, activity: 0.30 }[presetSel.value] || 0.42;
    tl?.setThreshold(on * (1.45 - 0.9 * +sens.value));
  }
  async function autoDetect() {
    try {
      const r = await api.post(`/api/videos/${vid}/segments/auto`, { preset: presetSel.value, sensitivity: +sens.value });
      segs = r.segments; A.segments = segs; A.segment_params = r.segment_params;
      sel = segs.length ? 0 : -1;
      tl.setSegments(segs, sel); renderSegList(); renderDetail(); renderChart();
      toast(`${segs.length} segment${segs.length === 1 ? "" : "s"} detected`);
    } catch (e) { toast(e.message, "err"); }
  }
  sens.addEventListener("input", () => {
    sensVal.textContent = (+sens.value).toFixed(2); updateThreshold();
    clearTimeout(autoTimer); autoTimer = setTimeout(autoDetect, 450);
  });
  presetSel.addEventListener("change", () => { updateThreshold(); autoDetect(); });

  // ---------- charts ----------
  function renderChartTabs() {
    chartTabs.innerHTML = "";
    for (const [k, c] of Object.entries(CHARTS)) {
      chartTabs.append(h("button", { class: `btn sm ${k === chartKey ? "on" : "ghost"}`, onclick: () => { chartKey = k; renderChartTabs(); renderChart(); } }, c.name));
    }
  }
  function renderChart() {
    if (!chart || !A) return;
    const c = CHARTS[chartKey];
    const s = segs[sel];
    const pad = Math.round(fps * 0.3);
    const range = s ? [Math.max(0, s.start - pad), Math.min(nF, s.end + pad)] : [0, nF];
    const series = c.series.map(([key, name, color]) => {
      let data = A.signals[key];
      if (key === "rotation" && s) {
        const r0 = A.signals.rotation[s.start] ?? 0;
        data = data.map((v) => (v === null ? null : Math.abs(v - r0)));
      }
      return { name, color, data };
    });
    const bands = s ? (s.elements || []).map((e) => ({
      start: e.frame0, end: e.frame1,
      color: ["salto", "jump", "leap", "flight"].includes(e.type) ? "rgba(106,168,255,.10)" : "rgba(255,179,71,.10)",
    })) : [];
    chart.set({ series, range, fps, unit: c.unit, yMin: c.yMin ?? null, yMax: c.yMax ?? null, bands, decimals: c.decimals ?? 0 });
    chart.setFrame(frame);
  }

  // ---------- scale ----------
  function renderScale() {
    const sc = A.scale || {};
    scaleInfo.textContent = sc.m_per_bl
      ? `Scale from ${sc.source}: 1 body-length ≈ ${(sc.m_per_bl * 100).toFixed(0)} cm (athlete ≈ ${(sc.height_m * 100).toFixed(0)} cm).`
      : "Distances are in body-lengths (BL = shoulder→ankle). Enter the athlete's height for metres and m/s. Flight-time heights are always physical (h = g·t²/8).";
  }
  async function saveHeight() {
    const v = heightIn.value ? +heightIn.value : null;
    await api.patch(`/api/videos/${vid}`, { athlete_height_cm: v });
    toast("Scale updated", "ok");
    await loadAnalysis();
  }

  // ---------- exports ----------
  async function doExport(mode) {
    const s = segs[sel];
    if (!s && mode !== "csv") { toast("Select a segment first (or drag on the timeline to make one)"); return; }
    const body = {
      mode, segment: s ? s.id : null,
      opts: { speed: +expSpeed.value, angles: expAngles.checked, aspect: expAspect.value, zoom: +expZoom.value, overlay: expOverlayReframe.checked,
        trail: true, dynamic: expDynamic.checked, action_only: expAction.checked },
    };
    if (!s) { body.start = 0; body.end = nF; }
    if (mode === "replay3d") {
      if (!s) { toast("Select a skill first"); return; }
      try {
        const job = await api.post(`/api/videos/${vid}/replay3d`, { segment: s.id, ghost: true });
        toast("3D replay queued – Unreal solves the body (a few minutes per attempt)…", "ok");
        trackJob(job);
      } catch (e) { toast(e.message, "err"); }
      return;
    }
    try {
      const job = await api.post(`/api/videos/${vid}/export`, body);
      toast(`${job.title} started…`);
      const wait = async () => {
        const j = await api.get(`/api/jobs/${job.id}`);
        if (j.state === "done") {
          toast(h("span", {}, `${j.title} ready – `, h("a", { href: "javascript:void 0", onclick: () => previewExport(j.result) }, "preview")), "ok");
          loadExports();
        } else if (j.state === "error") toast(`${j.title} failed: ${j.error}`, "err");
        else setTimeout(wait, 700);
      };
      wait();
    } catch (e) { toast(e.message, "err"); }
  }
  // long-running jobs (3D replays): a live progress row above the export list
  function trackJob(job) {
    const bar = h("i", { style: { width: "0%" } });
    const label = h("span", { class: "grow", style: { fontSize: "12.5px" } }, job.title);
    const msg = h("span", { class: "dim mono", style: { fontSize: "11.5px" } }, "");
    const row = h("div", { class: "export-row", style: { flexDirection: "column", alignItems: "stretch", gap: "4px" } },
      h("div", { class: "row" }, label, h("button", { class: "btn sm ghost", onclick: () => api.post(`/api/jobs/${job.id}/cancel`) }, "Cancel")),
      h("div", { class: "bar" }, bar), msg);
    jobRows.prepend(row);
    const poll = async () => {
      if (!alive) return;
      const j = await api.get(`/api/jobs/${job.id}`);
      bar.style.width = `${Math.round(j.progress * 100)}%`;
      label.textContent = `${j.title} – ${j.stage || j.state}`;
      msg.textContent = j.message || "";
      const segId = Number((j.title.match(/#(\d+)/) || [])[1]);
      job3d = ["queued", "running"].includes(j.state) ? { segId, pct: Math.round(j.progress * 100) } : null;
      render3dBtn();
      if (j.state === "done") {
        row.remove(); loadExports();
        meta = await api.get(`/api/videos/${vid}`); render3dBtn();
        toast(h("span", {}, "3D replay ready – ", h("a", { href: "javascript:void 0", onclick: () => previewExport(j.result) }, "watch")), "ok");
      } else if (j.state === "error" || j.state === "cancelled") {
        msg.textContent = j.error || j.state; msg.style.color = "var(--bad)";
      } else setTimeout(poll, 2500);
    };
    poll();
  }
  async function resumeJobs() {
    for (const j of (meta?.jobs || []).filter((j) => j.kind === "replay3d")) trackJob(j);
  }

  async function exportAll() {
    const r = await api.post("/api/export-all", { vid, mode: "reframe",
      opts: { speed: +expSpeed.value, aspect: expAspect.value, zoom: +expZoom.value, overlay: expOverlayReframe.checked, trail: true,
        dynamic: expDynamic.checked, action_only: expAction.checked } });
    toast(`${r.queued} focus videos queued – they appear below as they finish`, "ok");
    let n = 0;
    const t = setInterval(() => { loadExports(); if (++n > 40 || !alive) clearInterval(t); }, 3000);
  }

  async function loadExports() {
    const list = await api.get(`/api/videos/${vid}/exports`);
    exportsList.innerHTML = "";
    for (const x of list.slice(0, 12)) {
      exportsList.append(h("div", { class: "export-row" },
        h("a", { class: "n", href: "javascript:void 0", title: `Preview ${x.file}`, onclick: () => previewExport(x) }, x.file),
        h("span", { class: "dim mono" }, fmtBytes(x.size)),
        h("a", { class: "btn sm icon ghost", href: `${x.url}?download=1`, title: "Download" }, icon("download")),
        h("button", { class: "btn sm icon ghost", title: "Delete", onclick: async () => { await api.del(x.url); loadExports(); } }, icon("trash"))));
    }
  }

  const previewExport = (x) => previewFile(x);

  function reprocessDialog() {
    const current = (meta?.analysis_opts?.pose_model) || "";
    const modelSel = h("select", {}, h("option", { value: "" }, "Default (from Settings)"),
      ...MODELS.map(([v, t]) => h("option", { value: v, selected: v === current }, t)));
    const det = h("select", {}, ...[["", "Default"], [640, "640 px (fast)"], [960, "960 px"], [1280, "1280 px (far athletes)"]]
      .map(([v, t]) => h("option", { value: v, selected: String(v) === String(meta?.analysis_opts?.detect_size ?? "") }, t)));
    const refine = h("input", { type: "checkbox", checked: meta?.analysis_opts?.refine ?? true });
    const all = h("input", { type: "checkbox" });
    const body = h("div", { class: "form" },
      h("label", {}, "Pose model"), modelSel,
      h("div", { class: "hint" }, "Benchmark on your clips: YOLO26-x had the least jitter and no head/feet flips; ~1.5× slower than m."),
      h("label", {}, "Detection size"), det,
      h("label", {}, "Inverted-pose refinement"), h("label", { class: "check" }, refine, "rotation-aware crops (recommended)"),
      h("label", {}, "Apply to"), h("label", { class: "check" }, all, "all videos in the library (queued one after another)"),
      h("div", { class: "hint", style: { gridColumn: "1 / -1", marginTop: "4px" } }, "Manual segment edits are replaced by the new automatic cut."));
    const go = h("button", { class: "btn primary", onclick: async () => {
      const opts = { from: "track", refine: refine.checked, all: all.checked };
      if (modelSel.value) opts.pose_model = modelSel.value;
      if (det.value) opts.detect_size = +det.value;
      const r = await api.post(`/api/videos/${vid}/reprocess`, opts);
      m.close();
      toast(all.checked ? `${r.queued} videos queued for re-analysis` : "Re-analysis started", "ok");
      A = null; tracks = null; poll();
    } }, icon("refresh"), "Re-analyse");
    const m = modal("Re-analyse", body, [h("button", { class: "btn ghost", onclick: () => m.close() }, "Cancel"), go]);
  }

  // ---------- rename ----------
  title.addEventListener("click", () => {
    title.contentEditable = "true"; title.focus();
    document.getSelection().selectAllChildren(title);
  });
  title.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); title.blur(); } });
  title.addEventListener("blur", async () => {
    if (title.contentEditable !== "true") return;
    title.contentEditable = "false";
    const name = title.textContent.trim();
    if (name && meta && name !== meta.name) { meta = await api.patch(`/api/videos/${vid}`, { name }); toast("Renamed", "ok"); }
  });

  // ---------- keyboard ----------
  function onKey(e) {
    if (e.target.closest("input, select, textarea, [contenteditable=true]")) return;
    const k = e.key;
    if (k === " ") { e.preventDefault(); togglePlay(); }
    else if (k === "ArrowLeft") { e.preventDefault(); e.shiftKey ? (video.pause(), seek(frame - Math.round(fps))) : step(-1); }
    else if (k === "ArrowRight") { e.preventDefault(); e.shiftKey ? (video.pause(), seek(frame + Math.round(fps))) : step(1); }
    else if (k === "ArrowUp") { e.preventDefault(); selectSeg(Math.max(0, sel - 1), true); }
    else if (k === "ArrowDown") { e.preventDefault(); selectSeg(Math.min(segs.length - 1, sel + 1), true); }
    else if (k === "[") setEdge("start");
    else if (k === "]") setEdge("end");
    else if (k === "n" || k === "N") addSegAtPlayhead();
    else if (k === "Delete") deleteSeg();
    else if (k === "l" || k === "L") $("button[title='Loop']", root)?.click();
  }
  document.addEventListener("keydown", onKey);

  poll();
  return () => {
    alive = false; clearTimeout(pollTimer); clearTimeout(saveTimer); clearTimeout(autoTimer); clearInterval(syncTimer);
    document.removeEventListener("keydown", onKey);
    video.pause(); video.removeAttribute("src"); video.load();
  };
}
