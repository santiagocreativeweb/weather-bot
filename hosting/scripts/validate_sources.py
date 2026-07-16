#!/usr/bin/env python3
# scripts/validate_sources.py — VALIDACION FORWARD de las fuentes locales (CWA->RCSS, JMA->RJTT)
# contra el bot y el real, ANTES de mezclarlas al mu (pedido de Santiago 2026-07-11: validar primero).
# Para cada target ya resuelto: pick de la fuente = floor(valor), pick del bot = floor(mu_cal), ganador
# = floor(real IEM). Reporta hit exacto y MAE de la FUENTE vs el BOT. Cuando la fuente le gane
# consistentemente (n>=~15-20 dias), recien ahi se justifica meterla al ensemble.
# USO: python scripts/validate_sources.py
import os, sys, csv, json, math
import datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from check_predictions import fetch_obs_iem   # noqa: E402  (obs fisica IEM, real del dia)
from dashboard import freeze_utc              # noqa: E402  (deadline operativo por estacion)
from wxbt.forward_scoring import frozen_forecast  # noqa: E402

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
# fuente -> (csv, estacion, col_valor, col_corrida, unidad)
SOURCES = {
    "CWA": ("cwa_forward.csv", "RCSS", "tmax_c", "sent_utc", "C"),
    "JMA": ("jma_forward.csv", "RJTT", "tmax_c", "report_utc", "C"),
    "QWeather-ZBAA": ("qweather_forward.csv", "ZBAA", "tmax_c", "update_utc", "C"),
    "QWeather-ZSPD": ("qweather_forward.csv", "ZSPD", "tmax_c", "update_utc", "C"),
}


def _utc(value):
    """ISO timestamp -> aware UTC datetime."""
    x = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return x.replace(tzinfo=dt.timezone.utc) if x.tzinfo is None else x.astimezone(dt.timezone.utc)


def latest_src_before_freeze(csvname, station, val_col, key_col):
    """{target_date: valor} usando solo corridas publicadas antes del freeze."""
    p = os.path.join(D, csvname)
    out = {}
    if not os.path.exists(p):
        return out
    with open(p, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("station") != station or r.get(val_col) in (None, ""):
                continue
            tgt = dt.date.fromisoformat(r["target"])
            published = _utc(r[key_col])
            cutoff = freeze_utc(station, tgt).replace(tzinfo=dt.timezone.utc)
            if published > cutoff:
                continue
            if tgt not in out or published > out[tgt][0]:
                out[tgt] = (published, float(r[val_col]))
    return {k: v[1] for k, v in out.items()}


def bot_mu(station):
    """{target_date: mu_cal} del forecast congelado (fallback forward explicito)."""
    p = os.path.join(D, "predictions_forward.csv")
    rows = {}
    if not os.path.exists(p):
        return out
    with open(p, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("station") != station:
                continue
            tgt = dt.date.fromisoformat(r["target"]); lh = float(r.get("lead_h", 99))
            if tgt not in rows or lh < rows[tgt][0]:
                rows[tgt] = (lh, float(r["mu_cal"]), float(r["sigma_cal"]))
    try:
        with open(os.path.join(D, "forecast_audit.json"), encoding="utf-8") as f:
            audit = json.load(f)
    except (OSError, ValueError):
        audit = {}
    return {tgt: frozen_forecast(audit, station, tgt, mu, sg)[0]
            for tgt, (_, mu, sg) in rows.items()}


def main():
    today = dt.date.today()
    print("=== VALIDACION FORWARD fuentes locales vs bot (targets resueltos) ===\n")
    for name, (csvname, st, vcol, kcol, unit) in SOURCES.items():
        src = latest_src_before_freeze(csvname, st, vcol, kcol)
        bot = bot_mu(st)
        recs = []
        for tgt, sval in sorted(src.items()):
            if tgt >= today:
                continue
            obs = fetch_obs_iem(st, tgt)          # real fisico (°C para no-US)
            if obs is None:
                continue
            win = int(math.floor(obs))
            recs.append((tgt, sval, bot.get(tgt), obs, win))
        if not recs:
            print(f"[{name} -> {st}] sin targets resueltos aun (se acumula dia a dia).\n")
            continue
        n = len(recs)
        hit_s = sum(1 for _, sv, _, _, w in recs if int(math.floor(sv)) == w)
        hit_b = sum(1 for _, _, mv, _, w in recs if mv is not None and int(math.floor(mv)) == w)
        nb = sum(1 for _, _, mv, _, _ in recs if mv is not None)
        mae_s = sum(abs(sv - o) for _, sv, _, o, _ in recs) / n
        mae_b = (sum(abs(mv - o) for _, _, mv, o, _ in recs if mv is not None) / nb) if nb else float("nan")
        print(f"[{name} -> {st}] n={n}")
        print(f"  hit exacto:  {name} {hit_s}/{n}   bot {hit_b}/{nb}")
        print(f"  MAE:         {name} {mae_s:.2f}{unit}   bot {mae_b:.2f}{unit}")
        for tgt, sv, mv, o, w in recs:
            mvs = f"{mv:.1f}" if mv is not None else "-"
            print(f"    {tgt}: real {o:.1f}->{w} | {name} {sv:.1f}->{int(math.floor(sv))} | bot {mvs}")
        print()
    print("Veredicto: mezclar la fuente al mu SOLO cuando le gane al bot con n>=~15-20 dias.")


if __name__ == "__main__":
    main()
