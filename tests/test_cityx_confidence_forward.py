import pandas as pd

from scripts.accumulate_cityx_confidence import build_rows
from scripts.score_model_shadows import confidence_bootstrap
from wxbt.cityx_confidence import VERSION


def test_confidence_capture_uses_same_coherent_timestamp():
    exact = pd.DataFrame([dict(capture_utc="2026-07-13T03:00:00+00:00", station="KORD",
        target="2026-07-14", version="CITYX2-20260713", unit="F", mu=84.2,
        freeze_utc="2026-07-14T09:30:00")])
    models = pd.DataFrame([
        dict(capture_utc="2026-07-13T03:00:00+00:00", station="KORD", target="2026-07-14",
             model=model, tmax=value)
        for model, value in [("gfs13", 84), ("ecmwf", 85), ("icon", 84)]
    ] + [dict(capture_utc="2026-07-13T02:00:00+00:00", station="KORD",
              target="2026-07-14", model="aifs", tmax=100)])
    rows = build_rows(exact, models)
    assert len(rows) == 1
    assert rows[0]["version"] == VERSION
    assert rows[0]["n_models"] == 3
    assert rows[0]["selected"] == 1


def test_confidence_bootstrap_detects_better_selected_subset():
    all_city = pd.DataFrame({"target": ["d1", "d1", "d2", "d2"],
                             "hit_cityx": [1, 0, 1, 0]})
    selected = all_city.iloc[[0, 2]]
    p, interval = confidence_bootstrap(selected, all_city, reps=500)
    assert p == 0.0
    assert interval[0] > 0
