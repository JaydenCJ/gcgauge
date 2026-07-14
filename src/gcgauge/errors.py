"""Exception hierarchy for gcgauge.

Everything raised on purpose by this package derives from :class:`GcgaugeError`,
so callers embedding gcgauge as a library can catch one type. The CLI maps
these to exit code 2 (usage / input error) and never lets them escape as a
traceback.
"""


class GcgaugeError(Exception):
    """Base class for all gcgauge errors."""


class ParseError(GcgaugeError):
    """The log file could not be turned into GC events."""


class UnknownFormatError(ParseError):
    """The file does not look like any supported JVM GC log format."""


class EmptyLogError(ParseError):
    """The log file exists but contains no lines at all."""
