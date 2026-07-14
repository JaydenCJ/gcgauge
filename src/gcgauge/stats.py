"""Deterministic statistics primitives.

Only integer-rank percentiles and closed-form least squares — no sampling, no
interpolation modes that differ between library versions. Given the same
input, every function here returns bit-identical results on any supported
Python, which is what makes ``gcgauge report --format json`` diff-able across
machines and CI runs.
"""

import math
from typing import Optional, Sequence, Tuple


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile (the value at rank ``ceil(p/100 * n)``).

    Nearest-rank always returns an observed sample, so a reported p99 pause
    is a pause that actually happened — never an interpolated number that no
    request experienced.
    """
    if not values:
        raise ValueError("percentile of empty sequence")
    if not 0.0 < p <= 100.0:
        raise ValueError("percentile p must be in (0, 100], got %r" % p)
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100.0 * len(ordered)))
    return ordered[rank - 1]


def linear_regression(
    xs: Sequence[float], ys: Sequence[float]
) -> Tuple[float, float, float]:
    """Least-squares fit ``y = slope*x + intercept``; returns (slope, intercept, r2).

    Degenerate inputs are defined rather than errors, because leak scoring
    feeds arbitrary log slices through here:

    * fewer than two points, or zero variance in x -> slope 0, r2 0
    * zero variance in y (perfectly flat) -> slope 0, r2 0 (a flat line is
      exactly the "no trend" answer the caller wants)
    """
    n = len(xs)
    if n != len(ys):
        raise ValueError("x and y must have the same length")
    if n < 2:
        return 0.0, (ys[0] if ys else 0.0), 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    syy = sum((y - mean_y) ** 2 for y in ys)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    if sxx == 0.0 or syy == 0.0:
        return 0.0, mean_y, 0.0
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    r2 = (sxy * sxy) / (sxx * syy)
    return slope, intercept, r2


def summarize_pauses(values: Sequence[float]) -> Optional[dict]:
    """Percentile summary of a pause-duration series (milliseconds).

    Returns None for an empty series so callers can omit the class entirely
    instead of emitting a block of nulls.
    """
    if not values:
        return None
    total = sum(values)
    return {
        "count": len(values),
        "mean_ms": round(total / len(values), 3),
        "p50_ms": round(percentile(values, 50), 3),
        "p90_ms": round(percentile(values, 90), 3),
        "p95_ms": round(percentile(values, 95), 3),
        "p99_ms": round(percentile(values, 99), 3),
        "max_ms": round(max(values), 3),
        "total_ms": round(total, 3),
    }
