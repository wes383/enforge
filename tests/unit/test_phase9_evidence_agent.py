from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

from dr_core.db.enums import ArticleType, ProjectLanguage, ResearchDepth
from dr_core.providers.models import LLMResponse, LLMToolCall
from dr_core.research.evidence_agent import EVIDENCE_TASK, run_evidence_agent
from dr_core.research.generation import build_outline_template
from dr_core.research.models import ResearchBrief, ResearchGap, SourceRecord


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
    max_fetches = 2
    max_citation_calls = 2

    def __init__(self):
        self.records = [_record()]
        self.searches = 0
        self.fetches = 0
        self.citation_calls = 0
        self.executed: list[tuple[str, str]] = []

    def execute(self, name: str, arguments: str) -> dict:
        self.executed.append((name, arguments))
        if name == "search_sources":
            self.searches += 1
            return {
                "results": [
                    {
                        "source_id": str(self.records[0].source_id),
                        "title": self.records[0].title,
                    }
                ]
            }
        if name == "fetch_source":
            self.fetches += 1
            return {"text": "Peer review reliability evidence."}
        self.citation_calls += 1
        return {"results": []}


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
        "article_type": ArticleType.EXPLAINER,
    }
    values.update(overrides)
    return ResearchBrief(**values)


def _outline():
    return build_outline_template(_brief(), version=2)


def _search_call(section_id, call_id: str = "call_1") -> LLMResponse:
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
                        "section_id": str(section_id),
                    }
                ),
            )
        ],
    )


def _final(section_id, *, status: str = "sufficient", note: str = "Enough material."):
    return LLMResponse(
        text=json.dumps(
            {
                "summary": "Reviews debate reviewer bias and error detection.",
                "section_notes": [
                    {
                        "section_id": str(section_id),
                        "status": status,
                        "note": note,
                    }
                ],
            }
        ),
        model="fake-model",
    )


def test_evidence_agent_searches_within_the_approved_section_then_reports():
    outline = _outline()
    body = next(section for section in outline.sections if section.role.value == "body")
    llm = _ToolLLM([_search_call(body.section_id), _final(body.section_id)])
    toolbox = _Toolbox()
    observed: list[tuple[str, dict]] = []

    outcome = run_evidence_agent(
        _brief(),
        outline,
        [ResearchGap(section_id=body.section_id, section_title=body.title, reason="No source yet")],
        llm,
        toolbox,
        round_index=2,
        max_rounds=1,
        max_tool_calls=4,
        on_tool=lambda name, result: observed.append((name, result)),
    )

    assert outcome.completed is True
    assert outcome.error is None
    assert outcome.summary.startswith("Reviews debate")
    assert outcome.section_notes == [
        {
            "section_id": str(body.section_id),
            "status": "sufficient",
            "note": "Enough material.",
        }
    ]
    assert outcome.llm_calls == 2
    assert outcome.tool_calls == 1
    assert outcome.searches == 1
    assert outcome.sources == toolbox.records
    assert observed[0][0] == "search_sources"

    first, second = llm.calls
    assert first["tools"] is not None
    assert second["tools"] is None
    prompt = json.loads(first["messages"][1]["content"])
    assert prompt["task"] == EVIDENCE_TASK
    assert prompt["round"] == 2
    assert prompt["approved_outline"]["sections"][0]["section_id"] == str(
        outline.sections[0].section_id
    )
    assert any(
        section["section_id"] == str(body.section_id)
        for section in prompt["approved_outline"]["sections"]
    )
    assert prompt["coverage_gaps"][0]["section_id"] == str(body.section_id)
    assert any("immutable" in rule for rule in prompt["rules"])
    assert any("section_id" in rule for rule in prompt["rules"])

    tool_messages = [
        message for message in second["messages"] if message["role"] == "tool"
    ]
    assert tool_messages[0]["content"].startswith(
        '<UNTRUSTED_SOURCE_DATA label="tool_result"'
    )


