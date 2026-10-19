"""Build a 3D replay of a solved take (run inside the editor via gymcut.unreal_bridge).

Globals:
  TAKE      solved take name (required)
  GHOST     optional second take shown as a translucent ghost (the personal best)
  ALIGN_A   seconds into TAKE's animation to align on (e.g. takeoff of the key element)
  ALIGN_B   seconds into GHOST's animation to align on
  CAMERA    side | front | high | orbit   (which camera the sequence cuts to)
  COLOR     athlete colour (r, g, b) 0..1
Creates /Game/GymCut/Maps/Stage_<suffix> and /Game/GymCut/Sequences/LS_<suffix>.

Stage: a gym hall (walls with pads, tumbling track with 1 m marks, landing zone), the
MetaHuman template body + head in the athlete colour, a glowing translucent ghost, a hip
trail that draws itself as the skill happens and a peak-height marker. The side camera
follows the athlete along the track. Prints "peak <seq seconds> <hip height cm>" for the
encoder (freeze-frame at the top of the skill).
"""
import json
import math
import os

import unreal

TAKE = globals().get("TAKE", "Take02_FrontTuck")
GHOST = globals().get("GHOST")
ALIGN_A = float(globals().get("ALIGN_A", 0.0))
ALIGN_B = float(globals().get("ALIGN_B", 0.0))
CAMERA = globals().get("CAMERA", "side")
COLOR = tuple(globals().get("COLOR", (0.16, 0.62, 0.52)))
GHOST_RGB = (1.0, 0.31, 0.85)

eal = unreal.EditorAssetLibrary
mel = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
project_dir = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())

suffix = f"{TAKE}" + (f"_vs_{GHOST}" if GHOST else "")
level_path = f"/Game/GymCut/Maps/Stage_{suffix}"
seq_dir, seq_name = "/Game/GymCut/Sequences", f"LS_{suffix}"
MAT_DIR = "/Game/GymCut/Materials/Stage"

BODY_MESH = "/MetaHumanCharacter/Body/IdentityTemplate/SKM_Body"
FALLBACK_BODY = "/MetaHumanBodyTracker/SKM_Body"
FACE_MESH = "/MetaHumanCharacter/Face/SKM_Face"
PLANE, CUBE, SPHERE = "/Engine/BasicShapes/Plane", "/Engine/BasicShapes/Cube", "/Engine/BasicShapes/Sphere"


# ---------------------------------------------------------------- materials
def _new_material(name):
    path = f"{MAT_DIR}/{name}"
    if eal.does_asset_exist(path):
        return eal.load_asset(path), False
    return tools.create_asset(name, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew()), True


def _const3(m, rgb, x=-500, y=0):
    c = mel.create_material_expression(m, unreal.MaterialExpressionConstant3Vector, x, y)
    c.set_editor_property("constant", unreal.LinearColor(*rgb, 1.0))
    return c


def _const(m, v, x=-500, y=200):
    c = mel.create_material_expression(m, unreal.MaterialExpressionConstant, x, y)
    c.set_editor_property("r", v)
    return c


def _finish(m):
    mel.recompile_material(m)
    eal.save_loaded_asset(m)
    return m


def surface(name, rgb, rough=0.85, metal=0.0):
    m, new = _new_material(name)
    if not new:
        return m
    mel.connect_material_property(_const3(m, rgb), "", unreal.MaterialProperty.MP_BASE_COLOR)
    mel.connect_material_property(_const(m, rough), "", unreal.MaterialProperty.MP_ROUGHNESS)
    if metal:
        mel.connect_material_property(_const(m, metal, y=300), "", unreal.MaterialProperty.MP_METALLIC)
    return _finish(m)


