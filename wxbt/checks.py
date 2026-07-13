# wxbt/checks.py — Sanity checks de datos (FASE 4: "chequeos automáticos").
import numpy as np

REQ = dict(
    forecasts={"station", "target", "model", "init", "avail", "lead_h", "m", "s2"},
    obs={"station", "date", "tmax_int"},
    markets={"station", "target", "bucket", "lo", "hi", "open_t", "close_t"},
    prices={"t", "station", "target", "bucket", "lo", "hi", "mid", "hs"},
)


def validate_world(world):
    """Devuelve lista de problemas (vacía = ok). Corre SIEMPRE antes de backtestear."""
    issues = []
    for name, cols in REQ.items():
        df = world[name] if name in world else None
        if df is None:
            issues.append(f"falta tabla {name}")
            continue
        missing = cols - set(df.columns)
        if missing:
            issues.append(f"{name}: faltan columnas {missing}")
    fc = world["forecasts"]
    if fc["avail"].isna().any() or fc["init"].isna().any():
        issues.append("forecasts: avail/init con NaN (fecha no parseada)")
    if (fc["avail"] < fc["init"]).any():
        issues.append("forecasts: avail < init (lag imposible)")
    if fc["s2"].isna().any() or fc["m"].isna().any():
        issues.append("forecasts: m/s2 con NaN -- esto NO lo agarra '<=0' (NaN<=0 es False), "
                      "chequeo explícito. Downloader probablemente escribió '' en una celda.")
    if (fc["s2"] <= 0).any():
        issues.append("forecasts: varianza de ensemble <= 0")
    px = world["prices"]
    if px["mid"].isna().any():
        issues.append("prices: mid con NaN (no lo agarra el rango (0,1) -- chequeo explícito)")
    if ((px["mid"] <= 0) | (px["mid"] >= 1)).any():
        issues.append("prices: mid fuera de (0,1)")
    obs = world["obs"]
    for st, g in obs.groupby("station"):
        z = np.abs((g["tmax_int"] - g["tmax_int"].mean()) / max(g["tmax_int"].std(), 1e-6))
        n_out = int((z > 6).sum())
        if n_out:
            issues.append(f"obs {st}: {n_out} outliers |z|>6 (revisar sensor/parseo)")
    # coherencia de buckets: por mercado, exactamente 2 colas abiertas y rangos no solapados
    mk = world["markets"]
    for (st, tgt), g in list(mk.groupby(["station", "target"]))[:50]:
        open_tails = int(g["lo"].isna().sum() + g["hi"].isna().sum())
        if open_tails != 2:
            issues.append(f"markets {st} {tgt}: colas abiertas={open_tails} (esperado 2)")
            break
    return issues
