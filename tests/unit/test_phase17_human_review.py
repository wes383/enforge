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
            "citations": [
                {
                    "quoted_span": (
                        "Audited firms improved compliance after enforcement."
                    ),
                    "source_title": "Journal of Tests",
                }
            ],
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


def test_the_review_template_shows_every_bundled_citation():
    rows = build_review_template(
        [
            _sample(
                citations=[
                    {"quoted_span": "First.", "source_title": "Source A"},
                    {"quoted_span": "Second.", "source_title": "Source B"},
                ]
            )
        ]
    )

    assert rows[0]["quoted_span"] == "Audited firms improved compliance after enforcement."
    assert rows[0]["citations"] == [
        {"quoted_span": "First.", "source_title": "Source A"},
        {"quoted_span": "Second.", "source_title": "Source B"},
    ]


def test_unknown_or_empty_labels_do_not_count_as_reviews():
    sample = [_sample()]
    reviewed = [
        {"claim_id": "claim-1", "reviewer_label": "maybe"},
        {"claim_id": "claim-1", "reviewer_label": ""},
    ]

    comparison = compare_human_review(sample, reviewed)

    assert comparison["reviewed_claims"] == 0
    assert comparison["agreement_rate"] is None


def test_unsupported_agrees_with_contradicted_and_unverifiable():
    sample = [
        {"claim_id": "a", "model_status": "CONTRADICTED"},
        {"claim_id": "b", "model_status": "UNVERIFIABLE"},
        {"claim_id": "c", "model_status": "SUPPORTED"},
    ]
    reviewed = [
        {"claim_id": "a", "reviewer_label": "unsupported"},
        {"claim_id": "b", "reviewer_label": "UNSUPPORTED"},
        {"claim_id": "c", "reviewer_label": "unsupported"},
    ]

    comparison = compare_human_review(sample, reviewed)

    assert comparison["reviewed_claims"] == 3
    assert comparison["agreements"] == 2
    assert comparison["agreement_rate"] == 0.6667


def test_labels_match_a_new_run_by_case_and_claim_text():
    sample = [
        {
            "case_id": "case-a",
            "claim_id": "new-uuid",
            "claim_text": "Remote work raises individual throughput.",
            "model_status": "SUPPORTED",
        }
    ]
    reviewed = [
        {
            "case_id": "case-a",
            "claim_id": "old-uuid",
            "claim_text": "Remote work raises individual throughput.",
            "reviewer_label": "supported",
        }
    ]

    comparison = compare_human_review(sample, reviewed)

    assert comparison["reviewed_claims"] == 1
    assert comparison["agreements"] == 1


def test_a_different_claim_text_does_not_match_across_runs():
    sample = [
        {
            "case_id": "case-a",
            "claim_id": "new-uuid",
            "claim_text": "Remote work raises individual throughput.",
            "model_status": "SUPPORTED",
        }
    ]
    reviewed = [
        {
            "case_id": "case-a",
            "claim_id": "old-uuid",
            "claim_text": "Remote work lowers trust.",
            "reviewer_label": "supported",
        }
    ]

    comparison = compare_human_review(sample, reviewed)

    assert comparison["reviewed_claims"] == 0


def test_labels_fall_back_to_the_quote_when_the_claim_wording_changes():
    sample = [
        {
            "case_id": "case-a",
            "claim_id": "new-uuid",
            "claim_text": "The material offers a connected claim.",
            "quoted_span": "Remote work raises throughput.",
            "model_status": "PARTIAL",
        }
    ]
    reviewed = [
        {
            "case_id": "case-a",
            "claim_id": "old-uuid",
            "claim_text": "Taken together, the material supports this.",
            "quoted_span": "Remote work raises throughput.",
            "reviewer_label": "partial",
        }
    ]

    comparison = compare_human_review(sample, reviewed)

    assert comparison["reviewed_claims"] == 1
    assert comparison["agreements"] == 1


def test_one_human_label_is_counted_once_when_claims_share_a_quote():
    sample = [
        {
            "case_id": "case-a",
            "claim_id": "new-1",
            "claim_text": "Taken together, the material supports this.",
            "quoted_span": "Shared quote.",
            "model_status": "SUPPORTED",
        },
        {
            "case_id": "case-a",
            "claim_id": "new-2",
            "claim_text": "The material reports a different grouping.",
            "quoted_span": "Shared quote.",
            "model_status": "SUPPORTED",
        },
    ]
    reviewed = [
        {
            "case_id": "case-a",
            "claim_id": "old-1",
            "claim_text": "Unrelated original wording.",
            "quoted_span": "Shared quote.",
            "reviewer_label": "partial",
        }
    ]

    comparison = compare_human_review(sample, reviewed)

    assert comparison["reviewed_claims"] == 1
    assert comparison["agreements"] == 0


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
