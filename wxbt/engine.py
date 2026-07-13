# wxbt/engine.py — Backtest event-driven + walk-forward.
# Garantías anti look-ahead:
#   (1) en cada decisión t solo se leen forecasts con avail <= t (searchsorted sobre avail);
#   (2) la calibración de cada bloque walk-forward se ajusta SOLO con targets anteriores al bloque;
#   (3) precios usados = snapshot exacto en t.
# El núcleo de decisión (evaluate_market) es función pura -> se reusa en paper/live (FASE 5).
import numpy as np
import pandas as pd
from collections import defaultdict
from . import config as C
from .calibration import fit_emos, predict, predict_raw, crps_normal
from .market import (bucket_prob, resolve_bucket, taker_fee_per_share,
                     exec_price, entry_cost_per_share, exit_proceeds_per_share)


def _lead_day(hours):
    if hours <= 24: return 1
    if hours <= 48: return 2
    return 3


def _index_forecasts(fc):
    """dict[(station,target,model)] -> (avail_ns_sorted, m, s2) numpy para lookup O(log n)."""
    idx = {}
    for key, g in fc.groupby(["station", "target", "model"]):
        g = g.sort_values("avail")
        idx[key] = (g["avail"].values.astype("datetime64[ns]"),
                    g["m"].values.astype(float), g["s2"].values.astype(float))
    return idx


def latest_per_model(fidx, station, target, t):
    """Últimos forecasts disponibles a tiempo t (avail <= t). ÚNICO punto de lectura de forecasts."""
    t64 = np.datetime64(t)
    out = {}
    for mname in C.MODELS:
        tup = fidx.get((station, target, mname))
        if tup is None:
            continue
        av, m, s2 = tup
        i = np.searchsorted(av, t64, side="right") - 1
        if i >= 0:
            out[mname] = (float(m[i]), float(s2[i]))
    return out


def _fit_clim(obs, train_dates):
    """Climatología kernel-DOY por estación, SOLO con train (robusta con train corto,
    a diferencia de armónicos que extrapolan basura fuera de soporte).
    Motivo (diagnóstico FASE 4): OLS y~m sobre serie estacional sufre atenuación (b<1)
    -> sesgo sistemático al predecir fuera de la media del train. EMOS se ajusta en ANOMALÍAS."""
    clims = {}
    tset = set(train_dates)
    for st, g in obs[obs["date"].isin(tset)].groupby("station"):
        doy = np.array([pd.Timestamp(d).dayofyear for d in g["date"]], float)
        clims[st] = (doy, g["tmax_int"].values.astype(float))
    return clims


def clim_val(coef, date, bw=12.0):
    doys, ys = coef
    d = abs(pd.Timestamp(date).dayofyear - doys)
    d = np.minimum(d, 365 - d)                      # distancia circular en DOY
    w = np.exp(-0.5 * (d / bw) ** 2)
    return float(np.sum(w * ys) / max(np.sum(w), 1e-9))


def build_train_samples(fc, obs, train_dates):
    """Pares (per_model, y) por estación (pool de leads) usando SOLO targets de entrenamiento."""
    obs_map = {(r.station, r.date): r.tmax_int for r in obs.itertuples()}
    tset = set(train_dates)
    sub = fc[fc["target"].isin(tset)]
    samples = defaultdict(dict)   # station -> {(target,ld): {'y':..,'per_model':{},'target':..}}
    for r in sub.itertuples():
        ld = _lead_day(r.lead_h)
        y = obs_map.get((r.station, r.target))
        if y is None:
            continue
        d = samples[r.station].setdefault((r.target, ld),
                                          {"y": float(y), "per_model": {}, "target": r.target, "ld": float(ld)})
        d["per_model"][r.model] = (r.m, r.s2)   # queda el último por orden de avail (por lead)
    return {k: list(v.values()) for k, v in samples.items()}


def fit_all(fc, obs, train_dates):
    smp = build_train_samples(fc, obs, train_dates)
    clims = _fit_clim(obs, train_dates)
    params = {}
    for st, rows in smp.items():
        if st not in clims:
            continue
        coef = clims[st]
        anom_rows = []
        for r in rows:
            c = clim_val(coef, r["target"])
            anom_rows.append({"y": r["y"] - c, "ld": r["ld"],
                              "per_model": {k: (m - c, s2) for k, (m, s2) in r["per_model"].items()}})
        unit = C.STATION_BY_CODE[st].unit
        p = fit_emos(anom_rows, C.SIGMA_FLOOR[unit])
        if p is not None:
            params[st] = dict(emos=p, clim=coef)
    return params


