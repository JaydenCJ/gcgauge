"""Statistics primitives: nearest-rank percentiles and least-squares fits."""

import pytest

from gcgauge.stats import linear_regression, percentile, summarize_pauses


def test_percentile_nearest_rank_returns_observed_values():
    values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    assert percentile(values, 50) == 50.0
    assert percentile(values, 90) == 90.0
    assert percentile(values, 99) == 100.0
    assert percentile(values, 100) == 100.0
    # A single sample is every percentile of itself.
    assert percentile([42.0], 1) == 42.0
    assert percentile([42.0], 99) == 42.0
    # Input order is irrelevant — the function sorts internally.
    assert percentile([3.0, 1.0, 2.0], 50) == percentile([1.0, 2.0, 3.0], 50)
    # And it never interpolates: 3 samples, p50 -> rank ceil(1.5) = 2 -> the
    # middle sample. An interpolating implementation would invent 15.0, a
    # pause that never happened; nearest-rank must return 20.0.
    assert percentile([10.0, 20.0, 400.0], 50) == 20.0


def test_percentile_invalid_inputs_raise():
    with pytest.raises(ValueError):
        percentile([], 50)
    with pytest.raises(ValueError):
        percentile([1.0], 0)
    with pytest.raises(ValueError):
        percentile([1.0], 101)


def test_linear_regression_exact_line():
    slope, intercept, r2 = linear_regression([0.0, 1.0, 2.0], [1.0, 3.0, 5.0])
    assert slope == pytest.approx(2.0)
    assert intercept == pytest.approx(1.0)
    assert r2 == pytest.approx(1.0)


def test_linear_regression_flat_series_has_no_trend():
    slope, intercept, r2 = linear_regression([0.0, 1.0, 2.0], [7.0, 7.0, 7.0])
    assert slope == 0.0
    assert intercept == 7.0
    assert r2 == 0.0


def test_linear_regression_noisy_data_has_low_r2():
    _slope, _intercept, r2 = linear_regression(
        [0.0, 1.0, 2.0, 3.0], [5.0, 1.0, 6.0, 2.0]
    )
    assert r2 < 0.3


def test_linear_regression_degenerate_inputs_are_defined():
    assert linear_regression([], []) == (0.0, 0.0, 0.0)
    assert linear_regression([1.0], [5.0]) == (0.0, 5.0, 0.0)
    # Zero variance in x (all samples at the same instant).
    slope, _intercept, r2 = linear_regression([2.0, 2.0], [1.0, 9.0])
    assert slope == 0.0 and r2 == 0.0
    # Mismatched lengths are a caller bug and must fail loudly.
    with pytest.raises(ValueError):
        linear_regression([1.0, 2.0], [1.0])


def test_summarize_pauses_block_and_empty_case():
    block = summarize_pauses([2.0, 4.0, 6.0, 8.0])
    assert block["count"] == 4
    assert block["mean_ms"] == 5.0
    assert block["p50_ms"] == 4.0
    assert block["max_ms"] == 8.0
    assert block["total_ms"] == 20.0
    assert summarize_pauses([]) is None
