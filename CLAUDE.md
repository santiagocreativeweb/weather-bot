# wxbt — memoria de proyecto

Antes de tocar código, leé **PROJECT_CONTEXT.md** completo (tesis de edge, investigación FASE
0-1, reglas FASE 3, bugs ya encontrados y corregidos, estado real de los downloaders). Si hay
una tarea puntual de debugging en curso, también existe **HANDOFF_CLAUDE_CODE.md**.

## Invariantes (no romper sin avisar):
1. `evaluate_market()` en `wxbt/engine.py` es función PURA — sin I/O, sin estado oculto. Es el
   núcleo de decisión que se reusa igual en backtest/paper/live.
2. Anti-look-ahead depende 100% de que `avail` en `forecasts.csv` sea el instante REAL de
   publicación de esa corrida. Ninguna fuente de datos puede violar esto.
3. `validate_world()` corre antes de cualquier backtest. Si tira issues, no seguir.
4. `test_null_market` (en `tests/test_core.py`) debe dar ROI≤0 siempre. Si da positivo, el motor
   está roto — no es buena noticia, es una alarma. Ver PROJECT_CONTEXT.md §5 bugs #1-4 como
   sospechosos típicos (patrón repetido: NaN silencioso que se cuela por un chequeo `<=` que no
   lo atrapa, o sesgo por climatología mal restada).
5. EMOS calibra sobre ANOMALÍAS respecto a climatología, no sobre nivel absoluto (PROJECT_CONTEXT.md §5 bug #4).

Comandos: `python -m pytest tests/ -q` (7 tests, deben seguir pasando). Windows del usuario usa
`python`, no `python3`.
