# wxbt/synth.py — Mundo sintético para VALIDAR EL MOTOR (no el edge real).
# Genera: verdad diaria por estación, ensembles multimodelo con sesgo+subdispersión,
# mercados con buckets estilo Polymarket, y precios en dos regímenes:
#   'inefficient': crowd = 1 solo modelo sin corregir + overconfidence + anclaje recency + sobreprecio de colas
#   'efficient' : crowd = probabilidades calibradas correctas (+ruido chico)  -> NULL TEST:
#                 si el motor "gana" acá neto de costos, hay fuga (look-ahead / contabilidad rota).
import numpy as np
import pandas as pd
from math import sin, pi, sqrt
from .config import STATIONS, MODELS
from .market import bucket_prob

H6 = pd.Timedelta(hours=6)


def _seasonal(st, doy):
    return st.clim_mean + st.clim_amp * sin(2 * pi * (doy - 105) / 365.0)


def _sigma_err(st, lead_days):
    base, k = (1.6, 0.9) if st.unit == "F" else (0.9, 0.5)
    return base + k * lead_days


def gen_world(n_days=420, seed=42, regime="inefficient", start="2026-01-01", hist_days=365):
    """hist_days: obs previas SIN forecasts/mercados -> climatología (espeja historia IEM real)."""
    rng = np.random.default_rng(seed)
    dates_all = pd.date_range(pd.Timestamp(start) - pd.Timedelta(days=hist_days), periods=n_days + hist_days, freq="D")
    dates = dates_all[hist_days:]
    groups = sorted({s.group for s in STATIONS})

    # --- Verdad: estacional + AR(1) correlacionado por grupo sinóptico ---
    phi_ar, rho = 0.70, 0.60
    z_group = {g: 0.0 for g in groups}
    z_st = {s.code: 0.0 for s in STATIONS}
    truth = {}
    for d in dates_all:
        eg = {g: rng.normal() for g in groups}
        for g in groups:
            z_group[g] = phi_ar * z_group[g] + sqrt(1 - phi_ar**2) * eg[g]
        for s in STATIONS:
            e = rng.normal()
            z_st[s.code] = phi_ar * z_st[s.code] + sqrt(1 - phi_ar**2) * e
            z = sqrt(rho) * z_group[s.group] + sqrt(1 - rho) * z_st[s.code]
            amp = 4.5 if s.unit == "F" else 2.6
            truth[(s.code, d.date())] = _seasonal(s, d.dayofyear) + amp * z

    obs = pd.DataFrame([{"station": c, "date": d, "tmax": t, "tmax_int": int(round(t))}
                        for (c, d), t in truth.items()])

    # --- Sesgos fijos por (modelo, estación): lo que la calibración debe encontrar ---
    bias = {}
    brng = np.random.default_rng(seed + 1)
    for s in STATIONS:
        sc = 1.2 if s.unit == "F" else 0.7
        for mname in MODELS:
            bias[(mname, s.code)] = float(brng.normal(0, sc))

    # --- Forecasts: 4 corridas/día por modelo, targets D+0..D+3, avail = init + lag ---
    fc_rows = []
    for s in STATIONS:
        for d in dates:
            for mname, mi in MODELS.items():
                for run in mi["runs"]:
                    init = pd.Timestamp(d) + pd.Timedelta(hours=run)
                    avail = init + pd.Timedelta(hours=mi["lag_h"])
                    for ahead in range(0, 4):
                        tgt = (d + pd.Timedelta(days=ahead)).date()
                        if (s.code, tgt) not in truth:
                            continue
                        close = pd.Timestamp(tgt) + pd.Timedelta(hours=24 - s.utc_off)  # fin día local en UTC
                        lead_h = (close - avail).total_seconds() / 3600.0
                        if lead_h <= 1 or lead_h > 78:
                            continue
                        ld = min(max(lead_h / 24.0, 0.1), 3.5)
                        se = _sigma_err(s, ld)
                        m = truth[(s.code, tgt)] + bias[(mname, s.code)] + rng.normal(0, 0.8 * se)
                        s2 = (0.55 * se) ** 2 * float(rng.uniform(0.7, 1.3))  # spread subdispersivo
                        fc_rows.append((s.code, tgt, mname, init, avail, lead_h, m, s2))
    fc = pd.DataFrame(fc_rows, columns=["station", "target", "model", "init", "avail", "lead_h", "m", "s2"])

    # --- Mercados: buckets alrededor de la CLIMATOLOGÍA (sin fuga de verdad) ---
    mk_rows = []
    for s in STATIONS:
        w = 2 if s.unit == "F" else 1
        for d in dates:
            center = int(round(_seasonal(s, pd.Timestamp(d).dayofyear)))
            if s.unit == "F" and center % 2 == 1:
                center -= 1
            open_t = pd.Timestamp(d) - pd.Timedelta(days=3)
            close_t = pd.Timestamp(d) + pd.Timedelta(hours=24 - s.utc_off)
            edges = [center + w * k for k in range(-3, 4)]  # 6 buckets cerrados
            bid = 0
            mk_rows.append((s.code, d.date(), bid, None, edges[0] - 1, open_t, close_t)); bid += 1
            for lo in edges[:-1]:
                mk_rows.append((s.code, d.date(), bid, lo, lo + w - 1, open_t, close_t)); bid += 1
            mk_rows.append((s.code, d.date(), bid, edges[-1], None, open_t, close_t))
    mk = pd.DataFrame(mk_rows, columns=["station", "target", "bucket", "lo", "hi", "open_t", "close_t"])
    for c in ("lo", "hi"):
        mk[c] = mk[c].astype(object).where(mk[c].notna(), None)

    # --- Precios cada 6h por mercado-bucket ---
    def _np_index(df):
        out = {}
        for k, g in df.groupby(["station", "target", "model"] if "model_key" not in df else None):
            pass
        return out
    fc_idx = {}
    for k, g in fc.groupby(["station", "target", "model"]):
        g = g.sort_values("avail")
        fc_idx[k] = (g["avail"].values.astype("datetime64[ns]"), g["m"].values, g["avail"].values)
    def latest(code, tgt, mname, t):
        tup = fc_idx.get((code, tgt, mname))
        if tup is None: return None
        av, m, avv = tup
        i = np.searchsorted(av, np.datetime64(t), side="right") - 1
        if i < 0: return None
        return float(m[i]), pd.Timestamp(avv[i])
    px_rows = []
    for (code, tgt), grp in mk.groupby(["station", "target"]):
        s = next(x for x in STATIONS if x.code == code)
        close_t = grp.close_t.iloc[0]
        ts = pd.date_range(grp.open_t.iloc[0] + H6, close_t - H6, freq="6h")
        buckets = grp[["bucket", "lo", "hi"]].to_records(index=False)
        for t in ts:
            if regime == "efficient":
                # crowd = POSTERIOR BAYESIANO EXACTO dado TODO lo público:
                # prior AR(1) desde la última obs realizada + likelihood de cada modelo.
                # (El null test cazó 3 versiones anteriores mal construidas de este crowd.)
                amp = 4.5 if s.unit == "F" else 2.6
                seas_tgt = _seasonal(s, pd.Timestamp(tgt).dayofyear)
                # última fecha realizada (cierre local <= t) con verdad conocida
                prec, num = 0.0, 0.0
                for back in range(1, 8):
                    d0 = (pd.Timestamp(tgt) - pd.Timedelta(days=back)).date()
                    if (code, d0) in truth and pd.Timestamp(d0) + pd.Timedelta(hours=24 - s.utc_off) <= t:
                        z0 = truth[(code, d0)] - _seasonal(s, pd.Timestamp(d0).dayofyear)
                        k = back
                        pv = amp**2 * (1 - phi_ar**(2*k))
                        if pv > 1e-9:
                            prec += 1.0 / pv; num += (phi_ar**k * z0) / pv
                        break
                got = False
                for mname in MODELS:
                    lat = latest(code, tgt, mname, t)
                    if lat is None:
                        continue
                    m_val, avail_ts = lat
                    lead_d = max((close_t - avail_ts).total_seconds() / 86400.0, 0.1)
                    var_f = (0.8 * _sigma_err(s, lead_d)) ** 2
                    prec += 1.0 / var_f
                    num += (m_val - bias[(mname, code)] - seas_tgt) / var_f
                    got = True
                if not got or prec <= 0:
                    continue
                mu_c = seas_tgt + num / prec
                sd_c = (1.0 / prec) ** 0.5
                probs = np.array([bucket_prob(mu_c, sd_c, lo, hi) for _, lo, hi in buckets])
                probs = probs + rng.normal(0, 0.004, len(probs))
            else:
                # crowd ineficiente: solo GEFS crudo, overconfident, anclado a ayer, colas sobrepreciadas
                lat = latest(code, tgt, "gefs", t)
                if lat is None:
                    continue
                m_val, _av = lat
                yday = truth.get((code, (pd.Timestamp(tgt) - pd.Timedelta(days=1)).date()))
                mu_c = 0.85 * m_val + 0.15 * yday if (yday is not None and t >= pd.Timestamp(tgt)) else m_val
                ld = max((close_t - t).total_seconds() / 3600.0 / 24.0, 0.1)
                sd_c = 0.55 * _sigma_err(s, ld)  # overconfident: su resid real es 0.8*sigma_e (1 modelo)
                probs = np.array([bucket_prob(mu_c, sd_c, lo, hi) for _, lo, hi in buckets])
                lam = 0.05                        # favorite-longshot: masa uniforme a colas
                probs = (1 - lam) * probs + lam / len(probs)
                probs = probs + rng.normal(0, 0.012, len(probs))
            probs = np.clip(probs, 0.002, 0.998)
            probs = probs / probs.sum()
            hs = 0.015 if regime == "inefficient" else 0.012
            for (b, lo, hi), p in zip(buckets, probs):
                px_rows.append((t, code, tgt, int(b), None if pd.isna(lo) else lo,
                                None if pd.isna(hi) else hi, float(p), hs))
    px = pd.DataFrame(px_rows, columns=["t", "station", "target", "bucket", "lo", "hi", "mid", "hs"])
    px["lo"] = px["lo"].astype(object).where(px["lo"].notna(), None)
    px["hi"] = px["hi"].astype(object).where(px["hi"].notna(), None)
    return dict(obs=obs, forecasts=fc, markets=mk, prices=px, bias=bias, regime=regime)
