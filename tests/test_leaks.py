"""Leak indicators: each heuristic triggered and not triggered, plus scoring."""

from gcgauge import analyze, parse_lines
from tests.conftest import unified_full, unified_young


def indicator(report, id_):
    matches = [i for i in report["leak"]["indicators"] if i["id"] == id_]
    assert len(matches) == 1, "indicator %s missing" % id_
    return matches[0]


def test_steady_run_verdict_none_with_auditable_evidence(steady_report):
    assert steady_report["leak"]["verdict"] == "none"
    assert steady_report["leak"]["score"] == 0
    # A clean bill of health must be auditable, not silent: every indicator
    # still reports what it checked and found.
    for ind in steady_report["leak"]["indicators"]:
        assert not ind["triggered"]
        assert ind["severity"] == "ok"
        assert ind["detail"], ind["id"]


def test_leaky_run_verdict_likely_and_score_arithmetic(leaky_report):
    leak = leaky_report["leak"]
    assert leak["verdict"] == "likely"
    assert leak["score"] >= 4
    assert leak["score"] == sum(
        i["weight"] for i in leak["indicators"] if i["triggered"]
    )
    assert leak["max_score"] == sum(i["weight"] for i in leak["indicators"])


def test_rising_floor_triggered(leaky_report):
    ind = indicator(leaky_report, "rising_floor")
    assert ind["triggered"]
    assert ind["severity"] == "critical"
    assert "MB/min" in ind["detail"]


def test_rising_floor_needs_enough_samples():
    report = analyze(parse_lines([unified_young(1.0, 0, 96, 32),
                                  unified_young(3.0, 1, 96, 40)]))
    ind = indicator(report, "rising_floor")
    assert not ind["triggered"]
    assert "not enough" in ind["detail"]


def test_rising_floor_rejects_noise_and_immaterial_rises():
    # Alternating floor: slope may be positive but r² is far below the gate.
    after = [32, 200, 40, 190, 35, 210, 45, 205]
    noisy = [unified_young(1.0 + i * 10.0, i, a + 40, a)
             for i, a in enumerate(after)]
    assert not indicator(analyze(parse_lines(noisy)), "rising_floor")["triggered"]

    # +7M over a 256M heap fits perfectly (r²≈1) but is 2.7% — immaterial.
    tiny = [unified_young(1.0 + i * 10.0, i, 96 + i, 32 + i) for i in range(8)]
    ind = indicator(analyze(parse_lines(tiny)), "rising_floor")
    assert not ind["triggered"]
    assert "below the" in ind["detail"]


def test_full_gc_growth_triggered(leaky_report):
    assert indicator(leaky_report, "full_gc_growth")["triggered"]


def test_full_gc_growth_not_triggered_by_even_spread():
    lines = []
    for i in range(6):
        t = 10.0 + i * 20.0
        lines.append(unified_young(t, i * 2, 200, 100))
        lines.append(unified_full(t + 5.0, i * 2 + 1, 220, 100))
    ind = indicator(analyze(parse_lines(lines)), "full_gc_growth")
    assert not ind["triggered"]


def test_low_reclaim_judges_recent_fulls_only():
    # Early fulls reclaim well, late ones do not — the late ones must decide.
    lines = [unified_young(1.0, 0, 96, 32)]
    lines.append(unified_full(10.0, 1, 240, 60))   # reclaims 70% of heap
    lines.append(unified_full(50.0, 2, 245, 235))  # reclaims 3.9%
    lines.append(unified_full(90.0, 3, 250, 242))  # reclaims 3.1%
    ind = indicator(analyze(parse_lines(lines)), "low_full_reclaim")
    assert ind["triggered"]
    assert "%" in ind["detail"]

    # And a full GC that genuinely frees 70% of the heap is healthy.
    healthy = [unified_young(1.0, 0, 96, 32), unified_full(10.0, 1, 240, 60)]
    ind = indicator(analyze(parse_lines(healthy)), "low_full_reclaim")
    assert not ind["triggered"]


def test_evacuation_failure_triggered(leaky_report):
    ind = indicator(leaky_report, "evacuation_failure")
    assert ind["triggered"]
    assert "3 event(s)" in ind["detail"]


def test_high_occupancy_triggered():
    # Flat but pinned at ~92% of heap: not leak-shaped on its own (flat
    # floor), yet still worth a warning — hence weight 1 and "possible".
    lines = [unified_young(1.0 + i * 10.0, i, 250, 236) for i in range(8)]
    report = analyze(parse_lines(lines))
    assert indicator(report, "high_occupancy")["triggered"]
    assert report["leak"]["verdict"] == "possible"


def test_oom_forces_likely_verdict():
    lines = [unified_young(1.0 + i * 10.0, i, 96, 32) for i in range(4)]
    lines.append("java.lang.OutOfMemoryError: Java heap space")
    report = analyze(parse_lines(lines))
    assert indicator(report, "oom")["triggered"]
    assert report["leak"]["verdict"] == "likely"
