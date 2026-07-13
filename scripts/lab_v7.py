#!/usr/bin/env python3
# scripts/lab_v7.py -- EXPERIMENTO V7 "regime-aware": ataca el APILAMIENTO de error del calibrador
# V2 (EMOS + bias rolling 60d) en cortes de REGIMEN. [Creado 2026-07-11.]
#
# AUTOPSIA (RCSS Taipei 11/07, bot 31.4 vs real 28): clim julio ~35 (caliente). Consenso crudo
# 28.9 (dia 6 mas frio que la clim). EMOS con pendiente b<1 (b=0.81) ENCOGE la anomalia hacia la
# clim -> +1.16. bias V2 = -1.433 -> mu -= bias = +1.43 MAS en la MISMA direccion. Se APILAN ~2.6
# de sobre-prediccion. El bias CONSTANTE no puede corregir esto: mide el residuo medio de 60d, pero
# el error es CONDICIONAL al REGIMEN (signo/magnitud de la anomalia del dia). Mismo patron en
# costeros de Asia (brisa marina, dias frios) y en olas de calor (KLGA).
#
# OBJETIVO: walk-forward que pruebe si alguna variante "regime-aware" mejora HIT EXACTO / MAE /
# top2 / top3 sobre V2 SIN empeorar las estaciones que hoy andan bien (KORD/LEMD/LIMC).
#
# DATOS: reusa el cache data/lab_m.csv (m point-in-time via Previous-Runs, columnas
# temperature_2m_previous_day1/2 = leads 2/3; NUNCA temperature_2m = nowcast bug #5). Reales:
# obs.csv (hasta 07-01, tmax float) PRIORIZA + backfill_check.csv max_real (julio, hasta 07-10).
# Anti-look-ahead: fits EMOS por cutoff mensual (solo targets <= cut); bias/blend rolling solo dias
# < target. Eval 2026-05-10..07-10.
#
# RESOLUCION (regla REAL, WU FLOOREA): pick = floor(mu); bucket floor-consistente; prob al bucket =
# bucket_prob(mu-0.5, sigma, lo, hi) de wxbt/market.py; ganador = floor(max_real). Buckets: F pares
# par-impar (lo=floor si par, sino floor-1; hi=lo+1), C 1 grado (lo=hi=floor).
#
# VARIANTES (todas floor-scored):
#   V0  crudo equiponderado (mean gefs/ecmwf/icon), sigma raw.
#   V2  EMOS + bias rolling 60d (el CAMPEON a batir).
#   V7a "cap del empuje": limita |mu_V2 - mu_crudo| a un tope (1.0/1.5/2.0); recorta hacia el crudo.
#   V7b "bias condicional al signo": aplica el bias SOLO cuando su correccion (-bias) refuerza la
#       anomalia del dia (= OPONE al shrink del EMOS). En regimen opuesto -> bias 0 (o reducido).
#   V7c "blend con crudo por desacuerdo": w = min(|anom|/K, wmax); mu = (1-w)*mu_V2 + w*mu_crudo.
#       Cuanto mas disienten los modelos de la clim, mas peso al crudo (menos shrink).
#   V7d combinacion de la mejor gate + el mejor cap.
#
# CAVEAT bug #5: los NIVELES de hit son optimistas (frescura no declarada de previous_day1,
# common-mode entre variantes). Solo la comparacion RELATIVA V7 vs V2 es valida. ASCII prints.
import os, sys, re, math, json
import datetime as dt
from math import comb
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from wxbt import config as C                          # noqa: E402
from wxbt.engine import fit_all, clim_val, _lead_day  # noqa: E402
from wxbt.calibration import predict, predict_raw     # noqa: E402
from wxbt.market import bucket_prob                    # noqa: E402
from show_live import STATIONS                         # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "data")
LAB_M = os.path.join(D, "lab_m.csv")
DETAIL = os.path.join(D, "lab_v7_detail.csv")
SUMMARY = os.path.join(D, "lab_v7_summary.csv")
CONT = {"KLGA": "America", "KORD": "America", "EGLC": "Europa", "LFPB": "Europa",
        "LEMD": "Europa", "EDDM": "Europa", "LIMC": "Europa", "RJTT": "Asia",
        "RKSI": "Asia", "ZSPD": "Asia", "ZBAA": "Asia", "RCSS": "Asia"}
