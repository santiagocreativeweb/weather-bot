# Health check WXBT — 2026-07-15 (pedido Santiago: "revisá que los modelos funcionen y las ciudades donde va mal y por qué")

## 1. Fuentes y acumuladores — estado

| fuente | estado | detalle |
|---|---|---|
| Task diaria `wxbt-accumulate` (12:00) | ✅ corrió hoy | todos los capturadores con mtime 07-15 12:00-12:01 |
| predictions_forward | ✅ | 29/29 estaciones con predicción HOY (49 filas) |
| models_forward (12 modelos) | ✅ | 232/232 pares por día, sin huecos 07-12→07-15 |
| books / ensemble | ✅ | corrieron 00:02 (madrugada) |
| NBM / MOSMIX / CWA / JMA / QWeather / SMN | ✅ | capturas de hoy presentes |
| backfill_check / obs / forecasts | ⚠ semanal | cortaban en 07-11 → **re-extendidos hoy** con `run_check.ps1` (07-12→07-14, 29 estaciones) |
| check_accumulation | ✅ tras fixes | estaba FALLIDO con 3 problemas (abajo) — ahora **CHECK OK 07-08→07-15** |

### Bugs operativos encontrados y corregidos hoy
1. **Snapshots duplicados CITYX2 (171 claves) y CITYCONF1 (67)**: corridas CONCURRENTES del
   acumulador (task 12:00 + botón "live" del dashboard lanzando run_daily) — el guard `done` no
   protege concurrencia. Fix: lock cross-proceso (mismo patrón que accumulate_lamp_shadow) en
   ambos acumuladores + dedupe within-run + limpieza one-time (`scripts/fix_forward_dups.py`,
   backups `.bak-dedup0715`).
2. **LAMPNOW1 sin `lav_match_utc`**: `select_features` ya devolvía esa provenance pero
   `build_row` no la persistía y check_accumulation la exige. Fix: se persiste desde ahora +
   migración de header (filas viejas quedan vacías — esa provenance no se fabrica retroactivamente).
3. **`station_bias.json` (calibrador V2) solo cubre las 12 originales, asof 07-11**: las 17
   ciudades nuevas operan SIN bias60 (backfill_check no tenía su historia). El `run_check.ps1`
   corrido hoy extiende el backfill a las 29 → el bias de las nuevas se va a ir poblando con la
   ventana rolling. Hasta juntar ventana, esperar más error en las nuevas (es el mecanismo
   documentado, no un bug del motor).

## 2. Modelos — ¿funcionan?

Fuente VIVA (capturas reales pre-freeze, targets resueltos 12→14/07, pooled):

| modelo | n | exacto | MAE | veredicto |
|---|---|---|---|---|
| icon | 26 | 19% | **1.24** | ✅ el mejor MAE — sigue siendo pilar del mix |
| ukmo | 26 | **31%** | 1.32 | ✅ top exacto (no está en el mix de producción; ya vigilado por sombras W8/MED8) |
| ecmwf | 26 | 12% | 1.35 | ✅ sano |
| gem / knmi / arpege | 20-26 | 23-31% | 1.4-1.5 | ✅ sanos |
| gefs | 26 | 8% | 1.80 | ⚠ flojo pooled y **ROTO en RKSI (MAE 6.8 vivo; ya era 5.74 retro)** |
| meteofrance | 26 | 4% | 1.98 | ⚠ flojo en vivo (aunque retro era top en LIMC/LFPB) |
| jma | 26 | **0%** | **3.17** | ❌ el peor en vivo (igual que retro: peor global −8pp) — solo afecta labs/sombras, NO producción |
| cma | 20 | 15% | 2.35 | ⚠ flojo |

Producción = EMOS(gefs/ecmwf/icon) + bias60: **funciona** (predicción hoy en las 29; el track
desde 08/07 da 40% exacto / 64% top-2 / MAE 0.84 pooled — en línea con el techo estructural
~32-38% documentado). El gefs roto en RKSI refuerza NO operar Seúl (agregado a WEAK).

## 3. Ciudades donde va MAL y por qué (track congelado 08/07→14/07, ganador Gamma)

| ciudad | track | diagnóstico |
|---|---|---|
| **RKSI Seúl** | 1/5 top-2, 3 pérdidas | sesgo frío −1.1°C + **gefs roto ahí** (MAE 5-7°C envenena el mix). → agregada a WEAK del playbook (no operar) |
| **ZSPD Shanghai** | 1/5 top-2 | sesgo frío −0.7°C; bias60 (por diseño, sweep pre-registrado) sigue lento a la ola de calor. Ya era WEAK. Sombras W8/MED8/E3 son el camino, no tocar ventana |
| **ZBAA Beijing** | 2/5, 3 pérdidas | mismo patrón frío (−0.2 promedio pero pérdidas en días de ola); QWeather ya se captura como 2ª opinión |
| **KDAL/KATL/KHOU** | 0/2 c/u | n=2 (arrancaron 07-13); sin bias60 aún (recién hoy entran al backfill). Eran TIER-1 del scout — esperar n≥15 antes de juzgar |
| **SAEZ B.Aires** | 0/2 exacto, bias +1.9 | invierno austral + buckets angostos (ya diagnosticado TIER-3); SMN WRF en lab |
| **EFHK Helsinki** | 1/2, bias +2.3 | n=2, 60°N día largo; esperar muestra |
| **KLGA NY** | 4/6 top-2 pero bias −2.7°F | °F difícil (σ~2.4) — ya WEAK, no operar exacto |
| **ZGSZ Shenzhen** | (2/2 top-2) | **resolución rota** (WU usa fuente china no-METAR, 23% acuerdo IEM-Gamma) → **faltaba en WEAK del playbook y el screener la mostraba como MEDIA — corregido hoy** |

Caso Milán 15/07 (miss del día): pick congelado 35.0°C vs real ~32°C. Los 12 modelos pre-freeze
iban de 30.3 a 37.8 (spread 2.2 buckets) y **CITYCONF1 ya lo marcaba selected=0 (confianza
BAJA)** — el gate de confianza funcionó; es un bust meteorológico de alta dispersión, no un bug.

## 4. Ciudades donde va BIEN (para /top y value bets)

KORD 5/7 exactos · 7/7 top-2 (¡0 pérdidas!), LFPB 5/6 ex · 6/6 t2, LEMD 3/6 · 6/6 t2 (MAE 0.17),
EGLC 4/6 · 5/6, LIMC 3/6 · 5/6. Los STRONG del playbook siguen siendo los correctos.

## 5. Tests

`python -m pytest tests/ -q` → **80/80 pasan** (incluye test_null_market ROI≤0 y los 12 nuevos
de la capa insights/PWS/telegram). validate_world intacto (no se tocó el motor: `evaluate_market`
y `wxbt/` sin cambios).
