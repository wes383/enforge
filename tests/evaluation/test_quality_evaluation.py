from __future__ import annotations

import pytest

from dr_core.evaluation.metrics import compare_human_review
from dr_core.evaluation.runner import EvaluationOptions, run_evaluation

pytestmark = pytest.mark.integration


def test_quality_evaluation_dataset_passes_thresholds():
    report = run_evaluation(
        options=EvaluationOptions(
            keep_data=False,
            include_human_sample=True,
            human_review_limit=10,
        )
    )
    summary = report["summary"]
    assert summary["task_success_rate"] == 1.0, summary
    assert summary["recovery_success_rate"] == 1.0, summary
    assert summary["assertion_pass_rate"] == 1.0, summary
    assert summary["citation_validity_rate"] >= 0.95, summary
    assert summary["question_coverage_rate"] >= 0.8, summary
    assert summary["citation_support_accuracy"] >= 0.3, summary
    assert summary["unsupported_claim_ratio"] <= 0.8, summary
    assert summary["metadata_accuracy_rate"] >= 0.8, summary
    assert len(report["human_review_sample"]) > 0
    for case in report["cases"]:
        assert case["passed"], case


def test_human_review_comparison_counts_agreement():
    sample = [
        {"claim_id": "a", "model_status": "SUPPORTED"},
        {"claim_id": "b", "model_status": "PARTIAL"},
    ]
    reviewed = [
        {"claim_id": "a", "reviewer_label": "supported"},
        {"claim_id": "b", "reviewer_label": "UNSUPPORTED"},
    ]
    result = compare_human_review(sample, reviewed)
    assert result["reviewed_claims"] == 2
    assert result["agreements"] == 1
    assert result["agreement_rate"] == 0.5
