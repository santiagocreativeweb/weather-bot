import datetime as dt

from scripts import backfill_mos_features as mos
from scripts.backfill_regional_runs import selected_run
from scripts.dashboard import freeze_utc


def test_physical_features_are_derived_from_forecast_series():
    times = [f"2026-07-10T{h:02d}:00" for h in range(24)]
    hourly = {
        "time": times,
        "temperature_2m": list(range(24)),
        "relative_humidity_2m": list(reversed(range(24))),
        "cloud_cover": [50] * 24,
        "shortwave_radiation": [max(0, 700-abs(12-h)*100) for h in range(24)],
        "precipitation": [0] * 23 + [1],
        "wind_speed_10m": [10] * 24,
        "wind_direction_10m": [90] * 24,
    }
    got = mos.extract_features("LEMD", dt.date(2026, 7, 10), hourly)
    assert got["n_hours"] >= 20
    assert got["tmax"] == 21  # Madrid is UTC+2 in July; local target ends at UTC 21.
    assert got["cloud_mean"] == 50
    assert abs(got["wind_u_at_tmax"] - 10) < 1e-6


def test_every_mos_run_is_available_before_freeze():
    target = dt.date(2026, 7, 10)
    for station, models in mos.SPECS.items():
        for _, (_, cycle, lag) in models.items():
            run = selected_run(station, target, cycle, lag)
            assert run + dt.timedelta(hours=lag) <= freeze_utc(station, target)
