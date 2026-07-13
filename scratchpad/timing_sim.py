# -*- coding: utf-8 -*-
# ¿Conviene entrar ANTES (peor pronostico, precio temprano) o TARDE (mejor pronostico)?
# Simula sobre backfill 52 dias x 6 estaciones con precios reales (prices.csv):
#   TIMING:  T3h  = ultimo tick <= close-3h  con forecast lead2 (tarde, lo que hace Santiago hoy)
#            T24h = ultimo tick <= close-24h con forecast lead3 (honesto: a esa hora solo habia corrida de 2 dias antes)
#            T48h = ultimo tick <= close-48h con forecast lead3
#   EJECUCION: TAKER = mid + 0.02 (cruza spread) | MAKER = fill al mid, fee 0 (cota OPTIMISTA: asume fill)
#   ESTRATEGIA: A = top-3 del bot (todas las estaciones) | D = top-1 con edge>=10c (estaciones buenas)
# Comparacion JUSTA: solo mercados simulables en LOS TRES timings (mismo set).
import sys, re
sys.path.insert(0, r"C:\Users\Admin\Downloads\wxbt_fase4")
import pandas as pd, numpy as np
from wxbt.market import bucket_prob

D = r"C:\Users\Admin\Downloads\wxbt_fase4\data"
bf = pd.read_csv(f"{D}/backfill_check.csv")
bf["target"] = pd.to_datetime(bf["target"]).dt.date
bf = bf[bf.win_mkt.notna()]
fc = {}  # (station, target, lead) -> (mu_cal, sigma_cal)
for r in bf.itertuples():
    fc[(r.station, r.target, r.lead)] = (r.mu_cal, r.sigma_cal, r.max_real, r.win_mkt)

px = pd.read_csv(f"{D}/prices.csv", parse_dates=["t", "target"])
px["station"] = px["station"].replace("EGLL", "EGLC")
px["td"] = px.target.dt.date
mk = pd.read_csv(f"{D}/markets.csv")
mk["close_t"] = pd.to_datetime(mk["close_t"], format="mixed")
mk["station"] = mk["station"].replace("EGLL", "EGLC")
mk["td"] = pd.to_datetime(mk["target"]).dt.date
close = {(r.station, r.td): r.close_t for r in mk.itertuples()}

GOOD = {"LEMD", "EGLC", "LFPB", "LIMC", "RJTT", "ZSPD"}  # health-check (hit>=35%, |sesgo|<0.8)
TIMINGS = [("T3h  (tarde,  lead2)", 3, 2), ("T24h (temprano, lead3)", 24, 3), ("T48h (muy temp, lead3)", 48, 3)]

def is_win(lo, hi, win):
    s = str(win)
    nums = [int(x) for x in re.findall(r"\d+", s)]
    if not nums:
        return False
    if "or higher" in s or ">=" in s:
        return (lo is not None) and lo == nums[0] and hi is None
    if "or below" in s or "<=" in s:
        return (hi is not None) and hi == nums[0] and lo is None
    if len(nums) >= 2:
        return lo == nums[0] and hi == nums[1]
    return lo == nums[0] and hi == nums[0]

def snapshot(g, cut):
    """por bucket: ultimo tick <= cut. None si el mercado no tiene >=3 buckets con precio."""
    g2 = g[g.t <= cut]
    if g2.empty:
        return None
    out = []
    for b, gb in g2.groupby("bucket"):
        last = gb.sort_values("t").iloc[-1]
        lo = None if pd.isna(last.lo) else last.lo
        hi = None if pd.isna(last.hi) else last.hi
        out.append(dict(b=b, lo=lo, hi=hi, mid=float(last.mid)))
    return pd.DataFrame(out) if len(out) >= 3 else None

# ---- construir universo: mercados con snapshot en LOS 3 cortes y forecast lead2 y lead3
markets = {}  # (station, target) -> {timing_label: sdf con pb y win}
for (st, td), g in px.groupby(["station", "td"]):
    ct = close.get((st, td))
    if ct is None or (st, td, 2) not in fc or (st, td, 3) not in fc:
        continue
    snaps = {}
    ok = True
    for lbl, h, lead in TIMINGS:
        sdf = snapshot(g, ct - pd.Timedelta(hours=h))
        if sdf is None:
            ok = False
            break
        mu, sg, mx, win = fc[(st, td, lead)]
        sdf = sdf.copy()
        sdf["pb"] = [bucket_prob(mu, sg, r.lo, r.hi) for r in sdf.itertuples()]
        sdf["win"] = [is_win(r.lo, r.hi, win) for r in sdf.itertuples()]
        snaps[lbl] = sdf
    if ok:
        markets[(st, td)] = snaps

