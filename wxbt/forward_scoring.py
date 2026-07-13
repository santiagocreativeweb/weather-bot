"""Pure helpers for scoring the forecast that was actually frozen/traded."""

from __future__ import annotations

import math
from typing import Any, Mapping, Tuple


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


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
