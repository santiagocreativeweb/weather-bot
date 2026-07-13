import datetime as dt

from scripts import capture_market_consensus as capture
from wxbt.market_consensus import rank_consensus


def test_probability_pool_can_follow_either_independent_signal():
    buckets = [(20, 20), (21, 21), (22, 22)]
    mids = [.1, .8, .1]
    assert rank_consensus(buckets, mids, mu=20.2, sigma=.6, bot_weight=0)[0] == 1
    assert rank_consensus(buckets, mids, mu=20.2, sigma=.6, bot_weight=1)[0] == 0


def test_price_history_never_uses_point_after_cutoff(monkeypatch):
    cutoff = dt.datetime(2026, 7, 14, 6, 30)
    before = int((cutoff-dt.timedelta(minutes=5)).replace(tzinfo=dt.timezone.utc).timestamp())
    after = int((cutoff+dt.timedelta(minutes=1)).replace(tzinfo=dt.timezone.utc).timestamp())

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"history": [{"t": before, "p": .42}, {"t": after, "p": .99}]}

    monkeypatch.setattr(capture.requests, "get", lambda *args, **kwargs: Response())
    price, stamp = capture.token_price_before("token", cutoff)
    assert price == .42
    assert stamp < cutoff
