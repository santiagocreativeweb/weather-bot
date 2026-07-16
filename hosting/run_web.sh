#!/usr/bin/env bash
# Sirve el dashboard en vivo (con /timeline y /action) en el puerto 8765. Dejar corriendo
# (systemd wxbt-web.service). Abrir http://TU_IP:8765/live_dashboard.html
cd "$(dirname "$0")"
exec python3 -u scripts/dashboard.py --watch --serve 8765
