"""Live 3D in the editor, driven by GymCut's live stream.

Two modes:
  livelink  (default when the GymCutLiveLink plugin is loaded) - a real Live Link source,
            subject "GymCut" (UDP 54322); the MetaHuman plays it through a LiveLinkInstance and
            updates in the editor viewport. Works with the Live Link panel, Take Recorder, etc.
  puppet    fallback without the plugin - the editor applies bone rotations itself every tick
            on a poseable mesh in Simulate mode (UDP 54321).

Globals:
  STEP   start | stop | status
  MODE   auto | livelink | puppet
  PORT   UDP port of the puppet mode (default 54321, localhost only)
Packets (JSON): {"t": seconds, "p": [x, y, z] pelvis cm, "q": {bone: [x, y, z, w]}}  component space.
On start the MetaHuman reference skeleton is written to Saved/GymCut/mh_ref_skeleton.json
so GymCut can retarget onto exactly these bones.
"""
import builtins
import json
import math
import os
import socket
import time

import unreal

STEP = globals().get("STEP", "start")
MODE = globals().get("MODE", "auto")
LL_PORT = int(globals().get("LL_PORT", 54322))
HAS_LL = hasattr(unreal, "GymCutLiveLinkLibrary")
PORT = int(globals().get("PORT", 54321))
LEVEL = "/Game/GymCut/Maps/LiveStage"
BODY_MESH = "/MetaHumanCharacter/Body/IdentityTemplate/SKM_Body"
FACE_MESH = "/MetaHumanCharacter/Face/SKM_Face"
BONES = ["pelvis", "spine_01", "spine_02", "spine_03", "spine_04", "spine_05", "neck_01", "neck_02", "head",
         "clavicle_l", "upperarm_l", "lowerarm_l", "hand_l", "clavicle_r", "upperarm_r", "lowerarm_r", "hand_r",
         "thigh_l", "calf_l", "foot_l", "ball_l", "thigh_r", "calf_r", "foot_r", "ball_r"]
CS = unreal.BoneSpaces.COMPONENT_SPACE

eal = unreal.EditorAssetLibrary
eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
ues = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
S = builtins.__dict__.setdefault("_gymcut_live", {})       # survives between remote-execution calls


def _sim_comp(cls=None):
    """The puppet's component in the Simulate-in-Editor world (components tick there, so
    poseable-mesh changes actually render); falls back to the editor-world component."""
    world = ues.get_game_world()
    if world is None:
        return S.get("comp")
    cached = S.get("sim_comp")
    try:
        stale = cached is None or not cached.get_name()
    except Exception:                                     # object of an ended Simulate session
        stale = True
    if stale or S.get("sim_world") != world.get_path_name():
        found = unreal.GameplayStatics.get_all_actors_with_tag(world, "GymCutLive")
        S["sim_comp"] = found[0].get_component_by_class(cls or unreal.PoseableMeshComponent) if found else None
        S["sim_new"] = True
        S["sim_world"] = world.get_path_name()
    return S.get("sim_comp") or S.get("comp")


def _stop():
    unreal.SystemLibrary.execute_console_command(None, "t.MaxFPS 0")
    if HAS_LL and S.get("mode") == "livelink":
        unreal.GymCutLiveLinkLibrary.stop_gym_cut_source()
    if les.is_in_play_in_editor():
        les.editor_request_end_play()
    if S.get("tick") is not None:
        unreal.unregister_slate_post_tick_callback(S["tick"])
        S["tick"] = None
    if S.get("sock") is not None:
        try:
            S["sock"].close()
        except Exception:
            pass
        S["sock"] = None


def _mat(name, rgb, rough=0.6):
    path = f"/Game/GymCut/Materials/Stage/{name}"
    if eal.does_asset_exist(path):
        return eal.load_asset(path)
    mel = unreal.MaterialEditingLibrary
    m = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, "/Game/GymCut/Materials/Stage", unreal.Material, unreal.MaterialFactoryNew())
    c = mel.create_material_expression(m, unreal.MaterialExpressionConstant3Vector, -400, 0)
    c.set_editor_property("constant", unreal.LinearColor(*rgb, 1.0))
    mel.connect_material_property(c, "", unreal.MaterialProperty.MP_BASE_COLOR)
    r = mel.create_material_expression(m, unreal.MaterialExpressionConstant, -400, 200)
    r.set_editor_property("r", rough)
    mel.connect_material_property(r, "", unreal.MaterialProperty.MP_ROUGHNESS)
    mel.recompile_material(m)
    eal.save_loaded_asset(m)
    return m


