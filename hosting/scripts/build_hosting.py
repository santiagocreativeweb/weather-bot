#!/usr/bin/env python3
# scripts/build_hosting.py — arma la carpeta hosting/ AUTOCONTENIDA (pedido Santiago 2026-07-16:
# "solo tengo hasta 512mb y la carpeta pesa 850mb; crea una carpeta hosting con todo lo necesario
# y pusheala"). El peso (~792MB) es casi TODO backtest/labs/backups que la app EN VIVO no usa;
# lo necesario para servir el dashboard + correr el bot + la acumulacion diaria liviana pesa ~18MB.
#
# hosting/ es un MIRROR slim del repo (misma estructura de imports): hosting/scripts + hosting/wxbt
# + hosting/data (curada) + archivos de deploy (systemd, cron, requirements, README). Reproducible:
# re-correr regenera hosting/ desde cero. NO copia secretos (.telegram_token) ni backups/labs.
import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HOST = os.path.join(ROOT, "hosting")

# --- DATA que la app EN VIVO necesita (todo lo demas — prices.csv, lab_*, *.bak, wxbt.db,
#     single_runs, markets, xlsx — es backtest/dev y NO se copia) ---
DATA_KEEP = [
    # picks + calibracion + resolucion
    "predictions_forward.csv",   # snapshot diario de picks
    "forecasts.csv",             # EMOS fit_all para el recalculo en vivo (~10MB)
    "obs.csv",                   # verdad + charts obs 30d
    "forecast_audit.json",       # freeze inmutable 24h/48h (lo mas importante)
    "station_bias.json",         # sesgo V2
    "backfill_check.csv",        # ganadores + lab 60d de referencia
    "gamma_labels.csv",          # ganadores oficiales
    "winners_cache.json",
    "model_city_rank.csv",       # badge "mejor modelo"
    "models_forward.csv",        # perf de modelos vivo + timeline
    "lab_m8.csv",                # perf de modelos retro (~2MB)
    # PWS
    "pws_reference.csv", "pws_near.json", "pws_history.csv",
    # varios que leen las vistas
    "timing_analysis.json", "alerts.json",
    # GENERADOS (lo que se sirve) — se regeneran igual, pero van para que arranque con contenido
    "live_dashboard.html", "city.html", "cities.html", "cities_data.js",
    "leaderboard.html", "stats.html", "wxbt.css", "wxbt.js",
]

REQUIREMENTS = "requests>=2.31\npandas>=2.0\nnumpy>=1.24\n"

RUN_WEB = """#!/usr/bin/env bash
# Sirve el dashboard en vivo (con /timeline y /action) en el puerto 8765. Dejar corriendo
# (systemd wxbt-web.service). Abrir http://TU_IP:8765/live_dashboard.html
cd "$(dirname "$0")"
exec python3 -u scripts/dashboard.py --watch --serve 8765
"""

RUN_BOT = """#!/usr/bin/env bash
# Bot de Telegram (long-poll). UN SOLO poller a la vez. Token en data/.telegram_token o env
# WXBT_TG_TOKEN. Dejar corriendo (systemd wxbt-bot.service).
cd "$(dirname "$0")"
exec python3 -u scripts/telegram_bot.py --poll
"""

RUN_DAILY = """#!/usr/bin/env bash
# Acumulacion + regeneracion DIARIA (liviana, apta 512MB). Registrar en cron (ver README).
# NO corre los labs pesados (esos viven en el repo de desarrollo con toda la data).
set +e
cd "$(dirname "$0")"
TODAY=$(date +%F)
python3 scripts/hko_source.py            --append-obs
python3 scripts/accumulate_predictions.py --date "$TODAY"
python3 scripts/accumulate_models_forward.py --date "$TODAY"
python3 scripts/pws_setup.py             --update
python3 scripts/leaderboard.py
python3 scripts/stats_page.py
python3 scripts/city_pages.py
python3 scripts/telegram_bot.py          --push
exit 0
"""

SYSTEMD_WEB = """[Unit]
Description=WXBT dashboard (web)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/wxbt-hosting
ExecStart=/usr/bin/env bash %h/wxbt-hosting/run_web.sh
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""

SYSTEMD_BOT = """[Unit]
Description=WXBT telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/wxbt-hosting
ExecStart=/usr/bin/env bash %h/wxbt-hosting/run_bot.sh
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""

GITIGNORE = """# secretos y ruido de runtime (NO versionar)
data/.telegram_token
data/.wu_key
data/.qweather_key
data/telegram_chats.json
data/telegram_bot*.log
data/*.log
data/.dashboard_watch.lock
data/accumulator.log
__pycache__/
*.pyc
"""

