// Guided tours ("walk-me"): a spotlight on one feature at a time with a short explanation.
// "?" in the header → tour of this page, full tour of GymCut, keyboard shortcuts.
// First visit: a small welcome card offers the full tour (once).
import { h, icon, modal } from "./common.js";

const LS = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

// Target: CSS selector, optionally narrowed to the element whose text contains `text`.
function find(sel, text) {
  const els = [...document.querySelectorAll(sel)].filter((e) => e.getClientRects().length);
  if (!text) return els[0] || null;
  return els.find((e) => e.textContent.trim().toLowerCase().includes(text.toLowerCase())) || null;
}
const card = (title) => ({ sel: ".card", text: title });

// ---------------------------------------------------------------- the tours
const TOURS = {
  library: [
    { title: "Welcome to GymCut", text: "GymCut finds the athlete in your videos, cuts every skill out, analyses it on your GPU and turns it into coaching feedback – ratings, focus videos, 3D replays and live 3D. This tour points out where everything is.", center: true },
    { sel: ".nav", title: "The main pages", text: "<b>Library</b> – all videos · <b>Athletes</b> – progress per athlete · <b>Coach</b> – courtside big screen · <b>Live</b> – phone / camera streaming · <b>Exports</b> – every file GymCut made." },
    { sel: ".dropzone", title: "Add videos", text: "Drop clips here or press <b>Choose videos</b>. Any phone video works (slow-mo too). Each one is analysed automatically: athlete, skills, phases, angles, landing." },
    { sel: ".filters", title: "Filter by athlete", text: "Show one athlete's videos, or the ones nobody is assigned to yet. <b>Progress →</b> opens the athlete page." },
    { sel: ".page-head .btn", text: "Select", title: "Select videos", body: "Tick videos (or <b>Select all</b>, or click a session date) to <b>assign an athlete</b>, make <b>focus videos</b> (zoomed on the athlete, trimmed to each skill) or queue <b>3D replays</b> for all their skills." },
    { sel: ".nav", title: "Compare angles", body: "Filmed the same moment with two phones? Select both videos → <b>Compare angles</b>: they are synced automatically and play side by side with what each camera detected.", center: true },
    { sel: ".page-head .btn", text: "Export every skill", title: "Export every skill", body: "One focus video per detected skill, for the whole library. Files appear on the <b>Exports</b> page." },
    { sel: ".vcard", title: "A video", text: "Thumbnail, number of skills, athlete and the detected skills. A pink <b>3D × n</b> badge means it has 3D replays. Click to open it in the studio.", optional: true },
    { sel: "#btn-phone", title: "Connect a phone", text: "Shows a QR code: scan it on a phone on the same Wi-Fi to stream live or upload straight from the phone." },
    { sel: "#gpu-chip", title: "Hardware", text: "The pose model runs on your RTX 3070; video encoding uses NVENC." },
    { sel: "#btn-help", title: "Help is always here", text: "Press <b>?</b> any time for a tour of the page you're on, the full tour, or the keyboard shortcuts." },
  ],
  studio: [
    { sel: ".studio-head", title: "The video studio", text: "Everything about one video: rename it, set the <b>athlete</b> (for personal bests), see which pose model analysed it." },
    { sel: ".player", title: "Player with pose overlay", text: "Skeleton (left = cyan, right = orange), angles, centre-of-mass trail and a HUD with the current element and FEET / HANDS / AIR phase." },
    { sel: ".controls", title: "Playback", text: "Frame stepping, speed, skeleton / angles / trail / loop toggles, and the <b>ghost</b> – the athlete's best attempt of this skill overlaid. <b>Pick person</b> if GymCut chose the coach instead of the athlete." },
    { sel: ".filmstrip", title: "Attempts", text: "Each detected skill, with its traffic light. Click one to jump to it." },
    { sel: ".timeline-wrap", title: "Timeline", text: "Activity curve, phases and the skill segments. Drag segment edges to trim, drag empty space to add one, wheel to zoom." },
    { sel: ".seg-toolbar", title: "Auto-cut", text: "How skills are cut (each skill, tumbling passes, whole routine…) and how sensitive. <b>Re-detect</b> re-cuts with the new settings." },
    { sel: ".btn3d", title: "3D replay", text: "<b>Make 3D replay</b>: Unreal reconstructs the body in 3D (≈6 min) and renders side, front and orbit views with the personal-best ghost. Then this button becomes <b>Watch 3D</b>." },
    { sel: ".studio-head a.btn", text: "Coach mode", title: "Coach mode", body: "The big-screen courtside view – also has the interactive 3D view." },
    { sel: ".studio-head .btn", text: "Re-analyse", title: "Re-analyse", body: "Run the analysis again, optionally with a different pose model or detection size." },
    { sel: ".studio-side", title: "Side panel", text: "<b>Rating</b> (traffic lights, 3D-measured items marked 3D) · <b>Analysis</b> (elements, landing, notes) · <b>Export</b> (clips, skeleton video, focus video, sequence photo, CSV, Unreal) · <b>Athlete & scale</b>. Hide it with <b>Panel</b>.", optional: true },
  ],
  coach: [
    { sel: ".coach-bar", title: "Coach mode", text: "Made for talking an athlete through each attempt courtside. Pick the athlete and session; <b>Follow new</b> jumps to every new attempt as it is analysed (uploads or live phone clips)." },
    { sel: ".coach-bar .btn", text: "Ghost of best", title: "Ghost of best", body: "Overlays the athlete's best attempt of the same skill, aligned at takeoff (key <b>G</b>)." },
    { sel: ".seg-ctl", title: "Slow motion", text: "¼×, ½× or real speed. <b>Space</b> pauses, <b>,</b> and <b>.</b> step frame by frame." },
    { sel: ".coach-bar .btn", text: "3D", title: "Interactive 3D view", body: "Opens the 3D athlete next to the video, synced to it: pause, slow down and <b>drag to orbit</b> around the athlete – angles you can't get from the footage. Camera presets Side / Follow / Front / Top / Orbit. If an attempt isn't solved yet the button says <b>Make 3D</b> (key <b>3</b>, <b>L</b> = big 3D)." },
    { sel: ".coach-stage", title: "Video (+ 3D)", text: "The attempt loops in slow motion with the skeleton and ghost. Click the video to pause." },
    { sel: ".coach-graph", title: "Motion pattern", text: "This attempt (green) against the personal best (pink) – synced by matching their motion, the same way Compare does. Where the curves split is where the attempts differ: later, higher, less inverted… Click the graph to jump there; pick the curve on the right (key <b>M</b> hides it).", optional: true },
    { sel: ".coach-side", title: "Talking points", text: "The overall light and big tiles: flight, height, rotation, landing and the worst technique items. Items marked <b>3D</b> come from the 3D reconstruction and don't depend on the camera angle." },
    { sel: ".coach .filmstrip", title: "All attempts of the session", text: "Click any attempt; ← / → step through them. <b>F</b> = full screen." },
  ],
  live: [
    { sel: ".live-stage", title: "Live", text: "Stream from a phone or camera: GymCut tracks the athlete in real time and saves every skill as a clip, which is then analysed and shows up in Coach mode." },
    { ...card("Sources"), title: "Start a stream", text: "<b>Phone camera</b> shows a QR code for a phone on the same Wi-Fi · this PC's webcam · or a network camera (IP Webcam app, RTSP)." },
    { ...card("Sessions"), title: "Sessions", text: "Every phone or camera that is streaming. Pick one to watch and control it." },
    { ...card("Selected session"), title: "Session controls", text: "<b>Auto-cut</b> records skills automatically (sensitivity slider) · <b>Record now</b> · lock the athlete by clicking them in the video · <b>3D view</b> shows the athlete in 3D next to the feed · <b>3D in Unreal</b> drives a MetaHuman live (Live Link subject “GymCut”)." },
    { ...card("Clips"), title: "Clips", text: "Each saved skill. Click to open it in the studio." },
  ],
  exports: [
    { sel: ".page-head", title: "Exports", text: "Everything GymCut has made: focus videos, 3D replays, clips, sequence photos and data. Saved on this PC – <b>Open folder</b> shows them in Explorer." },
    { sel: ".filters", title: "Filter", text: "By kind – 3D replays, focus videos, clips… – or search by video, skill or athlete.", optional: true },
    { sel: ".xcard", title: "A file", text: "Click to preview, <b>Download</b>, delete, or <b>Show in folder</b>. Running exports and 3D jobs show progress at the top and as a number on the Exports tab.", optional: true },
  ],
  athletes: [
    { sel: ".ath-layout .card", title: "Athletes", text: "Everyone with assigned videos. Assign athletes in the Library (Select → Assign) or in a video's studio." },
    { sel: ".ath-layout > .col", title: "Progress", text: "Per skill: attempts, best and average flight, landing rate and a light per attempt over time. Click a skill for its history.", optional: true },
    { sel: ".page-head .btn", text: "Coach mode", title: "Coach mode", body: "Jump to the courtside view for this athlete." },
  ],
};

