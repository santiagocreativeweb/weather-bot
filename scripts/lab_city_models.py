#!/usr/bin/env python3
# scripts/lab_city_models.py -- ESTUDIO POR CIUDAD de combinaciones de modelos (pedido Santiago
# 2026-07-12: "tenemos un monton de modelos y no los usamos como corresponde; combina todo y
# compara con hasta 90 dias atras, ciudad por ciudad").
#
# DATOS: data/lab_m8.csv (8 modelos Previous-Runs point-in-time, leads 2/3, 2026-02-10..ayer,
# fetch_lab_m8.py) + data/nbm_backfill.csv (NBM NBS 13z D-1 = lead 2 HONESTO con avail real de S3,
# solo KLGA/KORD) + obs.csv/backfill_check.csv (reales). Eval: 2026-04-12..D1 (~90 dias); warm-up
# 02-10.. para que bias/sigma rolling esten calientes al inicio.
#
# VARIANTES por estacion (lead 2; TODAS con bias rolling 60d sobre sus PROPIOS errores, minn 10,
# y sigma = std rolling 60d de sus propios errores; V2 usa la sigma del EMOS):
#   V2      EMOS 3 modelos base + bias60  (PRODUCCION, el campeon a batir)
#   E3      media cruda gefs/ecmwf/icon + bias60 (aporte del EMOS)
#   S_*     cada modelo SOLO + bias60 (8)
#   ALL8    media de los 8 + bias60
#   W8      media ponderada 1/MSE rolling 60d + bias60
#   TOP3R   los 3 mejores por MSE rolling 60d (re-eleccion diaria walk-forward) + bias60
#   MED8    MEDIANA de los 8 modelos (robusta a outliers de un modelo) + bias60
#   NBM / NBME (solo KLGA/KORD): NBM solo + bias60; media(EMOS3, NBM) + bias60
#
# ANTI-LOOK-AHEAD: fits EMOS por cutoffs mensuales (targets <= cut); bias/sigma/pesos rolling solo
# con dias < target; NBM = ciclo 13z del dia ANTERIOR (avail real S3). CAVEAT bug #5: frescura
# residual de previous_day1, common-mode entre variantes -> vale el RELATIVO, no el nivel.
# ESTADISTICA: delta exacto vs V2 pareado por dia, bootstrap por dia (10k), y correccion por
# SELECCION (E[max de K variantes | nulo] por recentrado) — la trampa que mato a V6/V7/V8 y MED60.
#
# VEREDICTO 2026-07-12 (corrida inicial, eval 04-12..07-11, n=1080 pooled):
#   * por estacion: NADA sobrevive p_adj (el "mejor" por estacion es curse, como V8).
#   * pooled: TODA la familia multi-modelo es positiva vs V2 (MED8 +2.0pp p=0.050, W8 +1.8, ALL8
#     +1.2) y TODOS los singles negativos -> hay estructura, no sorteo. En DEBILES: E3 +2.9 p=0.054.
#   * MAE por modelo: gefs ROTO en RKSI (5.74 vs 1.47 icon); en ZSPD los mejores son ukmo/knmi/gefs.
# REGLA PRE-REGISTRADA (fijada 2026-07-12 ANTES de ver datos nuevos; 3 hipotesis, UNA mirada c/u
# al llegar a n>=45 dias de targets >= SHADOW0; sin peeking semanal):
#   H1 (primaria):   MED8 vs V2, pooled 12 estaciones, adoptar si delta_hit>0 y p_dia<0.05.
#   H2 (secundaria): W8 vs V2, idem, SOLO si H1 falla, umbral p<0.025 (penalidad por segunda mirada).
#   H3 (debiles):    E3 vs V2 pooled SOLO en WEAK, adoptar (solo en esas estaciones) si p<0.05.
import os
import sys
import math
import datetime as dt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from wxbt.engine import fit_all, clim_val, _lead_day        # noqa: E402
from wxbt.calibration import predict                        # noqa: E402
from lab_v7 import (pred_bucket_floor, true_bucket_floor,   # noqa: E402
                    ranked_buckets, CONT)
from show_live import STATIONS                              # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "data")
SUMMARY = os.path.join(D, "lab_city_models_summary.csv")
DETAIL = os.path.join(D, "lab_city_models_detail.csv")