# grupos del interes del pedido: regime-sensibles (debiles) vs fuertes que NO hay que romper
WEAK = ["RCSS", "ZSPD", "RKSI", "KLGA"]
STRONG = ["KORD", "LEMD", "LIMC"]
MODELS = ["gefs", "ecmwf", "icon"]
D0_WARM, D0_EVAL, D1 = dt.date(2026, 4, 9), dt.date(2026, 5, 10), dt.date(2026, 7, 10)
CUTS = [dt.date(2026, 4, 8), dt.date(2026, 5, 9), dt.date(2026, 6, 9)]  # re-fit mensual
LD_VAR = {2: 1.5, 3: 2.5}   # lead-en-dias para el termino de varianza de predict (= calib_lab lead2)

# grillas de hiperparametros a barrer por familia
CAPS = [1.0, 1.5, 2.0]                 # V7a
GATE_RED = [0.0, 0.5]                  # V7b: factor del bias en regimen opuesto (0=apagar, 0.5=mitad)
BLEND_K = [3.0, 5.0, 8.0]              # V7c: escala de |anom| que satura el peso del crudo
BLEND_WMAX = [0.7, 1.0]               # V7c: peso maximo del crudo


# ---------------------------------------------------------------- buckets / scoring (regla FLOOR)
def pred_bucket_floor(mu, unit):
    f = int(math.floor(mu))
    if unit == "F":
        lo = f if f % 2 == 0 else f - 1
        return (lo, lo + 1)
    return (f, f)


def true_bucket_floor(max_real, unit):
    return pred_bucket_floor(max_real, unit)   # ganador = floor(max_real), mismo esquema de bucket


def bucket_grid(mu, unit, span=8):
    """Buckets candidatos (lo,hi) alrededor de mu en el esquema de la unidad (para ranking top-k)."""
    center = int(round(mu))
    out = []
    if unit == "F":
        lo0 = center - span
        lo0 -= lo0 % 2                       # arrancar en par (py: (-3)%2==1 -> par hacia abajo)
        lo = lo0
        while lo <= center + span:
            out.append((lo, lo + 1)); lo += 2
    else:
        for b in range(center - span, center + span + 1):
            out.append((b, b))
    return out


def ranked_buckets(mu, sigma, unit):
    """Buckets ordenados por prob floor-consistente desc = bucket_prob(mu-0.5, sigma, lo, hi)."""
    g = bucket_grid(mu, unit)
    return sorted(g, key=lambda b: -bucket_prob(mu - 0.5, sigma, b[0], b[1]))


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
    """Hit exacto floor vs bucket de mercado (win_mkt), con colas abiertas (= lab_v6.hit_floor)."""
    pb = pred_bucket_floor(mu, unit)
    if wb[1] is None:
        return int(pb[0] >= wb[0])
    if wb[0] is None:
        return int(pb[1] <= wb[1])
    return int(pb == wb)


# ---------------------------------------------------------------- carga de datos
def load_reales():
    obs = pd.read_csv(f"{D}/obs.csv", parse_dates=["date"]); obs["date"] = obs["date"].dt.date
    bk = pd.read_csv(f"{D}/backfill_check.csv"); bk["target"] = pd.to_datetime(bk["target"]).dt.date
    real_map, win_map = {}, {}
    for lead in (2, 3):
        bl = bk[bk.lead == lead]
        real_map[lead] = {(r.station, r.target): float(r.max_real)
                          for r in bl.itertuples() if not pd.isna(r.max_real)}
        # obs.csv (tmax float) PRIORIZA; backfill llena julio (obs corta 07-01)
        real_map[lead].update({(r.station, r.date): float(r.tmax) for r in obs.itertuples()})
        win_map[lead] = {(r.station, r.target): r.win_mkt
                         for r in bl.itertuples() if isinstance(r.win_mkt, str)}
    return obs, real_map, win_map


