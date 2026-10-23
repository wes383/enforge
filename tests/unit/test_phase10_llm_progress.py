from __future__ import annotations

import pytest

from dr_core.db.enums import RunEventType, RunStatus
from dr_core.providers.models import LLMResponse
from dr_core.research.usage import ProgressReportingLLM


class _FakeProvider:
    name = "fake-llm"

    def __init__(
        self,
        *,
        response: LLMResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self._response = response or LLMResponse(
            text="ok",
            model="fake",
            input_tokens=10,
            output_tokens=5,
        )
        self._error = error
        self.calls = 0

    def generate(self, prompt, **kwargs) -> LLMResponse:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._response

    def chat(self, messages, **kwargs) -> LLMResponse:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._response


def _collector(events: list[dict]):
    def emit(
        event_type,
        *,
        message=None,
        payload=None,
        dedupe_key=None,
        stage=None,
    ) -> None:
        events.append(
            {
                "event_type": event_type,
                "message": message,
                "payload": payload or {},
                "dedupe_key": dedupe_key,
                "stage": stage,
            }
        )

    return emit


def _wrapper(events: list[dict], provider: _FakeProvider) -> ProgressReportingLLM:
    return ProgressReportingLLM(
        provider,
        _collector(events),
        stage=RunStatus.WRITING,
        label="Writing section 1/2",
        key_prefix="llm:section:0",
        payload={"title": "Introduction"},
    )


def test_progress_wrapper_reports_every_model_call():
    events: list[dict] = []
    provider = _FakeProvider()
    llm = _wrapper(events, provider)

    llm.generate("first")
    llm.generate("second")

    assert provider.calls == 2
    assert [event["dedupe_key"] for event in events] == [
        "llm:section:0:1:started",
        "llm:section:0:1:completed",
        "llm:section:0:2:started",
        "llm:section:0:2:completed",
    ]
    assert all(event["event_type"] is RunEventType.PROGRESS for event in events)
    assert all(event["stage"] is RunStatus.WRITING for event in events)
    assert events[0]["payload"]["title"] == "Introduction"
    completed = events[-1]["payload"]
    assert completed["title"] == "Introduction"
    assert completed["model_call"] == 2
    assert completed["operation"] == "generate"
    assert completed["input_tokens"] == 10
    assert completed["output_tokens"] == 5
    assert completed["duration_ms"] >= 0
    assert "model call 2 completed" in events[-1]["message"]


def test_progress_wrapper_reports_failures_and_reraises():
    events: list[dict] = []
    llm = _wrapper(events, _FakeProvider(error=RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        llm.chat([])

    assert [event["dedupe_key"] for event in events] == [
        "llm:section:0:1:started",
        "llm:section:0:1:failed",
    ]
    assert events[-1]["payload"]["error"] == "RuntimeError: boom"
    assert "model call 1 failed" in events[-1]["message"]


def test_progress_event_failure_does_not_hide_the_model_result():
    def broken_emit(*args, **kwargs) -> None:
        raise RuntimeError("events are down")

    provider = _FakeProvider()
    llm = ProgressReportingLLM(
        provider,
        broken_emit,
        stage=RunStatus.PLANNING,
        label="Planning outline",
        key_prefix="llm:planning",
    )

    response = llm.generate("prompt")

    assert response.text == "ok"
    assert provider.calls == 1
