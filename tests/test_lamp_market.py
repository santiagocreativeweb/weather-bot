import pytest

from scripts.lab_lamp_market import pool_rank


def test_probability_pool_respects_market_and_lamp_endpoints():
    buckets = [(None, 79), (80, 81), (82, None)]
    mids = [.1, .8, .1]
    assert pool_rank(buckets, mids, 83, 1, 0, "LIN")[0] == 1
    assert pool_rank(buckets, mids, 83, 1, 1, "LIN")[0] == 2


def test_log_pool_is_deterministic_and_rejects_unknown_mode():
    buckets = [(None, 79), (80, 81), (82, None)]
    first = pool_rank(buckets, [.2, .6, .2], 82, 1.5, .5, "LOG")
    second = pool_rank(buckets, [.2, .6, .2], 82, 1.5, .5, "LOG")
    assert first == second
    assert sorted(first) == [0, 1, 2]
    with pytest.raises(ValueError):
        pool_rank(buckets, [.2, .6, .2], 82, 1.5, .5, "BAD")
