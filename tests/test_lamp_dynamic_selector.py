import datetime as dt

import pandas as pd
import pytest

from scripts.lab_lamp_dynamic_selector import SPECS, choose_city, eligible_history


def history():
    rows = []
    start = dt.date(2026, 1, 1)
    for offset in range(10):
        rows.append({"d": start+dt.timedelta(days=offset),
            "hit_city": 1 if offset < 8 else 0, "hit_lamp": 0 if offset < 8 else 1,
            "ae_city": 1 if offset < 8 else 3, "ae_lamp": 3 if offset < 8 else 1})
    return pd.DataFrame(rows)


def test_selector_excludes_labels_newer_than_two_days():
    group = history()
    day = dt.date(2026, 1, 10)
    eligible = eligible_history(group, day, 30)
    assert eligible.d.max() == dt.date(2026, 1, 8)
    assert choose_city(group, day, SPECS["ACC30_M00"])


def test_selector_defaults_to_lamp_without_enough_history_and_rejects_bad_metric():
    group = history().iloc[:5]
    assert not choose_city(group, dt.date(2026, 1, 6), ("exact", 30, 0))
    with pytest.raises(ValueError):
        choose_city(history(), dt.date(2026, 1, 10), ("bad", 30, 0))
