// App shell: router, library, uploads, settings, phone connect.
import { api, h, $, icon, toast, modal, fmtTime, fmtBytes, timeAgo, LOGO } from "./common.js";
import { mountStudio } from "./studio.js";
import { mountLiveDesk } from "./livedesk.js";
import { mountCompare } from "./compare.js";
import { mountCoach } from "./coach.js";
import { mountAthletes } from "./athletes.js";
import { mountExports } from "./exports.js";
import { mountAngles } from "./angles.js";
import { installHelp } from "./tour.js";

const state = { info: null, unmount: null };

// ---------------------------------------------------------------- header
$("#brand").innerHTML = `${LOGO}<span>GymCut</span><small>studio</small>`;
$("#btn-phone").append(icon("phone"), h("span", {}, "Connect phone"));
$("#btn-settings").append(icon("gear"));
$("#btn-phone").addEventListener("click", () => phoneModal());
$("#btn-settings").addEventListener("click", () => settingsModal());
installHelp($("#btn-help"));

async function loadInfo() {
  try {
    state.info = await api.get("/api/info");
    const chip = $("#gpu-chip");
    chip.classList.toggle("cpu", !state.info.gpu.cuda);
    chip.lastElementChild.textContent = state.info.gpu.cuda
      ? `${state.info.gpu.name.replace("NVIDIA GeForce ", "")}${state.info.nvenc ? " · NVENC" : ""}`
      : "CPU mode (slow)";
    if (!state.info.local) $("#btn-phone").classList.add("hidden");
  } catch (e) {
    toast("Cannot reach the GymCut server: " + e.message, "err");
  }
}

// background exports / 3D replays: small counter on the Exports tab
async function pollJobs() {
  try {
    const n = (await api.get("/api/jobs?active=1")).filter((j) => j.kind === "export" || j.kind === "replay3d").length;
    const b = $("#jobs-badge");
    b.textContent = n; b.classList.toggle("hidden", !n);
    b.title = `${n} export${n === 1 ? "" : "s"} in progress`;
    setTimeout(pollJobs, n ? 3000 : 8000);
  } catch { setTimeout(pollJobs, 15000); }
}
pollJobs();

