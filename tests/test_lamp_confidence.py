import pandas as pd

from scripts.lab_lamp_confidence import GATES, summaries


def test_lamp_confidence_gates_are_prediction_only():
    frame = pd.DataFrame([
        {"same": 1, "distance": 0, "spread": 1.0, "hit": 1, "top2": 1},
        {"same": 0, "distance": 1, "spread": 1.2, "hit": 0, "top2": 1},
    ])
    assert list(GATES["SAME_SPREAD11"](frame)) == [True, False]
    assert list(GATES["DIST1"](frame)) == [True, True]
    got = summaries(frame).set_index("gate")
    assert got.loc["SAME", "coverage"] == .5
