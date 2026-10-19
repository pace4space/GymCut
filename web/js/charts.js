// Canvas timeline (activity, phases, editable segments) and synced line charts.
import { setupHiDPI, fmtTime } from "./common.js";

const PHASE_COL = { A: "#6aa8ff", H: "#ffb347", F: "#3a4350", "-": "transparent" };

export class Timeline {
  constructor(canvas, { fps, n, activity, phases, cuts, onSeek, onChange, onSelect }) {
    this.c = canvas;
    this.fps = fps; this.n = n;
    this.act = activity || []; this.phases = phases || ""; this.cuts = cuts || [];
    this.onSeek = onSeek; this.onChange = onChange; this.onSelect = onSelect;
    this.segs = []; this.sel = -1; this.frame = 0;
    this.v0 = 0; this.v1 = n; this.thresh = null;
    this.H = 128; this.drag = null; this.hoverX = null;
    this._bind();
    this.resize();
    new ResizeObserver(() => this.resize()).observe(canvas.parentElement);
  }

  resize() {
    this.W = this.c.parentElement.clientWidth;
    this.c.style.height = this.H + "px";
    this.ctx = setupHiDPI(this.c, this.W, this.H);
    this.draw();
  }

  setSegments(segs, sel = this.sel) { this.segs = segs.map((s) => ({ ...s })); this.sel = sel; this.draw(); }
  setFrame(f) { this.frame = f; this.draw(); }
  setThreshold(t) { this.thresh = t; this.draw(); }
  x(f) { return ((f - this.v0) / (this.v1 - this.v0)) * this.W; }
  f(x) { return Math.max(0, Math.min(this.n - 1, Math.round(this.v0 + (x / this.W) * (this.v1 - this.v0)))); }

  zoomTo(s, e) {
    const pad = (e - s) * 0.25 + this.fps * 0.5;
    this.v0 = Math.max(0, s - pad); this.v1 = Math.min(this.n, e + pad);
    this.draw();
  }
  resetZoom() { this.v0 = 0; this.v1 = this.n; this.draw(); }

