# scripts/run_check.ps1 — wrapper SEMANAL del chequeo de completitud. Registrar en Task Scheduler.
# Valida que la acumulacion forward no tenga huecos silenciosos (dias faltantes, cobertura,
# rangos). Exit != 0 si hay problemas -> el Task Scheduler lo marca como fallido y podes alertar.
# Correr SEMANAL, no a los 90 dias: detecta un scheduler roto cuando todavia hay tiempo de corregir.
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)
$today = (Get-Date).ToString("yyyy-MM-dd")
# CALIBRADOR V2: refrescar el sesgo rolling semanalmente (ventana movil, calib_lab re-evalua
# las variantes y reescribe data/station_bias.json). Corre ANTES del check para que un fallo
# del lab no tape el exit-code del chequeo de integridad.
# [2026-07-12] PRIMERO extender backfill_check.csv hasta ayer: alimenta el D1 dinamico del lab
# (sin esto el refresh del bias era un NO-OP silencioso: D1 viejo + cache lab_m.csv).
python scripts/backfill_check.py --extend
python scripts/calib_lab.py
# SOMBRA MED60 (2026-07-12): acumula la comparacion mediana-vs-media 60d con regla pre-registrada
# (ver header de lab_bias_window.py). NO toca produccion; solo imprime y actualiza los CSV del lab.
python scripts/lab_bias_window.py
# SOMBRA COMBOS 8-modelos (2026-07-12): refresca los caches point-in-time (ambos reanudables /
# skip-existing) y acumula MED8/W8/E3-debiles vs V2 con regla pre-registrada (header de
# lab_city_models.py). NO toca produccion.
python scripts/fetch_lab_m8.py
$yday = (Get-Date).AddDays(-1).ToString("yyyy-MM-dd")
python scripts/backfill_nbm.py --start 2026-04-12 --end $yday --only-lead 2
python scripts/lab_city_models.py
# SOMBRA H4 (2026-07-13): post-procesamiento ML (GBM mediana, 18 meses de training) vs V2.
# Corre DESPUES de lab_city_models (usa su detail de V2 como pareo). NO toca produccion.
python scripts/lab_ml.py
# SOMBRA H5 (2026-07-13): auditoria WU-vs-IEM + correccion de pick en KLGA/KORD. NO toca produccion.
python scripts/lab_wu_ground_truth.py
python scripts/check_accumulation.py --through $today
exit $LASTEXITCODE
