// Phone / webcam capture page: stream frames to the PC, draw returned pose, auto-clip skills.
import { $, h, icon, toast, drawSkeleton, containTransform, setupHiDPI, LOGO } from "./common.js";

$("#brand").innerHTML = LOGO;
const qs = new URLSearchParams(location.search);
const key = qs.get("k") || "";
if (key) $("#lib-link").href = `/?k=${encodeURIComponent(key)}#/`;

const video = $("#cam"), ov = $("#ov"), stage = $("#stage");
const S = {
  ws: null, stream: null, facing: "environment", running: false, inflight: 0, maxInflight: 2,
  last: null, armed: true, rec: false, size: 640, quality: 0.72, lat: 0, sent: 0,
  recorder: null, chunks: [], wake: null, clips: [], sid: null, lastSend: 0,
};
$("#sname").value = localStorageGet("gc_sname") || "";
// Over the internet (not a LAN address): data saver - smaller, lighter frames, at most 15 fps
const REMOTE = !/^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(location.hostname);
S.maxFps = REMOTE ? 15 : 30;
if (REMOTE) {
  S.size = 480; S.quality = 0.6;
  const res = $("#o-res");
  if (res) res.value = "480";
}

function localStorageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localStorageSet(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } }

// ---------- buttons ----------
const setBtn = (el, ic, label) => { el.replaceChildren(icon(ic), h("span", {}, label)); };
setBtn($("#btn-arm"), "wand", "Auto-cut on");
setBtn($("#btn-rec"), "record", "Record");
setBtn($("#btn-flip"), "flip", "Flip");
setBtn($("#btn-stop"), "stop", "Stop");

$("#btn-start").addEventListener("click", start);
$("#btn-stop").addEventListener("click", stop);
$("#btn-flip").addEventListener("click", async () => {
  S.facing = S.facing === "environment" ? "user" : "environment";
  if (S.running) { await openCamera(); }
});
$("#btn-arm").addEventListener("click", () => {
  S.armed = !S.armed;
  $("#btn-arm").classList.toggle("on", S.armed);
  setBtn($("#btn-arm"), "wand", S.armed ? "Auto-cut on" : "Auto-cut off");
  send({ type: "config", armed: S.armed });
});
$("#btn-rec").addEventListener("click", () => {
  const recording = S.last && S.last.state === "rec";
  send({ type: "record", on: !recording });
});
$("#o-sens").addEventListener("change", (e) => send({ type: "config", sensitivity: +e.target.value }));
$("#o-res").addEventListener("change", (e) => (S.size = +e.target.value));
$("#sname").addEventListener("change", (e) => { localStorageSet("gc_sname", e.target.value); send({ type: "config", name: e.target.value, athlete: e.target.value }); });

// tap to lock athlete
stage.addEventListener("click", (e) => {
  if (!S.running || !S.last || e.target.closest(".start")) return;
  const r = stage.getBoundingClientRect();
  const tf = containTransform(S.last.w, S.last.h, r.width, r.height);
  const x = (e.clientX - r.left - tf.ox) / tf.sx / S.last.w, y = (e.clientY - r.top - tf.oy) / tf.sy / S.last.h;
  if (x < 0 || x > 1 || y < 0 || y > 1) return;
  if (S.last.locked) { send({ type: "lock", x: null }); toast("Athlete unlocked – automatic selection"); }
  else { send({ type: "lock", x, y }); toast("Athlete locked"); }
});

function send(obj) { if (S.ws && S.ws.readyState === 1) S.ws.send(JSON.stringify(obj)); }

// ---------- camera ----------
async function openCamera() {
  if (S.stream) S.stream.getTracks().forEach((t) => t.stop());
  const constraints = { audio: false, video: { facingMode: { ideal: S.facing }, width: { ideal: 1920 }, height: { ideal: 1080 }, frameRate: { ideal: 30 } } };
  S.stream = await navigator.mediaDevices.getUserMedia(constraints);
  video.srcObject = S.stream;
  video.style.transform = S.facing === "user" ? "scaleX(-1)" : "";
  ov.style.transform = video.style.transform;
  await video.play();
}

