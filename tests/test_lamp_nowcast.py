import datetime as dt

import pandas as pd

from scripts import backfill_lamp_nowcast as nowcast


def test_nowcast_uses_only_observations_available_before_freeze(monkeypatch):
    target = dt.date(2026, 7, 14)
    monkeypatch.setattr(nowcast, "freeze_utc", lambda station, day:
                        dt.datetime(2026, 7, 14, 8, 30))
    monkeypatch.setattr(nowcast, "local_offset", lambda station, day: -4)
    lav_rows = []
    for runtime, base in [("2026-07-14T06:00:00Z", 70),
                          ("2026-07-14T08:00:00Z", 99)]:
        for hour in range(7, 24):
            lav_rows.append({"runtime": pd.Timestamp(runtime),
                "ftime": pd.Timestamp("2026-07-14T00:00:00Z")+pd.Timedelta(hours=hour),
                "tmp": base+hour/10})
    obs = pd.DataFrame([
        {"valid": pd.Timestamp("2026-07-14T07:00:00Z"), "tmpf": 68},
        {"valid": pd.Timestamp("2026-07-14T08:00:00Z"), "tmpf": 72},
        # With a 15-minute publication lag this report misses the 08:30 freeze.
        {"valid": pd.Timestamp("2026-07-14T08:20:00Z"), "tmpf": 90},
    ])
    got = nowcast.select_features(pd.DataFrame(lav_rows), obs, "KLGA", target, target)
    assert len(got) == 1
    row = got[0]
    assert row["runtime_utc"].startswith("2026-07-14T06:00:00")
    assert row["obs_valid_utc"].startswith("2026-07-14T08:00:00")
    assert row["obs_latest"] == 72
    assert row["obs_avail_utc"] <= row["freeze_utc"]