print(f"universo comun (simulable en los 3 timings): {len(markets)} mercados, "
      f"{len(set(k[0] for k in markets))} estaciones\n")

def sel_A(s, st):  # top-3 del bot, todas las estaciones
    return s.nlargest(3, "pb")

def sel_D(s, st):  # top-1 con edge>=10c, estaciones buenas
    if st not in GOOD:
        return s.iloc[0:0]
    best = s.nlargest(1, "pb").iloc[0]
    return s[s.b == best.b] if (best.pb - best.mid) >= 0.10 else s.iloc[0:0]

STRATS = [("A) top-3 bot", sel_A), ("D) top-1 edge>=10c", sel_D)]
FEE_TAKER = 0.02

def sim(timing_lbl, strat_fn, fee):
    pnls, cost_sum, n_mkts, mkt_hits = [], 0.0, 0, 0
    for (st, td), snaps in markets.items():
        picks = strat_fn(snaps[timing_lbl], st)
        if picks is None or picks.empty:
            continue
        n_mkts += 1
        got = False
        for r in picks.itertuples():
            cost = r.mid + fee
            pnls.append((1.0 if r.win else 0.0) - cost)
            cost_sum += cost
            got = got or r.win
        mkt_hits += got
    n = len(pnls)
    if n == 0:
        return dict(mkts=0, trades=0, pnl_tr=np.nan, se=np.nan, tot=0, hit=np.nan, avg_cost=np.nan)
    a = np.array(pnls)
    return dict(mkts=n_mkts, trades=n, pnl_tr=a.mean(), se=a.std(ddof=1) / np.sqrt(n),
                tot=a.sum(), hit=mkt_hits / n_mkts, avg_cost=cost_sum / n)

hdr = f"{'timing':<24} {'exec':<6} {'estrategia':<20} {'mkts':>4} {'trades':>6} {'PnL/trade':>10} {'±SE':>6} {'PnL_tot':>8} {'hit_mkt':>7} {'costo_med':>9}"
print(hdr); print("-" * len(hdr))
results = {}
for t_lbl, _, _ in TIMINGS:
    for ex_lbl, fee in [("TAKER", FEE_TAKER), ("MAKER", 0.0)]:
        for s_lbl, fn in STRATS:
            r = sim(t_lbl, fn, fee)
            results[(t_lbl, ex_lbl, s_lbl)] = r
            print(f"{t_lbl:<24} {ex_lbl:<6} {s_lbl:<20} {r['mkts']:>4} {r['trades']:>6} "
                  f"{r['pnl_tr']:>+10.3f} {r['se']:>6.3f} {r['tot']:>+8.1f} {r['hit']:>7.0%} {r['avg_cost']:>9.3f}")
    print()

# ---- descomposicion: ¿cuanto empeora el pronostico y cuanto abarata el precio?
print("descomposicion (mismo universo):")
errs = {2: [], 3: []}
for (st, td) in markets:
    for lead in (2, 3):
        mu, sg, mx, _ = fc[(st, td, lead)]
        errs[lead].append(abs(mu - mx))
print(f"  |mu - max_real| medio: lead2={np.mean(errs[2]):.2f}  lead3={np.mean(errs[3]):.2f}  (grados)")
for s_lbl, fn in STRATS:
    line = f"  {s_lbl}: costo medio del pick (mid) "
    parts = []
    for t_lbl, _, _ in TIMINGS:
        costs, hits = [], []
        for (st, td), snaps in markets.items():
            picks = fn(snaps[t_lbl], st)
            if picks is None or picks.empty:
                continue
            costs += list(picks.mid)
            hits += list(picks.win.astype(float))
        parts.append(f"{t_lbl.split()[0]}: mid={np.mean(costs):.3f} p_real={np.mean(hits):.3f}" if costs else f"{t_lbl.split()[0]}: -")
    print(line + " | ".join(parts))

# ---- ahorro maker
print("\nahorro MAKER vs TAKER (PnL/trade):")
for t_lbl, _, _ in TIMINGS:
    for s_lbl, _ in STRATS:
        rt, rm = results[(t_lbl, "TAKER", s_lbl)], results[(t_lbl, "MAKER", s_lbl)]
        if rt["trades"]:
            print(f"  {t_lbl:<24} {s_lbl:<20} {rm['pnl_tr'] - rt['pnl_tr']:+.3f}/trade "
                  f"(taker {rt['pnl_tr']:+.3f} -> maker {rm['pnl_tr']:+.3f})")
