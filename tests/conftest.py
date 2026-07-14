"""Shared fixtures: tiny hand-built logs and paths to the committed examples.

Every builder is deterministic — fixed numbers, no clocks, no randomness —
so any assertion made on a report is stable forever.
"""

from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def unified_young(t: float, gc_id: int, before_m: int, after_m: int,
                  total_m: int = 256, pause_ms: float = 5.0) -> str:
    return (
        "[%.3fs][info][gc] GC(%d) Pause Young (Normal) (G1 Evacuation Pause) "
        "%dM->%dM(%dM) %.3fms" % (t, gc_id, before_m, after_m, total_m, pause_ms)
    )


def unified_full(t: float, gc_id: int, before_m: int, after_m: int,
                 total_m: int = 256, pause_ms: float = 120.0) -> str:
    return (
        "[%.3fs][info][gc] GC(%d) Pause Full (G1 Compaction Pause) "
        "%dM->%dM(%dM) %.3fms" % (t, gc_id, before_m, after_m, total_m, pause_ms)
    )


def legacy_young(t: float, before_k: int, after_k: int, total_k: int = 251392,
                 secs: float = 0.0123456) -> str:
    return (
        "%.3f: [GC (Allocation Failure) [PSYoungGen: 65536K->10748K(76288K)] "
        "%dK->%dK(%dK), %.7f secs]" % (t, before_k, after_k, total_k, secs)
    )


@pytest.fixture
def steady_unified_lines():
    """A healthy G1 run: flat floor, small pauses, no full GC."""
    lines = ["[0.005s][info][gc] Using G1"]
    for i in range(10):
        t = 1.0 + i * 2.0
        lines.append(unified_young(t, i, 96, 32, pause_ms=4.0 + i * 0.5))
    return lines


@pytest.fixture
def leaky_unified_lines():
    """A leaking G1 run: floor climbs linearly, then full GCs barely help."""
    lines = ["[0.005s][info][gc] Using G1"]
    gc_id = 0
    for i in range(12):
        t = 1.0 + i * 10.0
        floor = 32 + i * 16  # steady climb: 32M -> 208M of 256M
        lines.append(unified_young(t, gc_id, floor + 64, floor, pause_ms=6.0))
        gc_id += 1
    for j in range(3):
        t = 125.0 + j * 10.0
        lines.append("[%.3fs][info][gc] GC(%d) To-space exhausted" % (t, gc_id))
        lines.append(unified_full(t, gc_id, 240, 224, pause_ms=300.0))
        gc_id += 1
    return lines


@pytest.fixture
def steady_report(steady_unified_lines):
    from gcgauge import analyze, parse_lines

    return analyze(parse_lines(steady_unified_lines), source="steady")


@pytest.fixture
def leaky_report(leaky_unified_lines):
    from gcgauge import analyze, parse_lines

    return analyze(parse_lines(leaky_unified_lines), source="leaky")
