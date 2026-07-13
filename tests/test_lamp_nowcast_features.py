import pandas as pd

from scripts.lab_lamp_nowcast_features import factories, rule_predictions


def test_expanded_nowcast_rules_are_causal_and_deterministic():
    data = pd.DataFrame([
        {"mu_lamp": 80, "mu_now25": 80.5, "innovation": 2,
         "obs_trend_fph": 1, "hours_to_lav_peak": 8, "obs_max": 82},
        {"mu_lamp": 80, "mu_now25": 79.5, "innovation": -2,
         "obs_trend_fph": 1, "hours_to_lav_peak": 2, "obs_max": 79},
    ])
    got = rule_predictions(data)
    assert got["BASE_NOW25"].tolist() == [80.5, 79.5]
    assert got["OBSFLOOR"].tolist() == [82, 79.5]
    assert got["TREND_DYN"].tolist() == [81, 79.5]
    assert got["PEAK_DYN"].tolist() == [81, 79.5]
    assert got["FLOOR_PEAK"].tolist() == [82, 79.5]


def test_expanded_nowcast_model_family_is_frozen_and_small():
    assert set(factories()) == {"RIDGE10", "HGBR7", "RFR3", "RFC3"}