def evaluate_market(rows, mu, sigma, mode):
    """NÚCLEO PURO de decisión. rows: [(bucket, lo, hi, mid, hs)].
    Entrada solo si el edge sobrevive también con sigma*SIGMA_STRESS (robustez a
    error de dispersión). Devuelve mejor candidato (edge robusto max) o None."""
    best = None
    for b, lo, hi, mid, hs in rows:
        p_yes = bucket_prob(mu, sigma, lo, hi)
        p_yes_s = bucket_prob(mu, sigma * C.SIGMA_STRESS, lo, hi)
        for token, p, p_s, mtok in (("YES", p_yes, p_yes_s, mid),
                                    ("NO", 1 - p_yes, 1 - p_yes_s, 1 - mid)):
            if mtok < C.MIN_ENTRY_PRICE:      # fill realista: sin liquidez bajo el piso (mode-indep)
                continue
            px = exec_price(mtok, hs, True, mode)
            cps, fee = entry_cost_per_share(px, mode)
            edge = min(p - cps, p_s - cps)
            if edge >= C.EDGE_MIN_NET and (best is None or edge > best["edge"]):
                best = dict(bucket=int(b), lo=lo, hi=hi, token=token, p=p,
                            px=px, cps=cps, fee=fee, edge=edge)
    return best


def build_settled_map(mk, px=None):
    """Ground-truth de PAGO real: el bucket que el MERCADO settleó (lo que cobras en vivo — paga el
    oraculo WU, no tu obs interna). Fuente primaria: columna `resolved` de markets.csv (=1 en el
    bucket ganador, de outcomePrices de Gamma) -> cobertura ~100% de resueltos. Fallback (sin esa
    columna): convergencia de precios (mid>=0.9 sostenido), que solo cubre ~10% (el precio corta
    antes de resolver en mercados finos). Devuelve (settled[(st,tg)]=bucket_ganador, unclear set)."""
    settled, unclear = {}, set()
    all_keys = set(zip(mk["station"], mk["target"]))
    if "resolved" in mk.columns:
        for r in mk[mk["resolved"] == 1].itertuples():
            settled[(r.station, r.target)] = int(r.bucket)     # 1 ganador por mercado
        unclear = all_keys - set(settled)
        return settled, unclear
    if px is None:
        return {}, all_keys
    for (st, tg), g in px.groupby(["station", "target"]):      # fallback por convergencia de precio
        ts = sorted(g["t"].unique())[-3:]
        if len(ts) < 3:
            unclear.add((st, tg)); continue
        winners = [g[g["t"] == t].loc[g[g["t"] == t]["mid"].idxmax()] for t in ts]
        w0 = int(winners[0]["bucket"]) if winners[0]["mid"] >= 0.9 else None
        if w0 is not None and all((int(w["bucket"]) == w0 and w["mid"] >= 0.9) for w in winners):
            settled[(st, tg)] = w0
        else:
            unclear.add((st, tg))
    return settled, unclear


