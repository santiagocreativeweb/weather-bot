#!/usr/bin/env python3
# scripts/lab_v8.py -- EXPERIMENTO V8: los 8 modelos DENTRO del framework EMOS (lo que V6 dejo
# pendiente) + SELECCION de modelos POR ESTACION. [Creado 2026-07-11.]
#
# MOTIVACION: V6 probo 8 modelos en consenso PLANO -> +3.7pp no significativo. Lo correcto es
# probarlos dentro de EMOS (pesos 1/MSE via fit_emos), que degrada solo a los modelos malos.
# Ademas la autopsia del 11/07 mostro ICON arruinando RCSS (outlier +3.4) y GEFS arruinando RKSI
# (-6.5): SACAR el modelo malo de cada estacion puede valer mas que agregar buenos.
#
# DATOS (solo cache, no descarga nada):
#   data/lab_m.csv        gefs/ecmwf/icon  (point-in-time previous_day1/2, 04-09..07-10)
#   data/lab_m_extra.csv  meteofrance/gem/ukmo/jma/knmi (idem, 04-09..07-08; ukmo falta ~34%)
#   Reales: obs.csv (tmax float, hasta 07-01) PRIORIZA + backfill_check.csv max_real (julio).
#
# FRAMEWORK INTERNO (necesario porque forecasts.csv NO tiene s2 de los 5 modelos nuevos):
#   - fits EMOS (wxbt.calibration.fit_emos) sobre el PROPIO cache lab_m(+extra), en ANOMALIAS
#     vs climatologia kernel-DOY de obs<=cut (invariante #5: EMOS calibra anomalias).
#   - s2 por (station, model) = MSE historico walk-forward (targets <= cut, leads 2+3 pooled).
#   - cutoffs de refit CUTS = [04-24, 05-09, 06-09]; el 04-24 (temprano, fit con ~30 muestras)
#     existe SOLO para calentar la serie de errores del bias rolling antes del eval.
#   - TODAS las variantes (incluida la V2-control de 3 modelos) usan este mismo framework ->
#     la comparacion es pareja; el "V2 real" (fits de forecasts.csv, lab_v7) se imprime como
#     ancla de nivel, no entra al test.
#
# VARIANTES (todas + bias rolling 60d walk-forward sobre su PROPIO mu pre-bias, mecanismo V2):
#   V2  EMOS 3 modelos (gefs/ecmwf/icon)                                  -- control/campeon.
#   V8a EMOS 8 modelos (pesos 1/MSE dentro de fit_emos).
#   V8b EMOS con SELECCION por estacion: en cada refit, subset de 3-5 modelos con menor MSE
#       historico (solo pasado) del pool de 8; k elegido por MSE del mix 1/MSE en el train.
#   V8c V8b + drop dinamico de outliers: si un modelo se aparta >2.5 sigma del resto ese dia
#       (std con piso 0.6C/1.1F), se excluye ESE dia (min 3 modelos). Ataca ICON/RCSS.
#   Ausencias toleradas: prediccion con subset disponible >=3; si una variante V8 no llega a 3
#   modelos ese dia (p.ej. subset con ukmo faltante, o targets 07-09/10 sin modelos extra) cae
#   en FALLBACK al mu de V2 de ese dia (flag fb=1) -> mismo set de (station,target) en todas.
#
# RESOLUCION (regla REAL, WU FLOOREA): pick = floor(mu); prob al bucket = bucket_prob(mu-0.5,
# sigma, lo, hi) de wxbt/market.py; ganador = floor(max_real). Buckets: F pares par-impar
# (lo=floor si par, sino floor-1; hi=lo+1), C 1 grado. Metricas: hit exacto, top2, top3, MAE.
# Eval 2026-05-10..07-10, lead 2 principal (lead 3 como robustez). McNemar mejor-V8 vs V2,
# global + por estacion (fuertes KORD/LEMD/LIMC vs debiles RCSS/ZSPD/RKSI/KLGA).
#
# CAVEAT bug #5: NIVELES de hit optimistas (frescura no declarada de previous_day1, common-mode
# entre variantes) -> solo el RELATIVO V8x vs V2 es valido. ASCII prints (consola cp1252).
import os, sys, re, math
import datetime as dt
from math import comb

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from wxbt import config as C                     # noqa: E402
from wxbt.engine import _fit_clim, clim_val      # noqa: E402
from wxbt.calibration import fit_emos, predict   # noqa: E402
from wxbt.market import bucket_prob              # noqa: E402
from show_live import STATIONS                   # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "data")
LAB_M = os.path.join(D, "lab_m.csv")
LAB_M_EXTRA = os.path.join(D, "lab_m_extra.csv")
DETAIL = os.path.join(D, "lab_v8_detail.csv")
SUMMARY = os.path.join(D, "lab_v8_summary.csv")

