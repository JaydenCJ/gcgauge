"""Command-line interface.

Three subcommands, three jobs:

* ``report`` — one log in, one deterministic report out (text/markdown/json).
* ``diff``   — two runs in (logs or saved JSON reports), regression table
  out; exit 1 when anything regresses beyond the threshold.
* ``check``  — one run against explicit budgets; exit 1 on any violation.

Exit codes: 0 success, 1 gate failed (diff regression / check violation),
2 input or usage error. Parse problems never raise a traceback at the user.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .analyze import analyze
from .diffing import diff_reports, render_diff_text
from .errors import GcgaugeError
from .parser import parse_log
from .render import RENDERERS, render_json

_LEAK_RANK = {"none": 0, "possible": 1, "likely": 2}


def _load_report(path: str) -> dict:
    """Load either a raw GC log or a previously exported JSON report.

    Detection is by content, not extension: a file whose first non-blank
    character is ``{`` and that parses as JSON with a ``gcgauge_report`` key
    is a saved report; anything else goes through the log parsers.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if isinstance(data, dict) and "gcgauge_report" in data:
            return data
        raise GcgaugeError(
            "%s looks like JSON but is not a gcgauge report "
            "(missing the gcgauge_report key)" % path
        )
    return analyze(parse_log(path), source=path)


def _emit(text: str, out: Optional[str]) -> None:
    if out:
        Path(out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def _cmd_report(args: argparse.Namespace) -> int:
    report = analyze(parse_log(args.log), source=args.log)
    _emit(RENDERERS[args.format](report), args.output)
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    baseline = _load_report(args.baseline)
    current = _load_report(args.current)
    diff = diff_reports(baseline, current, threshold_pct=args.threshold)
    if args.format == "json":
        _emit(json.dumps(diff, sort_keys=True, indent=2) + "\n", args.output)
    else:
        _emit(render_diff_text(diff), args.output)
    return 1 if diff["regressions"] else 0


def _cmd_check(args: argparse.Namespace) -> int:
    budgets_given = any(
        v is not None
        for v in (args.max_p99, args.max_pause, args.min_throughput, args.fail_on_leak)
    )
    if not budgets_given:
        raise GcgaugeError(
            "no budgets given — pass at least one of --max-p99, --max-pause, "
            "--min-throughput, --fail-on-leak"
        )
    report = _load_report(args.log)
    lines = ["gcgauge check — %s" % report["source"]]
    failures = 0
    checks = 0

    def record(ok: bool, text: str) -> None:
        nonlocal failures, checks
        checks += 1
        if not ok:
            failures += 1
        lines.append("  %s  %s" % ("PASS" if ok else "FAIL", text))

    overall = report["pauses"]["all"]
    if args.max_p99 is not None:
        value = overall["p99_ms"] if overall else None
        if value is None:
            record(False, "p99 pause unavailable (no pauses in the log)")
        else:
            record(
                value <= args.max_p99,
                "p99 pause %.2f ms <= %.2f ms" % (value, args.max_p99),
            )
    if args.max_pause is not None:
        value = overall["max_ms"] if overall else None
        if value is None:
            record(False, "max pause unavailable (no pauses in the log)")
        else:
            record(
                value <= args.max_pause,
                "max pause %.2f ms <= %.2f ms" % (value, args.max_pause),
            )
    if args.min_throughput is not None:
        value = report["throughput_pct"]
        if value is None:
            record(False, "throughput unavailable (window too short)")
        else:
            record(
                value >= args.min_throughput,
                "throughput %.2f%% >= %.2f%%" % (value, args.min_throughput),
            )
    if args.fail_on_leak is not None:
        verdict = report["leak"]["verdict"]
        ok = _LEAK_RANK[verdict] < _LEAK_RANK[args.fail_on_leak]
        record(ok, "leak verdict %s (fails at: %s)" % (verdict, args.fail_on_leak))

    if failures:
        lines.append("%d of %d check(s) failed" % (failures, checks))
    else:
        lines.append("all %d check(s) passed" % checks)
    _emit("\n".join(lines) + "\n", args.output)
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gcgauge",
        description=(
            "Parse JVM GC logs into pause percentiles, heap trends, and leak "
            "indicators — offline, deterministic, diff-able."
        ),
    )
    parser.add_argument(
        "--version", action="version", version="gcgauge %s" % __version__
    )
    sub = parser.add_subparsers(dest="command", metavar="command")

    p_report = sub.add_parser(
        "report", help="analyze one GC log and print a report"
    )
    p_report.add_argument("log", help="path to the GC log")
    p_report.add_argument(
        "--format",
        choices=sorted(RENDERERS),
        default="text",
        help="output format (default: text)",
    )
    p_report.add_argument(
        "-o", "--output", metavar="FILE", help="write to FILE instead of stdout"
    )
    p_report.set_defaults(func=_cmd_report)

    p_diff = sub.add_parser(
        "diff", help="compare two runs (GC logs or saved JSON reports)"
    )
    p_diff.add_argument("baseline", help="baseline run: GC log or JSON report")
    p_diff.add_argument("current", help="current run: GC log or JSON report")
    p_diff.add_argument(
        "--threshold",
        type=float,
        default=10.0,
        metavar="PCT",
        help="regression threshold in percent (default: 10.0)",
    )
    p_diff.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="output format (default: text)",
    )
    p_diff.add_argument(
        "-o", "--output", metavar="FILE", help="write to FILE instead of stdout"
    )
    p_diff.set_defaults(func=_cmd_diff)

    p_check = sub.add_parser(
        "check", help="gate a run against explicit pause/throughput/leak budgets"
    )
    p_check.add_argument("log", help="GC log or saved JSON report")
    p_check.add_argument(
        "--max-p99", type=float, metavar="MS", help="fail if p99 pause exceeds MS"
    )
    p_check.add_argument(
        "--max-pause", type=float, metavar="MS", help="fail if any pause exceeds MS"
    )
    p_check.add_argument(
        "--min-throughput",
        type=float,
        metavar="PCT",
        help="fail if throughput drops below PCT",
    )
    p_check.add_argument(
        "--fail-on-leak",
        choices=["possible", "likely"],
        help="fail if the leak verdict reaches this level",
    )
    p_check.add_argument(
        "-o", "--output", metavar="FILE", help="write to FILE instead of stdout"
    )
    p_check.set_defaults(func=_cmd_check)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except GcgaugeError as exc:
        print("gcgauge: error: %s" % exc, file=sys.stderr)
        return 2
    except OSError as exc:
        print("gcgauge: error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
