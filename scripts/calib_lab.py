#!/usr/bin/env python3
# scripts/calib_lab.py — LABORATORIO de calibracion: evalua variantes del calibrador en
# WALK-FORWARD estricto y las compara con metricas objetivas (pedido de Santiago, puntos 7-8).
# [Creado 2026-07-09.] REGLA DE ORO: ninguna variante se adopta si no le gana a la baseline
# en la ventana de evaluacion — asi nacieron los bugs d,e y lead-1: por hornear sin validar.
#
# Variantes:
#   V0 base      : EMOS actual (anomalias vs clim, pesos por MSE, var por s2+lead).
#   V1 bias30    : V0 + correccion de SESGO ROLLING por estacion (media de (pred-real) de los
#                  ultimos 30 dias ANTERIORES al target; ventana movil, sin futuro). Es la
#                  "comparacion historica por estacion" del punto 3, hecha honesta.
#   V2 bias60    : idem con ventana 60d.
#   V3 win90     : EMOS entrenado SOLO con los ultimos 90 dias (ventana movil vs expanding).
#   V4 bias30+ff : V1 + floor-fix °F: al scorear contra el GANADOR WU en KLGA/KORD se corre
#                  mu -0.5°F (evidencia: misses IEM-vs-WU casi 100% unilaterales +1°F).
#   V5 slope45   : V0 + re-regresion local y=a+b*mu de los ultimos 45 dias por estacion
#                  (corrige sesgo Y pendiente reciente).
#
# Scoring (eval 2026-05-10..2026-07-08, 60 dias, lead 2 = corrida del dia anterior):
#   hit_mkt (bucket argmax == ganador WU), pwin (prob al ganador), MAE/RMSE/sesgo vs IEM real.
#   Por estacion y por continente. Anti-look-ahead: 3 cutoffs de re-fit (mensuales) y ventanas
#   de sesgo que solo miran dias < target.
import os, sys, re, math
import datetime as dt
import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from wxbt.engine import fit_all, clim_val, _lead_day
from wxbt.calibration import predict
from wxbt.market import bucket_prob
from show_live import STATIONS, PREV_RUNS, daily_tmax

D = os.path.join(os.path.dirname(__file__), "..", "data")
LAB_M = os.path.join(D, "lab_m.csv")
CONT = {"KLGA": "America", "KORD": "America", "EGLC": "Europa", "LFPB": "Europa",
        "LEMD": "Europa", "EDDM": "Europa", "LIMC": "Europa", "RJTT": "Asia",
        "RKSI": "Asia", "ZSPD": "Asia", "ZBAA": "Asia", "RCSS": "Asia"}
MODELS_OM = {"gefs": ("gfs_seamless", 5.0), "ecmwf": ("ecmwf_ifs025", 7.0), "icon": ("icon_seamless", 7.0)}
LEAD_COL = {2: "temperature_2m_previous_day1", 3: "temperature_2m_previous_day2"}  # SIN lead1 (nowcast)
# [2026-07-12] D1 DINAMICO (antes hardcodeado -> el refresh semanal del bias era NO-OP silencioso):
# ultimo target con dia COMPLETO en todas las estaciones (ayer; hoy contamina con obs parcial),
# capeado por la cobertura de backfill_check.csv (que aporta win_mkt/max_real de julio).
# Requiere extender backfill_check.csv antes (run_check.ps1 lo hace: backfill_check.py --append).
def _d1_dynamic():
    yday = dt.date.today() - dt.timedelta(days=1)
    try:
        bk_t = pd.to_datetime(pd.read_csv(os.path.join(D, "backfill_check.csv"),
                                          usecols=["target"])["target"])
        return min(yday, bk_t.max().date())
    except Exception:
        return yday


BIAS_WINDOW = 60   # ventana (dias) del sesgo rolling que se dumpea a station_bias.json (V2)
D0_WARM, D0_EVAL, D1 = dt.date(2026, 4, 9), dt.date(2026, 5, 10), _d1_dynamic()
CUTS = [dt.date(2026, 4, 8), dt.date(2026, 5, 9), dt.date(2026, 6, 9)]   # re-fit mensual


