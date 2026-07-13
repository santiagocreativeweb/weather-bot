#!/usr/bin/env python3
# scripts/lab_wu_ground_truth.py -- AUDITORIA WU-vs-IEM estacion por estacion (pregunta Santiago
# 2026-07-13: "vos lo estas comparando con las estaciones de WU? tendrias que sacar datos historicos
# de estas 12 estaciones").
#
# QUE MIDE, y por que NO hace falta scrapear WU:
#   El mercado RESUELVE con Weather Underground. NO tenemos WU crudo (su API murio en 2018), PERO
#   la RESOLUCION de WU esta en Gamma: win_mkt = el bucket que Polymarket pago = lo que WU dijo.
#   backfill_check.csv tiene, lado a lado, win_mkt (WU via Gamma) y max_real (IEM, sobre lo que
#   CALIBRAMOS). Esta es la comparacion honesta: la verdad-de-pago (WU) contra nuestra verdad-de-
#   entrenamiento (IEM), estacion por estacion.
#
# DOS COSAS:
#   1) DIAGNOSTICO: por estacion, % de acuerdo (floor(IEM) cae en el bucket WU) y el OFFSET
#      sistematico delta = mediana(repr(WU) - floor(IEM)) en grados. Si delta != 0 estable, el bot
#      predice IEM pero el mercado paga WU -> sesgo corregible que NINGUN lab anterior toco.
#   2) TEST walk-forward: pick_WU = floor(mu - delta_rolling(station, <d)). Comparar hit_mkt (vs la
#      resolucion REAL) de pick normal vs pick corregido-a-WU. Bootstrap por dia. Si gana con
#      estructura -> sombra H5 pre-registrada.
#
# CAVEAT: delta se estima de dias con mercado resuelto (win_mkt presente); la resolucion Gamma es
# el ground-truth de pago. Solo se corrige el PICK (para el % de mercado), no el mu de calibracion.
import os
import sys
import math
import datetime as dt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from lab_v7 import parse_win, pred_bucket_floor, hit_mkt_floor, CONT   # noqa: E402
from show_live import STATIONS                                         # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "data")
SHADOW0 = dt.date(2026, 7, 13)
NBOOT = 10000


def win_repr(wb, unit):
    """Valor representativo (grados) del bucket ganador WU: centro si cerrado, borde si cola."""
    lo, hi = wb
    step = 2 if unit == "F" else 1
    if lo is None:
        return hi - (step - 1) / 2.0
    if hi is None:
        return lo + (step - 1) / 2.0
    return (lo + hi) / 2.0


def bucket_displacement(value, wb):
    """Smallest integer shift needed for floor(value) to enter the paid bucket."""
    observed = int(math.floor(value))
    lo, hi = wb
    if lo is None:
        return 0.0 if observed <= hi else float(hi-observed)
    if hi is None:
        return 0.0 if observed >= lo else float(lo-observed)
    if observed < lo:
        return float(lo-observed)
    if observed > hi:
        return float(hi-observed)
    return 0.0


