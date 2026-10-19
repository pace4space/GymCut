// Interactive 3D view of a solved attempt (Coach mode) or of the live 3D stream (Live page).
// Drag to orbit, wheel to zoom; camera presets: side (matches the footage), follow, front, top.
// Joint data: Unreal body solve (cm, z up) -> three.js metres, y up. A stylised mannequin is
// used on purpose: the MetaHuman licence only covers rendering inside Unreal.
import * as THREE from "three";
import { OrbitControls } from "/static/vendor/three/OrbitControls.js";
import { h } from "./common.js";

const BONES = [
  ["pelvis", "spine_03", 0.11], ["spine_03", "spine_05", 0.12], ["spine_05", "neck_01", 0.07], ["neck_01", "head", 0.05],
  ["neck_01", "clavicle_l", 0.04], ["clavicle_l", "upperarm_l", 0.045], ["upperarm_l", "lowerarm_l", 0.045], ["lowerarm_l", "hand_l", 0.037],
  ["neck_01", "clavicle_r", 0.04], ["clavicle_r", "upperarm_r", 0.045], ["upperarm_r", "lowerarm_r", 0.045], ["lowerarm_r", "hand_r", 0.037],
  ["pelvis", "thigh_l", 0.075], ["thigh_l", "calf_l", 0.07], ["calf_l", "foot_l", 0.05], ["foot_l", "ball_l", 0.035],
  ["pelvis", "thigh_r", 0.075], ["thigh_r", "calf_r", 0.07], ["calf_r", "foot_r", 0.05], ["foot_r", "ball_r", 0.035],
];
const GHOST = 0xff4fd8;
export const LIVE_JOINTS = ["pelvis", "spine_03", "spine_05", "neck_01", "head",
  "clavicle_l", "upperarm_l", "lowerarm_l", "hand_l", "clavicle_r", "upperarm_r", "lowerarm_r", "hand_r",
  "thigh_l", "calf_l", "foot_l", "ball_l", "thigh_r", "calf_r", "foot_r", "ball_r"];
const Y = new THREE.Vector3(0, 1, 0);

// UE (x, y, z) cm, left-handed z-up  ->  three (x, z, y) metres, right-handed y-up
const toV = (a, i, out) => out.set(a[i] / 100, a[i + 2] / 100, a[i + 1] / 100);

class Mannequin {
  constructor(scene, joints, material, shadows) {
    this.joints = joints;
    this.idx = Object.fromEntries(joints.map((j, i) => [j, i]));
    this.group = new THREE.Group();
    const cyl = new THREE.CylinderGeometry(1, 1, 1, 14, 1, true);
    const sph = new THREE.SphereGeometry(1, 16, 12);
    this.bones = BONES.filter(([a, b]) => a in this.idx && b in this.idx).map(([a, b, r]) => {
      const m = new THREE.Mesh(cyl, material);
      m.userData = { a: this.idx[a], b: this.idx[b], r };
      m.castShadow = shadows;
      this.group.add(m);
      return m;
    });
    const jr = {};
    for (const [a, b, r] of BONES) { jr[a] = Math.max(jr[a] || 0, r); jr[b] = Math.max(jr[b] || 0, r); }
    this.balls = joints.map((j) => {
      const m = new THREE.Mesh(sph, material);
      m.scale.setScalar(jr[j] || 0.04);
      m.castShadow = shadows;
      this.group.add(m);
      return m;
    });
    this.head = new THREE.Mesh(sph, material);
    this.head.scale.set(0.095, 0.115, 0.1);
    this.head.castShadow = shadows;
    this.group.add(this.head);
    this.P = joints.map(() => new THREE.Vector3());
    scene.add(this.group);
  }