def glow(name, rgb, strength=2.0):
    """Unlit emissive (markers, trail, labels)."""
    m, new = _new_material(name)
    if not new:
        return m
    m.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    mul = mel.create_material_expression(m, unreal.MaterialExpressionMultiply, -250, 0)
    mul.set_editor_property("const_b", strength)
    mel.connect_material_expressions(_const3(m, rgb), "", mul, "A")
    mel.connect_material_property(mul, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    return _finish(m)


def athlete_material(rgb):
    """Satin clay in the athlete colour with a soft rim light (reads well against the hall)."""
    name = "M_Athlete_" + "".join(f"{int(c * 255):02x}" for c in rgb)
    m, new = _new_material(name)
    if not new:
        return m
    mel.connect_material_property(_const3(m, rgb), "", unreal.MaterialProperty.MP_BASE_COLOR)
    mel.connect_material_property(_const(m, 0.45), "", unreal.MaterialProperty.MP_ROUGHNESS)
    fres = mel.create_material_expression(m, unreal.MaterialExpressionFresnel, -500, 400)
    fres.set_editor_property("exponent", 3.0)
    rim = mel.create_material_expression(m, unreal.MaterialExpressionMultiply, -250, 400)
    mel.connect_material_expressions(fres, "", rim, "A")
    mel.connect_material_expressions(_const3(m, tuple(min(1.0, c * 0.6 + 0.25) for c in rgb), y=520), "", rim, "B")
    mel.connect_material_property(rim, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    return _finish(m)


def ghost_material(rgb):
    """Translucent hologram: faint body, bright silhouette edges."""
    m, new = _new_material("M_GhostHolo")
    if not new:
        return m
    m.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    m.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    fres = mel.create_material_expression(m, unreal.MaterialExpressionFresnel, -700, 300)
    fres.set_editor_property("exponent", 2.2)
    # opacity = 0.10 + 0.55 * fresnel
    op_mul = mel.create_material_expression(m, unreal.MaterialExpressionMultiply, -450, 300)
    op_mul.set_editor_property("const_b", 0.55)
    mel.connect_material_expressions(fres, "", op_mul, "A")
    op_add = mel.create_material_expression(m, unreal.MaterialExpressionAdd, -250, 300)
    op_add.set_editor_property("const_b", 0.10)
    mel.connect_material_expressions(op_mul, "", op_add, "A")
    mel.connect_material_property(op_add, "", unreal.MaterialProperty.MP_OPACITY)
    # emissive = colour * (0.6 + 2.5 * fresnel)
    e_mul = mel.create_material_expression(m, unreal.MaterialExpressionMultiply, -450, 0)
    e_mul.set_editor_property("const_b", 2.5)
    mel.connect_material_expressions(fres, "", e_mul, "A")
    e_add = mel.create_material_expression(m, unreal.MaterialExpressionAdd, -350, 0)
    e_add.set_editor_property("const_b", 0.6)
    mel.connect_material_expressions(e_mul, "", e_add, "A")
    col = mel.create_material_expression(m, unreal.MaterialExpressionMultiply, -200, 0)
    mel.connect_material_expressions(_const3(m, rgb, -450, -150), "", col, "A")
    mel.connect_material_expressions(e_add, "", col, "B")
    mel.connect_material_property(col, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    return _finish(m)


# ---------------------------------------------------------------- actors
def take_assets(take):
    anim = eal.load_asset(f"/Game/GymCut/Takes/{take}/AS_{take}")
    with open(os.path.join(project_dir, "Content", "Movies", "GymCut", take, "gymcut_attempt.json"), encoding="utf-8") as fh:
        info = json.load(fh)
    return anim, info


def ref_bone_transform(mesh, bone):
    pose = unreal.AnimPoseExtensions.get_reference_pose(mesh.get_editor_property("skeleton"))
    return unreal.AnimPoseExtensions.get_bone_pose(pose, bone, unreal.AnimPoseSpaces.WORLD)


def spawn_character(label, material, shadows=True):
    body_mesh = eal.load_asset(BODY_MESH) or eal.load_asset(FALLBACK_BODY)
    body = eas.spawn_actor_from_class(unreal.SkeletalMeshActor, unreal.Vector(0, 0, 0))
    body.set_actor_label(label)
    comp = body.skeletal_mesh_component
    comp.set_skeletal_mesh_asset(body_mesh)
    comp.set_editor_property("cast_shadow", shadows)
    for i in range(comp.get_num_materials()):
        comp.set_material(i, material)
    face_mesh = eal.load_asset(FACE_MESH)
    if face_mesh:
        head = eas.spawn_actor_from_class(unreal.SkeletalMeshActor, unreal.Vector(0, 0, 0))
        head.set_actor_label(label + "_Head")
        hc = head.skeletal_mesh_component
        hc.set_skeletal_mesh_asset(face_mesh)
        hc.set_editor_property("cast_shadow", shadows)
        for i in range(hc.get_num_materials()):
            hc.set_material(i, material)
        # the face mesh (head + neck) copies the body's pose bone by bone ("leader pose", as in
        # MetaHuman's own blueprint), so the neck bends with the body instead of the head being
        # pinned rigidly to one bone and coming loose when the neck bends in a salto
        head.attach_to_component(comp, "", unreal.AttachmentRule.SNAP_TO_TARGET,
                                 unreal.AttachmentRule.SNAP_TO_TARGET, unreal.AttachmentRule.KEEP_WORLD, False)
        hc.set_leader_pose_component(comp, True, False)
        return body, head
    return body, None


def box(label, mesh, loc, scale, mat, yaw=0.0, pitch=0.0, roll=0.0, shadow=False):
    a = eas.spawn_actor_from_class(unreal.StaticMeshActor, loc, unreal.Rotator(roll, pitch, yaw))
    a.set_actor_label(label)
    c = a.static_mesh_component
    c.set_static_mesh(eal.load_asset(mesh))
    c.set_material(0, mat)
    c.set_editor_property("cast_shadow", shadow)
    a.set_actor_scale3d(scale)
    return a


def label_text(text, loc, yaw, size, rgb, label="Label"):
    a = eas.spawn_actor_from_class(unreal.TextRenderActor, loc, unreal.Rotator(0, 0, yaw))
    a.set_actor_label(label)
    t = a.text_render
    t.set_text(text)
    t.set_world_size(size)
    r_, g_, b_ = (int(c * 255) for c in rgb)
    t.set_text_render_color(unreal.Color(r=r_, g=g_, b=b_, a=255))
    t.set_horizontal_alignment(unreal.HorizTextAligment.EHTA_CENTER)
    t.set_vertical_alignment(unreal.VerticalTextAligment.EVRTA_TEXT_CENTER)
    t.set_editor_property("cast_shadow", False)
    unlit = eal.load_asset("/Engine/EngineMaterials/UnlitText")
    if unlit:
        t.set_text_material(unlit)
    return a


def bone_pos(anim, t, bone="pelvis"):
    pose = unreal.AnimPoseExtensions.get_anim_pose_at_time(anim, max(0.0, min(t, anim.get_play_length())), unreal.AnimPoseEvaluationOptions())
    return unreal.AnimPoseExtensions.get_bone_pose(pose, bone, unreal.AnimPoseSpaces.WORLD).translation


def heading(anim, t):
    a, b = bone_pos(anim, t - 0.4), bone_pos(anim, t + 0.4)
    return math.atan2(b.y - a.y, b.x - a.x)


def pass_window(info):
    """(start, end) seconds of the actual pass: hurdle (or first element) to landing."""
    rate = float(info["frame_rate"])
    els = info.get("elements") or []
    if not els:
        return None
    return max(0.0, els[0]["frame0"] / rate - 0.3), els[-1]["frame1"] / rate + 0.2


def pass_heading(anim, info, t_align):
    """Travel direction of the whole pass (hurdle -> landing). Robust to the half turn inside a
    round-off, which flips any short-window estimate. In-place skills fall back to the takeoff."""
    w = pass_window(info)
    if w:
        a, b = bone_pos(anim, w[0]), bone_pos(anim, w[1])
        if math.hypot(b.x - a.x, b.y - a.y) > 60:
            return math.atan2(b.y - a.y, b.x - a.x)
    return heading(anim, t_align)


# ---------------------------------------------------------------- level
if eal.does_asset_exist(level_path):
    les.load_level(level_path)
    for a in eas.get_all_level_actors():
        eas.destroy_actor(a)
else:
    les.new_level(level_path)

anim_a, info_a = take_assets(TAKE)
actor_a, _ = spawn_character("Athlete", athlete_material(COLOR))
actors = [(actor_a, anim_a, ALIGN_A, None)]
xf_b = None
if GHOST:
    anim_b, info_b = take_assets(GHOST)
    actor_b, head_b = spawn_character("Ghost_PersonalBest", ghost_material(GHOST_RGB), shadows=False)
    # same pelvis position at takeoff and the same direction of travel for the pass: both run
    # down the same track line and start the skill together
    yaw_b = math.degrees(pass_heading(anim_a, info_a, ALIGN_A) - pass_heading(anim_b, info_b, ALIGN_B))
    pa, pb = bone_pos(anim_a, ALIGN_A), bone_pos(anim_b, ALIGN_B)
    cb, sb = math.cos(math.radians(yaw_b)), math.sin(math.radians(yaw_b))
    off = (pa.x - (pb.x * cb - pb.y * sb), pa.y - (pb.x * sb + pb.y * cb))
    actor_b.set_actor_location_and_rotation(unreal.Vector(off[0], off[1], 0), unreal.Rotator(0, 0, yaw_b), False, False)
    xf_b = (cb, sb, off)
    actors.append((actor_b, anim_b, ALIGN_B, xf_b))


def world(p, xf):
    if xf is None:
        return p
    cb, sb, off = xf
    return unreal.Vector(p.x * cb - p.y * sb + off[0], p.x * sb + p.y * cb + off[1], p.z)


# ---------------------------------------------------------------- geometry of the attempt
rate = int(info_a["frame_rate"])
lead = max(a for _, _, a, _ in actors)
L = anim_a.get_play_length()
SAMPLE = 1.0 / 15.0
path_a = [(k * SAMPLE, bone_pos(anim_a, k * SAMPLE)) for k in range(int(L / SAMPLE) + 1)]
cx = sum(p.x for _, p in path_a) / len(path_a)
cy = sum(p.y for _, p in path_a) / len(path_a)
x0, x1 = path_a[0][1], path_a[-1][1]
tx, ty = x1.x - x0.x, x1.y - x0.y
tn = math.hypot(tx, ty) or 1.0
tx, ty = (tx / tn, ty / tn) if tn > 30 else (1.0, 0.0)       # travel direction
nx, ny = -ty, tx                                              # side normal (camera on -n)
yaw_t = math.degrees(math.atan2(ty, tx))
yaw_cam = math.degrees(math.atan2(-ny, -nx))                  # text faces +X: point it at the side camera
proj = [(p.x - cx) * tx + (p.y - cy) * ty for _, p in path_a]
s_min, s_max = min(proj), max(proj)
span = max(s_max - s_min, 250)
dist = span * 1.15 + 340


def along(s, side=0.0, z=0.0):
    return unreal.Vector(cx + tx * s + nx * side, cy + ty * s + ny * side, z)


# ---------------------------------------------------------------- gym hall
m_floor = surface("M_HallFloor", (0.11, 0.115, 0.13), 0.9)
m_track = surface("M_TumbleTrack", (0.03, 0.11, 0.42), 0.75)
m_line = surface("M_TrackLine", (0.85, 0.85, 0.85), 0.6)
m_mat = surface("M_LandingMat", (0.42, 0.05, 0.06), 0.8)
m_wall = surface("M_HallWall", (0.32, 0.33, 0.36), 0.95)
m_pad = surface("M_WallPad", (0.05, 0.09, 0.30), 0.85)

hall_len, hall_w, hall_h = span + 2200, 1500, 650
box("Floor", PLANE, along((s_min + s_max) / 2), unreal.Vector(hall_len / 100 + 30, hall_len / 100 + 30, 1), m_floor, yaw=yaw_t)
track_len = span + 900
track_mid = (s_min + s_max) / 2
box("TumbleTrack", CUBE, along(track_mid, 0, 0.5), unreal.Vector(track_len / 100, 2.0, 0.01), m_track, yaw=yaw_t)
for side in (-100, 100):
    box("TrackEdge", CUBE, along(track_mid, side, 0.7), unreal.Vector(track_len / 100, 0.04, 0.012), m_line, yaw=yaw_t)
# 1 m marks across the track, numbered from the run-up end
first = s_min - 300
for k in range(int(track_len / 100) + 1):
    s = first + k * 100
    if s > track_mid + track_len / 2:
        break
    box("Mark", CUBE, along(s, 0, 0.7), unreal.Vector(0.03, 2.0 if k % 5 == 0 else 0.5, 0.012), m_line, yaw=yaw_t)
    if k % 2 == 0:
        label_text(f"{k} m", along(s, -122, 9), yaw_cam, 14, (0.75, 0.75, 0.78), "MarkLabel")
# landing zone around where the attempt ends
land_s = proj[-1]
box("LandingZone", CUBE, along(land_s + 40, 0, 0.9), unreal.Vector(3.0, 2.2, 0.012), m_mat, yaw=yaw_t)

# walls (one-sided planes facing in: cameras outside the hall see straight through them)
mid = (s_min + s_max) / 2
for side, sgn in ((hall_w / 2, -1), (-hall_w / 2, 1)):
    # plane normal is +Z; roll it to point at the track (-n for the far wall, +n for the near wall)
    box("Wall", PLANE, along(mid, side, hall_h / 2), unreal.Vector(hall_len / 100, hall_h / 100, 1), m_wall,
        yaw=yaw_t, roll=90 * sgn)
    box("WallPad", CUBE, along(mid, side + sgn * 4, 70), unreal.Vector(hall_len / 100, 0.08, 1.4), m_pad, yaw=yaw_t)
for end, sgn in ((mid + hall_len / 2, 1), (mid - hall_len / 2, -1)):
    box("EndWall", PLANE, along(end, 0, hall_h / 2), unreal.Vector(hall_w / 100, hall_h / 100, 1), m_wall,
        yaw=yaw_t + 90, roll=-90 * sgn)

# lights: soft overhead key with shadows, sky fill, light haze, gentle vignette
sun = eas.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 800), unreal.Rotator(0, -62, yaw_t - 120))
sun.light_component.set_editor_property("intensity", 5.0)
sun.light_component.set_editor_property("light_color", unreal.Color(255, 244, 228, 255))
sun.light_component.set_editor_property("light_source_angle", 4.0)
sky = eas.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 400))
sky.light_component.set_editor_property("real_time_capture", True)
sky.light_component.set_editor_property("intensity", 1.4)
eas.spawn_actor_from_class(unreal.SkyAtmosphere, unreal.Vector(0, 0, 0))
for k, s in enumerate((s_min - 200, mid, s_max + 200)):
    rl = eas.spawn_actor_from_class(unreal.RectLight, along(s, 0, hall_h - 20), unreal.Rotator(0, -90, 0))
    rl.light_component.set_editor_property("intensity", 18.0)
    rl.light_component.set_editor_property("attenuation_radius", 1600.0)
    rl.light_component.set_editor_property("source_width", 300.0)
    rl.light_component.set_editor_property("source_height", 120.0)
    rl.light_component.set_editor_property("cast_shadows", False)
