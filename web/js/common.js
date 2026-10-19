// Shared helpers: API client, DOM utils, skeleton drawing, formatting.

export const api = {
  async get(url) {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error(await errText(r));
    return r.json();
  },
  async send(method, url, body) {
    const r = await fetch(url, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (!r.ok) throw new Error(await errText(r));
    return r.json();
  },
  post(url, body = {}) { return this.send("POST", url, body); },
  put(url, body) { return this.send("PUT", url, body); },
  patch(url, body) { return this.send("PATCH", url, body); },
  del(url) { return this.send("DELETE", url); },
};

async function errText(r) {
  try {
    const j = await r.json();
    return j.detail || JSON.stringify(j);
  } catch {
    return `${r.status} ${r.statusText}`;
  }
}

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export const $ = (sel, root = document) => root.querySelector(sel);

export function toast(msg, kind = "") {
  let box = $(".toasts");
  if (!box) { box = h("div", { class: "toasts" }); document.body.append(box); }
  const t = h("div", { class: `toast ${kind}` }, msg);
  box.append(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .3s"; }, 3800);
  setTimeout(() => t.remove(), 4200);
}

export function fmtTime(s, withMs = true) {
  if (s === null || s === undefined || !isFinite(s)) return "–";
  const m = Math.floor(s / 60);
  const sec = s - m * 60;
  return withMs ? `${m}:${sec.toFixed(2).padStart(5, "0")}` : `${m}:${Math.floor(sec).toString().padStart(2, "0")}`;
}

export function fmtNum(v, nd = 1, unit = "") {
  if (v === null || v === undefined || !isFinite(v)) return "–";
  return `${Number(v).toFixed(nd)}${unit}`;
}

export function fmtBytes(n) {
  if (n > 1e9) return (n / 1e9).toFixed(2) + " GB";
  if (n > 1e6) return (n / 1e6).toFixed(1) + " MB";
  return Math.round(n / 1e3) + " kB";
}

export function timeAgo(ts) {
  const d = Date.now() / 1000 - ts;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

export function modal(title, body, actions = []) {
  const bg = h("div", { class: "modal-bg" });
  const close = () => bg.remove();
  const m = h("div", { class: "modal", role: "dialog", "aria-label": title },
    h("div", { class: "card-h" }, h("h2", {}, title), h("span", { class: "spacer" }),
      h("button", { class: "btn ghost sm icon", onclick: close, "aria-label": "Close" }, icon("x"))),
    h("div", { class: "card-b" }, body),
    actions.length ? h("div", { class: "card-b row", style: { justifyContent: "flex-end", borderTop: "1px solid var(--line)" } }, ...actions) : null);
  bg.append(m);
  bg.addEventListener("mousedown", (e) => { if (e.target === bg) close(); });
  document.addEventListener("keydown", function esc(e) { if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc); } });
  document.body.append(bg);
  return { close, el: m };
}

// ---------- icons (inline SVG, stroke = currentColor) ----------
const ICONS = {
  play: '<path d="M7 4.5v15l12.5-7.5z" fill="currentColor" stroke="none"/>',
  pause: '<path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" fill="currentColor" stroke="none"/>',
  prev: '<path d="M15 6l-6 6 6 6"/>',
  next: '<path d="M9 6l6 6-6 6"/>',
  skipb: '<path d="M18 6l-6 6 6 6M11 6l-6 6 6 6"/>',
  skipf: '<path d="M6 6l6 6-6 6M13 6l6 6-6 6"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
  phone: '<rect x="7" y="2.5" width="10" height="19" rx="2"/><path d="M11 18.5h2"/>',
  live: '<circle cx="12" cy="12" r="3" fill="currentColor"/><path d="M6.3 6.3a8 8 0 0 0 0 11.4M17.7 6.3a8 8 0 0 1 0 11.4"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  scissors: '<circle cx="6" cy="7" r="2.5"/><circle cx="6" cy="17" r="2.5"/><path d="M8 8.5L20 18M8 15.5L20 6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  wand: '<path d="M4 20L16 8M14 4l1.5 1.5M19 9l1.5 1.5M18 3v3M21 6h-3"/>',
  person: '<circle cx="12" cy="6" r="2.5"/><path d="M12 9v6M8 12h8M12 15l-3 6M12 15l3 6"/>',
  download: '<path d="M12 4v12M7 11l5 5 5-5"/><path d="M4 20h16"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
  loop: '<path d="M4 12a6 6 0 0 1 6-6h8M15 3l3 3-3 3M20 12a6 6 0 0 1-6 6H6M9 21l-3-3 3-3"/>',
  bones: '<circle cx="12" cy="5" r="2"/><path d="M12 7v6M12 13l-4 7M12 13l4 7M7 10h10"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.6-4.5M4 4v4h4M4 13a8 8 0 0 0 14.6 4.5M20 20v-4h-4"/>',
  record: '<circle cx="12" cy="12" r="6" fill="currentColor" stroke="none"/>',
  stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="1.5" fill="currentColor" stroke="none"/>',
  camera: '<path d="M3 8h4l2-3h6l2 3h4v11H3z"/><circle cx="12" cy="13" r="3.5"/>',
  flip: '<path d="M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3M18 3v4h-4M6 21v-4h4"/>',
  folder: '<path d="M3 6.5a1.5 1.5 0 0 1 1.5-1.5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v9.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z"/>',
  cube: '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l8 4.5 8-4.5M12 12v9"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
};
export function icon(name, cls = "") {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("fill", "none");
  s.setAttribute("stroke", "currentColor");
  s.setAttribute("stroke-width", "2");
  s.setAttribute("stroke-linecap", "round");
  s.setAttribute("stroke-linejoin", "round");
  if (cls) s.setAttribute("class", cls);
  s.innerHTML = ICONS[name] || "";
  return s;
}

// ---------- skeleton drawing ----------
export const COLORS = { left: "#28c8eb", right: "#ff9628", mid: "#f0f0f0", com: "#78ff8c" };
const LIMBS = [
  [5, 7, "l"], [7, 9, "l"], [11, 13, "l"], [13, 15, "l"], [5, 11, "l"],
  [6, 8, "r"], [8, 10, "r"], [12, 14, "r"], [14, 16, "r"], [6, 12, "r"],
  [5, 6, "m"], [11, 12, "m"],
];

// kps: flat array [x,y,c]*17 in source pixels. tf = {sx, sy, ox, oy}
export function drawSkeleton(ctx, kps, tf, opts = {}) {
  if (!kps) return;
  const thr = opts.thr ?? 0.3;
  const lw = opts.lw ?? Math.max(2, Math.min(5, tf.sx * 3.2));
  const alpha = opts.alpha ?? 1;
  const X = (i) => kps[i * 3] * tf.sx + tf.ox;
  const Y = (i) => kps[i * 3 + 1] * tf.sy + tf.oy;
  const ok = (i) => kps[i * 3 + 2] >= thr && kps[i * 3] >= 0;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.lineCap = "round";
  for (const [a, b, s] of LIMBS) {
    if (!ok(a) || !ok(b)) continue;
    ctx.strokeStyle = "rgba(0,0,0,.6)";
    ctx.lineWidth = lw + 2.5;
    ctx.beginPath(); ctx.moveTo(X(a), Y(a)); ctx.lineTo(X(b), Y(b)); ctx.stroke();
    ctx.strokeStyle = opts.color || (s === "l" ? COLORS.left : s === "r" ? COLORS.right : COLORS.mid);
    ctx.lineWidth = lw;
    ctx.beginPath(); ctx.moveTo(X(a), Y(a)); ctx.lineTo(X(b), Y(b)); ctx.stroke();
  }
  if (ok(0) && ok(5) && ok(6)) {
    const nx = (X(5) + X(6)) / 2, ny = (Y(5) + Y(6)) / 2;
    ctx.strokeStyle = opts.color || COLORS.mid; ctx.lineWidth = lw * 0.8;
    ctx.beginPath(); ctx.moveTo(nx, ny); ctx.lineTo(X(0), Y(0)); ctx.stroke();
    const r = Math.hypot(X(5) - X(6), Y(5) - Y(6)) * 0.28 + lw;
    ctx.beginPath(); ctx.arc(X(0), Y(0), Math.max(lw * 1.6, Math.min(r, lw * 4)), 0, Math.PI * 2);
    ctx.fillStyle = "rgba(240,240,240,.25)"; ctx.fill();
    ctx.strokeStyle = opts.color || COLORS.mid; ctx.lineWidth = 1.5; ctx.stroke();
  }
  for (let i = 5; i < 17; i++) {
    if (!ok(i)) continue;
    ctx.beginPath(); ctx.arc(X(i), Y(i), lw * 0.95, 0, Math.PI * 2);
    ctx.fillStyle = "#000"; ctx.fill();
    ctx.beginPath(); ctx.arc(X(i), Y(i), lw * 0.7, 0, Math.PI * 2);
    ctx.fillStyle = opts.color || (i % 2 ? COLORS.left : COLORS.right); ctx.fill();
  }
  ctx.restore();
}

export function jointAngle(kps, a, b, c) {
  const ax = kps[a * 3] - kps[b * 3], ay = kps[a * 3 + 1] - kps[b * 3 + 1];
  const cx = kps[c * 3] - kps[b * 3], cy = kps[c * 3 + 1] - kps[b * 3 + 1];
  const n = Math.hypot(ax, ay) * Math.hypot(cx, cy);
  if (n < 1e-6) return null;
  return Math.acos(Math.max(-1, Math.min(1, (ax * cx + ay * cy) / n))) * 180 / Math.PI;
}

export function drawAngles(ctx, kps, tf) {
  const sets = [[11, 13, 15], [12, 14, 16], [5, 11, 13], [6, 12, 14], [5, 7, 9], [6, 8, 10]];
  ctx.save();
  ctx.font = "600 11.5px ui-monospace, Consolas, monospace";
  for (const [a, b, c] of sets) {
    if (Math.min(kps[a * 3 + 2], kps[b * 3 + 2], kps[c * 3 + 2]) < 0.3) continue;
    const ang = jointAngle(kps, a, b, c);
    if (ang === null) continue;
    const x = kps[b * 3] * tf.sx + tf.ox + 7, y = kps[b * 3 + 1] * tf.sy + tf.oy - 7;
    const t = `${Math.round(ang)}°`;
    const w = ctx.measureText(t).width;
    ctx.fillStyle = "rgba(0,0,0,.65)";
    ctx.fillRect(x - 3, y - 11, w + 6, 15);
    ctx.fillStyle = "#fff";
    ctx.fillText(t, x, y);
  }
  ctx.restore();
}

// Fit a w×h source into a box (object-fit: contain) -> transform
export function containTransform(srcW, srcH, boxW, boxH) {
  const s = Math.min(boxW / srcW, boxH / srcH);
  return { sx: s, sy: s, ox: (boxW - srcW * s) / 2, oy: (boxH - srcH * s) / 2 };
}

export function setupHiDPI(canvas, w, h) {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return ctx;
}

export const LOGO = `<svg viewBox="0 0 32 32" fill="none"><rect width="32" height="32" rx="8" fill="#36c2a0"/><circle cx="20.5" cy="8" r="2.6" fill="#062a21"/><path d="M8 24c3-1 5-3.5 6.5-7l2-4.5 4 3.5 3.5-1" stroke="#062a21" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/><path d="M16.5 12.5L12 10" stroke="#062a21" stroke-width="2.4" stroke-linecap="round"/></svg>`;

// Preview an exported file (video / image / other) in a modal.
export function previewFile(x, extra = []) {
  const url = x.url;
  const name = x.file || url.split("/").pop();
  let body;
  if (/\.mp4$/i.test(name)) body = h("video", { src: url, controls: true, autoplay: true, loop: true, playsinline: true, style: { width: "100%", maxHeight: "70vh", background: "#000", borderRadius: "8px" } });
  else if (/\.(jpg|png)$/i.test(name)) body = h("img", { src: url, style: { width: "100%", borderRadius: "8px" } });
  else body = h("div", { class: "col" }, h("p", {}, x.folder ? "Image sequence folder: " : "", x.folder ? h("code", { class: "u" }, x.folder) : null),
    h("a", { href: url, target: "_blank" }, "Open file"));
  const m = modal(name, body, [...extra, h("a", { class: "btn", href: `${url}?download=1` }, icon("download"), "Download"),
    h("button", { class: "btn primary", onclick: () => m.close() }, "Close")]);
  m.el.style.width = "min(1000px, 100%)";
  return m;
}
