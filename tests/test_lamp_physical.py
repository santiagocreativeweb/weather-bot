import datetime as dt

import pandas as pd

from scripts import backfill_lamp_features as physical
from scripts.lab_lamp_physical import factories


def test_physical_features_use_only_the_last_available_lav_run(monkeypatch):
    target = dt.date(2026, 7, 14)
    monkeypatch.setattr(physical, "freeze_utc", lambda station, day:
                        dt.datetime(2026, 7, 14, 8, 30))
    monkeypatch.setattr(physical, "local_offset", lambda station, day: -4)
    rows = []
    for runtime, base in [("2026-07-14T06:00:00Z", 70),
                          ("2026-07-14T07:00:00Z", 99)]:
        for utc_hour in range(12, 27):
            rows.append({
                "runtime": pd.Timestamp(runtime),
                "ftime": pd.Timestamp("2026-07-14T00:00:00Z") +
                         pd.Timedelta(hours=utc_hour),
                "tmp": base+(utc_hour-12), "dpt": 60, "cld": "BK",
                "wdr": 180, "wsp": 10, "p01": 20, "p06": 30,
                "cig": 5, "vis": 7,
            })
    got = physical.select_features(pd.DataFrame(rows), "KLGA", target, target)
    assert len(got) == 1
    row = got[0]
    assert row["runtime_utc"].startswith("2026-07-14T06:00:00")
    assert row["avail_utc"] <= row["freeze_utc"]
    assert row["n_hours"] == 15
    assert row["tmax"] == 84
    assert row["peak_hour_local"] == 22
    assert row["cloud_broken_fraction"] == 1


def test_physical_candidate_family_is_frozen_and_small():
    assert set(factories()) == {"RIDGE10", "HGBR7", "RFR3", "ETR3", "HGBC7", "RFC3"}
