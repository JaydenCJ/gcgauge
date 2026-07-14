"""Leak-indicator heuristics.

Six independent indicators, each with a fixed weight, evaluated from the
normalized event stream. The weighted sum maps to a three-level verdict:

* score 0        -> ``none``
* score 1..3     -> ``possible``
* score >= 4     -> ``likely``

The weights encode how diagnostic each signal is on its own: a rising
post-GC floor with a good fit, or an explicit ``OutOfMemoryError``, is enough
to say "likely" by itself; the softer signals (more frequent full GCs, poor
reclaim, evacuation failures, a high steady-state occupancy) only add up to
"likely" in combination. Every indicator reports evidence text either way, so
a clean log yields an auditable "checked and not found" list, not silence.
"""

from typing import List, Optional, Tuple

from .events import GCEvent
from .stats import linear_regression

#: Minimum points before the floor regression is trusted at all.
_MIN_FLOOR_SAMPLES = 4
#: Fit quality gate for the floor trend.
_MIN_FLOOR_R2 = 0.6
#: Floor must climb by this share of total heap to count as rising.
_MIN_FLOOR_RISE_PCT = 10.0
#: Relative-growth fallback when the log never printed a heap capacity.
_MIN_FLOOR_RISE_REL_PCT = 25.0
#: A full GC that frees less than this share of the heap is "low reclaim".
_LOW_RECLAIM_PCT = 10.0
#: Post-GC occupancy above this share of heap is sustained pressure.
_HIGH_OCCUPANCY_PCT = 85.0


def _indicator(
    id_: str,
    label: str,
    weight: int,
    severity: str,
    triggered: bool,
    detail: str,
) -> dict:
    return {
        "id": id_,
        "label": label,
        "weight": weight,
        "severity": severity if triggered else "ok",
        "triggered": triggered,
        "detail": detail,
    }


def floor_series(events: List[GCEvent]) -> Tuple[List[GCEvent], str]:
    """Choose the series that best represents the live-set floor.

    Full collections compact everything, so their post-GC heap is the truest
    floor — but only when they cover the run. Many healthy G1/ZGC runs never
    full-GC at all, and in a leaking run the fulls often only start once the
    heap has already saturated, which would hide the ramp that proves the
    leak. So the full-GC basis is used only when there are at least three
    fulls *and* they span at least half of the observed window; otherwise the
    post-GC heap of every collection is used — young-collection noise
    averages out under regression.
    """
    withheap = [e for e in events if e.heap_after_kb is not None]
    fulls = [e for e in withheap if e.kind == "full"]
    if len(fulls) >= 3 and withheap:
        run_span = withheap[-1].time_s - withheap[0].time_s
        full_span = fulls[-1].time_s - fulls[0].time_s
        if run_span <= 0 or full_span >= 0.5 * run_span:
            return fulls, "full GC"
    return withheap, "all collections"


def evaluate(
    events: List[GCEvent],
    duration_s: float,
    heap_total_kb: Optional[int],
    oom_count: int,
) -> dict:
    """Score the run and return the ``leak`` block of the report."""
    indicators = [
        _rising_floor(events, duration_s, heap_total_kb),
        _full_gc_growth(events, duration_s),
        _low_reclaim(events, heap_total_kb),
        _evacuation_failures(events),
        _high_occupancy(events, heap_total_kb),
        _oom(oom_count),
    ]
    score = sum(i["weight"] for i in indicators if i["triggered"])
    max_score = sum(i["weight"] for i in indicators)
    if score == 0:
        verdict = "none"
    elif score < 4:
        verdict = "possible"
    else:
        verdict = "likely"
    return {
        "verdict": verdict,
        "score": score,
        "max_score": max_score,
        "indicators": indicators,
    }


