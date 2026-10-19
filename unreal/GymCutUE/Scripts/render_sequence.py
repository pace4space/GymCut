"""Render a GymCut level sequence to JPG frames with Movie Render Queue (run in editor).
Globals: SUFFIX (as used by build_stage), RES (width, height)."""
import os

import unreal

SUFFIX = globals().get("SUFFIX")
ANGLE = globals().get("ANGLE", "side")
W, H = globals().get("RES", (1280, 720))
level = f"/Game/GymCut/Maps/Stage_{SUFFIX}"
seq = f"/Game/GymCut/Sequences/LS_{SUFFIX}"
out_dir = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir()), "GymCut", "Renders", SUFFIX, ANGLE)
os.makedirs(out_dir, exist_ok=True)
for f in os.listdir(out_dir):
    os.remove(os.path.join(out_dir, f))

mrq = unreal.get_editor_subsystem(unreal.MoviePipelineQueueSubsystem)
queue = mrq.get_queue()
queue.delete_all_jobs()
job = queue.allocate_new_job(unreal.MoviePipelineExecutorJob)
job.sequence = unreal.SoftObjectPath(seq)
job.map = unreal.SoftObjectPath(level)
cfg = job.get_configuration()
o = cfg.find_or_add_setting_by_class(unreal.MoviePipelineOutputSetting)
o.output_directory = unreal.DirectoryPath(out_dir)
o.output_resolution = unreal.IntPoint(W, H)
o.file_name_format = "frame.{frame_number}"
o.use_custom_frame_rate = False
cfg.find_or_add_setting_by_class(unreal.MoviePipelineDeferredPassBase)
cfg.find_or_add_setting_by_class(unreal.MoviePipelineImageSequenceOutput_JPG)
aa = cfg.find_or_add_setting_by_class(unreal.MoviePipelineAntiAliasingSetting)
aa.spatial_sample_count = 1
aa.temporal_sample_count = 4
mrq.render_queue_with_executor(unreal.MoviePipelinePIEExecutor)
print("render started ->", out_dir)
