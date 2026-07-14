"""Parser for JDK 8-era GC logs (``-XX:+PrintGCDetails``).

Covers the classic line shapes:

* Parallel/Serial minor:
  ``1.234: [GC (Allocation Failure) [PSYoungGen: 65536K->10748K(76288K)]
  65536K->10756K(251392K), 0.0123456 secs]``
* Full:
  ``9.876: [Full GC (Ergonomics) [PSYoungGen: ...] [ParOldGen: ...]
  10756K->10444K(251392K), [Metaspace: ...], 0.0621829 secs]``
* G1 on JDK 8:
  ``1.234: [GC pause (G1 Evacuation Pause) (young) 12M->8M(64M), 0.0034567 secs]``
* CMS stop-the-world phases (``CMS Initial Mark`` / ``CMS Final Remark``, which
  report a single occupancy instead of a before->after transition) and
  ``[CMS-concurrent-<phase>: cpu/wall secs]`` progress lines.

Both ``-XX:+PrintGCTimeStamps`` (uptime) and ``-XX:+PrintGCDateStamps``
(wall clock) prefixes are accepted, in either order or alone. The strategy for
heap figures is deliberately structural: every nested ``[Gen: ...]`` segment
is stripped first, and the transition that remains at the top level is the
whole-heap one — so unusual generation layouts cannot be mistaken for totals.
"""

import re
from typing import Iterable, Optional

from .events import GCEvent, ParseResult, size_to_kb
from .unified import _parse_iso_seconds

_ISO_PREFIX = r"(\d{4}-\d{2}-\d{2}T[\d:.,]+(?:Z|[+-]\d{2}:?\d{2})?)"

_PREFIX_RE = re.compile(
    r"^(?:" + _ISO_PREFIX + r":\s+)?"
    r"(?:(\d+(?:\.\d+)?):\s+)?"
    r"\[(Full GC|GC)\b(\s+pause)?"
)

_QUAL_RE = re.compile(r"\((?:[^()]|\([^()]*\))*\)")

_TRANSITION_RE = re.compile(
    r"(\d+(?:\.\d+)?)([BKMG])B?->(\d+(?:\.\d+)?)([BKMG])B?"
    r"\((\d+(?:\.\d+)?)([BKMG])B?\)"
)

# CMS Initial Mark / Final Remark report "occupancy(capacity)" with no arrow.
_OCCUPANCY_RE = re.compile(
    r"(\d+(?:\.\d+)?)([BKMG])B?\((\d+(?:\.\d+)?)([BKMG])B?\)"
)

_SECS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*secs?")

_CMS_CONCURRENT_RE = re.compile(
    r"^(?:" + _ISO_PREFIX + r":\s+)?"
    r"(?:(\d+(?:\.\d+)?):\s+)?"
    r"\[CMS-concurrent-([a-z-]+(?:-start)?): "
    r"(?:(\d+\.\d+)/(\d+\.\d+) secs)?"
)

_NESTED_BRACKET_RE = re.compile(r"\[[^\[\]]*\]")

#: Substrings that identify the collector from generation names.
_GEN_HINTS = [
    ("PSYoungGen", "Parallel"),
    ("ParOldGen", "Parallel"),
    ("CMS", "CMS"),
    ("ParNew", "CMS"),
    ("DefNew", "Serial"),
    ("Tenured", "Serial"),
    ("G1 ", "G1"),
    ("garbage-first", "G1"),
]

#: CMS stop-the-world phase causes mapped to normalized kinds.
_CMS_PAUSE_KINDS = {
    "cms initial mark": "initial-mark",
    "cms final remark": "remark",
}


def looks_legacy(line: str) -> bool:
    """Cheap format-sniff predicate for one line."""
    return bool(_PREFIX_RE.match(line)) or bool(_CMS_CONCURRENT_RE.match(line))


def _strip_nested_brackets(text: str) -> str:
    """Remove innermost ``[...]`` groups until none remain."""
    previous = None
    while previous != text:
        previous = text
        text = _NESTED_BRACKET_RE.sub(" ", text)
    return text


