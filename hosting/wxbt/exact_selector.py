"""Frozen city recipes selected before their respective 2026-06-21 holdouts."""

# Do not edit these choices after seeing new resolved targets.  A future change
# must use a new selector version and a new pre-registered holdout.
CITYX1_RECIPES = {
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

# Selected on DEV 2026-05-10..06-20 and evaluated once on a previously unseen
# 2026-06-21..07-11 holdout: 31.6% -> 41.8% exact, p=0.0001.
CITYX2_NEW_RECIPES = {
    "CYYZ": "S_aifs|RAW",
    "EFHK": "BEST_MSE60|B60",
    "KATL": "W_MSE60|X60",
    "KAUS": "S_aifs|B60",
    "KDAL": "G3_MEAN|B30",
    "KHOU": "G3_MEAN|B60",
    "KLAX": "S_arpege|B30",
    "KMIA": "TOP3_MSE60|B60",
    "KSFO": "S_arpege|X30",
    "LTAC": "ALL_MEAN|X60",
    "MMMX": "S_aifs|X60",
    "NZWN": "TOP3_MSE60|X60",
    "SAEZ": "S_ukmo|X60",
    "SBGR": "ALL_TRIM|X30",
    "WMKK": "S_ecmwf|B60",
    "WSSS": "ALL_MED|X60",
    "ZGSZ": "TOP3_MSE60|X30",
}

VERSION = "CITYX2-20260713"
SHADOW0 = "2026-07-14"
RECIPES = {**CITYX1_RECIPES, **CITYX2_NEW_RECIPES}


def recipe(station):
    """Return the frozen base/correction recipe for a station, if supported."""
    return RECIPES.get(station)
