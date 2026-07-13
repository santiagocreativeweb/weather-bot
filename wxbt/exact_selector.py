"""Frozen city recipes selected before the 2026-06-21 holdout."""

# Do not edit these choices after seeing new resolved targets.  A future change
# must use a new selector version and a new pre-registered holdout.
VERSION = "CITYX1-20260713"
RECIPES = {
    "EDDM": "S_aifs|B30",
    "EGLC": "S_arpege|RAW",
    "KLGA": "G3_MEAN|X30",
    "KORD": "TOP3_MSE60|B60",
    "LEMD": "BUCKET_ACC60|X60",
    "LFPB": "S_gfs13|B30",
    "LIMC": "S_ukmo|B60",
    "RCSS": "S_ukmo|X30",
    "RJTT": "TOP3_MSE60|RAW",
    "RKSI": "S_ecmwf|B30",
    "ZBAA": "S_aifs|B60",
    "ZSPD": "BEST_MSE60|B30",
}


def recipe(station):
    """Return the frozen base/correction recipe for a station, if supported."""
    return RECIPES.get(station)
