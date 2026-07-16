#!/usr/bin/env bash
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
