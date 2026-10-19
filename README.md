# GymCut — gymnastics video trimming & pose analysis

GymCut runs on your PC's GPU. It finds the gymnast in a video, **cuts out every skill
automatically** ("nip-n-cut"), and **analyses each one**: elements, rotation, flight time,
jump height, body shape, split angle, arm/knee extension, landing, and form notes. It works on
uploaded videos and **live from a phone camera**, where each skill is saved as its own clip as it
happens.

Everything runs locally. No cloud, no accounts.

## Quick start

1. Double-click **`run.bat`**. The first run executes `setup.bat`, which creates a Python 3.12
   environment in `.venv` inside this folder and installs CUDA PyTorch (~3 GB) plus
   dependencies. After that it starts in seconds.
2. The studio opens at **http://localhost:8000**.
3. Drag videos into the Library. Phone videos are fine (MOV/HEVC, slow-mo, variable frame rate).

Requirements: Windows with an NVIDIA GPU (tested on an RTX 3070, driver CUDA 13.x), Python 3.12,
and ffmpeg on PATH (`winget install Gyan.FFmpeg`). Without a GPU it still runs, just slowly.

## Using a phone (live)

1. In the studio, click **Connect phone** and scan the QR code. The phone must be on the same Wi-Fi.
2. The phone page uses a self-signed HTTPS certificate, which phones require for camera access.
   Accept the warning:
   - Android: *Advanced → Proceed*
   - iPhone: *Show details → visit this website*
3. Allow camera access and tap **Start camera**. Prop the phone up side-on to the tumbling line.
4. With *Auto-cut* on, every skill is recorded with 2 s of pre-roll, saved to the Library and
   fully analysed. Tap a person on screen to lock onto the athlete.
5. Optional: *Full-quality copy* also records on the phone and uploads the full-resolution video
   when you stop.

The first time GymCut starts, **Windows Firewall asks whether Python may accept connections.
Allow it on Private networks.** Otherwise the phone can't connect.

Other live sources (in the **Live** tab): this PC's webcam, the Android *IP Webcam* app
(`http://<phone-ip>:8080/video`), or any RTSP camera.

LAN access is protected by an access key that's embedded in the QR link. Other devices on the
network can't open your library without it. Use `run.bat --no-lan` to keep everything on this PC.

## Over the internet (Live from the gym)

1. On the PC: double-click **`internet.bat`** (or in the app: *Connect phone → Turn on internet access*).
   GymCut opens a free Cloudflare quick tunnel – no router or account needed (`cloudflared` is installed
   with `winget install Cloudflare.cloudflared`).
2. *Connect phone* shows an **internet QR code**. Scan it before you leave (or send yourself the link).
3. At the gym, on mobile data or the gym's Wi-Fi: open the link, allow the camera, start. The phone page
   switches to **data saver** automatically (480 px, ≤ 15 fps ≈ 1.5 Mbit/s ≈ 0.7 GB per hour).
   Clips are cut and analysed on the PC; the full studio / Coach mode works through the second link.
4. Leave the PC on with the GymCut window open – it is kept awake while internet access is on.

Notes: the address changes every time the tunnel starts. Everything through the tunnel needs the access
key that is part of the link (treat the link like a password); settings and the tunnel itself can only be
changed on the PC. Turn it off in *Connect phone* or by closing the window.

## The studio

