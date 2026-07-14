"""Report building: windows, throughput, allocation rate, heap trend blocks."""

import pytest

from gcgauge import analyze, parse_lines
from tests.conftest import unified_full, unified_young


def test_report_schema_basics(steady_report):
    assert steady_report["gcgauge_report"] == 1
    assert steady_report["source"] == "steady"
    assert steady_report["format"] == "unified"
    assert steady_report["collector"] == "G1"
    assert steady_report["events"]["total"] == 10
    assert steady_report["events"]["pauses"] == 10


def test_throughput_window_and_total_pause_time():
    # 2 pauses of 500ms over a 10s window -> 1s paused -> 90% throughput.
    # The window must include the final pause's duration: the run does not
    # end when the last pause starts, but when it finishes.
    lines = [unified_young(0.0, 0, 96, 32, pause_ms=500.0),
             unified_young(9.5, 1, 96, 32, pause_ms=500.0)]
    report = analyze(parse_lines(lines))
    assert report["window"]["end_s"] == 10.0
    assert report["window"]["duration_s"] == 10.0
    assert report["throughput_pct"] == 90.0
    assert report["gc_overhead_pct"] == 10.0
    assert report["total_pause_s"] == 1.0


def test_concurrent_time_does_not_count_against_throughput():
    lines = [
        unified_young(0.0, 0, 96, 32, pause_ms=100.0),
        "[5.000s][info][gc] GC(1) Concurrent Mark Cycle 4000.000ms",
        unified_young(9.9, 2, 96, 32, pause_ms=100.0),
    ]
    report = analyze(parse_lines(lines))
    assert report["total_pause_s"] == 0.2  # the 4s concurrent cycle is free


def test_allocation_rate_from_heap_growth():
    # after=32M, next before=96M, 2s apart -> 64MiB allocated in 2s = 32 MB/s.
    lines = [unified_young(1.0 + i * 2.0, i, 96, 32) for i in range(5)]
    report = analyze(parse_lines(lines))
    assert report["allocation_rate_mb_s"] == pytest.approx(32.0)
    # A single event has no interval to measure over.
    single = analyze(parse_lines([unified_young(1.0, 0, 96, 32)]))
    assert single["allocation_rate_mb_s"] is None
    # A concurrent collector can hand back memory mid-interval ("before"
    # below the previous "after"); the rate clamps at zero instead of
    # subtracting phantom deallocation.
    shrink = analyze(parse_lines([
        unified_young(1.0, 0, 96, 90),
        unified_young(3.0, 1, 40, 32),
        unified_young(5.0, 2, 96, 32),
    ]))
    assert shrink["allocation_rate_mb_s"] >= 0.0


def test_pause_classes_ordered_and_all_covers_everything(leaky_report):
    classes = leaky_report["pauses"]["classes"]
    assert list(classes) == ["young", "full"]
    assert leaky_report["pauses"]["all"]["count"] == sum(
        c["count"] for c in classes.values()
    )


def test_floor_block_flat_run(steady_report):
    floor = steady_report["heap"]["post_gc_floor"]
    assert floor["basis"] == "all collections"
    assert floor["samples"] == 10
    assert floor["slope_mb_per_min"] == 0.0
    assert floor["first_mb"] == 32.0
    assert floor["last_mb"] == 32.0


def test_floor_block_rising_run(leaky_report):
    floor = leaky_report["heap"]["post_gc_floor"]
    assert floor["slope_mb_per_min"] > 0
    assert floor["r2"] > 0.9


def test_floor_basis_depends_on_full_gc_coverage():
    # Fulls spread across the run: they are the truest floor, use them.
    spread = []
    for i in range(6):
        t = 10.0 + i * 20.0
        spread.append(unified_young(t, i * 2, 200, 180))
        spread.append(unified_full(t + 5.0, i * 2 + 1, 220, 100 + i))
    report = analyze(parse_lines(spread))
    assert report["heap"]["post_gc_floor"]["basis"] == "full GC"
    assert report["heap"]["post_gc_floor"]["samples"] == 6

    # Fulls clustered at the end (they began only after the heap saturated)
    # would hide the ramp; the basis must fall back to all collections.
    clustered = [unified_young(1.0 + i * 10.0, i, 32 + i * 16 + 64, 32 + i * 16)
                 for i in range(12)]
    clustered += [unified_full(125.0 + j * 5.0, 12 + j, 240, 224)
                  for j in range(3)]
    report = analyze(parse_lines(clustered))
    assert report["heap"]["post_gc_floor"]["basis"] == "all collections"


def test_full_gc_block_split_by_time_midpoint(leaky_report):
    fg = leaky_report["full_gc"]
    assert fg["count"] == 3
    assert fg["first_half"] == 0
    assert fg["second_half"] == 3
    assert fg["per_min"] > 0


def test_full_gc_reclaim_percentage_and_max_heap_total():
    # Heap total is the maximum observed capacity (the heap grew to -Xmx),
    # and one full GC 220M->100M of 256M total = 46.9% reclaimed.
    lines = [
        unified_young(1.0, 0, 96, 32, total_m=128),
        unified_full(10.0, 1, 220, 100, total_m=256),
    ]
    report = analyze(parse_lines(lines))
    assert report["heap"]["total_mb"] == 256.0
    assert report["full_gc"]["avg_reclaim_pct"] == pytest.approx(46.9)


def test_report_is_deterministic_and_json_serializable(steady_unified_lines):
    import json

    a = analyze(parse_lines(steady_unified_lines), source="x")
    b = analyze(parse_lines(steady_unified_lines), source="x")
    assert a == b
    assert json.loads(json.dumps(a, sort_keys=True)) == a