def build_m():
    """m point-in-time por (station, target, model, lead 2/3) para D0_WARM..D1. Cachea en lab_m.csv.
    [2026-07-12] el cache se INVALIDA si no cubre D1 (antes se devolvia viejo silenciosamente):
    refetch completo — Previous-Runs es archivo deterministico, refetchear da lo mismo."""
    if os.path.exists(LAB_M):
        m = pd.read_csv(LAB_M, parse_dates=["target"]); m["target"] = m["target"].dt.date
        if m["target"].max() >= D1:
            return m
        print(f"   cache lab_m.csv viejo (max {m['target'].max()} < D1 {D1}) -> refetch completo")
    rows = []
    for code, (lat, lon, off, unit) in STATIONS.items():
        for model, (om, lag) in MODELS_OM.items():
            p = dict(latitude=lat, longitude=lon, models=om, hourly=",".join(LEAD_COL.values()),
                     start_date=(D0_WARM - dt.timedelta(days=1)).isoformat(),
                     end_date=(D1 + dt.timedelta(days=1)).isoformat(), timezone="UTC",
                     temperature_unit=("fahrenheit" if unit == "F" else "celsius"))
            try:
                r = requests.get(PREV_RUNS, params=p, timeout=90); r.raise_for_status()
                h = r.json()["hourly"]
            except Exception as e:
                print(f"[WARN] {code} {model}: {e}", file=sys.stderr); continue
            for lead, col in LEAD_COL.items():
                if col not in h:
                    continue
                for d, mval in daily_tmax(h["time"], h[col], off).items():
                    if D0_WARM <= d <= D1:
                        rows.append([code, d.isoformat(), model, lead, round(mval, 2)])
    m = pd.DataFrame(rows, columns=["station", "target", "model", "lead", "m"])
    m.to_csv(LAB_M, index=False)
    m["target"] = pd.to_datetime(m["target"]).dt.date
    return m


