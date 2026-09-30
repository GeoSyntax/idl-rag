from types import SimpleNamespace

import pytest

from app.services.python_runner import PythonRunner


def test_wilson_intervals_are_bounded_and_reproducible() -> None:
    intervals = PythonRunner._binary_confidence_intervals(3, 1, 1, 1)

    assert intervals["method"] == "wilson_95_unweighted"
    assert intervals["confidence_level"] == 0.95
    assert "未加权" in intervals["assumption"]
    for metric in ("overall_accuracy", "precision", "recall", "iou"):
        interval = intervals[metric]
        assert interval is not None
        assert 0.0 <= interval["lower"] <= interval["upper"] <= 1.0
        assert interval["successes"] <= interval["trials"]


def test_wilson_interval_is_null_when_a_metric_has_no_trials() -> None:
    intervals = PythonRunner._binary_confidence_intervals(0, 4, 0, 0)

    assert intervals["overall_accuracy"]["trials"] == 4
    assert intervals["precision"] is None
    assert intervals["recall"] is None
    assert intervals["iou"] is None


def test_weighted_point_metrics_record_weight_coverage_without_fake_ci() -> None:
    samples = [
        (SimpleNamespace(id=1, label=1), 1),
        (SimpleNamespace(id=2, label=0), 1),
        (SimpleNamespace(id=3, label=1), 0),
    ]
    metrics = PythonRunner._point_sample_metrics(samples, weight_by_id={1: 2.0, 2: 1.0, 3: 3.0})

    assert metrics["sample_count"] == 3
    assert metrics["weight_sum"] == 6.0
    assert metrics["effective_sample_size"] == pytest.approx(2.5714285714)
    assert metrics["metrics"]["overall_accuracy"] == pytest.approx(1 / 3)
    assert metrics["confidence_intervals"]["method"] == "not_reported_for_weighted_samples"


def test_weighting_rejects_missing_or_non_positive_metadata_weights() -> None:
    samples = [
        (SimpleNamespace(id=1, metadata_json={"sampling_weight": 2.0}), 1),
        (SimpleNamespace(id=2, metadata_json={}), 0),
    ]

    with pytest.raises(ValueError, match="必须是正的有限数值"):
        PythonRunner._resolve_point_sample_weighting(
            {"weighting": {"enabled": True, "metadata_key": "sampling_weight"}},
            samples,
        )


def test_stratified_area_adjusted_metrics_use_declared_stratum_areas() -> None:
    samples = [
        (
            SimpleNamespace(id=1, label=1, metadata_json={"sampling_stratum": "A", "stratum_area": 60.0}),
            1,
        ),
        (
            SimpleNamespace(id=2, label=1, metadata_json={"sampling_stratum": "A", "stratum_area": 60.0}),
            0,
        ),
        (
            SimpleNamespace(id=3, label=0, metadata_json={"sampling_stratum": "B", "stratum_area": 40.0}),
            0,
        ),
        (
            SimpleNamespace(id=4, label=0, metadata_json={"sampling_stratum": "B", "stratum_area": 40.0}),
            1,
        ),
    ]
    stratum_by_sample, area_by_stratum, selection = PythonRunner._resolve_area_adjustment(
        {
            "area_adjustment": {
                "enabled": True,
                "stratum_metadata_key": "sampling_stratum",
                "area_metadata_key": "stratum_area",
                "area_unit": "ha",
                "expected_strata": ["A", "B"],
                "minimum_strata": 2,
            }
        },
        samples,
    )

    assert selection["enabled"] is True
    assert selection["total_area"] == 100.0
    metrics = PythonRunner._point_sample_area_adjusted_metrics(
        samples,
        stratum_by_sample,
        area_by_stratum,
        selection["area_unit"],
    )
    assert metrics["method"] == "stratified_area_adjusted"
    assert metrics["confusion_matrix"]["true_positive"] == pytest.approx(30.0)
    assert metrics["confusion_matrix"]["true_negative"] == pytest.approx(20.0)
    assert metrics["metrics"]["overall_accuracy"] == pytest.approx(0.5)
    assert metrics["metrics"]["iou"] == pytest.approx(0.375)
    assert metrics["reference_positive_area"] == pytest.approx(60.0)
    assert metrics["predicted_positive_area"] == pytest.approx(50.0)
    assert metrics["absolute_area_error"] == pytest.approx(10.0)
    assert metrics["confidence_intervals"]["method"] == "not_reported_for_design_based_area_adjustment"


def test_area_adjustment_rejects_missing_expected_stratum_and_invalid_area() -> None:
    samples = [
        (SimpleNamespace(id=1, label=1, metadata_json={"sampling_stratum": "A", "stratum_area": 0}), 1),
    ]
    with pytest.raises(ValueError, match="正的有限面积数值"):
        PythonRunner._resolve_area_adjustment(
            {"area_adjustment": {"enabled": True}},
            samples,
        )

    samples[0][0].metadata_json["stratum_area"] = 10.0
    with pytest.raises(ValueError, match="缺少协议声明的分层样本"):
        PythonRunner._resolve_area_adjustment(
            {"area_adjustment": {"enabled": True, "expected_strata": ["A", "B"]}},
            samples,
        )


def test_area_adjustment_does_not_silently_combine_with_generic_weights() -> None:
    samples = [
        (SimpleNamespace(id=1, label=1, metadata_json={"sampling_stratum": "A", "stratum_area": 10.0}), 1),
    ]
    with pytest.raises(ValueError, match="同时启用"):
        PythonRunner._resolve_area_adjustment(
            {
                "weighting": {"enabled": True},
                "area_adjustment": {"enabled": True},
            },
            samples,
        )
