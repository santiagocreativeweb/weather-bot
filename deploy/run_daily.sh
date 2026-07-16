#!/usr/bin/env bash
# Acumulacion + regeneracion DIARIA (la corre cron). Baja modelos/precios/obs del dia, recalcula
# picks, y regenera las paginas + manda el resumen al bot. Liviano (no corre los labs pesados).
set +e
cd "$(dirname "$0")/.."          # raiz del repo
source .venv/bin/activate 2>/dev/null || true
TODAY=$(date +%F)
echo "===== run_daily $TODAY · $(date -u) UTC ====="
python scripts/hko_source.py             --append-obs          # obs oficiales Hong Kong
python scripts/accumulate_predictions.py --date "$TODAY"       # picks del dia (EMOS + bias)
python scripts/accumulate_models_forward.py --date "$TODAY"    # 8 modelos point-in-time
python scripts/pws_setup.py              --update               # densifica bias PWS
python scripts/city_pages.py                                    # dashboard por ciudad (+ rank csv)
python scripts/leaderboard.py                                   # track record
python scripts/stats_page.py                                    # estadisticas (tabs 24/48h)
python scripts/telegram_bot.py           --push                 # resumen diario al bot
echo "===== fin $(date -u) UTC ====="
exit 0
