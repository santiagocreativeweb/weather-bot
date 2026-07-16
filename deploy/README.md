# WXBT · deploy en VPS (Ubuntu / AWS EC2)

Monta el dashboard (visible desde la IP en el puerto 80) + el bot de Telegram + la acumulación
diaria por cron. Todo con systemd, así se reinicia solo y sobrevive reboots.

## Pasos (pegar en el SSH del VPS)

```bash
# 1. clonar el repo (si es PRIVADO, ver nota abajo)
cd ~
git clone https://github.com/santiagocreativeweb/weather-bot.git
cd weather-bot

# 2. tu token de Telegram (gitignoreado). Opción A: pegarlo a mano:
printf 'TU_TOKEN_DE_BOTFATHER' > data/.telegram_token
#    Opción B: copiarlo desde tu PC (desde otra terminal Windows, NO el SSH):
#    scp -i "C:\Users\Admin\Desktop\POLYMARKET BOTS\aws.pem" ^
#        "C:\Users\Admin\Downloads\wxbt_fase4\data\.telegram_token" ^
#        ubuntu@18.200.244.60:~/weather-bot/data/

# 3. montaje automático (deps, venv, systemd, nginx, cron)
chmod +x deploy/*.sh
./deploy/setup.sh
```

Al terminar imprime la URL. **Abrí el puerto 80 en el Security Group** de la instancia (consola AWS
→ EC2 → Security Groups → Inbound rules → Add rule → HTTP 80 → 0.0.0.0/0). Sin eso, la IP no abre.

Dashboard: **http://18.200.244.60/**  (redirige a `/live_dashboard.html`).

## Qué queda corriendo
- `wxbt-web` (systemd): sirve el dashboard en 8765 y regenera `live_dashboard.html` + captura los
  freezes 24h/48h en el audit cada 3 min. nginx lo publica en el puerto 80.
- `wxbt-bot` (systemd): el bot de Telegram (long-poll). **Un solo poller** — no lo corras también
  en tu PC o da 409.
- `cron` 12:00: `deploy/run_daily.sh` baja modelos/obs del día, recalcula picks y regenera
  ciudades/leaderboard/estadísticas + push al bot. **Eso responde tu "cuándo se descargan los
  modelos nuevos": lo hace el cron solo, todos los días.**

## Comandos útiles
```bash
sudo systemctl status wxbt-web wxbt-bot --no-pager   # estado
journalctl -u wxbt-web -f                            # log del dashboard
journalctl -u wxbt-bot -f                            # log del bot
sudo systemctl restart wxbt-web wxbt-bot             # reiniciar
./deploy/run_daily.sh                                # correr la acumulación a mano
crontab -l                                           # ver el cron
```

## Notas
- **Repo privado**: si `git clone` pide credenciales, o hacés el repo público (Settings → General
  → Danger Zone → Change visibility) o usás un token: 
  `git clone https://<TOKEN>@github.com/santiagocreativeweb/weather-bot.git`.
- **Datos**: los CSV de runtime están gitignoreados; `setup.sh` los toma de `hosting/data/` (van en
  el repo). Los labs/backtest pesados (prices.csv, single_runs.csv, lab_*) NO se despliegan (no
  hacen falta para servir). Si algún día querés correr backtests en el VPS, `rsync` esos aparte.
- **Zona horaria del cron**: por default el server está en UTC. Para 12:00 hora Argentina:
  `sudo timedatectl set-timezone America/Argentina/Buenos_Aires` (o ajustá la hora del cron).
- **Reboot**: con systemd `enable`, web y bot arrancan solos al bootear. El cron también persiste.