fog = eas.spawn_actor_from_class(unreal.ExponentialHeightFog, unreal.Vector(0, 0, 0))
fog.get_component_by_class(unreal.ExponentialHeightFogComponent).set_editor_property("fog_density", 0.006)
ppv = eas.spawn_actor_from_class(unreal.PostProcessVolume, unreal.Vector(0, 0, 0))
ppv.set_editor_property("unbound", True)
pps = ppv.get_editor_property("settings")
pps.set_editor_property("override_vignette_intensity", True)
pps.set_editor_property("vignette_intensity", 0.45)
pps.set_editor_property("override_auto_exposure_bias", True)
pps.set_editor_property("auto_exposure_bias", -1.0)
pps.set_editor_property("override_auto_exposure_speed_up", True)
pps.set_editor_property("auto_exposure_speed_up", 20.0)
pps.set_editor_property("override_auto_exposure_speed_down", True)
pps.set_editor_property("auto_exposure_speed_down", 20.0)
ppv.set_editor_property("settings", pps)

# ---------------------------------------------------------------- sequence
seq_path = f"{seq_dir}/{seq_name}"
if eal.does_asset_exist(seq_path):
    seq = eal.load_asset(seq_path)
    for b in list(seq.get_bindings()):
        b.remove()
    for t in list(seq.get_tracks() if hasattr(seq, "get_tracks") else seq.get_master_tracks()):
        seq.remove_track(t) if hasattr(seq, "remove_track") else seq.remove_master_track(t)
