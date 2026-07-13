import datetime as dt

from scripts import playbook


def test_cityx_shadow_uses_current_version_and_pre_freeze_rows(tmp_path, monkeypatch):
    path = tmp_path / "exact.csv"
    path.write_text(
        "capture_utc,station,target,version,recipe,unit,mu,freeze_utc\n"
        "2026-07-13T01:00:00+00:00,KORD,2026-07-14,CITYX1-20260713,OLD,F,90,2026-07-14T09:30:00\n"
        "2026-07-13T02:00:00+00:00,KORD,2026-07-14,CITYX2-20260713,A,F,94,2026-07-14T09:30:00\n"
        "2026-07-13T03:00:00+00:00,KORD,2026-07-14,CITYX2-20260713,B,F,95,2026-07-14T09:30:00\n"
        "2026-07-14T10:00:00+00:00,KORD,2026-07-14,CITYX2-20260713,LATE,F,99,2026-07-14T09:30:00\n",
        encoding="utf-8")
    monkeypatch.setattr(playbook.D, "freeze_utc", lambda station, target:
                        dt.datetime(2026, 7, 14, 9, 30))
    got = playbook.load_cityx_shadow(str(path))
    assert got[("KORD", dt.date(2026, 7, 14))]["mu"] == 95
    assert got[("KORD", dt.date(2026, 7, 14))]["recipe"] == "B"
