#!/usr/bin/env python3
# scripts/lab_ml.py -- POST-PROCESAMIENTO ML (pedido Santiago 2026-07-13: "ciencia de datos como
# meteorologos profesionales; no nos bloqueemos en el 44%"). Gradient boosting sobre covariables,
# entrenado con los 18 MESES de forecasts.csv (2025-01->) — lo que NINGUN lab anterior uso (todos
# eran variantes lineales del EMOS sobre 3-5 meses).
#
# METODO (post-procesamiento estadistico estandar en centros operativos, familia "ML-MOS"):
#   target   = anomalia de la obs vs climatologia kernel (bug #4: NUNCA nivel absoluto), en °C.
#   features = anomalias de gefs/ecmwf/icon (°C), s2 de c/u, spread y media del trio, 5 modelos
#              extra (lab_m8, NaN antes de 02/2026 — HistGB los tolera nativo), armonicos del dia
#              del año, errores rolling 15/60d del consenso (walk-forward), estacion (categorica).
#   2 variantes FIJADAS a priori (sin grid de hiperparametros — 2 candidatos, no 200):
#     GBMm  loss=squared_error (media)  |  GBMq  loss=absolute_error (mediana)
#   Entrenamiento POOLED 12 estaciones (°C comun; °F convertido), refit por cutoff mensual,
#   prediccion solo en (cut, cut_sig] -> walk-forward estricto. Pick = floor(clim + anom_pred).
#
# EVAL: mismos 90 dias del lab de combos (04-12..D1), pareado por (st,dia) contra V2 usando
# data/lab_city_models_detail.csv. Bootstrap por DIA + split H1/H2. CAVEAT bug #5 common-mode.
#
# VEREDICTO 2026-07-13 (corrida inicial, n=1080): GBMm PIERDE (-0.28pp). GBMq +1.57pp p=0.171
# pero INESTABLE (H1 +4.8 / H2 -1.7, LIVE 29% vs 44%) -> NO se adopta. Patron consistente con
# E3-debiles y W8: el ML gana solo en las estaciones DEBILES (RCSS 15->27, KLGA 36->43,
# LEMD 31->43) y pierde donde V2 es bueno -> el techo esta en las FUENTES, no en el calibrador.
# REGLA PRE-REGISTRADA H4 (fijada 2026-07-13, ANTES de datos nuevos; 5ta hipotesis de la familia
# -> alpha estricto): GBMq vs V2 pooled, targets >= 2026-07-13, n >= 45 dias, UNA mirada:
# adoptar solo si delta_hit > 0 y p_dia < 0.025.
import os
import sys
import math
import datetime as dt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from wxbt.engine import clim_val, _lead_day                          # noqa: E402
from lab_v7 import pred_bucket_floor, true_bucket_floor, ranked_buckets  # noqa: E402
from lab_city_models import (load_reales, build_fits, cut_for, CUTS,     # noqa: E402
                             CONT, D0_EVAL, LIVE0, SHADOW0)
from show_live import STATIONS                                       # noqa: E402
from sklearn.ensemble import HistGradientBoostingRegressor           # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "data")
DETAIL = os.path.join(D, "lab_ml_detail.csv")
BASE3 = ["gefs", "ecmwf", "icon"]
EXTRA5 = ["meteofrance", "gem", "ukmo", "jma", "knmi"]
NBOOT = 10000
GBM_PARAMS = dict(max_iter=400, learning_rate=0.06, min_samples_leaf=30,
                  l2_regularization=1.0, random_state=20260713)      # FIJOS a priori


def f2c(v, unit):
    return v * 5.0 / 9.0 if unit == "F" else v      # para ANOMALIAS/errores (diferencias)