def _box(label, mesh, loc, scale, mat, rot=None):
    a = eas.spawn_actor_from_class(unreal.StaticMeshActor, loc, rot or unreal.Rotator(0, 0, 0))
    a.set_actor_label(label)
    a.static_mesh_component.set_static_mesh(eal.load_asset(mesh))
    a.static_mesh_component.set_material(0, mat)
    a.set_actor_scale3d(scale)
    return a


def _build_level():
    if eal.does_asset_exist(LEVEL):
        les.load_level(LEVEL)
        for a in eas.get_all_level_actors():
            eas.destroy_actor(a)
    else:
        les.new_level(LEVEL)
    _box("Floor", "/Engine/BasicShapes/Plane", unreal.Vector(0, 0, 0), unreal.Vector(60, 60, 1), _mat("M_HallFloor", (0.11, 0.115, 0.13), 0.9))
    _box("TumbleTrack", "/Engine/BasicShapes/Cube", unreal.Vector(0, 0, 0.5), unreal.Vector(16, 2, 0.01), _mat("M_TumbleTrack", (0.03, 0.11, 0.42), 0.75))
    for k in range(-8, 9):
        _box("Mark", "/Engine/BasicShapes/Cube", unreal.Vector(k * 100, 0, 0.7), unreal.Vector(0.03, 2.0 if k % 5 == 0 else 0.5, 0.012), _mat("M_TrackLine", (0.85, 0.85, 0.85)))
    sun = eas.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 800), unreal.Rotator(0, -60, -40))
    sun.light_component.set_editor_property("intensity", 5.0)
    sky = eas.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 400))
    sky.light_component.set_editor_property("real_time_capture", True)
    eas.spawn_actor_from_class(unreal.SkyAtmosphere, unreal.Vector(0, 0, 0))


def _spawn_puppet(material):
    actor = eas.spawn_actor_from_class(unreal.Actor, unreal.Vector(0, 0, 0))
    actor.set_actor_label("GymCutLiveAthlete")
    actor.tags = ["GymCutLive"]
    sds = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    root = sds.k2_gather_subobject_data_for_instance(actor)[0]
    sds.add_new_subobject(unreal.AddNewSubobjectParams(parent_handle=root, new_class=unreal.PoseableMeshComponent))
    comp = actor.get_component_by_class(unreal.PoseableMeshComponent)
    comp.set_skinned_asset_and_update(eal.load_asset(BODY_MESH), True)
    for i in range(comp.get_num_materials()):
        comp.set_material(i, material)
    face = eal.load_asset(FACE_MESH)
    if face:
        head = eas.spawn_actor_from_class(unreal.SkeletalMeshActor, unreal.Vector(0, 0, 0))
        head.set_actor_label("GymCutLiveAthlete_Head")
        hc = head.skeletal_mesh_component
        hc.set_skeletal_mesh_asset(face)
        for i in range(hc.get_num_materials()):
            hc.set_material(i, material)
        # face mesh (head + neck) copies the body's pose bone by bone, so the neck bends with it
        head.attach_to_component(comp, "", unreal.AttachmentRule.SNAP_TO_TARGET, unreal.AttachmentRule.SNAP_TO_TARGET,
                                 unreal.AttachmentRule.KEEP_WORLD, False)
        hc.set_leader_pose_component(comp, True, False)
    return actor, comp