async function start() {
  $("#start-err").textContent = "";
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    $("#start-err").textContent = "Camera needs HTTPS. Open the https:// link from the QR code in GymCut on your PC.";
    return;
  }
  try {
    await openCamera();
  } catch (e) {
    $("#start-err").textContent = `Camera error: ${e.message}. Allow camera access for this site.`;
    return;
  }
  $("#start").classList.add("hidden");
  ["#badge-rec", "#stats", "#hint-lock"].forEach((s) => $(s).classList.remove("hidden"));
  S.running = true;
  try { S.wake = await navigator.wakeLock?.request("screen"); } catch { /* optional */ }
  connect();
  if ($("#o-hq").checked) {
    if (REMOTE) toast("Full-quality copy over mobile data uploads ~75 MB per minute when you stop", "");
    startHQ();
  }
  loop();
}

function stop() {
  S.running = false;
  if (S.ws) { S.ws.close(); S.ws = null; }
  if (S.recorder && S.recorder.state !== "inactive") S.recorder.stop();
  if (S.stream) S.stream.getTracks().forEach((t) => t.stop());
  S.stream = null;
  try { S.wake?.release(); } catch { /* ignore */ }
  $("#start").classList.remove("hidden");
  $("#conn").classList.remove("ok");
  const ctx = ov.getContext("2d"); ctx.clearRect(0, 0, ov.width, ov.height);
}

// ---------- full-quality local recording ----------
function startHQ() {
  if (!window.MediaRecorder) { toast("This browser cannot record locally", "err"); return; }
  const types = ["video/mp4;codecs=avc1", "video/mp4", "video/webm;codecs=vp9", "video/webm"];
  const mimeType = types.find((t) => MediaRecorder.isTypeSupported(t)) || "";
  S.chunks = [];
  S.recorder = new MediaRecorder(S.stream, { mimeType, videoBitsPerSecond: 10_000_000 });
  S.recorder.ondataavailable = (e) => e.data.size && S.chunks.push(e.data);
  S.recorder.onstop = () => uploadHQ(new Blob(S.chunks, { type: S.recorder.mimeType || "video/mp4" }));
  S.recorder.start(1000);
}

function uploadHQ(blob) {
  if (!blob.size) return;
  const ext = blob.type.includes("mp4") ? "mp4" : "webm";
  const name = `${$("#sname").value || "Live"} full ${new Date().toLocaleTimeString().replace(/:/g, "-")}.${ext}`;
  const row = h("div", { class: "clip" }, icon("upload"), h("span", {}, "Uploading full-quality recording… "), h("b", { class: "mono" }, "0%"));
  $("#clips").prepend(row);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", `/api/upload?name=${encodeURIComponent(name)}&source=live-hq${key ? `&k=${encodeURIComponent(key)}` : ""}`);
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) row.lastChild.textContent = `${Math.round((e.loaded / e.total) * 100)}%`; };
  xhr.onload = () => { row.lastChild.textContent = xhr.status < 300 ? "done – analysing on the PC" : "failed"; };
  xhr.onerror = () => { row.lastChild.textContent = "failed"; };
  xhr.send(blob);
}

