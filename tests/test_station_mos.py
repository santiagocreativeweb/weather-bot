import datetime as dt

import pandas as pd

from scripts import backfill_station_mos as mos


def test_station_mos_uses_latest_runtime_available_before_freeze(monkeypatch):
    target = dt.date(2026, 7, 14)
    monkeypatch.setattr(mos, "freeze_utc", lambda station, day:
                        dt.datetime(2026, 7, 14, 8, 30))
    monkeypatch.setattr(mos, "local_offset", lambda station, day: -4)
    rows = []
    for runtime, value in [("2026-07-14T00:00:00Z", 84),
                           ("2026-07-14T06:00:00Z", 99)]:
        rows.extend([
            {"runtime": pd.Timestamp(runtime), "ftime": pd.Timestamp("2026-07-14T12:00:00Z"),
             "n_x": value-10},
            {"runtime": pd.Timestamp(runtime), "ftime": pd.Timestamp("2026-07-15T00:00:00Z"),
             "n_x": value},
        ])
    got = mos.select_daily(pd.DataFrame(rows), "KLGA", "GFS", target, target)
    assert len(got) == 1
    assert got[0]["runtime_utc"].startswith("2026-07-14T00:00:00")
    assert got[0]["tmax"] == 84
    assert got[0]["avail_utc"] < got[0]["freeze_utc"]


def test_station_mos_uses_nbm_native_txn_field(monkeypatch):
    target = dt.date(2026, 7, 14)
    monkeypatch.setattr(mos, "freeze_utc", lambda station, day:
                        dt.datetime(2026, 7, 14, 8, 30))
    monkeypatch.setattr(mos, "local_offset", lambda station, day: -4)
    frame = pd.DataFrame([
        {"runtime": pd.Timestamp("2026-07-14T00:00:00Z"),
         "ftime": pd.Timestamp("2026-07-14T12:00:00Z"), "txn": 72},
        {"runtime": pd.Timestamp("2026-07-14T00:00:00Z"),
         "ftime": pd.Timestamp("2026-07-15T00:00:00Z"), "txn": 91},
    ])
    got = mos.select_daily(frame, "KLGA", "NBS", target, target)
    assert got[0]["tmax"] == 91
