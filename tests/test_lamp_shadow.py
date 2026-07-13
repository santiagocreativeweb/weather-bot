import datetime as dt
import json
import os
import time

import pandas as pd

from scripts.accumulate_lamp_shadow import (acquire_lock, build_row, capture_window_open,
                                            eligible_cityx, release_lock)
from scripts import score_lamp_shadow as scorer
from scripts.score_lamp_shadow import gate_state, winner_label
from wxbt.lamp_shadow import NOW_VERSION, VERSION, gate, now_gate, now_prediction, prediction


def test_lamp_prediction_is_frozen_blend_plus_offset():
    assert prediction("KATL", 90, 86) == 89.5


def test_lamp_gate_requires_every_preregistered_condition():
    assert gate(.45, .40, .75, .70, .01, 45, .90)
    assert not gate(.45, .40, .75, .70, .01, 44, .90)
    assert not gate(.45, .46, .75, .70, .01, 45, .90)
    assert not gate(.45, .40, .69, .70, .01, 45, .90)
    assert not gate(.45, .40, .75, .70, .01, 45, .79)


def test_nowcast_is_clipped_and_gate_is_hierarchical():
    assert now_prediction(80, 2) == 80.5
    assert now_prediction(80, 20) == 81
    assert now_gate(True, .47, .45, .76, .75, .01, 45, .90)
    assert not now_gate(False, .47, .45, .76, .75, .01, 45, .90)
    assert not now_gate(True, .47, .45, .76, .75, .01, 45, .70)


def test_cityx_parent_must_be_frozen_before_cutoff(monkeypatch):
    monkeypatch.setattr("scripts.accumulate_lamp_shadow.freeze_utc",
                        lambda station, target: dt.datetime(2026, 7, 14, 8, 30))
    frame = pd.DataFrame([
        {"station": "KLGA", "target": "2026-07-14", "version": "CITYX2-20260713",
         "capture_utc": "2026-07-14T08:00:00Z", "mu": 80},
        {"station": "KLGA", "target": "2026-07-14", "version": "CITYX2-20260713",
         "capture_utc": "2026-07-14T09:00:00Z", "mu": 99},
    ])
    got = eligible_cityx(frame, "KLGA", dt.date(2026, 7, 14))
    assert got.mu == 80


def test_capture_waits_for_freeze_and_stops_at_local_midnight(monkeypatch):
    monkeypatch.setattr("scripts.accumulate_lamp_shadow.freeze_utc",
                        lambda station, target: dt.datetime(2026, 7, 14, 8, 30))
    monkeypatch.setattr("scripts.accumulate_lamp_shadow.local_day_end_utc",
                        lambda station, target: dt.datetime(2026, 7, 15, 4, 0))
    utc = dt.timezone.utc
    assert not capture_window_open("KLGA", dt.date(2026, 7, 14),
                                   dt.datetime(2026, 7, 14, 8, tzinfo=utc))
    assert capture_window_open("KLGA", dt.date(2026, 7, 14),
                               dt.datetime(2026, 7, 14, 15, tzinfo=utc))
    assert not capture_window_open("KLGA", dt.date(2026, 7, 14),
                                   dt.datetime(2026, 7, 15, 5, tzinfo=utc))


def test_capture_row_preserves_provenance():
    lav = {"runtime_utc": "2026-07-14T06:00:00Z",
           "avail_utc": "2026-07-14T08:00:00Z",
           "freeze_utc": "2026-07-14T08:30:00Z", "tmax": 84}
    cityx = pd.Series({"capture_utc": pd.Timestamp("2026-07-13T13:00:00Z"), "mu": 82})
    nowcast = {"obs_valid_utc": "2026-07-14T08:00:00Z",
               "obs_avail_utc": "2026-07-14T08:15:00Z", "n_obs": 4,
               "obs_latest": 80, "obs_first": 76, "obs_max": 80, "obs_min": 75,
               "obs_trend_fph": 1, "lav_at_obs": 78, "lav_slope_fph": 1.5,
               "lav_peak_utc": "2026-07-14T18:00:00Z", "lav_peak_hour_local": 14,
               "hours_to_lav_peak": 10, "innovation": 2}
    row = build_row("KLGA", dt.date(2026, 7, 14), lav, cityx,
                    dt.datetime(2026, 7, 14, 15, tzinfo=dt.timezone.utc), nowcast)
    assert row["version"] == VERSION
    assert row["mu_lampx"] == 83
    assert row["now_version"] == NOW_VERSION
    assert row["mu_nowx"] == 83.5
    assert row["obs_max"] == 80
    assert row["hours_to_lav_peak"] == 10
    assert row["lav_avail_utc"] < row["freeze_utc"]


def test_gamma_tuple_is_rendered_for_existing_scoring_helpers():
    assert winner_label((84, 85)) == "84-85°F"
    assert winner_label((None, 81)) == "<= 81°F"
    assert winner_label((88, None)) == ">= 88°F"


def test_process_lock_is_exclusive_and_reusable(tmp_path):
    path = str(tmp_path / "lamp.lock")
    assert acquire_lock(path)
    assert not acquire_lock(path)
    release_lock(path)
    assert acquire_lock(path)
    release_lock(path)
    assert not os.path.exists(path)


def test_process_lock_recovers_dead_owner_but_not_fresh_corruption(tmp_path):
    path = tmp_path / "lamp.lock"
    path.write_text(json.dumps({"pid": 99999999}), encoding="utf-8")
    assert acquire_lock(str(path))
    release_lock(str(path))
    path.write_text("partial", encoding="utf-8")
    assert not acquire_lock(str(path))
    old = time.time()-7200
    os.utime(path, (old, old))
    assert acquire_lock(str(path))
    release_lock(str(path))


def test_gate_waits_for_days_and_resolved_coverage_before_deciding():
    assert gate_state(44, 1.0, True, True) == "ACCUMULATING"
    assert gate_state(45, .79, False, False) == "WAITING_RESOLUTION"
    assert gate_state(45, .80, False, False) == "REJECT_LAMP_AND_NOW"
    assert gate_state(45, .80, True, False) == "ADOPT_LAMP_REJECT_NOW"
    assert gate_state(45, .80, True, True) == "ADOPT_NOW"


def test_gate_status_is_atomically_materialized(tmp_path, monkeypatch):
    path = tmp_path / "verdict.csv"
    monkeypatch.setattr(scorer, "STATUS", str(path))
    scorer.write_status(state="ACCUMULATING", resolved_days=3,
                        lamp_decision="PENDING", now_decision="PENDING")
    row = pd.read_csv(path).iloc[0]
    assert row.state == "ACCUMULATING"
    assert row.resolved_days == 3
    assert row.lamp_version == VERSION
    assert not (tmp_path / "verdict.csv.tmp").exists()
