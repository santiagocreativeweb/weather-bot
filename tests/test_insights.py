# tests/test_insights.py — unit tests OFFLINE de la capa nueva (wxbt_insights + telegram_bot).
# Sin red: todo con CSVs sinteticos en tmp_path (los paths de modulo se monkeypatchean).
import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import wxbt_insights as I  # noqa: E402


def test_parse_win_label_variants():
    assert I.parse_win_label("23°C") == (23, 23)
    assert I.parse_win_label("72-73°F") == (72, 73)
    assert I.parse_win_label(">= 64°F") == (64, None)
    assert I.parse_win_label("<= 10°C") == (None, 10)
    assert I.parse_win_label("12°C or higher") == (12, None)
    assert I.parse_win_label("30°C or below") == (None, 30)
    assert I.parse_win_label(None) == (None, None)


def test_bucket_label():
    assert I.bucket_label(88, 89, "F") == "88-89°F"
    assert I.bucket_label(23, 23, "C") == "23°C"
    assert I.bucket_label(None, 10, "C") == "≤10°C"
    assert I.bucket_label(30, None, "C") == "≥30°C"


def test_wilson_low_penalizes_small_n():
    # 2/2 NO debe ganarle a 6/7: con n chico la cota inferior cae
    assert I.wilson_low(2, 2) < I.wilson_low(6, 7)
    assert I.wilson_low(0, 0) == 0.0
    assert 0.0 <= I.wilson_low(5, 10) <= 0.5


def test_synthetic_buckets_alignment_f():
    # °F: ancho 2, grilla alineada al ganador (88,89); el pick de mu=85.3 (84-85) debe existir
    buckets = I._synthetic_buckets("F", 88, 89, 85.3)
    assert (88, 89) in buckets
    assert (84, 85) in buckets
    widths = {hi - lo for lo, hi in buckets}
    assert widths == {1}   # lo..hi inclusive con ancho 2 -> hi-lo = 1


def test_synthetic_buckets_alignment_c():
    buckets = I._synthetic_buckets("C", 31, 31, 29.7)
    assert (31, 31) in buckets and (29, 29) in buckets
    assert all(hi == lo for lo, hi in buckets)


def test_freeze_utc_matches_dashboard_rule():
    # 04:30 hora local: Milan (utc+1, DST verano +2) el 15/07 -> 02:30 UTC
    f = I.freeze_utc("LIMC", dt.date(2026, 7, 15))
    assert f == dt.datetime(2026, 7, 15, 2, 30)


def _write(p, text):
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def test_model_perf_tally(tmp_path, monkeypatch):
    """Un modelo capturado ANTES del freeze cuenta; uno capturado DESPUES no. El hit usa la regla
    FLOOR contra el bucket ganador oficial."""
    target = dt.date(2026, 7, 10)
    # freeze LIMC 2026-07-10 = 02:30 UTC. Captura valida 01:00Z, invalida 03:00Z.
    mf = tmp_path / "models_forward.csv"
    _write(mf, "capture_utc,station,target,model,unit,tmax\n"
               f"2026-07-10T01:00:00+00:00,LIMC,{target},icon,C,34.6\n"
               f"2026-07-10T01:00:00+00:00,LIMC,{target},gefs,C,36.2\n"
               f"2026-07-10T03:00:00+00:00,LIMC,{target},ukmo,C,34.0\n")
    bf = tmp_path / "backfill_check.csv"
    _write(bf, "station,target,lead,unit,mu_cal,sigma_cal,mu_raw,sigma_raw,max_real,win_mkt,"
               "hit_cal,hit_raw,pwin_cal,pwin_raw,crps_cal,crps_raw\n"
               f"LIMC,{target},2,C,34.1,1.1,33.9,1.5,34.4,34°C,1,1,0.4,0.3,0.3,0.4\n")
    monkeypatch.setattr(I, "MODELS_FWD", str(mf))
    monkeypatch.setattr(I, "BACKFILL", str(bf))
    monkeypatch.setattr(I, "GAMMA_LABELS", str(tmp_path / "none1.csv"))
    monkeypatch.setattr(I, "LAB_M8", str(tmp_path / "none2.csv"))
    monkeypatch.setattr(I, "WINNERS_CACHE", str(tmp_path / "wc.json"))
    perf = I.model_perf(days=None, today=dt.date(2026, 7, 15))
    by = {(r["model"], r["src"]): r for r in perf}
    assert ("icon", "vivo") in by and by[("icon", "vivo")]["hits"] == 1     # floor(34.6)=34 ∈ 34°C
    assert by[("gefs", "vivo")]["hits"] == 0                                # floor(36.2)=36 ∉ 34°C
    assert ("ukmo", "vivo") not in by                                       # captura post-freeze
    assert abs(by[("icon", "vivo")]["mae"] - 0.2) < 1e-9                    # |34.6-34.4|