else:
    seq = tools.create_asset(seq_name, seq_dir, unreal.LevelSequence, unreal.LevelSequenceFactoryNew())
seq.set_display_rate(unreal.FrameRate(rate, 1))
end_t = max(lead - align + anim.get_play_length() for _, anim, align, _ in actors)
end_f = int(end_t * rate) + 1
seq.set_playback_start(0)
seq.set_playback_end(end_f)
for actor, anim, align, _ in actors:
    binding = seq.add_possessable(actor)
    track = binding.add_track(unreal.MovieSceneSkeletalAnimationTrack)
    sec = track.add_section()
    start_f = int(round((lead - align) * rate))
    sec.set_range(start_f, start_f + int(anim.get_play_length() * rate) + 1)
    params = sec.get_editor_property("params")
    params.set_editor_property("animation", anim)
    sec.set_editor_property("params", params)


def appear_at(actor, seq_t):
    """Hidden until seq_t (sequencer visibility track)."""
    b = seq.add_possessable(actor)
    tr = b.add_track(unreal.MovieSceneVisibilityTrack)
    tr.set_property_name_and_path("bHiddenInGame", "bHiddenInGame")
    sec = tr.add_section()
    sec.set_range(0, end_f)
    ch = sec.get_all_channels()[0]
    ch.add_key(unreal.FrameNumber(0), False)
    ch.add_key(unreal.FrameNumber(max(1, int(seq_t * rate))), True)


