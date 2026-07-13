#!/usr/bin/env python3
# scripts/playbook.py — QUE OPERAR HOY. Traduce el pronostico del bot + el mercado en una ACCION
# por mercado, siguiendo la estrategia acordada (2026-07-11, tras perder por apostar al bucket exacto):
#   - el bot es un identificador de TOP-2 (~61%), NO un predictor exacto (~32-35%). No comprar el
#     bucket exacto al ask.
#   - operar SOLO estaciones fuertes; saltar las debiles hasta tener fuente local.
#   - jugadas: comprar el PAR top-2 si esta subvaluado, o VENDER NO en buckets descartados; maker.
# Reusa las funciones del dashboard (no duplica fetching). USO: python scripts/playbook.py [--date YYYY-MM-DD]
import argparse, sys, os
import datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import dashboard as D   # noqa: E402  (importar NO arranca servidor; solo define funciones/const)

# TIERS por track record real (leaderboard vivo + backtest 45d, regla floor). Revisar al re-medir.
STRONG = {"KORD", "LEMD", "LIMC", "EGLC", "LFPB"}   # exacto alto / MAE bajo -> operables
WEAK = {"RCSS", "ZSPD", "KLGA"}                     # desastre consistente -> NO operar (sin fuente local)
EDGE_MIN = 0.10        # edge bruto minimo para considerar comprar top-1
PAIR_EDGE_MIN = 0.12   # (pbot1+pbot2) - (px1+px2) minimo para comprar el PAR top-2
NO_PX_MIN, NO_PBOT_MAX = 0.08, 0.04   # bucket caro que el bot ve improbable -> candidato a NO


def tier(code):
    return "FUERTE" if code in STRONG else ("DEBIL" if code in WEAK else "MEDIA")


