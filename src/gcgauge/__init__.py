"""gcgauge — JVM GC log analysis: pause percentiles, heap trends, leak indicators.

Library entry points::

    from gcgauge import parse_log, analyze, diff_reports

    report = analyze(parse_log("gc.log"), source="gc.log")
    print(report["pauses"]["all"]["p99_ms"])

Everything is standard library only; parsing and analysis never touch the
network or the clock, so identical logs produce identical reports.
"""

from .analyze import analyze, analyze_log
from .diffing import diff_reports
from .errors import EmptyLogError, GcgaugeError, ParseError, UnknownFormatError
from .events import GCEvent, ParseResult
from .parser import parse_lines, parse_log
from .stats import linear_regression, percentile

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "analyze",
    "analyze_log",
    "diff_reports",
    "parse_log",
    "parse_lines",
    "percentile",
    "linear_regression",
    "GCEvent",
    "ParseResult",
    "GcgaugeError",
    "ParseError",
    "UnknownFormatError",
    "EmptyLogError",
]