BASE = ["gefs", "ecmwf", "icon"]
EXTRA = ["meteofrance", "gem", "ukmo", "jma", "knmi"]
ALL8 = BASE + EXTRA
VARIANTS = ["V2", "V8a", "V8b", "V8c"]
WEAK = ["RCSS", "ZSPD", "RKSI", "KLGA"]          # debiles (autopsias 07/11)
STRONG = ["KORD", "LEMD", "LIMC"]                # fuertes: NO romper

D0_WARM, D0_EVAL, D1 = dt.date(2026, 4, 9), dt.date(2026, 5, 10), dt.date(2026, 7, 10)
CUTS = [dt.date(2026, 4, 24), dt.date(2026, 5, 9), dt.date(2026, 6, 9)]
LD_VAR = {2: 1.5, 3: 2.5}                        # lead decimal p/ termino de varianza (= calib_lab)
BIAS_WIN, BIAS_MIN_N = 60, 10                    # = V2 adoptada
SEL_KS, SEL_MIN_N = (3, 4, 5), 20                # V8b: tamanos de subset y n minimo por modelo
OUT_Z = 2.5                                      # V8c: umbral de outlier diario
OUT_STD_FLOOR = {"C": 0.6, "F": 1.1}             # piso del std entre modelos (evita drops espurios)
S2_DEFAULT = {"C": 2.0, "F": 6.5}                # fallback si n<10 para el MSE walk-forward
P_SIG = 0.10


# ---------------------------------------------------------------- buckets / scoring (regla FLOOR)
def pred_bucket_floor(mu, unit):
    f = int(math.floor(mu))
    if unit == "F":
        lo = f if f % 2 == 0 else f - 1
        return (lo, lo + 1)
    return (f, f)


def bucket_grid(mu, unit, span=8):
    center = int(round(mu))
    out = []
    if unit == "F":
        lo = (center - span) - ((center - span) % 2)
        while lo <= center + span:
            out.append((lo, lo + 1)); lo += 2
    else:
        for b in range(center - span, center + span + 1):
            out.append((b, b))
    return out


def ranked_buckets(mu, sigma, unit):
    """Buckets ordenados por prob floor-consistente desc = bucket_prob(mu-0.5, sigma, lo, hi)."""
    return sorted(bucket_grid(mu, unit), key=lambda b: -bucket_prob(mu - 0.5, sigma, b[0], b[1]))


def parse_win(w):
    nums = [int(x) for x in re.findall(r"\d+", str(w))]
    if not nums:
        return None
    if "higher" in str(w) or ">=" in str(w):
        return (nums[0], None)
    if "below" in str(w) or "<=" in str(w):
        return (None, nums[0])
    return (nums[0], nums[1]) if len(nums) >= 2 else (nums[0], nums[0])


def hit_mkt_floor(mu, unit, wb):
    pb = pred_bucket_floor(mu, unit)
    if wb[1] is None:
        return int(pb[0] >= wb[0])
    if wb[0] is None:
        return int(pb[1] <= wb[1])
    return int(pb == wb)


