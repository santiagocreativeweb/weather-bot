"""Frozen definition of the forward-only NOAA LAMP exact challenger."""

VERSION = "LAMPX1-20260713"
NOW_VERSION = "LAMPNOW1-20260713"
PARENT_VERSION = "CITYX2-20260713"
SHADOW0 = "2026-07-14"
GATE_DAYS = 45
MIN_EXACT = 0.396
MIN_RESOLVED_COVERAGE = 0.80
AVAIL_LAG_HOURS = 2.0
NOW_ALPHA = 0.25
NOW_CLIP_F = 4.0

# Selected globally on DEV through 2026-06-20.  The offsets and uncertainty
# below were refit once using resolved history through 2026-07-11, before the
# first forward target.  Never update them during this gate.
RECIPE = "BLEND50|X60"
TRAINING_CUTOFF = "2026-07-11"
OFFSETS_F = {
    "KATL": 1.5, "KAUS": 1.0, "KDAL": 0.0,
    "KHOU": 0.5, "KLAX": 0.5, "KLGA": 0.0,
    "KMIA": 1.0, "KORD": 0.5, "KSFO": 0.0,
}
SIGMA_F = {
    "KATL": 2.038446, "KAUS": 1.792899, "KDAL": 2.011532,
    "KHOU": 1.406769, "KLAX": 1.0, "KLGA": 2.066366,
    "KMIA": 1.305275, "KORD": 1.719920, "KSFO": 1.893256,
}


def prediction(station, lav_tmax, cityx_mu):
    """Apply the immutable LAMPX1 50/50 blend and station offset."""
    return (float(lav_tmax) + float(cityx_mu)) / 2 + OFFSETS_F[station]


def now_prediction(lamp_mu, innovation):
    """Immutable secondary correction from the last pre-freeze ASOS report."""
    clipped = max(min(float(innovation), NOW_CLIP_F), -NOW_CLIP_F)
    return float(lamp_mu) + NOW_ALPHA*clipped


def gate(exact, cityx_exact, top2, cityx_top2, p_value, days, resolved_coverage):
    """Return the preregistered promotion verdict; no trading side effects."""
    return (days >= GATE_DAYS and resolved_coverage >= MIN_RESOLVED_COVERAGE and
            exact > MIN_EXACT and exact > cityx_exact and
            top2 >= cityx_top2 and p_value < 0.05)


def now_gate(lamp_passed, exact, lamp_exact, top2, lamp_top2, p_value, days,
             resolved_coverage):
    """Hierarchical gate: NOW is tested only after the parent LAMP gate passes."""
    return (lamp_passed and days >= GATE_DAYS and
            resolved_coverage >= MIN_RESOLVED_COVERAGE and exact > lamp_exact and
            top2 >= lamp_top2 and p_value < 0.05)
