from __future__ import annotations

import json

import pytest

from dr_core.evaluation.judge import (
    JudgeOptions,
    _generation_summary,
    aggregate_scores,
    judge_article,
    judge_settings,
    run_judge,
)
from dr_core.providers.models import LLMResponse


class _RecordingLLM:
    name = "recording-judge"

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict] = []

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        self.calls.append(
            {
                "prompt": prompt,
                "system": system,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        return LLMResponse(
            text=self.text,
            model="fake-model",
            input_tokens=11,
            output_tokens=7,
        )


def _answer(**overrides) -> str:
    payload = {
        "coherence": 4,
        "specificity_discipline": 3,
        "calibrated_tone": 5,
        "notes": "The middle section repeats one point.",
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_the_judge_fixes_temperature_and_records_the_model():
    llm = _RecordingLLM(_answer())

    score = judge_article(llm, "A finished article.", case_id="case-a")

    assert score.coherence == 4
    assert score.specificity_discipline == 3
    assert score.calibrated_tone == 5
    assert score.model == "fake-model"
    assert score.error is None
    assert score.input_tokens == 11
    assert score.output_tokens == 7
    call = llm.calls[0]
    assert call["temperature"] == 0.0
    assert "Do not verify citations" in call["system"]
    assert "case-a" in call["prompt"]


def test_the_judge_parses_a_fenced_json_answer():
    llm = _RecordingLLM("```json\n" + _answer(coherence=2) + "\n```")

    score = judge_article(llm, "A finished article.", case_id="case-a")

    assert score.coherence == 2
    assert score.error is None


def test_scores_are_clamped_to_the_one_to_five_scale():
    llm = _RecordingLLM(
        _answer(coherence=9, specificity_discipline=0, calibrated_tone=3.6)
    )

    score = judge_article(llm, "A finished article.", case_id="case-a")

    assert score.coherence == 5
    assert score.specificity_discipline == 1
    assert score.calibrated_tone == 4


def test_an_unusable_answer_is_an_error_not_a_crash():
    llm = _RecordingLLM("The article is quite good, I think.")

    score = judge_article(llm, "A finished article.", case_id="case-a")

    assert score.error
    assert score.coherence is None


def test_an_empty_article_is_not_sent_to_the_model():
    llm = _RecordingLLM(_answer())

    score = judge_article(llm, "   ", case_id="case-a")

    assert score.error == "empty article"
    assert llm.calls == []


def test_aggregate_scores_ignores_missing_values():
    from dr_core.evaluation.judge import JudgeScore

    scores = [
        JudgeScore("a", 4, 2, 5, "", "m", 10, 5),
        JudgeScore("b", None, 4, None, "", "m", 20, 6, error="failed"),
    ]

    summary = aggregate_scores(scores)

    assert summary["cases"] == 2
    assert summary["judged"] == 1
    assert summary["errors"] == 1
    assert summary["coherence"] == 4.0
    assert summary["specificity_discipline"] == 3.0
    assert summary["calibrated_tone"] == 5.0
    assert summary["input_tokens"] == 30
    assert summary["output_tokens"] == 11


def test_existing_articles_are_scored_without_a_database(tmp_path):
    (tmp_path / "case-a.md").write_text("First article.", encoding="utf-8")
    (tmp_path / "case-b.md").write_text("Second article.", encoding="utf-8")
    llm = _RecordingLLM(_answer())

    report = run_judge(
        JudgeOptions(provider="fake", articles_dir=str(tmp_path)),
        llm=llm,
    )

    assert report["summary"]["cases"] == 2
    assert report["summary"]["judged"] == 2
    assert report["model"] == "recording-judge"
    assert "deterministic audit" in report["note"]
    assert len(llm.calls) == 2


def test_the_fake_provider_refuses_to_generate_articles():
    with pytest.raises(ValueError):
        run_judge(JudgeOptions(provider="fake"))


def test_judge_settings_give_a_slow_polish_a_longer_attempt_and_retry():
    settings = judge_settings()

    assert settings.llm_request_timeout_seconds == 360
    assert settings.llm_max_retries == 1


def test_generation_summary_attributes_polish_outcomes():
    report = {
        "summary": {
            "cases": 3,
            "total_evidence": 10,
            "total_claims": 20,
            "total_input_tokens": 100,
            "total_output_tokens": 200,
        },
        "cases": [
            {"status": "completed", "polish": {"status": "applied"}},
            {"status": "completed", "polish": {"status": "model_error"}},
            {"status": "failed"},
        ],
    }

    summary = _generation_summary(report)

    assert summary["polish"] == {"applied": 1, "failed": 1, "other": 1}
    assert summary["completed"] == 2
