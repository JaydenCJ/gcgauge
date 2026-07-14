"""Unified-logging (JDK 9+) parser: every supported line shape and decoration."""

from gcgauge.events import finalize_times
from gcgauge.unified import looks_unified, parse_unified


def parse_one(line):
    result = parse_unified([line])
    assert len(result.events) == 1, "expected exactly one event from: %s" % line
    return result.events[0]


def test_g1_young_normal_pause():
    e = parse_one(
        "[1.234s][info][gc] GC(5) Pause Young (Normal) (G1 Evacuation Pause) "
        "102M->24M(256M) 3.456ms"
    )
    assert e.kind == "young"
    assert e.gc_id == 5
    assert e.cause == "G1 Evacuation Pause"
    assert e.pause_ms == 3.456
    assert e.time_s == 1.234
    assert e.heap_before_kb == 102 * 1024
    assert e.heap_after_kb == 24 * 1024
    assert e.heap_total_kb == 256 * 1024
    assert e.is_pause


def test_young_mode_qualifiers_drive_classification():
    # "(Mixed)" and "(Prepare Mixed)" are mixed collections; "(Concurrent
    # Start)" is a mode of a plain young pause, not a different class.
    for mode, kind in [
        ("Mixed", "mixed"),
        ("Prepare Mixed", "mixed"),
        ("Normal", "young"),
        ("Concurrent Start", "young"),
    ]:
        e = parse_one(
            "[9.9s][info][gc] GC(9) Pause Young (%s) (G1 Evacuation Pause) "
            "120M->60M(256M) 18.2ms" % mode
        )
        assert e.kind == kind, mode
        assert e.cause == "G1 Evacuation Pause", mode


def test_full_pause_units_and_nested_parenthesis_cause():
    # Gigabyte sizes normalize to KiB, and the "(System.gc())" cause nests
    # parentheses — the qualifier regex must not stop at the first ")".
    e = parse_one(
        "[2.0s][info][gc] GC(3) Pause Full (System.gc()) 2G->1G(4G) 150.123ms"
    )
    assert e.kind == "full"
    assert e.cause == "System.gc()"
    assert e.pause_ms == 150.123
    assert e.heap_before_kb == 2 * 1024 * 1024
    assert e.heap_total_kb == 4 * 1024 * 1024


def test_concurrent_cycle_is_not_a_pause():
    e = parse_one("[30.0s][info][gc] GC(8) Concurrent Mark Cycle 45.678ms")
    assert e.kind == "concurrent"
    assert not e.is_pause
    assert e.pause_ms == 45.678


def test_shenandoah_named_pauses():
    result = parse_unified(
        [
            "[1.0s][info][gc] GC(0) Pause Init Mark 0.437ms",
            "[1.1s][info][gc] GC(0) Concurrent marking 16M->17M(64M) 2.462ms",
            "[1.2s][info][gc] GC(0) Pause Final Mark (process weakrefs) 0.892ms",
        ]
    )
    kinds = [e.kind for e in result.events]
    assert kinds == ["init-mark", "concurrent", "final-mark"]
    assert result.events[0].is_pause and result.events[2].is_pause
    assert not result.events[1].is_pause
    assert result.collector == "Shenandoah"  # inferred from the pause names


def test_zgc_cycles_percent_sizes_and_optional_duration():
    e = parse_one(
        "[10.0s][info][gc] GC(2) Garbage Collection (Allocation Rate) "
        "914M(89%)->586M(57%)"
    )
    assert e.kind == "cycle"
    assert not e.is_pause
    assert e.pause_ms is None
    assert e.heap_before_kb == 914 * 1024
    assert e.heap_after_kb == 586 * 1024
    # Capacity back-computed from the percentage annotation: 914M is 89%.
    assert e.heap_total_kb == int(round(914 * 1024 * 100 / 89))

    generational = parse_one(
        "[4.2s][info][gc] GC(3) Minor Collection (Allocation Rate) "
        "1024M(50%)->256M(12%) 0.123s"
    )
    assert generational.kind == "cycle"
    assert generational.pause_ms == 123.0


def test_to_space_exhausted_marks_matching_pause():
    result = parse_unified(
        [
            "[8.0s][info][gc] GC(4) To-space exhausted",
            "[8.0s][info][gc] GC(4) Pause Young (Normal) (G1 Evacuation Pause) "
            "250M->240M(256M) 88.8ms",
            "[9.0s][info][gc] GC(5) Pause Young (Normal) (G1 Evacuation Pause) "
            "120M->40M(256M) 5.5ms",
        ]
    )
    assert result.events[0].evacuation_failure
    assert not result.events[1].evacuation_failure


