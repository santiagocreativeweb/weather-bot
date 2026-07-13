import datetime as dt

from wxbt.forward_scoring import frozen_forecast


TARGET = dt.date(2026, 7, 12)


def test_immutable_freeze_wins_over_fresher_forward_snapshot():
    audit = {"KLGA|2026-07-12": {"frozen": True, "froze": {"mu": 85.74, "sg": 1.81}}}
    assert frozen_forecast(audit, "KLGA", TARGET, 88.0, 0.9) == (85.74, 1.81, "frozen")


def test_legacy_audit_uses_last_pre_deadline_mean():
    audit = {"KLGA|2026-07-12": {"frozen": True, "hist": [["a", 84.2], ["b", 85.1]]}}
    assert frozen_forecast(audit, "KLGA", TARGET, 88.0, 1.3) == (85.1, 1.3, "legacy-audit")


def test_forward_snapshot_is_explicit_last_resort():
    assert frozen_forecast({}, "KLGA", TARGET, 88.0, 1.3) == (88.0, 1.3, "forward-fallback")
