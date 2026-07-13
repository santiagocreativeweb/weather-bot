#!/usr/bin/env python3
# scripts/lab_bias_window.py -- SWEEP de la VENTANA del sesgo rolling (pedido Santiago 2026-07-12:
# "proba 90, 60, 30 y 7 dias atras"). V2 produccion = ventana 60d; aca se barre la familia completa
# en el MISMO harness walk-forward de lab_v7 (floor-consistente, leads 2/3, anti-look-ahead).
#
# VARIANTES (todas = mu_EMOS - bias_rolling, cambia SOLO como se estima el bias):
#   V0emos      sin bias (EMOS pelado, baseline)
#   W7/W14/W30/W45/W60/W90   media de (pred-real) de los ultimos N dias (< target). W60 = V2 prod.
#   Wexp        media expanding (toda la historia disponible)
#   EW7/EW15/EW30            media exponencial (half-life N dias) — mas peso a lo reciente sin
#                            tirar la muestra (candidata natural entre 7d ruidoso y 60d lento)
#   MED30/MED60              mediana (robusta a outliers tipo RCSS 11/07)
#   minn: ventanas cortas exigen menos puntos (7d no puede juntar 10) — 5 para <=14d/EWMA, 10 resto.
#
# EVALUACION: full 05-10..D1, split H1/H2 (estabilidad — un ganador que solo gana en una mitad es
# ruido), LIVE 07-08..D1 (los dias que Santiago opero en vivo), McNemar ganador-vs-W60, por estacion.
# CAVEAT bug #5: niveles OPTIMISTAS (frescura previous_day1, common-mode) — solo el RELATIVO vale.
# CAVEAT refresh (auditoria 2026-07-12): el lab refresca el bias A DIARIO, produccion SEMANAL ->
# las ventanas cortas (W7/EW7) tienen aca una ventaja irreal; si alguna vez "ganan", descontarlo.
# ASCII prints (cp1252).
#
# VEREDICTO 2026-07-12 (verificacion adversarial, 4 agentes): W60 (=V2 produccion) QUEDA.
#   * Ventanas media 7/30/60/90 (lo pedido): 32.3 / 32.7 / 33.1 / 32.7 % exacto lead 2 -> 60 gana.
#   * MED60 dio +1.6pp pero es EXACTAMENTE E[max de 12 variantes | nulo] (+1.60pp); p ajustado por
#     seleccion 0.44; MED30 PIERDE (sin dosis-respuesta); look-ahead: LIMPIO (756/756 reproducidas).
# REGLA PRE-REGISTRADA (fijada 2026-07-12, ANTES de ver datos nuevos — no tocar el umbral despues):
#   adoptar MED60 si, SOLO con targets >= SHADOW0 (2026-07-12), con n >= 45 dias de calendario:
#   delta hit_real (lead 2, MED60-W60) > 0 Y bootstrap por bloques de dia da P(delta<=0) < 0.05.
#   Una sola mirada al cumplirse n>=45 (sin peeking semanal que reintroduzca seleccion).
import os
import sys
import math
import datetime as dt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from lab_v7 import (CONT, load_reales, build_fits, build_base, build_err,     # noqa: E402
                    pred_bucket_floor, true_bucket_floor, ranked_buckets,
                    parse_win, hit_mkt_floor, mcnemar_exact)
from wxbt.market import bucket_prob                                           # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "data")
LAB_M = os.path.join(D, "lab_m.csv")
DETAIL = os.path.join(D, "lab_bias_window_detail.csv")
SUMMARY = os.path.join(D, "lab_bias_window_summary.csv")

D0_EVAL = dt.date(2026, 5, 10)
LIVE0 = dt.date(2026, 7, 8)          # primer dia del track record vivo de Santiago
SHADOW0 = dt.date(2026, 7, 12)       # sombra MED60: solo targets desde aca cuentan para la regla