  draw() {
    const { ctx, W, H } = this;
    if (!ctx) return;
    ctx.clearRect(0, 0, W, H);
    const top = 16, bot = H - 16, ph = 10;
    // ruler
    ctx.fillStyle = "#6f7a88"; ctx.font = "10.5px ui-monospace, Consolas, monospace";
    const span = (this.v1 - this.v0) / this.fps;
    const step = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300].find((s) => span / s <= W / 70) || 600;
    const t0 = Math.ceil(this.v0 / this.fps / step) * step;
    for (let t = t0; t * this.fps <= this.v1; t += step) {
      const x = this.x(t * this.fps);
      ctx.fillRect(x, 0, 1, 5);
      ctx.fillText(step < 1 ? t.toFixed(2) : fmtTime(t, false), x + 3, 11);
    }
    // activity area
    const a0 = Math.max(0, Math.floor(this.v0)), a1 = Math.min(this.n, Math.ceil(this.v1));
    const ah = bot - top - ph - 4;
    const step2 = Math.max(1, Math.floor((a1 - a0) / W));
    ctx.beginPath();
    ctx.moveTo(this.x(a0), bot - ph - 4);
    for (let i = a0; i < a1; i += step2) {
      let v = 0;
      for (let k = i; k < Math.min(a1, i + step2); k++) v = Math.max(v, this.act[k] || 0);
      ctx.lineTo(this.x(i), bot - ph - 4 - v * ah);
    }
    ctx.lineTo(this.x(a1), bot - ph - 4);
    ctx.closePath();
    const g = ctx.createLinearGradient(0, top, 0, bot);
    g.addColorStop(0, "rgba(54,194,160,.55)"); g.addColorStop(1, "rgba(54,194,160,.05)");
    ctx.fillStyle = g; ctx.fill();
    ctx.strokeStyle = "rgba(54,194,160,.9)"; ctx.lineWidth = 1.2; ctx.stroke();
    if (this.thresh !== null) {
      const y = bot - ph - 4 - this.thresh * ah;
      ctx.setLineDash([4, 4]); ctx.strokeStyle = "rgba(255,255,255,.25)";
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); ctx.setLineDash([]);
    }
    // phases
    let runStart = a0, cur = this.phases[a0];
    for (let i = a0 + 1; i <= a1; i++) {
      const p = this.phases[i];
      if (i === a1 || p !== cur) {
        ctx.fillStyle = PHASE_COL[cur] || "transparent";
        ctx.fillRect(this.x(runStart), bot - ph, Math.max(1, this.x(i) - this.x(runStart)), ph);
        runStart = i; cur = p;
      }
    }
    // cuts
    ctx.fillStyle = "#ff5d5d";
    for (const c of this.cuts) if (c >= this.v0 && c <= this.v1) ctx.fillRect(this.x(c), top, 1.5, bot - top);
    // segments
    this.segs.forEach((s, i) => {
      const x0 = this.x(s.start), x1 = this.x(s.end);
      if (x1 < 0 || x0 > W) return;
      const sel = i === this.sel;
      ctx.fillStyle = sel ? "rgba(54,194,160,.20)" : "rgba(255,255,255,.07)";
      ctx.fillRect(x0, top, x1 - x0, bot - top);
      ctx.strokeStyle = sel ? "#36c2a0" : "rgba(255,255,255,.35)";
      ctx.lineWidth = sel ? 2 : 1;
      ctx.strokeRect(x0 + 0.5, top + 0.5, x1 - x0 - 1, bot - top - 1);
      // handles
      ctx.fillStyle = sel ? "#36c2a0" : "rgba(255,255,255,.55)";
      ctx.fillRect(x0 - 2, top + (bot - top) / 2 - 12, 4, 24);
      ctx.fillRect(x1 - 2, top + (bot - top) / 2 - 12, 4, 24);
      ctx.font = "700 11px Inter, system-ui, sans-serif";
      ctx.fillStyle = sel ? "#36c2a0" : "#cfd6df";
      if (x1 - x0 > 16) ctx.fillText(String(i + 1), x0 + 5, top + 13);
    });
    // drag preview
    if (this.drag && this.drag.mode === "new" && this.drag.moved) {
      const a = Math.min(this.drag.f0, this.drag.f1), b = Math.max(this.drag.f0, this.drag.f1);
      ctx.fillStyle = "rgba(54,194,160,.25)";
      ctx.fillRect(this.x(a), top, this.x(b) - this.x(a), bot - top);
    }
    // playhead
    const px = this.x(this.frame);
    ctx.fillStyle = "#fff"; ctx.fillRect(px - 1, 0, 2, H);
    ctx.beginPath(); ctx.moveTo(px - 5, 0); ctx.lineTo(px + 5, 0); ctx.lineTo(px, 6); ctx.fill();
    if (this.hoverX !== null && !this.drag) {
      const f = this.f(this.hoverX);
      ctx.fillStyle = "rgba(255,255,255,.3)"; ctx.fillRect(this.hoverX, top, 1, bot - top);
      const t = fmtTime(f / this.fps);
      ctx.font = "11px ui-monospace, Consolas, monospace";
      const w = ctx.measureText(t).width + 8;
      const tx = Math.min(W - w, this.hoverX + 6);
      ctx.fillStyle = "rgba(0,0,0,.8)"; ctx.fillRect(tx, bot - 32, w, 16);
      ctx.fillStyle = "#fff"; ctx.fillText(t, tx + 4, bot - 20);
    }
  }

  _hit(x) {
    for (let i = this.segs.length - 1; i >= 0; i--) {
      const s = this.segs[i];
      const x0 = this.x(s.start), x1 = this.x(s.end);
      if (Math.abs(x - x0) < 7) return { i, edge: "start" };
      if (Math.abs(x - x1) < 7) return { i, edge: "end" };
    }
    for (let i = this.segs.length - 1; i >= 0; i--) {
      const s = this.segs[i];
      if (x > this.x(s.start) && x < this.x(s.end)) return { i, edge: null };
    }
    return null;
  }

  _bind() {
    const c = this.c;
    const pos = (e) => { const r = c.getBoundingClientRect(); return e.clientX - r.left; };
    c.addEventListener("pointerdown", (e) => {
      c.setPointerCapture(e.pointerId);
      const x = pos(e), hit = this._hit(x);
      if (hit && hit.edge) {
        this.drag = { mode: "edge", i: hit.i, edge: hit.edge };
        if (this.sel !== hit.i) { this.sel = hit.i; this.onSelect?.(hit.i); }
      } else {
        this.drag = { mode: "new", f0: this.f(x), f1: this.f(x), moved: false, x0: x, hit };
      }
    });
    c.addEventListener("pointermove", (e) => {
      const x = pos(e);
      this.hoverX = x;
      if (!this.drag) {
        const hit = this._hit(x);
        c.style.cursor = hit && hit.edge ? "ew-resize" : "pointer";
        this.draw();
        return;
      }
      const f = this.f(x);
      if (this.drag.mode === "edge") {
        const s = this.segs[this.drag.i];
        if (this.drag.edge === "start") s.start = Math.min(f, s.end - 2);
        else s.end = Math.max(f + 1, s.start + 2);
        this.onSeek?.(this.drag.edge === "start" ? s.start : s.end - 1);
        this.onChange?.(this.segs, false);
      } else {
        this.drag.f1 = f;
        if (Math.abs(x - this.drag.x0) > 6) this.drag.moved = true;
      }
      this.draw();
    });
    c.addEventListener("pointerleave", () => { this.hoverX = null; this.draw(); });
    c.addEventListener("pointerup", (e) => {
      const d = this.drag; this.drag = null;
      if (!d) return;
      if (d.mode === "edge") {
        this.onChange?.(this.segs, true);
      } else if (d.moved) {
        const a = Math.min(d.f0, d.f1), b = Math.max(d.f0, d.f1) + 1;
        this.segs.push({ start: a, end: b, label: null });
        this.segs.sort((p, q) => p.start - q.start);
        this.sel = this.segs.findIndex((s) => s.start === a);
        this.onChange?.(this.segs, true);
        this.onSelect?.(this.sel);
      } else {
        if (d.hit && this.sel !== d.hit.i) { this.sel = d.hit.i; this.onSelect?.(d.hit.i); }
        this.onSeek?.(d.f0);
      }
      this.draw();
    });
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const x = pos(e), fx = this.v0 + (x / this.W) * (this.v1 - this.v0);
      if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        const d = (e.deltaX || e.deltaY) / this.W * (this.v1 - this.v0);
        const w = this.v1 - this.v0;
        this.v0 = Math.max(0, Math.min(this.n - w, this.v0 + d)); this.v1 = this.v0 + w;
      } else {
        const k = Math.exp(e.deltaY * 0.0015);
        const w = Math.max(this.fps * 0.5, Math.min(this.n, (this.v1 - this.v0) * k));
        this.v0 = Math.max(0, fx - (x / this.W) * w); this.v1 = Math.min(this.n, this.v0 + w);
        this.v0 = Math.max(0, this.v1 - w);
      }
      this.draw();
    }, { passive: false });
    c.addEventListener("dblclick", () => this.resetZoom());
  }
}

