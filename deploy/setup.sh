#!/usr/bin/env bash
# deploy/setup.sh — montaje AUTOMATICO en un VPS Ubuntu (pedido Santiago 2026-07-16).
# Corre DESPUES de: git clone <repo> && cd weather-bot.  Idempotente: se puede re-correr.
# Hace: deps del sistema, venv + pip, rellena los CSV de runtime (gitignoreados) desde hosting/data,
# instala systemd (web + bot), nginx (:80 -> :8765) y el cron diario.
set -e
cd "$(dirname "$0")/.."
REPO="$(pwd)"
USER_NAME="$(id -un)"
echo "== repo: $REPO  · user: $USER_NAME =="

echo "== 1/6 deps del sistema =="
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip nginx

echo "== 2/6 venv + pip =="
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install requests pandas numpy openpyxl

echo "== 3/6 datos de runtime (los CSV estan gitignoreados; se toman de hosting/data) =="
cp -n hosting/data/*.csv  data/ 2>/dev/null || true
cp -n hosting/data/*.json data/ 2>/dev/null || true
if [ ! -f data/.telegram_token ] && [ -z "$WXBT_TG_TOKEN" ]; then
  echo "  !! FALTA data/.telegram_token — pegá tu token antes de arrancar el bot:"
  echo "     printf 'TU_TOKEN' > data/.telegram_token"
fi

echo "== 4/6 systemd (web + bot) =="
for svc in wxbt-web wxbt-bot; do
  sed "s#__REPO__#$REPO#g; s#__USER__#$USER_NAME#g" "deploy/$svc.service" | sudo tee "/etc/systemd/system/$svc.service" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now wxbt-web wxbt-bot

echo "== 5/6 nginx (:80 -> :8765) =="
sudo cp deploy/wxbt.nginx /etc/nginx/sites-available/wxbt
sudo ln -sf /etc/nginx/sites-available/wxbt /etc/nginx/sites-enabled/wxbt
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx

echo "== 6/6 cron diario (12:00 hora del server) =="
CRON="0 12 * * * cd $REPO && ./deploy/run_daily.sh >> data/daily.log 2>&1"
( crontab -l 2>/dev/null | grep -v 'deploy/run_daily.sh' ; echo "$CRON" ) | crontab -

IP=$(curl -s --max-time 5 http://checkip.amazonaws.com || echo "<IP>")
echo ""
echo "==================== LISTO ===================="
echo "Dashboard:  http://$IP/           (redirige a /live_dashboard.html)"
echo "RECORDA: abrir el puerto 80 (HTTP) inbound en el Security Group de la instancia en la consola AWS."
echo "Estados:  sudo systemctl status wxbt-web wxbt-bot --no-pager"
echo "Logs:     journalctl -u wxbt-web -f   /   journalctl -u wxbt-bot -f"
echo "==============================================="
