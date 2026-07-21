#!/usr/bin/env python3
# scripts/check_freshness.py — CENTINELA de acumulacion (2026-07-21, pedido Santiago tras
# descubrir que el VPS estuvo 5 dias sin acumular y nadie se entero): chequea la frescura de
# las fuentes criticas y AVISA por Telegram si algo esta atrasado. "Un edge que no podes operar
# de forma confiable no existe."
#
# Pensado para cron (2x/dia, independiente de run_daily — si run_daily muere, ESTE avisa):
#   0 9,21 * * *  cd ~/weather-bot && .venv/bin/python scripts/check_freshness.py
# Sin dependencias pesadas (no importa dashboard/pandas). Solo avisa cuando HAY problema;
# --report manda el estado aunque este todo OK (para probar el canal).
import argparse
import csv
import json
import os
import sys
import datetime as dt

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def _p(name):
    return os.path.join(DATA, name)


def _mtime_h(path):
    """Horas desde la ultima modificacion (None si no existe)."""
    if not os.path.exists(path):
        return None
    return (dt.datetime.now() - dt.datetime.fromtimestamp(os.path.getmtime(path))).total_seconds() / 3600


def checks(today=None):
    """[(nivel, msg)] — nivel ERR = accion requerida, WARN = mirar."""
    today = today or dt.date.today()
    out = []

    # 1) predictions_forward.csv: tiene que haber snapshot de HOY (run_daily corre 12:00)
    try:
        last = None
        with open(_p("predictions_forward.csv"), newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                s = r.get("snapshot_date") or ""
                if not last or s > last:
                    last = s
        age = (today - dt.date.fromisoformat(last)).days if last else 999
        if age > 1:
            out.append(("ERR", f"predictions_forward SIN snapshot hace {age} días (último {last}) — ¿corrió run_daily?"))
    except Exception as e:
        out.append(("ERR", f"predictions_forward ilegible: {e}"))

    # 2) models_forward.csv: ultima captura de los 8 modelos
    try:
        last = ""
        with open(_p("models_forward.csv"), newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                c = r.get("capture_utc") or ""
                if c > last:
                    last = c
        age_d = (today - dt.date.fromisoformat(last[:10])).days if last else 999
        if age_d > 1:
            out.append(("ERR", f"models_forward SIN capturas hace {age_d} días (última {last[:16]})"))
    except Exception as e:
        out.append(("ERR", f"models_forward ilegible: {e}"))

    # 3) station_bias.json: el refresh semanal (lunes) no puede quedar >8 dias atras
    try:
        b = json.load(open(_p("station_bias.json"), encoding="utf-8"))
        age = (today - dt.date.fromisoformat(b.get("asof", "2000-01-01"))).days
        if age > 8:
            out.append(("ERR", f"station_bias.json con asof {b.get('asof')} ({age} días) — falta el refresh semanal (backfill --extend + calib_lab)"))
    except Exception as e:
        out.append(("ERR", f"station_bias.json ilegible: {e}"))

    # 4) forecast_audit.json: el watcher del dashboard lo toca todo el tiempo — si esta quieto
    #    >3h, el watcher esta caido (y los freezes de 04:30 NO se van a capturar).
    h = _mtime_h(_p("forecast_audit.json"))
    if h is None:
        out.append(("ERR", "forecast_audit.json NO existe"))
    elif h > 3:
        out.append(("ERR", f"forecast_audit.json quieto hace {h:.1f}h — ¿wxbt-web (watcher) caído?"))

    # 5) paginas servidas: si cities_data.js quedo viejo, el dashboard muestra data vieja
    h = _mtime_h(_p("cities_data.js"))
    if h is not None and h > 26:
        out.append(("WARN", f"cities_data.js sin regenerar hace {h:.0f}h (run_daily/botones)"))

    return out


def tg_send(text):
    """Aviso por Telegram a los chats registrados del bot (token del bot, cero config extra)."""
    tok = os.environ.get("WXBT_TG_TOKEN", "").strip()
    tf = _p(".telegram_token")
    if not tok and os.path.exists(tf):
        tok = open(tf, encoding="utf-8").read().strip()
    if not tok:
        return False
    try:
        chats = json.load(open(_p("telegram_chats.json"), encoding="utf-8")).get("chats", {})
    except Exception:
        chats = {}
    if not chats:
        return False
    import requests
    ok = False
    for cid in chats:
        try:
            r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                              json={"chat_id": int(cid), "text": text[:4000],
                                    "parse_mode": "HTML", "disable_web_page_preview": True},
                              timeout=15)
            ok = ok or bool(r.json().get("ok"))
        except Exception:
            pass
    return ok


def main():
    ap = argparse.ArgumentParser(description="Centinela de frescura de los acumuladores WXBT.")
    ap.add_argument("--report", action="store_true",
                    help="mandar el estado por Telegram aunque esté todo OK (probar el canal)")
    ap.add_argument("--no-telegram", action="store_true", help="solo consola")
    a = ap.parse_args()
    probs = checks()
    ts = dt.datetime.now().strftime("%d/%m %H:%M")
    if not probs:
        print(f"[{ts}] frescura OK — todas las fuentes al día.")
        if a.report and not a.no_telegram:
            tg_send("🟢 <b>WXBT centinela</b> — acumuladores al día.")
        return 0
    lines = [f"{'🔴' if n == 'ERR' else '🟡'} {m}" for n, m in probs]
    print(f"[{ts}] PROBLEMAS DE FRESCURA:")
    for ln in lines:
        print("   " + ln.encode("ascii", "replace").decode())
    if not a.no_telegram:
        sent = tg_send("⚠️ <b>WXBT centinela — data atrasada</b>\n" + "\n".join(lines) +
                       "\n\nRevisar el VPS: <code>bash deploy/run_daily.sh</code> o los botones del dashboard.")
        print(f"   aviso telegram: {'enviado' if sent else 'NO enviado (sin token/chats)'}")
    return 1 if any(n == "ERR" for n, _ in probs) else 0


if __name__ == "__main__":
    sys.exit(main())
