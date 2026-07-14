"""Report renderers: text (default), markdown, and JSON.

The JSON renderer is the canonical machine format — sorted keys, fixed
rounding, trailing newline — and its output is accepted back as a diff or
check input. Text and markdown are projections of the same dict; they never
compute anything themselves, so the three formats cannot disagree.
"""

import json
from typing import List, Optional

_PAUSE_COLUMNS = ["count", "p50_ms", "p90_ms", "p95_ms", "p99_ms", "max_ms", "total_ms"]
_PAUSE_HEADERS = ["class", "count", "p50", "p90", "p95", "p99", "max", "total"]


def _fmt(value: Optional[float], decimals: int = 2) -> str:
    if value is None:
        return "-"
    if isinstance(value, int):
        return str(value)
    return "%.*f" % (decimals, value)


def render_json(report: dict) -> str:
    return json.dumps(report, sort_keys=True, indent=2) + "\n"


def _pause_rows(report: dict) -> List[List[str]]:
    rows = []
    classes = report["pauses"]["classes"]
    for kind, stats in classes.items():
        rows.append(
            [kind]
            + [_fmt(stats[c]) if c != "count" else str(stats[c]) for c in _PAUSE_COLUMNS]
        )
    overall = report["pauses"]["all"]
    if overall and len(classes) != 1:
        rows.append(
            ["all"]
            + [
                _fmt(overall[c]) if c != "count" else str(overall[c])
                for c in _PAUSE_COLUMNS
            ]
        )
    return rows


def _align(headers: List[str], rows: List[List[str]], indent: str = "  ") -> List[str]:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = []
    for row in [headers] + rows:
        cells = [row[0].ljust(widths[0])] + [
            cell.rjust(widths[i]) for i, cell in enumerate(row) if i > 0
        ]
        lines.append(indent + "  ".join(cells).rstrip())
    return lines


def render_text(report: dict) -> str:
    """Human-oriented plain-text report (no color, stable column layout)."""
    w = report["window"]
    lines = [
        "gcgauge report — %s" % report["source"],
        "format: %s   collector: %s   clock: %s" % (
            report["format"], report["collector"], report["clock"],
        ),
        "events: %d (%d pauses, %d concurrent)   window: %ss -> %ss (%.1f min)" % (
            report["events"]["total"],
            report["events"]["pauses"],
            report["events"]["concurrent"],
            _fmt(w["start_s"]),
            _fmt(w["end_s"]),
            w["duration_s"] / 60.0,
        ),
        "",
        "Pause percentiles (ms)",
    ]
    lines += _align(_PAUSE_HEADERS, _pause_rows(report))
    lines.append("")

    summary = []
    if report["throughput_pct"] is not None:
        summary.append(
            "Throughput %s%% — %ss paused of %ss"
            % (_fmt(report["throughput_pct"]), _fmt(report["total_pause_s"]),
               _fmt(w["duration_s"]))
        )
    if report["allocation_rate_mb_s"] is not None:
        summary.append("Allocation rate %s MB/s" % _fmt(report["allocation_rate_mb_s"]))
    if summary:
        lines.append("   ".join(summary))
        lines.append("")

    heap = report["heap"]
    floor = heap["post_gc_floor"]
    header = "Heap"
    if heap["total_mb"] is not None:
        header += " (total %s MB)" % _fmt(heap["total_mb"], 1)
    lines.append(header)
    if floor:
        lines.append(
            "  post-GC floor: %s MB -> %s MB   slope %+.3f MB/min   r²=%s"
            " (%d samples, %s basis)"
            % (
                _fmt(floor["first_mb"]),
                _fmt(floor["last_mb"]),
                floor["slope_mb_per_min"],
                _fmt(floor["r2"], 3),
                floor["samples"],
                floor["basis"],
            )
        )
        if "last_quarter_occupancy_pct" in floor:
            lines.append(
                "  post-GC occupancy, last quarter: %s%% of heap"
                % _fmt(floor["last_quarter_occupancy_pct"], 1)
            )
    else:
        lines.append("  no heap figures in this log")
    fg = report["full_gc"]
    if fg["count"]:
        reclaim = (
            ", avg reclaim %s%% of heap" % _fmt(fg["avg_reclaim_pct"], 1)
            if fg["avg_reclaim_pct"] is not None
            else ""
        )
        lines.append(
            "  full GC: %d total (%d first half -> %d second half)%s"
            % (fg["count"], fg["first_half"], fg["second_half"], reclaim)
        )
    lines.append("")

    leak = report["leak"]
    lines.append(
        "Leak indicators — verdict: %s (score %d/%d)"
        % (leak["verdict"].upper(), leak["score"], leak["max_score"])
    )
    label_width = max(len(i["label"]) for i in leak["indicators"])
    for ind in leak["indicators"]:
        lines.append(
            "  [%-8s] %-*s  %s"
            % (ind["severity"], label_width, ind["label"], ind["detail"])
        )

    for warning in report["warnings"]:
        lines.append("")
        lines.append("note: %s" % warning)
    return "\n".join(lines) + "\n"


def render_markdown(report: dict) -> str:
    """Markdown report suitable for pasting into a PR or issue."""
    w = report["window"]
    lines = [
        "# gcgauge report — %s" % report["source"],
        "",
        "| Key | Value |",
        "|---|---|",
        "| Format | %s |" % report["format"],
        "| Collector | %s |" % report["collector"],
        "| Events | %d (%d pauses, %d concurrent) |" % (
            report["events"]["total"],
            report["events"]["pauses"],
            report["events"]["concurrent"],
        ),
        "| Window | %ss -> %ss (%.1f min) |" % (
            _fmt(w["start_s"]), _fmt(w["end_s"]), w["duration_s"] / 60.0,
        ),
        "| Throughput | %s%% |" % _fmt(report["throughput_pct"]),
        "| Allocation rate | %s MB/s |" % _fmt(report["allocation_rate_mb_s"]),
        "",
        "## Pause percentiles (ms)",
        "",
        "| " + " | ".join(_PAUSE_HEADERS) + " |",
        "|" + "---|" * len(_PAUSE_HEADERS),
    ]
    for row in _pause_rows(report):
        lines.append("| " + " | ".join(row) + " |")

    leak = report["leak"]
    lines += [
        "",
        "## Leak indicators — verdict: **%s** (score %d/%d)"
        % (leak["verdict"], leak["score"], leak["max_score"]),
        "",
        "| Indicator | Status | Evidence |",
        "|---|---|---|",
    ]
    for ind in leak["indicators"]:
        lines.append(
            "| %s | %s | %s |" % (ind["label"], ind["severity"], ind["detail"])
        )
    for warning in report["warnings"]:
        lines.append("")
        lines.append("> note: %s" % warning)
    return "\n".join(lines) + "\n"


RENDERERS = {
    "text": render_text,
    "markdown": render_markdown,
    "json": render_json,
}
