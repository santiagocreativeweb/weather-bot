# scripts/run_daily.ps1 — wrapper DIARIO de acumulacion forward. Registrar en Task Scheduler.
# Calcula la fecha de HOY y corre ambos acumuladores (books + ensemble). Ambos loguean a
# data/accumulator.log y son idempotentes (guard anti doble-corrida), asi que re-disparar es seguro.
# El default de fallo es "no escribir" (fail-loud); el check semanal detecta huecos.
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)   # raiz del repo (los scripts usan rutas data/ relativas)
$today = (Get-Date).ToString("yyyy-MM-dd")
python scripts/accumulate_books.py       --date $today
python scripts/accumulate_ensemble.py    --date $today
python scripts/accumulate_predictions.py --date $today
# Ocho modelos crudos point-in-time para validar MED8/W8 sin Previous-Runs retrospectivo.
# Repetir intradia es deseable: el scorer tomara la ultima captura anterior a cada freeze.
python scripts/accumulate_models_forward.py --date $today
python scripts/accumulate_exact_selector.py
python scripts/accumulate_cityx_confidence.py
# NOAA LAMP exact challenger: archive-explicit runtime +2h before freeze; shadow only.
python scripts/accumulate_lamp_shadow.py --date $today
python scripts/capture_market_consensus.py
# Capturadores de fuentes calibradas (agregados 2026-07-10): NBM (KLGA/KORD), MOSMIX TTT y TX
# nativo. Exit 1 con [SKIP] cuando el ciclo ya fue capturado = benigno (guard de idempotencia).
# NBM/MOSMIX publican 4 ciclos/dia: correr este wrapper mas de 1 vez/dia captura ciclos extra.
python scripts/capture_nbm.py        --date $today
python scripts/capture_mosmix.py     --date $today
python scripts/accumulate_mosmix.py  --date $today
python scripts/capture_cwa.py        --date $today
python scripts/capture_jma.py        --date $today
python scripts/capture_qweather.py   --date $today
# SMN argentino (2026-07-13): pronostico oficial para SAEZ (forward-only, API interna con
# token scrapeado de ws2). El WRF del SMN no se captura: tiene archivo S3 point-in-time.
python scripts/capture_smn.py        --date $today
python scripts/validate_sources.py
python scripts/score_model_shadows.py
python scripts/score_lamp_shadow.py
python scripts/score_market_consensus.py
# Leaderboard + estadisticas (track record vivo) y consolidacion a SQLite + Excel (#7/#8).
python scripts/leaderboard.py
python scripts/stats_page.py
python scripts/export_data.py        --date $today
# [2026-07-15] Paginas nuevas: historial desde 08/07, modelos por ciudad (+ CSV para el badge
# del dashboard), vistas por ciudad, y refresh incremental del bias de PWS (dias recientes de
# las referencias ya elegidas). Telegram: resumen diario (no-op silencioso si no hay token).
python scripts/models_page.py        --refresh
python scripts/history_page.py       --refresh
python scripts/city_pages.py
python scripts/pws_setup.py          --update
python scripts/telegram_bot.py       --push
# Los sub-scripts idempotentes salen con code 1 en los SKIP (benigno); el estado real de cada uno
# queda en data/accumulator.log. Salir 0 para que Task Scheduler marque la corrida como exitosa.
exit 0