def _spawn_livelink(material):
    """MetaHuman body + head playing the Live Link subject "GymCut" in the editor world."""
    body = eas.spawn_actor_from_class(unreal.SkeletalMeshActor, unreal.Vector(0, 0, 0))
    body.set_actor_label("GymCutLiveLinkAthlete")
    body.tags = ["GymCutLive"]
    comp = body.skeletal_mesh_component
    comp.set_skeletal_mesh_asset(eal.load_asset(BODY_MESH))
    for i in range(comp.get_num_materials()):
        comp.set_material(i, material)
    comp.set_update_animation_in_editor(True)            # animate in the editor world (no Simulate needed)
    # keep evaluating while the viewport isn't drawing (editor in the background, off-screen captures)
    comp.set_editor_property("visibility_based_anim_tick_option", unreal.VisibilityBasedAnimTickOption.ALWAYS_TICK_POSE_AND_REFRESH_BONES)
    comp.set_animation_mode(unreal.AnimationMode.ANIMATION_BLUEPRINT)
    comp.set_anim_instance_class(unreal.LiveLinkInstance)
    anim = comp.get_anim_instance()
    if anim:
        anim.set_subject(unreal.LiveLinkSubjectName(name="GymCut"))
        anim.enable_live_link_evaluation(True)
    face = eal.load_asset(FACE_MESH)
    if face:
        head = eas.spawn_actor_from_class(unreal.SkeletalMeshActor, unreal.Vector(0, 0, 0))
        head.set_actor_label("GymCutLiveLinkAthlete_Head")
        hc = head.skeletal_mesh_component
        hc.set_skeletal_mesh_asset(face)
        for i in range(hc.get_num_materials()):
            hc.set_material(i, material)
        # face mesh (head + neck) copies the body's pose bone by bone, so the neck bends with it
        head.attach_to_component(comp, "", unreal.AttachmentRule.SNAP_TO_TARGET, unreal.AttachmentRule.SNAP_TO_TARGET,
                                 unreal.AttachmentRule.KEEP_WORLD, False)
        hc.set_leader_pose_component(comp, True, False)
    return body, comp


def _ll_tick(dt):
    """Live Link mode: Live Link animates the mesh in the Simulate world (it ticks even while the
    editor is in the background); this re-binds the subject on the simulated copy and moves the
    follow camera."""
    comp = _sim_comp(unreal.SkeletalMeshComponent)
    try:
        if S.pop("sim_new", False):
            anim = comp.get_anim_instance()
            if isinstance(anim, unreal.LiveLinkInstance):
                anim.set_subject(unreal.LiveLinkSubjectName(name="GymCut"))
                anim.enable_live_link_evaluation(True)
        p = comp.get_socket_location("pelvis")
    except Exception:
        _stop()
        return
    cx = S["cam_x"] = S.get("cam_x", p.x) + (p.x - S.get("cam_x", p.x)) * min(1.0, dt * 3.0)
    S["p"] = [p.x, p.y, p.z]
    if S.get("follow", True):
        loc = unreal.Vector(cx, -560.0, 150.0)
        ues.set_level_viewport_camera_info(loc, unreal.MathLibrary.find_look_at_rotation(loc, unreal.Vector(cx, 0, 110)))


def _dump_ref(comp):
    out = {}
    for b in BONES:
        tr = comp.get_bone_transform_by_name(b, CS)
        q = tr.rotation
        out[b] = {"parent": str(comp.get_parent_bone(b)), "t": [tr.translation.x, tr.translation.y, tr.translation.z],
                  "q": [q.x, q.y, q.z, q.w]}
    d = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir()), "GymCut")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "mh_ref_skeleton.json"), "w") as fh:
        json.dump({"bones": out, "order": BONES}, fh, indent=1)
    return out


def _nlerp(a, b, t):
    if sum(x * y for x, y in zip(a, b)) < 0:
        b = [-x for x in b]
    q = [x + (y - x) * t for x, y in zip(a, b)]
    n = math.sqrt(sum(x * x for x in q)) or 1.0
    return [x / n for x in q]