# (nombre, kind, win_dias, minn). EWMA: win = half-life, mira hasta 120d atras.
VARIANTS = [
    ("V0emos", None,     0,   0),
    ("W7",     "mean",   7,   5),
    ("W14",    "mean",   14,  5),
    ("W30",    "mean",   30, 10),
    ("W45",    "mean",   45, 10),
    ("W60",    "mean",   60, 10),
    ("W90",    "mean",   90, 10),
    ("Wexp",   "mean", 9999, 10),
    ("EW7",    "ewma",   7,   5),
    ("EW15",   "ewma",   15,  5),
    ("EW30",   "ewma",   30,  5),
    # [2026-07-13, tweets AlterEgo] alpha operativo MOS 0.1-0.3 = half-life ~2-6 dias. Parametros
    # ESPECIFICADOS por la fuente (no barridos): test unico vs W60, misma vara. OJO: produccion
    # refresca SEMANAL -> un half-life de 2-3d necesitaria refresh diario para transferir.
    ("EW2",    "ewma",   2,   5),
    ("EW3",    "ewma",   3,   5),
    ("EW5",    "ewma",   5,   5),
    ("MED30",  "median", 30, 10),
    ("MED60",  "median", 60, 10),
]


def rolling_bias(err_st, d, kind, win, minn):
    """bias walk-forward: solo dias < d. mean/median en ventana `win`; ewma half-life `win`."""
    if kind is None:
        return 0.0
    look = 120 if kind == "ewma" else win
    pts = [(dd, mu - y) for (dd, mu, y) in err_st if dd < d and (d - dd).days <= look]
    if len(pts) < minn:
        return 0.0
    es = np.array([e for _, e in pts])
    if kind == "mean":
        return float(np.mean(es))
    if kind == "median":
        return float(np.median(es))
    ws = np.array([0.5 ** ((d - dd).days / win) for dd, _ in pts])
    return float(np.sum(ws * es) / np.sum(ws))


def score_variant(name, kind, win, minn, base, err, real_map, win_map, lead):
    rows = []
    for (st, d), b in base.items():
        if d < D0_EVAL:
            continue
        unit = b["unit"]
        mu = b["mu_emos"] - rolling_bias(err.get(st, []), d, kind, win, minn)
        sg = b["sigma_emos"]
        y = real_map.get((st, d))
        rec = dict(variant=name, lead=lead, st=st, cont=CONT[st], d=d, mu=round(mu, 2))
        if y is not None:
            tb = true_bucket_floor(y, unit)
            pb = pred_bucket_floor(mu, unit)
            rk = ranked_buckets(mu, sg, unit)
            rec["ae"] = abs(mu - y); rec["e"] = mu - y
            rec["hit_real"] = int(pb == tb)
            rec["top2"] = int(tb in rk[:2])
            rec["top3"] = int(tb in rk[:3])
            rec["pwin"] = bucket_prob(mu - 0.5, sg, tb[0], tb[1])
        w = win_map.get((st, d))
        if isinstance(w, str):
            wb = parse_win(w)
            if wb is not None:
                rec["hit_mkt"] = hit_mkt_floor(mu, unit, wb)
        rows.append(rec)
    return pd.DataFrame(rows)


def agg(df):
    if df.empty or "hit_real" not in df:
        return dict(hit=float("nan"), top2=float("nan"), top3=float("nan"), mae=float("nan"),
                    bias=float("nan"), hit_mkt=float("nan"), n=0)
    return dict(hit=df.hit_real.mean(), top2=df.top2.mean(), top3=df.top3.mean(),
                mae=df.ae.mean(), bias=df.e.mean(),
                hit_mkt=df.hit_mkt.mean() if "hit_mkt" in df else float("nan"),
                n=int(df.hit_real.notna().sum()))


def print_block(dfs, names, title, dmin=None, dmax=None):
    print(f"\n=== {title} ===")
    print(f"{'variante':>8} {'exacto':>8} {'top2':>7} {'top3':>7} {'MAE':>6} {'sesgo':>7} {'hit_mkt':>8} {'n':>5}")
    for nm in names:
        df = dfs[nm]
        if dmin is not None:
            df = df[(df.d >= dmin) & (df.d <= dmax)]
        a = agg(df.dropna(subset=["hit_real"]) if "hit_real" in df else df)
        print(f"{nm:>8} {a['hit']:>7.1%} {a['top2']:>6.1%} {a['top3']:>6.1%} {a['mae']:>6.2f} "
              f"{a['bias']:>+7.2f} {a['hit_mkt']:>7.1%} {a['n']:>5}")


