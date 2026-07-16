# WXBT · hosting (deploy slim, cabe en <512 MB)

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