def main():
    print("1) dataset m point-in-time (leads 2-3, 90 dias)...")
    M = build_m()
    print(f"   {len(M)} filas")

    fc = pd.read_csv(f"{D}/forecasts.csv", parse_dates=["target"]); fc["target"] = fc["target"].dt.date
    fc = fc[fc.lead_h > 24]   # SIN lead-1 (nowcast del bug #5): fits solo con leads honestos
    obs = pd.read_csv(f"{D}/obs.csv", parse_dates=["date"]); obs["date"] = obs["date"].dt.date
    obs_map = {(r.station, r.date): float(r.tmax) for r in obs.itertuples()}
    bk = pd.read_csv(f"{D}/backfill_check.csv"); bk["target"] = pd.to_datetime(bk["target"]).dt.date
    bk2 = bk[bk.lead == 2]
    win_map = {(r.station, r.target): r.win_mkt for r in bk2.itertuples() if isinstance(r.win_mkt, str)}
    real_map = {(r.station, r.target): r.max_real for r in bk2.itertuples() if not pd.isna(r.max_real)}
    real_map = {**real_map, **obs_map}   # obs.csv PRIORIZA (fix verificador: estaba invertido), backfill llena julio

    # s2 por (station, model, lead) al ultimo dato <= cada cutoff (walk-forward)
    fc_ld = pd.read_csv(f"{D}/forecasts.csv", parse_dates=["avail", "target"])
    fc_ld["ld"] = fc_ld["lead_h"].map(_lead_day)
    fc_ld["tdate"] = fc_ld["target"].dt.date
    print("2) fits EMOS por cutoff (expanding y win90)...")
    fits, fits90, s2maps = {}, {}, {}
    for cut in CUTS:
        fce = fc[fc.target <= cut]; obse = obs[obs.date <= cut]
        fits[cut] = fit_all(fce, obse, sorted(obse.date.unique()))
        lo90 = cut - dt.timedelta(days=90)
        fc9 = fc[(fc.target <= cut) & (fc.target > lo90)]; ob9 = obs[(obs.date <= cut) & (obs.date > lo90)]
        fits90[cut] = fit_all(fc9, ob9, sorted(ob9.date.unique()))
        sub = fc_ld[fc_ld.tdate <= cut].sort_values("avail")
        s2maps[cut] = {(r.station, r.model, r.ld): r.s2 for r in sub.itertuples()}

    def cut_for(d):
        return max(c for c in CUTS if c < d)

    # prediccion base (mu, sigma) por (station, target) — lead 2
    print("3) predicciones V0 walk-forward (lead 2)...")
    piv = M[M.lead == 2].pivot_table(index=["station", "target"], columns="model", values="m", aggfunc="last")
    base = {}
    for (st, d), row in piv.iterrows():
        cut = cut_for(d)
        pars = fits[cut].get(st); s2m = s2maps[cut]
        if pars is None:
            continue
        pm = {}
        for model in MODELS_OM:
            v = row.get(model)
            if pd.isna(v):
                continue
            s2 = s2m.get((st, model, 2))
            if s2 is None:
                continue
            pm[model] = (float(v), float(s2))
        if len(pm) < 3:
            continue
        c = clim_val(pars["clim"], d)
        pr = predict(pars["emos"], {k: (m - c, s2) for k, (m, s2) in pm.items()}, ld=1.5)
        if pr is None:
            continue
        base[(st, d)] = (c + pr[0], pr[1], pm)

    # V3: mismo pipeline con fits90
    base90 = {}
    for (st, d), (_, _, pm) in base.items():
        pars = fits90[cut_for(d)].get(st)
        if pars is None:
            continue
        c = clim_val(pars["clim"], d)
        pr = predict(pars["emos"], {k: (m - c, s2) for k, (m, s2) in pm.items()}, ld=1.5)
        if pr is not None:
            base90[(st, d)] = (c + pr[0], pr[1])

    # errores diarios de V0 para ventanas rolling (bias/slope), walk-forward por construccion
    err = {}
    for (st, d), (mu, sg, _) in base.items():
        y = real_map.get((st, d))
        if y is not None:
            err.setdefault(st, []).append((d, mu, y))
    for st in err:
        err[st].sort()

    def rolling_bias(st, d, win):
        pts = [(mu - y) for (dd, mu, y) in err.get(st, []) if dd < d and (d - dd).days <= win]
        return float(np.mean(pts)) if len(pts) >= 10 else 0.0

    def rolling_slope(st, d, win=45):
        pts = [(mu, y) for (dd, mu, y) in err.get(st, []) if dd < d and (d - dd).days <= win]
        if len(pts) < 15:
            return (0.0, 1.0)
        mus = np.array([p[0] for p in pts]); ys = np.array([p[1] for p in pts])
        b, a = np.polyfit(mus, ys, 1)
        b = float(np.clip(b, 0.5, 1.5))
        return (float(a), b)

    # variantes -> mu ajustado por (st, d)
    def variant_mu(name):
        out = {}
        for (st, d), (mu, sg, _) in base.items():
            if d < D0_EVAL:
                continue
            if name == "V0":
                m2 = mu
            elif name == "V1":
                m2 = mu - rolling_bias(st, d, 30)
            elif name == "V2":
                m2 = mu - rolling_bias(st, d, 60)
            elif name == "V3":
                b = base90.get((st, d))
                m2 = b[0] if b else mu
            elif name == "V4":
                m2 = mu - rolling_bias(st, d, 30)
            elif name == "V5":
                a, b = rolling_slope(st, d)
                m2 = a + b * mu if a != 0.0 or b != 1.0 else mu
            out[(st, d)] = (m2, sg)
        return out

    def parse_win(w, unit):
        nums = [int(x) for x in re.findall(r"\d+", str(w))]
        if not nums:
            return None
        if "higher" in str(w) or ">=" in str(w):
            return (nums[0], None)
        if "below" in str(w) or "<=" in str(w):
            return (None, nums[0])
        return (nums[0], nums[1]) if len(nums) >= 2 else (nums[0], nums[0])

    def pred_bucket(mu, unit):
        r = int(math.floor(mu + 0.5))
        if unit == "F":
            lo = r if r % 2 == 0 else r - 1
            return (lo, lo + 1)
        return (r, r)

    def score(name, mus, floor_fix=False):
        rows = []
        for (st, d), (mu, sg) in mus.items():
            unit = STATIONS[st][3]
            w = win_map.get((st, d)); y = real_map.get((st, d))
            mu_mkt = mu - 0.5 if (floor_fix and unit == "F") else mu
            rec = dict(st=st, d=d, cont=CONT[st])
            if w is not None:
                wb = parse_win(w, unit)
                pb = pred_bucket(mu_mkt, unit)
                if wb and wb[1] is None:
                    hit = pb[0] >= wb[0]
                elif wb and wb[0] is None:
                    hit = pb[1] <= wb[1]
                else:
                    hit = wb == pb
                rec["hit"] = int(hit)
                lo, hi = (wb if wb else (None, None))
                rec["pwin"] = bucket_prob(mu_mkt, sg, lo, hi) if wb else np.nan
            if y is not None:
                rec["ae"] = abs(mu - y); rec["e"] = mu - y
            rows.append(rec)
        df = pd.DataFrame(rows)
        return df

    print("4) evaluacion de variantes (eval 2026-05-10..2026-07-08, lead 2):\n")
    results = {}
    for name, ff in [("V0", False), ("V1", False), ("V2", False), ("V3", False), ("V4", True), ("V5", False)]:
        df = score(name, variant_mu(name), floor_fix=ff)
        results[name] = df
        h = df.hit.mean(); pw = df.pwin.mean()
        mae = df.ae.mean(); rmse = np.sqrt((df.e ** 2).mean()); bias = df.e.mean()
        print(f"{name}: hit_mkt={h:.1%}  pwin={pw:.3f}  MAE={mae:.2f}  RMSE={rmse:.2f}  sesgo={bias:+.2f}  n={df.hit.notna().sum()}")

    best = max(results, key=lambda k: (results[k].hit.mean(), -results[k].ae.mean()))
    print(f"\nGANADOR por hit_mkt: {best}")
    print("\npor estacion (hit V0 -> ganador):")
    v0 = results["V0"].groupby("st").hit.mean()
    vb = results[best].groupby("st").hit.mean()
    for st in sorted(v0.index):
        print(f"  {st}: {v0[st]:.0%} -> {vb[st]:.0%}")
    print("\npor continente (ganador): ")
    print(results[best].groupby("cont")[["hit", "ae"]].mean().round(3).to_string())
    results[best].to_csv(os.path.join(D, "lab_best_detail.csv"), index=False)
    print(f"\ndetalle: data/lab_best_detail.csv")

    # DUMP del sesgo vigente (V2 adoptada 2026-07-09): media de (pred_V0 − real) de los últimos
    # 60 días por estación → lo consumen accumulate_predictions y el dashboard.
    # Re-correr este lab SEMANALMENTE refresca la corrección (la ventana es móvil).
    import json
    asof = max(d for (_, d) in base.keys())
    bias = {}
    for st in {k[0] for k in base.keys()}:
        pts = [(mu - y) for (dd, mu, y) in err.get(st, []) if (asof - dd).days < BIAS_WINDOW]
        if len(pts) >= 10:
            bias[st] = round(float(np.mean(pts)), 3)
    out = {"asof": asof.isoformat(), "window_days": BIAS_WINDOW, "variant": "V2",
           "note": "mu_corregido = mu_EMOS - bias[station]; V2 gano en walk-forward 60d (hit 39->43%)",
           "bias": bias}
    json.dump(out, open(os.path.join(D, "station_bias.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("sesgo vigente -> data/station_bias.json:", bias)


if __name__ == "__main__":
    main()