def _tick(dt):
    sock, comp = S.get("sock"), _sim_comp()
    if sock is None or comp is None:
        return
    pkt = None
    try:
        while True:
            pkt = sock.recv(65535)
    except (BlockingIOError, socket.timeout, OSError):
        pass
    if pkt:
        try:
            S["target"] = json.loads(pkt)
            S["rx"] += 1
            S["last_rx"] = time.time()
        except ValueError:
            pass
    tgt = S.get("target")
    if not tgt:
        return
    a = 1.0 - math.exp(-max(dt, 1e-3) * 14.0)            # ~70 ms smoothing between 15 Hz updates
    cur = S["cur"]
    for b, q in tgt["q"].items():
        cur[b] = _nlerp(cur.get(b, q), q, a)
    p = S["p"] = [x + (y - x) * a for x, y in zip(S.get("p") or tgt["p"], tgt["p"])]
    try:
        comp.set_bone_location_by_name("pelvis", unreal.Vector(*p), CS)
        for b in BONES:
            if b in cur:
                q = cur[b]
                comp.set_bone_rotation_by_name(b, unreal.Quat(q[0], q[1], q[2], q[3]).rotator(), CS)
    except Exception as e:                                # the level was closed / actor deleted
        unreal.log_warning(f"GymCut live puppet stopped: {e}")
        _stop()
        return
    if S.get("follow", True):
        # side-on viewport camera that follows the athlete along the track
        cx = S["cam_x"] = S.get("cam_x", p[0]) + (p[0] - S.get("cam_x", p[0])) * min(1.0, dt * 3.0)
        loc = unreal.Vector(cx, -560.0, 150.0)
        ues.set_level_viewport_camera_info(loc, unreal.MathLibrary.find_look_at_rotation(loc, unreal.Vector(cx, 0, 110)))


if STEP == "stop":
    _stop()
    print("live puppet stopped")
elif STEP == "status":
    ll = unreal.GymCutLiveLinkLibrary.get_gym_cut_source_status() if HAS_LL else "plugin not loaded"
    print(f"live puppet: mode={S.get('mode')} running={S.get('tick') is not None} simulating={les.is_in_play_in_editor()} livelink=[{ll}] port={S.get('port')} frames={S.get('rx', 0)} "
          f"last={time.time() - S.get('last_rx', 0):.1f}s ago")
else:
    _stop()
    _build_level()
    mat = eal.load_asset(globals().get("MATERIAL", "/Game/GymCut/Materials/Stage/M_Athlete_289e84")) or _mat("M_LiveAthlete", (0.16, 0.62, 0.52), 0.45)
    actor, comp = _spawn_puppet(mat)
    ref = _dump_ref(comp)
    cap = eas.spawn_actor_from_class(unreal.SceneCapture2D, unreal.Vector(0, -560, 150))
    cap.set_actor_label("GymCutCapture")
    cap.tags = ["GymCutCapture"]
    cap.capture_component2d.set_editor_property("capture_every_frame", False)
    loc = unreal.Vector(0, -560, 150)
    ues.set_level_viewport_camera_info(loc, unreal.MathLibrary.find_look_at_rotation(loc, unreal.Vector(0, 0, 110)))
    unreal.SystemLibrary.execute_console_command(None, "t.MaxFPS 30")   # leave GPU headroom for GymCut's tracker
    if HAS_LL and MODE != "puppet":
        # Live Link mode: the poseable puppet was only needed for the reference skeleton
        for a in eas.get_all_level_actors():
            if a.get_actor_label().startswith("GymCutLiveAthlete"):
                eas.destroy_actor(a)
        actor, comp = _spawn_livelink(mat)
        ok = unreal.GymCutLiveLinkLibrary.start_gym_cut_source(LL_PORT)
        S.update(comp=comp, actor=actor, mode="livelink", port=LL_PORT, rx=0, last_rx=0.0, p=None, sim_world=None, sim_comp=None)
        les.editor_play_simulate()                       # the world ticks even while Unreal is in the background
        S["fn"] = _ll_tick
        S["tick"] = unreal.register_slate_post_tick_callback(_ll_tick)
        print(f"live link source {'running' if ok else 'FAILED'} on udp 127.0.0.1:{LL_PORT} | subject GymCut | bones {len(ref)}")
    else:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", PORT))
        sock.setblocking(False)
        S.update(sock=sock, comp=comp, actor=actor, mode="puppet", port=PORT, rx=0, last_rx=0.0, target=None, cur={}, p=None,
                 sim_world=None, sim_comp=None)
        les.editor_play_simulate()                       # world ticks -> the puppet renders live
        S["fn"] = _tick                                  # lets tools apply the latest packet on demand
        S["tick"] = unreal.register_slate_post_tick_callback(_tick)
        print(f"live puppet listening on udp 127.0.0.1:{PORT} | bones {len(ref)} | pelvis ref {ref['pelvis']['t']}")
