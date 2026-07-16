#!/usr/bin/env python3
# scripts/lab_v6.py — EXPERIMENTO V6: ¿un mixture de 8 modelos mejora el acierto exacto vs los
# 3 actuales? Comparacion de CONSENSOS SIMPLES (sin EMOS) — decide si vale integrar los 5 modelos
# nuevos al pipeline completo. Extiende el lab SIN tocar calib_lab.py. [Creado 2026-07-10.]
#
#   A_3mod = media(gefs, ecmwf, icon)                − bias_rolling_60d(A)
#   B_8mod = media(los 3 + meteofrance/gem/ukmo/jma/knmi) − bias_rolling_60d(B)
#
# Datos: m point-in-time via Previous-Runs API, columnas temperature_2m_previous_day1/2
# EXCLUSIVAMENTE (temperature_2m es nowcast anclado al valid time = bug #5 — prohibido acá).
# Cache: data/lab_m_extra.csv, mismo formato que data/lab_m.csv (station,target,model,lead,m).
# Anti-look-ahead: el bias de cada estrategia solo mira dias ANTERIORES al target (<60d) usando
# las predicciones crudas de ESA estrategia; real = obs.csv (tmax float) + backfill_check
# max_real para julio (obs.csv corta 07-01). La frescura no declarada de previous_day1 (~14-17h,
# bug #5 residual) es COMMON-MODE entre A y B — no invalida la comparacion relativa.
# Scoring (eval 2026-05-10..2026-07-08, lead 2): hit exacto vs win_mkt por regla FLOOR
# (floor(mu); en F el bucket es par-impar: lo=floor si es par, sino floor-1) y MAE vs real.
# A y B se scorean sobre el MISMO set de (station,target) — donde los 8 modelos existen.
import math
import os
import re
import sys
import time
import datetime as dt

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(__file__))
from show_live import STATIONS, PREV_RUNS, daily_tmax

D = os.path.join(os.path.dirname(__file__), "..", "data")
LAB_M = os.path.join(D, "lab_m.csv")
LAB_M_EXTRA = os.path.join(D, "lab_m_extra.csv")
DETAIL = os.path.join(D, "lab_v6_detail.csv")

BASE = ["gefs", "ecmwf", "icon"]                       # ya en lab_m.csv (calib_lab.build_m)
EXTRA_OM = {"meteofrance": "meteofrance_seamless", "gem": "gem_seamless",
            "ukmo": "ukmo_seamless", "jma": "jma_seamless", "knmi": "knmi_seamless"}
ALL8 = BASE + list(EXTRA_OM)
LEAD_COL = {2: "temperature_2m_previous_day1", 3: "temperature_2m_previous_day2"}  # SIN temperature_2m
D0_WARM, D0_EVAL, D1 = dt.date(2026, 4, 9), dt.date(2026, 5, 10), dt.date(2026, 7, 8)
BIAS_WIN, BIAS_MIN_N = 60, 10                          # = rolling_bias de calib_lab (V2 adoptada)


def build_m_extra():
    """m point-in-time de los 5 modelos nuevos, leads 2/3, 12 estaciones. Cachea en lab_m_extra.csv."""
    if os.path.exists(LAB_M_EXTRA):
        m = pd.read_csv(LAB_M_EXTRA, parse_dates=["target"]); m["target"] = m["target"].dt.date
        return m
    rows = []
    for code, (lat, lon, off, unit) in STATIONS.items():
        for model, om in EXTRA_OM.items():
            p = dict(latitude=lat, longitude=lon, models=om, hourly=",".join(LEAD_COL.values()),
                     start_date=(D0_WARM - dt.timedelta(days=1)).isoformat(),
                     end_date=(D1 + dt.timedelta(days=1)).isoformat(), timezone="UTC",
                     temperature_unit=("fahrenheit" if unit == "F" else "celsius"))
            h = None
            for attempt in (1, 2, 3):
                try:
                    r = requests.get(PREV_RUNS, params=p, timeout=90); r.raise_for_status()
                    h = r.json()["hourly"]; break
                except Exception as e:
                    print(f"[WARN] {code} {model} intento {attempt}: {e}", file=sys.stderr)
                    time.sleep(3 * attempt)
            if h is None:
                continue
            n0 = len(rows)
            for lead, col in LEAD_COL.items():
                if col not in h or h[col] is None:
                    print(f"[WARN] {code} {model}: sin columna {col}", file=sys.stderr); continue
                for d, mval in daily_tmax(h["time"], h[col], off).items():
                    if D0_WARM <= d <= D1:
                        rows.append([code, d.isoformat(), model, lead, round(mval, 2)])
            if len(rows) == n0:
                print(f"[WARN] {code} {model}: 0 dias utiles", file=sys.stderr)
            time.sleep(0.3)
    if not rows:
        sys.exit("[ERROR] la API no devolvio NADA para los 5 modelos nuevos — no escribo cache vacio (patron bug #3).")
    m = pd.DataFrame(rows, columns=["station", "target", "model", "lead", "m"])
    m.to_csv(LAB_M_EXTRA, index=False)
    m["target"] = pd.to_datetime(m["target"]).dt.date
    return m


