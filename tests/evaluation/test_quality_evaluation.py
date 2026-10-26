from __future__ import annotations

import json

import pytest

from dr_core.evaluation.metrics import compare_human_review
from dr_core.evaluation.runner import (
    EvaluationOptions,
    evaluation_settings,
    run_evaluation,
)
from dr_core.providers.mocks import MockLLMProvider

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
    assert summary["section_coverage_rate"] >= 0.8, summary
    assert summary["citation_support_accuracy"] >= 0.3, summary
    assert summary["unsupported_claim_ratio"] <= 0.8, summary
    assert summary["metadata_accuracy_rate"] >= 0.8, summary
    # The regression floors for the quality metrics. These are close to the
    # measured values of this dataset, so a drop means a real change in
    # behaviour; they are not quality targets.
    assert summary["evidence_target_ratio"] >= 0.50, summary
    assert summary["full_text_evidence_ratio"] >= 0.35, summary
    assert summary["snippet_only_cited_ratio"] <= 0.55, summary
    assert summary["citation_span_traceability"] >= 0.98, summary
    assert summary["total_refute_evidence"] >= 45, summary
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


def test_review_template_is_written_without_the_model_verdict(tmp_path):
    report = run_evaluation(
        case_ids=["general_knowledge"],
        options=EvaluationOptions(
            keep_data=False,
            include_human_sample=True,
            human_review_limit=5,
            review_template_dir=str(tmp_path),
        ),
    )

    template_path = tmp_path / "review_template.json"
    rows = json.loads(template_path.read_text(encoding="utf-8"))
    assert rows, report["summary"]
    assert report["review_template"] == str(template_path)
    assert set(rows[0]) == {
        "case_id",
        "claim_id",
        "claim_text",
        "quoted_span",
        "source_title",
        "citations",
        "reviewer_label",
        "reviewer_notes",
    }
    assert all(row["reviewer_label"] == "" for row in rows)
    assert all("model_status" not in row for row in rows)
    assert all(isinstance(row["citations"], list) for row in rows)


class _RecordingLLM:
    name = "recording-mock"

    def __init__(self) -> None:
        self._inner = MockLLMProvider()
        self.calls = 0

    def generate(self, prompt, *, system=None, max_tokens=None, temperature=0.0):
        self.calls += 1
        return self._inner.generate(
            prompt,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )


def test_an_injected_llm_reaches_the_writer_and_exports_an_article(tmp_path):
    llm = _RecordingLLM()
    settings = evaluation_settings().model_copy(
        update={
            "research_llm_writing_enabled": True,
            "research_llm_outline_enabled": True,
            "research_llm_polish_enabled": True,
            "research_max_sources": 6,
            "research_max_evidence": 24,
            "research_node_timeout_seconds": 60,
        }
    )

    report = run_evaluation(
        case_ids=["general_knowledge"],
        options=EvaluationOptions(
            keep_data=False,
            include_human_sample=False,
            export_dir=str(tmp_path),
        ),
        settings=settings,
        llm=llm,
    )

    assert report["cases"][0]["status"] == "completed"
    assert llm.calls > 0
    assert (tmp_path / "general_knowledge.md").exists()
