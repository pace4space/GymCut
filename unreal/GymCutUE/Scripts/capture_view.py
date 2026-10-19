"""Off-screen capture of the live stage (works while the editor is in the background).
Globals: OUT (png path), APPLY (apply the latest live packet first)."""
import builtins
import os

import unreal

OUT = globals().get("OUT") or os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir()), "GymCut", "capture.png")
S = builtins.__dict__.get("_gymcut_live", {})
ues = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
world = ues.get_game_world() or ues.get_editor_world()
if globals().get("APPLY", True) and S.get("fn"):
    S["fn"](1.0)
p = S.get("p") or [0, 0, 90]
caps = unreal.GameplayStatics.get_all_actors_with_tag(world, "GymCutCapture")
if not caps:
    raise RuntimeError("no GymCutCapture actor - start the live puppet first")
cap = caps[0]
def _alive(o):
    try:
        return o is not None and bool(o.get_name())
    except Exception:
        return False


if S.get("rt_world") != world.get_path_name() or not _alive(S.get("rt")):
    S["rt"] = unreal.RenderingLibrary.create_render_target2d(world, 1280, 720, unreal.TextureRenderTargetFormat.RTF_RGBA8_SRGB)
    S["rt_world"] = world.get_path_name()
comp = cap.capture_component2d
comp.set_editor_property("texture_target", S["rt"])
comp.set_editor_property("capture_source", unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR)
comp.set_editor_property("fov_angle", 70.0)
loc = unreal.Vector(p[0], -560, 150)
cap.set_actor_location_and_rotation(loc, unreal.MathLibrary.find_look_at_rotation(loc, unreal.Vector(p[0], 0, 110)), False, False)
comp.capture_scene()
unreal.RenderingLibrary.export_render_target(world, S["rt"], os.path.dirname(OUT), os.path.basename(OUT))
print("captured", OUT)
try:
    sc = S.get("sim_comp")
    print("debug", unreal.GymCutLiveLinkLibrary.get_gym_cut_source_status() if hasattr(unreal, "GymCutLiveLinkLibrary") else "",
          "pelvis", sc.get_socket_location("pelvis") if sc else None, "hand_l", sc.get_socket_location("hand_l") if sc else None)
except Exception as e:
    print("debug error", e)
