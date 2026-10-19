// Exports page: every file GymCut has produced (focus videos, 3D replays, clips, photos, CSV) + running jobs.
import { api, h, icon, toast, fmtBytes, timeAgo, previewFile } from "./common.js";

const KINDS = [["all", "All"], ["3d", "3D replays"], ["focus", "Focus videos"], ["clip", "Clips"],
  ["overlay", "Skeleton overlays"], ["photo", "Sequence photos"], ["csv", "Data"]];
const BADGE = { "3d": "3D", focus: "FOCUS", clip: "CLIP", overlay: "SKELETON", photo: "PHOTO", csv: "CSV", other: "FILE" };

export function mountExports(root, getInfo, query = "") {
  let alive = true, timer = 0;
  const params = new URLSearchParams(query);
  let kind = params.get("kind") || "all";
  let search = "";
  let info = getInfo();
  const local = () => !!info?.local;

  const openFolder = (body = {}) => api.post("/api/exports/open-folder", body)
    .catch((e) => toast(e.message, "err"));
  const folderCode = h("code", { class: "u" }, "…");
  const folderBtn = h("button", { class: "btn", onclick: () => openFolder() }, icon("folder"), "Open folder");
  const head = h("div", { class: "page-head" },
    h("div", { class: "grow" }, h("h1", {}, "Exports"),
      h("p", {}, "Everything GymCut has made for you: focus videos, 3D replays, clips, photos and data. Saved on the GymCut PC in ", folderCode)),
    folderBtn);
  const filters = h("div", { class: "filters" });
  const searchIn = h("input", { type: "search", placeholder: "Search video, skill or athlete", style: { width: "260px" } });
  searchIn.addEventListener("input", () => { search = searchIn.value.trim().toLowerCase(); render(); });
  const jobsBox = h("div", { class: "col", style: { gap: "8px", marginBottom: "16px" } });
  const grid = h("div");
  root.append(h("div", { class: "page" }, head, jobsBox, h("div", { class: "row", style: { marginBottom: "14px", flexWrap: "wrap", gap: "8px" } }, filters, h("span", { class: "spacer" }), searchIn), grid));

  let data = { files: [], folder: "" };
  let jobs = [];

  function render() {
    folderCode.textContent = data.folder;
    folderBtn.classList.toggle("hidden", !local());
    const counts = Object.fromEntries(KINDS.map(([k]) => [k, k === "all" ? data.files.length : data.files.filter((f) => f.kind === k).length]));
    filters.replaceChildren(...KINDS.filter(([k]) => k === "all" || counts[k]).map(([k, t]) =>
      h("button", { class: `pill ${kind === k ? "on" : ""}`, onclick: () => { kind = k; render(); } }, `${t} (${counts[k]})`)));
    const shown = data.files.filter((f) => (kind === "all" || f.kind === kind) &&
      (!search || `${f.file} ${f.video} ${f.athlete || ""}`.toLowerCase().includes(search)));
    grid.replaceChildren();
    if (!shown.length) {
      grid.append(h("div", { class: "empty" }, h("h3", {}, data.files.length ? "Nothing matches" : "No exports yet"),
        h("p", {}, "In the Library press “Select”, tick videos and choose Focus videos or 3D replays – or open a video and use the 3D replay / Export buttons.")));
      return;
    }
    // group by video, newest export first
    const groups = new Map();
    for (const f of shown) { if (!groups.has(f.vid)) groups.set(f.vid, []); groups.get(f.vid).push(f); }
    for (const [vid, list] of groups) {
      const f0 = list[0];
      grid.append(h("div", { class: "group-h" },
        h("h2", {}, h("a", { href: `#/v/${vid}` }, f0.video)),
        h("span", { class: "dim" }, [f0.athlete, f0.recorded, `${list.length} file${list.length > 1 ? "s" : ""}`].filter(Boolean).join(" · ")),
        local() ? h("button", { class: "btn sm ghost", onclick: () => openFolder({ vid }) }, icon("folder"), "Folder") : null),
        h("div", { class: "grid" }, ...list.map(card)));
    }
  }

  function card(f) {
    const skill = f.skill;
    const bg = f.kind === "photo" ? `url(${f.url})` : `url(/api/videos/${f.vid}/thumb.jpg)`;
    const open = () => previewFile(f, local() ? [h("button", { class: "btn", onclick: () => openFolder({ vid: f.vid, file: f.file }) }, icon("folder"), "Show in folder")] : []);
    return h("div", { class: "vcard xcard", onclick: open },
      h("div", { class: "thumb", style: { backgroundImage: bg } },
        h("span", { class: `src kind-${f.kind}` }, BADGE[f.kind] || "FILE"),
        /\.mp4$/i.test(f.file) ? h("span", { class: "playbadge" }, icon("play")) : null),
      h("div", { class: "meta" },
        h("div", { class: "title", title: f.file }, skill || f.kind_label),
        h("div", { class: "sub" }, h("span", {}, f.kind_label), h("span", { class: "dim" }, `· ${fmtBytes(f.size)} · ${timeAgo(f.mtime)}`)),
        h("div", { class: "row", style: { gap: "6px" } },
          h("a", { class: "btn sm", href: `${f.url}?download=1`, onclick: (e) => e.stopPropagation() }, icon("download"), "Download"),
          h("span", { class: "spacer" }),
          h("button", { class: "btn sm icon ghost", title: "Delete this file", onclick: async (e) => {
            e.stopPropagation();
            if (!confirm(`Delete ${f.file}?`)) return;
            await api.del(f.url); load();
          } }, icon("trash")))));
  }

  function renderJobs() {
    const list = jobs.filter((j) => j.kind === "export" || j.kind === "replay3d");
    jobsBox.replaceChildren();
    if (!list.length) return;
    const running = list.filter((j) => j.state === "running");
    const queued = list.length - running.length;
    jobsBox.append(h("div", { class: "row" }, h("b", {}, "Working on it"),
      h("span", { class: "dim" }, `${running.length} running · ${queued} queued`),
      h("span", { class: "spacer" }),
      h("button", { class: "btn sm ghost", onclick: async () => {
        if (!confirm("Cancel all queued and running exports?")) return;
        for (const j of list) await api.post(`/api/jobs/${j.id}/cancel`);
        load();
      } }, "Cancel all")));
    for (const j of list.slice(0, 8)) {
      jobsBox.append(h("div", { class: "banner processing", style: { flexWrap: "wrap" } },
        h("span", { class: "chip" }, j.kind === "replay3d" ? "3D" : "export"),
        h("span", { class: "grow" }, j.title, j.state === "running" ? h("span", { class: "dim" }, ` – ${j.stage}`) : h("span", { class: "dim" }, " – waiting")),
        j.message ? h("span", { class: "dim mono", style: { fontSize: "12px" } }, j.message) : null,
        h("div", { class: "bar" }, h("i", { style: { width: `${Math.round(j.progress * 100)}%` } })),
        h("button", { class: "btn sm ghost", onclick: () => api.post(`/api/jobs/${j.id}/cancel`).then(load) }, "Cancel")));
    }
    if (list.length > 8) jobsBox.append(h("div", { class: "dim" }, `…and ${list.length - 8} more`));
  }

  async function load() {
    if (!alive) return;
    clearTimeout(timer);
    try {
      const before = jobs.length;
      if (!info) info = getInfo() || await api.get("/api/info");
      [data, jobs] = await Promise.all([api.get("/api/exports"), api.get("/api/jobs?active=1")]);
      render(); renderJobs();
      if (before && !jobs.length) toast("All exports finished", "ok");
    } catch (e) { toast(e.message, "err"); }
    timer = setTimeout(load, jobs.length ? 2000 : 8000);
  }
  load();
  return () => { alive = false; clearTimeout(timer); };
}