# the ghost fades in at its hurdle (its run-up would only clutter the athlete's approach)
ghost_from = None
if GHOST:
    w = pass_window(info_b)
    ghost_from = max(0.0, (w[0] if w else 0.0)) + lead - ALIGN_B
    for a_ in (actor_b, head_b):
        if a_ is not None:
            a_.set_actor_hidden_in_game(True)
            appear_at(a_, ghost_from)

# hip trails that draw themselves + peak marker (athlete and ghost)
peak_info = None
m_trail_a = glow("M_TrailAthlete_" + "".join(f"{int(c * 255):02x}" for c in COLOR), tuple(min(1, c * 1.3 + 0.1) for c in COLOR), 3.0)
m_trail_g = glow("M_TrailGhost", GHOST_RGB, 2.0)
m_peak = glow("M_PeakMarker", (1.0, 0.85, 0.3), 3.0)
for idx, (actor, anim, align, xf) in enumerate(actors):
    is_ghost = idx > 0
    shift = lead - align
    pts = [(t, world(bone_pos(anim, t), xf)) for t in (k * SAMPLE for k in range(int(anim.get_play_length() / SAMPLE) + 1))]
    # floor level = same estimate as GymCut's 3D view / measurements: 3rd percentile of the lowest
    # foot joint, minus the ~3 cm the ball/ankle joints sit above the floor
    lows = sorted(min(world(bone_pos(anim, t, b), xf).z for b in ("ball_l", "ball_r", "foot_l", "foot_r")) for t, _ in pts[::2])
    ground = lows[int(len(lows) * 0.03)] - 3.0
    # peak of the flight element itself (a low salto can be barely higher than standing tall)
    flight = [q for q in pts if align - 0.1 <= q[0] <= align + 1.2] or pts
    top_t, top_p = max(flight, key=lambda q: q[1].z)
    for t, p in pts:
        if is_ghost and ghost_from is not None and t + shift < ghost_from:
            continue
        dot = box("Trail", SPHERE, p, unreal.Vector(0.045, 0.045, 0.045) if not is_ghost else unreal.Vector(0.035, 0.035, 0.035),
                  m_trail_g if is_ghost else m_trail_a)
        dot.set_actor_hidden_in_game(True)
        appear_at(dot, t + shift)
    hip_h = top_p.z - ground
    mk = box("PeakBar", CUBE, top_p, unreal.Vector(0.02, 0.9, 0.02), m_trail_g if is_ghost else m_peak, yaw=yaw_t)
    mk.set_actor_hidden_in_game(True)
    appear_at(mk, top_t + shift)
    txt = f"best {hip_h / 100:.2f} m" if is_ghost else f"hips {hip_h / 100:.2f} m"
    lab = label_text(txt, unreal.Vector(top_p.x - nx * 50, top_p.y - ny * 50, top_p.z + (34 if not is_ghost else -34)), yaw_cam, 22,
                     GHOST_RGB if is_ghost else (1.0, 0.85, 0.3), "PeakLabel")
    lab.set_actor_hidden_in_game(True)
    appear_at(lab, top_t + shift)
    if not is_ghost:
        peak_info = (top_t + shift, hip_h)
    else:
        print(f"ghost_peak {hip_h:.1f}")