  pose(flatA, flatB, f) {          // interpolate two frames of flat [x, y, z, ...] arrays
    const tmp = new THREE.Vector3();
    for (let j = 0; j < this.joints.length; j++) {
      const i = j * 3;
      if (flatA[i] == null) continue;
      toV(flatA, i, this.P[j]);
      if (flatB && flatB[i] != null && f > 0) this.P[j].lerp(toV(flatB, i, tmp), f);
    }
    this.balls.forEach((b, j) => b.position.copy(this.P[j]));
    for (const m of this.bones) {
      const pa = this.P[m.userData.a], pb = this.P[m.userData.b];
      const d = tmp.subVectors(pb, pa);
      const len = d.length();
      m.position.addVectors(pa, pb).multiplyScalar(0.5);
      m.scale.set(m.userData.r, Math.max(len, 1e-3), m.userData.r);
      m.quaternion.setFromUnitVectors(Y, d.normalize());
    }
    const neck = this.P[this.idx.neck_01], head = this.P[this.idx.head];
    const dir = tmp.subVectors(head, neck).normalize();
    this.head.position.copy(head).addScaledVector(dir, 0.08);
    this.head.quaternion.setFromUnitVectors(Y, dir);
  }

  get pelvis() { return this.P[this.idx.pelvis]; }
  set visible(v) { this.group.visible = v; }
}

