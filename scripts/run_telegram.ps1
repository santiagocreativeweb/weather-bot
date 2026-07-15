# scripts/run_telegram.ps1 — deja el bot de Telegram ESCUCHANDO comandos (long-poll).
# Dejalo corriendo en una terminal, o registralo en Task Scheduler (al inicio de sesion) para
# que ande siempre. Requiere el token en data/.telegram_token o la env WXBT_TG_TOKEN.
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)   # raiz del repo
Write-Host "Arrancando bot de Telegram WXBT (Ctrl+C para parar)..."
python -u scripts/telegram_bot.py --poll
