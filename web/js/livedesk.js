// Desktop "Live" tab: watch any live session (phone, webcam, IP camera) and manage auto-clipping.
import { api, h, icon, toast, drawSkeleton, containTransform, setupHiDPI, timeAgo } from "./common.js";

export function mountLiveDesk(root, getInfo, phoneModal) {
  let alive = true, sel = null, ws = null, timer = null;
  const canvas = h("canvas");
  const placeholder = h("div", { class: "ph" },
    h("h3", { style: { margin: "0 0 6px", color: "var(--text)" } }, "No live session selected"),
    h("p", { style: { margin: 0 } }, "Start the phone camera page or a network camera – sessions appear on the right."));
  const view = h("div", { class: "live-view" }, canvas, placeholder);
  const box3d = h("div", { class: "live-3d hidden" });
  const liveStage = h("div", { class: "live-stage" }, view, box3d);
  let v3 = null, view3dOn = false;
  async function setView3d(on, s) {
    view3dOn = on;
    box3d.classList.toggle("hidden", !on);
    liveStage.classList.toggle("split", on);
    if (on && !v3) {
      const { createView3D } = await import("./view3d.js");
      v3 = createView3D(box3d);
      v3.setHud([h("span", { class: "v3d-chip a" }, "live 3D"), h("span", { class: "v3d-chip", style: { color: "var(--text-2)" } }, "waiting for the athlete…")]);
    }
    if (s) api.post(`/api/live/sessions/${s.id}`, { view3d: on }).catch((e) => toast(e.message, "err"));
  }
  const meterFill = h("i", { style: { width: "0%" } }), meterTh = h("b", { style: { left: "50%" } });
  const recDot = h("span", { class: "rec-dot" }, "—");
  const stats = h("span", { class: "mono dim" });
  const sessList = h("div");
  const ctrl = h("div", { class: "card-b col" });
  const clips = h("div", { class: "col", style: { gap: "6px" } });
  const urlIn = h("input", { type: "url", placeholder: "rtsp://… or http://phone-ip:8080/video or 0", style: { flex: 1, minWidth: 0 } });

  root.append(h("div", { class: "page" },
    h("div", { class: "page-head" }, h("div", { class: "grow" }, h("h1", {}, "Live"),
      h("p", {}, "Stream from a phone or camera; GymCut tracks the athlete in real time and saves every skill as a clip."))),
    h("div", { class: "live-layout" },
      h("div", { class: "col", style: { gap: "12px" } }, liveStage,
        h("div", { class: "row" }, recDot, h("div", { class: "meter grow" }, meterFill, meterTh), stats)),
      h("div", { class: "col", style: { gap: "12px" } },
        h("div", { class: "card" }, h("div", { class: "card-h" }, h("h3", {}, "Sources")),
          h("div", { class: "card-b col" },
            h("button", { class: "btn primary", onclick: () => phoneModal() }, icon("phone"), "Phone camera (QR code)"),
            h("a", { class: "btn", href: "/live", target: "_blank" }, icon("camera"), "This PC's webcam (new tab)"),
            h("div", { class: "dim", style: { fontSize: "12.5px", marginTop: "4px" } }, "Network camera – Android “IP Webcam” app, RTSP action cams, or a webcam index:"),
            h("div", { class: "row" }, urlIn, h("button", { class: "btn", onclick: startCam }, "Start")))),
        h("div", { class: "card" }, h("div", { class: "card-h" }, h("h3", {}, "Sessions")), sessList),
        h("div", { class: "card" }, h("div", { class: "card-h" }, h("h3", {}, "Selected session")), ctrl),
        h("div", { class: "card" }, h("div", { class: "card-h" }, h("h3", {}, "Clips")), h("div", { class: "card-b" }, clips))))));

  async function startCam() {
    const url = urlIn.value.trim();
    if (!url) return;
    try {
      const s = await api.post("/api/live/camera", { url });
      toast("Camera session started", "ok");
      select(s.id);
    } catch (e) { toast(e.message, "err"); }
  }

  async function refresh() {
    if (!alive) return;
    let list = [];
    try { list = await api.get("/api/live/sessions"); } catch { /* server down */ }
    sessList.innerHTML = "";
    const open = list.filter((s) => !s.closed);
    if (!list.length) sessList.append(h("div", { class: "card-b dim" }, "No sessions yet."));
    for (const s of list.slice(0, 12)) {
      sessList.append(h("div", { class: `session ${s.id === sel ? "sel" : ""}`, onclick: () => select(s.id) },
        h("div", { class: "row" }, h("b", {}, s.name), h("span", { class: "spacer" }),
          s.closed ? h("span", { class: "chip" }, "ended") : h("span", { class: `rec-dot ${s.state === "rec" ? "rec" : ""}` }, s.state === "rec" ? "REC" : "live")),
        h("div", { class: "dim", style: { fontSize: "12.5px" } }, `${s.kind} · ${s.fps} fps · ${s.clips.length} clips · ${timeAgo(s.created)}`)));
    }
    if (!sel && open.length) select(open[0].id);
    const cur = list.find((s) => s.id === sel);
    if (cur && cur.view3d !== view3dOn) setView3d(!!cur.view3d);
    renderCtrl(cur);
    clips.innerHTML = "";
    const all = list.flatMap((s) => s.clips.map((c) => ({ ...c, session: s.name }))).reverse();
    if (!all.length) clips.append(h("div", { class: "dim" }, "Clips appear here as skills are detected."));
    for (const c of all.slice(0, 15)) {
      clips.append(h("a", { class: "export-row", href: `#/v/${c.id}` }, icon("scissors"), h("span", { class: "n" }, c.name), h("span", { class: "mono dim" }, `${c.duration.toFixed(1)}s`)));
    }
    timer = setTimeout(refresh, 1000);
  }

  function renderCtrl(s) {
    ctrl.innerHTML = "";
    if (!s) { ctrl.append(h("div", { class: "dim" }, "Select a session.")); return; }
    const sens = h("input", { type: "range", min: 0, max: 1, step: 0.05, value: s.cfg.sensitivity ?? 0.5, style: { flex: 1 } });
    sens.addEventListener("change", () => api.post(`/api/live/sessions/${s.id}`, { sensitivity: +sens.value }));
    ctrl.append(
      h("div", { class: "row wrap" },
        h("button", { class: `btn sm ${s.armed ? "on" : ""}`, onclick: () => api.post(`/api/live/sessions/${s.id}`, { armed: !s.armed }) }, icon("wand"), s.armed ? "Auto-cut on" : "Auto-cut off"),
        h("button", { class: "btn sm", onclick: () => api.post(`/api/live/sessions/${s.id}`, { record: s.state !== "rec" }) }, icon(s.state === "rec" ? "stop" : "record"), s.state === "rec" ? "Stop rec" : "Record now"),
        h("button", { class: "btn sm", onclick: () => api.post(`/api/live/sessions/${s.id}`, { lock: null }) }, icon("lock"), "Auto athlete"),
        h("button", { class: "btn sm danger", onclick: async () => { await api.del(`/api/live/sessions/${s.id}`); } }, icon("x"), "End")),
      h("label", { class: "row", style: { fontSize: "13px", color: "var(--text-2)" } }, "Sensitivity", sens),
      h("div", { class: "row wrap" },
        h("button", { class: `btn sm ${s.view3d ? "on" : ""}`, title: "Live 3D reconstruction of the athlete next to the camera feed – drag to orbit (no Unreal needed)",
          onclick: () => setView3d(!s.view3d, s) }, icon("cube"), s.view3d ? "Hide 3D view" : "3D view"),
        h("span", { class: "dim", style: { fontSize: "12px" } }, s.view3d ? `${s.ue3d?.fps ?? 0} fps · tracking ${Math.round((s.ue3d?.tracking ?? 0) * 100)}%` : "")),
      ue3dRow(s),
      h("div", { class: "dim", style: { fontSize: "12.5px" } }, "Click a person in the video to lock them as the athlete."));
  }

  // live 3D: drive a MetaHuman in the Unreal editor from this session
  function ue3dRow(s) {
    const st = s.ue3d || { state: "off" };
    const on = st.mine && (st.state === "on" || st.state === "starting");
    const btn = h("button", { class: `btn sm btn3d ${on ? "has" : ""}`, title: "Lift the athlete to 3D (MediaPipe) and drive a MetaHuman live in the Unreal editor", onclick: async () => {
      try { await api.post(`/api/live/sessions/${s.id}`, { ue3d: !on }); }
      catch (e) { toast(e.message, "err"); }
    } }, icon("cube"), on ? "Stop 3D in Unreal" : "3D in Unreal");
    let txt = "";
    if (st.mine && st.state === "starting") txt = st.message || "starting…";
    else if (st.mine && st.state === "on") txt = `${st.mode === "livelink" ? "Live Link · subject GymCut" : "puppet"} · ${st.fps ?? 0} fps · ${st.ms ?? 0} ms · tracking ${Math.round((st.tracking ?? 0) * 100)}%`;
    else if (st.state === "error") txt = st.error;
    else if (st.state === "on") txt = "running for another session";
    return h("div", { class: "col", style: { gap: "4px" } }, h("div", { class: "row wrap" }, btn,
      h("span", { class: "dim mono", style: { fontSize: "12px", color: st.state === "error" ? "var(--bad)" : "" } }, txt)),
      on ? h("div", { class: "dim", style: { fontSize: "12px" } }, "Unreal shows the athlete as a MetaHuman (Simulate mode, side camera following). With the GymCut Live Link plugin the stream is a real Live Link subject “GymCut” – record it with Take Recorder or drive any character. Use a tripod: travel and height come from the camera view.") : null);
  }

  function select(id) {
    if (sel === id && ws) return;
    sel = id;
    if (ws) { ws.close(); ws = null; }
    placeholder.classList.add("hidden");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/live/view/${id}`);
    ws.binaryType = "arraybuffer";
    ws.onmessage = async (ev) => {
      if (typeof ev.data === "string") {
        const m = JSON.parse(ev.data);
        if (m.type === "closed") { placeholder.classList.remove("hidden"); placeholder.querySelector("h3").textContent = "Session ended"; }
        return;
      }
      const dv = new DataView(ev.data);
      const n = dv.getUint32(0, true);
      const info = JSON.parse(new TextDecoder().decode(new Uint8Array(ev.data, 4, n)));
      const jpeg = new Blob([new Uint8Array(ev.data, 4 + n)], { type: "image/jpeg" });
      const bmp = await createImageBitmap(jpeg);
      draw(bmp, info);
    };
  }

  let ctx = null, cw = 0, ch = 0, lastInfo = null;
  function draw(bmp, info) {
    lastInfo = info;
    const r = view.getBoundingClientRect();
    if (r.width !== cw || r.height !== ch) { cw = r.width; ch = r.height; ctx = setupHiDPI(canvas, cw, ch); canvas.style.width = cw + "px"; canvas.style.height = ch + "px"; }
    const tf = containTransform(info.w, info.h, cw, ch);
    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, cw, ch);
    ctx.drawImage(bmp, tf.ox, tf.oy, info.w * tf.sx, info.h * tf.sy);
    for (const p of info.people) {
      if (p.id === info.athlete && p.kps) drawSkeleton(ctx, p.kps, tf);
      else {
        const [x1, y1, x2, y2] = p.box;
        ctx.strokeStyle = "rgba(255,255,255,.4)"; ctx.lineWidth = 1;
        ctx.strokeRect(x1 * tf.sx + tf.ox, y1 * tf.sy + tf.oy, (x2 - x1) * tf.sx, (y2 - y1) * tf.sy);
      }
    }
    if (view3dOn && v3 && info.p3d) {
      if (!v3._live) { v3._live = true; v3.setHud([h("span", { class: "v3d-chip a" }, "live 3D")]); }
      v3.setLivePose(info.p3d);
    }
    meterFill.style.width = `${Math.min(100, info.act * 100)}%`;
    meterTh.style.left = `${Math.min(100, info.on * 100)}%`;
    recDot.className = `rec-dot ${info.state === "rec" ? "rec" : ""}`;
    recDot.textContent = info.state === "rec" ? `REC ${info.rec_t.toFixed(1)}s` : info.armed ? "ARMED" : "IDLE";
    stats.textContent = `${info.fps.toFixed(0)} fps · GPU ${info.ms.toFixed(0)} ms · ${info.phase === "A" ? "AIR" : info.phase === "H" ? "HANDS" : "FEET"}`;
    bmp.close?.();
  }
  canvas.addEventListener("click", (e) => {
    if (!lastInfo || !sel) return;
    const r = canvas.getBoundingClientRect();
    const tf = containTransform(lastInfo.w, lastInfo.h, r.width, r.height);
    const x = (e.clientX - r.left - tf.ox) / tf.sx / lastInfo.w, y = (e.clientY - r.top - tf.oy) / tf.sy / lastInfo.h;
    api.post(`/api/live/sessions/${sel}`, { lock: { x, y } }).then(() => toast("Athlete locked"));
  });

  refresh();
  return () => { alive = false; clearTimeout(timer); ws?.close(); v3?.dispose(); };
}
