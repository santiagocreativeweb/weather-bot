#!/usr/bin/env python3
# scripts/lab_entry_timing.py -- TIMING DE ENTRADA con PRECIOS REALES (hipotesis Santiago 2026-07-13:
# "cerrar/entrar mas temprano -- madrugada, 8h antes del pico -- porque mas falta = mas incertidumbre
# = precios mas blandos = mejor % comprando, incluido el top-3").
#
# ESTO SI SE PUEDE TESTEAR HONESTO: data/prices.csv son precios REALES del orderbook (mid + half-
# spread) de 18 meses, 6 estaciones (EGLL/KLGA/KORD/LFPB/RJTT/RKSI). markets.csv marca el bucket
# GANADOR (resolved=1). Reconstruyo el pick del bot (EMOS lead-2 walk-forward) y SIMULO comprarlo a
# distintas horas-al-cierre al precio real, liquidando contra el ganador real.
#
# METRICA: PnL por share = 1{bucket gano} - (precio_en_T + half_spread). Es el edge_re del proyecto.
# Por (entry-time, estrategia top-1/top-2/top-3) -> promedio y hit. La hipotesis GANA si entrar mas
# TEMPRANO da MAYOR PnL/share y si el top-3 es +EV.
#
# [FIX 2026-07-13, auto-correccion] La v1 usaba el pick lead-2 en TODOS los bins, incluido 72-48h
# — pero la corrida 00Z de la vispera (lead 2) se publica ~29-31h antes del cierre nominal (12Z):
# a 72-48h ese pick NO EXISTIA (look-ahead). Ahora cada bin usa SOLO el pick DISPONIBLE en ese
# momento: bins >31h -> pick lead-3 (00Z de D-2, avail ~53-55h antes); bins <=31h -> pick lead-2.
# Este es exactamente el trade-off de Santiago: precio blando temprano vs prediccion mas gruesa.
#
# CAVEAT bug #5: leads 2/3 con frescura optimista (comun a todos los bins) -> NIVELES optimistas,
# comparacion temprano-vs-tarde valida. Precios cortan ~12h antes del cierre real (close nominal
# 12Z) -> la cola final de convergencia no esta para las no-asiaticas.
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
from wxbt.market import bucket_prob                         # noqa: E402
from show_live import STATIONS                              # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "data")
STATS = ["KLGA", "KORD", "LFPB", "RJTT", "RKSI"]            # EGLL excluido (station vieja Heathrow)
# (hi_h, lo_h, lead_del_pick): horas-al-cierre-nominal-12Z y el pick DISPONIBLE en esa ventana.
# lead 3 (00Z D-2) avail ~53-55h antes; lead 2 (00Z D-1) avail ~29-31h antes.
TBINS = [(53, 31, 3), (31, 24, 2), (24, 12, 2), (12, 6, 2), (6, 0, 2)]
NBOOT = 10000


def monthly_cuts(d0, d1):
    cuts, y, m = [], d0.year, d0.month
    while (y, m) <= (d1.year, d1.month):
        cuts.append(dt.date(y, m, 1) - dt.timedelta(days=1))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return cuts


def pbot_floor(mu, sg, lo, hi):
    return bucket_prob(mu - 0.5, sg, lo, hi)