| Area | What it does |
|---|---|
| Player | Skeleton (left = cyan, right = orange), joint angles, centre-of-mass trail glued to the floor even when the camera pans, and a live HUD showing the element, FEET/HANDS/AIR, inversion and rotation |
| Timeline | Activity curve, phase strip (feet / hands / airborne), scene cuts, skill segments. Drag edges to trim, drag empty space to add a segment, mouse-wheel to zoom |
| Auto-cut | Presets: *Each skill*, *Tumbling passes* (combinations merged), *Whole routine*, *Any movement*, plus a sensitivity slider (the dashed line is the trigger level) |
| Analysis | Metrics, recognised elements (round-off, front/back handspring, walkover, cartwheel, salto with count, direction, shape and twist estimate, leaps, jumps, handstands), landing (stuck / steps / fall) and timestamped form notes. Click any of them to jump there |
| Kinematics | Knee, hip, arm, shoulder, split and body-line angles; rotation; height; speed |
| Export | Trimmed clip · skeleton overlay video · **auto-reframe** (virtual camera follows the athlete, 9:16 for reels) · **sequence photo** (stroboscopic image of the whole skill) · per-frame CSV. All available in ½ or ¼ slow motion |
| Athlete | *Athlete* button: click the right person if GymCut picked a coach. Enter the athlete's height to get metres and m/s |
| Compare | *Compare…* (in a skill's analysis): two attempts side by side, synced at takeoff, start or landing, with overlaid angle curves and a metric-difference table |

Keyboard: `Space` play/pause · `←/→` frame step (`Shift` = 1 s) · `↑/↓` previous/next skill ·
`[` / `]` set start/end at the playhead · `N` new segment · `Del` delete segment · `L` loop.

## Exports page

Everything GymCut produces (focus videos, 3D replays, clips, sequence photos, CSV) is listed on
the **Exports** tab, grouped by video, with preview, download, delete and *Show in folder*
(files live in `data\exports\<video id>\`). Running export / 3D jobs show live progress there and
as a counter on the tab. In the Library, **Select** → tick videos (or *Select all* / *Deselect all*,
or click a session date) → *Assign athlete*, *Focus videos* or *3D replays* for just those videos.

## Compare angles & two-camera 3D

Library → **Select** two videos of the same moment → **Compare angles**: synced automatically from the
motion (nudge ±1 frame), one timeline, each camera's skeleton and detections side by side, and lanes
showing which elements each camera recognised. **3D** shows both single-camera solves in one scene
with an agreement score. **Two-camera 3D** triangulates both views together (bundle adjustment:
camera B's pose, both focal lengths and the joints at once; `gymcut/triangulate.py`) and reports a
confidence verdict with reasons. Works best with both phones on tripods (or one held very still),
the athlete ≥ ~200 px tall in each view, 60–120° apart. Synthetic tests: ~10 cm joint accuracy from a
start 150 cm off (`tests/test_triangulate.py`).

## Unreal Engine (3D)

Needs UE 5.8 with the MetaHuman + MetaHuman Body Tracker plugins and the `unreal/GymCutUE`
project in this repo (path in Settings → `ue_project`). GymCut starts the editor itself when needed.

| Feature | Where | What happens |
|---|---|---|
| **3D replay** | Studio: *Make 3D replay / Watch 3D* button (top bar) · Library: Select → *3D replays* · Coach: `3` | MetaHuman Animator markerless body solve (~6 min per attempt on an RTX 3070), then a gym-hall stage: athlete in their own colour, the personal best as a magenta hologram ghost aligned at takeoff, hip trails that draw themselves, peak-height labels, 1 m track marks, a side camera that follows the athlete, front view and a 200° orbit. Half speed, with a freeze-frame at the peak (“PEAK · hips 1.15 m (best 1.36 m)”) |
| **3D measurements** | Rating panel / Coach tiles marked **3D** | From the solved skeleton: true knee / hip / elbow angles (camera-independent), knees together in the tuck, chest-up at landing (trunk lean at touchdown = under-rotation), twist (turns about the long axis), hip height vs personal best |
| **3D view (Coach)** | Coach: *3D view* button or `3` · `L` = split / big 3D · `,` `.` step | Interactive 3D next to the real video, on any device (browser, works offline): the solved attempt as a mannequin plus the personal-best ghost, synced to the video (pause, ¼–1× speed, frame step), drag to orbit, presets *Side* (matches the camera) · *Follow* · *Front* · *Top* · *Orbit* (circles the athlete, also while paused) · *Free*; hip trails and peak-height labels. Needs the attempt's Unreal solve (*Make 3D* queues it) |
| **3D view (Live)** | Live tab → selected session → *3D view* | The live athlete in 3D next to the camera feed (MediaPipe lifting, ~15–20 fps, no Unreal needed), follow camera, orbit with the mouse |
| **3D in Unreal (live)** | Live tab → selected session → *3D in Unreal* | The live athlete is lifted to 3D with MediaPipe (≈10–12 fps alongside tracking), retargeted onto the MetaHuman skeleton and streamed to the editor. With the **GymCut Live Link** plugin (`unreal/GymCutUE/Plugins/GymCutLiveLink`) it is a real Live Link source: subject **GymCut** (Animation role, UDP 127.0.0.1:54322), so it also shows in *Window › Virtual Production › Live Link*, can be recorded with **Take Recorder** or drive any character via a Live Link Pose node. Without the plugin a Python puppet fallback is used (UDP 54321). Use a tripod – travel and height come from the camera view. Experimental: horizontal layouts and fast twists are the weak spots |

Live 3D and 3D replay rendering share the editor: a replay job waits while live 3D is on, and
live 3D can't start during a replay render. While live 3D runs the editor is capped at 30 fps
(`t.MaxFPS 30`) to leave GPU headroom for the tracker.

Rebuilding the plugin (after an engine update): close the editor and run
`unreal/GymCutUE/build_plugin.bat` (needs the VS 2022 17.14 or VS 2026 C++ build tools).

## How it works

1. **Working copy:** ffmpeg converts the video to constant-frame-rate H.264 (NVENC), so frame *n*
   is exactly at *n / fps*.
2. **Tracking pass:** YOLO26-pose and ByteTrack on the GPU detect and track everyone. Lucas-Kanade
   on the background estimates camera pan and zoom, and colour histograms detect scene cuts.
   For high-fps footage the tracker runs on every 2nd or 4th frame.
3. **Athlete selection:** tracks are scored by size, presence, motion and time spent inverted.
   Fragments (trackers lose gymnasts during flips) are merged when they never co-exist with the
   athlete and continue the athlete's trajectory.
4. **Rotation-aware refinement:** pose models are trained mostly on upright people and fail on
   inverted bodies. Every athlete frame is re-run on tight, upscaled crops rotated 0/90/180/270°
   and to the estimated body angle. A Viterbi pass picks the most consistent sequence, weighing
   confidence, motion continuity, physically possible rotation speed (≤ ~1400°/s), and a prior
   for the model seeing an upright body. This also recovers frames where the detector lost the
   athlete.
5. **Cleaning:** left/right label swaps are fixed with a Viterbi pass, spikes are rejected, short
   gaps are filled, and a Savitzky-Golay filter smooths without blunting takeoffs.
6. **Signals:** everything is in camera-compensated coordinates and in body-lengths
   (BL = shoulder→ankle ≈ 0.78 × height), so results don't depend on zoom.
   - Ground contact is where a low joint is still and at a local *low* point, which excludes jump
     apexes.
   - Orientation is unwrapped robustly into total rotation.
   - Facing comes from the head and from the direction flexed knees point.
7. **Segmentation and analysis:** the activity score uses speed, limb motion, inversion and
   elevation, with hysteresis, run-up extension and evidence filtering. Elements come from the
   feet/hands/air phase sequence. Flight time gives physical jump height via **h = g·t²/8**.

## Accuracy notes and filming tips

- All values are 2D estimates from one camera. **Side-on to the direction of travel** gives the
  most accurate angles and rotation counts. Twist counts are rough.
- Flight time is precise to about ±1 frame. **Film at 60 fps or more** (phone slow-mo is great);
  30 fps means ±33 ms.
- Keep the athlete **at least ~150 px tall** in the frame. The studio warns you when footage limits
  accuracy.
- Form notes are coaching hints, not official FIG deductions.

## Command line & tests

```
.venv\Scripts\python.exe -m gymcut.cli analyze video1.mp4 video2.mov
.venv\Scripts\python.exe -m gymcut.cli list
.venv\Scripts\python.exe -m pytest -q tests
```

The tests drive a synthetic gymnast with real ballistic flight. They check flight time at
30/60/120 fps, salto rotation, direction and shape, mirror invariance, left/right repair,
segmentation, camera-pan compensation, the live auto-recorder, and the server's access guard and
range requests.

## Layout

```
gymcut/            Python package (FastAPI server + analysis)
  pose.py          YOLO-pose wrapper, model download, tracker config
  refine.py        rotation-aware crop refinement + Viterbi selection
  tracking.py      athlete selection and fragment stitching
  camera_motion.py pan/zoom estimation, scene cuts
  kinematics.py    cleaning, signals, ground contact, orientation
  analysis.py      phases, elements, landing, form notes
  segmentation.py  activity score and auto-cut presets
  pipeline.py      offline processing stages
  live.py          real-time sessions, auto-recorder, network cameras
  render.py        exports (clip, overlay, reframe, sequence photo, CSV)
  server.py        REST + WebSocket API
web/               studio (index.html) and phone capture page (live.html), plain ES modules
tests/             synthetic-gymnast and API tests
data/              your library (videos, analyses, exports) - created at runtime
models/            downloaded pose weights
```

Settings (gear icon) let you choose the pose model (YOLO26 n/s/m/l/x; *m* is the default and *l/x*
are more accurate), detection size, refinement, encoder, and live pre/post-roll.

## Demo content

The two demo videos in the library come from
[“Backhandspring.ogv”](https://commons.wikimedia.org/wiki/File:Backhandspring.ogv) by **Wmagro**
(Wikimedia Commons, [CC BY 3.0](https://creativecommons.org/licenses/by/3.0)). The training-session
demo is a derived edit (original + mirrored copy with pauses). Delete them any time.