def test_evidence_agent_retries_invalid_json_and_drops_unknown_sections():
    outline = _outline()
    body = next(section for section in outline.sections if section.role.value == "body")
    invalid = LLMResponse(text="not json", model="fake-model")
    final = LLMResponse(
        text=json.dumps(
            {
                "summary": "Coverage is partial after the second pass.",
                "section_notes": [
                    {"section_id": str(uuid4()), "status": "blocked", "note": "Invented."},
                    {
                        "section_id": str(body.section_id),
                        "status": "partial",
                        "note": "  Needs   one more source.  ",
                    },
                    {
                        "section_id": str(body.section_id),
                        "status": "unknown",
                        "note": "Retry status.",
                    },
                ],
            }
        ),
        model="fake-model",
    )
    llm = _ToolLLM([invalid, final])

    outcome = run_evidence_agent(
        _brief(),
        outline,
        [],
        llm,
        _Toolbox(),
        round_index=1,
        max_rounds=2,
        max_tool_calls=2,
    )

    assert outcome.completed is True
    assert outcome.section_notes == [
        {
            "section_id": str(body.section_id),
            "status": "partial",
            "note": "Needs one more source.",
        },
        {
            "section_id": str(body.section_id),
            "status": "partial",
            "note": "Retry status.",
        },
    ]
    assert llm.calls[1]["messages"][-1]["role"] == "user"
    assert "could not be used" in llm.calls[1]["messages"][-1]["content"]


def test_evidence_agent_reports_missing_summary_and_model_failure():
    outline = _outline()
    empty = LLMResponse(
        text=json.dumps({"summary": "", "section_notes": []}),
        model="fake-model",
    )
    outcome = run_evidence_agent(
        _brief(),
        outline,
        [],
        _ToolLLM([empty, empty]),
        _Toolbox(),
        round_index=1,
        max_rounds=1,
        max_tool_calls=2,
    )
    assert outcome.completed is False
    assert outcome.summary == ""
    assert "summary was empty" in (outcome.error or "")

    failed = _ToolLLM([])
    failed.chat = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down"))
    outcome = run_evidence_agent(
        _brief(),
        outline,
        [],
        failed,
        _Toolbox(),
        round_index=1,
        max_rounds=1,
        max_tool_calls=2,
    )
    assert outcome.completed is False
    assert "RuntimeError" in (outcome.error or "")


def test_evidence_agent_rejects_tool_calls_after_the_round_budget():
    outline = _outline()
    body = next(section for section in outline.sections if section.role.value == "body")
    llm = _ToolLLM([_search_call(body.section_id), _search_call(body.section_id, "call_2")])

    outcome = run_evidence_agent(
        _brief(),
        outline,
        [],
        llm,
        _Toolbox(),
        round_index=1,
        max_rounds=1,
        max_tool_calls=4,
    )

    assert outcome.completed is False
    assert outcome.llm_calls == 2
    assert outcome.tool_calls == 1
    assert "tool-round budget" in (outcome.error or "")


def test_evidence_agent_sections_come_only_from_the_outline():
    outline = _outline()
    prompt = json.loads(
        _evidence_prompt(outline)
    )
    supplied = {section["section_id"] for section in prompt["approved_outline"]["sections"]}
    assert supplied == {str(section.section_id) for section in outline.sections}
    assert all(
        section["search_terms"]
        for section in prompt["approved_outline"]["sections"]
        if section["role"] != "limitations"
    )


def _evidence_prompt(outline):
    llm = _ToolLLM([])
    toolbox = _Toolbox()
    # Build the prompt without consuming a model turn.
    try:
        run_evidence_agent(
            _brief(),
            outline,
            [],
            llm,
            toolbox,
            round_index=1,
            max_rounds=1,
            max_tool_calls=1,
        )
    except IndexError:
        pass
    return llm.calls[0]["messages"][1]["content"]
