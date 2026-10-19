# GymCut 3D Stage (UE 5.8)

Unreal side of GymCut: turns GymCut's analysed gymnastics attempts into 3D body
animation with Epic's **MetaHuman Animator markerless body capture**, so attempts
can be replayed, orbited and compared in 3D.

## Pipeline

1. **GymCut** (`F:\Code\GymCut`) finds the athlete, cuts each skill, analyses it.
2. GymCut's **Export → Unreal (MetaHuman)** writes an athlete-centred JPG sequence
   (standard frame rate, coaches cropped out) + `gymcut_attempt.json` (elements,
   takeoff/landing frames, metrics). Takes live in `Content/Movies/GymCut/<Take>/`.
3. `Scripts/gymcut_take.py`, run inside this editor by GymCut through Unreal's
   Python remote execution (`python -m gymcut.unreal_bridge`), per take:
   - `setup`: ImgMediaSource → FootageCaptureData → MetaHumanPerformance
     (mono footage, body tracking on, face off) under `/Game/GymCut/Takes/<Take>/`
   - `start` / `status`: run the body solve
   - `export`: AnimSequence of the body motion
4. Next: a Level Sequence per attempt with the athlete as a MetaHuman (and the
   athlete's best attempt as a ghost), rendered back to GymCut.

## Requirements

- UE 5.8 with the free Fab add-on **MetaHuman Animator Markerless Motion Capture**
  installed to the engine (the editor log says `MetaHumanBodyTracker plugin not
  found` without it).
- DirectX 12 (set in `Config/DefaultEngine.ini`).
- Python remote execution is enabled and bound to 127.0.0.1 (local machine only).

## Pilot takes

| Take | Skill | Frames |
|---|---|---|
| Take01_RoundoffBackTuck | Round-off → back salto (tuck) | 219 @ 60 fps |
| Take02_FrontTuck | Front salto (tuck) | 58 @ 30 fps |
| Take03_Handspring | Front handspring | 158 @ 60 fps |
| Take04_RoundoffDoubleBackTuck | Round-off → back tuck → back tuck | 324 @ 60 fps |

## Pilot results (2026-10-13)

All four takes solved with `MetaHumanBodyTracker` 1.0.0 (UE 5.8.3, RTX 3070, Ryzen 9 5950X):

| Take | GymCut 2D | 3D solve (torso rotation) | Solve time |
|---|---|---|---|
| Front tuck (58 f @30) | 1 salto | −344° (1 rot) | ~3.5 min |
| Round-off → back tuck (219 f @60) | 2 rotations | +722° | ~13 min* |
| Front handspring (158 f @60) | 1 rotation | +345° | ~4.9 min |
| Round-off → 2× back tuck (324 f @60) | 3 rotations | +1058° | ~8.4 min |

\* includes the time it waited in the queue. Roughly 1.6–2 s per frame.

Notes: body tracking data is reported by `contains_animation_data_type(FrameAnimationDataType.BODY)`
(the plain `contains_animation_data()` only looks at face data). Export uses the solver skeleton
`/MetaHumanBodyTracker/metahuman_base_skel`; replays use `/MetaHumanBodyTracker/SKM_Body` (headless body).

Scripts: `gymcut_take.py` (setup/start/status/export), `dump_anim_pose.py` (joint positions →
`Saved/GymCut/<take>_pose.json`), `build_stage.py` (level + sequence, optional ghost aligned in
time and space), `render_sequence.py` (Movie Render Queue → JPG frames). Driven from GymCut with
`python -m gymcut.ue_batch <takes…>` and `gymcut.unreal_bridge`.

## GymCut Live Link plugin

`Plugins/GymCutLiveLink` - a Live Link source ("GymCut" in the Live Link panel's + Source menu,
or `unreal.GymCutLiveLinkLibrary.start_gym_cut_source(54322)` from Python). It listens on UDP
127.0.0.1:54322 for GymCut's live skeleton (JSON: bones, parents, parent-local transforms) and
publishes subject **GymCut** with the Animation role, built for the MetaHuman body skeleton.
`Scripts/live_puppet.py` sets up the LiveStage level with a MetaHuman using `LiveLinkInstance`.
Rebuild with `build_plugin.bat` (editor closed).