def main():
    bk = pd.read_csv(f"{D}/backfill_check.csv")
    bk["target"] = pd.to_datetime(bk["target"]).dt.date
    bk = bk[(bk.lead == 2) & bk.win_mkt.notna() & bk.max_real.notna()].copy()
    precision_path = f"{D}/lab_metar_precision.csv"
    if os.path.exists(precision_path):
        precision = pd.read_csv(precision_path)
        precision = precision[precision.candidate == "raw_tmpf"]
        precision["target"] = pd.to_datetime(precision.target).dt.date
        precision = precision[["station", "target", "value"]].drop_duplicates(
            ["station", "target"])
        bk = bk.merge(precision, on=["station", "target"], how="left")
        bk["max_real"] = bk.value.fillna(bk.max_real)
    print(f"backfill lead2 con WU(win_mkt)+truth(max_real; METAR hourly override °F): {len(bk)} filas, "
          f"{bk.target.min()}..{bk.target.max()}\n")

    rows = []
    for r in bk.itertuples():
        unit = STATIONS[r.station][3]
        wb = parse_win(r.win_mkt)
        if wb is None:
            continue
        iem_floor = int(math.floor(r.max_real))
        ib = pred_bucket_floor(r.max_real, unit)                 # bucket IEM (floor)
        # acuerdo: el bucket IEM coincide con el ganador WU (con colas)
        if wb[1] is None:
            agree = ib[0] >= wb[0]
        elif wb[0] is None:
            agree = ib[1] <= wb[1]
        else:
            agree = ib == wb
        # Do not subtract the centre of a 2°F bucket: that manufactures a -0.5°F
        # "bias" even on exact agreement. Delta is zero whenever truth is in-bucket.
        delta = bucket_displacement(r.max_real, wb)
        rows.append(dict(st=r.station, unit=unit, d=r.target, mu=r.mu_cal,
                         agree=int(agree), delta=delta, win=r.win_mkt))
    df = pd.DataFrame(rows)

    print("=== 1) DIAGNOSTICO por estacion (WU via Gamma vs truth compatible) ===")
    print(f"{'st':6}{'unit':5}{'n':>4}  {'acuerdo':>8}  {'delta_med(WU-IEM)':>18}  {'delta_std':>10}")
    diag = {}
    for st in sorted(df.st.unique()):
        g = df[df.st == st]
        dm = g.delta.median()
        diag[st] = dm
        print(f"{st:6}{g.unit.iloc[0]:5}{len(g):>4}  {g.agree.mean():>7.0%}  "
              f"{dm:>+17.2f}  {g.delta.std():>10.2f}")
    print("\nlectura: acuerdo alto (>90%) => IEM==WU, no hay nada que corregir. delta!=0 estable =>")
    print("WU corre sistematicamente por debajo/encima de IEM (el bot calibra IEM, el mercado paga WU).")

    # === 2) TEST walk-forward: corregir el pick con el delta rolling por estacion ===
    print("\n=== 2) TEST: pick corregido a WU = floor(mu - delta_rolling) vs pick normal ===")
    df = df.sort_values(["st", "d"]).reset_index(drop=True)
    hist = {}
    res = []
    for r in df.itertuples():
        unit = STATIONS[r.st][3]
        wb = parse_win(r.win)
        past = [h for (dd, h) in hist.get(r.st, []) if dd < r.d]
        delta_roll = float(np.median(past)) if len(past) >= 15 else 0.0
        hit_norm = hit_mkt_floor(r.mu, unit, wb)
        hit_wu = hit_mkt_floor(r.mu + delta_roll, unit, wb)     # mu + (WU-IEM): mover mu hacia WU
        res.append(dict(st=r.st, d=r.d, hit_norm=hit_norm, hit_wu=hit_wu, delta_roll=delta_roll))
        hist.setdefault(r.st, []).append((r.d, r.delta))
    R = pd.DataFrame(res)
    # bootstrap por dia del delta hit_mkt global
    rng = np.random.default_rng(20260713)

    def boot(sub):
        days = sorted(sub.d.unique())
        per = {dd: (g.hit_wu.sum() - g.hit_norm.sum(), len(g)) for dd, g in sub.groupby("d")}
        s = np.array([per[dd][0] for dd in days], float)
        n = np.array([per[dd][1] for dd in days], float)
        idx = rng.integers(0, len(days), size=(NBOOT, len(days)))
        reps = s[idx].sum(1) / np.maximum(n[idx].sum(1), 1)
        return float(sub.hit_wu.mean() - sub.hit_norm.mean()), float((reps <= 0).mean())

    dg, pg = boot(R)
    print(f"GLOBAL hit_mkt: normal {R.hit_norm.mean():.1%} -> WU-corregido {R.hit_wu.mean():.1%}  "
          f"delta {100*dg:+.2f}pp  p_dia={pg:.3f}  (n={len(R)})")
    print(f"\n{'st':6}{'n':>4}  {'hit_norm':>9}  {'hit_wu':>8}  {'delta':>7}  {'delta_roll_final':>16}")
    for st in sorted(R.st.unique()):
        g = R[R.st == st]
        dr = g.delta_roll.iloc[-1] if len(g) else 0.0
        print(f"{st:6}{len(g):>4}  {g.hit_norm.mean():>8.0%}  {g.hit_wu.mean():>7.0%}  "
              f"{100*(g.hit_wu.mean()-g.hit_norm.mean()):>+6.0f}  {dr:>+16.2f}")

    # === SOMBRA H5 (pre-registrada): pick WU-corregido en las estaciones con |delta|>=0.5 ===
    strong_delta = [st for st, dm in diag.items() if abs(dm) >= 0.5]
    print(f"\n--- SOMBRA H5 (correccion WU) -- estaciones con |delta|>=0.5: {strong_delta} ---")
    print(f"    regla pre-registrada (fijada 2026-07-13): en ESAS estaciones, pick_WU vs pick_normal,")
    print(f"    targets >= {SHADOW0}, n>=45 dias, adoptar si delta hit_mkt>0 y p_dia<0.05. UNA mirada.")
    sh = R[(R.d >= SHADOW0) & (R.st.isin(strong_delta))]
    if sh.empty:
        print("    sin datos aun (0 dias).")
    else:
        d5, p5 = boot(sh)
        nd = sh.d.nunique()
        print(f"    delta {100*d5:+.2f}pp p_dia={p5:.3f} ({nd}/45 dias)"
              + ("  >>> " + ("ADOPTAR" if d5 > 0 and p5 < 0.05 else "NO adoptar") if nd >= 45 else ""))

    R.to_csv(f"{D}/lab_wu_ground_truth.csv", index=False)
    print(f"\ndetalle -> {os.path.relpath(D)}/lab_wu_ground_truth.csv")
    print("NOTA: H5 queda vacía: no hay sesgo de fuente estable que aplicar al pick.")


if __name__ == "__main__":
    main()
