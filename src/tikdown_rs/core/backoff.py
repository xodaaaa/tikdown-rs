"""Minimal exponential backoff with jitter and a ceiling (8.1).

Status: NO current callers. Retained by owner decision pending the
network-probe stability epic, which will consume this (30 s base, 120 s
ceiling, +- jitter) per spec rule 8.1. Delete it there if the epic never
takes it.
"""

import random

BACKOFF_BASE_SECONDS = 30
BACKOFF_CEILING_SECONDS = 120
#: Symmetric jitter band: the computed delay is scaled by uniform(1-j, 1+j).
JITTER_FRACTION = 0.1


def backoff_seconds(
    consecutive_failures: int,
    base: float = BACKOFF_BASE_SECONDS,
    ceiling: float = BACKOFF_CEILING_SECONDS,
    jitter_fn=random.uniform,
) -> float:
    """Exponential backoff: base * 2**failures, capped at ``ceiling``, +- jitter.

    ``consecutive_failures == 0`` yields the healthy cadence (base), which for
    the network probe IS the probe interval (8.1).
    """
    delay = min(float(ceiling), base * (2 ** max(consecutive_failures, 0)))
    return delay * jitter_fn(1 - JITTER_FRACTION, 1 + JITTER_FRACTION)
