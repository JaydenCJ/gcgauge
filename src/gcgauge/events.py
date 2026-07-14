"""Normalized GC event model shared by every parser.

Both the JDK 9+ unified parser and the JDK 8 legacy parser reduce their input
to a flat list of :class:`GCEvent`. Everything downstream (statistics, leak
scoring, rendering, diffing) works only on this model, so adding a new log
dialect never touches the analysis code.

Sizes are normalized to KiB and times to seconds so that logs printed with
different units ("24M" vs "24576K") compare equal.
"""

from dataclasses import dataclass, field
from typing import List, Optional

#: Multipliers from a JVM size-unit letter to KiB.
_UNIT_TO_KB = {
    "B": 1.0 / 1024.0,
    "K": 1.0,
    "M": 1024.0,
    "G": 1024.0 * 1024.0,
    "T": 1024.0 * 1024.0 * 1024.0,
}


def size_to_kb(value: float, unit: str) -> int:
    """Convert ``value`` with JVM unit letter ``unit`` (B/K/M/G/T) to whole KiB.

    Raises ``ValueError`` for an unknown unit letter so parser regressions
    surface loudly instead of silently mis-scaling heap numbers.
    """
    try:
        factor = _UNIT_TO_KB[unit.upper()]
    except KeyError:
        raise ValueError("unknown size unit: %r" % unit) from None
    return int(round(value * factor))


@dataclass
class GCEvent:
    """One garbage-collection occurrence, normalized across log dialects.

    ``kind`` is a lowercase slug: ``young``, ``mixed``, ``full``, ``remark``,
    ``cleanup``, ``concurrent``, ``cycle`` (ZGC), or a dialect-specific pause
    name such as ``init-mark``. ``is_pause`` is True for stop-the-world
    events only; concurrent phases and ZGC cycles carry a duration but do not
    stall the application and are excluded from pause percentiles.
    """

    time_s: Optional[float]
    kind: str
    gc_id: Optional[int] = None
    cause: Optional[str] = None
    pause_ms: Optional[float] = None
    heap_before_kb: Optional[int] = None
    heap_after_kb: Optional[int] = None
    heap_total_kb: Optional[int] = None
    is_pause: bool = True
    evacuation_failure: bool = False
    line_no: int = 0

    @property
    def reclaimed_kb(self) -> Optional[int]:
        """KiB freed by this event, or None if heap figures are missing."""
        if self.heap_before_kb is None or self.heap_after_kb is None:
            return None
        return self.heap_before_kb - self.heap_after_kb


@dataclass
class ParseResult:
    """Everything a parser learned from one log file."""

    events: List[GCEvent] = field(default_factory=list)
    format: str = "unknown"  # "unified" | "legacy"
    collector: str = "unknown"  # G1 | Parallel | Serial | CMS | ZGC | Shenandoah
    clock: str = "uptime"  # "uptime" | "absolute" | "index"
    total_lines: int = 0
    oom_count: int = 0  # lines mentioning java.lang.OutOfMemoryError
    warnings: List[str] = field(default_factory=list)

    @property
    def pauses(self) -> List[GCEvent]:
        """Stop-the-world events only, in file order."""
        return [e for e in self.events if e.is_pause]


def finalize_times(result: ParseResult) -> None:
    """Normalize event timestamps in place to seconds relative to run start.

    Three cases, recorded in ``result.clock``:

    * ``uptime`` — the log carried JVM uptime; values are already relative.
    * ``absolute`` — only wall-clock timestamps were present; the first
      event becomes t=0 so reports stay byte-identical across time zones.
    * ``index`` — the log carried no timestamps at all (``-Xlog:gc:...:none``);
      each event gets its ordinal as the time so trends still have an x-axis,
      and a warning is recorded.

    Events missing a timestamp inside an otherwise-stamped log inherit the
    previous event's time, which keeps ordering stable.
    """
    times = [e.time_s for e in result.events]
    if any(t is not None for t in times):
        if result.clock == "absolute":
            t0 = next(t for t in times if t is not None)
            for e in result.events:
                if e.time_s is not None:
                    e.time_s = round(e.time_s - t0, 6)
        last = 0.0
        for e in result.events:
            if e.time_s is None:
                e.time_s = last
            else:
                last = e.time_s
    else:
        result.clock = "index"
        if result.events:
            result.warnings.append(
                "log has no timestamps; using event index as the time axis"
            )
        for i, e in enumerate(result.events):
            e.time_s = float(i)
