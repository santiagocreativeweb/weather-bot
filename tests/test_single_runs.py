import datetime as dt

import pandas as pd

from scripts import backfill_single_runs as backfill
from scripts import backfill_regional_runs as regional
from scripts import lab_single_runs as lab
from scripts.accumulate_exact_selector import station_truth
from wxbt.exact_selector import CITYX1_RECIPES, CITYX2_NEW_RECIPES, RECIPES, SHADOW0, VERSION


def test_conservative_run_is_available_before_freeze():
    target = dt.date(2026, 7, 10)
    for station in ("RJTT", "EGLC", "KLGA", "NZWN"):
        run = backfill.conservative_run(station, target)
        freeze = backfill.freeze_utc(station, target)
        assert run.hour in (0, 12)
        assert run + dt.timedelta(hours=backfill.LAG_H) <= freeze
        assert freeze - (run + dt.timedelta(hours=backfill.LAG_H)) < dt.timedelta(hours=12)


def test_sparse_native_model_daily_max_is_not_dropped():
    times = [f"2026-07-10T{h:02d}:00" for h in range(24)]
    values = [float(h) if h % 3 == 0 else None for h in range(24)]
    got = backfill.model_daily_tmax(times, values, 0, min_points=6)
    assert got[dt.date(2026, 7, 10)] == 21.0


def test_exact_offset_uses_prior_gamma_winners():
    day = dt.date(2026, 7, 10)
    # Raw forecast is consistently one degree cold; +1 should maximize exact hits.
    history = [(day - dt.timedelta(days=i), 29.1, 30.0, "30°C") for i in range(1, 20)]
    assert lab.exact_offset(history, day, 30, "C") == 1.0


def test_bucket_vote_targets_modal_exact_bucket():
    values = {"a": 29.1, "b": 29.8, "c": 30.2, "d": 31.0}
    assert 29.0 <= lab.bucket_vote(values, {}, dt.date(2026, 7, 10), "C") < 30.0


def test_regional_run_is_published_before_freeze():
    target = dt.date(2026, 7, 10)
    for station in ("KLGA", "EGLC", "RJTT"):
        for spec in regional.SPECS.values():
            if station not in spec["stations"]:
                continue
            run = regional.selected_run(station, target, spec["cycle"], spec["lag"])
            available = run + dt.timedelta(hours=spec["lag"])
            assert available <= regional.freeze_utc(station, target)
            assert regional.freeze_utc(station, target) - available < dt.timedelta(
                hours=spec["cycle"])


def test_city_selector_is_frozen_for_all_live_stations():
    assert VERSION == "CITYX2-20260713"
    assert SHADOW0 == "2026-07-14"
    assert len(CITYX1_RECIPES) == 12
    assert len(CITYX2_NEW_RECIPES) == 17
    assert len(RECIPES) == 29
    assert RECIPES["LEMD"] == "BUCKET_ACC60|X60"
    assert RECIPES["LTAC"] == "ALL_MEAN|X60"


def test_cityx2_truth_falls_back_to_gamma_plus_observation():
    day = dt.date(2026, 7, 10)
    backfill = pd.DataFrame(columns=["station", "d", "lead", "max_real", "win_mkt"])
    labels = pd.DataFrame([{"station": "LTAC", "d": day, "win_mkt": "31°C"}])
    obs = pd.DataFrame([{"station": "LTAC", "d": day, "tmax": 31.2}])
    got = station_truth("LTAC", backfill, labels, obs)
    assert len(got) == 1
    assert got.iloc[0].max_real == 31.2
    assert got.iloc[0].win_mkt == "31°C"
