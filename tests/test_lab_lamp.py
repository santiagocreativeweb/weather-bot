import pandas as pd

from scripts.lab_lamp import rank


def test_lamp_rank_excludes_cityx_control():
    frame = pd.DataFrame([
        {"candidate": candidate, "hit": hit, "top2": hit, "ae": 1.0}
        for candidate, hit in [("CITYX2", 1), ("LAV|RAW", 0), ("LAV|RAW", 1)]
    ])
    assert list(rank(frame).candidate) == ["LAV|RAW"]