# ---------------------------------------------------------------- cameras
center = unreal.Vector(cx, cy, 120)


def place_camera(label, loc, focal=28.0, look=None):
    cam = eas.spawn_actor_from_class(unreal.CineCameraActor, loc)
    cam.set_actor_label(label)
    cam.set_actor_rotation(unreal.MathLibrary.find_look_at_rotation(loc, look or center), False)
    cc = cam.get_cine_camera_component()
    cc.set_editor_property("current_focal_length", focal)
    fs = cc.get_editor_property("focus_settings")
    fs.set_editor_property("focus_method", unreal.CameraFocusMethod.DISABLE)
    cc.set_editor_property("focus_settings", fs)
    return cam


side_d = 560.0
cams = {
    "side": place_camera("Cam_SideFollow", along(proj[0], -side_d, 135), 20.0),
    "front": place_camera("Cam_Front", unreal.Vector(cx + tx * (dist * 0.9 + span * 0.4), cy + ty * (dist * 0.9 + span * 0.4), 135), 32.0),
    "high": place_camera("Cam_High", unreal.Vector(cx - nx * dist * 0.7 - tx * dist * 0.55, cy - ny * dist * 0.7 - ty * dist * 0.55, 420)),
    "orbit": place_camera("Cam_Orbit", unreal.Vector(cx - nx * dist, cy - ny * dist, 160)),
}


