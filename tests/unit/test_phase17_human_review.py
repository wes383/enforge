from __future__ import annotations

from dr_core.evaluation.metrics import (
    compare_human_review,
    human_review_gate,
)
from dr_core.evaluation.runner import build_review_template


def _sample(**overrides):
    entry = {
        "case_id": "case-a",
        "claim_id": "claim-1",
        "claim_text": "Enforcement improves compliance among audited firms.",
        "quoted_span": "Audited firms improved compliance after enforcement.",
        "source_title": "Journal of Tests",
        "model_status": "SUPPORTED",
        "evidence_id": "evidence-1",
        "relation": "supports",
        "reviewer_label": None,
        "reviewer_notes": None,
    }
    entry.update(overrides)
    return entry


def test_the_review_template_hides_the_model_verdict():
    rows = build_review_template([_sample()])

    assert rows == [
        {
            "case_id": "case-a",
            "claim_id": "claim-1",
            "claim_text": "Enforcement improves compliance among audited firms.",
            "quoted_span": (
                "Audited firms improved compliance after enforcement."
            ),
            "source_title": "Journal of Tests",
            "reviewer_label": "",
            "reviewer_notes": "",
        }
    ]
    assert "model_status" not in rows[0]
    assert "relation" not in rows[0]


def test_the_review_template_keeps_the_quote_a_reviewer_needs():
    rows = build_review_template(
        [_sample(claim_id="claim-2", quoted_span="The only quoted sentence.")]
    )

    assert rows[0]["quoted_span"] == "The only quoted sentence."
    assert rows[0]["source_title"] == "Journal of Tests"


def test_unknown_or_empty_labels_do_not_count_as_reviews():
    sample = [_sample()]
    reviewed = [
        {"claim_id": "claim-1", "reviewer_label": "maybe"},
        {"claim_id": "claim-1", "reviewer_label": ""},
    ]

    comparison = compare_human_review(sample, reviewed)

    assert comparison["reviewed_claims"] == 0
    assert comparison["agreement_rate"] is None


def test_the_gate_stays_open_below_thirty_labels():
    comparison = {
        "sampled_claims": 40,
        "reviewed_claims": 29,
        "agreement_rate": 0.1,
    }

    gate = human_review_gate(comparison)

    assert gate["enforced"] is False
    assert gate["passed"] is True


def test_the_gate_enforces_eighty_percent_at_thirty_labels():
    comparison = {
        "sampled_claims": 40,
        "reviewed_claims": 30,
        "agreement_rate": 0.8,
    }

    gate = human_review_gate(comparison)

    assert gate["enforced"] is True
    assert gate["passed"] is True


def test_the_gate_fails_below_eighty_percent_once_enforced():
    comparison = {
        "sampled_claims": 40,
        "reviewed_claims": 30,
        "agreement_rate": 0.79,
    }

    gate = human_review_gate(comparison)

    assert gate["enforced"] is True
    assert gate["passed"] is False


def test_an_unlabelled_review_cannot_pass_the_enforced_gate():
    comparison = {
        "sampled_claims": 40,
        "reviewed_claims": 30,
        "agreement_rate": None,
    }

    gate = human_review_gate(comparison)

    assert gate["enforced"] is True
    assert gate["passed"] is False