// steps where the explanation lives in `body` (because `text` is used to find the element)
for (const steps of Object.values(TOURS)) {
  for (const s of steps) if (s.body) { s.find = s.text; s.text = s.body; }
}

function pageOf(hash = location.hash || "#/") {
  if (/^#\/v\//.test(hash)) return "studio";
  const m = hash.match(/^#\/(\w+)/);
  return m && TOURS[m[1]] ? m[1] : "library";
}

// Full tour: every page, navigating between them.
function fullTour() {
  const nav = (hash, wait) => ({ go: hash, wait });
  const steps = [];
  steps.push(...TOURS.library.slice(0, -1).map((s) => ({ ...s, ...nav("#/", ".page-head") })));
  steps.push({ title: "Opening a video…", text: "Next: the studio, where one video is analysed in detail.", center: true,
    go: () => { const v = document.querySelector(".vcard"); if (v) v.click(); return !!v; }, wait: ".studio-head", optional: true, group: "studio" });
  steps.push(...TOURS.studio.map((s) => ({ ...s, group: "studio" })));
  steps.push(...TOURS.coach.map((s) => ({ ...s, ...nav("#/coach", ".coach-bar") })));
  steps.push(...TOURS.live.map((s) => ({ ...s, ...nav("#/live", ".live-stage") })));
  steps.push(...TOURS.athletes.map((s) => ({ ...s, ...nav("#/athletes", ".page-head") })));
  steps.push(...TOURS.exports.map((s) => ({ ...s, ...nav("#/exports", ".page-head") })));
  steps.push({ ...TOURS.library.at(-1), ...nav("#/", ".page-head") });
  return steps;
}

// ---------------------------------------------------------------- engine
let active = null;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(sel, ms = 4000) {
  const t0 = performance.now();
  while (performance.now() - t0 < ms) {
    if (find(sel)) return true;
    await sleep(80);
  }
  return false;
}

export function startTour(which = "page") {
  active?.end();
  const steps = which === "full" ? fullTour() : TOURS[pageOf()];
  if (!steps?.length) return;
  LS.set("gc_tour_done", "1");

  const spot = h("div", { class: "tour-spot" });
  const bubble = h("div", { class: "tour-bubble", role: "dialog", "aria-live": "polite" });
  const layer = h("div", { class: "tour-layer" }, spot, bubble);
  document.body.append(layer);
  let i = 0, target = null, studioMissing = false, seq = 0;

  function place() {
    const vw = window.innerWidth, vh = window.innerHeight;
    if (!target) {
      spot.style.cssText = `left:${vw / 2}px;top:${vh / 2}px;width:0;height:0;`;
      bubble.style.left = `${Math.max(12, (vw - bubble.offsetWidth) / 2)}px`;
      bubble.style.top = `${Math.max(12, (vh - bubble.offsetHeight) / 2)}px`;
      bubble.dataset.side = "none";
      return;
    }
    const r = target.getBoundingClientRect();
    const pad = 6;
    const box = { x: r.left - pad, y: r.top - pad, w: r.width + pad * 2, h: Math.min(r.height + pad * 2, vh - 24) };
    spot.style.cssText = `left:${box.x}px;top:${box.y}px;width:${box.w}px;height:${box.h}px;`;
    const bw = bubble.offsetWidth, bh = bubble.offsetHeight, gap = 14;
    let side = box.y + box.h + gap + bh < vh ? "below" : box.y - gap - bh > 0 ? "above" : box.x + box.w + gap + bw < vw ? "right" : "left";
    let x, y;
    if (side === "below") { x = box.x; y = box.y + box.h + gap; }
    else if (side === "above") { x = box.x; y = box.y - gap - bh; }
    else if (side === "right") { x = box.x + box.w + gap; y = box.y; }
    else { x = box.x - gap - bw; y = box.y; }
    x = Math.min(Math.max(12, x), vw - bw - 12);
    y = Math.min(Math.max(12, y), vh - bh - 12);
    bubble.style.left = `${x}px`;
    bubble.style.top = `${y}px`;
    bubble.dataset.side = side;
  }

  async function show(k, dir = 1) {
    if (k < 0) k = 0;
    if (k >= steps.length) return end();
    const my = ++seq;                     // a newer show() (fast key presses) wins
    i = k;
    const s = steps[i];
    if (s.group === "studio" && studioMissing) return show(i + dir, dir);
    layer.classList.add("busy");
    if (typeof s.go === "function") {
      if (pageOf() !== "studio") {
        const ok = s.go();
        if (!ok) { studioMissing = true; layer.classList.remove("busy"); return show(i + dir, dir); }
      }
    } else if (s.go && location.hash !== s.go && !(s.go === "#/" && pageOf() === "library")) {
      location.hash = s.go;
    }
    if (s.wait) await waitFor(s.wait);
    if (!active || my !== seq) return;
    target = s.sel ? find(s.sel, s.find) : null;
    if (!target && s.sel && s.optional) { layer.classList.remove("busy"); return show(i + dir, dir); }
    if (target) {
      target.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" });
      await sleep(60);
      if (my !== seq) return;
    }
    bubble.replaceChildren(
      h("div", { class: "tour-step" }, `${i + 1} / ${steps.length}`),
      h("h3", {}, s.title),
      h("p", { html: s.text }),
      h("div", { class: "tour-actions" },
        h("button", { class: "btn sm ghost", onclick: () => end() }, "Skip tour"),
        h("span", { class: "spacer" }),
        i > 0 ? h("button", { class: "btn sm", onclick: () => show(i - 1, -1) }, "Back") : null,
        h("button", { class: "btn sm primary", onclick: () => show(i + 1, 1) }, i === steps.length - 1 ? "Done" : "Next")));
    layer.classList.remove("busy");
    place();
    bubble.querySelector(".btn.primary")?.focus({ preventScroll: true });
  }

  function onKey(e) {
    if (e.key === "Escape") { e.preventDefault(); end(); }
    else if (e.key === "ArrowRight" || e.key === "Enter") { e.preventDefault(); e.stopPropagation(); show(i + 1, 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); e.stopPropagation(); show(i - 1, -1); }
  }
  const onMove = () => place();
  function end() {
    if (!active) return;
    active = null;
    document.removeEventListener("keydown", onKey, true);
    window.removeEventListener("resize", onMove);
    window.removeEventListener("scroll", onMove, true);
    layer.remove();
  }
  document.addEventListener("keydown", onKey, true);
  window.addEventListener("resize", onMove);
  window.addEventListener("scroll", onMove, true);
  active = { end };
  show(0);
}

export function showShortcuts() {
  const rows = [
    ["Video studio", [["Space", "play / pause"], ["← →", "previous / next frame (Shift = 1 s)"], ["↑ ↓", "previous / next skill"], ["[ ]", "set segment start / end at the playhead"], ["N", "new segment"], ["Del", "delete segment"], ["L", "loop"]]],
    ["Coach mode", [["Space", "pause"], [", .", "frame step"], ["← →", "previous / next attempt"], ["G", "ghost of best on / off"], ["3", "3D view (or Make 3D)"], ["L", "split / big 3D"], ["F", "full screen"]]],
    ["3D view", [["drag", "orbit"], ["wheel", "zoom"], ["right-drag", "pan"]]],
    ["Tours", [["→ / Enter", "next step"], ["←", "back"], ["Esc", "close the tour"]]],
  ];
  modal("Keyboard shortcuts", h("div", { class: "kbd-grid" },
    ...rows.map(([title, keys]) => h("div", {},
      h("h4", {}, title),
      ...keys.map(([k, d]) => h("div", { class: "kbd-row" }, h("kbd", {}, k), h("span", {}, d)))))));
}

// "?" button + menu, and the one-time welcome offer
export function installHelp(btn) {
  btn.append(h("span", { class: "help-q" }, "?"));
  btn.title = "Tours and help";
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    document.querySelector(".help-menu")?.remove();
    const page = pageOf();
    const names = { library: "Library", studio: "video studio", coach: "Coach mode", live: "Live", exports: "Exports", athletes: "Athletes" };
    const menu = h("div", { class: "help-menu" },
      h("button", { onclick: () => { menu.remove(); startTour("page"); } }, icon("play"), `Tour of the ${names[page]} page`),
      h("button", { onclick: () => { menu.remove(); startTour("full"); } }, icon("skipf"), "Full tour of GymCut"),
      h("button", { onclick: () => { menu.remove(); showShortcuts(); } }, icon("gear"), "Keyboard shortcuts"));
    const r = btn.getBoundingClientRect();
    menu.style.top = `${r.bottom + 6}px`;
    menu.style.right = `${Math.max(8, window.innerWidth - r.right)}px`;
    document.body.append(menu);
    setTimeout(() => document.addEventListener("click", () => menu.remove(), { once: true }), 0);
  });
  if (!LS.get("gc_tour_done")) {
    setTimeout(() => {
      if (LS.get("gc_tour_done") || active) return;
      const w = h("div", { class: "tour-welcome" },
        h("b", {}, "New to GymCut?"),
        h("p", {}, "A 2-minute tour shows where everything is."),
        h("div", { class: "row" },
          h("button", { class: "btn sm primary", onclick: () => { w.remove(); startTour("full"); } }, "Take the tour"),
          h("button", { class: "btn sm ghost", onclick: () => { w.remove(); LS.set("gc_tour_done", "1"); } }, "Not now")),
        h("div", { class: "dim", style: { fontSize: "12px" } }, "You can always start it from the ? button."));
      document.body.append(w);
    }, 1500);
  }
}
