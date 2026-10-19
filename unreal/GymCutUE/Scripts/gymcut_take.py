"""GymCut -> MetaHuman Animator body solve, run inside Unreal Editor (via gymcut.unreal_bridge).

Set TAKE (folder name under Content/Movies/GymCut) and STEP before running:
  STEP = "setup"   create ImgMediaSource + FootageCaptureData + MetaHumanPerformance (body tracking on)
  STEP = "start"   start processing (non-blocking)
  STEP = "status"  report progress
  STEP = "export"  export the solved body animation as an AnimSequence
"""
import json
import os

import unreal

TAKE = globals().get("TAKE", "Take01_RoundoffBackTuck")
STEP = globals().get("STEP", "setup")

project_dir = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
movie_dir = os.path.join(project_dir, "Content", "Movies", "GymCut", TAKE)
pkg = f"/Game/GymCut/Takes/{TAKE}"
tools = unreal.AssetToolsHelpers.get_asset_tools()
eal = unreal.EditorAssetLibrary


def load_or_create(name, cls, factory):
    path = f"{pkg}/{name}"
    if eal.does_asset_exist(path):
        return eal.load_asset(path)
    return tools.create_asset(name, pkg, cls, factory)


def get_perf():
    return eal.load_asset(f"{pkg}/PERF_{TAKE}")


with open(os.path.join(movie_dir, "gymcut_attempt.json"), encoding="utf-8") as _fh:
    info = json.load(_fh)
rate = int(info["frame_rate"])

if STEP == "setup":
    img = load_or_create(f"IMG_{TAKE}", unreal.ImgMediaSource, unreal.ImgMediaSourceFactoryNew())
    img.set_sequence_path(movie_dir)
    img.set_editor_property("frame_rate_override", unreal.FrameRate(rate, 1))
    eal.save_loaded_asset(img)

    fcd = load_or_create(f"FCD_{TAKE}", unreal.FootageCaptureData, unreal.FootageCaptureDataFactory())
    fcd.set_editor_property("image_sequences", [img])
    first = sorted(f for f in os.listdir(movie_dir) if f.lower().endswith(".jpg"))[0]
    md = unreal.FootageCaptureMetadata()
    md.set_editor_property("frame_rate", float(rate))
    md.set_editor_property("device_model_name", "GymCut export (phone camera)")
    try:  # resolution from the first frame
        import struct
        with open(os.path.join(movie_dir, first), "rb") as fh:
            data = fh.read()
        i = 2
        while i < len(data):
            marker, size = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC2):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                md.set_editor_property("width", w)
                md.set_editor_property("height", h)
                break
            i += 2 + size
    except Exception as e:
        print("resolution:", e)
    fcd.set_editor_property("metadata", md)
    eal.save_loaded_asset(fcd)

    perf = load_or_create(f"PERF_{TAKE}", unreal.MetaHumanPerformance, unreal.MetaHumanPerformanceFactoryNew())
    perf.set_editor_property("input_type", unreal.DataInputType.MONO_FOOTAGE)
    perf.set_editor_property("footage_capture_data", fcd)
    perf.set_editor_property("face_tracking", False)
    perf.set_editor_property("body_tracking", True)
    perf.set_editor_property("skip_diagnostics", True)
    eal.save_loaded_asset(perf)
    print(f"setup ok: {TAKE} | {info['frames']} frames @ {rate} fps | can_process={perf.can_process()}")

elif STEP == "start":
    perf = get_perf()
    if not perf.can_process():
        print("cannot process yet (missing footage, solver or identity)")
    else:
        perf.set_blocking_processing(False)
        print("started:", perf.start_pipeline())

elif STEP == "status":
    perf = get_perf()
    print(f"processing={perf.is_processing()} frames_done={perf.get_number_of_processed_frames()} / {info['frames']} "
          f"has_anim={perf.contains_animation_data()} issues={perf.diagnostics_indicates_processing_issue()}")

elif STEP == "export":
    perf = get_perf()
    settings = unreal.MetaHumanPerformanceExportUtils.get_export_animation_sequence_settings(perf)
    settings.set_editor_property("export_body", True)
    settings.set_editor_property("export_face", False)
    settings.set_editor_property("show_export_dialog", False)
    settings.set_editor_property("package_path", pkg)
    settings.set_editor_property("asset_name", f"AS_{TAKE}")
    settings.set_editor_property("body_unsolved_behavior", unreal.BodyUnsolvedFrameBehavior.LAST_VALID_FRAME)
    anim = unreal.MetaHumanPerformanceExportUtils.export_animation_sequence(perf, settings)
    print("exported:", anim.get_path_name() if anim else None)