def _rising_floor(
    events: List[GCEvent], duration_s: float, heap_total_kb: Optional[int]
) -> dict:
    series, basis = floor_series(events)
    make = lambda triggered, detail: _indicator(  # noqa: E731
        "rising_floor", "rising post-GC floor", 4, "critical", triggered, detail
    )
    if len(series) < _MIN_FLOOR_SAMPLES:
        return make(False, "not enough post-GC heap samples to fit a trend")
    xs = [e.time_s for e in series]
    ys = [float(e.heap_after_kb) for e in series]
    slope_kb_s, intercept, r2 = linear_regression(xs, ys)
    rise_kb = slope_kb_s * (xs[-1] - xs[0])
    slope_mb_min = slope_kb_s * 60.0 / 1024.0
    if heap_total_kb:
        rise_pct = rise_kb / heap_total_kb * 100.0
        big_enough = rise_pct >= _MIN_FLOOR_RISE_PCT
    else:
        first = ys[0] if ys[0] > 0 else 1.0
        rise_pct = rise_kb / first * 100.0
        big_enough = rise_pct >= _MIN_FLOOR_RISE_REL_PCT
    triggered = slope_kb_s > 0 and r2 >= _MIN_FLOOR_R2 and big_enough
    minutes = (xs[-1] - xs[0]) / 60.0
    if triggered:
        detail = "+%.2f MB/min over %.1f min, r²=%.2f (%s basis)" % (
            slope_mb_min,
            minutes,
            r2,
            basis,
        )
    elif slope_kb_s <= 0:
        detail = "post-GC floor is flat or falling (%.2f MB/min)" % slope_mb_min
    elif r2 < _MIN_FLOOR_R2:
        detail = "floor wobbles without a clear trend (r²=%.2f)" % r2
    else:
        detail = "floor rose only %.1f%% of heap — below the %.0f%% gate" % (
            rise_pct,
            _MIN_FLOOR_RISE_PCT,
        )
    return make(triggered, detail)


def _full_gc_growth(events: List[GCEvent], duration_s: float) -> dict:
    fulls = [e for e in events if e.kind == "full"]
    make = lambda triggered, detail: _indicator(  # noqa: E731
        "full_gc_growth", "full GC frequency growth", 2, "warn", triggered, detail
    )
    if not fulls or duration_s <= 0:
        return make(False, "no full GC observed")
    midpoint = events[0].time_s + duration_s / 2.0
    first = sum(1 for e in fulls if e.time_s < midpoint)
    second = len(fulls) - first
    triggered = second >= 2 and second >= 2 * max(first, 1)
    detail = "%d full GC in the first half -> %d in the second" % (first, second)
    return make(triggered, detail)


def _low_reclaim(events: List[GCEvent], heap_total_kb: Optional[int]) -> dict:
    fulls = [e for e in events if e.kind == "full" and e.reclaimed_kb is not None]
    make = lambda triggered, detail: _indicator(  # noqa: E731
        "low_full_reclaim", "low full-GC reclaim", 2, "warn", triggered, detail
    )
    if not fulls:
        return make(False, "no full GC with heap figures")
    # Judge the most recent half: early full GCs reclaiming well is expected
    # even in a leaking process; it is the late ones that stop helping.
    recent = fulls[len(fulls) // 2 :]
    pcts = []
    for e in recent:
        denominator = heap_total_kb or e.heap_before_kb
        if denominator:
            pcts.append(e.reclaimed_kb / denominator * 100.0)
    if not pcts:
        return make(False, "no full GC with heap figures")
    mean_pct = sum(pcts) / len(pcts)
    triggered = mean_pct < _LOW_RECLAIM_PCT
    if triggered:
        detail = "recent full GCs reclaim only %.1f%% of heap" % mean_pct
    else:
        detail = "recent full GCs reclaim %.1f%% of heap" % mean_pct
    return make(triggered, detail)


def _evacuation_failures(events: List[GCEvent]) -> dict:
    count = sum(1 for e in events if e.evacuation_failure)
    make = lambda triggered, detail: _indicator(  # noqa: E731
        "evacuation_failure", "evacuation failure", 1, "warn", triggered, detail
    )
    if count:
        return make(
            True, "%d event(s) with to-space exhausted / promotion failed" % count
        )
    return make(False, "none observed")


def _high_occupancy(events: List[GCEvent], heap_total_kb: Optional[int]) -> dict:
    make = lambda triggered, detail: _indicator(  # noqa: E731
        "high_occupancy", "post-GC occupancy", 1, "warn", triggered, detail
    )
    withheap = [e for e in events if e.heap_after_kb is not None]
    if not withheap or not heap_total_kb:
        return make(False, "no heap capacity figures in the log")
    quarter = withheap[-max(1, len(withheap) // 4) :]
    mean_after = sum(e.heap_after_kb for e in quarter) / len(quarter)
    pct = mean_after / heap_total_kb * 100.0
    triggered = pct >= _HIGH_OCCUPANCY_PCT
    detail = "%.1f%% of heap still live in the last quarter of the run" % pct
    return make(triggered, detail)


def _oom(oom_count: int) -> dict:
    make = lambda triggered, detail: _indicator(  # noqa: E731
        "oom", "OutOfMemoryError", 4, "critical", triggered, detail
    )
    if oom_count:
        return make(True, "mentioned %d time(s) in the log" % oom_count)
    return make(False, "not present")
