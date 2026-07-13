#!/usr/bin/env python3
# scripts/check_predictions.py — Compara NUESTRAS predicciones (predictions_forward.csv) contra el
# BUCKET GANADOR real, para los targets ya resueltos. Solo lectura, no escribe (salvo --csv).
# [Creado 2026-07-08.] Correr cuando pase el tiempo (D+1 ya resuelto): 'python scripts/check_predictions.py'
#
# DOS RESOLUCIONES, SEPARADAS (toda la sesion mostro que difieren):
#   * MERCADO  = Gamma outcomePrices (lo que PAGA Polymarket, via Weather Underground).
#   * FISICA   = obs IEM (lo que el modelo intenta predecir). Puede no estar (red restringida) -> se
#                omite esa mitad, no rompe.
#
# METRICAS (una prediccion probabilistica NO se juzga solo por "acerto el bucket"):
#   * hit         = nuestro bucket MAS probable fue el ganador (0/1).
#   * p_win       = probabilidad que le dimos al bucket ganador (mas alta = mejor). Calibrado vs crudo.
#   * CRPS        = error probabilistico continuo vs la temp fisica real (mas bajo = mejor). Solo FISICA.
#   * PIT         = Phi((y-mu)/sigma); si el modelo esta bien calibrado se distribuye ~Uniforme(0,1).
# El punto: ver si el CALIBRADO le gana al CRUDO fuera de muestra, contra el ganador REAL.
import argparse, json, math, os, sys
import datetime as dt
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from wxbt.market import bucket_prob, resolve_bucket             # noqa: E402
from wxbt.calibration import crps_normal, Phi                   # noqa: E402
from show_live import CITY_SERIES, CITY_STATION, parse_bucket, STATIONS, GAMMA  # noqa: E402
import requests                                                 # noqa: E402
import re                                                       # noqa: E402

PRED = "data/predictions_forward.csv"
DATE_RE = re.compile(r"-on-([a-z]+)-(\d+)-(\d+)")
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
NETWORKS = {"KLGA": "NY_ASOS", "KORD": "IL_ASOS", "EGLC": "GB__ASOS",
            "LFPB": "FR__ASOS", "RJTT": "JP__ASOS", "RKSI": "KR__ASOS",
            "ZSPD": "CN__ASOS", "ZBAA": "CN__ASOS", "RCSS": "TW__ASOS",
            "LEMD": "ES__ASOS", "EDDM": "DE__ASOS", "LIMC": "IT__ASOS",
            # [2026-07-13] nuevas (redes IEM verificadas). Miami US -> FL_ASOS + station 'MIA'.
            "NZWN": "NF__ASOS", "LTAC": "TR__ASOS", "KMIA": "FL_ASOS",
            "WSSS": "SG__ASOS", "WMKK": "MY__ASOS", "ZGSZ": "CN__ASOS",
            # [2026-07-13 tarde] +11. US strip-K (KSFO->SFO). Toronto = red Ontario CA_ON_ASOS.
            "KSFO": "CA_ASOS", "KLAX": "CA_ASOS", "KDAL": "TX_ASOS", "KATL": "GA_ASOS",
            "KHOU": "TX_ASOS", "KAUS": "TX_ASOS", "CYYZ": "CA_ON_ASOS", "SBGR": "BR__ASOS",
            "SAEZ": "AR__ASOS", "MMMX": "MX__ASOS", "EFHK": "FI__ASOS"}


MONTHS_EN = ["january", "february", "march", "april", "may", "june", "july",
             "august", "september", "october", "november", "december"]
CITY_OF = {v: k for k, v in CITY_STATION.items()}


