#!/usr/bin/env python3
# scripts/backfill_check.py — Reconstruye las predicciones POINT-IN-TIME del motor para un rango de
# targets PASADOS (default 2026-07-01..2026-07-08) y las compara contra el bucket GANADOR real.
# [Creado 2026-07-08 a pedido: "chequear con dias anteriores las predicciones y el bucket ganador".]
#
# ANTI-LOOK-AHEAD (por construccion, igual que el backtest):
#   * m: Previous-Runs API — temperature_2m = corrida de la MISMA manana del target (lead 1),
#     previous_day1 = corrida del dia ANTERIOR (lead 2), previous_day2 = de 2 dias antes (lead 3).
#     Es lo que el modelo dijo EN ESE MOMENTO; no se puede contaminar con el futuro.
#   * params EMOS + climatologia: entrenados SOLO con el historico de forecasts.csv/obs.csv
#     (targets <= 2026-06-30) -> ninguna fila del rango chequeado participo del ajuste.
#   * s2: ultimo s2 modelado por (estacion, modelo, _lead_day) del historico.
#
# GANADOR, en DOS resoluciones separadas (toda la FASE 4 mostro que difieren):
#   * MERCADO = Gamma outcomePrices (lo que PAGO Polymarket via Weather Underground).
#   * FISICA  = MAX real de obs IEM (skill meteorologico puro).
#
# Salida: tabla por dia + resumen por lead (hit/prob-al-ganador/CRPS, calibrado vs crudo) +
# data/backfill_check.csv + bloque listo para pegar en la planilla.
import argparse, csv, json, os, re, sys
import datetime as dt
import requests
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from wxbt import config as C                                    # noqa: E402
from wxbt.engine import fit_all, clim_val, _lead_day            # noqa: E402
from wxbt.calibration import predict, predict_raw, crps_normal  # noqa: E402
from wxbt.market import bucket_prob, resolve_bucket             # noqa: E402
from wxbt.observations import fetch_iem_maxima                  # noqa: E402
from show_live import (STATIONS, GAMMA, PREV_RUNS, CITY_SERIES, CITY_STATION,  # noqa: E402
                       parse_bucket, daily_tmax)
from check_predictions import fetch_obs_iem                     # noqa: E402

OUT_CSV = "data/backfill_check.csv"
# lead -> columna Previous Runs (IDENTICO a download_openmeteo.LEAD_COL — el training uso esto)
LEAD_COL = {1: "temperature_2m", 2: "temperature_2m_previous_day1", 3: "temperature_2m_previous_day2"}
MODELS_OM = {"gefs": ("gfs_seamless", 5.0), "ecmwf": ("ecmwf_ifs025", 7.0), "icon": ("icon_seamless", 7.0)}
DATE_RE = re.compile(r"-on-([a-z]+)-(\d+)-(\d+)")
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}


def fetch_prevruns(code, lat, lon, off, unit, om, d0, d1):
    """{(target, lead): m} para un (estacion, modelo) en el rango, con las 3 columnas."""
    p = dict(latitude=lat, longitude=lon, models=om, hourly=",".join(LEAD_COL.values()),
             start_date=(d0 - dt.timedelta(days=1)).isoformat(),
             end_date=(d1 + dt.timedelta(days=1)).isoformat(), timezone="UTC",
             temperature_unit=("fahrenheit" if unit == "F" else "celsius"))
    r = requests.get(PREV_RUNS, params=p, timeout=90); r.raise_for_status()
    h = r.json()["hourly"]
    out = {}
    for lead, col in LEAD_COL.items():
        if col not in h:
            continue
        for d, m in daily_tmax(h["time"], h[col], off).items():
            if d0 <= d <= d1:
                out[(d, lead)] = m
    return out


def latest_s2(fc):
    fc = fc.copy()
    fc["ld"] = fc["lead_h"].map(_lead_day)
    fc = fc.sort_values("avail")
    return {(r.station, r.model, r.ld): r.s2 for r in fc.itertuples()}


