import datetime as dt

import pandas as pd

from scripts import backfill_lamp


def test_lamp_selects_latest_run_available_before_freeze(monkeypatch):
    target = dt.date(2026, 7, 14)
    monkeypatch.setattr(backfill_lamp, "freeze_utc", lambda station, day:
                        dt.datetime(2026, 7, 14, 8, 30))
    monkeypatch.setattr(backfill_lamp, "local_offset", lambda station, day: -4)
    rows = []
    for runtime, value in [("2026-07-14T06:00:00Z", 80), ("2026-07-14T08:00:00Z", 99)]:
        for hour in range(12, 27):
            rows.append(dict(runtime=pd.Timestamp(runtime),
                             ftime=pd.Timestamp("2026-07-14T00:00:00Z")+pd.Timedelta(hours=hour),
                             tmp=value+hour/100))
    got = backfill_lamp.select_daily(pd.DataFrame(rows), "KLGA", target, target)
    assert len(got) == 1
    assert got[0]["runtime_utc"].startswith("2026-07-14T06:00:00")
    assert got[0]["tmax"] < 90


def test_lamp_supports_more_conservative_publication_lag(monkeypatch):
    target = dt.date(2026, 7, 14)
    monkeypatch.setattr(backfill_lamp, "freeze_utc", lambda station, day:
                        dt.datetime(2026, 7, 14, 8, 30))
    monkeypatch.setattr(backfill_lamp, "local_offset", lambda station, day: -4)
    rows = []
    for runtime, value in [("2026-07-14T06:00:00Z", 80), ("2026-07-14T07:00:00Z", 99)]:
        for hour in range(12, 27):
            rows.append(dict(runtime=pd.Timestamp(runtime),
                             ftime=pd.Timestamp("2026-07-14T00:00:00Z") +
                                   pd.Timedelta(hours=hour), tmp=value))
    got = backfill_lamp.select_daily(
        pd.DataFrame(rows), "KLGA", target, target, avail_lag_hours=2)
    assert len(got) == 1
    assert got[0]["runtime_utc"].startswith("2026-07-14T06:00:00")
