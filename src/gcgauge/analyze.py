"""Turns a parsed event stream into the report structure.

The report is a plain nested dict (no custom classes) so that

* ``--format json`` is a one-liner with sorted keys,
* :mod:`gcgauge.diffing` can navigate baseline and current uniformly, and
* a JSON report saved yesterday is a first-class diff input today.

All floats are rounded at fixed precision here, at the boundary, which is
what makes reports byte-identical across runs and platforms.
"""

from typing import Dict, List, Optional

from . import leaks
from .events import GCEvent, ParseResult
from .stats import linear_regression, summarize_pauses

#: Report schema version, bumped on any breaking key change.
SCHEMA_VERSION = 1

#: Fixed ordering for well-known pause classes in reports.
_CLASS_ORDER = ["young", "mixed", "full"]


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None else round(value, digits)


def _window(events: List[GCEvent]) -> Dict[str, float]:
    start = events[0].time_s
    end = start
    for e in events:
        finish = e.time_s + (e.pause_ms or 0.0) / 1000.0
        if finish > end:
            end = finish
    return {
        "start_s": round(start, 3),
        "end_s": round(end, 3),
        "duration_s": round(end - start, 3),
    }


def _pause_classes(pauses: List[GCEvent]) -> Dict[str, dict]:
    grouped: Dict[str, List[float]] = {}
    for e in pauses:
        if e.pause_ms is not None:
            grouped.setdefault(e.kind, []).append(e.pause_ms)
    ordered = [k for k in _CLASS_ORDER if k in grouped]
    ordered += sorted(k for k in grouped if k not in _CLASS_ORDER)
    return {kind: summarize_pauses(grouped[kind]) for kind in ordered}


def _allocation_rate_mb_s(events: List[GCEvent]) -> Optional[float]:
    """Mean allocation rate from heap growth between consecutive collections.

    Between the end of one collection and the start of the next, everything
    the heap gained is fresh allocation: ``before[i] - after[i-1]``. Negative
    gaps (a concurrent collector shrinking the heap mid-interval) are
    clamped to zero rather than allowed to cancel real allocation.
    """
    allocated_kb = 0.0
    elapsed_s = 0.0
    previous: Optional[GCEvent] = None
    for e in events:
        if e.heap_before_kb is None or e.heap_after_kb is None:
            continue
        if previous is not None:
            dt = e.time_s - previous.time_s
            if dt > 0:
                allocated_kb += max(0.0, e.heap_before_kb - previous.heap_after_kb)
                elapsed_s += dt
        previous = e
    if elapsed_s <= 0:
        return None
    return allocated_kb / 1024.0 / elapsed_s


def _heap_block(
    events: List[GCEvent], heap_total_kb: Optional[int]
) -> Optional[dict]:
    series, basis = leaks.floor_series(events)
    if not series:
        return None
    xs = [e.time_s for e in series]
    ys = [float(e.heap_after_kb) for e in series]
    slope_kb_s, _intercept, r2 = linear_regression(xs, ys)
    block = {
        "basis": basis,
        "samples": len(series),
        "first_mb": _round(ys[0] / 1024.0),
        "last_mb": _round(ys[-1] / 1024.0),
        "slope_mb_per_min": _round(slope_kb_s * 60.0 / 1024.0, 3),
        "r2": _round(r2, 3),
    }
    if heap_total_kb:
        quarter = series[-max(1, len(series) // 4) :]
        mean_after = sum(e.heap_after_kb for e in quarter) / len(quarter)
        block["last_quarter_occupancy_pct"] = _round(
            mean_after / heap_total_kb * 100.0, 1
        )
    return block


def _full_gc_block(
    events: List[GCEvent], window: Dict[str, float], heap_total_kb: Optional[int]
) -> dict:
    fulls = [e for e in events if e.kind == "full"]
    midpoint = window["start_s"] + window["duration_s"] / 2.0
    duration_min = window["duration_s"] / 60.0
    reclaim_pcts = []
    for e in fulls:
        if e.reclaimed_kb is None:
            continue
        denominator = heap_total_kb or e.heap_before_kb
        if denominator:
            reclaim_pcts.append(e.reclaimed_kb / denominator * 100.0)
    return {
        "count": len(fulls),
        "first_half": sum(1 for e in fulls if e.time_s < midpoint),
        "second_half": sum(1 for e in fulls if e.time_s >= midpoint),
        "per_min": _round(len(fulls) / duration_min, 3) if duration_min > 0 else None,
        "avg_reclaim_pct": _round(
            sum(reclaim_pcts) / len(reclaim_pcts), 1
        )
        if reclaim_pcts
        else None,
    }


def analyze(result: ParseResult, source: str = "<log>") -> dict:
    """Build the full report dict for one parsed log."""
    events = result.events
    pauses = result.pauses
    window = _window(events)
    duration_s = window["duration_s"]

    total_pause_ms = sum(e.pause_ms for e in pauses if e.pause_ms is not None)
    throughput_pct = None
    if duration_s > 0:
        throughput_pct = max(
            0.0, min(100.0, 100.0 * (1.0 - (total_pause_ms / 1000.0) / duration_s))
        )

    heap_total_kb = max(
        (e.heap_total_kb for e in events if e.heap_total_kb is not None),
        default=None,
    )

    classes = _pause_classes(pauses)
    all_pause_values = [e.pause_ms for e in pauses if e.pause_ms is not None]

    report = {
        "gcgauge_report": SCHEMA_VERSION,
        "source": source,
        "format": result.format,
        "collector": result.collector,
        "clock": result.clock,
        "events": {
            "total": len(events),
            "pauses": len(pauses),
            "concurrent": len(events) - len(pauses),
        },
        "window": window,
        "pauses": {
            "classes": classes,
            "all": summarize_pauses(all_pause_values),
        },
        "throughput_pct": _round(throughput_pct),
        "gc_overhead_pct": _round(100.0 - throughput_pct)
        if throughput_pct is not None
        else None,
        "total_pause_s": _round(total_pause_ms / 1000.0, 3),
        "allocation_rate_mb_s": _round(_allocation_rate_mb_s(events)),
        "heap": {
            "total_mb": _round(heap_total_kb / 1024.0, 1) if heap_total_kb else None,
            "post_gc_floor": _heap_block(events, heap_total_kb),
        },
        "full_gc": _full_gc_block(events, window, heap_total_kb),
        "leak": leaks.evaluate(events, duration_s, heap_total_kb, result.oom_count),
        "warnings": list(result.warnings),
    }
    return report


def analyze_log(path: str) -> dict:
    """Parse ``path`` and analyze it in one call (library convenience)."""
    from .parser import parse_log

    return analyze(parse_log(path), source=str(path))