def test_bot_history_scores_frozen_only(tmp_path, monkeypatch):
    """Solo filas con evidencia congelada entran al KPI; el nivel sale del ranking pick-first."""
    import json
    target = dt.date(2026, 7, 10)
    audit = {f"LIMC|{target}": {"hist": [["10/07 01:00", 34.1]], "frozen": True,
                                "froze": {"mu": 34.1, "sg": 1.1, "top": ["34°C"]}},
             f"EGLC|{target}": {"hist": [], "frozen": False}}   # sin freeze -> no scorea
    aj = tmp_path / "audit.json"
    _write(aj, json.dumps(audit))
    bf = tmp_path / "backfill_check.csv"
    _write(bf, "station,target,lead,unit,mu_cal,sigma_cal,mu_raw,sigma_raw,max_real,win_mkt,"
               "hit_cal,hit_raw,pwin_cal,pwin_raw,crps_cal,crps_raw\n"
               f"LIMC,{target},2,C,34.1,1.1,33.9,1.5,34.4,34°C,1,1,0.4,0.3,0.3,0.4\n"
               f"EGLC,{target},2,C,27.0,1.0,26.8,1.2,26.7,26°C,0,0,0.2,0.2,0.4,0.5\n")
    monkeypatch.setattr(I, "AUDIT_JSON", str(aj))
    monkeypatch.setattr(I, "BACKFILL", str(bf))
    monkeypatch.setattr(I, "GAMMA_LABELS", str(tmp_path / "none1.csv"))
    monkeypatch.setattr(I, "PREDS_FWD", str(tmp_path / "none2.csv"))
    monkeypatch.setattr(I, "WINNERS_CACHE", str(tmp_path / "wc.json"))
    rows = I.bot_history(today=dt.date(2026, 7, 15))
    assert len(rows) == 1 and rows[0]["station"] == "LIMC"
    assert rows[0]["nivel"] == "EXACTO" and rows[0]["pick_lbl"] == "34°C"


def test_stability_ranking(monkeypatch):
    hist = ([dict(station="KORD", nivel="EXACTO", mu=90, max_real=90.2, pwin=0.4)] * 6 +
            [dict(station="KORD", nivel="TOP-2", mu=88, max_real=89.0, pwin=0.3)] +
            [dict(station="RKSI", nivel="EXACTO", mu=30, max_real=30.1, pwin=0.5)] * 2)
    rows = I.stability(hist=hist)
    assert rows[0]["station"] == "KORD"          # 7/7 top-2 con n=7 le gana a 2/2
    assert rows[0]["top2"] == 7 and rows[1]["station"] == "RKSI"


def test_telegram_help_and_city_matching():
    import telegram_bot as T
    out, kb = T.handle("/help")               # v2: (texto, inline_keyboard)
    assert "/picks" in out and "/top" in out
    assert "/value" not in out                 # value bets eliminada (2026-07-16)
    assert kb and kb[0][0]["callback_data"] == "menu"
    assert T.find_station("milan") == "LIMC"
    assert T.find_station("sao paulo") == "SBGR"
    assert T.find_station("KLGA") == "KLGA"
    assert T.find_station("nyc") == "KLGA"
    assert T.find_station("hong kong") == "HKO"
    assert T.find_station("xyzzy") is None
    # callbacks: menu y rutas por ciudad
    text, kb2 = T.handle_callback("menu")
    assert kb2 and any(b["callback_data"].startswith("c|") for row in kb2 for b in row)


def test_pws_rank_prefers_stable_bias(tmp_path, monkeypatch):
    """rank_station: elige por std BAJA (bias estable), no por bias chico; descarta basura."""
    import pws_setup as P
    hist = {}
    d0 = dt.date(2026, 1, 1)
    obs = {}
    for k in range(30):
        ds = (d0 + dt.timedelta(days=k)).isoformat()
        obs[("LIMC", ds)] = 30.0
        hist[("LIMC", "STABLE_BIASED", ds)] = 32.0          # bias +2 exacto, std 0
        hist[("LIMC", "NOISY_CENTERED", ds)] = 30.0 + (3 if k % 2 else -3)   # bias 0, std 3
        hist[("LIMC", "BROKEN", ds)] = 55.0                  # basura (delta > 8C) -> afuera
    monkeypatch.setattr(P, "discover", lambda code, n_cand=10, force=False: [
        dict(pws_id=p, dist_km=2.0, lat=45.6, lon=8.7)
        for p in ("STABLE_BIASED", "NOISY_CENTERED", "BROKEN")])
    kept, stats = P.rank_station("LIMC", hist, obs, keep=5, min_n=10)
    ids = [s["pws_id"] for s in kept]
    assert ids[0] == "STABLE_BIASED"             # std 0 gana aunque bias sea +2
    assert "BROKEN" not in ids                   # descartada por outlier
    assert kept[0]["bias"] == pytest.approx(2.0)


def test_value_bets_offline_lost_filter(monkeypatch):
    """El screener excluye buckets ya imposibles por la obs viva (mismo criterio que playbook)."""
    import dashboard as D
    today = dt.date.today()
    d = today + dt.timedelta(days=1)             # mañana: el pico aun no paso (filtro post-pico)
    mk = {"LIMC": {d: {"buckets": [("30°C", 30, 30, 0.02), ("31°C", 31, 31, 0.05),
                                   ("32°C", 32, 32, 0.60), ("33°C", 33, 33, 0.30)],
                       "close_utc": None, "winner": None, "closed": False}}}
    preds = {("LIMC", d): (30.4, 1.0)}           # el bot quiere 30°C...
    live = {("LIMC", d): {"max": 32.0}}          # ...pero la obs viva ya esta en 32
    monkeypatch.setattr(D, "state_of", lambda code, dd, info, now: ("encurso", "EN CURSO"))
    monkeypatch.setattr(I, "_load_audit", lambda: {})
    rows = I.value_bets(today=today, horizon=1, mk=mk, preds=preds, live=live)
    lim = [r for r in rows if r["station"] == "LIMC"]
    assert lim, "el mercado deberia evaluarse"
    assert lim[0]["t1"] in ("32°C", "33°C")      # 30/31 muertos: no pueden ser top-1
