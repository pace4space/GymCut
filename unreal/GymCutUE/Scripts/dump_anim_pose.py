"""Dump component-space joint positions of an exported body AnimSequence (run in editor).
Set TAKE before running. Writes Saved/GymCut/<TAKE>_pose.json."""
import json
import os

import unreal

TAKE = globals().get("TAKE", "Take02_FrontTuck")
anim = unreal.EditorAssetLibrary.load_asset(f"/Game/GymCut/Takes/{TAKE}/AS_{TAKE}")
BONES = ["pelvis", "spine_03", "spine_05", "neck_01", "head",
         "clavicle_l", "upperarm_l", "lowerarm_l", "hand_l", "clavicle_r", "upperarm_r", "lowerarm_r", "hand_r",
         "thigh_l", "calf_l", "foot_l", "ball_l", "thigh_r", "calf_r", "foot_r", "ball_r"]
length = anim.get_play_length()
fps = 30.0
n = int(round(length * fps)) + 1
opts = unreal.AnimPoseEvaluationOptions()
frames = []
for i in range(n):
    t = min(length, i / fps)
    pose = unreal.AnimPoseExtensions.get_anim_pose_at_time(anim, t, opts)
    row = {}
    for b in BONES:
        try:
            tr = unreal.AnimPoseExtensions.get_bone_pose(pose, b, unreal.AnimPoseSpaces.WORLD)
            v = tr.translation
            row[b] = [round(v.x, 2), round(v.y, 2), round(v.z, 2)]
        except Exception:
            row[b] = None
    frames.append(row)
out_dir = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir()), "GymCut")
os.makedirs(out_dir, exist_ok=True)
path = os.path.join(out_dir, f"{TAKE}_pose.json")
with open(path, "w") as fh:
    json.dump({"take": TAKE, "fps": fps, "length": length, "frames": frames}, fh)
missing = [b for b in BONES if frames[0].get(b) is None]
print(f"wrote {path}: {n} frames, missing bones: {missing}")