export function createView3D(container, opts = {}) {
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x14171c);
  scene.fog = new THREE.Fog(0x14171c, 14, 34);
  const camera = new THREE.PerspectiveCamera(42, 16 / 9, 0.05, 100);
  camera.position.set(0, 1.4, 6);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.target.set(0, 1, 0);
  controls.maxPolarAngle = Math.PI * 0.495;      // stay above the floor
  controls.minDistance = 1.2;
  controls.maxDistance = 25;
  controls.autoRotateSpeed = 2.2;

  scene.add(new THREE.HemisphereLight(0xdfe8ff, 0x2a2420, 1.4));
  const sun = new THREE.DirectionalLight(0xfff4e4, 2.2);
  sun.position.set(3, 8, 4);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -8, right: 8, top: 8, bottom: -8, near: 0.5, far: 30 });
  scene.add(sun, sun.target);
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(80, 80), new THREE.MeshStandardMaterial({ color: 0x2a2e35, roughness: 0.95 }));
  floor.rotation.x = -Math.PI / 2;
  floor.receiveShadow = true;
  scene.add(floor);
  const trackGroup = new THREE.Group();
  scene.add(trackGroup);

  // overlay: legend / labels / camera buttons
  const hud = h("div", { class: "v3d-hud" });
  const labels = h("div", { class: "v3d-labels" });
  const presetBar = h("div", { class: "v3d-presets" });
  container.classList.add("v3d");
  container.append(renderer.domElement, labels, hud, presetBar);

  let data = null, athlete = null, ghost = null, ghostOn = true, color = opts.color || 0x29a68c;
  let preset = "side", t = 0, travel = new THREE.Vector3(1, 0, 0), sideN = new THREE.Vector3(0, 0, 1), center = new THREE.Vector3();
  const focus = new THREE.Vector3();               // where the skill happens (takeoff of the flight element)
  let trailA = null, trailG = null, peakA = null, peakG = null, camX = null;
  const PRESETS = [["side", "Side"], ["follow", "Follow"], ["front", "Front"], ["top", "Top"], ["orbit", "Orbit"], ["free", "Free"]];
  function renderPresets() {
    presetBar.replaceChildren(...PRESETS.map(([k, label]) => h("button", { class: `btn sm ${preset === k ? "on" : ""}`, onclick: () => setPreset(k) }, label)));
  }
  renderPresets();
  controls.addEventListener("start", () => {
    if (preset !== "free") { preset = "free"; controls.autoRotate = false; renderPresets(); }
  });

  function clearAthletes() {
    for (const m of [athlete, ghost, ...extras.map((e) => e.m)]) if (m) scene.remove(m.group);
    extras = [];
    for (const o of [trailA, trailG, peakA, peakG]) if (o) scene.remove(o);
    trackGroup.clear();
    athlete = ghost = trailA = trailG = peakA = peakG = null;
  }

  function trail(frames, jIdx, col, opacity) {
    const pts = frames.map((f) => (f[jIdx * 3] == null ? null : toV(f, jIdx * 3, new THREE.Vector3()))).filter(Boolean);
    const g = new THREE.BufferGeometry().setFromPoints(pts);
    const line = new THREE.Line(g, new THREE.LineBasicMaterial({ color: col, transparent: true, opacity }));
    line.userData.n = pts.length;
    g.setDrawRange(0, 0);
    scene.add(line);
    return line;
  }

  function peakBar(p, col) {
    const m = new THREE.Mesh(new THREE.BoxGeometry(0.02, 0.02, 0.9), new THREE.MeshBasicMaterial({ color: col }));
    m.position.copy(p);
    m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), sideN);
    m.visible = false;
    scene.add(m);
    return m;
  }

  // additional figures (e.g. the two-camera fusion): {frames, fps, shift, color, opacity}
  let extras = [];
  function addExtra(e) {
    const mat = e.opacity < 1 ? new THREE.MeshBasicMaterial({ color: e.color, transparent: true, opacity: e.opacity, depthWrite: false })
      : new THREE.MeshStandardMaterial({ color: e.color, roughness: 0.4, metalness: 0.2 });
    const m = new Mannequin(scene, data.joints, mat, e.opacity >= 1);
    extras.push({ ...e, m });
    setTime(t);
  }
  function removeExtras() {
    for (const e of extras) scene.remove(e.m.group);
    extras = [];
  }
  function setData(d, opt = {}) {
    clearAthletes();
    data = d;
    if (!d) return;
    if (opt.color) color = opt.color;
    athlete = new Mannequin(scene, d.joints, new THREE.MeshStandardMaterial({ color, roughness: 0.45, metalness: 0.05 }), true);
    if (d.ghost) {
      ghost = new Mannequin(scene, d.joints, new THREE.MeshBasicMaterial({ color: GHOST, transparent: true, opacity: 0.32, depthWrite: false }), false);
    }
    // travel direction (UE heading -> three xz) and the side the real camera was on
    travel.set(Math.cos(d.heading || 0), 0, Math.sin(d.heading || 0)).normalize();
    sideN.set(-travel.z, 0, travel.x);
    const right = new THREE.Vector3().crossVectors(sideN.clone().negate(), Y);   // screen right seen from +sideN
    if (opt.screenDir && Math.sign(right.dot(travel)) !== Math.sign(opt.screenDir)) sideN.negate();
    // centre of the action
    const pel = d.frames.map((f) => toV(f, 0, new THREE.Vector3()));
    center.set(0, 0, 0);
    pel.forEach((p) => center.add(p));
    center.multiplyScalar(1 / Math.max(1, pel.length)).setY(0);
    const ka = Math.round((d.align || 0) * d.fps);
    if (pel[ka]) focus.copy(pel[ka]).addScaledVector(travel, 0.6).setY(0); else focus.copy(center);
    camX = null;
    // tumbling track + 1 m marks along the pass
    const len = 14;
    const track = new THREE.Mesh(new THREE.PlaneGeometry(len, 2), new THREE.MeshStandardMaterial({ color: 0x0b2a78, roughness: 0.8 }));
    track.rotation.x = -Math.PI / 2;
    track.rotation.z = -Math.atan2(travel.z, travel.x);
    track.position.copy(center).setY(0.004);
    track.receiveShadow = true;
    trackGroup.add(track);
    const markMat = new THREE.MeshBasicMaterial({ color: 0xbfc6d4 });
    for (let k = -7; k <= 7; k++) {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(0.03, k % 5 === 0 ? 2 : 0.5), markMat);
      m.rotation.x = -Math.PI / 2;
      m.rotation.z = -Math.atan2(travel.z, travel.x);
      m.position.copy(center).addScaledVector(travel, k).setY(0.006);
      trackGroup.add(m);
    }
    sun.target.position.copy(center);
    sun.position.copy(center).add(new THREE.Vector3(3, 8, 4));
    const pj = d.joints.indexOf("pelvis");
    trailA = trail(d.frames, pj, color, 0.9);
    if (d.ghost) trailG = trail(d.ghost.frames, pj, GHOST, 0.7);
    const pk = d.frames[Math.round(d.peak.t * d.fps)];
    if (pk) peakA = peakBar(toV(pk, pj * 3, new THREE.Vector3()), 0xffd94d);
    if (d.ghost) {
      const gk = d.ghost.frames[Math.round(d.ghost.peak.t * d.ghost.fps)];
      if (gk) peakG = peakBar(toV(gk, pj * 3, new THREE.Vector3()), GHOST);
    }
    setTime(t);
  }

  // live stream: one pose at a time (flat array in UE cm, ~10-15 Hz) - eased every rendered frame
  let liveTarget = null, liveCur = null;
  function setLivePose(flat, joints = LIVE_JOINTS) {
    if (!athlete || athlete.joints !== joints || data) {
      clearAthletes();
      data = null;
      athlete = new Mannequin(scene, joints, new THREE.MeshStandardMaterial({ color, roughness: 0.45 }), true);
      travel.set(1, 0, 0);
      sideN.set(0, 0, -1);                         // GymCut's live space: phone on -Y (UE) = -z here
      center.set(0, 0, 0);
      focus.set(0, 0, 0);
      const track = new THREE.Mesh(new THREE.PlaneGeometry(16, 2), new THREE.MeshStandardMaterial({ color: 0x0b2a78, roughness: 0.8 }));
      track.rotation.x = -Math.PI / 2;
      track.position.y = 0.004;
      track.receiveShadow = true;
      trackGroup.add(track);
      const markMat = new THREE.MeshBasicMaterial({ color: 0xbfc6d4 });
      for (let k = -8; k <= 8; k++) {
        const m = new THREE.Mesh(new THREE.PlaneGeometry(0.03, k % 5 === 0 ? 2 : 0.5), markMat);
        m.rotation.x = -Math.PI / 2;
        m.position.set(k, 0.006, 0);
        trackGroup.add(m);
      }
      liveCur = null;
      if (preset === "side") preset = "follow";
      renderPresets();
    }
    liveTarget = flat;
    if (!liveCur) liveCur = flat.slice();
  }
  function easeLive(dt) {
    if (!liveTarget || !liveCur || data) return;
    const a = 1 - Math.exp(-dt * 14);
    for (let i = 0; i < liveCur.length; i++) {
      if (liveTarget[i] != null && liveCur[i] != null) liveCur[i] += (liveTarget[i] - liveCur[i]) * a;
      else liveCur[i] = liveTarget[i];
    }
    athlete.pose(liveCur, null, 0);
  }

  function frameAt(frames, fps, time) {
    const x = Math.max(0, Math.min(frames.length - 1, time * fps));
    const i = Math.floor(x);
    return [frames[i], frames[Math.min(frames.length - 1, i + 1)], x - i, i];
  }

  function setTime(animT) {
    t = animT;
    if (!data || !athlete) return;
    for (const e of extras) {
      const te = t + (e.shift || 0);
      e.m.visible = te >= 0 && te <= e.frames.length / e.fps;
      if (e.m.group.visible) { const [ea, eb, ef] = frameAt(e.frames, e.fps, te); e.m.pose(ea, eb, ef); }
    }
    const [a, b, f, i] = frameAt(data.frames, data.fps, t);
    athlete.pose(a, b, f);
    trailA?.geometry.setDrawRange(0, Math.min(trailA.userData.n, i + 1));
    if (peakA) peakA.visible = t >= data.peak.t;
    if (ghost) {
      const g = data.ghost;
      const tg = t + g.shift;
      const show = ghostOn && tg >= g.from && tg <= g.frames.length / g.fps;
      ghost.visible = show;
      if (show) {
        const [ga, gb, gf, gi] = frameAt(g.frames, g.fps, tg);
        ghost.pose(ga, gb, gf);
        trailG?.geometry.setDrawRange(Math.round(g.from * g.fps), Math.max(0, Math.min(trailG.userData.n, gi + 1) - Math.round(g.from * g.fps)));
      }
      if (trailG) trailG.visible = ghostOn;
      if (peakG) peakG.visible = ghostOn && tg >= g.peak.t;
    }
  }

  function setGhost(on) { ghostOn = on; setTime(t); }
  function setPreset(k) { preset = k; camX = null; controls.autoRotate = k === "orbit"; renderPresets(); }

  // camera presets
  const tmp = new THREE.Vector3();
  function updateCamera(dt) {
    if (preset === "free" || !athlete) return;
    const p = athlete.pelvis;
    if (preset === "orbit") {                      // circle the athlete (works while paused too)
      controls.target.lerp(tmp.copy(p).setY(Math.max(0.8, p.y)), Math.min(1, dt * 3));
      const off = camera.position.clone().sub(controls.target);
      const want = 4.2;
      off.setLength(off.length() + (want - off.length()) * Math.min(1, dt * 2));
      if (off.y < 0.6) off.y += (0.9 - off.y) * Math.min(1, dt * 2);
      camera.position.copy(controls.target).add(off);
      return;
    }
    const along = tmp.subVectors(p, focus).dot(travel);
    camX = camX == null ? along : camX + (along - camX) * Math.min(1, dt * 3);
    const look = focus.clone().addScaledVector(travel, preset === "side" ? 0 : camX).setY(1.0);
    let pos;
    if (preset === "side") pos = focus.clone().addScaledVector(sideN, 5.6).setY(1.35);
    else if (preset === "follow") pos = look.clone().addScaledVector(sideN, 4.6).setY(1.4);
    else if (preset === "front") pos = focus.clone().addScaledVector(travel, 6.5).addScaledVector(sideN, 1.0).setY(1.6);
    else pos = focus.clone().addScaledVector(sideN, 0.01).setY(9.0);
    camera.position.lerp(pos, Math.min(1, dt * 4));
    controls.target.lerp(preset === "top" ? focus.clone().setY(0) : look, Math.min(1, dt * 4));
  }

  // labels (peak heights) follow their 3D anchors
  function updateLabels() {
    labels.replaceChildren();
    const r = renderer.domElement.getBoundingClientRect();
    const add = (mesh, text, cls) => {
      if (!mesh || !mesh.visible) return;
      const v = mesh.position.clone().add(new THREE.Vector3(0, 0.18, 0)).project(camera);
      if (v.z > 1) return;
      labels.append(h("div", { class: `v3d-label ${cls}`, style: { left: `${(v.x * 0.5 + 0.5) * r.width}px`, top: `${(-v.y * 0.5 + 0.5) * r.height}px` } }, text));
    };
    if (data) {
      add(peakA, `hips ${(data.peak.hip_cm / 100).toFixed(2)} m`, "a");
      if (data.ghost) add(peakG, `${data.ghost.tag || "best"} ${(data.ghost.peak.hip_cm / 100).toFixed(2)} m`, "g");
    }
  }

  function setHud(nodes) { hud.replaceChildren(...nodes.filter(Boolean)); }

  let w = 0, hgt = 0, last = performance.now(), raf = 0, alive = true;
  function resize() {
    const r = container.getBoundingClientRect();
    if (r.width === w && r.height === hgt) return;
    w = r.width; hgt = r.height;
    renderer.setSize(w, hgt, false);
    renderer.domElement.style.width = `${w}px`;
    renderer.domElement.style.height = `${hgt}px`;
    camera.aspect = w / Math.max(1, hgt);
    camera.updateProjectionMatrix();
  }
  const ro = new ResizeObserver(resize);
  ro.observe(container);
  function loop() {
    if (!alive) return;
    const now = performance.now();
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    resize();
    easeLive(dt);
    updateCamera(dt);
    controls.update();
    renderer.render(scene, camera);
    updateLabels();
    raf = requestAnimationFrame(loop);
  }
  loop();

  const api = {
    setData, setTime, setGhost, setPreset, setLivePose, setHud, addExtra, removeExtras,
    setAthleteVisible: (v) => { if (athlete) athlete.visible = v; },
    debug: () => ({ cam: camera.position.toArray().map((v) => +v.toFixed(2)), target: controls.target.toArray().map((v) => +v.toFixed(2)),
      pelvis: athlete?.pelvis.toArray().map((v) => +v.toFixed(2)), preset }),
    get time() { return t; },
    get data() { return data; },
    dispose() {
      alive = false;
      cancelAnimationFrame(raf);
      ro.disconnect();
      controls.dispose();
      renderer.dispose();
      container.replaceChildren();
      container.classList.remove("v3d");
    },
  };
  container.__view3d = api;              // handle for debugging from the console
  return api;
}