def _latest_source(path, station, target, val_col, key_col):
    """Ultimo valor (por key_col) de una fuente forward (CWA/JMA) para (station, target). None si falta."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", path)
    if not os.path.exists(p):
        return None
    import csv as _csv
    best = None
    with open(p, encoding="utf-8") as f:
        for r in _csv.DictReader(f):
            if r.get("station") == station and r.get("target") == target.isoformat():
                if r.get(val_col) not in (None, ""):
                    if best is None or r[key_col] > best[0]:
                        best = (r[key_col], float(r[val_col]))
    return best[1] if best else None


def second_opinion(code, target):
    """2da opinion de la fuente LOCAL relevante por estacion. -> (nombre, valor)|None."""
    SRC = {"RCSS": ("CWA", "cwa_forward.csv", "sent_utc"),
           "RJTT": ("JMA", "jma_forward.csv", "report_utc"),
           "ZBAA": ("QWeather", "qweather_forward.csv", "update_utc"),
           "ZSPD": ("QWeather", "qweather_forward.csv", "update_utc")}
    if code not in SRC:
        return None
    name, csvf, kcol = SRC[code]
    v = _latest_source(csvf, code, target, "tmax_c", kcol)
    return (name, v) if v is not None else None


def main(a):
    today = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    fc = D.fetch_forecast_minmax(today, 2)
    mk = D.fetch_market_full(today, 2)
    live = D.fetch_obs_live(today)
    snap = D.load_preds(today)
    now_utc = dt.datetime.now(dt.timezone.utc)

    rows = []
    for code in D.STATIONS:
        unit = D.STATIONS[code][3]
        for d in [today + dt.timedelta(days=k) for k in range(0, 3)]:
            info = mk.get(code, {}).get(d)
            if not info or not info.get("buckets"):
                continue
            state, _ = D.state_of(code, d, info, now_utc)
            if state not in ("encurso", "soon", "prox"):
                continue   # solo mercados operables (aun no resueltos/pendientes)
            fcd = fc.get(code, {}).get(d)
            mu = sg = None
            cl = D.calibrated_live(code, d, fcd) if fcd else None
            if cl:
                mu, sg = cl
            elif snap.get((code, d)):
                mu, sg = snap[(code, d)]
            if mu is None:
                continue
            priced = [(lab, lo, hi, p) for lab, lo, hi, p in info["buckets"] if p is not None]
            if not priced:
                continue
            # buckets ya imposibles por la max EN VIVO (fresca)
            live_max = (live.get((code, d)) or {}).get("max") if state in ("encurso", "soon") else None
            import math
            floor_live = int(math.floor(live_max)) if live_max is not None else None
            lost = {lab for lab, lo, hi, p in priced if floor_live is not None and hi is not None and hi < floor_live}
            pbot = {lab: D.pbot_floor(mu, sg, lo, hi) for lab, lo, hi, p in priced}
            px = {lab: p for lab, lo, hi, p in priced}
            rank = [l for l, _ in sorted(pbot.items(), key=lambda kv: -kv[1]) if l not in lost]
            if not rank:
                continue
            t1 = rank[0]
            t2 = rank[1] if len(rank) > 1 else None
            edge1 = pbot[t1] - px.get(t1, 1.0)
            pair_edge = (pbot[t1] + (pbot.get(t2, 0))) - (px.get(t1, 1.0) + (px.get(t2, 1.0) if t2 else 1.0))
            # candidatos a NO: caros en el mercado pero improbables para el bot (o ya perdidos)
            no_cands = [lab for lab, lo, hi, p in priced
                        if (p >= NO_PX_MIN and pbot.get(lab, 1) <= NO_PBOT_MAX) or lab in lost]

            # [2026-07-13, perfiles korenssss] LONGSHOT VIVO: bucket que el mercado dio por muerto
            # (1-10c) pero el bot ve claramente vivo (pbot >= max(0.15, 3x precio)). INFORMATIVO,
            # no cambia la accion: es el patron de los mejores tickets de los perfiles analizados
            # (tenkiyoho 80% WR comprando 1-2c que el pronostico decia vivos). Size chico SIEMPRE.
            longs = [lab for lab, lo, hi, p in priced
                     if lab not in lost and 0.005 <= p <= 0.10
                     and pbot.get(lab, 0) >= max(0.15, 3 * p)]

            # 2da opinion / GATE con fuente local (forward-safe, NO toca el mu del bot)
            so = second_opinion(code, d)
            gate = ""
            if so:
                src, sval = so
                diff = mu - sval
                if abs(diff) >= 2.0:
                    gate = f" [!] {src} dice {sval:.0f}{unit} (bot {diff:+.1f} — divergen, {src} suele acertar mas)"
                else:
                    gate = f" [OK] {src} {sval:.0f}{unit} coincide (mas confiable)"

            # VENTANA DE ENTRADA (lab_entry_timing v2 avail-honesta 2026-07-13, precios reales 18m):
            # entrar TEMPRANO (tras la corrida disponible, >=24h) gana ~3c/share vs tarde (top-1
            # taker +2.2/+2.4c vs -0.9c) y los books fillean $40 al 100% con hs MAS barato temprano
            # (2.4-3.0c vs 5.9c mismo dia). EXCEPTO Asia: ahi manda el nowcast, sin edge de timing
            # (RJTT/RKSI negativas en todo bin). Concentrar top-1 / top-2-par maker.
            h2p = (D.peak_utc(code, d) - now_utc.replace(tzinfo=None)).total_seconds() / 3600.0
            if D.STATION_META[code][0] == "Asia":
                win_tag = "timing neutro (Asia=nowcast)"
            elif h2p >= 24:
                win_tag = f"ENTRAR YA (blando, {h2p:.0f}h al pico)"
            elif h2p >= 6:
                win_tag = f"tibio ({h2p:.0f}h — precio ya converge)"
            else:
                win_tag = f"TARDE ({h2p:.0f}h — precio caro, poco margen)"

            ti = tier(code)
            if ti == "DEBIL":
                action = "SALTAR — estacion debil sin fuente local (no operar)"
                if code == "RCSS" and so and abs(mu - so[1]) >= 2.0:
                    action = f"SALTAR — bot {mu:.0f} vs {so[0]} {so[1]:.0f}{unit} divergen 2°+; si operas, segui a {so[0]}"
            elif edge1 >= EDGE_MIN and pbot[t1] >= 0.35:
                action = f"COMPRAR top-1 {t1} @<={px.get(t1,0):.2f} (pbot {pbot[t1]:.2f}, edge {edge1*100:+.0f}c) · MAKER/limit"
            elif t2 and pair_edge >= PAIR_EDGE_MIN:
                action = (f"COMPRAR PAR top-2 {t1}+{t2} (pbot {pbot[t1]:.2f}/{pbot.get(t2,0):.2f} vs "
                          f"px {px.get(t1,0):.2f}/{px.get(t2,0):.2f}, edge par {pair_edge*100:+.0f}c) · MAKER")
            elif no_cands:
                action = f"VENDER NO en {', '.join(no_cands[:3])} (mercado los sobrevalua / ya perdidos) · MAKER"
            else:
                action = "MIRAR — sin edge claro (no forzar)"
            if longs and ti != "DEBIL":
                action += " · LONGSHOT vivo: " + ", ".join(
                    f"{l} @{px[l]:.2f} (pbot {pbot[l]:.2f})" for l in longs[:2])
            if ti != "DEBIL" and "SALTAR" not in action:
                action += f" | {win_tag}"

            rows.append((ti, code, d, state, mu, unit, t1, t2, action + gate))

    order = {"FUERTE": 0, "MEDIA": 1, "DEBIL": 2}
    rows.sort(key=lambda r: (order[r[0]], r[2], r[1]))
    print(f"\n=== PLAYBOOK {today} — que operar (estrategia top-2, solo fuertes, maker) ===\n")
    print(f"{'tier':7}{'est':6}{'fecha':11}{'estado':8}{'bot':7} accion")
    for ti, code, d, state, mu, unit, t1, t2, action in rows:
        print(f"{ti:7}{code:6}{d.isoformat():11}{state:8}{mu:.1f}{unit:<3} {action}")
    print("\nReglas [ACTUALIZADO 2026-07-13, lab_entry_timing v2 avail-honesta, precios reales 18m]:")
    print("  * ENTRAR TEMPRANO en US/EU: apenas esta la corrida (>=24h al cierre; madrugada/vispera),")
    print("    NO las ultimas horas: top-1 taker +2.2/+2.4c temprano vs -0.9c tarde (~3c de diferencia);")
    print("    el ganador cotiza 0.32-0.34 temprano vs 0.37 al final y el hit casi no cae (36-38%).")
    print("    Books reales: $40 fillea 100% a toda hora, hs efectivo 2.4-3.0c temprano vs 5.9c mismo dia.")
    print("  * ASIA: sin edge de timing (nowcast domina; RJTT/RKSI negativas en todo bin).")
    print("  * CONCENTRAR top-1 (y top-2 como PAR, solo temprano+maker +2.9c); el 3er bucket es lastre (-7c).")
    print("  * MAKER siempre (limit al mid, +2c/share), size chico.")
    print("Fuertes operables: " + ", ".join(sorted(STRONG)) + " | Debiles a evitar: " + ", ".join(sorted(WEAK)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Recomendacion de trading por mercado segun la estrategia.")
    ap.add_argument("--date", default=None)
    main(ap.parse_args())
