"""GymCut - gymnastics video trimming and pose analysis."""
import warnings as _w

__version__ = "1.0.0"

# Pose signals are full of legitimately missing values (occlusion, flips); numpy's
# nan-reductions warn on every all-NaN window, which would only clutter the console.
for _msg in ("Mean of empty slice", "All-NaN slice encountered", "All-NaN axis encountered", "invalid value encountered",
             "Degrees of freedom <= 0"):
    _w.filterwarnings("ignore", message=_msg)