export class LineChart {
  constructor(canvas, { height = 170, onSeek } = {}) {
    this.c = canvas; this.H = height; this.onSeek = onSeek;
    this.series = []; this.range = [0, 1]; this.frame = 0; this.fps = 30;
    this.hover = null; this.yLabel = ""; this.bands = [];
    this._bind();
    new ResizeObserver(() => this.resize()).observe(canvas.parentElement);
    this.resize();
  }
  resize() {
    this.W = this.c.parentElement.clientWidth;
    this.c.style.height = this.H + "px";
    this.ctx = setupHiDPI(this.c, this.W, this.H);
    this.draw();
  }
  set({ series, range, fps, unit = "", yMin = null, yMax = null, bands = [], decimals = 0 }) {
    this.series = series; this.range = range; this.fps = fps; this.unit = unit; this.dec = decimals;
    this.yMinFix = yMin; this.yMaxFix = yMax; this.bands = bands;
    this.draw();
  }
  setFrame(f) { this.frame = f; this.draw(); }
  draw() {
    const { ctx, W, H } = this;
    if (!ctx) return;
    ctx.clearRect(0, 0, W, H);
    const L = 40, R = 10, T = 10, B = 22;
    const [f0, f1] = this.range;
    if (f1 <= f0 || !this.series.length) return;
    let lo = Infinity, hi = -Infinity;
    for (const s of this.series) for (let i = f0; i < f1; i++) {
      const v = s.data[i]; if (v === null || v === undefined) continue;
      lo = Math.min(lo, v); hi = Math.max(hi, v);
    }
    if (!isFinite(lo)) { ctx.fillStyle = "#6f7a88"; ctx.fillText("no data", L, H / 2); return; }
    if (this.yMinFix !== null) lo = Math.min(lo, this.yMinFix);
    if (this.yMaxFix !== null) hi = Math.max(hi, this.yMaxFix);
    const padv = (hi - lo) * 0.08 || 1; lo -= padv; hi += padv;
    const X = (f) => L + ((f - f0) / (f1 - f0)) * (W - L - R);
    const Y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
    this._X = X; this._L = L; this._R = R;
    // bands (element spans)
    for (const b of this.bands) {
      ctx.fillStyle = b.color; ctx.fillRect(X(b.start), T, X(b.end) - X(b.start), H - T - B);
    }
    // grid
    ctx.strokeStyle = "#262c35"; ctx.lineWidth = 1; ctx.fillStyle = "#6f7a88";
    ctx.font = "10.5px ui-monospace, Consolas, monospace";
    const ticks = 4;
    for (let k = 0; k <= ticks; k++) {
      const v = lo + (k / ticks) * (hi - lo), y = Y(v);
      ctx.beginPath(); ctx.moveTo(L, y); ctx.lineTo(W - R, y); ctx.stroke();
      ctx.fillText(v.toFixed(this.dec || 0), 4, y + 3);
    }
    const dur = (f1 - f0) / this.fps;
    const st = [0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 30].find((s) => dur / s <= 8) || 60;
    for (let t = 0; t <= dur; t += st) {
      const x = X(f0 + t * this.fps);
      ctx.fillText(t.toFixed(st < 1 ? 1 : 0) + "s", x - 6, H - 6);
    }
    // series
    for (const s of this.series) {
      ctx.strokeStyle = s.color; ctx.lineWidth = s.width || 1.8; ctx.setLineDash(s.dash || []); ctx.beginPath();
      let pen = false;
      for (let i = f0; i < f1; i++) {
        const v = s.data[i];
        if (v === null || v === undefined) { pen = false; continue; }
        if (!pen) { ctx.moveTo(X(i), Y(v)); pen = true; } else ctx.lineTo(X(i), Y(v));
      }
      ctx.stroke();
    }
    ctx.setLineDash([]);
    // playhead
    if (this.frame >= f0 && this.frame <= f1) {
      ctx.fillStyle = "rgba(255,255,255,.8)"; ctx.fillRect(X(this.frame) - 0.5, T, 1.5, H - T - B);
    }
    // legend / hover values
    const fr = this.hover ?? this.frame;
    let lx = L + 6;
    ctx.font = "600 11.5px Inter, system-ui, sans-serif";
    for (const s of this.series) {
      const v = s.data[Math.max(f0, Math.min(f1 - 1, fr))];
      const t = `${s.name} ${v === null || v === undefined ? "–" : v.toFixed(this.dec || 0) + (this.unit || "")}`;
      ctx.fillStyle = s.color; ctx.fillRect(lx, T + 3, 8, 8);
      ctx.fillStyle = "#cfd6df"; ctx.fillText(t, lx + 12, T + 11);
      lx += ctx.measureText(t).width + 26;
    }
    if (this.hover !== null) {
      ctx.fillStyle = "rgba(255,255,255,.35)"; ctx.fillRect(X(this.hover), T, 1, H - T - B);
    }
  }
  _bind() {
    const fr = (e) => {
      const r = this.c.getBoundingClientRect();
      const x = e.clientX - r.left;
      const [f0, f1] = this.range;
      const t = (x - this._L) / (this.W - this._L - this._R);
      return Math.max(f0, Math.min(f1 - 1, Math.round(f0 + t * (f1 - f0))));
    };
    this.c.addEventListener("pointermove", (e) => { if (this._X) { this.hover = fr(e); this.draw(); } });
    this.c.addEventListener("pointerleave", () => { this.hover = null; this.draw(); });
    this.c.addEventListener("click", (e) => { if (this._X) this.onSeek?.(fr(e)); });
  }
}
