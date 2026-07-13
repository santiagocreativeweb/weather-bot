"""Frozen market/weather consensus challenger for forward evaluation."""
import numpy as np

from wxbt.market import bucket_prob

VERSION = "MKTWX1-20260713"
SHADOW0 = "2026-07-14"
STATIONS = {"KLGA", "KORD", "LFPB", "RJTT", "RKSI"}
CUTOFF_HOURS_BEFORE_FREEZE = 3
BOT_WEIGHT = 0.5
MAX_PRICE_AGE_H = 8


def rank_consensus(buckets, mids, mu, sigma, bot_weight=BOT_WEIGHT):
    """Return bucket indices ranked by the frozen linear probability pool."""
    market = np.clip(np.asarray(mids, float), 1e-5, 1.0)
    market /= market.sum()
    bot = np.array([bucket_prob(mu-0.5, sigma, lo, hi) for lo, hi in buckets])
    bot = np.clip(bot, 1e-8, None); bot /= bot.sum()
    return np.argsort(-(bot_weight*bot + (1-bot_weight)*market)).tolist()