def mcnemar_exact(b, c):
    """p-valor exacto (binomial two-sided) sobre pares discordantes."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(comb(n, i) for i in range(0, k + 1)) * (0.5 ** n)
    return min(1.0, 2.0 * p)


def cut_for(d):
    return max(c for c in CUTS if c < d)


# ---------------------------------------------------------------- datos
def load_m():
    for f in (LAB_M, LAB_M_EXTRA):
        if not os.path.exists(f):
            sys.exit(f"[ERROR] falta {os.path.relpath(f)} -- correr calib_lab.py / lab_v6.py antes.")
    m = pd.read_csv(LAB_M, parse_dates=["target"])
    e = pd.read_csv(LAB_M_EXTRA, parse_dates=["target"])
    M = pd.concat([m, e], ignore_index=True)
    M["target"] = M["target"].dt.date
    return M


def load_reales():
    obs = pd.read_csv(f"{D}/obs.csv", parse_dates=["date"]); obs["date"] = obs["date"].dt.date
    bk = pd.read_csv(f"{D}/backfill_check.csv"); bk["target"] = pd.to_datetime(bk["target"]).dt.date
    obs_f = {(r.station, r.date): float(r.tmax) for r in obs.itertuples()}
    obs_i = {(r.station, r.date): float(r.tmax_int) for r in obs.itertuples()}
    real_map, win_map = {}, {}
    for lead in (2, 3):
        bl = bk[bk.lead == lead]
        real_map[lead] = {(r.station, r.target): float(r.max_real)
                          for r in bl.itertuples() if not pd.isna(r.max_real)}
        real_map[lead].update(obs_f)     # obs.csv (float) PRIORIZA; backfill llena julio
        win_map[lead] = {(r.station, r.target): r.win_mkt
                         for r in bl.itertuples() if isinstance(r.win_mkt, str)}
    return obs, obs_f, obs_i, real_map, win_map


# ---------------------------------------------------------------- fits walk-forward internos
_clim_memo = {}


def clim_at(clims, cut, st, d):
    key = (cut, st, d)
    if key not in _clim_memo:
        coef = clims[cut].get(st)
        _clim_memo[key] = None if coef is None else clim_val(coef, d)
    return _clim_memo[key]


def build_clims(obs):
    clims = {}
    for cut in CUTS:
        clims[cut] = _fit_clim(obs, [d for d in obs["date"].unique() if d <= cut])
    return clims


def build_s2(M, obs_f):
    """s2[cut][(st,model)] = MSE walk-forward (targets<=cut, leads 2+3, vs obs float).
    Fallbacks: media de s2 de la estacion -> default por unidad. Tambien devuelve
    hist[cut][(st,model)] = (mse, n) para la seleccion V8b."""
    E = M.copy()
    E["y"] = [obs_f.get((st, d)) for st, d in zip(E["station"], E["target"])]
    E = E.dropna(subset=["y"])
    E["sq"] = (E["m"] - E["y"]) ** 2
    s2maps, hist = {}, {}
    for cut in CUTS:
        sub = E[E["target"] <= cut]
        g = sub.groupby(["station", "model"])["sq"].agg(["mean", "count"])
        smap, h = {}, {}
        for st in STATIONS:
            unit = STATIONS[st][3]
            per_st = {}
            for model in ALL8:
                if (st, model) in g.index:
                    mse, n = g.loc[(st, model)]
                    h[(st, model)] = (float(mse), int(n))
                    if n >= 10:
                        per_st[model] = max(float(mse), 1e-3)
            fb = float(np.mean(list(per_st.values()))) if per_st else S2_DEFAULT[unit]
            for model in ALL8:
                smap[(st, model)] = per_st.get(model, fb)
        s2maps[cut], hist[cut] = smap, h
    return s2maps, hist


def select_subsets(piv23, hist, obs_f):
    """V8b: subset de 3-5 modelos por (cut, station), SOLO con pasado (targets<=cut).
    Ranking por MSE historico; k elegido por MSE del mix ponderado 1/MSE en el train."""
    sel = {}
    for cut in CUTS:
        for st in STATIONS:
            h = hist[cut]
            elig = sorted([m for m in ALL8 if h.get((st, m), (0, 0))[1] >= SEL_MIN_N],
                          key=lambda m: h[(st, m)][0])
            if len(elig) < 3:
                sel[(cut, st)] = list(BASE)
                continue
            try:
                g = piv23.loc[st]
            except KeyError:
                sel[(cut, st)] = list(BASE); continue
            best = None
            for k in SEL_KS:
                if k > len(elig):
                    break
                S = elig[:k]
                w = {m: 1.0 / max(h[(st, m)][0], 1e-3) for m in S}
                sq, cov = [], 0
                for (lead, tgt), row in g.iterrows():
                    if tgt > cut:
                        continue
                    y = obs_f.get((st, tgt))
                    if y is None:
                        continue
                    avail = [m for m in S if not pd.isna(row.get(m))]
                    if len(avail) < 3:
                        continue
                    ws = sum(w[m] for m in avail)
                    mix = sum(w[m] * float(row[m]) for m in avail) / ws
                    sq.append((mix - y) ** 2); cov += 1
                if len(sq) < 10:
                    continue
                cand = (float(np.mean(sq)), -cov, k, S)
                if best is None or cand[:3] < best[:3]:
                    best = cand
            sel[(cut, st)] = best[3] if best else list(BASE)
    return sel


def build_fit(piv23, obs_i, clims, s2maps, cut, st, pool):
    """fit_emos en ANOMALIAS (invariante #5) con pool restringido; leads 2+3 pooled (= engine)."""
    try:
        g = piv23.loc[st]
    except KeyError:
        return None
    rows = []
    for (lead, tgt), row in g.iterrows():
        if tgt > cut:
            continue
        y = obs_i.get((st, tgt))
        if y is None:
            continue
        c = clim_at(clims, cut, st, tgt)
        if c is None:
            continue
        pm = {}
        for model in pool:
            v = row.get(model)
            if pd.isna(v):
                continue
            pm[model] = (float(v) - c, s2maps[cut][(st, model)])
        if len(pm) < 3:
            continue
        rows.append({"y": float(y) - c, "ld": float(lead), "per_model": pm})
    if len(rows) < 25:
        return None
    return fit_emos(rows, C.SIGMA_FLOOR[STATIONS[st][3]])


# ---------------------------------------------------------------- prediccion por variante
def drop_outliers(vals, unit):
    """V8c: modelos a >OUT_Z sigmas del resto ese dia (std con piso). Conserva >=3 modelos."""
    ks = list(vals)
    if len(ks) < 4:
        return []
    flagged = []
    for k in ks:
        others = [vals[j] for j in ks if j != k]
        z = abs(vals[k] - float(np.mean(others))) / max(float(np.std(others)), OUT_STD_FLOOR[unit])
        if z > OUT_Z:
            flagged.append((z, k))
    flagged.sort(reverse=True)
    dropped, keep = [], set(ks)
    for z, k in flagged:
        if len(keep) <= 3:
            break
        keep.discard(k); dropped.append(k)
    return dropped


def predict_one(params, pm, lead, c):
    ks = {k: v for k, v in pm.items() if k in params["w"]}
    if len(ks) < 3:
        return None
    pr = predict(params, ks, ld=LD_VAR[lead])
    if pr is None:
        return None
    return c + pr[0], pr[1], len(ks)


def build_predictions(piv_lead, lead, fits, sel, clims, s2maps):
    """res[variant][(st,d)] = dict(mu, sigma, nmod, fb, ndrop). Set COMUN = donde V2 computa.
    V8b/V8c caen al mu de V2 (fb=1) si su subset no llega a 3 modelos ese dia."""
    res = {v: {} for v in VARIANTS}
    for (st, d), row in piv_lead.iterrows():
        if d <= CUTS[0]:
            continue
        cut = cut_for(d)
        c = clim_at(clims, cut, st, d)
        if c is None:
            continue
        unit = STATIONS[st][3]

        def make_pm(pool):
            pm = {}
            for model in pool:
                v = row.get(model)
                if not pd.isna(v):
                    pm[model] = (float(v) - c, s2maps[cut][(st, model)])
            return pm

        pars2 = fits["V2"].get((cut, st))
        pm2 = make_pm(BASE)
        if pars2 is None or len(pm2) < 3:
            continue                                   # sin V2 no hay fila (set comun)
        r2 = predict_one(pars2, pm2, lead, c)
        if r2 is None:
            continue
        res["V2"][(st, d)] = dict(mu=r2[0], sigma=r2[1], nmod=r2[2], fb=0, ndrop=0)

        # V8a: EMOS 8 modelos, tolera ausencia (>=3 con peso)
        pars_a = fits["V8a"].get((cut, st))
        ra = predict_one(pars_a, make_pm(ALL8), lead, c) if pars_a else None
        res["V8a"][(st, d)] = (dict(mu=ra[0], sigma=ra[1], nmod=ra[2], fb=0, ndrop=0) if ra
                               else dict(mu=r2[0], sigma=r2[1], nmod=r2[2], fb=1, ndrop=0))

        # V8b: subset seleccionado por estacion
        S = sel[(cut, st)]
        pars_b = fits["V8b"].get((cut, st))
        pm_b = make_pm(S)
        rb = predict_one(pars_b, pm_b, lead, c) if pars_b else None
        res["V8b"][(st, d)] = (dict(mu=rb[0], sigma=rb[1], nmod=rb[2], fb=0, ndrop=0) if rb
                               else dict(mu=r2[0], sigma=r2[1], nmod=r2[2], fb=1, ndrop=0))

        # V8c: V8b + drop diario de outliers (sobre los m crudos del subset disponible)
        rc = None; nd = 0
        if pars_b:
            vals = {m: float(row[m]) for m in S if not pd.isna(row.get(m))}
            dropped = drop_outliers(vals, unit)
            nd = len(dropped)
            pm_c = {k: v for k, v in pm_b.items() if k not in dropped}
            rc = predict_one(pars_b, pm_c, lead, c)
        res["V8c"][(st, d)] = (dict(mu=rc[0], sigma=rc[1], nmod=rc[2], fb=0, ndrop=nd) if rc
                               else dict(mu=r2[0], sigma=r2[1], nmod=r2[2], fb=1, ndrop=0))
    return res


# ---------------------------------------------------------------- bias rolling + scoring
def build_err(res_v, real_map_l):
    err = {}
    for (st, d), r in res_v.items():
        y = real_map_l.get((st, d))
        if y is not None:
            err.setdefault(st, []).append((d, r["mu"], y))
    for st in err:
        err[st].sort()
    return err


def rolling_bias(err, st, d):
    pts = [(mu - y) for (dd, mu, y) in err.get(st, []) if dd < d and (d - dd).days <= BIAS_WIN]
    return float(np.mean(pts)) if len(pts) >= BIAS_MIN_N else 0.0


def score_variant(name, res_v, err, real_map_l, win_map_l, lead):
    rows = []
    for (st, d), r in sorted(res_v.items()):
        if not (D0_EVAL <= d <= D1):
            continue
        y = real_map_l.get((st, d))
        if y is None:
            continue
        unit = STATIONS[st][3]
        mu = r["mu"] - rolling_bias(err, st, d)          # bias V2 encima, walk-forward
        sg = r["sigma"]
        tb = pred_bucket_floor(y, unit)                  # ganador = floor(max_real)
        pb = pred_bucket_floor(mu, unit)
        rk = ranked_buckets(mu, sg, unit)
        rec = dict(variant=name, lead=lead, st=st, unit=unit, d=d, mu=round(mu, 2),
                   sigma=round(sg, 2), nmod=r["nmod"], fb=r["fb"], ndrop=r["ndrop"],
                   ae=abs(mu - y), e=mu - y, hit=int(pb == tb),
                   top2=int(tb in rk[:2]), top3=int(tb in rk[:3]),
                   pwin=bucket_prob(mu - 0.5, sg, tb[0], tb[1]))
        w = win_map_l.get((st, d))
        if isinstance(w, str):
            wb = parse_win(w)
            if wb is not None:
                rec["hit_mkt"] = hit_mkt_floor(mu, unit, wb)
        rows.append(rec)
    return pd.DataFrame(rows)


def agg(df):
    return dict(hit=df.hit.mean(), top2=df.top2.mean(), top3=df.top3.mean(), mae=df.ae.mean(),
                bias=df.e.mean(), pwin=df.pwin.mean(),
                hit_mkt=df.hit_mkt.mean() if "hit_mkt" in df else float("nan"),
                fb=int(df.fb.sum()), ndrop=int(df.ndrop.sum()), n=int(len(df)))


def mcnemar_vs(df_a, df_b):
    """pares discordantes de hit (df_a = variante, df_b = V2) sobre el set comun."""
    j = df_a[["st", "d", "hit"]].rename(columns={"hit": "ha"}).merge(
        df_b[["st", "d", "hit"]].rename(columns={"hit": "hb"}), on=["st", "d"])
    b = int(((j.ha == 1) & (j.hb == 0)).sum())
    c = int(((j.ha == 0) & (j.hb == 1)).sum())
    return b, c, mcnemar_exact(b, c)


# ---------------------------------------------------------------- main
def main():
    print("1) cargando caches (lab_m + lab_m_extra) y reales...")
    M = load_m()
    obs, obs_f, obs_i, real_map, win_map = load_reales()
    cov = M[M.lead == 2].groupby("model")["m"].count().reindex(ALL8)
    print("   cobertura lead-2 por modelo:", "  ".join(f"{m}={int(n)}" for m, n in cov.items()))
    print(f"   targets {M.target.min()}..{M.target.max()} (extra corta 07-08: los dias 07-09/10")
    print("   corren solo con los 3 base -> V8b/c fallback, V8a subset=3; tolerado por diseno)")

    print("2) climatologias + s2 walk-forward (MSE por station/model, targets<=cut)...")
    clims = build_clims(obs)
    s2maps, hist = build_s2(M, obs_f)

    piv23 = M.pivot_table(index=["station", "lead", "target"], columns="model",
                          values="m", aggfunc="last")
    print("3) seleccion V8b por (cut, station) -- solo pasado...")
    sel = select_subsets(piv23, hist, obs_f)
    print("   subset elegido por estacion (cut 04-24 | 05-09 | 06-09):")
    for st in sorted(STATIONS):
        subs = [",".join(sel[(cut, st)]) for cut in CUTS]
        drop8 = [m for m in ALL8 if m not in sel[(CUTS[-1], st)]]
        print(f"     {st}: {subs[0]} | {subs[1]} | {subs[2]}   (fuera al 06-09: {','.join(drop8) or '-'})")

    print("4) fits EMOS por cutoff (V2=3mod, V8a=8mod, V8b=subset)...")
    fits = {"V2": {}, "V8a": {}, "V8b": {}}
    nfail = 0
    for cut in CUTS:
        for st in STATIONS:
            fits["V2"][(cut, st)] = build_fit(piv23, obs_i, clims, s2maps, cut, st, BASE)
            fits["V8a"][(cut, st)] = build_fit(piv23, obs_i, clims, s2maps, cut, st, ALL8)
            fits["V8b"][(cut, st)] = build_fit(piv23, obs_i, clims, s2maps, cut, st, sel[(cut, st)])
            nfail += sum(fits[v][(cut, st)] is None for v in fits)
    print(f"   fits nulos (muestra corta): {nfail} de {3 * len(CUTS) * len(STATIONS)}")

    all_detail, lead_res = [], {}
    for lead in (2, 3):
        piv_lead = M[M.lead == lead].pivot_table(index=["station", "target"], columns="model",
                                                 values="m", aggfunc="last")
        res = build_predictions(piv_lead, lead, fits, sel, clims, s2maps)
        dfs = {}
        for v in VARIANTS:
            err = build_err(res[v], real_map[lead])
            dfs[v] = score_variant(v, res[v], err, real_map[lead], win_map[lead], lead)
            all_detail.append(dfs[v])
        lead_res[lead] = dfs
        print(f"\n=== LEAD {lead}  (eval {D0_EVAL}..{D1}, walk-forward, floor-scoring) ===")
        print(f"{'variante':>9} {'hit':>7} {'top2':>7} {'top3':>7} {'MAE':>7} {'sesgo':>7} "
              f"{'pwin':>7} {'hit_mkt':>8} {'fb':>4} {'drops':>6} {'n':>5} {'p_vsV2':>7}")
        for v in VARIANTS:
            a = agg(dfs[v])
            pv = "-" if v == "V2" else f"{mcnemar_vs(dfs[v], dfs['V2'])[2]:.3f}"
            print(f"{v:>9} {a['hit']:>6.1%} {a['top2']:>6.1%} {a['top3']:>6.1%} {a['mae']:>7.2f} "
                  f"{a['bias']:>+7.2f} {a['pwin']:>7.3f} {a['hit_mkt']:>7.1%} {a['fb']:>4} "
                  f"{a['ndrop']:>6} {a['n']:>5} {pv:>7}")

    # ---- veredicto sobre LEAD 2 ----
    dfs2 = lead_res[2]
    best = max(["V8a", "V8b", "V8c"], key=lambda v: (agg(dfs2[v])["hit"], -agg(dfs2[v])["mae"]))
    a2, ab = agg(dfs2["V2"]), agg(dfs2[best])
    b, c, p = mcnemar_vs(dfs2[best], dfs2["V2"])
    print("\n" + "=" * 78)
    print(f"LEAD 2 -- mejor V8: {best}   delta hit = {ab['hit'] - a2['hit']:+.1%}   "
          f"delta MAE = {ab['mae'] - a2['mae']:+.2f}")
    print(f"McNemar {best} vs V2: {best} si & V2 no = {b}  |  V2 si & {best} no = {c}  "
          f"|  p exacto (binomial 2-sided) = {p:.3f}")

    print(f"\npor estacion (LEAD 2) [hit V2 -> {best} | MAE V2 -> {best} | McNemar b/c p]:")
    summary_rows = []
    for grp, label in [(STRONG, "FUERTES (no romper)"), (WEAK, "DEBILES"),
                       ([s for s in sorted(STATIONS) if s not in WEAK + STRONG], "resto")]:
        print(f"  -- {label} --")
        for st in grp:
            s2 = dfs2["V2"][dfs2["V2"].st == st]; sb = dfs2[best][dfs2[best].st == st]
            if not len(s2):
                continue
            bb, cc, pp = mcnemar_vs(sb, s2)
            dh = sb.hit.mean() - s2.hit.mean()
            mk = "+" if dh > 1e-9 else ("-" if dh < -1e-9 else "=")
            print(f"    {st}: {s2.hit.mean():>4.0%} -> {sb.hit.mean():>4.0%} [{mk}{dh:+.0%}]  |  "
                  f"MAE {s2.ae.mean():.2f} -> {sb.ae.mean():.2f}  |  {bb}/{cc} p={pp:.2f}")
    for grp, label in [(STRONG, "fuertes"), (WEAK, "debiles")]:
        s2 = dfs2["V2"][dfs2["V2"].st.isin(grp)]; sb = dfs2[best][dfs2[best].st.isin(grp)]
        bb, cc, pp = mcnemar_vs(sb, s2)
        print(f"  grupo {label}: hit {s2.hit.mean():.1%} -> {sb.hit.mean():.1%}  "
              f"McNemar {bb}/{cc} p={pp:.2f}")

    # ancla de nivel: V2 "real" del lab_v7 (fits forecasts.csv) si existe
    v7s = os.path.join(D, "lab_v7_summary.csv")
    if os.path.exists(v7s):
        try:
            v7 = pd.read_csv(v7s)
            r = v7[(v7.variant == "V2") & (v7.lead == 2)].iloc[0]
            print(f"\nancla de nivel -- V2 real (lab_v7, fits forecasts.csv): hit={r.hit:.1%} "
                  f"top3={r.top3:.1%} MAE={r.mae:.2f} n={int(r.n)}  (framework distinto: comparar"
                  f" niveles con cautela; el test valido es V8x vs V2 INTERNOS)")
        except Exception:
            pass

    # robustez lead 3 (NO operativo: el bot decide con lead 2; se reporta como observacion)
    dfs3 = lead_res[3]
    b3, c3, p3 = mcnemar_vs(dfs3[best], dfs3["V2"])
    a23_v = agg(pd.concat([dfs2["V2"], dfs3["V2"]])); a23_b = agg(pd.concat([dfs2[best], dfs3[best]]))
    jp = pd.concat([dfs2[best], dfs3[best]])[["st", "d", "lead", "hit"]].rename(columns={"hit": "ha"}) \
        .merge(pd.concat([dfs2["V2"], dfs3["V2"]])[["st", "d", "lead", "hit"]].rename(columns={"hit": "hb"}),
               on=["st", "d", "lead"])
    bp = int(((jp.ha == 1) & (jp.hb == 0)).sum()); cp = int(((jp.ha == 0) & (jp.hb == 1)).sum())
    print(f"\nrobustez LEAD 3 -- {best} vs V2: delta hit = "
          f"{agg(dfs3[best])['hit'] - agg(dfs3['V2'])['hit']:+.1%}  McNemar {b3}/{c3} p={p3:.3f}")
    print(f"pooled leads 2+3 -- {best} vs V2: hit {a23_v['hit']:.1%} -> {a23_b['hit']:.1%}  "
          f"McNemar {bp}/{cp} p={mcnemar_exact(bp, cp):.3f}")

    # veredicto formal
    strong_ok = all(dfs2[best][dfs2[best].st == st].hit.mean()
                    >= dfs2["V2"][dfs2["V2"].st == st].hit.mean() - 1e-9 for st in STRONG)
    print("\nVEREDICTO:")
    if ab["hit"] > a2["hit"] and p < P_SIG and strong_ok:
        print(f"  {best} SUPERA a V2 con p={p:.3f} (<{P_SIG}) sin empeorar las fuertes -> "
              f"candidata a produccion (validar forward antes de aplicar).")
    else:
        why = []
        if ab["hit"] <= a2["hit"]:
            why.append("no mejora hit")
        if p >= P_SIG:
            why.append(f"p={p:.3f} >= {P_SIG}")
        if not strong_ok:
            why.append("empeora alguna fuerte (KORD/LEMD/LIMC)")
        print(f"  {best} NO alcanza el liston ({'; '.join(why)}) -> MANTENER V2 en produccion.")

    # dumps
    det = pd.concat(all_detail, ignore_index=True)
    det.to_csv(DETAIL, index=False)
    for lead in (2, 3):
        for v in VARIANTS:
            df = lead_res[lead][v]
            bb, cc, pp = mcnemar_vs(df, lead_res[lead]["V2"]) if v != "V2" else (0, 0, 1.0)
            a = agg(df); a.update(variant=v, lead=lead, scope="GLOBAL", mcn_b=bb, mcn_c=cc, mcn_p=pp)
            summary_rows.append(a)
            for st in sorted(df.st.unique()):
                s = df[df.st == st]
                a = agg(s); a.update(variant=v, lead=lead, scope=st,
                                     mcn_b=np.nan, mcn_c=np.nan, mcn_p=np.nan)
                summary_rows.append(a)
    pd.DataFrame(summary_rows).to_csv(SUMMARY, index=False)
    print(f"\ndetalle -> {os.path.relpath(DETAIL)}   resumen -> {os.path.relpath(SUMMARY)}")
    print("CAVEAT bug #5: niveles OPTIMISTAS (frescura previous_day1, common-mode entre variantes);")
    print("solo el RELATIVO V8x vs V2 es valido. Fits/s2/seleccion/bias: 100% walk-forward (<target).")


if __name__ == "__main__":
    main()