def main():
    fc = pd.read_csv(f"{D}/forecasts.csv", parse_dates=["avail", "target"])
    fc["d"] = fc["target"].dt.date
    fc["ld"] = fc["lead_h"].map(_lead_day)
    obs = pd.read_csv(f"{D}/obs.csv", parse_dates=["date"]); obs["date"] = obs["date"].dt.date
    mk = pd.read_csv(f"{D}/markets.csv", parse_dates=["close_t"])
    mk["d"] = pd.to_datetime(mk["target"]).dt.date
    pr = pd.read_csv(f"{D}/prices.csv", parse_dates=["t"])
    pr["d"] = pd.to_datetime(pr["target"]).dt.date

    fc2 = fc[fc.ld.isin([2, 3]) & fc.station.isin(STATS)].copy()
    d0, d1 = fc2.d.min(), fc2.d.max()
    cuts = monthly_cuts(d0 + dt.timedelta(days=60), d1)
    print(f"reconstruyendo picks del bot (EMOS leads 2 y 3, walk-forward) {d0}..{d1}, {len(cuts)} cutoffs...")

    # fits EMOS por cutoff (expanding, leads 2+3 como los labs) + s2 al ultimo dato <= cut
    fits, s2maps = {}, {}
    for cut in cuts:
        fce = fc2[fc2.d <= cut]
        obse = obs[obs.date <= cut]
        fits[cut] = fit_all(fce.assign(target=fce["d"]), obse, sorted(obse.date.unique()))
        sub = fc[(fc.d <= cut)].sort_values("avail")
        s2maps[cut] = {(r.station, r.model, r.ld): r.s2 for r in sub.itertuples()}

    def cut_for(d):
        c = [c for c in cuts if c < d]
        return c[-1] if c else None

    obs_map = {(r.station, r.date): float(r.tmax) for r in obs.itertuples()}

    # mu del bot por (station, target, LEAD) + bias rolling 60d por (station, lead)
    LD_VAR = {2: 1.5, 3: 2.5}
    bot = {}
    err_hist = {}
    for lead in (2, 3):
        piv = fc2[fc2.ld == lead].pivot_table(index=["station", "d"], columns="model",
                                              values="m", aggfunc="last")
        for (st, d), row in piv.sort_index(level=1).iterrows():
            cut = cut_for(d)
            if cut is None:
                continue
            pars = fits[cut].get(st); s2m = s2maps[cut]
            if pars is None:
                continue
            pm = {}
            for mo in ("gefs", "ecmwf", "icon"):
                v = row.get(mo)
                s2 = s2m.get((st, mo, lead))
                if pd.isna(v) or s2 is None:
                    continue
                pm[mo] = (float(v), float(s2))
            if len(pm) < 3:
                continue
            c = clim_val(pars["clim"], d)
            prd = predict(pars["emos"], {k: (m - c, s2) for k, (m, s2) in pm.items()}, ld=LD_VAR[lead])
            if prd is None:
                continue
            mu0 = c + prd[0]
            past = [(dd, e) for (dd, e) in err_hist.get((st, lead), []) if dd < d and (d - dd).days <= 60]
            bias = float(np.mean([e for _, e in past])) if len(past) >= 10 else 0.0
            bot[(st, d, lead)] = (mu0 - bias, prd[1])
            y = obs_map.get((st, d))
            if y is not None:
                err_hist.setdefault((st, lead), []).append((d, mu0 - y))
    n2 = sum(1 for k in bot if k[2] == 2); n3 = sum(1 for k in bot if k[2] == 3)
    print(f"  picks reconstruidos: lead2={n2}  lead3={n3}")

    # winner y grid por (st, d)
    winner, grid = {}, {}
    for (st, d), g in mk.groupby(["station", "d"]):
        if st not in STATS:
            continue
        buckets = [(int(r.bucket), (None if pd.isna(r.lo) else float(r.lo)),
                    (None if pd.isna(r.hi) else float(r.hi))) for r in g.itertuples()]
        grid[(st, d)] = buckets
        w = g[g.resolved == 1]
        if len(w):
            r0 = w.iloc[0]
            winner[(st, d)] = (None if pd.isna(r0.lo) else float(r0.lo),
                               None if pd.isna(r0.hi) else float(r0.hi))

    # precios: dict (st,d,bucket) -> serie [(t, mid, hs)]
    price_ser = {}
    for r in pr.itertuples():
        if r.station not in STATS:
            continue
        price_ser.setdefault((r.station, r.d, int(r.bucket)), []).append((r.t, r.mid, r.hs))
    for k in price_ser:
        price_ser[k].sort()

    def price_at(st, d, bucket, close_t, lo_h, hi_h):
        """mid+hs del bucket en la ventana [hi_h, lo_h) horas-al-cierre. Ultimo <= el borde temprano."""
        ser = price_ser.get((st, d, bucket))
        if not ser or pd.isna(close_t):
            return None
        t_lo = close_t - pd.Timedelta(hours=hi_h)   # borde mas temprano de la ventana
        t_hi = close_t - pd.Timedelta(hours=lo_h)
        best = None
        for (t, mid, hs) in ser:
            if t_lo <= t < t_hi:
                best = (mid, hs)
        return best

    # simulacion: por (st,d) y por T-bin, rankear top-3 del pick DISPONIBLE en ese bin (lead 3 o 2)
    close_by = {(r.station, r.d): r.close_t for r in mk.itertuples() if r.station in STATS}
    pairs = sorted({(st, d) for (st, d, _) in bot})
    rows = []
    for (st, d) in pairs:
        if (st, d) not in winner or (st, d) not in grid:
            continue
        buckets = grid[(st, d)]
        win = winner[(st, d)]
        ct = close_by.get((st, d))
        for (hi_h, lo_h, ld) in TBINS:
            pk = bot.get((st, d, ld))
            if pk is None:
                continue
            mu, sg = pk
            ranked = sorted(buckets, key=lambda b: -pbot_floor(mu, sg, b[1], b[2]))
            for rank, (bk, lo, hi) in enumerate(ranked[:3], 1):
                won = int((lo, hi) == win)
                pv = price_at(st, d, bk, ct, lo_h, hi_h)
                if pv is None:
                    continue
                mid, hs = pv
                pnl = won - (mid + hs)          # taker; maker=mid (hs=0)
                rows.append(dict(st=st, d=d, rank=rank, tbin=f"{hi_h}-{lo_h}h", lead=ld,
                                 tbin_hi=hi_h, price=mid, won=won,
                                 pnl_taker=pnl, pnl_maker=won - mid))
    R = pd.DataFrame(rows)
    if R.empty:
        print("[WARN] sin filas simuladas (overlap precios/picks vacio)"); return
    R.to_csv(f"{D}/lab_entry_timing.csv", index=False)
    print(f"  {len(R)} (pick, entry-time) simulados; {R.d.nunique()} dias, {R.st.nunique()} estaciones\n")

    rng = np.random.default_rng(20260713)

    def boot_mean(x):
        x = np.asarray(x, float)
        if len(x) < 5:
            return (float(np.mean(x)) if len(x) else float("nan"), float("nan"))
        idx = rng.integers(0, len(x), size=(NBOOT, len(x)))
        reps = x[idx].mean(1)
        return float(x.mean()), float((reps <= 0).mean())

    print("=== HIPOTESIS: comprar mas TEMPRANO (mas barato, pick mas grueso) rinde mas? ===")
    print("Cada bin usa SOLO el pick disponible en ese momento (53-31h: lead3; <=31h: lead2).")
    print("PnL/share TAKER (=1{gano} - precio - half_spread); maker entre parentesis.\n")
    print(f"{'entry (h, pick)':>20} | {'TOP-1':>16} | {'TOP-2 (basket)':>16} | {'TOP-3 (basket)':>16}")
    for (hi_h, lo_h, ld) in TBINS:
        sub = R[R.tbin == f"{hi_h}-{lo_h}h"]
        cells = []
        for k in (1, 2, 3):
            s = sub[sub["rank"] <= k]
            if s.empty:
                cells.append(f"{'-':>16}"); continue
            m, p = boot_mean(s.pnl_taker.values)
            mm = s.pnl_maker.mean()
            cells.append(f"{m:>+6.3f}({mm:+.3f})p{p:.2f}"[:16].rjust(16))
        print(f"{f'{hi_h}-{lo_h}h ld{ld}':>20} | {cells[0]} | {cells[1]} | {cells[2]}")

    print("\n=== precio medio + hit por rank y entry-time (el trade-off precio vs finura) ===")
    for k in (1, 2, 3):
        print(f" top-{k}:")
        for (hi_h, lo_h, ld) in TBINS:
            s = R[(R["rank"] == k) & (R.tbin == f"{hi_h}-{lo_h}h")]
            if s.empty:
                continue
            print(f"   {hi_h:>2}-{lo_h:>2}h ld{ld}  precio_med {s.price.mean():.3f}  hit {s.won.mean():.0%}  "
                  f"PnL_taker {s.pnl_taker.mean():+.3f}  n={len(s)}")

    print("\n=== por ESTACION: top-1, mejor bin temprano (53-31h ld3) vs madrugada (31-24h ld2) vs tarde (6-0h) ===")
    for st in sorted(R.st.unique()):
        cells = []
        for tb in ("53-31h", "31-24h", "6-0h"):
            s = R[(R.st == st) & (R["rank"] == 1) & (R.tbin == tb)]
            cells.append(f"{tb}: {s.pnl_taker.mean():+.3f} (hit {s.won.mean():.0%}, n={len(s)})"
                         if len(s) else f"{tb}: -")
        print(f"  {st}: " + "  |  ".join(cells))

    print("\nLECTURA: compara 53-31h(ld3) y 31-24h(ld2) contra 6-0h. Si el precio blando compensa el")
    print("pick mas grueso, temprano gana. CAVEAT bug#5: niveles optimistas (common-mode entre bins).")


if __name__ == "__main__":
    main()
