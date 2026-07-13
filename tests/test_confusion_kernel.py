import datetime as dt

import numpy as np
import pandas as pd

from scripts.lab_confusion_kernel import closest_displacement, kernel


def test_closest_displacement_respects_fahrenheit_bucket_width():
    assert closest_displacement(82.4, "F", "84-85°F") == 1
    assert closest_displacement(86.0, "F", "84-85°F") == -1


def test_local_kernel_prefers_empirical_model_displacement():
    day = dt.date(2026, 7, 1)
    history = [(day-dt.timedelta(days=i), 1) for i in range(1, 21)]
    probs = kernel((history, day), ([], day), "LOCAL30")
    assert int(np.argmax(probs))-4 == 1


def test_shrunk_kernel_can_borrow_global_model_history():
    day = dt.date(2026, 7, 1)
    pooled = [(day-dt.timedelta(days=i), -1) for i in range(1, 21)]
    probs = kernel(([], day), (pooled, day), "SHRINK60")
    assert int(np.argmax(probs))-4 == -1
