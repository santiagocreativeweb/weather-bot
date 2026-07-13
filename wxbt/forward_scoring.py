"""Pure helpers for scoring the forecast that was actually frozen/traded."""

from __future__ import annotations

import datetime as _dt
import math
from typing import Any, Iterable, List, Mapping, Set, Tuple


def frozen_forecast(
    audit: Mapping[str, Any],
    station: str,
    target: Any,
    fallback_mu: float,
    fallback_sigma: float,
) -> Tuple[float, float, str]:
    """Return the point-in-time forecast to score.

    New audit rows persist ``froze.mu`` and ``froze.sg``.  Legacy rows only
    retained the pre-deadline history, so their last numeric history value is
    the best reconstructable frozen mean.  The forward snapshot is used only
    when neither form exists.
    """
    key = f"{station}|{target:%Y-%m-%d}"
    rec = audit.get(key) or {}
    froze = rec.get("froze") or {}

    mu = froze.get("mu")
    sigma = froze.get("sg")
    if _finite(mu):
        return (
            float(mu),
            float(sigma) if _finite(sigma) and float(sigma) > 0 else float(fallback_sigma),
            "frozen",
        )

    # Before the immutable ``froze`` payload was introduced, ``hist`` stopped
    # receiving updates at the deadline.  Walk backwards past malformed rows.
    if rec.get("frozen"):
        for item in reversed(rec.get("hist") or []):
            if isinstance(item, (list, tuple)) and len(item) >= 2 and _finite(item[1]):
                return float(item[1]), float(fallback_sigma), "legacy-audit"

    return float(fallback_mu), float(fallback_sigma), "forward-fallback"


def audit_only_targets(
    audit: Mapping[str, Any],
    known: Set[Tuple[str, _dt.date]],
    today: _dt.date,
    stations: Iterable[str],
) -> List[Tuple[str, _dt.date]]:
    """(station, target) con evidencia congelada en el audit que faltan en ``known``.

    El acumulador forward puede perderse un dia entero (p.ej. Asia/NZ cuando la
    corrida diaria llega despues del pico local, o el primer dia de una ciudad
    recien dada de alta) aunque el freeze SI haya quedado en el audit. Sin esto,
    ese dia desaparece del KPI en silencio: el universo de scoring debe ser
    audit UNION predictions_forward, no solo el acumulador.
    """
    valid = set(stations)
    out: List[Tuple[str, _dt.date]] = []
    for key, rec in audit.items():
        st, _, ds = key.partition("|")
        if st not in valid or not isinstance(rec, dict):
            continue
        try:
            tgt = _dt.date.fromisoformat(ds)
        except ValueError:
            continue
        if tgt > today or (st, tgt) in known:
            continue
        froze = rec.get("froze") or {}
        legacy_ok = rec.get("frozen") and any(
            isinstance(it, (list, tuple)) and len(it) >= 2 and _finite(it[1])
            for it in (rec.get("hist") or []))
        if _finite(froze.get("mu")) or legacy_ok:
            out.append((st, tgt))
    return sorted(out)


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
