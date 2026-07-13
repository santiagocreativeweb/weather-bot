import datetime as dt

import pandas as pd

from scripts import lab_physical_mos


def test_apply_metar_truth_overrides_matching_fahrenheit_rows(tmp_path, monkeypatch):
    pd.DataFrame([
        {"candidate": "raw_tmpf", "station": "KLGA", "target": "2026-07-01", "value": 84.0},
        {"candidate": "metar_body_floor", "station": "KLGA", "target": "2026-07-01", "value": 82.0},
    ]).to_csv(tmp_path / "lab_metar_precision.csv", index=False)
    monkeypatch.setattr(lab_physical_mos, "D", str(tmp_path))
    truth = pd.DataFrame([
        {"station": "KLGA", "d": dt.date(2026, 7, 1), "max_real": 85.0},
        {"station": "EGLC", "d": dt.date(2026, 7, 1), "max_real": 20.0},
    ])
    got = lab_physical_mos.apply_metar_truth(truth)
    assert got.loc[got.station == "KLGA", "max_real"].iloc[0] == 84.0
    assert got.loc[got.station == "EGLC", "max_real"].iloc[0] == 20.0