def build_fits(obs):
    """Fits EMOS por cutoff (expanding) + s2 map por (station,model,lead) al ultimo dato <= cut."""
    fc = pd.read_csv(f"{D}/forecasts.csv", parse_dates=["avail", "target"])
    fc["target_d"] = fc["target"].dt.date
    fc_fit = fc[fc.lead_h > 24].copy()                        # SIN lead-1 (nowcast bug #5)
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


def cut_for(d):
    return max(c for c in CUTS if c < d)


def build_base(M, fits, s2maps, lead):
    """base[(st,d)] = dict(c, mu_emos, sigma_emos, mu_raw, sigma_raw, anom). Walk-forward puro.
    mu_emos = EMOS calibrado SIN bias (= pred_V0 de calib_lab, sobre el que se mide el bias rolling).
    mu_raw  = consenso crudo equiponderado (V0 del pedido)."""
    piv = M[M.lead == lead].pivot_table(index=["station", "target"], columns="model",
                                        values="m", aggfunc="last")
    base = {}
    for (st, d), row in piv.iterrows():
        if d <= CUTS[0]:
            continue
        cut = cut_for(d)
        pars = fits[cut].get(st); s2m = s2maps[cut]
        if pars is None:
            continue
        unit = STATIONS[st][3]
        pm = {}
        for model in MODELS:
            v = row.get(model)
            if pd.isna(v):
                continue
            s2 = s2m.get((st, model, lead))
            if s2 is None:
                continue
            pm[model] = (float(v), float(s2))
        if len(pm) < 3:
            continue
        c = clim_val(pars["clim"], d)
        pr = predict(pars["emos"], {k: (m - c, s2) for k, (m, s2) in pm.items()}, ld=LD_VAR[lead])
        if pr is None:
            continue
        raw = predict_raw(pm, C.SIGMA_FLOOR[unit])
        if raw is None:
            continue
        mu_emos, sigma_emos = c + pr[0], pr[1]
        mu_raw, sigma_raw = raw
        base[(st, d)] = dict(unit=unit, c=c, mu_emos=mu_emos, sigma_emos=sigma_emos,
                             mu_raw=mu_raw, sigma_raw=sigma_raw, anom=mu_raw - c)
    return base


def build_err(base, real_map_lead):
    """errores diarios de mu_emos (pred_V0) por estacion para el bias rolling walk-forward."""
    err = {}
    for (st, d), b in base.items():
        y = real_map_lead.get((st, d))
        if y is not None:
            err.setdefault(st, []).append((d, b["mu_emos"], y))
    for st in err:
        err[st].sort()
    return err


def rolling_bias(err, st, d, win=60, minn=10):
    pts = [(mu - y) for (dd, mu, y) in err.get(st, []) if dd < d and (d - dd).days <= win]
    return float(np.mean(pts)) if len(pts) >= minn else 0.0


