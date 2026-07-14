"""Renderers: text, markdown, and JSON projections of the same report dict."""

import json

from gcgauge.render import render_json, render_markdown, render_text


def test_text_report_headline(steady_report):
    text = render_text(steady_report)
    assert text.startswith("gcgauge report — steady")
    assert "collector: G1" in text
    assert "Pause percentiles (ms)" in text
    assert text.endswith("\n")


def test_text_report_percentile_table_rows(steady_report, leaky_report):
    import re

    text = render_text(leaky_report)
    # A percentile table row is "<class> <count> ..." — the heap section's
    # "full GC: 3 total" line must not be miscounted as one.
    rows = [l for l in text.splitlines() if re.match(r"^  (young|full)\s+\d", l)]
    assert len(rows) == 2
    # The "all" summary row appears only when classes differ; a single-class
    # run would just repeat its one class.
    assert "\n  all " in text
    assert "\n  all " not in render_text(steady_report)


def test_text_report_leak_section(leaky_report):
    text = render_text(leaky_report)
    assert "verdict: LIKELY" in text
    assert "[critical]" in text
    assert "rising post-GC floor" in text


def test_text_and_markdown_reports_carry_warnings():
    from gcgauge import analyze, parse_lines

    report = analyze(parse_lines(
        ["GC(0) Pause Young (Normal) (G1 Evacuation Pause) 24M->8M(64M) 3.3ms",
         "GC(1) Pause Young (Normal) (G1 Evacuation Pause) 24M->8M(64M) 3.3ms"]
    ))
    # A synthetic time axis is a caveat the reader must see in every
    # human-oriented format, not just the default text one.
    assert "note: log has no timestamps" in render_text(report)
    assert "> note: log has no timestamps" in render_markdown(report)


def test_json_round_trips_sorted_and_deterministic(leaky_report):
    rendered = render_json(leaky_report)
    assert json.loads(rendered) == leaky_report
    assert rendered == json.dumps(leaky_report, sort_keys=True, indent=2) + "\n"
    assert rendered == render_json(leaky_report)
    # Spot-check: "collector" must appear before "format" in sorted output.
    assert rendered.index('"collector"') < rendered.index('"format"')


def test_markdown_report_tables(leaky_report):
    md = render_markdown(leaky_report)
    assert md.startswith("# gcgauge report")
    assert "| class | count | p50 |" in md.replace("  ", " ")
    assert "| Indicator | Status | Evidence |" in md
    assert "**likely**" in md
    for ind in leaky_report["leak"]["indicators"]:
        assert "| %s |" % ind["label"] in md


def test_renderers_never_mutate_the_report(leaky_report):
    import copy

    snapshot = copy.deepcopy(leaky_report)
    render_text(leaky_report)
    render_markdown(leaky_report)
    render_json(leaky_report)
    assert leaky_report == snapshot
