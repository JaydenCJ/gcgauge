"""Parser for JDK 9+ unified GC logging (JEP 158, ``-Xlog:gc``).

Handles the default ``uptime,level,tags`` decorations, wall-clock (``time``)
decorations, and undecorated logs (``-Xlog:gc:file=gc.log:none``). Covers the
line shapes emitted by G1, Parallel, Serial, ZGC (single-gen and
generational), and Shenandoah at the plain ``gc`` tag level:

* ``GC(7) Pause Young (Normal) (G1 Evacuation Pause) 102M->24M(256M) 3.456ms``
* ``GC(12) Pause Full (G1 Compaction Pause) 240M->100M(256M) 150.123ms``
* ``GC(8) Concurrent Mark Cycle 45.678ms``
* ``GC(2) Garbage Collection (Allocation Rate) 914M(89%)->586M(57%)``
* ``GC(0) Pause Init Mark 0.437ms`` (Shenandoah)
* ``GC(12) To-space exhausted`` (marks the matching pause as an evacuation failure)

Lines that carry the ``gc`` tag but describe phases, region breakdowns, or
in-progress starts are recognized and skipped without warnings — they are a
normal part of a verbose log, not noise.
"""

import datetime
import re
from typing import Iterable, List, Optional, Tuple

from .events import GCEvent, ParseResult, size_to_kb

_LEVELS = frozenset({"error", "warning", "info", "debug", "trace"})

_UPTIME_RE = re.compile(r"^(\d+(?:\.\d+)?)(ms|s)$")
_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})"
    r"(?:[.,](\d{1,3}))?"
    r"(Z|[+-]\d{2}:?\d{2})?$"
)

_GCID_RE = re.compile(r"^GC\((\d+)\)\s+(.*)$")

# A parenthesized qualifier, tolerating one nesting level for "(System.gc())".
_QUAL = r"\((?:[^()]|\([^()]*\))*\)"

_HEAP = (
    r"(\d+(?:\.\d+)?)([BKMGT])B?->"
    r"(\d+(?:\.\d+)?)([BKMGT])B?"
    r"\((\d+(?:\.\d+)?)([BKMGT])B?\)"
)

_PAUSE_RE = re.compile(
    r"^Pause\s+([A-Za-z][A-Za-z ]*?)"
    r"((?:\s*" + _QUAL + r")*)"
    r"(?:\s+" + _HEAP + r")?"
    r"\s+(\d+(?:\.\d+)?)ms$"
)

_CONCURRENT_RE = re.compile(
    r"^Concurrent\s+([A-Za-z][A-Za-z ()]*?)"
    r"(?:\s+" + _HEAP + r")?"
    r"\s+(\d+(?:\.\d+)?)ms$"
)

# ZGC cycle line: sizes are annotated with percentages instead of a capacity.
_ZGC_RE = re.compile(
    r"^(Garbage Collection|Major Collection|Minor Collection)\s+"
    r"\(([^)]+)\)\s+"
    r"(\d+(?:\.\d+)?)([BKMGT])B?\((\d+)%\)->"
    r"(\d+(?:\.\d+)?)([BKMGT])B?\((\d+)%\)"
    r"(?:\s+(\d+(?:\.\d+)?)(ms|s))?$"
)

_QUAL_FINDER = re.compile(_QUAL)

#: Qualifiers that name a collection *mode* rather than a trigger cause.
_MODES = frozenset(
    {"normal", "mixed", "prepare mixed", "concurrent start", "concurrent end"}
)

_COLLECTOR_MAP = [
    ("g1", "G1"),
    ("parallel", "Parallel"),
    ("serial", "Serial"),
    ("concurrent mark sweep", "CMS"),
    ("z garbage collector", "ZGC"),
    ("z ", "ZGC"),
    ("shenandoah", "Shenandoah"),
]