// ---------------------------------------------------------------- router
function route() {
  state.unmount?.();
  state.unmount = null;
  const view = $("#view");
  view.innerHTML = "";
  const hash = location.hash || "#/";
  document.querySelectorAll(".nav a").forEach((a) => a.classList.remove("active"));
  let m;
  const query = hash.split("?")[1] || "";
  document.body.classList.toggle("coach-on", hash.startsWith("#/coach"));
  if ((m = hash.match(/^#\/v\/([\w-]+)/))) {
    state.unmount = mountStudio(view, m[1], Object.fromEntries(new URLSearchParams(query)));
  } else if (hash.startsWith("#/coach")) {
    $('.nav a[data-route="coach"]').classList.add("active");
    state.unmount = mountCoach(view, query);
  } else if (hash.startsWith("#/athletes")) {
    $('.nav a[data-route="athletes"]').classList.add("active");
    state.unmount = mountAthletes(view, query);
  } else if (hash.startsWith("#/compare")) {
    state.unmount = mountCompare(view, hash.split("?")[1] || "");
  } else if (hash.startsWith("#/angles")) {
    state.unmount = mountAngles(view, query);
  } else if (hash.startsWith("#/exports")) {
    $('.nav a[data-route="exports"]').classList.add("active");
    state.unmount = mountExports(view, () => state.info, query);
  } else if (hash.startsWith("#/live")) {
    $('.nav a[data-route="live"]').classList.add("active");
    state.unmount = mountLiveDesk(view, () => state.info, phoneModal);
  } else {
    $('.nav a[data-route="library"]').classList.add("active");
    state.unmount = mountLibrary(view);
  }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);

// ---------------------------------------------------------------- uploads
const uploads = new Map();
let uploadBox = null;

function renderUploads() {
  if (!uploadBox) return;
  uploadBox.innerHTML = "";
  for (const [id, u] of uploads) {
    uploadBox.append(h("div", { class: "upload-row" },
      icon("upload"), h("span", { class: "grow", style: { maxWidth: "260px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, u.name),
      h("div", { class: "bar" }, h("i", { style: { width: `${(u.p * 100).toFixed(1)}%` } })),
      h("span", { class: "mono" }, u.err ? "failed" : u.p >= 1 ? "processing…" : `${Math.round(u.p * 100)}% · ${fmtBytes(u.size)}`)));
  }
}

export function uploadFiles(files, onDone) {
  for (const f of files) {
    if (!/^video\//.test(f.type) && !/\.(mp4|mov|m4v|webm|mkv|avi|ogv|3gp|mts|wmv|mpg)$/i.test(f.name)) {
      toast(`${f.name}: not a video file`, "err");
      continue;
    }
    const id = Math.random().toString(36).slice(2);
    uploads.set(id, { name: f.name, p: 0, size: f.size });
    renderUploads();
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/upload?name=${encodeURIComponent(f.name)}`);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) { uploads.get(id).p = e.loaded / e.total; renderUploads(); } };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        toast(`Uploaded ${f.name} – analysing…`, "ok");
      } else {
        toast(`Upload failed: ${xhr.responseText}`, "err");
      }
      uploads.delete(id); renderUploads(); onDone?.();
    };
    xhr.onerror = () => { toast(`Upload failed: ${f.name}`, "err"); uploads.delete(id); renderUploads(); };
    xhr.send(f);
  }
}

// ---------------------------------------------------------------- library
function mountLibrary(root) {
  let timer = null, alive = true;
  const input = h("input", { type: "file", accept: "video/*,.mov,.mkv,.ogv", multiple: true, class: "hidden" });
  const dz = h("div", { class: "dropzone" },
    h("div", { class: "big-icon" }, icon("upload")),
    h("div", { class: "grow" },
      h("h2", {}, "Drop gymnastics videos here"),
      h("p", {}, "Phone videos (MOV/HEVC, slow-mo, any frame rate) are fine. GymCut finds the athlete, cuts out each skill and analyses it on your GPU.")),
    h("div", { class: "row wrap" },
      h("button", { class: "btn primary", onclick: () => input.click() }, icon("upload"), "Choose videos"),
      h("a", { class: "btn", href: "#/live" }, icon("live"), "Go live")),
    input);
  input.addEventListener("change", () => { uploadFiles([...input.files], refresh); input.value = ""; });
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
  dz.addEventListener("drop", (e) => uploadFiles([...e.dataTransfer.files], refresh));
  // allow dropping anywhere on the page
  const pageDrop = (e) => { e.preventDefault(); if (e.type === "drop") uploadFiles([...e.dataTransfer.files], refresh); };
  document.addEventListener("dragover", pageDrop);
  document.addEventListener("drop", pageDrop);

  uploadBox = h("div", { class: "uploads" });
  const grid = h("div");
  const filters = h("div", { class: "filters" });
  let filter = (() => { try { return localStorage.getItem("gc_lib_filter") ?? "__all"; } catch { return "__all"; } })();
  let selecting = false;
  let shownIds = [];
  const picked = new Set();
  const selectBtn = h("button", { class: "btn", title: "Tick videos to assign an athlete, export focus videos or make 3D replays" }, icon("check"), "Select");
  const assignBar = h("div", { class: "banner hidden selbar", style: { marginBottom: "14px" } });
  const endSelect = () => { selecting = false; picked.clear(); selectBtn.classList.remove("on"); refresh(); };
  selectBtn.addEventListener("click", () => { selecting ? endSelect() : (selecting = true, selectBtn.classList.add("on"), refresh()); });
  const exportsLink = (txt) => h("span", {}, txt, " ", h("a", { href: "#/exports" }, "Open Exports →"));
  async function focusVideos(ids) {
    const r = await api.post("/api/export-all", { ...(ids ? { vids: ids } : {}), mode: "reframe", opts: { aspect: "source", zoom: 1.0, overlay: false } });
    toast(exportsLink(`${r.queued} focus video${r.queued === 1 ? "" : "s"} queued.`), "ok");
  }
  const head = h("div", { class: "page-head" },
    h("div", { class: "grow" }, h("h1", {}, "Library"), h("p", {}, "Every upload and live clip, with detected skills.")),
    h("a", { class: "btn ghost", href: "#/exports", title: "Focus videos, 3D replays and other files you exported" }, icon("folder"), "Exports"),
    h("button", { class: "btn", title: "One zoomed-in, trimmed video per detected skill, for every video in the library", onclick: async () => {
      if (!confirm("Create a focus video (zoomed on the athlete, trimmed to the skill) for every detected skill in the library? Tip: to do only some videos, press Select first.")) return;
      focusVideos(null);
    } }, icon("download"), "Export every skill"), selectBtn);
  root.append(h("div", { class: "page" }, head, dz, uploadBox, filters, assignBar, grid));

  function renderAssignBar(athletes) {
    assignBar.classList.toggle("hidden", !selecting);
    if (!selecting) return;
    const ids = () => [...picked];
    const need = () => { if (!picked.size) { toast("Tick some videos first"); return false; } return true; };
    const allOn = shownIds.length > 0 && shownIds.every((id) => picked.has(id));
    const inp = h("input", { type: "text", list: "lib-ath", placeholder: "Athlete name", style: { width: "170px" } });
    assignBar.replaceChildren(
      h("button", { class: `btn sm ${allOn ? "on" : ""}`, title: "Select or clear every video shown", onclick: () => {
        if (allOn) shownIds.forEach((id) => picked.delete(id)); else shownIds.forEach((id) => picked.add(id));
        refresh();
      } }, icon("check"), allOn ? "Deselect all" : "Select all"),
      h("b", {}, `${picked.size} selected`),
      h("span", { class: "sep" }),
      inp, h("datalist", { id: "lib-ath" }, ...athletes.map((a) => h("option", { value: a }))),
      h("button", { class: "btn sm", onclick: async () => {
        if (!need()) return;
        const name = inp.value.trim() || null;
        for (const id of picked) await api.patch(`/api/videos/${id}`, { athlete: name });
        toast(`${picked.size} video${picked.size > 1 ? "s" : ""} → ${name || "unassigned"}`, "ok");
        endSelect();
      } }, "Assign athlete"),
      h("span", { class: "sep" }),
      h("button", { class: "btn sm", title: "Zoomed-in, trimmed video of every skill in the selected videos", onclick: async () => {
        if (!need()) return;
        await focusVideos(ids()); endSelect();
      } }, icon("download"), "Focus videos"),
      h("button", { class: "btn sm primary", title: "Unreal 3D replay (side, front, orbit + ghost of best) of every skill in the selected videos", onclick: async () => {
        if (!need()) return;
        const n = shownSkills(ids());
        if (!confirm(`Make 3D replays for ${n} skill${n === 1 ? "" : "s"}?

Unreal solves each attempt in about 6–15 minutes, one after another (≈ ${Math.round(n * 10 / 60 * 10) / 10} h). Attempts that already have a replay are skipped. You can keep using GymCut meanwhile.`)) return;
        const r = await api.post("/api/replay3d-batch", { vids: ids(), ghost: true });
        toast(exportsLink(`${r.queued} 3D replay${r.queued === 1 ? "" : "s"} queued${r.skipped ? `, ${r.skipped} already done` : ""}.`), "ok");
        endSelect();
      } }, icon("cube"), "3D replays"),
      h("button", { class: "btn sm", title: "Two videos of the same moment from different cameras: synced side by side", onclick: () => {
        if (picked.size !== 2) { toast("Tick exactly two videos of the same moment"); return; }
        const [a, b] = [...picked];
        location.hash = `#/angles?a=${a}&b=${b}`;
      } }, icon("flip"), "Compare angles"),
      h("span", { class: "spacer" }),
      h("button", { class: "btn sm ghost", onclick: () => endSelect() }, "Done"),
      h("span", { class: "dim", style: { flexBasis: "100%", fontSize: "12.5px" } }, "Tip: click a session date to select (or clear) the whole session."));
  }
  let lastVids = [];
  const shownSkills = (ids) => lastVids.filter((v) => ids.includes(v.id)).reduce((n, v) => n + (v.segments || 0), 0);
  renderUploads();

  async function refresh() {
    if (!alive) return;
    let vids = [];
    try { vids = await api.get("/api/videos"); } catch (e) { return; }
    lastVids = vids;
    grid.innerHTML = "";
    const names = [...new Set(vids.map((v) => v.athlete).filter(Boolean))].sort();
    const pill = (val, label) => h("button", { class: `pill ${filter === val ? "on" : ""}`, onclick: () => {
      filter = val; try { localStorage.setItem("gc_lib_filter", val); } catch { /* */ } refresh(); } }, label);
    filters.replaceChildren(...[pill("__all", `All (${vids.length})`),
      ...names.map((n) => pill(n, `${n} (${vids.filter((v) => v.athlete === n).length})`)),
      vids.some((v) => !v.athlete) ? pill("", `Unassigned (${vids.filter((v) => !v.athlete).length})`) : null,
      h("a", { class: "btn sm ghost", href: "#/athletes" }, "Progress →")].filter(Boolean));
    const shown = vids.filter((v) => filter === "__all" || (v.athlete || "") === filter);
    shownIds = shown.map((v) => v.id);
    renderAssignBar(names);
    if (!vids.length) {
      grid.append(h("div", { class: "empty" },
        h("h3", {}, "No videos yet"), h("p", {}, "Upload a clip above, or connect your phone and record live.")));
    }
    const byDate = new Map();
    for (const v of shown) { if (!byDate.has(v.recorded)) byDate.set(v.recorded, []); byDate.get(v.recorded).push(v); }
    for (const [d, list] of [...byDate.entries()].sort((a, b) => (a[0] < b[0] ? 1 : -1))) {
      const skills = list.reduce((n, v) => n + (v.segments || 0), 0);
      const hdr = h("div", { class: "group-h" }, h("h2", {}, new Date(d + "T12:00").toLocaleDateString(undefined, { weekday: "short", year: "numeric", month: "short", day: "numeric" })),
        h("span", { class: "dim" }, `${list.length} video${list.length > 1 ? "s" : ""} · ${skills} skill${skills === 1 ? "" : "s"}`));
      if (selecting) {
        hdr.style.cursor = "pointer";
        hdr.addEventListener("click", () => {
          const all = list.every((v) => picked.has(v.id));
          list.forEach((v) => (all ? picked.delete(v.id) : picked.add(v.id)));
          refresh();
        });
      }
      grid.append(hdr, h("div", { class: "grid" }, ...list.map((v) => videoCard(v, refresh, selecting ? picked : null))));
    }
    const busy = vids.some((v) => ["queued", "processing", "new"].includes(v.status)) || uploads.size;
    clearTimeout(timer);
    timer = setTimeout(refresh, busy ? 1200 : 5000);
  }
  refresh();
  return () => {
    alive = false; clearTimeout(timer); uploadBox = null;
    document.removeEventListener("dragover", pageDrop);
    document.removeEventListener("drop", pageDrop);
  };
}

function videoCard(v, refresh, picked = null) {
  const job = v.job;
  const status = v.status === "ready" ? "ready" : v.status;
  const thumb = h("div", { class: "thumb", style: v.has_thumb ? { backgroundImage: `url(/api/videos/${v.id}/thumb.jpg?t=${Math.floor(v.processed || v.created)})` } : {} },
    v.duration ? h("span", { class: "dur" }, fmtTime(v.duration, false)) : null,
    Object.keys(v.replays3d || {}).length ? h("span", { class: "src3d", title: "Has 3D replays – see Exports" }, `3D × ${Object.keys(v.replays3d).length}`) : null,
    v.source !== "upload" ? h("span", { class: `src ${v.source.startsWith("live") ? "live" : ""}` }, v.source === "live" ? "● LIVE CLIP" : v.source.toUpperCase()) : null);
  const statusText = {
    ready: `${v.segments || 0} segment${v.segments === 1 ? "" : "s"}`,
    processing: job ? `${job.stage} ${Math.round(job.progress * 100)}%` : "processing…",
    queued: "queued", new: "uploading…", error: "error", cancelled: "cancelled",
  }[status] || status;
  const del = h("button", { class: "btn sm icon del", title: "Delete", onclick: async (e) => {
    e.stopPropagation();
    if (!confirm(`Delete "${v.name}" and its exports?`)) return;
    await api.del(`/api/videos/${v.id}`); refresh();
  } }, icon("trash"));
  const card = h("div", { class: `vcard ${picked && picked.has(v.id) ? "picked" : ""}`, onclick: () => {
    if (picked) { picked.has(v.id) ? picked.delete(v.id) : picked.add(v.id); refresh(); return; }
    location.hash = `#/v/${v.id}`;
  } },
    picked ? h("input", { type: "checkbox", class: "pick", checked: picked.has(v.id), onclick: (e) => e.stopPropagation(), onchange: () => { picked.has(v.id) ? picked.delete(v.id) : picked.add(v.id); refresh(); } }) : null,
    thumb, picked ? null : del,
    h("div", { class: "meta" },
      h("div", { class: "title", title: v.name }, v.name),
      h("div", { class: "sub" }, h("span", { class: `status ${status}` }, h("span", { class: "d" }), statusText),
        v.athlete ? h("span", { class: "ath" }, "· " + v.athlete) : h("span", { class: "dim" }, "· " + timeAgo(v.created))),
      job ? h("div", { class: "bar" }, h("i", { style: { width: `${job.progress * 100}%` } })) : null,
      v.status === "error" ? h("div", { class: "sub", style: { color: "var(--bad)" } }, v.error || "failed") : null,
      (v.labels || []).length ? h("div", { class: "labels" }, ...(v.labels || []).slice(0, 4).map((l) => h("span", { class: "chip accent", title: l }, l))) : null));
  return card;
}

// ---------------------------------------------------------------- modals
export async function phoneModal() {
  if (!state.info) await loadInfo();
  const i = state.info;
  if (!i.lan || !i.phone_url) {
    modal("Connect a phone", h("p", {}, "LAN access is disabled. Start GymCut without --no-lan to stream from a phone."));
    return;
  }
  const url = i.phone_url;
  const net = h("div", { class: "card net-card" });
  async function renderNet(st) {
    st = st || (await api.get("/api/info")).internet;
    const on = st.state === "on" && st.phone_url;
    const btn = h("button", { class: `btn sm ${st.state === "off" || st.state === "error" ? "primary" : ""}`, onclick: async () => {
      btn.disabled = true;
      try { renderNet(await api.post("/api/tunnel", { on: st.state === "off" || st.state === "error" })); }
      catch (e) { toast(e.message, "err"); btn.disabled = false; }
    } }, st.state === "off" || st.state === "error" ? "Turn on internet access" : "Turn off");
    net.replaceChildren(h("div", { class: "card-b col", style: { gap: "10px" } },
      h("div", { class: "row" }, h("b", { class: "grow" }, "Away from home – over the internet"), i.local ? btn : null),
      !st.installed ? h("div", { class: "dim" }, "Needs cloudflared: run  winget install Cloudflare.cloudflared  once.") : null,
      st.state === "starting" ? h("div", { class: "dim" }, st.error || "Opening a secure tunnel… (a few seconds)") : null,
      st.state === "error" ? h("div", { style: { color: "var(--bad)" } }, st.error) : null,
      on ? h("div", { class: "row", style: { gap: "18px", alignItems: "flex-start", flexWrap: "wrap" } },
        h("div", { class: "qr" }, h("img", { src: `/api/qr.svg?text=${encodeURIComponent(st.phone_url)}`, alt: "QR code for the phone camera page over the internet" })),
        h("div", { class: "grow col", style: { minWidth: "220px", gap: "6px" } },
          h("ol", { class: "steps" },
            h("li", {}, "Scan this QR code now (or send yourself the link) – it works on any network, no certificate warning."),
            h("li", {}, "At the gym: open it, allow the camera, start. Data saver is on automatically (≈1–2 Mbit/s, roughly 0.7 GB per hour)."),
            h("li", {}, "Keep this PC on with GymCut running – it is kept awake while internet access is on.")),
          h("span", { class: "dim" }, "Phone camera page:"), h("code", { class: "u" }, st.phone_url),
          h("span", { class: "dim" }, "Full studio (Coach mode etc.):"), h("code", { class: "u" }, st.app_url),
          h("div", { class: "dim", style: { fontSize: "12px" } }, "The address changes each time internet access is turned on. Anyone with the link (it contains your access key) can open GymCut – don't share it."))) : null,
      st.state === "off" ? h("div", { class: "dim", style: { fontSize: "12.5px" } }, "Turn on to stream from a phone that is not on this Wi-Fi (mobile data or the gym's Wi-Fi). Uses a free Cloudflare tunnel – no router setup.") : null));
    if (st.state === "starting") setTimeout(() => net.isConnected && renderNet(), 1500);
  }
  renderNet(i.internet);
  const body = h("div", { class: "col", style: { gap: "14px" } },
    h("div", { class: "row", style: { gap: "18px", alignItems: "flex-start", flexWrap: "wrap" } },
      h("div", { class: "qr" }, h("img", { src: `/api/qr.svg?text=${encodeURIComponent(url)}`, alt: "QR code for the phone camera page" })),
      h("div", { class: "grow col", style: { minWidth: "220px" } },
        h("b", {}, "Stream your phone camera"),
        h("ol", { class: "steps" },
          h("li", {}, "Put the phone on the same Wi-Fi as this PC."),
          h("li", {}, "Scan the QR code with the camera app."),
          h("li", {}, "The page uses a self-signed certificate: tap ", h("i", {}, "Advanced → Proceed"), " (Android) or ", h("i", {}, "Show details → visit this website"), " (iPhone)."),
          h("li", {}, "Allow camera access, point it at the athlete. Skills are cut automatically.")),
        h("div", { class: "dim", style: { fontSize: "12.5px" } }, "If the phone cannot connect, allow Python through Windows Firewall on private networks (Windows asks the first time GymCut starts)."))),
    h("div", { class: "col", style: { gap: "4px" } },
      h("span", { class: "dim" }, "Phone camera page:"), h("code", { class: "u" }, url),
      h("span", { class: "dim", style: { marginTop: "6px" } }, "Full studio on another PC/tablet (Wi-Fi):"), h("code", { class: "u" }, i.lan_url || i.phone_app_url)),
    net);
  modal("Connect a phone", body);
}

async function settingsModal() {
  const s = await api.get("/api/settings");
  if (!state.info) await loadInfo();
  const models = state.info?.models || [];
  const sel = (id, opts, val) => h("select", { id }, ...opts.map(([v, t]) => h("option", { value: v, selected: String(v) === String(val) }, t)));
  const modelOpts = models.map((m) => [m, m.replace(".pt", "").replace("-pose", " pose") + (m.startsWith("yolo26") ? "" : " (older)")]);
  const body = h("div", { class: "form" },
    h("b", { class: "full" }, "Offline analysis"),
    h("label", { for: "s-model" }, "Pose model"), sel("s-model", modelOpts, s.pose_model),
    h("div", { class: "hint" }, "n/s = fastest, m = balanced (default), l/x = most accurate. Weights download on first use."),
    h("label", { for: "s-det" }, "Detection size"), sel("s-det", [[640, "640 px (fast)"], [960, "960 px (default)"], [1280, "1280 px (small/far athletes)"]], s.detect_size),
    h("label", { for: "s-ref" }, "Inverted-pose refinement"), h("label", { class: "check" }, h("input", { type: "checkbox", id: "s-ref", checked: s.refine }), "rotation-aware crop re-estimation"),
    h("div", { class: "hint" }, "Re-runs pose on upright-rotated crops of the athlete – much better on flips and handstands."),
    h("label", { for: "s-rsz" }, "Refinement crop size"), sel("s-rsz", [[320, "320"], [384, "384 (default)"], [448, "448"], [512, "512"]], s.refine_size),
    h("label", { for: "s-enc" }, "Video encoder"), sel("s-enc", [["auto", "Auto (NVENC if available)"], ["nvenc", "NVIDIA NVENC"], ["x264", "x264 (CPU)"]], s.encoder),
    h("label", { for: "s-af" }, "Auto focus videos"), h("label", { class: "check" }, h("input", { type: "checkbox", id: "s-af", checked: s.auto_focus }), "render a focus video for every new attempt"),
    h("label", { for: "s-h" }, "Default athlete height"), h("div", { class: "row" }, h("input", { type: "number", id: "s-h", min: 90, max: 220, placeholder: "e.g. 150", value: s.athlete_height_cm ?? "" , style: { width: "110px" } }), h("span", { class: "dim" }, "cm – enables m/s and metres")),
    h("b", { class: "full", style: { marginTop: "8px" } }, "Live capture"),
    h("label", { for: "s-lm" }, "Live model"), sel("s-lm", modelOpts, s.live.model),
    h("label", { for: "s-pre" }, "Pre-roll / post-roll"), h("div", { class: "row" },
      h("input", { type: "number", id: "s-pre", step: 0.5, min: 0, max: 10, value: s.live.pre_roll, style: { width: "80px" } }),
      h("input", { type: "number", id: "s-post", step: 0.5, min: 0, max: 10, value: s.live.post_roll, style: { width: "80px" } }), h("span", { class: "dim" }, "seconds")),
    h("label", { for: "s-aa" }, "Analyse live clips"), h("label", { class: "check" }, h("input", { type: "checkbox", id: "s-aa", checked: s.live.auto_analyze }), "run full analysis on every saved clip"));
  const save = h("button", { class: "btn primary", onclick: async () => {
    const hv = $("#s-h").value;
    await api.put("/api/settings", {
      pose_model: $("#s-model").value, detect_size: +$("#s-det").value, refine: $("#s-ref").checked,
      refine_size: +$("#s-rsz").value, encoder: $("#s-enc").value, athlete_height_cm: hv ? +hv : null, auto_focus: $("#s-af").checked,
      live: { model: $("#s-lm").value, pre_roll: +$("#s-pre").value, post_roll: +$("#s-post").value, auto_analyze: $("#s-aa").checked },
    });
    toast("Settings saved", "ok"); m.close();
  } }, "Save");
  const m = modal("Settings", body, [h("button", { class: "btn ghost", onclick: () => m.close() }, "Cancel"), save]);
}

// ---------------------------------------------------------------- boot
loadInfo();
route();
