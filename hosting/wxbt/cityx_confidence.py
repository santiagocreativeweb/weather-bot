"""Frozen selective-confidence rule for CITYX2 forward evaluation."""
import numpy as np


VERSION = "CITYCONF1-20260713"
PARENT_VERSION = "CITYX2-20260713"
SHADOW0 = "2026-07-14"
MAX_SPREAD_BUCKETS = 1.1
MIN_FORWARD_COVERAGE = .35
MIN_FORWARD_EXACT = .45
GATE_DAYS = 45


def spread_buckets(values, unit):
    width = 2.0 if unit == "F" else 1.0
    return float(np.std(np.asarray(values, dtype=float))/width)


def is_selected(spread):
    return bool(spread <= MAX_SPREAD_BUCKETS)
