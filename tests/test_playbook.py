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


def test_cityx_confidence_uses_latest_current_version(tmp_path):
    path = tmp_path / "confidence.csv"
    path.write_text(
        "capture_utc,station,target,version,selected,spread_buckets\n"
        "2026-07-13T01:00:00+00:00,KORD,2026-07-14,OLD,1,0.5\n"
        "2026-07-13T02:00:00+00:00,KORD,2026-07-14,CITYCONF1-20260713,1,0.9\n"
        "2026-07-13T03:00:00+00:00,KORD,2026-07-14,CITYCONF1-20260713,0,1.2\n",
        encoding="utf-8")
    got = playbook.load_cityx_confidence(str(path))
    assert got[("KORD", dt.date(2026, 7, 14))]["selected"] == 0
    assert got[("KORD", dt.date(2026, 7, 14))]["spread"] == 1.2


def test_lamp_shadow_rejects_late_sources_and_keeps_latest_valid_row(tmp_path):
    path = tmp_path / "lamp.csv"
    header = ("capture_utc,station,target,version,now_version,freeze_utc,lav_avail_utc,"
              "obs_avail_utc,mu_lampx,mu_nowx,innovation\n")
    path.write_text(header +
        "2026-07-14T14:00:00Z,KORD,2026-07-14,LAMPX1-20260713,LAMPNOW1-20260713,"
        "2026-07-14T09:30:00Z,2026-07-14T09:00:00Z,2026-07-14T09:15:00Z,90,90.5,2\n" +
        "2026-07-14T15:00:00Z,KORD,2026-07-14,LAMPX1-20260713,LAMPNOW1-20260713,"
        "2026-07-14T09:30:00Z,2026-07-14T09:00:00Z,2026-07-14T09:45:00Z,99,99,0\n",
        encoding="utf-8")
    got = playbook.load_lamp_shadow(str(path))
    row = got[("KORD", dt.date(2026, 7, 14))]
    assert row["mu_lampx"] == 90
    assert row["mu_nowx"] == 90.5