def _times(iso: Optional[str], uptime: Optional[str]) -> Optional[float]:
    if uptime is not None:
        return float(uptime)
    if iso is not None:
        return _parse_iso_seconds(iso)
    return None


def parse_legacy(lines: Iterable[str]) -> ParseResult:
    """Parse JDK 8 ``PrintGCDetails`` lines into a :class:`ParseResult`."""
    result = ParseResult(format="legacy")
    saw_uptime = False
    collector_hint: Optional[str] = None

    for line_no, raw in enumerate(lines, start=1):
        result.total_lines += 1
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        if "OutOfMemoryError" in line:
            result.oom_count += 1

        if collector_hint is None:
            for needle, name in _GEN_HINTS:
                if needle in line:
                    collector_hint = name
                    break

        cms = _CMS_CONCURRENT_RE.match(line)
        if cms:
            if cms.group(3).endswith("-start") or cms.group(5) is None:
                continue  # phase start markers carry no duration
            if cms.group(2) is not None:
                saw_uptime = True
            result.events.append(
                GCEvent(
                    time_s=_times(cms.group(1), cms.group(2)),
                    kind="concurrent",
                    cause="CMS " + cms.group(3),
                    pause_ms=float(cms.group(5)) * 1000.0,
                    is_pause=False,
                    line_no=line_no,
                )
            )
            continue

        prefix = _PREFIX_RE.match(line)
        if not prefix:
            continue
        if prefix.group(2) is not None:
            saw_uptime = True

        event = _event_from_line(line, prefix, line_no)
        if event is not None:
            result.events.append(event)

    result.collector = collector_hint or "unknown"
    if result.events and not saw_uptime:
        if any(e.time_s is not None for e in result.events):
            result.clock = "absolute"
    return result


def _event_from_line(line: str, prefix: "re.Match", line_no: int) -> Optional[GCEvent]:
    time_s = _times(prefix.group(1), prefix.group(2))
    is_full = prefix.group(3) == "Full GC"
    body = line[prefix.end() :]

    # Qualifiers immediately after "[GC" / "[Full GC" / "[GC pause".
    quals = []
    cursor = 0
    while True:
        while cursor < len(body) and body[cursor] == " ":
            cursor += 1
        m = _QUAL_RE.match(body, cursor)
        if not m:
            break
        quals.append(m.group(0)[1:-1].strip())
        cursor = m.end()

    cause = quals[0] if quals else None
    kind = "full" if is_full else "young"
    if not is_full:
        lowered = [q.lower() for q in quals]
        if "mixed" in lowered:
            kind = "mixed"
        elif cause and cause.lower() in _CMS_PAUSE_KINDS:
            kind = _CMS_PAUSE_KINDS[cause.lower()]

    flat = _strip_nested_brackets(body)
    transition = None
    for m in _TRANSITION_RE.finditer(flat):
        transition = m  # keep the last top-level transition = whole heap
    secs = None
    for m in _SECS_RE.finditer(flat):
        secs = m  # total pause is the last "N secs" on the line

    before = after = total = None
    if transition is not None:
        before = size_to_kb(float(transition.group(1)), transition.group(2))
        after = size_to_kb(float(transition.group(3)), transition.group(4))
        total = size_to_kb(float(transition.group(5)), transition.group(6))
    else:
        occ = _OCCUPANCY_RE.search(flat)
        if occ:
            before = after = size_to_kb(float(occ.group(1)), occ.group(2))
            total = size_to_kb(float(occ.group(3)), occ.group(4))

    if secs is None:
        return None  # an in-progress or truncated line; nothing to measure

    return GCEvent(
        time_s=time_s,
        kind=kind,
        cause=cause,
        pause_ms=float(secs.group(1)) * 1000.0,
        heap_before_kb=before,
        heap_after_kb=after,
        heap_total_kb=total,
        is_pause=True,
        evacuation_failure="promotion failed" in line
        or "--[" in line  # JDK 8 marks a scavenge that could not promote with "--"
        or "to-space exhausted" in line.lower()
        or "to-space overflow" in line.lower(),
        line_no=line_no,
    )
