# Changelog

GymCut was built before it was put under version control; this log reconstructs the
development history (dates approximate). From the first commit on, `git log` is the record.

## 2026-10-19 — repository
- Unreal project moved into this repo (`unreal/GymCutUE`); all paths are now relative.
- First public commit.

## 2026-10-16 — sync everywhere, two-camera 3D
- **Motion sync** (`web/js/sync.js`): two clips are lined up by matching their motion curves
  (inversion, hip height, activity, knees, hips). Used by Compare, Compare angles and Coach.
- **Compare**: sync by motion (default) or by key elements, frame-accurate fine tuning
  (B ±1 frame / Shift+←→), shared timeline (plays only what both clips cover), looping slow motion,
  element labels on each side; ghost overlay follows the sync.
- **Compare angles**: same sync controls + a *Motion pattern* graph of both cameras.
- **Coach**: *Motion pattern* graph — this attempt vs the personal best; the 2D ghost uses the same sync.
- **Two-camera 3D** (`gymcut/triangulate.py`): bundle adjustment of both cameras' 2D joints
  (camera-B pose, both focal lengths, joints) with a confidence verdict. Synthetic tests:
  ~10 cm joint accuracy from a 150 cm-off start (`tests/test_triangulate.py`).

## 2026-10-15 — compare angles
- **Compare angles** page: two cameras of the same moment, synced automatically, one timeline,
  each camera's skeleton and detections, detection lanes, both 3D solves in one scene with an
  agreement score.

## 2026-10-14 — interactive 3D, tours, internet access
- 3D replay ghost: aligned on the flight element's takeoff with the pass direction, fades in at the hurdle;
  peak freeze-frame restricted to the flight; consistent floor estimate across views.
- **Interactive 3D view** (three.js, offline): Coach split view (video | 3D), camera presets,
  orbit, hip trails, peak labels; live 3D next to the camera feed on the Live page.
- **Guided tours** ("?" in the header), keyboard shortcut sheet, first-visit welcome.
- **Internet access** through a Cloudflare quick tunnel (`internet.bat`), data-saver phone streaming;
  tunnel traffic always needs the access key.

## 2026-10-13 — Unreal stage, 3D measurements, Live Link
- **Exports** page; Library selection with Select all / bulk focus videos / bulk 3D replays.
- 3D replay stage: gym hall, tumbling track with 1 m marks, athlete colour, hologram ghost,
  self-drawing hip trails, side follow camera, freeze at the peak.
- **3D measurements** from the solved skeleton (camera-independent angles, knees together,
  chest-up at landing, twist, hip height) feed the ratings and Coach tiles.
- **Live 3D**: MediaPipe lifting of the live athlete, retargeted to the MetaHuman skeleton;
  **GymCut Live Link** C++ plugin (subject "GymCut"), Python puppet fallback.

## 2026-10-12 — coaching UI, Unreal pilot
- UI redesign: ghost-of-best overlay, traffic-light ratings, attempt filmstrip, athletes & sessions,
  **Coach mode**; compare picker with thumbnails; re-analyse with model choice; collapsible side panel;
  focus video (virtual camera follows the athlete).
- Wi-Fi LAN access (HTTPS for phones, QR code with access key).
- Unreal pilot: MetaHuman Animator markerless body solve of real clips; automated
  **3D replay** job (solve → stage → Movie Render Queue → MP4).

## 2026-10-11 — first version
- Video pipeline on the GPU: YOLO pose + tracking, athlete selection, rotation-aware refinement,
  camera-motion compensation, phases (feet / hands / air), skill segmentation and recognition
  (round-off, handspring, saltos with rotation count, jumps, leaps…), landing verdicts, form notes.
- Studio (timeline editing, kinematics charts), exports (clips, skeleton overlay, sequence photo, CSV),
  live phone streaming with auto-clipping.