README = """# WXBT · hosting (deploy slim, cabe en <512 MB)

Bundle **autocontenido** para hostear el dashboard en vivo + el bot de Telegram. Es un espejo
reducido del repo: solo el codigo y los datos que la app **en vivo** necesita (~18 MB de datos,
vs los ~800 MB del repo de desarrollo, que son backtest/labs/backups y NO hacen falta para servir).

## Que hay adentro
- `scripts/` y `wxbt/` — el codigo (identico al repo).
- `data/` — subconjunto curado: picks, freezes (forecast_audit.json), sesgo, ganadores, PWS,
  y los HTML/JS generados que se sirven.
- `run_web.sh` / `run_bot.sh` / `run_daily.sh` — arranques.
- `deploy/` — units de systemd.

## Requisitos
Python 3.10+ y `pip install -r requirements.txt` (requests, pandas, numpy).
Si el limite de **512 MB es de RAM**: corre web y bot como procesos separados (systemd de abajo);
pandas se carga una vez por proceso (~120-200 MB pico durante el fit EMOS). Alcanza, pero no
corras los dos + otras cosas pesadas juntas.
Si el limite es de **disco**: con pandas/numpy instalados el total ronda ~150 MB. Sobra.

## Poner en marcha (Linux)
```bash
git clone <repo> wxbt-hosting && cd wxbt-hosting/hosting
mv * ../ 2>/dev/null || true          # o cloná directo esta subcarpeta
python3 -m pip install -r requirements.txt
printf 'TU_TOKEN_DE_BOTFATHER' > data/.telegram_token   # gitignoreado
chmod +x run_*.sh
```

### Como servicios (systemd, se reinician solos)
```bash
mkdir -p ~/wxbt-hosting && cp -r . ~/wxbt-hosting/
mkdir -p ~/.config/systemd/user
cp deploy/wxbt-web.service deploy/wxbt-bot.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now wxbt-web wxbt-bot
loginctl enable-linger $USER          # que sigan sin sesion abierta
```
Dashboard: `http://TU_IP:8765/live_dashboard.html`

### Acumulacion diaria (cron)
```bash
crontab -e
# 12:00 hora local, todos los dias:
0 12 * * *  cd $HOME/wxbt-hosting && ./run_daily.sh >> data/daily.log 2>&1
```

## Notas
- El bot: **un solo poller a la vez** (dos → 409 Conflict). El token va SOLO en
  `data/.telegram_token` (gitignoreado), nunca en el codigo.
- El timeline y los botones del dashboard necesitan el server propio (`--serve`, ya en run_web.sh).
- Polymarket geobloquea Argentina → el VPS tiene que estar en la UE (el CLOB corre en AWS Londres).
- Este bundle NO corre los labs/backtests (lab_*.py, prices.csv, etc.): eso queda en el repo de
  desarrollo. Aca solo se sirve y se acumula lo diario.
- Regenerar este bundle desde el repo: `python scripts/build_hosting.py`.
"""


def main():
    # [2026-07-17] PRESERVAR la data previa del bundle: en un clon de dev fresco los CSVs de
    # runtime (forecasts/obs/backfill...) estan gitignoreados en data/ y NO existen — sin este
    # rescate, regenerar hosting/ los BORRABA del bundle y el deploy quedaba sin historia.
    import tempfile
    prev = {}
    hdata = os.path.join(HOST, "data")
    if os.path.isdir(hdata):
        tmpd = tempfile.mkdtemp(prefix="wxbt_hostdata_")
        for fn in DATA_KEEP:
            old = os.path.join(hdata, fn)
            if os.path.exists(old):
                shutil.copy2(old, os.path.join(tmpd, fn))
                prev[fn] = os.path.join(tmpd, fn)
    if os.path.isdir(HOST):
        shutil.rmtree(HOST)
    os.makedirs(os.path.join(HOST, "data"))
    os.makedirs(os.path.join(HOST, "deploy"))
    # codigo: wxbt/ y scripts/ (todos los .py; son ~2.5MB, no vale la pena curar)
    shutil.copytree(os.path.join(ROOT, "wxbt"), os.path.join(HOST, "wxbt"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    os.makedirs(os.path.join(HOST, "scripts"))
    for fn in sorted(os.listdir(os.path.join(ROOT, "scripts"))):
        if fn.endswith(".py"):
            shutil.copy2(os.path.join(ROOT, "scripts", fn), os.path.join(HOST, "scripts", fn))
    # datos curados (data/ del repo primero — es lo fresco; si falta, la copia previa del bundle)
    kept, miss = [], []
    for fn in DATA_KEEP:
        src = os.path.join(ROOT, "data", fn)
        if not os.path.exists(src) and fn in prev:
            src = prev[fn]
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(HOST, "data", fn))
            kept.append(fn)
        else:
            miss.append(fn)
    # archivos de deploy
    def w(path, txt, mode=None):
        p = os.path.join(HOST, path)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(txt)
        if mode:
            os.chmod(p, mode)
    w("requirements.txt", REQUIREMENTS)
    w("README.md", README)
    w(".gitignore", GITIGNORE)
    w("run_web.sh", RUN_WEB, 0o755)
    w("run_bot.sh", RUN_BOT, 0o755)
    w("run_daily.sh", RUN_DAILY, 0o755)
    w("deploy/wxbt-web.service", SYSTEMD_WEB)
    w("deploy/wxbt-bot.service", SYSTEMD_BOT)
    # tamaño total
    total = sum(os.path.getsize(os.path.join(dp, f))
                for dp, _, fs in os.walk(HOST) for f in fs)
    print(f"hosting/ armado: {len(kept)}/{len(DATA_KEEP)} data ok, {total/1048576:.1f} MB total")
    if miss:
        print("  [falta, se regenera al correr]:", ", ".join(miss))


if __name__ == "__main__":
    main()
