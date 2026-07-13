#!/usr/bin/env python3
# scripts/validate_sources.py — VALIDACION FORWARD de las fuentes locales (CWA->RCSS, JMA->RJTT)
# contra el bot y el real, ANTES de mezclarlas al mu (pedido de Santiago 2026-07-11: validar primero).
# Para cada target ya resuelto: pick de la fuente = floor(valor), pick del bot = floor(mu_cal), ganador
# = floor(real IEM). Reporta hit exacto y MAE de la FUENTE vs el BOT. Cuando la fuente le gane
# consistentemente (n>=~15-20 dias), recien ahi se justifica meterla al ensemble.
# USO: python scripts/validate_sources.py
import os, sys, csv, math
import datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from check_predictions import fetch_obs_iem   # noqa: E402  (obs fisica IEM, real del dia)

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
# fuente -> (csv, estacion, col_valor, col_corrida, unidad)
SOURCES = {
    "CWA": ("cwa_forward.csv", "RCSS", "tmax_c", "sent_utc", "C"),
    "JMA": ("jma_forward.csv", "RJTT", "tmax_c", "report_utc", "C"),
    "QWeather-ZBAA": ("qweather_forward.csv", "ZBAA", "tmax_c", "update_utc", "C"),
    "QWeather-ZSPD": ("qweather_forward.csv", "ZSPD", "tmax_c", "update_utc", "C"),
}


def latest_src(csvname, station, val_col, key_col):
    """{target_date: valor} usando la corrida MAS RECIENTE por target."""
    p = os.path.join(D, csvname)
    out = {}
    if not os.path.exists(p):
        return out
    with open(p, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("station") != station or r.get(val_col) in (None, ""):
                continue
            tgt = r["target"]
            if tgt not in out or r[key_col] > out[tgt][0]:
                out[tgt] = (r[key_col], float(r[val_col]))
    return {dt.date.fromisoformat(k): v[1] for k, v in out.items()}


def bot_mu(station):
    """{target_date: mu_cal} del snapshot forward MAS FRESCO (min lead_h) por target."""
    p = os.path.join(D, "predictions_forward.csv")
    out = {}
    if not os.path.exists(p):
        return out
    with open(p, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("station") != station:
                continue
            tgt = r["target"]; lh = float(r.get("lead_h", 99))
            if tgt not in out or lh < out[tgt][0]:
                out[tgt] = (lh, float(r["mu_cal"]))
    return {dt.date.fromisoformat(k): v[1] for k, v in out.items()}


def main():
    today = dt.date.today()
    print("=== VALIDACION FORWARD fuentes locales vs bot (targets resueltos) ===\n")
    for name, (csvname, st, vcol, kcol, unit) in SOURCES.items():
        src = latest_src(csvname, st, vcol, kcol)
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
