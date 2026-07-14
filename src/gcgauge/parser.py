"""Front door for parsing: format sniffing and dispatch.

:func:`parse_log` reads a file, decides between the unified (JDK 9+) and
legacy (JDK 8) dialects by scoring a sample of lines against each parser's
cheap predicate, and returns a normalized :class:`~gcgauge.events.ParseResult`
with monotonic, relative timestamps.
"""

from pathlib import Path
from typing import List, Union

from .errors import EmptyLogError, ParseError, UnknownFormatError
from .events import ParseResult, finalize_times
from .legacy import looks_legacy, parse_legacy
from .unified import looks_unified, parse_unified

#: How many non-blank lines the sniffer inspects before deciding.
_SNIFF_WINDOW = 400


def sniff_format(lines: List[str]) -> str:
    """Return ``"unified"`` or ``"legacy"``; raise if neither matches.

    The two predicates are scored over a bounded window rather than
    first-match so that a stray shell banner or a wrapped stack trace at the
    top of the file cannot misclassify the whole log.
    """
    unified_score = 0
    legacy_score = 0
    inspected = 0
    for line in lines:
        if not line.strip():
            continue
        inspected += 1
        if looks_unified(line):
            unified_score += 1
        if looks_legacy(line):
            legacy_score += 1
        if inspected >= _SNIFF_WINDOW:
            break
    if unified_score == 0 and legacy_score == 0:
        raise UnknownFormatError(
            "not a recognizable JVM GC log (expected JDK 9+ unified logging "
            "from -Xlog:gc, or JDK 8 -XX:+PrintGCDetails output)"
        )
    return "unified" if unified_score >= legacy_score else "legacy"


def parse_lines(lines: List[str]) -> ParseResult:
    """Parse in-memory lines (the file-less core behind :func:`parse_log`)."""
    if not any(line.strip() for line in lines):
        raise EmptyLogError("log is empty")
    detected = sniff_format(lines)
    result = parse_unified(lines) if detected == "unified" else parse_legacy(lines)
    if not result.events:
        raise ParseError(
            "log matched the %s format but contained no completed GC events"
            % detected
        )
    finalize_times(result)
    result.events.sort(key=lambda e: (e.time_s, e.line_no))
    return result


def parse_log(path: Union[str, Path]) -> ParseResult:
    """Parse a GC log file from disk.

    Undecodable bytes are replaced rather than fatal: GC logs are sometimes
    concatenated across JVM restarts or truncated mid-write, and a report on
    the readable majority beats no report at all.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        raise EmptyLogError("log file is empty: %s" % path)
    return parse_lines(text.splitlines())