def main():
    obs, real = load_reales()
    print("fits/climatologia por cutoff (reusa lab_city_models.build_fits)...")
    fits, s2maps = build_fits(obs)

    # ---- m lead-2: forecasts.csv (2025-01..2026-06-30, con s2) + lab_m8 (2026-02-10.., julio) ----
    fc = pd.read_csv(f"{D}/forecasts.csv", parse_dates=["target"])
    fc["d"] = fc["target"].dt.date
    fc["ld"] = fc["lead_h"].map(_lead_day)
    fc = fc[fc.ld == 2]
    m_base, s2_base = {}, {}
    for r in fc.itertuples():
        m_base[(r.station, r.d, r.model)] = float(r.m)
        s2_base[(r.station, r.d, r.model)] = float(r.s2)
    m8 = pd.read_csv(f"{D}/lab_m8.csv", parse_dates=["target"])
    m8["d"] = m8["target"].dt.date
    m8 = m8[m8.lead == 2]
    m_extra = {}
    for r in m8.itertuples():
        if r.model in BASE3:
            m_base.setdefault((r.station, r.d, r.model), float(r.m))   # julio (forecasts corta 06-30)
        else:
            m_extra[(r.station, r.d, r.model)] = float(r.m)
    D1 = max(k[1] for k in m_base)
    print(f"m: base {len(m_base)} celdas, extra {len(m_extra)}; D1={D1}")

    # ---- filas por (st, d): features + target (todo en °C) ----
    st_list = sorted(STATIONS)
    st_code = {s: i for i, s in enumerate(st_list)}
    rows = []
    err_hist = {}    # st -> [(d, err_consenso_C)] para features rolling (walk-forward)
    # NOTA anti-look-ahead: filas 2025 usan la clim del 1er cutoff (02-09) — la clim "ve" hasta
    # 02-09, pero esas filas solo entrenan GBMs con cutoff >= 02-09 -> el GBM nunca ve mas alla
    # de su propio cutoff. Las filas de EVAL usan siempre fits[cut < d] (walk-forward estricto).
    all_days = sorted({k[1] for k in m_base})
    for d in all_days:
        for st in st_list:
            unit = STATIONS[st][3]
            ms = [m_base.get((st, d, mo)) for mo in BASE3]
            if any(v is None for v in ms):
                continue
            cut = max((c for c in CUTS if c < d), default=None)
            pars = fits[cut].get(st) if cut else None
            if pars is None:                        # dias pre-2026: usar clim del PRIMER cutoff
                pars = fits[CUTS[0]].get(st)
                if pars is None or d >= CUTS[0]:
                    continue
            clim = clim_val(pars["clim"], d)
            a3 = [f2c(m - clim, unit) for m in ms]
            s3 = [s2_base.get((st, d, mo)) for mo in BASE3]
            s3 = [f2c(f2c(v, unit), unit) if (v is not None and unit == "F") else v for v in s3]
            s3 = [v if v is not None else np.nan for v in s3]
            a5 = [f2c(m_extra[(st, d, mo)] - clim, unit) if (st, d, mo) in m_extra else np.nan
                  for mo in EXTRA5]
            hist = [(dd, e) for (dd, e) in err_hist.get(st, []) if dd < d]
            e15 = np.mean([e for dd, e in hist if (d - dd).days <= 15]) if sum(
                1 for dd, e in hist if (d - dd).days <= 15) >= 5 else 0.0
            e60 = np.mean([e for dd, e in hist if (d - dd).days <= 60]) if sum(
                1 for dd, e in hist if (d - dd).days <= 60) >= 10 else 0.0
            doy = d.timetuple().tm_yday
            feats = a3 + s3 + a5 + [max(a3) - min(a3), float(np.mean(a3)),
                                    math.sin(2 * math.pi * doy / 365.25),
                                    math.cos(2 * math.pi * doy / 365.25),
                                    float(e15), float(e60), st_code[st]]
            y = real.get((st, d))
            y_anom = f2c(y - clim, unit) if y is not None else None
            rows.append(dict(st=st, d=d, unit=unit, clim=clim, feats=feats, y=y, y_anom=y_anom))
            if y_anom is not None:
                err_hist.setdefault(st, []).append((d, float(np.mean(a3)) - y_anom))

    FEAT_N = len(rows[0]["feats"])
    CAT_IDX = [FEAT_N - 1]
    print(f"{len(rows)} filas ({rows[0]['d']}..{rows[-1]['d']}), {FEAT_N} features")

    # ---- walk-forward: refit en cada cutoff, predecir (cut, cut_sig] ----
    df = pd.DataFrame(rows)
    preds = {"GBMm": {}, "GBMq": {}}
    bounds = CUTS + [dt.date(2099, 1, 1)]
    for ci, cut in enumerate(CUTS):
        tr = df[(df.d <= cut) & df.y_anom.notna()]
        te = df[(df.d > cut) & (df.d <= bounds[ci + 1])]
        if len(tr) < 500 or te.empty:
            continue
        Xtr = np.array(tr.feats.tolist()); ytr = tr.y_anom.values
        Xte = np.array(te.feats.tolist())
        for name, loss in [("GBMm", "squared_error"), ("GBMq", "absolute_error")]:
            gbm = HistGradientBoostingRegressor(loss=loss, categorical_features=CAT_IDX, **GBM_PARAMS)
            gbm.fit(Xtr, ytr)
            for (i, r), p in zip(te.iterrows(), gbm.predict(Xte)):
                # anomalia °C -> unidad nativa
                anat = p * 9.0 / 5.0 if r.unit == "F" else p
                preds[name][(r.st, r.d)] = r.clim + anat
        print(f"  cutoff {cut}: train n={len(tr)}, test n={len(te)}")

    # ---- scoring floor en la ventana de eval, pareado contra V2 del lab de combos ----
    v2 = pd.read_csv(f"{D}/lab_city_models_detail.csv")
    v2 = v2[v2.variant == "V2"].copy()
    v2["d"] = pd.to_datetime(v2.d).dt.date
    v2 = v2[["st", "d", "mu", "hit", "top2", "ae"]].rename(
        columns={"mu": "mu_v2", "hit": "hit_v2", "top2": "top2_v2", "ae": "ae_v2"})

    det = []
    sig_hist = {}
    for name in ("GBMm", "GBMq"):
        errs_by_st = {}
        for (st, d), mu in sorted(preds[name].items(), key=lambda kv: kv[0][1]):
            y = real.get((st, d))
            unit = STATIONS[st][3]
            if y is None:
                continue
            past = [e for (dd, e) in errs_by_st.get(st, []) if dd < d and (d - dd).days <= 60]
            sg = max(float(np.std(past)), 0.6) if len(past) >= 10 else (2.5 if unit == "F" else 1.5)
            if D0_EVAL <= d <= D1:
                tb = true_bucket_floor(y, unit)
                pb = pred_bucket_floor(mu, unit)
                rk = ranked_buckets(mu, sg, unit)
                det.append(dict(variant=name, st=st, d=d, mu=round(mu, 2), e=mu - y,
                                ae=abs(mu - y), hit=int(pb == tb),
                                top2=int(tb in rk[:2]), top3=int(tb in rk[:3])))
            errs_by_st.setdefault(st, []).append((d, mu - y))
    det = pd.DataFrame(det)
    det.to_csv(DETAIL, index=False)

    rng = np.random.default_rng(20260713)

    def boot(j, xc, vc):
        days = sorted(j.d.unique())
        per = {dd: (g[xc].sum() - g[vc].sum(), len(g)) for dd, g in j.groupby("d")}
        arr_s = np.array([per[dd][0] for dd in days], float)
        arr_n = np.array([per[dd][1] for dd in days], float)
        idx = rng.integers(0, len(days), size=(NBOOT, len(days)))
        s = arr_s[idx].sum(axis=1); n = arr_n[idx].sum(axis=1)
        reps = np.divide(s, n, out=np.zeros_like(s), where=n > 0)
        return float(j[xc].mean() - j[vc].mean()), float((reps <= 0).mean())

    print("\n" + "=" * 88)
    mid = D0_EVAL + (D1 - D0_EVAL) / 2
    for name in ("GBMm", "GBMq"):
        dx = det[det.variant == name]
        j = dx.merge(v2, on=["st", "d"])
        if j.empty:
            print(f"{name}: sin filas pareadas"); continue
        dh, p = boot(j, "hit", "hit_v2")
        dt2, _ = boot(j, "top2", "top2_v2")
        dm = j.ae.mean() - j.ae_v2.mean()
        print(f"\n{name} vs V2 (eval {D0_EVAL}..{D1}, n={len(j)} pareadas):")
        print(f"  exacto {j.hit_v2.mean():.1%} -> {j.hit.mean():.1%}  delta {100*dh:+.2f}pp  p_dia={p:.3f}")
        print(f"  top2   {j.top2_v2.mean():.1%} -> {j.top2.mean():.1%}  delta {100*dt2:+.2f}pp   MAE {j.ae_v2.mean():.2f} -> {j.ae.mean():.2f} ({dm:+.3f})")
        for tag, lo, hi in [("H1", D0_EVAL, mid), ("H2", mid + dt.timedelta(days=1), D1),
                            ("LIVE", LIVE0, D1)]:
            jj = j[(j.d >= lo) & (j.d <= hi)]
            if len(jj):
                print(f"  {tag}: V2 {jj.hit_v2.mean():.1%} vs {name} {jj.hit.mean():.1%} (n={len(jj)})")
        print("  por estacion (V2 -> %s, exacto):" % name)
        for st in sorted(j.st.unique()):
            js = j[j.st == st]
            mk = "+" if js.hit.mean() > js.hit_v2.mean() + 1e-9 else (
                "-" if js.hit.mean() < js.hit_v2.mean() - 1e-9 else "=")
            print(f"    {st}: {js.hit_v2.mean():>4.0%} -> {js.hit.mean():>4.0%} [{mk}]")

    # ---- SOMBRA H4 (regla del header): GBMq vs V2, targets >= 2026-07-13 ----
    SHADOW1 = dt.date(2026, 7, 13)
    dx = det[(det.variant == "GBMq") & (det.d >= SHADOW1)]
    j = dx.merge(v2[v2.d >= SHADOW1], on=["st", "d"]) if len(dx) else pd.DataFrame()
    print(f"\n--- SOMBRA H4 GBMq (targets >= {SHADOW1}; regla: n>=45 dias, alpha=0.025, UNA mirada) ---")
    if j.empty:
        print("  sin datos aun (0/45 dias).")
    else:
        ndays = j.d.nunique()
        dh, p = boot(j, "hit", "hit_v2")
        print(f"  delta {100*dh:+.2f}pp  p_dia={p:.3f}  ({ndays}/45 dias, n={len(j)})"
              + ("  >>> EVALUAR REGLA: " + ("ADOPTAR" if (dh > 0 and p < 0.025) else "NO adoptar")
                 if ndays >= 45 else ""))

    print(f"\ndetalle -> {os.path.relpath(DETAIL)}")
    print("CAVEATS: bug #5 common-mode (relativo valido); 2 candidatos fijados a priori (sin grid);")
    print("nada se adopta directo: solo via la sombra H4.")


if __name__ == "__main__":
    main()