def _split_decorations(line: str) -> Tuple[List[str], str]:
    """Split leading ``[...]`` decoration groups from the message."""
    decorations: List[str] = []
    rest = line
    while rest.startswith("["):
        end = rest.find("]")
        if end < 0:
            break
        decorations.append(rest[1:end])
        rest = rest[end + 1 :]
    return decorations, rest.strip()


def _parse_iso_seconds(text: str) -> Optional[float]:
    """Parse an ISO-8601 decoration to epoch-ish seconds, deterministically.

    Naive timestamps are treated as UTC so a report never depends on the
    machine's local time zone; only differences between timestamps are used
    anyway (see :func:`gcgauge.events.finalize_times`).
    """
    m = _ISO_RE.match(text)
    if not m:
        return None
    year, month, day, hour, minute, second = (int(m.group(i)) for i in range(1, 7))
    frac = m.group(7)
    millis = int(frac.ljust(3, "0")) if frac else 0
    offset = m.group(8)
    # Days since an arbitrary epoch via a proleptic-Gregorian ordinal.
    try:
        ordinal = datetime.date(year, month, day).toordinal()
    except ValueError:
        return None
    seconds = (
        ordinal * 86400.0 + hour * 3600.0 + minute * 60.0 + second + millis / 1000.0
    )
    if offset and offset != "Z":
        sign = 1 if offset[0] == "+" else -1
        digits = offset[1:].replace(":", "")
        seconds -= sign * (int(digits[:2]) * 3600 + int(digits[2:4]) * 60)
    return seconds


def _classify_decorations(
    decorations: List[str],
) -> Tuple[Optional[float], Optional[float], bool, bool]:
    """Return (uptime_s, absolute_s, has_level_or_tags, is_start_line)."""
    uptime = absolute = None
    meta = False
    is_start = False
    for dec in decorations:
        m = _UPTIME_RE.match(dec)
        if m:
            value = float(m.group(1))
            uptime = value / 1000.0 if m.group(2) == "ms" else value
            continue
        iso = _parse_iso_seconds(dec)
        if iso is not None:
            absolute = iso
            continue
        if dec in _LEVELS:
            meta = True
            continue
        tags = [t.strip() for t in dec.split(",")]
        if "gc" in tags:
            meta = True
            if "start" in tags:
                is_start = True
    return uptime, absolute, meta, is_start


def looks_unified(line: str) -> bool:
    """Cheap format-sniff predicate for one line."""
    decorations, rest = _split_decorations(line)
    if decorations:
        _, _, meta, _ = _classify_decorations(decorations)
        return meta
    return rest.startswith("GC(") or rest.startswith("Using ")


def _detect_collector(message: str) -> Optional[str]:
    lowered = message[len("Using ") :].strip().lower()
    for needle, name in _COLLECTOR_MAP:
        if needle in lowered:
            return name
    return None


def _qualifiers(blob: str) -> List[str]:
    return [m.group(0)[1:-1].strip() for m in _QUAL_FINDER.finditer(blob)]


def _heap_from_groups(groups: Tuple[str, ...]) -> Tuple[int, int, int]:
    before = size_to_kb(float(groups[0]), groups[1])
    after = size_to_kb(float(groups[2]), groups[3])
    total = size_to_kb(float(groups[4]), groups[5])
    return before, after, total


def _kind_for_pause(name: str, quals: List[str]) -> str:
    lowered = name.strip().lower()
    if lowered == "young":
        modes = {q.lower() for q in quals}
        return "mixed" if modes & {"mixed", "prepare mixed"} else "young"
    if lowered == "full":
        return "full"
    return lowered.replace(" ", "-")


def _cause_from(quals: List[str]) -> Optional[str]:
    causes = [q for q in quals if q.lower() not in _MODES]
    return causes[-1] if causes else None