def resolved_buckets(station_targets):
    """{(station,target): {"buckets":[(lo,hi)], "winner":(lo,hi)|None}} consultando Gamma POR SLUG
    directo (1 request por par). [FIX 2026-07-12, pedido Santiago] ganador SOLO con el mercado
    RESUELTO (closed / umaResolutionStatus=resolved): un bucket cotizando >=0.99 con el dia EN CURSO
    no cuenta — inflaba stats/leaderboard con mercados sin terminar."""
    out = {}
    for station, tgt in set(station_targets):
        city = CITY_OF.get(station)
        if not city:
            continue
        slug = f"highest-temperature-in-{city}-on-{MONTHS_EN[tgt.month-1]}-{tgt.day}-{tgt.year}"
        try:
            r = requests.get(f"{GAMMA}/events", params={"slug": slug}, timeout=30)
            evs = r.json() if r.status_code == 200 else []
        except Exception as e:
            print(f"[WARN] Gamma slug {slug}: {e}", file=sys.stderr); continue
        if not evs:
            continue
        ev_closed = bool(evs[0].get("closed"))
        buckets, winner = [], None
        for mk in evs[0].get("markets", []):
            lo, hi = parse_bucket(mk.get("groupItemTitle"))
            if lo is None and hi is None:
                continue
            buckets.append((lo, hi))
            op = mk.get("outcomePrices")
            try:
                yes = float(json.loads(op)[0]) if isinstance(op, str) else float(op[0])
            except Exception:
                yes = None
            resolved = (ev_closed or bool(mk.get("closed"))
                        or str(mk.get("umaResolutionStatus") or "").lower() == "resolved")
            if yes is not None and yes >= 0.99 and resolved:
                winner = (lo, hi)
        out[(station, tgt)] = {"buckets": buckets, "winner": winner}
    return out


def fetch_obs_iem(station, d):
    """tmax observada (unidad de la estacion) para (station, date) desde IEM. None si falla/no hay."""
    net = NETWORKS.get(station)
    if not net:
        return None
    url = "https://mesonet.agron.iastate.edu/cgi-bin/request/daily.py"
    p = dict(network=net, stations=station.lstrip("K") if station.startswith("K") else station,
             var="max_temp_f", year1=d.year, month1=d.month, day1=d.day,
             year2=d.year, month2=d.month, day2=d.day, format="csv")
    try:
        r = requests.get(url, params=p, timeout=60); r.raise_for_status()
    except Exception:
        return None
    lines = [l for l in r.text.splitlines() if l and not l.startswith("#")]
    if len(lines) < 2:
        return None
    hdr = lines[0].split(",")
    row = dict(zip(hdr, lines[1].split(",")))
    v = row.get("max_temp_f")
    if not v or v in ("None", "M"):
        return None
    tf = float(v)
    return tf if station.startswith("K") else (tf - 32) * 5 / 9   # °C para no-US


def winner_by_temp(buckets, t_int):
    for lo, hi in buckets:
        if resolve_bucket(t_int, lo, hi):
            return (lo, hi)
    return None


def score(pred, buckets, winner, unit):
    """Metricas de una prediccion vs un bucket ganador. Devuelve dict o None si sin ganador/buckets."""
    if winner is None or not buckets:
        return None
    # [FIX 2026-07-10] resolucion FLOOR de WU: bucket [lo,hi] gana si floor(obs) in [lo,hi] <=>
    # obs in [lo, hi+1). El bucket_prob del motor es half-up; correr mu -0.5 lo vuelve floor-exacto.
    # Mantiene coherencia con el pick floor(mu) del dashboard y con winner = floor(obs).
    def probs(mu, sigma):
        return [bucket_prob(mu - 0.5, sigma, lo, hi) for lo, hi in buckets]
    pc = probs(pred.mu_cal, pred.sigma_cal)
    pr = probs(pred.mu_raw, pred.sigma_raw)
    wi = buckets.index(winner)
    argmax_cal = buckets[max(range(len(buckets)), key=lambda i: pc[i])]
    argmax_raw = buckets[max(range(len(buckets)), key=lambda i: pr[i])]
    return dict(hit_cal=int(argmax_cal == winner), hit_raw=int(argmax_raw == winner),
                pwin_cal=pc[wi], pwin_raw=pr[wi])


