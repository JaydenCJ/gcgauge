"""Legacy (JDK 8 PrintGCDetails) parser: minor, full, CMS, and G1-on-8 shapes."""

from gcgauge.legacy import looks_legacy, parse_legacy


def parse_one(line):
    result = parse_legacy([line])
    assert len(result.events) == 1, "expected exactly one event from: %s" % line
    return result.events[0]


def test_parallel_minor_gc():
    e = parse_one(
        "1.204: [GC (Allocation Failure) [PSYoungGen: 65536K->10748K(76288K)] "
        "65536K->10756K(251392K), 0.0121342 secs] "
        "[Times: user=0.03 sys=0.01, real=0.01 secs]"
    )
    assert e.kind == "young"
    assert e.time_s == 1.204
    assert e.cause == "Allocation Failure"
    # The whole-heap transition, not the PSYoungGen one, must win.
    assert e.heap_before_kb == 65536
    assert e.heap_after_kb == 10756
    assert e.heap_total_kb == 251392
    assert e.pause_ms == 12.1342


def test_full_gc_with_metaspace_segment():
    # The [Metaspace: ...] segment sits between the heap transition and the
    # total secs; stripping nested brackets must not eat either.
    e = parse_one(
        "24.215: [Full GC (Ergonomics) [PSYoungGen: 10748K->0K(76288K)] "
        "[ParOldGen: 148140K->155904K(175104K)] 158888K->155904K(251392K), "
        "[Metaspace: 3312K->3312K(1056768K)], 0.4171326 secs]"
    )
    assert e.kind == "full"
    assert e.heap_before_kb == 158888
    assert e.heap_after_kb == 155904
    assert e.heap_total_kb == 251392
    assert abs(e.pause_ms - 417.1326) < 1e-9


def test_time_prefixes_uptime_wins_and_datestamp_alone_works():
    both = parse_one(
        "2026-05-01T10:00:01.204+0000: 1.204: [GC (Allocation Failure) "
        "[PSYoungGen: 65536K->10748K(76288K)] 65536K->10756K(251392K), "
        "0.0121342 secs]"
    )
    assert both.time_s == 1.204  # uptime wins when both prefixes are present

    dates_only = parse_legacy(
        [
            "2026-05-01T10:00:00.000+0000: [GC (Allocation Failure) "
            "6144K->1024K(25600K), 0.0100000 secs]",
            "2026-05-01T10:00:05.000+0000: [GC (Allocation Failure) "
            "6144K->1024K(25600K), 0.0100000 secs]",
        ]
    )
    assert dates_only.clock == "absolute"
    assert dates_only.events[1].time_s - dates_only.events[0].time_s == 5.0


def test_g1_on_jdk8_pause_lines():
    young = parse_one(
        "1.234: [GC pause (G1 Evacuation Pause) (young) 12M->8M(64M), "
        "0.0034567 secs]"
    )
    assert young.kind == "young"
    assert young.cause == "G1 Evacuation Pause"
    assert young.heap_before_kb == 12 * 1024
    assert young.heap_total_kb == 64 * 1024

    mixed = parse_one(
        "9.876: [GC pause (G1 Evacuation Pause) (mixed) 40M->20M(64M), "
        "0.0104567 secs]"
    )
    assert mixed.kind == "mixed"


def test_cms_initial_mark_reports_occupancy_not_transition():
    e = parse_one(
        "4.812: [GC (CMS Initial Mark) [1 CMS-initial-mark: 8192K(174784K)] "
        "12345K(253440K), 0.0004900 secs]"
    )
    assert e.kind == "initial-mark"
    assert e.heap_before_kb == e.heap_after_kb == 12345
    assert e.heap_total_kb == 253440
    assert e.is_pause


def test_cms_concurrent_phases():
    e = parse_one("5.905: [CMS-concurrent-mark: 0.005/0.106 secs]")
    assert e.kind == "concurrent"
    assert not e.is_pause
    assert e.pause_ms == 106.0  # wall time, not CPU time
    assert e.cause == "CMS mark"
    # The -start marker carries no duration and must produce no event.
    assert parse_legacy(["5.799: [CMS-concurrent-mark-start]"]).events == []


def test_promotion_failure_marker_sets_evacuation_failure():
    e = parse_one(
        "34.912: [GC (Allocation Failure) --[PSYoungGen: 65536K->65536K(76288K)] "
        "240386K->245122K(251392K), 0.0312245 secs]"
    )
    assert e.evacuation_failure


def test_collector_detected_from_generation_names():
    for line, expected in [
        (
            "1.0: [GC (Allocation Failure) [PSYoungGen: 6K->1K(76288K)] "
            "6K->1K(251392K), 0.01 secs]",
            "Parallel",
        ),
        (
            "1.0: [GC (Allocation Failure) [DefNew: 6K->1K(76288K)] "
            "6K->1K(251392K), 0.01 secs]",
            "Serial",
        ),
        (
            "1.0: [GC (Allocation Failure) [ParNew: 6K->1K(76288K)] "
            "6K->1K(251392K), 0.01 secs]",
            "CMS",
        ),
    ]:
        assert parse_legacy([line]).collector == expected, line


def test_torn_lines_and_oom_are_handled():
    # A JVM killed mid-write leaves a torn last line; it must not crash or
    # produce a half-formed event — and OOM mentions are still counted.
    result = parse_legacy(
        [
            "50.0: [Full GC (Allocation Failure) 251000K->250900K(251392K), "
            "0.9 secs]",
            "Exception in thread \"main\" java.lang.OutOfMemoryError: Java heap space",
            "99.9: [GC (Allocation Failure) [PSYoungGen: 65536K->107",
        ]
    )
    assert len(result.events) == 1
    assert result.oom_count == 1


def test_looks_legacy_predicate():
    assert looks_legacy(
        "1.204: [GC (Allocation Failure) 6K->1K(25K), 0.01 secs]"
    )
    assert looks_legacy("5.905: [CMS-concurrent-mark: 0.005/0.106 secs]")
    assert not looks_legacy("[1.0s][info][gc] GC(0) Pause Young (Normal)")
    assert not looks_legacy("plain application output")