def main():
    if not os.path.exists(LAB_M):
        sys.exit("[ERROR] falta data/lab_m.csv -- correr scripts/calib_lab.py primero.")
    M = pd.read_csv(LAB_M, parse_dates=["target"]); M["target"] = M["target"].dt.date
    D1 = M["target"].max()
    print(f"lab_m: {len(M)} filas, targets {M['target'].min()}..{D1}")
    obs, real_map, win_map = load_reales()
    print("fits EMOS por cutoff mensual (expanding, sin lead-1)...")
    fits, s2maps = build_fits(obs)

    mid = D0_EVAL + (D1 - D0_EVAL) / 2
    all_detail, lead_dfs = [], {}
    for lead in (2, 3):
        base = build_base(M, fits, s2maps, lead)
        err = build_err(base, real_map[lead])
        dfs = {nm: score_variant(nm, k, w, mn, base, err, real_map[lead], win_map[lead], lead)
               for nm, k, w, mn in VARIANTS}
        lead_dfs[lead] = dfs
        for nm, *_ in VARIANTS:
            all_detail.append(dfs[nm])
        names = [v[0] for v in VARIANTS]
        print_block(dfs, names, f"LEAD {lead} -- FULL {D0_EVAL}..{D1} (walk-forward)")
        print_block(dfs, names, f"LEAD {lead} -- H1 {D0_EVAL}..{mid}", D0_EVAL, mid)
        print_block(dfs, names, f"LEAD {lead} -- H2 {mid + dt.timedelta(days=1)}..{D1}",
                    mid + dt.timedelta(days=1), D1)
        print_block(dfs, names, f"LEAD {lead} -- LIVE {LIVE0}..{D1} (dias operados en vivo)",
                    LIVE0, D1)

    # ---- veredicto LEAD 2 (operativo) ----
    dfs2 = lead_dfs[2]
    names = [v[0] for v in VARIANTS]
    cand = [nm for nm in names if nm != "V0emos"]
    best = max(cand, key=lambda nm: (agg(dfs2[nm])["hit"], -agg(dfs2[nm])["mae"]))
    print("\n" + "=" * 74)
    print(f"LEAD 2 -- mejor por hit exacto (full): {best}")

    j = dfs2["W60"][["st", "d", "hit_real"]].rename(columns={"hit_real": "h60"}).merge(
        dfs2[best][["st", "d", "hit_real"]].rename(columns={"hit_real": "hb"}), on=["st", "d"]).dropna()
    b = int(((j.hb == 1) & (j.h60 == 0)).sum())
    c = int(((j.h60 == 1) & (j.hb == 0)).sum())
    p = mcnemar_exact(b, c)
    a60, ab = agg(dfs2["W60"]), agg(dfs2[best])
    print(f"McNemar {best} vs W60(=V2 prod), hit exacto lead 2: {best} si & W60 no = {b} | "
          f"W60 si & {best} no = {c} | p = {p:.3f}")
    print(f"delta hit = {ab['hit'] - a60['hit']:+.1%}  delta top2 = {ab['top2'] - a60['top2']:+.1%}  "
          f"delta MAE = {ab['mae'] - a60['mae']:+.2f}")

    # estabilidad H1/H2 del ganador vs W60
    for tag, lo, hi in [("H1", D0_EVAL, mid), ("H2", mid + dt.timedelta(days=1), D1)]:
        s60 = dfs2["W60"][(dfs2["W60"].d >= lo) & (dfs2["W60"].d <= hi)]
        sb = dfs2[best][(dfs2[best].d >= lo) & (dfs2[best].d <= hi)]
        print(f"  {tag}: W60 {agg(s60)['hit']:.1%} vs {best} {agg(sb)['hit']:.1%}")

    print(f"\npor estacion (lead 2, hit exacto W60 -> {best}):")
    g60 = dfs2["W60"].dropna(subset=["hit_real"]).groupby("st").hit_real.mean()
    gb = dfs2[best].dropna(subset=["hit_real"]).groupby("st").hit_real.mean()
    for st in sorted(g60.index):
        d_ = gb[st] - g60[st]
        mk = "+" if d_ > 1e-9 else ("-" if d_ < -1e-9 else "=")
        print(f"  {st}: {g60[st]:>4.0%} -> {gb[st]:>4.0%} [{mk}]")

    # ---- SOMBRA MED60 (regla pre-registrada del header; solo targets >= SHADOW0) ----
    sh60 = dfs2["W60"][dfs2["W60"].d >= SHADOW0].dropna(subset=["hit_real"])
    shmd = dfs2["MED60"][dfs2["MED60"].d >= SHADOW0].dropna(subset=["hit_real"])
    ndays = len({r for r in sh60.d})
    print(f"\n--- SOMBRA MED60 (targets >= {SHADOW0}, {ndays} dias acumulados; regla: n>=45) ---")
    if ndays == 0:
        print("  sin datos todavia (la sombra arranca con el proximo run semanal).")
    else:
        j2 = sh60[["st", "d", "hit_real"]].rename(columns={"hit_real": "h60"}).merge(
            shmd[["st", "d", "hit_real"]].rename(columns={"hit_real": "hmd"}), on=["st", "d"])
        delta = j2.hmd.mean() - j2.h60.mean()
        # bootstrap por bloques de dia (correlacion sinoptica): P(delta<=0)
        days = sorted(j2.d.unique())
        per_day = {dd: (g.hmd.sum() - g.h60.sum(), len(g)) for dd, g in j2.groupby("d")}
        rng = np.random.default_rng(20260712)
        reps = []
        for _ in range(10000):
            pick = rng.choice(len(days), size=len(days), replace=True)
            s = sum(per_day[days[i]][0] for i in pick); n = sum(per_day[days[i]][1] for i in pick)
            reps.append(s / n if n else 0.0)
        p_le0 = float(np.mean(np.array(reps) <= 0.0))
        print(f"  W60 {j2.h60.mean():.1%} vs MED60 {j2.hmd.mean():.1%}  delta {delta:+.1%}  "
              f"P(delta<=0) bootstrap-dia = {p_le0:.3f}  (n={len(j2)})")
        if ndays >= 45:
            ok = delta > 0 and p_le0 < 0.05
            print(f"  >>> REGLA PRE-REGISTRADA CUMPLIDA: {'ADOPTAR MED60' if ok else 'NO adoptar (se mantiene W60)'}")
        else:
            print(f"  regla aun no evaluable ({ndays}/45 dias) — NO mirar antes (peeking = curse).")

    det = pd.concat(all_detail, ignore_index=True)
    det.to_csv(DETAIL, index=False)
    summ = []
    for lead in (2, 3):
        for nm in names:
            for tag, lo, hi in [("full", D0_EVAL, D1), ("H1", D0_EVAL, mid),
                                ("H2", mid + dt.timedelta(days=1), D1), ("live", LIVE0, D1)]:
                df = lead_dfs[lead][nm]
                a = agg(df[(df.d >= lo) & (df.d <= hi)].dropna(subset=["hit_real"]))
                a.update(variant=nm, lead=lead, window=tag)
                summ.append(a)
    pd.DataFrame(summ).to_csv(SUMMARY, index=False)
    print(f"\ndetalle -> {os.path.relpath(DETAIL)}   resumen -> {os.path.relpath(SUMMARY)}")
    print("CAVEAT bug #5: niveles optimistas (common-mode); vale el RELATIVO entre ventanas.")
    print("OJO winner's curse: 12 variantes sobre ~60 dias -> exigir H1/H2 consistente + McNemar.")


if __name__ == "__main__":
    main()
