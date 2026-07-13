import datetime as dt

from wxbt import observations
from wxbt.observations import fetch_iem_maxima, parse_asos_daily_max, parse_iem_daily_max


def test_asos_max_groups_by_local_date_and_ignores_missing():
    text = ("station,valid,tmpf\n"
            "LGA,2026-07-01 23:51,80.0\n"
            "LGA,2026-07-01 15:51,84.0\n"
            "LGA,2026-07-02 00:51,M\n"
            "LGA,2026-07-02 14:51,82.0\n")
    assert parse_asos_daily_max(text) == {
        dt.date(2026, 7, 1): 84.0, dt.date(2026, 7, 2): 82.0}


def test_daily_celsius_conversion():
    text = "station,day,max_temp_f\nEGLC,2026-07-01,68.0\n"
    got = parse_iem_daily_max(text, "C")
    assert got[dt.date(2026, 7, 1)] == 20.0


def test_fahrenheit_fetch_uses_hourly_local_time_and_exclusive_end(monkeypatch):
    seen = {}

    class Response:
        text = "station,valid,tmpf\nLGA,2026-07-01 15:51,84.0\n"

        @staticmethod
        def raise_for_status():
            return None

    def fake_get(url, **kwargs):
        seen.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr(observations.requests, "get", fake_get)
    day = dt.date(2026, 7, 1)
    assert fetch_iem_maxima("KLGA", "NY_ASOS", day, day, "F") == {day: 84.0}
    assert seen["url"] == observations.ASOS_URL
    assert seen["params"]["station"] == "LGA"
    assert seen["params"]["tz"] == "America/New_York"
    assert (seen["params"]["year2"], seen["params"]["month2"], seen["params"]["day2"]) == (2026, 7, 2)
