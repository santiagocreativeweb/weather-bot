# wxbt/market.py — Buckets, fees, Kelly y modelo de ejecución (bracketing).
# [ASUNCION] Resolución en grados ENTEROS (WU) con redondeo half-up: bucket [lo,hi] gana si
#            lo-0.5 <= T_real < hi+0.5. Colas abiertas: lo=None o hi=None.
# Ejecución (FASE 3/4): dos modos que ACOTAN la realidad:
#   'taker' = cruza el spread (ejecuta en ask) y paga fee  -> cota PESIMISTA
#   'mid'   = ejecuta al mid sin fee (aprox maker llenado)  -> cota OPTIMISTA
# El resultado real (maker con fill parcial) vive entre ambos.
from math import inf, isnan
from .calibration import Phi
from .config import FEE_RATE_WEATHER


def _open(x):
    """None o NaN = cola abierta (pandas coerciona None->NaN en columnas numéricas)."""
    return x is None or (isinstance(x, float) and isnan(x))


def bucket_prob(mu, sigma, lo, hi):
    """P(bucket) bajo Normal(mu, sigma) con redondeo entero half-up."""
    sigma = max(float(sigma), 1e-6)
    a = -inf if _open(lo) else (lo - 0.5 - mu) / sigma
    b = inf if _open(hi) else (hi + 0.5 - mu) / sigma
    pa = 0.0 if a == -inf else Phi(a)
    pb = 1.0 if b == inf else Phi(b)
    return max(min(pb - pa, 1.0), 0.0)


def resolve_bucket(t_int, lo, hi):
    """¿Gana el bucket con la temperatura entera observada?"""
    lo_ok = _open(lo) or (t_int >= lo)
    hi_ok = _open(hi) or (t_int <= hi)
    return lo_ok and hi_ok


def taker_fee_per_share(price, rate=FEE_RATE_WEATHER):
    """Fee taker por share (USDC): rate * p * (1-p). [VERIFICAR-VIVO] rate weather=0.05."""
    return rate * price * (1.0 - price)


def kelly_fraction_yes(p, price):
    """Fracción Kelly para comprar un token a `price` con prob real `p` (payout $1)."""
    if price >= 1.0 or price <= 0.0:
        return 0.0
    return max((p - price) / (1.0 - price), 0.0)


def exec_price(mid, hs, side_buy, mode):
    """Precio de ejecución del token que compramos. hs = half-spread."""
    if mode == "mid":
        return min(max(mid, 0.001), 0.999)
    # taker: compramos al ask
    return min(max(mid + hs, 0.001), 0.999)


def entry_cost_per_share(px, mode):
    """Costo total por share incl. fee según modo."""
    fee = taker_fee_per_share(px) if mode == "taker" else 0.0
    return px + fee, fee


def exit_proceeds_per_share(mid, hs, mode):
    """Venta anticipada del token: taker vende al bid y paga fee; mid sin fee."""
    if mode == "mid":
        px = min(max(mid, 0.001), 0.999)
        return px, px, 0.0
    px = min(max(mid - hs, 0.001), 0.999)
    fee = taker_fee_per_share(px)
    return px - fee, px, fee