D0_WARM = dt.date(2026, 2, 10)
D0_EVAL = dt.date(2026, 4, 12)          # ~90 dias hasta D1
LIVE0 = dt.date(2026, 7, 8)
SHADOW0 = dt.date(2026, 7, 12)          # sombra: solo targets desde aca cuentan para la regla
CUTS = [dt.date(2026, 2, 9), dt.date(2026, 3, 9), dt.date(2026, 4, 8),
        dt.date(2026, 5, 9), dt.date(2026, 6, 9)]           # re-fit mensual walk-forward
BASE3 = ["gefs", "ecmwf", "icon"]
ALLM = BASE3 + ["meteofrance", "gem", "ukmo", "jma", "knmi"]
LD_VAR = 1.5
WIN, MINN = 60, 10
NBOOT = 10000
WEAK = ["RCSS", "ZSPD", "ZBAA", "RKSI", "KLGA"]


def cut_for(d):
    return max(c for c in CUTS if c < d)


def load_reales():
    obs = pd.read_csv(f"{D}/obs.csv", parse_dates=["date"]); obs["date"] = obs["date"].dt.date
    bk = pd.read_csv(f"{D}/backfill_check.csv"); bk["target"] = pd.to_datetime(bk["target"]).dt.date
    bl = bk[bk.lead == 2]
    real = {(r.station, r.target): float(r.max_real) for r in bl.itertuples() if not pd.isna(r.max_real)}
    real.update({(r.station, r.date): float(r.tmax) for r in obs.itertuples()})  # obs PRIORIZA
    return obs, real


def build_fits(obs):
    fc = pd.read_csv(f"{D}/forecasts.csv", parse_dates=["avail", "target"])
    fc["target_d"] = fc["target"].dt.date
    fc_fit = fc[fc.lead_h > 24].copy()                       # SIN lead-1 (nowcast bug #5)
    fc_fit["target"] = fc_fit["target_d"]
    fc["ld"] = fc["lead_h"].map(_lead_day)
    fits, s2maps = {}, {}
    for cut in CUTS:
        fce = fc_fit[fc_fit.target <= cut]
        obse = obs[obs.date <= cut]
        fits[cut] = fit_all(fce, obse, sorted(obse.date.unique()))
        sub = fc[fc.target_d <= cut].sort_values("avail")
        s2maps[cut] = {(r.station, r.model, r.ld): r.s2 for r in sub.itertuples()}
    return fits, s2maps


def load_nbm():
    """{(st,d): txn_f} lead 2 (ciclo 13z D-1, avail real S3)."""
    p = os.path.join(D, "nbm_backfill.csv")
    if not os.path.exists(p):
        return {}
    df = pd.read_csv(p)
    df = df[df.lead == 2]
    return {(r.station, dt.date.fromisoformat(r.target)): float(r.txn_f) for r in df.itertuples()}