def market_winners(d0, d1):
    """{(station,target): {"buckets":[(lo,hi)], "winner":(lo,hi)|None}} paginando Gamma CERRADOS
    hasta vaciar cada serie (los eventos recientes pueden estar en las ultimas paginas)."""
    out = {}
    for city, sid in CITY_SERIES.items():
        station = CITY_STATION[city]
        offset = 0
        while offset < 2000:
            try:
                r = requests.get(f"{GAMMA}/events",
                                 params={"series_id": sid, "closed": "true", "limit": 100,
                                         "offset": offset}, timeout=60)
                r.raise_for_status()
                batch = r.json()
            except Exception as e:
                print(f"[WARN] Gamma {city} offset {offset}: {e}", file=sys.stderr); break
            if not batch:
                break
            for e in batch:
                m = DATE_RE.search(e.get("slug") or "")
                if not m or m.group(1) not in MONTHS:
                    continue
                try:
                    tgt = dt.date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2)))
                except ValueError:
                    continue
                if not (d0 <= tgt <= d1):
                    continue
                buckets, winner = [], None
                for mk in e.get("markets", []):
                    lo, hi = parse_bucket(mk.get("groupItemTitle"))
                    if lo is None and hi is None:
                        continue
                    buckets.append((lo, hi))
                    op = mk.get("outcomePrices")
                    try:
                        yes = float(json.loads(op)[0]) if isinstance(op, str) else float(op[0])
                    except Exception:
                        yes = None
                    if yes is not None and yes >= 0.99:
                        winner = (lo, hi)
                out[(station, tgt)] = {"buckets": buckets, "winner": winner}
            offset += 100
    return out


def winner_by_temp(buckets, t_int):
    for lo, hi in buckets:
        if resolve_bucket(t_int, lo, hi):
            return (lo, hi)
    return None


def blabel(lo, hi, unit):
    deg = "°F" if unit == "F" else "°C"
    if lo is None:
        return f"<= {hi}{deg}"
    if hi is None:
        return f">= {lo}{deg}"
    return f"{lo}-{hi}{deg}" if lo != hi else f"{lo}{deg}"


