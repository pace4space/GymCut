"""Run inside the editor: list MetaHuman / capture / body-solve API surface."""
import unreal

names = sorted(n for n in dir(unreal) if any(k in n for k in ("MetaHuman", "Performance", "CaptureData", "Footage", "ImgMedia", "Body", "Markerless")))
print("classes:", len(names))
for n in names:
    print(" ", n)

def props(cls_name):
    cls = getattr(unreal, cls_name, None)
    if cls is None:
        print(f"\n[{cls_name}] not available")
        return
    print(f"\n[{cls_name}]")
    try:
        obj = unreal.get_default_object(cls)
    except Exception:
        obj = None
    for attr in sorted(a for a in dir(cls) if not a.startswith("_")):
        if any(k in attr.lower() for k in ("body", "track", "footage", "process", "solve", "export", "frame", "source", "capture", "identity", "skeleton", "realtime", "head", "depth", "camera")):
            print("   ", attr)

for c in ("MetaHumanPerformance", "FootageCaptureData", "MetaHumanPerformanceExportUtils", "ImgMediaSource"):
    props(c)

plugins = unreal.SystemLibrary  # placeholder to keep module referenced
pm = getattr(unreal, "PluginBlueprintLibrary", None)
if pm:
    enabled = [p for p in pm.get_enabled_plugin_names() if any(k in p for k in ("MetaHuman", "Capture", "Body", "Markerless", "Performance"))]
    print("\nenabled plugins:", enabled)
