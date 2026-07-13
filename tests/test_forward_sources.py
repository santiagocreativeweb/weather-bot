import datetime as dt

import pandas as pd

from scripts import score_model_shadows as shadows
from scripts import validate_sources as sources


def test_local_source_discards_updates_after_freeze(tmp_path, monkeypatch):
    path = tmp_path / "source.csv"
    path.write_text(
        "sent_utc,station,target,tmax_c\n"
        "2026-07-11T19:00:00+00:00,RCSS,2026-07-12,31\n"
        "2026-07-11T21:00:00+00:00,RCSS,2026-07-12,35\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sources, "D", str(tmp_path))
    monkeypatch.setattr(sources, "freeze_utc", lambda station, target: dt.datetime(2026, 7, 11, 20, 30))
    got = sources.latest_src_before_freeze("source.csv", "RCSS", "tmax_c", "sent_utc")
    assert got[dt.date(2026, 7, 12)] == 31.0


def test_med8_bias_uses_only_prior_60_days():
    target = dt.date(2026, 7, 12)
    rows = [
        ("RJTT", target - dt.timedelta(days=i), float(i))
        for i in range(1, 16)
    ]
    rows += [("RJTT", target - dt.timedelta(days=61), 999.0), ("RJTT", target, 999.0)]
    errors = pd.DataFrame(rows, columns=["station", "target", "error"])
    assert shadows.bias_before(errors, "RJTT", target) == 8.0