def key_transform(binding, keys):
    """keys: [(frame, loc, rot)]"""
    tt = binding.add_track(unreal.MovieScene3DTransformTrack)
    ts = tt.add_section()
    ts.set_range(0, end_f)
    ch = ts.get_all_channels()
    for f, loc, rot in keys:
        for i, v in enumerate((loc.x, loc.y, loc.z, rot.roll, rot.pitch, rot.yaw, 1, 1, 1)):
            ch[i].add_key(unreal.FrameNumber(int(f)), v)


cam = cams.get(CAMERA, cams["side"])
cam_binding = seq.add_possessable(cam)
if CAMERA == "side":
    # follow the athlete along the track: heavily smoothed hip position, fixed distance and height
    shift_a = lead - ALIGN_A
    win = 0.6

    def smooth_s(t):
        vals = [s for (tt_, _), s in zip(path_a, proj) if abs(tt_ - t) <= win]
        return sum(vals) / len(vals) if vals else proj[0]

    keys = []
    step = max(1, rate // 6)
    for f in range(0, end_f + step, step):
        t = f / rate - shift_a
        s = smooth_s(max(0.0, min(L, t)))
        loc = along(s, -side_d, 135)
        look = along(s, 0, 112)
        keys.append((min(f, end_f), loc, unreal.MathLibrary.find_look_at_rotation(loc, look)))
    key_transform(cam_binding, keys)
elif CAMERA == "orbit":
    # a slow 200-degree arc around the athlete over the whole replay
    a0 = math.atan2(-ny, -nx)
    steps = 24
    keys = []
    for k in range(steps + 1):
        ang = a0 + math.radians(200) * k / steps
        loc = unreal.Vector(cx + math.cos(ang) * dist, cy + math.sin(ang) * dist, 160 + 60 * math.sin(math.pi * k / steps))
        keys.append((int(end_f * k / steps), loc, unreal.MathLibrary.find_look_at_rotation(loc, center)))
    key_transform(cam_binding, keys)
cut_track = seq.add_track(unreal.MovieSceneCameraCutTrack)
cut = cut_track.add_section()
cut.set_range(0, end_f)
cut.set_camera_binding_id(seq.get_binding_id(cam_binding))
eal.save_loaded_asset(seq)
les.save_current_level()
if peak_info:
    print(f"peak {peak_info[0]:.3f} {peak_info[1]:.1f}")
print(f"stage ready: {suffix} | camera {CAMERA} | {end_t:.2f}s @ {rate} fps | actors {len(actors)}")