def main(a):
    today = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    if not os.path.exists(PRED):
        print(f"[ABORT] no existe {PRED} (correr accumulate_predictions.py primero).", file=sys.stderr)
        sys.exit(1)
    p = pd.read_csv(PRED, parse_dates=["target"]); p["target"] = p["target"].dt.date
    due = p[p["target"] < today].copy()
    if due.empty:
        nearest = p["target"].min() if len(p) else None
        print(f"Nada resuelto todavia (hoy {today}). La prediccion mas temprana es para {nearest}. "
              f"Volve cuando ese target ya haya pasado.")
        return
    print(f"Chequeando {len(due)} predicciones con target < {today} ...\n")
    unit_by_st = {c: u for c, (_, _, _, u) in STATIONS.items()}
    resb = resolved_buckets(list(due[["station", "target"]].itertuples(index=False, name=None)))

    recs = []
    for pred in due.itertuples():
        info = resb.get((pred.station, pred.target))
        unit = unit_by_st[pred.station]
        # --- resolucion MERCADO ---
        if info and info["winner"]:
            s = score(pred, info["buckets"], info["winner"], unit)
            if s:
                recs.append(dict(res="mercado", station=pred.station, lead=pred.lead_day, **s, crps_cal=None, crps_raw=None))
        # --- resolucion FISICA (obs IEM) ---
        obs = fetch_obs_iem(pred.station, pred.target)
        if obs is not None and info and info["buckets"]:
            # [FIX 2026-07-10 auditoria] la regla WU confirmada 2x en vivo (Milan 34.x->34,
            # Beijing 35.9->35) es FLOOR de la obs, no half-up: floor(obs) en ambas unidades.
            t_int = int(math.floor(obs))
            wphys = winner_by_temp(info["buckets"], t_int)
            s = score(pred, info["buckets"], wphys, unit)
            if s:
                s["crps_cal"] = crps_normal(obs, pred.mu_cal, pred.sigma_cal)
                s["crps_raw"] = crps_normal(obs, pred.mu_raw, pred.sigma_raw)
                recs.append(dict(res="fisica", station=pred.station, lead=pred.lead_day, **s))

    if not recs:
        print("Targets pasados pero sin resolucion disponible aun (mercado no cerrado / obs no publicada).")
        return
    df = pd.DataFrame(recs)
    for res in ("mercado", "fisica"):
        d = df[df.res == res]
        if d.empty:
            print(f"[{res}] sin datos.\n"); continue
        print(f"=== resolucion {res.upper()} (n={len(d)}) ===")
        print(f"  hit rate:   calibrado {d.hit_cal.mean():.2f}   crudo {d.hit_raw.mean():.2f}")
        print(f"  prob al ganador (mayor=mejor): calibrado {d.pwin_cal.mean():.3f}   crudo {d.pwin_raw.mean():.3f}")
        if d.crps_cal.notna().any():
            print(f"  CRPS (menor=mejor):            calibrado {d.crps_cal.mean():.3f}   crudo {d.crps_raw.mean():.3f}")
        print(f"  por estacion (hit_cal / pwin_cal):")
        for st, g in d.groupby("station"):
            print(f"    {st}: hit {g.hit_cal.mean():.2f}  pwin {g.pwin_cal.mean():.3f}  n={len(g)}")
        print()
    if a.csv:
        df.to_csv(a.csv, index=False)
        print(f"Detalle por prediccion -> {a.csv}")
    print("Recordatorio: mas señal con mas N. Con pocos dias esto es indicativo, no veredicto.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Compara predicciones forward vs bucket ganador real (mercado + fisica).")
    ap.add_argument("--date", default=None, help="fecha 'hoy' YYYY-MM-DD (default: hoy real)")
    ap.add_argument("--csv", default=None, help="volcar el detalle por prediccion a este CSV")
    main(ap.parse_args())
