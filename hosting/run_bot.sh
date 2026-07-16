#!/usr/bin/env bash
# Bot de Telegram (long-poll). UN SOLO poller a la vez. Token en data/.telegram_token o env
# WXBT_TG_TOKEN. Dejar corriendo (systemd wxbt-bot.service).
cd "$(dirname "$0")"
exec python3 -u scripts/telegram_bot.py --poll
