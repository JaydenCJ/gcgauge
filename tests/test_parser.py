"""Format sniffing, dispatch, and file-level error handling."""

import pytest

from gcgauge import (
    EmptyLogError,
    ParseError,
    UnknownFormatError,
    parse_lines,
    parse_log,
)
from gcgauge.parser import sniff_format

UNIFIED_LINE = (
    "[1.0s][info][gc] GC(0) Pause Young (Normal) (G1 Evacuation Pause) "
    "24M->8M(64M) 3.3ms"
)
LEGACY_LINE = (
    "1.204: [GC (Allocation Failure) [PSYoungGen: 65536K->10748K(76288K)] "
    "65536K->10756K(251392K), 0.0121342 secs]"
)


def test_sniff_recognizes_both_formats():
    assert sniff_format([UNIFIED_LINE]) == "unified"
    assert sniff_format([LEGACY_LINE]) == "legacy"


def test_sniff_survives_leading_noise():
    lines = [
        "OpenJDK 64-Bit Server VM warning: something benign",
        "starting application...",
        LEGACY_LINE,
        LEGACY_LINE,
    ]
    assert sniff_format(lines) == "legacy"


def test_sniff_unknown_raises_with_guidance():
    with pytest.raises(UnknownFormatError) as excinfo:
        sniff_format(["not a gc log", "just text"])
    assert "-Xlog:gc" in str(excinfo.value)
    assert "PrintGCDetails" in str(excinfo.value)


def test_empty_input_raises(tmp_path):
    with pytest.raises(EmptyLogError):
        parse_lines(["", "   ", ""])
    empty = tmp_path / "empty.log"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(EmptyLogError):
        parse_log(empty)


def test_recognized_format_without_events_raises():
    # A file of unified decorations with no completed collections is a
    # recognized format but useless; the error must say so, not crash later.
    with pytest.raises(ParseError):
        parse_lines(["[0.01s][info][gc] Using G1"])


def test_parse_lines_sorts_events_by_time():
    # Concatenated or interleaved logs can be out of order on disk.
    lines = [
        "[5.0s][info][gc] GC(1) Pause Young (Normal) (G1 Evacuation Pause) "
        "24M->8M(64M) 3.3ms",
        "[1.0s][info][gc] GC(0) Pause Young (Normal) (G1 Evacuation Pause) "
        "24M->8M(64M) 3.3ms",
    ]
    result = parse_lines(lines)
    assert [e.gc_id for e in result.events] == [0, 1]


def test_parse_log_reads_files_and_tolerates_undecodable_bytes(tmp_path):
    log = tmp_path / "gc.log"
    log.write_text(UNIFIED_LINE + "\n", encoding="utf-8")
    result = parse_log(log)
    assert result.format == "unified"
    assert len(result.events) == 1

    # Torn writes / mixed encodings must degrade to replacement chars, not
    # abort the whole report.
    torn = tmp_path / "torn.log"
    torn.write_bytes(
        (UNIFIED_LINE + "\n").encode("utf-8") + b"\xff\xfe garbage \xff\n"
    )
    assert len(parse_log(torn).events) == 1


def test_committed_example_logs_parse():
    from tests.conftest import EXAMPLES

    for name, fmt, collector in [
        ("g1-steady.log", "unified", "G1"),
        ("g1-leak.log", "unified", "G1"),
        ("jdk8-parallel.log", "legacy", "Parallel"),
    ]:
        result = parse_log(EXAMPLES / name)
        assert result.format == fmt, name
        assert result.collector == collector, name
        assert len(result.events) > 10, name