def run_backtest(world, mode="taker", use_calibration=True, block_days=30, capital=C.CAPITAL_INICIAL,
                 force_final_settle=True, resolve="obs"):
    # resolve: "obs" = paga contra obs_IEM (temperatura fisica); "market" = paga contra el bucket
    # que el mercado settleó (ground-truth de pago real, elude el delta IEM-vs-WU). En "market" las
    # metricas de CALIBRACION (CRPS/Brier/reliability) SIGUEN contra obs fisica -- market-settled es
    # SOLO para el payout del PnL (medir "predigo el clima" != "predigo lo que el mercado creera").
    fc, obs, mk, px = world["forecasts"], world["obs"], world["markets"], world["prices"]
    fidx = _index_forecasts(fc)
    obs_map = {(r.station, r.date): r.tmax_int for r in obs.itertuples()}
    close_map = {(r.station, r.target): r.close_t for r in mk.itertuples()}
    settled_map, unclear_set = build_settled_map(mk, px) if resolve == "market" else ({}, set())
    unclear_settled = []   # posiciones settleadas mark-to-market por mercado no-resuelto

    obs_dates = sorted(obs["date"].unique())
    fc_dates = sorted(set(fc["target"].unique()) & set(obs_dates))
    blocks = []
    i = C.MIN_TRAIN_DAYS                      # mínimo de días CON forecasts antes de operar
    while i < len(fc_dates):
        block_start = fc_dates[i]
        train = [d for d in obs_dates if d < block_start]   # clim usa toda la historia de obs
        blocks.append((train, fc_dates[i:i + block_days]))
        i += block_days

    # precomputo: snapshots ordenados; filas por (t, station, target)
    px = px.sort_values("t")
    snap_groups = px.groupby("t", sort=True)

    cash, equity = capital, capital
    positions = {}          # (station,target) -> pos dict
    group_cost = defaultdict(float)
    trades, preds, eq_rows = [], [], []
    cooldown = {}
    crps_cal, crps_raw, brier_cal_buckets, brier_raw_buckets = [], [], [], []
    kill_day, day0, day0_eq = None, None, capital
    hw_max = capital         # high-water ABSOLUTO (ATH) para el freno trailing
    brake_factor = 1.0
    block_of_date = {}
    for bi, (_, test) in enumerate(blocks):
        for d in test:
            block_of_date[d] = bi
    params_cache = {}
    clim_memo = {}

    def get_clim(pars, st, tgt):
        bi = block_of_date.get(tgt)
        key = (bi, st, tgt)
        if key not in clim_memo:
            clim_memo[key] = clim_val(pars["clim"], tgt)
        return clim_memo[key]

    def get_params(d):
        bi = block_of_date.get(d)
        if bi is None:
            return None
        if bi not in params_cache:
            params_cache[bi] = fit_all(fc, obs, blocks[bi][0])
        return params_cache[bi]

    def settle_due(t):
        nonlocal cash
        for key in [k for k, p in positions.items() if p["close_t"] <= t]:
            p = positions.pop(key)
            group_cost[p["group"]] -= p["cost"]
            y = obs_map[(key[0], key[1])]
            won_yes_obs = resolve_bucket(y, p["lo"], p["hi"])          # física: para reliability
            won_obs = won_yes_obs if p["token"] == "YES" else (not won_yes_obs)
            if resolve == "market":
                sb = settled_map.get(key)
                if sb is None:                                        # mercado no resolvió claro
                    mtok = p["last_mid"] if p["token"] == "YES" else 1 - p["last_mid"]
                    payout = p["shares"] * min(max(mtok, 0.0), 1.0)   # salida mark-to-market
                    won_pay = None; unclear_settled.append(key)
                else:
                    won_pay = (p["bucket"] == sb) if p["token"] == "YES" else (p["bucket"] != sb)
                    payout = p["shares"] * (1.0 if won_pay else 0.0)
            else:
                won_pay = won_obs
                payout = p["shares"] * (1.0 if won_pay else 0.0)
            cash += payout
            trades.append(dict(**{k2: p[k2] for k2 in ("station", "target", "bucket", "token",
                                                       "shares", "px", "cost", "p_entry")},
                               exit="resolved", pnl=payout - p["cost"],
                               won=(None if won_pay is None else int(won_pay))))
            preds.append((p["p_entry"], int(won_obs)))                # reliability SIEMPRE contra obs

    tradable_dates = set(block_of_date)
    for t, snap in snap_groups:
        settle_due(t)
        d_utc = t.date()
        if day0 != d_utc:
            day0, day0_eq, kill_day = d_utc, equity, None
        blocked = (kill_day == d_utc)
        # freno trailing GEOMETRICO por drawdown desde el ATH (completa el kill diario, que no
        # frena goteos multi-dia): cada -10% adicional divide el tamano por 2. Sin escalon de
        # cero (un stop total es estado absorbente: sin entradas no hay recuperacion posible).
        dd_hw = equity / hw_max - 1
        brake_factor = C.TRAILING_BRAKE_FACTOR ** int(dd_hw / C.TRAILING_BRAKE_DD)

        # mark-to-market + salidas + entradas por mercado presente en el snapshot
        mids_now = {}
        for (st, tgt), g in snap.groupby(["station", "target"]):
            if tgt not in tradable_dates:
                continue
            pm = latest_per_model(fidx, st, tgt, t)
            if len(pm) < C.MIN_MODELS_ENTRY:
                continue
            pars_all = get_params(tgt)
            unit = C.STATION_BY_CODE[st].unit
            if use_calibration:
                pars = (pars_all or {}).get(st)
                if pars is None:
                    continue
                c = get_clim(pars, st, tgt)
                pm_a = {k: (m - c, s2) for k, (m, s2) in pm.items()}
                ld_dec = max((close_map[(st, tgt)] - t).total_seconds() / 86400.0, 0.05)
                mu_a, sigma = predict(pars["emos"], pm_a, ld=ld_dec)
                mu = c + mu_a
            else:
                mu, sigma = predict_raw(pm, C.SIGMA_FLOOR[unit])

            rows = [(r.bucket, r.lo, r.hi, r.mid, r.hs) for r in g.itertuples()]
            for b, lo, hi, mid, hs in rows:
                mids_now[(st, tgt, b)] = mid

            key = (st, tgt)
            if key in positions:
                p = positions[key]
                p_yes_now = bucket_prob(mu, sigma, p["lo"], p["hi"])
                p_now = p_yes_now if p["token"] == "YES" else 1 - p_yes_now
                mid_b = mids_now.get((st, tgt, p["bucket"]), p["last_mid"])
                mtok = mid_b if p["token"] == "YES" else 1 - mid_b
                p["last_mid"] = mid_b
                if p_now <= p["p_entry"] - C.EXIT_PROB_SHIFT:
                    net, _, _ = exit_proceeds_per_share(mtok, p["hs"], mode)
                    cash += p["shares"] * net
                    group_cost[p["group"]] -= p["cost"]
                    trades.append(dict(**{k2: p[k2] for k2 in ("station", "target", "bucket", "token",
                                                               "shares", "px", "cost", "p_entry")},
                                       exit="fc_changed", pnl=p["shares"] * net - p["cost"], won=None))
                    cooldown[key] = t + pd.Timedelta(hours=C.REENTRY_COOLDOWN_H)
                    y = obs_map[(st, tgt)]
                    won_yes = resolve_bucket(y, p["lo"], p["hi"])
                    preds.append((p["p_entry"], int(won_yes if p["token"] == "YES" else not won_yes)))
                    positions.pop(key)
                continue

            if blocked:
                continue
            if key in cooldown and t < cooldown[key]:
                continue
            cand = evaluate_market(rows, mu, sigma, mode)
            if cand is None:
                continue
            grp = C.STATION_BY_CODE[st].group
            # Kelly sobre el edge DE-SESGADO (el aparente esta inflado ~5x por seleccion/curse).
            # OJO: con capital chico el cap PER_MARKET_CAP_USD domina a Kelly casi siempre — el
            # sizing efectivo son los caps. El gate de entrada (EDGE_MIN_NET) sigue sobre el aparente.
            f = C.KELLY_FRACTION * C.EDGE_SHRINK * (cand["p"] - cand["cps"]) / max(1 - cand["cps"], 1e-6)
            cost = min(f * equity, C.PER_MARKET_CAP_FRAC * equity, C.PER_MARKET_CAP_USD,
                       C.GROUP_CAP_FRAC * equity - group_cost[grp], cash)
            # freno trailing SOBRE EL COSTO FINAL (post-caps): si multiplicara solo a f, el cap
            # de $40 lo dejaria inerte (mismo mecanismo por el que EDGE_SHRINK resulto inerte).
            cost *= brake_factor
            if cost < 1.0:
                continue
            shares = min(cost / cand["cps"], C.PAYOUT_CAP_USD)
            cost = shares * cand["cps"]
            cash -= cost
            group_cost[grp] += cost
            positions[key] = dict(station=st, target=tgt, bucket=cand["bucket"], lo=cand["lo"],
                                  hi=cand["hi"], token=cand["token"], shares=shares, px=cand["px"],
                                  cost=cost, p_entry=cand["p"], hs=rows[0][4],
                                  close_t=close_map[key], group=grp, last_mid=mids_now[(st, tgt, cand["bucket"])],
                                  fee_paid=shares * cand["fee"])

        # equity mark-to-market
        mtm = 0.0
        for (st, tgt), p in positions.items():
            mid_b = mids_now.get((st, tgt, p["bucket"]), p["last_mid"])
            p["last_mid"] = mid_b
            mtok = mid_b if p["token"] == "YES" else 1 - mid_b
            mtm += p["shares"] * mtok
        equity = cash + mtm
        if equity - day0_eq <= C.DAILY_KILL_SWITCH * day0_eq:
            kill_day = d_utc
        hw_max = max(hw_max, equity)
        eq_rows.append((t, equity))

        # evaluación CRPS/Brier calibrado-vs-crudo (una vez por mercado, ~24-30h antes del cierre)
        for (st, tgt), g in snap.groupby(["station", "target"]):
            if tgt not in tradable_dates:
                continue
            hrs = (close_map[(st, tgt)] - t).total_seconds() / 3600.0
            if not (24.0 <= hrs < 30.0):
                continue
            pm = latest_per_model(fidx, st, tgt, t)
            if len(pm) < 2:
                continue
            pars = (get_params(tgt) or {}).get(st)
            unit = C.STATION_BY_CODE[st].unit
            y = obs_map[(st, tgt)]
            raw = predict_raw(pm, C.SIGMA_FLOOR[unit])
            if pars is not None and raw is not None:
                cc = get_clim(pars, st, tgt)
                mu_ca, sd_c = predict(pars["emos"], {k: (m - cc, s2) for k, (m, s2) in pm.items()},
                                      ld=max(hrs / 24.0, 0.05))
                mu_c = cc + mu_ca
                mu_r, sd_r = raw
                crps_cal.append(crps_normal(y, mu_c, sd_c))
                crps_raw.append(crps_normal(y, mu_r, sd_r))
                for r in g.itertuples():
                    o = 1.0 if resolve_bucket(y, r.lo, r.hi) else 0.0
                    brier_cal_buckets.append((bucket_prob(mu_c, sd_c, r.lo, r.hi) - o) ** 2)
                    brier_raw_buckets.append((bucket_prob(mu_r, sd_r, r.lo, r.hi) - o) ** 2)

    open_snapshot = []
    if force_final_settle:
        settle_due(pd.Timestamp.max)
    else:
        # posiciones REALMENTE abiertas al final de la ventana de datos (no fabricadas):
        # el mercado todavía no cerró (close_t > último t simulado) -> igual que "live" en corte.
        for (st, tgt), p in positions.items():
            mid_b = p["last_mid"]
            mtok = mid_b if p["token"] == "YES" else 1 - mid_b
            mtm_val = p["shares"] * mtok
            open_snapshot.append(dict(station=st, target=str(tgt), bucket=p["bucket"],
                                      lo=p["lo"], hi=p["hi"], token=p["token"],
                                      shares=round(p["shares"], 2), cost=round(p["cost"], 2),
                                      p_entry=round(p["p_entry"], 3), last_mid=round(mid_b, 3),
                                      mtm=round(mtm_val, 2), unrl_pnl=round(mtm_val - p["cost"], 2),
                                      close_t=str(p["close_t"]), group=p["group"]))
    eq = pd.DataFrame(eq_rows, columns=["t", "equity"]).set_index("t")
    return dict(equity=eq, trades=pd.DataFrame(trades), preds=preds, open_positions=open_snapshot,
                crps=dict(cal=float(np.mean(crps_cal)) if crps_cal else None,
                          raw=float(np.mean(crps_raw)) if crps_raw else None),
                brier_buckets=dict(cal=float(np.mean(brier_cal_buckets)) if brier_cal_buckets else None,
                                   raw=float(np.mean(brier_raw_buckets)) if brier_raw_buckets else None),
                n_unclear_settled=len(unclear_settled), capital0=capital)


