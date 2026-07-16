# wxbt/calibration.py — EMOS-lite (NGR simplificado) por (estación, lead_day).
# Señal (FASE 3): mezcla multimodelo ponderada por skill + corrección afín de media (a+b·m)
# + inflación de varianza (c+d·s2). "Raw" = mezcla sin corrección (baseline a batir).
import numpy as np
from math import erf, pi, sqrt, exp

SQRT2 = sqrt(2.0)


def phi(z):  # pdf normal estándar
    return exp(-0.5 * z * z) / sqrt(2.0 * pi)


def Phi(z):  # cdf normal estándar
    return 0.5 * (1.0 + erf(z / SQRT2))


def crps_normal(y, mu, sigma):
    """CRPS cerrado para Normal(mu, sigma). Menor = mejor."""
    sigma = max(float(sigma), 1e-6)
    z = (y - mu) / sigma
    return sigma * (z * (2.0 * Phi(z) - 1.0) + 2.0 * phi(z) - 1.0 / sqrt(pi))


def mixture_mean_var(ms, s2s, ws):
    """Media/varianza de mezcla de normales por modelo (pesos ws, suman 1)."""
    ms, s2s, ws = np.asarray(ms, float), np.asarray(s2s, float), np.asarray(ws, float)
    m = float(np.sum(ws * ms))
    s2 = float(np.sum(ws * (s2s + ms**2)) - m**2)
    return m, max(s2, 1e-6)


def fit_emos(samples, sigma_floor):
    """samples: lista de dicts {'y': obs, 'per_model': {model:(m, s2)}}.
    Devuelve params dict o None si muestra insuficiente.
    Ajuste: (1) pesos w_k ∝ 1/MSE_k; (2) OLS y = a + b·m_mix; (3) OLS resid² = c + d·s2_mix (con pisos)."""
    if len(samples) < 25:
        return None
    models = sorted({k for s in samples for k in s["per_model"]})
    # (1) pesos por skill (MSE de la media de cada modelo, solo muestras donde el modelo está)
    mses = {}
    for k in models:
        errs = [(s["per_model"][k][0] - s["y"]) ** 2 for s in samples if k in s["per_model"]]
        if len(errs) >= 15:
            mses[k] = max(np.mean(errs), 1e-3)
    if not mses:
        return None
    inv = {k: 1.0 / v for k, v in mses.items()}
    tot = sum(inv.values())
    w = {k: v / tot for k, v in inv.items()}

    # (2)+(3) sobre la mezcla; varianza depende de spread Y de lead (diagnóstico FASE 4:
    # s2 solo no captura el crecimiento de error con horizonte -> sigma mal por lead)
    M, S2, LD, Y = [], [], [], []
    for s in samples:
        ks = [k for k in s["per_model"] if k in w]
        if not ks:
            continue
        ws = np.array([w[k] for k in ks]); ws /= ws.sum()
        m, s2 = mixture_mean_var([s["per_model"][k][0] for k in ks],
                                 [s["per_model"][k][1] for k in ks], ws)
        M.append(m); S2.append(s2); LD.append(float(s.get("ld", 2.0))); Y.append(s["y"])
    M, S2, LD, Y = np.array(M), np.array(S2), np.array(LD), np.array(Y)
    if len(M) < 25:
        return None
    A = np.vstack([np.ones_like(M), M]).T
    (a, b), *_ = np.linalg.lstsq(A, Y, rcond=None)
    resid2 = (Y - (a + b * M)) ** 2
    B = np.vstack([np.ones_like(S2), S2, LD]).T
    (c, d, e), *_ = np.linalg.lstsq(B, resid2, rcond=None)
    c = max(float(c), 0.0)
    d = float(np.clip(d, 0.05, 10.0))
    e = max(float(e), 0.0)
    return dict(w=w, a=float(a), b=float(b), c=c, d=d, e=e, n=len(M), floor=float(sigma_floor))


def predict(params, per_model, ld=2.0):
    """(mu, sigma) calibrados desde forecasts vigentes {model:(m,s2)} a lead ld (días)."""
    ks = [k for k in per_model if k in params["w"]]
    if not ks:
        return None
    ws = np.array([params["w"][k] for k in ks]); ws /= ws.sum()
    m, s2 = mixture_mean_var([per_model[k][0] for k in ks], [per_model[k][1] for k in ks], ws)
    mu = params["a"] + params["b"] * m
    var = max(params["c"] + params["d"] * s2 + params.get("e", 0.0) * ld, params["floor"] ** 2)
    return mu, sqrt(var)


def predict_raw(per_model, sigma_floor):
    """Baseline sin calibrar: mezcla equiponderada de los modelos disponibles."""
    ks = sorted(per_model)
    if not ks:
        return None
    ws = np.full(len(ks), 1.0 / len(ks))
    m, s2 = mixture_mean_var([per_model[k][0] for k in ks], [per_model[k][1] for k in ks], ws)
    return m, sqrt(max(s2, sigma_floor ** 2))
