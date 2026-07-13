# tests/test_core.py — Cada test tiene un claim falsable (FASE 4).
import numpy as np
import pandas as pd
import pytest
from wxbt import config as C
from wxbt.market import bucket_prob, taker_fee_per_share, resolve_bucket
from wxbt.calibration import crps_normal
from wxbt.synth import gen_world
from wxbt.engine import run_backtest, metrics, latest_per_model, _index_forecasts
from wxbt.checks import validate_world


def test_fee_formula_punto_conocido():
    # Claim: 100 shares @ $0.50 con rate 0.05 -> fee total $1.25 (FASE 1, verificar vivo)
    assert abs(100 * taker_fee_per_share(0.50, 0.05) - 1.25) < 1e-9
    # simetría p / 1-p
    assert abs(taker_fee_per_share(0.3) - taker_fee_per_share(0.7)) < 1e-12


def test_bucket_probs_particionan_a_1():
    # Claim: colas abiertas + buckets cerrados contiguos particionan la recta -> suma 1
    mu, sd = 61.3, 2.7
    edges = [54, 56, 58, 60, 62, 64, 66]
    probs = [bucket_prob(mu, sd, None, edges[0] - 1)]
    for lo in edges[:-1]:
        probs.append(bucket_prob(mu, sd, lo, lo + 1))
    probs.append(bucket_prob(mu, sd, edges[-1], None))
    assert abs(sum(probs) - 1.0) < 1e-9
    # redondeo entero: T=59.4 -> 59 cae en bucket 58-59, no en 60-61
    assert resolve_bucket(int(round(59.4)), 58, 59) and not resolve_bucket(int(round(59.4)), 60, 61)


def test_crps_sano():
    # Claim: CRPS menor cuando mu acierta; ~0.23*sigma en el óptimo
    assert crps_normal(10, 10, 1) < crps_normal(10, 13, 1)
    assert abs(crps_normal(0, 0, 1) - (np.sqrt(2 / np.pi) - 1 / np.sqrt(np.pi))) < 1e-9


def test_tripwire_anti_lookahead():
    # Claim: envenenar forecasts FUTUROS (+100°) no cambia lo que el motor lee a tiempo t
    w = gen_world(n_days=90, seed=7, regime="inefficient")
    fc = w["forecasts"]
    t = pd.Timestamp("2026-02-15 10:00")
    key_rows = fc[(fc.station == "KLGA")]
    tgt = sorted(key_rows.target.unique())[50]
    before = latest_per_model(_index_forecasts(fc), "KLGA", tgt, t)
    poison = fc.copy()
    poison.loc[poison.avail > t, "m"] += 100.0
    after = latest_per_model(_index_forecasts(poison), "KLGA", tgt, t)
    assert before == after, "el motor leyó un forecast aún no disponible"


def test_validate_world_limpio():
    w = gen_world(n_days=80, seed=3)
    assert validate_world(w) == []


@pytest.mark.slow
def test_null_market_sin_ganancia_fantasma():
    # Claim CRÍTICO: con precios EFICIENTES, el motor no puede ganar neto de costos.
    # Si gana -> hay fuga (look-ahead o contabilidad rota).
    rois_mid, rois_tak, n_tr = [], [], 0
    for seed in (1, 2, 3):
        w = gen_world(n_days=150, seed=seed, regime="efficient")
        for mode, bag in (("mid", rois_mid), ("taker", rois_tak)):
            r = run_backtest(w, mode=mode)
            m = metrics(r)
            bag.append(m["roi"]); n_tr += m["n_trades"]
    mean_mid, se_mid = np.mean(rois_mid), np.std(rois_mid) / np.sqrt(len(rois_mid))
    assert mean_mid - 2 * se_mid <= 0.01, f"ROI fantasma en mercado eficiente: {rois_mid}"
    assert np.mean(rois_tak) <= 0.01, f"taker gana en mercado eficiente: {rois_tak}"


@pytest.mark.slow
def test_calibracion_mejora_y_encuentra_edge_sintetico():
    # Claim: en el régimen ineficiente sintético, (a) EMOS baja CRPS y Brier vs crudo,
    # (b) el motor calibrado encuentra EV>0 al mid (pre-costos de spread).
    w = gen_world(n_days=240, seed=42, regime="inefficient")
    r = run_backtest(w, mode="mid", use_calibration=True)
    m = metrics(r)
    assert r["crps"]["cal"] < r["crps"]["raw"], (r["crps"])
    assert r["brier_buckets"]["cal"] < r["brier_buckets"]["raw"], (r["brier_buckets"])
    assert m["n_trades"] > 50, "muestra de trades demasiado chica para concluir"
    assert m["pnl_total"] > 0, f"sin edge ni siquiera en el mundo sintético sesgado: {m}"