def test_using_line_sets_collector_and_inference_fallback():
    for message, expected in [
        ("Using G1", "G1"),
        ("Using Parallel", "Parallel"),
        ("Using Serial", "Serial"),
        ("Using Concurrent Mark Sweep", "CMS"),
        ("Using The Z Garbage Collector", "ZGC"),
        ("Using Shenandoah", "Shenandoah"),
    ]:
        result = parse_unified(["[0.01s][info][gc] " + message])
        assert result.collector == expected, message
    # Without a "Using" line, G1 is inferred from its causes.
    inferred = parse_unified(
        [
            "[1.0s][info][gc] GC(0) Pause Young (Normal) (G1 Evacuation Pause) "
            "24M->8M(64M) 3.3ms"
        ]
    )
    assert inferred.collector == "G1"


def test_start_phase_and_foreign_lines_are_skipped_silently():
    # gc,start lines have no duration yet (double-count risk), detail tags
    # describe internals, and other subsystems' lines are not GC at all.
    result = parse_unified(
        [
            "[1.0s][info][gc,start] GC(0) Pause Young (Normal) (G1 Evacuation Pause)",
            "[1.0s][info][gc,phases] GC(0)   Pre Evacuate Collection Set: 0.1ms",
            "[1.0s][info][gc,heap] GC(0) Eden regions: 12->0(14)",
            "[1.0s][info][gc,metaspace] GC(0) Metaspace: 3246K(3392K)->3246K(3392K)",
            "[1.0s][info][os,thread] Thread attached (tid: 12345)",
            "[1.0s][info][gc] GC(0) Pause Young (Normal) (G1 Evacuation Pause) "
            "24M->8M(64M) 3.3ms",
        ]
    )
    assert len(result.events) == 1
    assert not result.warnings


def test_time_decorations_uptime_ms_and_wallclock_offsets():
    # Millisecond uptime decorations convert to seconds.
    e = parse_one(
        "[1234ms][info][gc] GC(0) Pause Young (Normal) (G1 Evacuation Pause) "
        "24M->8M(64M) 3.3ms"
    )
    assert e.time_s == 1.234
    # Wall-clock-only logs normalize so the first event is t=0, and mixed
    # UTC offsets refer to the same instants (+0900 19:00 == +0000 10:00).
    result = parse_unified(
        [
            "[2026-05-01T10:00:00.000+0000][info][gc] GC(0) Pause Young (Normal) "
            "(G1 Evacuation Pause) 24M->8M(64M) 3.3ms",
            "[2026-05-01T10:00:02.500+0000][info][gc] GC(1) Pause Young (Normal) "
            "(G1 Evacuation Pause) 24M->8M(64M) 3.3ms",
            "[2026-05-01T19:00:03.000+0900][info][gc] GC(2) Pause Young (Normal) "
            "(G1 Evacuation Pause) 24M->8M(64M) 3.3ms",
        ]
    )
    finalize_times(result)
    assert result.clock == "absolute"
    assert [e.time_s for e in result.events] == [0.0, 2.5, 3.0]

    # No decorations at all (-Xlog:gc:file:none): ordinals become the time
    # axis and a warning tells the user the trend x-axis is synthetic.
    bare = parse_unified(
        [
            "GC(0) Pause Young (Normal) (G1 Evacuation Pause) 24M->8M(64M) 3.3ms",
            "GC(1) Pause Young (Normal) (G1 Evacuation Pause) 24M->8M(64M) 3.3ms",
        ]
    )
    finalize_times(bare)
    assert bare.clock == "index"
    assert [e.time_s for e in bare.events] == [0.0, 1.0]
    assert bare.warnings


def test_oom_lines_are_counted():
    result = parse_unified(
        [
            "[1.0s][info][gc] GC(0) Pause Full (G1 Compaction Pause) "
            "63M->63M(64M) 900.0ms",
            "java.lang.OutOfMemoryError: Java heap space",
        ]
    )
    assert result.oom_count == 1


def test_looks_unified_predicate():
    for line, expected in [
        ("[0.1s][info][gc] Using G1", True),
        ("[0.1s][info][gc,start] GC(0) Pause Young (Normal)", True),
        ("GC(0) Pause Young (Normal) (G1 Evacuation Pause) 1M->1M(2M) 1.0ms", True),
        ("1.204: [GC (Allocation Failure) 6K->1K(25K), 0.01 secs]", False),
        ("random shell noise", False),
    ]:
        assert looks_unified(line) is expected, line