def main(a):
    if a.extend:
        # [2026-07-12] modo SEMANAL: continuar desde el ultimo target del CSV hasta AYER (dia
        # completo; hoy contamina con obs parcial). Implica --append. Alimenta el D1 dinamico
        # de calib_lab.py — sin esto el refresh del bias vuelve a ser un no-op silencioso.
        # SOLAPE de 3 dias (auditoria 2026-07-12): re-visita los ultimos targets ya escritos para
        # rellenar win_mkt/max_real que Gamma/IEM no tenian aun en la corrida anterior (sin esto,
        # una corrida temprana congela None para siempre = atricion silenciosa del sample).
        if not os.path.exists(OUT_CSV):
            print(f"[ABORT] --extend sin {OUT_CSV} previo.", file=sys.stderr); sys.exit(1)
        prev = pd.read_csv(OUT_CSV, usecols=["target"])
        prev_max = pd.to_datetime(prev["target"]).max().date()
        a.start = (prev_max - dt.timedelta(days=2)).isoformat()
        a.end = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        a.append = True
        if prev_max >= dt.date.fromisoformat(a.end):
            print(f"backfill_check.csv ya cubre hasta ayer ({a.end}); nada que extender."); return
    d0, d1 = dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end)
    if not a.append and os.path.exists(OUT_CSV):
        # guard anti-pisado (auditoria 2026-07-12): sin --append, abortar si el CSV existente
        # tiene targets FUERA del rango pedido (pisarlo destruiria labels acumulados).
        old_t = pd.to_datetime(pd.read_csv(OUT_CSV, usecols=["target"])["target"]).dt.date
        if old_t.min() < d0 or old_t.max() > d1:
            print(f"[ABORT] {OUT_CSV} cubre {old_t.min()}..{old_t.max()}, fuera del rango pedido "
                  f"{d0}..{d1}: pisarlo destruiria el historico. Usar --append o --extend.",
                  file=sys.stderr); sys.exit(1)
    print(f"Backfill {d0}..{d1} — reconstruyendo predicciones point-in-time...\n")

    fc = pd.read_csv("data/forecasts.csv", parse_dates=["init", "avail", "target"])
    fc["target"] = fc["target"].dt.date
    obs = pd.read_csv("data/obs.csv", parse_dates=["date"]); obs["date"] = obs["date"].dt.date
    # --train-until: walk-forward honesto — entrenar EMOS/clim/s2 SOLO con target/date <= cutoff,
    # para poder backfillear ventanas que el historico completo pisaria (ej. junio).
    if a.train_until:
        cutoff = dt.date.fromisoformat(a.train_until)
        fc = fc[fc.target <= cutoff]
        obs = obs[obs.date <= cutoff]
    max_hist = max(fc.target.max(), obs.date.max())
    if d0 <= max_hist:
        if a.extend:
            # en modo semanal el cutoff se auto-ajusta (walk-forward: entrenar solo con < d0)
            cutoff = d0 - dt.timedelta(days=1)
            fc = fc[fc.target <= cutoff]; obs = obs[obs.date <= cutoff]
            print(f"[extend] historico llega a {max_hist} >= start {d0} -> train-until {cutoff} automatico.")
        else:
            print(f"[ABORT] el rango pisa el historico de entrenamiento (<= {max_hist}) -> look-ahead. "
                  f"Usar --train-until anterior a --start.", file=sys.stderr); sys.exit(1)
    params = fit_all(fc, obs, sorted(obs.date.unique()))
    s2map = latest_s2(fc)

    # m point-in-time por (estacion, modelo, target, lead)
    m_all = {}
    for code, (lat, lon, off, unit) in STATIONS.items():
        for model, (om, lag) in MODELS_OM.items():
            try:
                m_all[(code, model)] = fetch_prevruns(code, lat, lon, off, unit, om, d0, d1)
            except Exception as e:
                print(f"[WARN] prev-runs {code} {model}: {e}", file=sys.stderr)
                m_all[(code, model)] = {}

    winners = market_winners(d0, d1)
    # obs reales BATCH: 1 request por estacion para todo el rango (no 1 por dia — con 12x31 dias
    # serian ~370 llamadas a IEM)
    obs_real = {}
    from check_predictions import NETWORKS
    for code, (_, _, _, unit) in STATIONS.items():
        try:
            maxima = fetch_iem_maxima(code, NETWORKS[code], d0, d1, unit, timeout=90)
            obs_real.update({(code, day): value for day, value in maxima.items()})
        except Exception as e:
            print(f"[WARN] IEM batch {code}: {e}", file=sys.stderr)

    rows = []
    for code, (lat, lon, off, unit) in STATIONS.items():
        pars = params.get(code)
        if pars is None:
            continue
        for n in range((d1 - d0).days + 1):
            d = d0 + dt.timedelta(days=n)
            info = winners.get((code, d))
            treal = obs_real.get((code, d))
            for lead in (1, 2, 3):
                # per_model con el gate del downloader: avail=init+lag, lead_h al pico, 1<lh<=78
                pm = {}
                for model, (om, lag) in MODELS_OM.items():
                    m = m_all[(code, model)].get((d, lead))
                    if m is None:
                        continue
                    init = dt.datetime.combine(d, dt.time()) - dt.timedelta(days=lead - 1)
                    avail = init + dt.timedelta(hours=lag)
                    peak = dt.datetime.combine(d, dt.time()) + dt.timedelta(hours=15 - off)
                    lh = (peak - avail).total_seconds() / 3600.0
                    if not (1.0 < lh <= 78.0):
                        continue
                    s2 = s2map.get((code, model, _lead_day(lh)))
                    if s2 is None:
                        continue
                    pm[model] = (m, s2, lh)
                if len(pm) < C.MIN_MODELS_ENTRY:
                    continue
                lh_mean = sum(v[2] for v in pm.values()) / len(pm)
                pm2 = {k: (v[0], v[1]) for k, v in pm.items()}
                c = clim_val(pars["clim"], d)
                pr = predict(pars["emos"], {k: (m - c, s2) for k, (m, s2) in pm2.items()},
                             ld=lh_mean / 24.0)
                if pr is None:
                    continue
                mu_cal, sigma_cal = c + pr[0], pr[1]
                mu_raw, sigma_raw = predict_raw(pm2, C.SIGMA_FLOOR[unit])

                rec = dict(station=code, target=d.isoformat(), lead=lead, unit=unit,
                           mu_cal=round(mu_cal, 2), sigma_cal=round(sigma_cal, 2),
                           mu_raw=round(mu_raw, 2), sigma_raw=round(sigma_raw, 2),
                           max_real=(round(treal, 1) if treal is not None else None),
                           win_mkt=None, hit_cal=None, hit_raw=None,
                           pwin_cal=None, pwin_raw=None, crps_cal=None, crps_raw=None)
                if info and info["buckets"] and info["winner"]:
                    bks, win = info["buckets"], info["winner"]
                    pc = [bucket_prob(mu_cal, sigma_cal, lo, hi) for lo, hi in bks]
                    prw = [bucket_prob(mu_raw, sigma_raw, lo, hi) for lo, hi in bks]
                    wi = bks.index(win)
                    rec["win_mkt"] = blabel(*win, unit)
                    rec["hit_cal"] = int(bks[max(range(len(bks)), key=lambda i: pc[i])] == win)
                    rec["hit_raw"] = int(bks[max(range(len(bks)), key=lambda i: prw[i])] == win)
                    rec["pwin_cal"] = round(pc[wi], 3)
                    rec["pwin_raw"] = round(prw[wi], 3)
                if treal is not None:
                    rec["crps_cal"] = round(crps_normal(treal, mu_cal, sigma_cal), 3)
                    rec["crps_raw"] = round(crps_normal(treal, mu_raw, sigma_raw), 3)
                rows.append(rec)

    if not rows:
        print("[WARN] 0 filas reconstruidas."); return
    df = pd.DataFrame(rows)
    key = ["station", "target", "lead"]
    if a.append and os.path.exists(OUT_CSV):
        # merge con el historico: dedup por (station,target,lead), la corrida NUEVA pisa,
        # PERO los labels (win_mkt/max_real/derivados) hacen COALESCE: si la corrida nueva no
        # los consiguio (Gamma/IEM caidos) y la vieja si, se conserva el viejo — un label
        # resuelto nunca puede volver a None por una falla transitoria de red.
        old = pd.read_csv(OUT_CSV).set_index(key)
        new = df.set_index(key)
        common = new.index.intersection(old.index)
        lab_cols = [c for c in ["max_real", "win_mkt", "hit_cal", "hit_raw",
                                "pwin_cal", "pwin_raw", "crps_cal", "crps_raw"] if c in new.columns]
        if len(common):
            new.loc[common, lab_cols] = new.loc[common, lab_cols].where(
                new.loc[common, lab_cols].notna(), old.loc[common, lab_cols])
        df = pd.concat([old[~old.index.isin(new.index)], new]).reset_index()
        df = df.sort_values(["target", "station", "lead"])
    df.to_csv(OUT_CSV, index=False)

    print("=== resumen por lead ===")
    print("OJO lead 1: para fechas PASADAS, temperature_2m de Previous-Runs esta anclada al valid")
    print("time (day0 ~ analisis/nowcast) -> las metricas de lead 1 NO son una prediccion operable,")
    print("miden un nowcast. Usar lead 2 (corrida del dia anterior) y lead 3. [VERIFICADO 2026-07-08]")
    scored = df[df.hit_cal.notna()]
    for lead, g in scored.groupby("lead"):
        print(f"lead {lead}: n={len(g):3d}  hit CALIBRADO={g.hit_cal.mean():.2f} vs CRUDO={g.hit_raw.mean():.2f}"
              f"  | prob-al-ganador cal={g.pwin_cal.mean():.3f} vs crudo={g.pwin_raw.mean():.3f}")
    withobs = df[df.crps_cal.notna()]
    if len(withobs):
        print(f"\nCRPS vs MAX real IEM (menor=mejor): calibrado={withobs.crps_cal.mean():.3f}  "
              f"crudo={withobs.crps_raw.mean():.3f}  (n={len(withobs)})")
    print("\n=== por estacion (solo lead 2 = 'prediccion del dia anterior', la de la planilla) ===")
    l2 = scored[scored.lead == 2]
    for st, g in l2.groupby("station"):
        print(f"{st}: hit_cal={g.hit_cal.mean():.2f} ({int(g.hit_cal.sum())}/{len(g)})  "
              f"pwin_cal={g.pwin_cal.mean():.3f}")

    print("\n=== BLOQUE PARA LA PLANILLA (lead 2 = corrida del dia anterior al target) ===")
    print("Estacion | Fecha | Consenso crudo | Prediccion calibrada | MAX real (IEM) | "
          "Bucket ganador (WU) | Acerto calibrado?")
    l2all = df[df.lead == 2].sort_values(["target", "station"])
    for r in l2all.itertuples():
        deg = "°F" if r.unit == "F" else "°C"
        mr = f"{r.max_real}{deg}" if r.max_real is not None else "-"
        wm = r.win_mkt if r.win_mkt else "-"
        hit = ("SI" if r.hit_cal == 1 else "NO") if r.hit_cal is not None else "-"
        print(f"{r.station} | {r.target} | {r.mu_raw}{deg} | {r.mu_cal}{deg} | {mr} | {wm} | {hit}")
    print(f"\nDetalle completo (3 leads x dia): {OUT_CSV}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Reconstruye predicciones point-in-time pasadas y las compara con el ganador real.")
    ap.add_argument("--start", default="2026-07-02")
    ap.add_argument("--end", default="2026-07-08")
    ap.add_argument("--train-until", default=None,
                    help="entrenar EMOS/clim/s2 solo con datos <= esta fecha (walk-forward honesto)")
    ap.add_argument("--append", action="store_true",
                    help="mergear con el CSV existente en vez de pisarlo (dedup por station/target/lead)")
    ap.add_argument("--extend", action="store_true",
                    help="continuar desde el ultimo target del CSV hasta AYER (implica --append)")
    main(ap.parse_args())