def parse_win(w):
    """= calib_lab.parse_win: '72-73°F' -> (72,73); '15°C' -> (15,15); '>= 34°C' -> (34,None)."""
    nums = [int(x) for x in re.findall(r"\d+", str(w))]
    if not nums:
        return None
    if "higher" in str(w) or ">=" in str(w):
        return (nums[0], None)
    if "below" in str(w) or "<=" in str(w):
        return (None, nums[0])
    return (nums[0], nums[1]) if len(nums) >= 2 else (nums[0], nums[0])


def pred_bucket_floor(mu, unit):
    """Regla FLOOR: floor(mu) en bucket. F: buckets de 2° par-impar (lo=floor si par, sino floor-1)."""
    f = int(math.floor(mu))
    if unit == "F":
        lo = f if f % 2 == 0 else f - 1
        return (lo, lo + 1)
    return (f, f)


def hit_floor(mu, unit, wb):
    pb = pred_bucket_floor(mu, unit)
    if wb[1] is None:                # cola abierta arriba: '>= X'
        return int(pb[0] >= wb[0])
    if wb[0] is None:                # cola abierta abajo: '<= X'
        return int(pb[1] <= wb[1])
    return int(pb == wb)


def main():
    if not os.path.exists(LAB_M):
        sys.exit("[ERROR] falta data/lab_m.csv — correr antes scripts/calib_lab.py (genera el cache base).")
    base_m = pd.read_csv(LAB_M, parse_dates=["target"]); base_m["target"] = base_m["target"].dt.date

    print("1) m point-in-time de los 5 modelos nuevos (Previous-Runs, leads 2-3)...")
    extra_m = build_m_extra()
    M = pd.concat([base_m, extra_m], ignore_index=True)
    cov = M[M.lead == 2].groupby("model").m.count().reindex(ALL8)
    print("   cobertura lead-2 por modelo (dias x estacion, max ~%d):" % (12 * ((D1 - D0_WARM).days + 1)))
    for mod, n in cov.items():
        print(f"     {mod:<12} {0 if pd.isna(n) else int(n)}")

    # real: obs.csv (tmax float) PRIORIZA; backfill_check max_real llena julio (obs corta 07-01)
    obs = pd.read_csv(f"{D}/obs.csv", parse_dates=["date"]); obs["date"] = obs["date"].dt.date
    bk = pd.read_csv(f"{D}/backfill_check.csv"); bk["target"] = pd.to_datetime(bk["target"]).dt.date
    bk2 = bk[bk.lead == 2]
    real_map = {(r.station, r.target): float(r.max_real) for r in bk2.itertuples() if not pd.isna(r.max_real)}
    real_map.update({(r.station, r.date): float(r.tmax) for r in obs.itertuples()})
    win_map = {(r.station, r.target): r.win_mkt for r in bk2.itertuples() if isinstance(r.win_mkt, str)}

    # consensos crudos por (station, target) — lead 2
    piv = M[M.lead == 2].pivot_table(index=["station", "target"], columns="model", values="m", aggfunc="last")
    for mod in ALL8:
        if mod not in piv.columns:
            piv[mod] = np.nan
    okA = piv[BASE].notna().all(axis=1)
    okB = piv[ALL8].notna().all(axis=1)
    rawA = piv.loc[okA, BASE].mean(axis=1)
    rawB = piv.loc[okB, ALL8].mean(axis=1)
    print(f"2) consensos crudos: A_3mod n={len(rawA)}  B_8mod n={len(rawB)} (se scorea sobre la interseccion)")

    # errores diarios crudos por estrategia (para el bias walk-forward: solo dias < target)
    def err_series(raw):
        err = {}
        for (st, d), p in raw.items():
            y = real_map.get((st, d))
            if y is not None:
                err.setdefault(st, []).append((d, float(p), y))
        for st in err:
            err[st].sort()
        return err

    errA, errB = err_series(rawA), err_series(rawB)

    def rolling_bias(err_st, d):
        pts = [p - y for (dd, p, y) in err_st if dd < d and (d - dd).days <= BIAS_WIN]
        return float(np.mean(pts)) if len(pts) >= BIAS_MIN_N else 0.0

    # evaluacion sobre el set COMUN (donde B es computable; B ⊆ A)
    print("3) walk-forward eval 2026-05-10..2026-07-08 (lead 2, bias rolling 60d)...")
    rows = []
    for (st, d) in rawB.index:
        if not (D0_EVAL <= d <= D1) or (st, d) not in rawA.index:
            continue
        unit = STATIONS[st][3]
        muA = float(rawA[(st, d)]) - rolling_bias(errA.get(st, []), d)
        muB = float(rawB[(st, d)]) - rolling_bias(errB.get(st, []), d)
        rec = dict(station=st, target=d, unit=unit, muA=round(muA, 2), muB=round(muB, 2))
        w = win_map.get((st, d))
        wb = parse_win(w) if w is not None else None
        if wb is not None:
            rec["win_mkt"] = w
            rec["hitA"] = hit_floor(muA, unit, wb)
            rec["hitB"] = hit_floor(muB, unit, wb)
        y = real_map.get((st, d))
        if y is not None:
            rec["aeA"] = abs(muA - y); rec["aeB"] = abs(muB - y)
        rows.append(rec)
    df = pd.DataFrame(rows)
    if df.empty:
        sys.exit("[ERROR] 0 filas evaluables — revisar cobertura de los modelos nuevos arriba.")
    df.to_csv(DETAIL, index=False)

    print("\n=== V6: consenso 3 modelos (A) vs 8 modelos (B) — hit exacto FLOOR + MAE ===\n")
    nh, nm = int(df.hitA.notna().sum()), int(df.aeA.notna().sum())
    print(f"{'':>10} {'hit_mkt':>9} {'MAE':>7}   (n_hit={nh}, n_mae={nm})")
    print(f"{'A_3mod':>10} {df.hitA.mean():>8.1%} {df.aeA.mean():>7.2f}")
    print(f"{'B_8mod':>10} {df.hitB.mean():>8.1%} {df.aeB.mean():>7.2f}")
    print(f"{'delta B-A':>10} {df.hitB.mean() - df.hitA.mean():>+8.1%} {df.aeB.mean() - df.aeA.mean():>+7.2f}")
    disc = df.dropna(subset=["hitA"])
    ba = int(((disc.hitB == 1) & (disc.hitA == 0)).sum()); ab = int(((disc.hitA == 1) & (disc.hitB == 0)).sum())
    print(f"\npares discordantes: B acierta y A no = {ba}  |  A acierta y B no = {ab}")

    print("\npor estacion (hit A -> B  |  MAE A -> B):")
    g = df.groupby("station").agg(hitA=("hitA", "mean"), hitB=("hitB", "mean"),
                                  aeA=("aeA", "mean"), aeB=("aeB", "mean"), n=("hitA", "count"))
    for st, r in g.sort_index().iterrows():
        mark = "+" if r.hitB > r.hitA else ("-" if r.hitB < r.hitA else "=")
        print(f"  {st}: {r.hitA:>4.0%} -> {r.hitB:>4.0%} [{mark}]  |  {r.aeA:.2f} -> {r.aeB:.2f}  (n={int(r.n)})")

    print(f"\ndetalle: data/lab_v6_detail.csv")
    print("NOTA: comparacion de CONSENSOS sin EMOS — el nivel absoluto de hit NO es el del pipeline")
    print("(calib_lab V2 usa EMOS + regla half-up); aca solo importa el RELATIVO A vs B.")


if __name__ == "__main__":
    main()
