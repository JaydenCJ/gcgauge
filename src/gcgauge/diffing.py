"""Cross-run comparison of two reports.

Both sides can be a raw GC log or a JSON report exported earlier with
``gcgauge report --format json`` — teams commit the baseline JSON next to
their load-test scripts and diff each new run against it without keeping the
original multi-megabyte log around.

A metric "regresses" when it moves in its bad direction by more than the
threshold percentage. Pause percentiles and GC overhead are lower-is-better;
the leak verdict regresses on any escalation (none -> possible -> likely).
Allocation rate is reported for context but never gates: allocating more is
usually the application doing more work, not a GC problem.
"""

from typing import List, Optional, Sequence, Tuple

_LEAK_RANK = {"none": 0, "possible": 1, "likely": 2}

#: (id, human label, path into the report dict, direction)
#: direction: "lower" = lower is better, "info" = never gates.
_METRICS: Sequence[Tuple[str, str, Tuple[str, ...], str]] = [
    ("p50_ms", "p50 pause (ms)", ("pauses", "all", "p50_ms"), "lower"),
    ("p90_ms", "p90 pause (ms)", ("pauses", "all", "p90_ms"), "lower"),
    ("p99_ms", "p99 pause (ms)", ("pauses", "all", "p99_ms"), "lower"),
    ("max_ms", "max pause (ms)", ("pauses", "all", "max_ms"), "lower"),
    ("gc_overhead_pct", "GC overhead (%)", ("gc_overhead_pct",), "lower"),
    ("full_gc_per_min", "full GC / min", ("full_gc", "per_min"), "lower"),
    ("alloc_mb_s", "alloc rate (MB/s)", ("allocation_rate_mb_s",), "info"),
]


def _lookup(report: dict, path: Tuple[str, ...]) -> Optional[float]:
    node = report
    for key in path:
        if not isinstance(node, dict) or node.get(key) is None:
            return None
        node = node[key]
    return node  # type: ignore[return-value]


def _numeric_row(
    id_: str,
    label: str,
    base: Optional[float],
    cur: Optional[float],
    direction: str,
    threshold_pct: float,
) -> dict:
    row = {
        "id": id_,
        "label": label,
        "baseline": base,
        "current": cur,
        "delta_pct": None,
        "verdict": "info" if direction == "info" else "ok",
    }
    if base is None or cur is None:
        row["verdict"] = "n/a"
        return row
    if base == 0.0:
        if cur == 0.0:
            row["delta_pct"] = 0.0
        else:
            # From nothing to something: no finite percentage exists.
            row["delta_pct"] = None
            if direction == "lower":
                row["verdict"] = "regression"
            return row
    else:
        row["delta_pct"] = round((cur - base) / base * 100.0, 1)
    if direction == "lower" and row["delta_pct"] is not None:
        if row["delta_pct"] > threshold_pct:
            row["verdict"] = "regression"
        elif row["delta_pct"] < -threshold_pct:
            row["verdict"] = "improved"
    return row


def _leak_row(base: dict, cur: dict) -> dict:
    b = base["leak"]["verdict"]
    c = cur["leak"]["verdict"]
    if _LEAK_RANK[c] > _LEAK_RANK[b]:
        verdict, delta = "regression", "escalated"
    elif _LEAK_RANK[c] < _LEAK_RANK[b]:
        verdict, delta = "improved", "reduced"
    else:
        verdict, delta = "ok", "same"
    return {
        "id": "leak_verdict",
        "label": "leak verdict",
        "baseline": b,
        "current": c,
        "delta_pct": None,
        "delta_text": delta,
        "verdict": verdict,
    }


def diff_reports(baseline: dict, current: dict, threshold_pct: float = 10.0) -> dict:
    """Compare two report dicts and return the diff structure."""
    rows: List[dict] = []
    for id_, label, path, direction in _METRICS:
        rows.append(
            _numeric_row(
                id_,
                label,
                _lookup(baseline, path),
                _lookup(current, path),
                direction,
                threshold_pct,
            )
        )
    rows.append(_leak_row(baseline, current))
    return {
        "gcgauge_diff": 1,
        "baseline": baseline["source"],
        "current": current["source"],
        "threshold_pct": threshold_pct,
        "metrics": rows,
        "regressions": sum(1 for r in rows if r["verdict"] == "regression"),
        "improvements": sum(1 for r in rows if r["verdict"] == "improved"),
    }


def _fmt_value(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return value
    return "%.2f" % value


def _fmt_delta(row: dict) -> str:
    if "delta_text" in row:
        return row["delta_text"]
    if row["delta_pct"] is None:
        if row["verdict"] == "regression":
            return "new"
        return "-"
    return "%+.1f%%" % row["delta_pct"]


def render_diff_text(diff: dict) -> str:
    """Aligned plain-text diff table."""
    headers = ["metric", "baseline", "current", "delta", "verdict"]
    rows = [
        [r["label"], _fmt_value(r["baseline"]), _fmt_value(r["current"]),
         _fmt_delta(r), r["verdict"]]
        for r in diff["metrics"]
    ]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = [
        "gcgauge diff — baseline: %s   current: %s"
        % (diff["baseline"], diff["current"]),
        "threshold: %.1f%%" % diff["threshold_pct"],
        "",
    ]
    for row in [headers] + rows:
        lines.append(
            row[0].ljust(widths[0])
            + "  "
            + "  ".join(row[i].rjust(widths[i]) for i in range(1, 5))
        )
    lines.append("")
    if diff["regressions"]:
        lines.append(
            "%d regression(s) beyond the %.1f%% threshold"
            % (diff["regressions"], diff["threshold_pct"])
        )
    else:
        lines.append("no regressions beyond the %.1f%% threshold" % diff["threshold_pct"])
    if diff["improvements"]:
        lines.append("%d improvement(s)" % diff["improvements"])
    return "\n".join(lines) + "\n"