def parse_unified(lines: Iterable[str]) -> ParseResult:
    """Parse unified-logging lines into a :class:`ParseResult`."""
    result = ParseResult(format="unified")
    exhausted_ids = set()
    saw_absolute_only = True

    for line_no, raw in enumerate(lines, start=1):
        result.total_lines += 1
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        if "OutOfMemoryError" in line:
            result.oom_count += 1

        decorations, message = _split_decorations(line)
        uptime, absolute, meta, is_start = _classify_decorations(decorations)
        if decorations and not meta:
            continue  # bracketed line from some other subsystem
        if not decorations and not (
            message.startswith("GC(") or message.startswith("Using ")
        ):
            continue
        if is_start:
            continue  # "GC(5) Pause Young ... (start)" — wait for the completion line

        time_s = uptime if uptime is not None else absolute
        if uptime is not None:
            saw_absolute_only = False

        if message.startswith("Using "):
            collector = _detect_collector(message)
            if collector:
                result.collector = collector
            continue

        gcid_match = _GCID_RE.match(message)
        if not gcid_match:
            continue
        gc_id = int(gcid_match.group(1))
        body = gcid_match.group(2).strip()

        if body == "To-space exhausted":
            exhausted_ids.add(gc_id)
            continue

        event = _event_from_body(body, gc_id, time_s, line_no)
        if event is not None:
            result.events.append(event)

    for event in result.events:
        if event.gc_id in exhausted_ids and event.is_pause:
            event.evacuation_failure = True

    if result.events and saw_absolute_only:
        if any(e.time_s is not None for e in result.events):
            result.clock = "absolute"
    _infer_collector(result)
    return result


def _event_from_body(
    body: str, gc_id: int, time_s: Optional[float], line_no: int
) -> Optional[GCEvent]:
    m = _PAUSE_RE.match(body)
    if m:
        name, qual_blob = m.group(1), m.group(2)
        quals = _qualifiers(qual_blob)
        heap_groups = m.groups()[2:8]
        before = after = total = None
        if heap_groups[0] is not None:
            before, after, total = _heap_from_groups(heap_groups)
        return GCEvent(
            time_s=time_s,
            kind=_kind_for_pause(name, quals),
            gc_id=gc_id,
            cause=_cause_from(quals),
            pause_ms=float(m.group(9)),
            heap_before_kb=before,
            heap_after_kb=after,
            heap_total_kb=total,
            is_pause=True,
            line_no=line_no,
        )

    m = _CONCURRENT_RE.match(body)
    if m:
        heap_groups = m.groups()[1:7]
        before = after = total = None
        if heap_groups[0] is not None:
            before, after, total = _heap_from_groups(heap_groups)
        return GCEvent(
            time_s=time_s,
            kind="concurrent",
            gc_id=gc_id,
            cause=m.group(1).strip(),
            pause_ms=float(m.group(8)),
            heap_before_kb=before,
            heap_after_kb=after,
            heap_total_kb=total,
            is_pause=False,
            line_no=line_no,
        )

    m = _ZGC_RE.match(body)
    if m:
        before = size_to_kb(float(m.group(3)), m.group(4))
        before_pct = int(m.group(5))
        after = size_to_kb(float(m.group(6)), m.group(7))
        total = (
            int(round(before * 100.0 / before_pct)) if before_pct > 0 else None
        )
        duration = None
        if m.group(9) is not None:
            duration = float(m.group(9))
            if m.group(10) == "s":
                duration *= 1000.0
        return GCEvent(
            time_s=time_s,
            kind="cycle",
            gc_id=gc_id,
            cause=m.group(2).strip(),
            pause_ms=duration,
            heap_before_kb=before,
            heap_after_kb=after,
            heap_total_kb=total,
            is_pause=False,
            line_no=line_no,
        )

    return None


def _infer_collector(result: ParseResult) -> None:
    """Fall back to shape-based collector inference when 'Using …' is absent."""
    if result.collector != "unknown" or not result.events:
        return
    kinds = {e.kind for e in result.events}
    causes = " ".join(e.cause or "" for e in result.events)
    if "cycle" in kinds:
        result.collector = "ZGC"
    elif kinds & {"init-mark", "final-mark", "init-update-refs", "final-update-refs"}:
        result.collector = "Shenandoah"
    elif "G1" in causes or "mixed" in kinds:
        result.collector = "G1"
