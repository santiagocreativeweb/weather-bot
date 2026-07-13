import datetime as dt

import pandas as pd

from scripts.accumulate_lamp_shadow import build_row, capture_window_open, eligible_cityx
from scripts.score_lamp_shadow import winner_label
from wxbt.lamp_shadow import VERSION, gate, prediction


def test_lamp_prediction_is_frozen_blend_plus_offset():
    assert prediction("KATL", 90, 86) == 89.5


def test_lamp_gate_requires_every_preregistered_condition():
    assert gate(.45, .40, .75, .70, .01, 45)
    assert not gate(.45, .40, .75, .70, .01, 44)
    assert not gate(.45, .46, .75, .70, .01, 45)
    assert not gate(.45, .40, .69, .70, .01, 45)


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
    row = build_row("KLGA", dt.date(2026, 7, 14), lav, cityx,
                    dt.datetime(2026, 7, 14, 15, tzinfo=dt.timezone.utc))
    assert row["version"] == VERSION
    assert row["mu_lampx"] == 83
    assert row["lav_avail_utc"] < row["freeze_utc"]


def test_gamma_tuple_is_rendered_for_existing_scoring_helpers():
    assert winner_label((84, 85)) == "84-85°F"
    assert winner_label((None, 81)) == "<= 81°F"
    assert winner_label((88, None)) == ">= 88°F"