# ---------------------------------------------------------------- variantes -> (mu, sigma)
def variant_mu(name, b, bias60):
    """Devuelve (mu, sigma) de la variante `name` para un registro base b (con su bias rolling)."""
    mu_emos, sg = b["mu_emos"], b["sigma_emos"]
    mu_raw, anom = b["mu_raw"], b["anom"]
    corr = -bias60                                   # lo que V2 SUMA a mu_emos (mu_V2 = mu_emos+corr)
    mu_v2 = mu_emos + corr
    if name == "V0":
        return b["mu_raw"], b["sigma_raw"]
    if name == "V2":
        return mu_v2, sg
    if name.startswith("V7a"):
        cap = float(name.split("_")[1])
        push = mu_v2 - mu_raw
        if abs(push) > cap:
            push = math.copysign(cap, push)
        return mu_raw + push, sg
    if name.startswith("V7b"):
        red = float(name.split("_")[1])
        # aplicar corr completo solo si REFUERZA la anomalia del dia (sign(corr)==sign(anom)),
        # es decir cuando OPONE al shrink del EMOS. En regimen opuesto -> corr*red (0 o 0.5).
        aligned = (corr == 0.0) or (anom == 0.0) or (math.copysign(1, corr) == math.copysign(1, anom))
        g = 1.0 if aligned else red
        return mu_emos + g * corr, sg
    if name.startswith("V7c"):
        _, ks, ws = name.split("_")
        K, wmax = float(ks), float(ws)
        w = min(abs(anom) / K, wmax)
        return (1.0 - w) * mu_v2 + w * mu_raw, sg
    if name.startswith("V7d"):
        # combinacion: gate de signo (apaga bias en regimen opuesto) + cap del empuje residual.
        _, red_s, cap_s = name.split("_")
        red, cap = float(red_s), float(cap_s)
        aligned = (corr == 0.0) or (anom == 0.0) or (math.copysign(1, corr) == math.copysign(1, anom))
        g = 1.0 if aligned else red
        mu = mu_emos + g * corr
        push = mu - mu_raw
        if abs(push) > cap:
            push = math.copysign(cap, push)
        return mu_raw + push, sg
    raise ValueError(name)


# ---------------------------------------------------------------- scoring
def score_variant(name, base, err, real_map_lead, win_map_lead, lead):
    rows = []
    for (st, d), b in base.items():
        if not (D0_EVAL <= d <= D1):
            continue
        unit = b["unit"]
        bias60 = rolling_bias(err, st, d)
        mu, sg = variant_mu(name, b, bias60)
        y = real_map_lead.get((st, d))
        rec = dict(variant=name, lead=lead, st=st, cont=CONT[st], unit=unit, d=d,
                   mu=round(mu, 2), sigma=round(sg, 2), anom=round(b["anom"], 2))
        if y is not None:
            tb = true_bucket_floor(y, unit)
            pb = pred_bucket_floor(mu, unit)
            rk = ranked_buckets(mu, sg, unit)
            rec["ae"] = abs(mu - y); rec["e"] = mu - y
            rec["hit_real"] = int(pb == tb)
            rec["top2"] = int(tb in rk[:2])
            rec["top3"] = int(tb in rk[:3])
            rec["pwin_real"] = bucket_prob(mu - 0.5, sg, tb[0], tb[1])
        w = win_map_lead.get((st, d))
        if isinstance(w, str):
            wb = parse_win(w)
            if wb is not None:
                rec["hit_mkt"] = hit_mkt_floor(mu, unit, wb)
        rows.append(rec)
    return pd.DataFrame(rows)


def agg(df):
    return dict(hit=df.hit_real.mean(), top2=df.top2.mean(), top3=df.top3.mean(),
                mae=df.ae.mean(), bias=df.e.mean(), pwin=df.pwin_real.mean(),
                hit_mkt=df.hit_mkt.mean() if "hit_mkt" in df else float("nan"),
                n=int(df.hit_real.notna().sum()))