def metrics(res):
    eq = res["equity"]["equity"]
    daily = eq.resample("1D").last().dropna()
    rets = daily.pct_change().dropna()
    roi = eq.iloc[-1] / res["capital0"] - 1 if len(eq) else 0.0
    dd = float((eq / eq.cummax() - 1).min()) if len(eq) else 0.0
    sharpe = float(rets.mean() / rets.std() * np.sqrt(365)) if len(rets) > 2 and rets.std() > 0 else float("nan")
    tr = res["trades"]
    resolved = tr[tr["exit"] == "resolved"] if len(tr) else tr
    hit = float(resolved["won"].mean()) if len(resolved) else float("nan")
    fees = float(tr.get("fee_paid", pd.Series(dtype=float)).sum()) if len(tr) else 0.0
    preds = res["preds"]
    brier_entry = float(np.mean([(p - o) ** 2 for p, o in preds])) if preds else float("nan")
    return dict(roi=float(roi), max_dd=dd, sharpe=sharpe, n_trades=int(len(tr)),
                n_resolved=int(len(resolved)), hit_rate=hit, brier_entry=brier_entry,
                ev_por_trade=float(tr["pnl"].mean()) if len(tr) else float("nan"),
                pnl_total=float(tr["pnl"].sum()) if len(tr) else 0.0)


def reliability_bins(preds, nbins=10):
    if not preds:
        return []
    arr = np.array(preds)
    out = []
    for i in range(nbins):
        lo, hi = i / nbins, (i + 1) / nbins
        m = (arr[:, 0] >= lo) & (arr[:, 0] < hi)
        if m.sum() >= 5:
            out.append((float(arr[m, 0].mean()), float(arr[m, 1].mean()), int(m.sum())))
    return out