def main():
    M = pd.read_csv(f"{D}/lab_m8.csv", parse_dates=["target"]); M["target"] = M["target"].dt.date
    M = M[M.lead == 2]
    D1 = M.target.max()
    print(f"lab_m8: {len(M)} filas lead2, {M.target.min()}..{D1}")
    obs, real = load_reales()
    print("fits EMOS por cutoff (6 cortes mensuales desde feb)...")
    fits, s2maps = build_fits(obs)
    nbm = load_nbm()
    print(f"NBM backfill: {len(nbm)} (st,dia)")

    piv = M.pivot_table(index=["station", "target"], columns="model", values="m", aggfunc="last")

    # ---- serie por estacion: por dia, m de cada modelo + mu_emos walk-forward ----
    per_st = {}
    for (st, d), row in piv.iterrows():
        if d <= CUTS[0]:
            continue
        m8 = {mo: float(row[mo]) for mo in ALLM if mo in row and not pd.isna(row[mo])}
        rec = dict(d=d, m8=m8, emos=None)
        pars = fits[cut_for(d)].get(st)
        if pars is not None and all(mo in m8 for mo in BASE3):
            s2m = s2maps[cut_for(d)]
            pm = {}
            for mo in BASE3:
                s2 = s2m.get((st, mo, 2))
                if s2 is not None:
                    pm[mo] = (m8[mo], float(s2))
            if len(pm) == 3:
                c = clim_val(pars["clim"], d)
                pr = predict(pars["emos"], {k: (m - c, s2) for k, (m, s2) in pm.items()}, ld=LD_VAR)
                if pr is not None:
                    rec["emos"] = (c + pr[0], pr[1])
        per_st.setdefault(st, []).append(rec)
    for st in per_st:
        per_st[st].sort(key=lambda r: r["d"])

    # ---- variantes: mu crudo por dia (pre-bias) ----
    def variant_mu(st, rec, hist_err_models):
        """dict {variant: mu} para un dia. hist_err_models: {model: [(d, err)]} para pesos rolling."""
        m8, d = rec["m8"], rec["d"]
        out = {}
        if rec["emos"] is not None:
            out["V2"] = rec["emos"][0]
        if all(mo in m8 for mo in BASE3):
            out["E3"] = float(np.mean([m8[mo] for mo in BASE3]))
        for mo in ALLM:
            if mo in m8:
                out[f"S_{mo}"] = m8[mo]
        avail = [mo for mo in ALLM if mo in m8]
        if len(avail) >= 6:
            vals = np.array([m8[mo] for mo in avail])
            out["ALL8"] = float(vals.mean())
            out["MED8"] = float(np.median(vals))
            # pesos/seleccion rolling walk-forward (solo dias < d, ventana 60, min 15 por modelo)
            mses = {}
            for mo in avail:
                errs = [e for (dd, e) in hist_err_models.get(mo, []) if dd < d and (d - dd).days <= WIN]
                if len(errs) >= 15:
                    mses[mo] = max(np.mean(np.square(errs)), 1e-3)
            if len(mses) >= 4:
                inv = {k: 1.0 / v for k, v in mses.items()}
                tot = sum(inv.values())
                out["W8"] = float(sum(m8[k] * w / tot for k, w in inv.items()))
                top3 = sorted(mses, key=mses.get)[:3]
                out["TOP3R"] = float(np.mean([m8[k] for k in top3]))
            # [2026-07-13, tweet AlterEgo "30-60 days accuracy per city"]: version 30d de W8
            # (nosotros solo habiamos probado 60d). Parametro especificado por la fuente.
            mses30 = {}
            for mo in avail:
                errs = [e for (dd, e) in hist_err_models.get(mo, []) if dd < d and (d - dd).days <= 30]
                if len(errs) >= 10:
                    mses30[mo] = max(np.mean(np.square(errs)), 1e-3)
            if len(mses30) >= 4:
                inv = {k: 1.0 / v for k, v in mses30.items()}
                tot = sum(inv.values())
                out["W830"] = float(sum(m8[k] * w / tot for k, w in inv.items()))
        v = nbm.get((st, d))
        if v is not None:
            out["NBM"] = v
            if rec["emos"] is not None:
                out["NBME"] = (rec["emos"][0] + v) / 2.0
        return out

    # ---- loop por estacion: construir series con bias/sigma rolling propios y scorear ----
    unit_of = {st: STATIONS[st][3] for st in per_st}
    detail = []
    for st, recs in per_st.items():
        unit = unit_of[st]
        hist_err_models = {}       # errores por modelo individual (para W8/TOP3R)
        hist_err_var = {}          # errores por variante (bias/sigma rolling propios)
        sig_emos_by_d = {r["d"]: (r["emos"][1] if r["emos"] else None) for r in recs}
        for rec in recs:
            d = rec["d"]
            y = real.get((st, d))
            mus = variant_mu(st, rec, hist_err_models)
            for name, mu0 in mus.items():
                errs = [(dd, e) for (dd, e) in hist_err_var.get(name, []) if dd < d and (d - dd).days <= WIN]
                bias = float(np.mean([e for _, e in errs])) if len(errs) >= MINN else 0.0
                mu = mu0 - bias
                if name == "V2":
                    sg = sig_emos_by_d.get(d) or (2.5 if unit == "F" else 1.5)
                else:
                    sg = float(np.std([e for _, e in errs])) if len(errs) >= MINN else (2.5 if unit == "F" else 1.5)
                    sg = max(sg, 0.6)
                if D0_EVAL <= d <= D1 and y is not None:
                    tb = true_bucket_floor(y, unit)
                    pb = pred_bucket_floor(mu, unit)
                    rk = ranked_buckets(mu, sg, unit)
                    detail.append(dict(st=st, cont=CONT[st], d=d, variant=name,
                                       mu=round(mu, 2), e=mu - y, ae=abs(mu - y),
                                       hit=int(pb == tb), top2=int(tb in rk[:2]), top3=int(tb in rk[:3])))
                if y is not None:
                    hist_err_var.setdefault(name, []).append((d, mu0 - y))   # err PRE-bias (estable)
            if y is not None:
                for mo, v in rec["m8"].items():
                    hist_err_models.setdefault(mo, []).append((d, v - y))

    det = pd.DataFrame(detail)
    det.to_csv(DETAIL, index=False)
    print(f"detalle: {len(det)} filas -> {os.path.relpath(DETAIL)}")

    # ---- estadistica por estacion: cada variante vs V2, pareada por dia ----
    rng = np.random.default_rng(20260712)

    def boot_delta(j, col="hit"):
        """j: df con columnas d, x (variante), v (V2). Bootstrap por dia -> (delta, p_le0)."""
        days = sorted(j.d.unique())
        per_day = {dd: (g.x.sum() - g.v.sum(), len(g)) for dd, g in j.groupby("d")}
        obsdelta = (j.x.mean() - j.v.mean())
        reps = np.empty(NBOOT)
        keys = list(per_day.values())
        arr_s = np.array([k[0] for k in keys], float)
        arr_n = np.array([k[1] for k in keys], float)
        idx = rng.integers(0, len(keys), size=(NBOOT, len(keys)))
        s = arr_s[idx].sum(axis=1); n = arr_n[idx].sum(axis=1)
        reps = np.divide(s, n, out=np.zeros_like(s), where=n > 0)
        return obsdelta, float((reps <= 0).mean())

    summary = []
    print("\n" + "=" * 100)
    print("POR ESTACION (lead 2, eval %s..%s, exacto floor vs real):" % (D0_EVAL, D1))
    print("%-6s %5s | %-52s | %s" % ("st", "V2", "variantes (delta exacto vs V2)", "mejor [p_boot dia]"))
    for st in sorted(per_st):
        dv = det[(det.st == st) & (det.variant == "V2")]
        if dv.empty:
            continue
        base_hit = dv.hit.mean()
        rowtxt, best = [], (None, -9, 1.0)
        deltas_by_day = {}
        for name in sorted(det[det.st == st].variant.unique()):
            if name == "V2":
                continue
            dx = det[(det.st == st) & (det.variant == name)]
            j = dx[["d", "hit"]].rename(columns={"hit": "x"}).merge(
                dv[["d", "hit"]].rename(columns={"hit": "v"}), on="d")
            if len(j) < 40:
                continue
            delta, p = boot_delta(j)
            rowtxt.append("%s %+0.1f" % (name, 100 * delta))
            deltas_by_day[name] = j.assign(dl=j.x - j.v).groupby("d").dl.mean()
            if delta > best[1]:
                best = (name, delta, p)
        # correccion por seleccion: E[max de K | nulo] por recentrado + bootstrap conjunto por dia
        p_adj = None
        if deltas_by_day and best[0] is not None:
            dd_all = sorted(set().union(*[set(s.index) for s in deltas_by_day.values()]))
            Mx = np.array([[s.reindex(dd_all).fillna(0.0)[dd] for dd in dd_all] for s in deltas_by_day.values()])
            Mc = Mx - Mx.mean(axis=1, keepdims=True)     # nulo: recentrar cada variante
            idx = rng.integers(0, len(dd_all), size=(NBOOT, len(dd_all)))
            maxes = np.array([Mc[:, ix].mean(axis=1).max() for ix in idx])
            p_adj = float((maxes >= best[1]).mean())
        summary.append(dict(st=st, v2=base_hit, best=best[0], delta=best[1], p_raw=best[2],
                            p_adj=p_adj, n=len(dv)))
        print("%-6s %4.0f%% | %-52s | %s %+0.1fpp p=%.2f p_adj=%.2f" % (
            st, 100 * base_hit, " ".join(rowtxt[:6]), best[0], 100 * best[1], best[2],
            (p_adj if p_adj is not None else float("nan"))))

    # ---- pooled: cada variante vs V2 sobre TODAS las estaciones (donde existe) ----
    print("\nPOOLED (todas las estaciones, pareado por (st,dia); bootstrap por DIA):")
    pool_rows = []
    for name in sorted(det.variant.unique()):
        if name == "V2":
            continue
        dx = det[det.variant == name]
        dv = det[det.variant == "V2"]
        j = dx[["st", "d", "hit", "top2", "ae"]].rename(columns={"hit": "x", "top2": "x2", "ae": "xa"}).merge(
            dv[["st", "d", "hit", "top2", "ae"]].rename(columns={"hit": "v", "top2": "v2", "ae": "va"}),
            on=["st", "d"])
        if len(j) < 300:
            continue
        delta, p = boot_delta(j)
        d2 = j.x2.mean() - j.v2.mean(); dm = j.xa.mean() - j.va.mean()
        pool_rows.append(dict(variant=name, n=len(j), delta_hit=delta, p_le0=p,
                              delta_top2=d2, delta_mae=dm))
        print("  %-14s n=%4d  delta exacto %+0.2fpp (p_dia=%.3f)  top2 %+0.2fpp  MAE %+0.03f" % (
            name, len(j), 100 * delta, p, 100 * d2, dm))

    # ---- pooled solo DEBILES ----
    print("\nPOOLED solo DEBILES (%s):" % ",".join(WEAK))
    for name in sorted(det.variant.unique()):
        if name == "V2":
            continue
        dx = det[(det.variant == name) & (det.st.isin(WEAK))]
        dv = det[(det.variant == "V2") & (det.st.isin(WEAK))]
        j = dx[["st", "d", "hit"]].rename(columns={"hit": "x"}).merge(
            dv[["st", "d", "hit"]].rename(columns={"hit": "v"}), on=["st", "d"])
        if len(j) < 150:
            continue
        delta, p = boot_delta(j)
        print("  %-14s n=%4d  delta exacto %+0.2fpp (p_dia=%.3f)" % (name, len(j), 100 * delta, p))

    pd.DataFrame(summary).to_csv(SUMMARY, index=False)
    ps = pd.DataFrame(pool_rows)
    ps.to_csv(SUMMARY.replace(".csv", "_pooled.csv"), index=False)
    print(f"\nresumen -> {os.path.relpath(SUMMARY)} (+_pooled)")

    # ---- diagnostico por modelo y estacion (MAE 60d reciente) ----
    print("\nMAE por MODELO y estacion (ultimos 60 dias, lead 2) — quien es bueno donde:")
    cutd = D1 - dt.timedelta(days=60)
    hdr = ["st"] + ALLM + ["nbm"]
    print(("%-6s" + " %6s" * (len(hdr) - 1)) % tuple(hdr))
    for st in sorted(per_st):
        vals = []
        for mo in ALLM:
            errs = [abs(r["m8"][mo] - real[(st, r["d"])]) for r in per_st[st]
                    if mo in r["m8"] and (st, r["d"]) in real and r["d"] > cutd]
            vals.append("%6.2f" % np.mean(errs) if len(errs) >= 20 else "     -")
        nerrs = [abs(nbm[(st, r["d"])] - real[(st, r["d"])]) for r in per_st[st]
                 if (st, r["d"]) in nbm and (st, r["d"]) in real and r["d"] > cutd]
        vals.append("%6.2f" % np.mean(nerrs) if len(nerrs) >= 20 else "     -")
        print(("%-6s" + " %s" * (len(hdr) - 1)) % tuple([st] + vals))

    # ---- SOMBRA pre-registrada (header): MED8 / W8 pooled, E3 en debiles; targets >= SHADOW0 ----
    print("\n--- SOMBRA COMBOS (targets >= %s; regla: n>=45 dias, UNA mirada) ---" % SHADOW0)
    for name, scope, alpha in [("MED8", None, 0.05), ("W8", None, 0.025), ("E3", WEAK, 0.05)]:
        dx = det[(det.variant == name) & (det.d >= SHADOW0)]
        dv = det[(det.variant == "V2") & (det.d >= SHADOW0)]
        if scope is not None:
            dx = dx[dx.st.isin(scope)]; dv = dv[dv.st.isin(scope)]
        j = dx[["st", "d", "hit"]].rename(columns={"hit": "x"}).merge(
            dv[["st", "d", "hit"]].rename(columns={"hit": "v"}), on=["st", "d"])
        ndays = j.d.nunique()
        tag = "pooled" if scope is None else "solo DEBILES"
        if ndays == 0:
            print("  %s (%s, alpha=%.3f): sin datos aun (%d/45 dias)." % (name, tag, alpha, ndays))
            continue
        delta, p = boot_delta(j)
        print("  %s (%s): delta %+0.2fpp  p_dia=%.3f  (%d/45 dias, n=%d)%s" % (
            name, tag, 100 * delta, p, ndays, len(j),
            "  >>> EVALUAR REGLA (n>=45): " + ("ADOPTAR" if (delta > 0 and p < alpha) else "NO adoptar")
            if ndays >= 45 else ""))

    print("\nCAVEATS: niveles optimistas (bug #5 common-mode). K~13 variantes/estacion -> el mejor")
    print("por estacion casi siempre es curse (mirar p_adj). Solo el POOLED con p_dia<0.05 y")
    print("consistencia entre estaciones merece sombra pre-registrada; nada se adopta directo.")


if __name__ == "__main__":
    main()
