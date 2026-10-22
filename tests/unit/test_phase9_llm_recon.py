from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

from dr_core.config import Settings
from dr_core.db.enums import ProjectLanguage, ResearchDepth
from dr_core.providers.models import LLMResponse, LLMToolCall
from dr_core.research.models import ResearchBrief, SourceRecord
from dr_core.research.nodes import _llm_recon, _recon_node_timeout
from dr_core.research.recon import RECON_TASK, run_recon
from dr_core.research.usage import UsageRecordingLLM


class _ToolLLM:
    name = "fake-model"

    def __init__(self, turns: list[LLMResponse]):
        self.turns = list(turns)
        self.calls: list[dict] = []

    def chat(
        self,
        messages,
        *,
        tools=None,
        max_tokens=None,
        temperature=0.0,
    ) -> LLMResponse:
        self.calls.append(
            {
                "messages": list(messages),
                "tools": tools,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        return self.turns.pop(0)


class _Toolbox:
    max_searches = 4
    max_sources = 6

    def __init__(self, *, with_source: bool = True):
        self.searches = 0
        self.records = [_record()] if with_source else []
        self.executed: list[tuple[str, str]] = []

    @property
    def source_count(self) -> int:
        return len(self.records)

    def execute(self, name: str, arguments: str) -> dict:
        self.executed.append((name, arguments))
        if name == "search_sources":
            self.searches += 1
            if not self.records:
                self.records.append(_record())
            return {
                "results": [
                    {
                        "source_id": str(self.records[0].source_id),
                        "title": self.records[0].title,
                    }
                ]
            }
        return {"text": "Peer review reliability evidence."}


def _record() -> SourceRecord:
    return SourceRecord(
        source_id=uuid4(),
        title="Mock source one",
        url="https://example.org/one",
        provider="mock",
        snippet="Peer review reliability is widely studied.",
    )


def _brief(**overrides) -> ResearchBrief:
    values = {
        "project_id": uuid4(),
        "run_id": uuid4(),
        "title": "Peer review",
        "topic": "How does peer review affect research reliability?",
        "language": ProjectLanguage.EN,
        "research_depth": ResearchDepth.DEEP,
    }
    values.update(overrides)
    return ResearchBrief(**values)


def _search_call(call_id: str = "call_1") -> LLMResponse:
    return LLMResponse(
        text="",
        model="fake-model",
        tool_calls=[
            LLMToolCall(
                id=call_id,
                name="search_sources",
                arguments=json.dumps(
                    {
                        "query": "peer review reliability",
                        "kind": "web",
                        "limit": 2,
                    }
                ),
            )
        ],
    )


def _digest() -> LLMResponse:
    return LLMResponse(
        text=json.dumps(
            {
                "summary": "Reviews debate reviewer bias and error detection.",
                "themes": ["reviewer bias", "error detection"],
                "terminology": ["single-blind review"],
                "controversies": ["whether review improves reliability"],
            }
        ),
        model="fake-model",
    )


def test_recon_searches_then_records_a_verified_digest():
    llm = _ToolLLM([_search_call(), _digest()])
    toolbox = _Toolbox()

    outcome = run_recon(
        _brief(),
        llm,
        toolbox,
        max_rounds=1,
        max_tool_calls=4,
    )

    assert outcome.error is None
    assert outcome.digest is not None
    assert outcome.digest.source_count == 1
    assert outcome.digest.themes == ["reviewer bias", "error detection"]
    assert outcome.llm_calls == 2
    assert outcome.tool_calls == 1
    assert outcome.searches == 1
    assert outcome.sources == toolbox.records

    first, second = llm.calls
    assert first["tools"] is not None
    assert second["tools"] is None
    assert first["messages"][0]["role"] == "system"
    prompt = json.loads(first["messages"][1]["content"])
    assert prompt["task"] == RECON_TASK
    assert prompt["brief"]["topic"].startswith(
        '<UNTRUSTED_SOURCE_DATA label="topic"'
    )

    tool_messages = [
        message for message in second["messages"] if message["role"] == "tool"
    ]
    assert len(tool_messages) == 1
    assert tool_messages[0]["content"].startswith(
        '<UNTRUSTED_SOURCE_DATA label="tool_result"'
    )


def test_recon_requires_a_search_before_accepting_a_digest():
    llm = _ToolLLM([_digest(), _search_call(), _digest()])
    toolbox = _Toolbox(with_source=False)

    outcome = run_recon(
        _brief(),
        llm,
        toolbox,
        max_rounds=2,
        max_tool_calls=4,
    )

    assert outcome.digest is not None
    assert outcome.llm_calls == 3
    assert outcome.searches == 1
    nudge = llm.calls[1]["messages"][-1]
    assert nudge["role"] == "user"
    assert "search_sources" in nudge["content"]


def test_recon_stops_when_the_tool_round_budget_is_spent():
    llm = _ToolLLM([_search_call(), _search_call("call_2")])
    toolbox = _Toolbox()

    outcome = run_recon(
        _brief(),
        llm,
        toolbox,
        max_rounds=1,
        max_tool_calls=1,
    )

    assert outcome.digest is None
    assert outcome.llm_calls == 2
    assert outcome.tool_calls == 1
    assert "tool-round budget" in (outcome.error or "")


def test_recon_rejects_invalid_json_without_searching():
    llm = _ToolLLM([LLMResponse(text="not json", model="fake-model")])
    outcome = run_recon(
        _brief(),
        llm,
        _Toolbox(),
        max_rounds=2,
        max_tool_calls=4,
    )

    assert outcome.digest is None
    assert "invalid reconnaissance JSON" in (outcome.error or "")


def test_recon_digest_is_capped_and_cleaned():
    text = json.dumps(
        {
            "summary": "  " + ("Long summary " * 500) + "  ",
            "themes": ["  reviewer   bias  ", "reviewer bias", ""],
            "terminology": ["x" * 500],
            "controversies": "not a list",
        }
    )
    llm = _ToolLLM([_search_call(), LLMResponse(text=text, model="fake-model")])
    outcome = run_recon(
        _brief(),
        llm,
        _Toolbox(),
        max_rounds=2,
        max_tool_calls=4,
    )

    assert outcome.digest is not None
    assert 3000 < len(outcome.digest.summary) <= 4000
    assert outcome.digest.themes == ["reviewer bias"]
    assert len(outcome.digest.terminology[0]) == 120
    assert outcome.digest.controversies == []


def test_recon_gate_requires_planning_and_a_tool_capable_provider():
    settings = Settings(
        _env_file=None,
        research_llm_planning_enabled=True,
        research_llm_recon_enabled=True,
        research_node_timeout_seconds=90,
        llm_request_timeout_seconds=120,
        llm_max_retries=0,
        research_recon_max_rounds=2,
    )
    tool_llm = _ToolLLM([])
    ctx = SimpleNamespace(
        settings=settings,
        registry=SimpleNamespace(llm=tool_llm),
        record_usage=lambda response: None,
    )
    researcher = _llm_recon(ctx)
    assert isinstance(researcher, UsageRecordingLLM)
    assert _recon_node_timeout(ctx) == 90 + 3 * 120

    without_planning = SimpleNamespace(
        settings=settings.model_copy(
            update={"research_llm_planning_enabled": False}
        ),
        registry=SimpleNamespace(llm=tool_llm),
    )
    assert _llm_recon(without_planning) is None
    assert _recon_node_timeout(without_planning) == 90

    mock = SimpleNamespace(
        settings=settings,
        registry=SimpleNamespace(llm=SimpleNamespace(name="mock-llm")),
    )
    assert _llm_recon(mock) is None

    no_tools = SimpleNamespace(
        settings=settings,
        registry=SimpleNamespace(
            llm=SimpleNamespace(name="fake-model", generate=lambda *a, **k: None)
        ),
    )
    assert _llm_recon(no_tools) is None


def test_recon_is_off_by_default():
    settings = Settings(_env_file=None)
    assert settings.research_llm_recon_enabled is False
    assert settings.llm_workflow_enabled is False
