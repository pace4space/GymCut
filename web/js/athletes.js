// Athletes: per-athlete skill progress across sessions (the "batch" view of the traffic lights).
import { api, h, icon, fmtNum } from "./common.js";
import { rateAttempt, skillKey, LIGHT } from "./rating.js";

export function mountAthletes(root, query) {
  const qs = new URLSearchParams(query || "");
  let athlete = qs.get("name");
  let openSkill = null;
  const listBox = h("div");
  const main = h("div", { class: "col", style: { gap: "14px", minWidth: 0 } });
  root.append(h("div", { class: "page" },
    h("div", { class: "page-head" }, h("div", { class: "grow" }, h("h1", {}, "Athletes"),
      h("p", {}, "Progress per skill across sessions. Assign athletes in the Library (Select) or in the studio header.")),
      h("a", { class: "btn primary", href: "#/coach" }, icon("live"), "Coach mode")),
    h("div", { class: "ath-layout" }, h("div", { class: "card" }, listBox), main)));

  let ath = [], segs = [];
  async function load() {
    [ath, segs] = await Promise.all([api.get("/api/athletes"), api.get("/api/segments")]);
    if (athlete === null || athlete === undefined) athlete = ath.find((a) => a.name)?.name ?? "";
    renderList(); renderMain();
  }

  function renderList() {
    listBox.replaceChildren(...ath.map((a) => h("div", { class: `ath-item ${(a.name || "") === athlete ? "sel" : ""}`,
      onclick: () => { athlete = a.name || ""; openSkill = null; renderList(); renderMain(); } },
      h("b", {}, a.name || "Unassigned"),
      h("div", { class: "dim", style: { fontSize: "12.5px" } }, `${a.attempts} attempts · ${a.sessions} session${a.sessions === 1 ? "" : "s"} · last ${a.last}`))));
  }

  function renderMain() {
    const mine = segs.filter((s) => (s.athlete || "") === athlete);
    if (!mine.length) { main.replaceChildren(h("div", { class: "card card-b empty" }, h("h3", {}, "No attempts"), h("p", {}, "Analyse videos for this athlete first."))); return; }
    const bySkill = new Map();
    for (const s of mine) { const k = skillKey(s); if (!bySkill.has(k)) bySkill.set(k, []); bySkill.get(k).push(s); }
    const sessions = new Set(mine.map((s) => s.recorded));
    const stuck = mine.filter((s) => s.landing?.result === "stuck").length;
    const landed = mine.filter((s) => s.landing?.result).length;
    const head = h("div", { class: "metrics", style: { gridTemplateColumns: "repeat(4, 1fr)" } },
      ...[["Attempts", mine.length], ["Skills", bySkill.size], ["Sessions", sessions.size],
        ["Stuck landings", landed ? `${Math.round((stuck / landed) * 100)}%` : "–"]].map(([k, v]) =>
        h("div", { class: "metric" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v))));
    const rows = [...bySkill.entries()].sort((a, b) => b[1].length - a[1].length).map(([skill, atts]) => {
      const fl = atts.map((a) => a.metrics?.max_flight_time).filter(Boolean);
      const st = atts.filter((a) => a.landing?.result === "stuck").length;
      const ld = atts.filter((a) => a.landing?.result).length;
      const dots = atts.map((a, i) => {
        const r = rateAttempt(a, atts.slice(0, i));
        return h("i", { title: `${a.recorded} · ${a.landing?.result || ""}`, style: { background: r.overall ? LIGHT[r.overall] : "var(--line-2)" } });
      });
      return h("tr", { class: "click", onclick: () => { openSkill = openSkill === skill ? null : skill; renderMain(); } },
        h("td", {}, h("b", {}, skill)), h("td", { class: "mono" }, atts.length),
        h("td", { class: "mono" }, fl.length ? Math.max(...fl).toFixed(2) + " s" : "–"),
        h("td", { class: "mono" }, fl.length ? (fl.reduce((x, y) => x + y, 0) / fl.length).toFixed(2) + " s" : "–"),
        h("td", { class: "mono" }, ld ? `${Math.round((st / ld) * 100)}%` : "–"),
        h("td", {}, h("div", { class: "dots" }, ...dots)),
        h("td", { class: "dim" }, atts.at(-1).recorded));
    });
    const table = h("table", { class: "skill-table" },
      h("tr", {}, ...["Skill", "Attempts", "Best flight", "Avg flight", "Stuck", "Trend (oldest → newest)", "Last"].map((t) => h("th", {}, t))),
      ...rows);
    const blocks = [head, h("div", { class: "card" }, table)];
    if (openSkill) {
      const atts = bySkill.get(openSkill) || [];
      blocks.push(h("div", { class: "card" }, h("div", { class: "card-h" }, h("h3", {}, `${openSkill} – attempts`)),
        h("div", { class: "card-b filmstrip", style: { flexWrap: "wrap" } }, ...atts.map((a, i) => {
          const r = rateAttempt(a, atts.slice(0, i));
          return h("a", { class: "film", href: `#/v/${a.vid}?seg=${a.id}`, style: { textDecoration: "none" } },
            h("div", { class: "film-img", style: { backgroundImage: `url(/api/videos/${a.vid}/segthumb/${a.id}.jpg)` } },
              h("span", { class: "film-num" }, i + 1), r.overall ? h("span", { class: "film-dot", style: { background: LIGHT[r.overall] } }) : null),
            h("div", { class: "film-lbl" }, a.recorded),
            h("div", { class: "film-sub" }, `${a.metrics?.max_flight_time ? `air ${fmtNum(a.metrics.max_flight_time, 2)}s · ` : ""}${a.landing?.result || ""}`));
        }))));
    }
    main.replaceChildren(...blocks);
  }
  load();
  return () => {};
}