// ---------- websocket ----------
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${location.host}/ws/live?name=${encodeURIComponent($("#sname").value || "Phone")}${key ? `&k=${encodeURIComponent(key)}` : ""}`;
  const ws = new WebSocket(url);
  ws.binaryType = "arraybuffer";
  S.ws = ws; S.inflight = 0;
  ws.onopen = () => { $("#conn").classList.add("ok"); send({ type: "config", armed: S.armed, sensitivity: +$("#o-sens").value, athlete: $("#sname").value || null }); };
  ws.onclose = () => {
    $("#conn").classList.remove("ok");
    if (S.running) setTimeout(() => S.running && connect(), 1000);
  };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "pose") {
      S.inflight = Math.max(0, S.inflight - 1);
      if (m.ts) S.lat = 0.8 * S.lat + 0.2 * (performance.now() - m.ts);
      S.last = m;
      render(m);
    } else if (m.type === "clip") {
      addClip(m);
    } else if (m.type === "hello") {
      S.sid = m.session;
    } else if (m.type === "error") {
      toast(m.message, "err");
    } else if (m.type === "state") {
      S.rec = m.state === "rec";
    }
  };
}

const cap = document.createElement("canvas");
const capCtx = cap.getContext("2d", { willReadFrequently: false });

function loop() {
  if (!S.running) return;
  requestAnimationFrame(loop);
  const now = performance.now();
  if (!S.ws || S.ws.readyState !== 1 || S.inflight >= S.maxInflight || !video.videoWidth) return;
  if (now - S.lastSend < 1000 / S.maxFps) return;
  S.lastSend = now;
  const vw = video.videoWidth, vh = video.videoHeight;
  const sc = Math.min(1, S.size / Math.max(vw, vh) * (vw > vh ? 1 : 1));
  const w = Math.round(vw * sc), h2 = Math.round(vh * sc);
  if (cap.width !== w || cap.height !== h2) { cap.width = w; cap.height = h2; }
  capCtx.drawImage(video, 0, 0, w, h2);
  S.inflight++;
  cap.toBlob(async (blob) => {
    if (!blob || !S.ws || S.ws.readyState !== 1) { S.inflight = Math.max(0, S.inflight - 1); return; }
    const buf = new Uint8Array(await blob.arrayBuffer());
    const pkt = new Uint8Array(buf.length + 8);
    new DataView(pkt.buffer).setFloat64(0, now, true);
    pkt.set(buf, 8);
    S.ws.send(pkt);
    S.sent++;
  }, "image/jpeg", S.quality);
}

// ---------- rendering ----------
let ovCtx = null, ovW = 0, ovH = 0;
function render(m) {
  const r = stage.getBoundingClientRect();
  if (r.width !== ovW || r.height !== ovH) { ovW = r.width; ovH = r.height; ovCtx = setupHiDPI(ov, ovW, ovH); }
  ovCtx.clearRect(0, 0, ovW, ovH);
  const tf = containTransform(m.w, m.h, ovW, ovH);
  if ($("#o-skel").checked) {
    for (const p of m.people) {
      if (p.id === m.athlete && p.kps) drawSkeleton(ovCtx, p.kps, tf, { thr: 0.3 });
      else {
        const [x1, y1, x2, y2] = p.box;
        ovCtx.strokeStyle = "rgba(255,255,255,.35)"; ovCtx.lineWidth = 1;
        ovCtx.strokeRect(x1 * tf.sx + tf.ox, y1 * tf.sy + tf.oy, (x2 - x1) * tf.sx, (y2 - y1) * tf.sy);
      }
    }
    if (m.locked) {
      const a = m.people.find((p) => p.id === m.athlete);
      if (a) {
        const [x1, y1] = a.box;
        ovCtx.fillStyle = "#36c2a0"; ovCtx.font = "700 12px Inter, system-ui";
        ovCtx.fillText("LOCKED", x1 * tf.sx + tf.ox, y1 * tf.sy + tf.oy - 6);
      }
    }
  }
  $("#meter").style.width = `${Math.min(100, m.act * 100)}%`;
  $("#meter-th").style.left = `${Math.min(100, m.on * 100)}%`;
  const rec = m.state === "rec";
  const dot = $("#recdot");
  dot.className = `rec-dot ${rec ? "rec" : ""}`;
  dot.textContent = rec ? `REC ${m.rec_t.toFixed(1)}s` : m.armed ? "ARMED" : "IDLE";
  $("#btn-rec").classList.toggle("rec-on", rec);
  setBtn($("#btn-rec"), rec ? "stop" : "record", rec ? "Stop rec" : "Record");
  $("#stats").innerHTML = `${m.fps.toFixed(0)} fps · ${Math.round(S.lat)} ms<br>GPU ${m.ms.toFixed(0)} ms`;
  $("#phase").innerHTML = m.phase && m.phase !== "-" ? `<span class="phase-badge ${m.phase}">${{ A: "AIR", H: "HANDS", F: "FEET" }[m.phase]}</span>` : "";
  $("#hint-lock").textContent = m.locked ? "tap to unlock athlete" : "tap a person to lock";
}

function addClip(c) {
  if (!S.clips.length) $("#clips").innerHTML = "";
  S.clips.push(c);
  const link = `/${key ? `?k=${encodeURIComponent(key)}` : ""}#/v/${c.id}`;
  $("#clips").prepend(h("div", { class: "clip" }, icon("scissors"), h("span", {}, c.name), h("span", { class: "dim mono" }, `${c.duration.toFixed(1)}s`),
    h("a", { href: link, target: "_blank" }, "open")));
  toast(`Clip saved (${c.duration.toFixed(1)} s)`, "ok");
  if (navigator.vibrate) navigator.vibrate(60);
}

document.addEventListener("visibilitychange", async () => {
  if (document.visibilityState === "visible" && S.running) {
    try { S.wake = await navigator.wakeLock?.request("screen"); } catch { /* ignore */ }
  }
});
