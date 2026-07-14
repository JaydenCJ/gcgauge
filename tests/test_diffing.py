"""Cross-run diffing: deltas, thresholds, and verdict classification."""

from gcgauge import diff_reports
from gcgauge.diffing import render_diff_text


def metric(diff, id_):
    matches = [m for m in diff["metrics"] if m["id"] == id_]
    assert len(matches) == 1
    return matches[0]


def test_identical_reports_have_no_regressions(steady_report):
    diff = diff_reports(steady_report, steady_report)
    assert diff["regressions"] == 0
    assert diff["improvements"] == 0
    assert all(m["verdict"] in ("ok", "info") for m in diff["metrics"])


def test_leak_run_regresses_against_steady(steady_report, leaky_report):
    import json

    diff = diff_reports(steady_report, leaky_report)
    assert diff["regressions"] >= 3
    assert metric(diff, "p99_ms")["verdict"] == "regression"
    assert metric(diff, "leak_verdict")["verdict"] == "regression"
    assert metric(diff, "leak_verdict")["delta_text"] == "escalated"
    assert json.loads(json.dumps(diff, sort_keys=True)) == diff


def test_improvement_direction(steady_report, leaky_report):
    diff = diff_reports(leaky_report, steady_report)
    assert diff["regressions"] == 0
    assert diff["improvements"] >= 3
    assert metric(diff, "leak_verdict")["delta_text"] == "reduced"


def test_delta_percentage_math(steady_report, leaky_report):
    row = metric(diff_reports(steady_report, leaky_report), "p50_ms")
    expected = round(
        (leaky_report["pauses"]["all"]["p50_ms"]
         - steady_report["pauses"]["all"]["p50_ms"])
        / steady_report["pauses"]["all"]["p50_ms"] * 100.0,
        1,
    )
    assert row["delta_pct"] == expected


def test_threshold_gates_finite_deltas_only(steady_report, leaky_report):
    # With an absurdly high threshold no finite delta can regress. Two cases
    # are threshold-independent by design: the leak-verdict escalation, and
    # the zero-to-nonzero "new" case where no percentage exists.
    diff = diff_reports(steady_report, leaky_report, threshold_pct=1e9)
    assert diff["threshold_pct"] == 1e9
    finite = [m for m in diff["metrics"]
              if m["id"] != "leak_verdict" and m["delta_pct"] is not None]
    assert finite  # the guard below must actually cover metrics
    assert all(m["verdict"] in ("ok", "info", "n/a") for m in finite)
    assert metric(diff, "leak_verdict")["verdict"] == "regression"
    assert metric(diff, "full_gc_per_min")["verdict"] == "regression"


def test_zero_baseline_semantics(steady_report, leaky_report):
    # steady has 0 full GC/min, leaky has >0: no finite percentage exists,
    # but going from never-full-GC to full-GCing is exactly what a gate is
    # for. Zero to zero, by contrast, is a plain 0% "ok".
    row = metric(diff_reports(steady_report, leaky_report), "full_gc_per_min")
    assert row["baseline"] == 0.0
    assert row["current"] > 0
    assert row["delta_pct"] is None
    assert row["verdict"] == "regression"

    same = metric(diff_reports(steady_report, steady_report), "full_gc_per_min")
    assert same["delta_pct"] == 0.0
    assert same["verdict"] == "ok"


def test_alloc_rate_is_informational_and_absent_metrics_are_na(
    steady_report, leaky_report
):
    import copy

    row = metric(diff_reports(steady_report, leaky_report), "alloc_mb_s")
    assert row["verdict"] == "info"

    stripped = copy.deepcopy(steady_report)
    stripped["allocation_rate_mb_s"] = None
    row = metric(diff_reports(steady_report, stripped), "alloc_mb_s")
    assert row["verdict"] == "n/a"


def test_render_diff_text_regression_and_clean(steady_report, leaky_report):
    text = render_diff_text(diff_reports(steady_report, leaky_report))
    assert "p99 pause (ms)" in text
    assert "escalated" in text
    assert "regression(s) beyond the 10.0% threshold" in text

    clean = render_diff_text(diff_reports(steady_report, steady_report))
    assert "no regressions beyond the 10.0% threshold" in clean