def mcnemar_exact(b, c):
    """p-valor exacto (binomial two-sided) para el test de McNemar sobre pares discordantes."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(comb(n, i) for i in range(0, k + 1)) * (0.5 ** n)
    return min(1.0, 2.0 * p)


# ---------------------------------------------------------------- main
def run_lead(lead, M, fits, s2maps, real_map, win_map):
    base = build_base(M, fits, s2maps, lead)
    err = build_err(base, real_map[lead])
    # familias de variantes a barrer
    v7a = [f"V7a_{c}" for c in CAPS]
    v7b = [f"V7b_{r}" for r in GATE_RED]
    v7c = [f"V7c_{k}_{w}" for k in BLEND_K for w in BLEND_WMAX]
    names = ["V0", "V2"] + v7a + v7b + v7c
    dfs = {nm: score_variant(nm, base, err, real_map[lead], win_map[lead], lead) for nm in names}

    # elegir la MEJOR de cada familia por hit_real (desempate: menor MAE), luego V7d con esas piezas
    def best_of(fam):
        cand = [nm for nm in names if nm.startswith(fam)]
        return max(cand, key=lambda nm: (agg(dfs[nm])["hit"], -agg(dfs[nm])["mae"]))
    best_a, best_b, best_c = best_of("V7a"), best_of("V7b"), best_of("V7c")
    red = best_b.split("_")[1]; cap = best_a.split("_")[1]
    v7d = f"V7d_{red}_{cap}"
    dfs[v7d] = score_variant(v7d, base, err, real_map[lead], win_map[lead], lead)
    names.append(v7d)
    return base, err, dfs, names, dict(a=best_a, b=best_b, c=best_c, d=v7d)


def print_table(dfs, names, title):
    print(f"\n=== {title} ===")
    print(f"{'variante':>12} {'hit_real':>9} {'top2':>7} {'top3':>7} {'MAE':>7} "
          f"{'sesgo':>7} {'pwin':>7} {'hit_mkt':>8} {'n':>5}")
    for nm in names:
        a = agg(dfs[nm])
        print(f"{nm:>12} {a['hit']:>8.1%} {a['top2']:>6.1%} {a['top3']:>6.1%} {a['mae']:>7.2f} "
              f"{a['bias']:>+7.2f} {a['pwin']:>7.3f} {a['hit_mkt']:>7.1%} {a['n']:>5}")


def main():
    print("1) cargando cache m point-in-time (data/lab_m.csv, leads 2-3)...")
    if not os.path.exists(LAB_M):
        sys.exit("[ERROR] falta data/lab_m.csv -- correr antes scripts/calib_lab.py (genera el cache).")
    M = pd.read_csv(LAB_M, parse_dates=["target"]); M["target"] = M["target"].dt.date
    print(f"   {len(M)} filas; leads={sorted(M.lead.unique())}; "
          f"target {M.target.min()}..{M.target.max()}")

    obs, real_map, win_map = load_reales()
    print("2) fits EMOS por cutoff mensual (expanding, sin lead-1)...")
    fits, s2maps = build_fits(obs)

    all_detail = []
    lead_res = {}
    for lead in (2, 3):
        base, err, dfs, names, best = run_lead(lead, M, fits, s2maps, real_map, win_map)
        lead_res[lead] = (dfs, names, best)
        for nm in names:
            all_detail.append(dfs[nm])
        print_table(dfs, names, f"LEAD {lead}  (eval 2026-05-10..07-10, walk-forward)")

    # ---- foco en LEAD 2 (operativo + donde se valido V2) para el veredicto ----
    dfs2, names2, best2 = lead_res[2]
    v7_best = max([best2["a"], best2["b"], best2["c"], best2["d"]],
                  key=lambda nm: (agg(dfs2[nm])["hit"], -agg(dfs2[nm])["mae"]))
    print("\n" + "=" * 78)
    print(f"LEAD 2 -- mejor por familia: V7a={best2['a']}  V7b={best2['b']}  "
          f"V7c={best2['c']}  V7d={best2['d']}")
    print(f"LEAD 2 -- V7 GANADOR global: {v7_best}")

    # por estacion: V2 -> V7best (hit_real y MAE), marcando debiles y fuertes
    print("\npor estacion (LEAD 2) [hit_real V2 -> V7best  |  MAE V2 -> V7best]:")
    g2 = dfs2["V2"].groupby("st"); gb = dfs2[v7_best].groupby("st")
    h2 = g2.hit_real.mean(); hb = gb.hit_real.mean(); m2 = g2.ae.mean(); mb = gb.ae.mean()
    for grp, label in [(WEAK, "DEBILES/regime-sensibles"), (STRONG, "FUERTES (no romper)"),
                       ([s for s in sorted(h2.index) if s not in WEAK and s not in STRONG], "resto")]:
        print(f"  -- {label} --")
        for st in grp:
            if st not in h2.index:
                continue
            dh = hb[st] - h2[st]; dm = mb[st] - m2[st]
            mk = "+" if dh > 1e-9 else ("-" if dh < -1e-9 else "=")
            print(f"    {st}: {h2[st]:>4.0%} -> {hb[st]:>4.0%} [{mk}{dh:+.0%}]  |  "
                  f"MAE {m2[st]:.2f} -> {mb[st]:.2f} ({dm:+.2f})")

    # por continente
    print("\npor continente (LEAD 2, hit_real):")
    for cont in ["America", "Europa", "Asia"]:
        s2 = dfs2["V2"][dfs2["V2"].cont == cont]; sb = dfs2[v7_best][dfs2[v7_best].cont == cont]
        print(f"    {cont:>8}: V2={s2.hit_real.mean():>4.0%} -> {v7_best}={sb.hit_real.mean():>4.0%} "
              f"(n={int(s2.hit_real.notna().sum())})")

    # McNemar V7best vs V2 (pares discordantes de hit_real, LEAD 2)
    j = dfs2["V2"][["st", "d", "hit_real"]].rename(columns={"hit_real": "h2"}).merge(
        dfs2[v7_best][["st", "d", "hit_real"]].rename(columns={"hit_real": "hb"}), on=["st", "d"])
    j = j.dropna()
    b = int(((j.hb == 1) & (j.h2 == 0)).sum())   # V7best acierta, V2 no
    cc = int(((j.h2 == 1) & (j.hb == 0)).sum())  # V2 acierta, V7best no
    p = mcnemar_exact(b, cc)
    chi2 = ((abs(b - cc) - 1) ** 2) / (b + cc) if (b + cc) > 0 else 0.0
    a2, ab = agg(dfs2["V2"]), agg(dfs2[v7_best])
    print(f"\nMcNemar LEAD 2  {v7_best} vs V2 (hit_real):")
    print(f"    {v7_best} acierta & V2 no = {b}   |   V2 acierta & {v7_best} no = {cc}")
    print(f"    delta hit = {ab['hit'] - a2['hit']:+.1%}   delta MAE = {ab['mae'] - a2['mae']:+.2f}   "
          f"delta top2 = {ab['top2'] - a2['top2']:+.1%}   delta top3 = {ab['top3'] - a2['top3']:+.1%}")
    print(f"    p-valor exacto (binomial two-sided) = {p:.3f}   chi2_cc(df=1) = {chi2:.2f}")

    # pooled leads 2+3 (robustez)
    poolv2 = pd.concat([lead_res[2][0]["V2"], lead_res[3][0]["V2"]], ignore_index=True)
    poolvb = pd.concat([lead_res[2][0].get(v7_best, lead_res[2][0]["V2"]),
                        lead_res[3][0].get(v7_best, lead_res[3][0]["V2"])], ignore_index=True) \
        if v7_best in lead_res[3][0] else None
    ap2 = agg(poolv2)
    print(f"\npooled leads 2+3 -- V2: hit={ap2['hit']:.1%} MAE={ap2['mae']:.2f} "
          f"top3={ap2['top3']:.1%} n={ap2['n']}")
    if poolvb is not None:
        apb = agg(poolvb)
        print(f"pooled leads 2+3 -- {v7_best}: hit={apb['hit']:.1%} MAE={apb['mae']:.2f} "
              f"top3={apb['top3']:.1%} n={apb['n']}")

    # dump
    det = pd.concat(all_detail, ignore_index=True)
    det.to_csv(DETAIL, index=False)
    summ = []
    for lead in (2, 3):
        dfs, names, _ = lead_res[lead]
        for nm in names:
            a = agg(dfs[nm]); a.update(variant=nm, lead=lead); summ.append(a)
    pd.DataFrame(summ).to_csv(SUMMARY, index=False)
    print(f"\ndetalle -> {os.path.relpath(DETAIL)}   resumen -> {os.path.relpath(SUMMARY)}")
    print("CAVEAT bug #5: niveles de hit OPTIMISTAS (frescura previous_day1, common-mode). Solo el")
    print("RELATIVO V7 vs V2 es valido. hit_real = vs floor(max_real IEM); hit_mkt = vs bucket que")
    print("resolvio el mercado (85% de acuerdo; KLGA/KORD IEM corre ~+2F sobre WU -> cross-check).")


if __name__ == "__main__":
    main()
