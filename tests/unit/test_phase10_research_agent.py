from __future__ import annotations

import json
from uuid import uuid4

from dr_core.db.enums import ArticleType, ProjectLanguage, ResearchDepth, SectionRole
from dr_core.providers.models import LLMResponse, LLMToolCall
from dr_core.research.agent import AGENT_TASK, run_research_agent
from dr_core.research.models import ResearchBrief, SourceRecord

SOURCE_ID = uuid4()

_SECTIONS = [
    {
        "title": "From Braille to screen readers",
        "role": "body",
        "purpose": "Trace the accessibility technology line.",
        "search_terms": ["accessibility assistive technology milestones"],
        "target_words": 700,
    },
    {
        "title": "Law and digital practice",
        "role": "body",
        "purpose": "Explain the accessibility regulatory turn.",
        "search_terms": ["accessibility law digital design"],
        "target_words": 700,
    },
]


def _brief(**overrides) -> ResearchBrief:
    values = {
        "project_id": uuid4(),
        "run_id": uuid4(),
        "title": "Accessibility efforts",
        "topic": "How have people worked for accessibility?",
        "language": ProjectLanguage.EN,
        "research_depth": ResearchDepth.STANDARD,
        "article_type": ArticleType.HISTORICAL_DEVELOPMENT,
    }
    values.update(overrides)
    return ResearchBrief(**values)


def _final(**overrides) -> str:
    payload = {
        "summary": (
            "The material links assistive technology, disability rights and "
            "accessibility law to changes in design practice."
        ),
        "themes": ["assistive technology", "disability rights"],
        "terminology": ["screen reader"],
        "controversies": ["medical versus social model of disability"],
        "sections": [dict(section) for section in _SECTIONS],
        "gaps": ["The material is thin on non-Western history."],
        "next_steps": ["Search national disability laws."],
    }
    payload.update(overrides)
    return json.dumps(payload)


class _FakeToolbox:
    def __init__(self, *, records: list[SourceRecord] | None = None):
        self.records = list(records or [])
        self.searches = 0
        self.fetches = 0
        self.citation_calls = 0
        self.reads = 0
        self.max_searches = 4
        self.max_sources = 6
        self.max_fetches = 2
        self.max_citation_calls = 2
        self.calls: list[str] = []

    @property
    def source_count(self) -> int:
        return len(self.records)

    def execute(self, name: str, arguments: str) -> dict:
        self.calls.append(name)
        payload = json.loads(arguments or "{}")
        if name == "search_sources":
            self.searches += 1
            if not self.records:
                self.records = [_existing_source()]
            return {
                "results": [
                    {"source_id": str(SOURCE_ID), "title": "Accessibility timeline"}
                ]
            }
        if name == "read_source":
            self.reads += 1
            return {
                "source_id": str(payload.get("source_id")),
                "text": "A long excerpt about accessibility milestones.",
            }
        return {"error": f"unexpected tool {name}"}


class _FakeToolLLM:
    name = "fake-agent"

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
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
                "messages": [dict(message) for message in messages],
                "tools": tools,
                "max_tokens": max_tokens,
            }
        )
        return self.responses.pop(0)


def _tool_response(name: str, arguments: dict, call_id: str) -> LLMResponse:
    return LLMResponse(
        text="",
        model="fake-agent",
        tool_calls=[
            LLMToolCall(
                id=call_id,
                name=name,
                arguments=json.dumps(arguments),
            )
        ],
    )


def _text_response(text: str) -> LLMResponse:
    return LLMResponse(text=text, model="fake-agent")


def _existing_source() -> SourceRecord:
    return SourceRecord(
        source_id=SOURCE_ID,
        title="Accessibility timeline",
        url="https://example.org/timeline",
        provider="mock",
        snippet="Blind readers used tactile writing before screen readers.",
    )


def test_planning_agent_researches_then_commits_to_an_outline():
    toolbox = _FakeToolbox()
    llm = _FakeToolLLM(
        [
            _tool_response(
                "search_sources",
                {"query": "accessibility history", "kind": "web", "limit": 2},
                "call_search",
            ),
            _tool_response(
                "read_source",
                {"source_id": str(SOURCE_ID)},
                "call_read",
            ),
            _text_response(_final()),
        ]
    )

    outcome = run_research_agent(
        _brief(),
        llm,
        toolbox,
        max_rounds=3,
        max_tool_calls=6,
    )

    assert outcome.error is None
    assert outcome.outline is not None
    assert [section.title for section in outcome.outline.sections] == [
        "From Braille to screen readers",
        "Law and digital practice",
    ]
    assert all(
        section.role is SectionRole.BODY for section in outcome.outline.sections
    )
    assert outcome.outline.sections[0].search_terms == [
        "accessibility assistive technology milestones"
    ]
    assert outcome.recon is not None
    assert outcome.recon.source_count == 1
    assert outcome.recon.themes == ["assistive technology", "disability rights"]
    assert [record.source_id for record in outcome.sources] == [SOURCE_ID]
    assert outcome.tool_calls == 2
    assert outcome.searches == 1
    assert toolbox.calls == ["search_sources", "read_source"]

    prompt = json.loads(llm.calls[0]["messages"][1]["content"])
    assert prompt["task"] == AGENT_TASK
    assert prompt["limits"]["sections_hint"][1] >= 3
    assert prompt["brief"]["article_type"]["id"] == ArticleType.HISTORICAL_DEVELOPMENT.value
    assert any("A -> B -> C" in rule for rule in prompt["rules"])
    assert any("never a question" in rule for rule in prompt["rules"])
    assert "questions" not in prompt
    assert llm.calls[0]["tools"] is not None
    assert any(
        tool["function"]["name"] == "follow_citations"
        for tool in llm.calls[0]["tools"]
    )


def test_agent_repairs_an_invalid_outline_before_accepting_it():
    toolbox = _FakeToolbox(records=[_existing_source()])
    llm = _FakeToolLLM(
        [
            _text_response(_final(sections=[])),
            _text_response(_final()),
        ]
    )

    outcome = run_research_agent(
        _brief(),
        llm,
        toolbox,
        max_rounds=2,
        max_tool_calls=2,
    )

    assert outcome.outline is not None
    assert outcome.error is None
    assert len(llm.calls) == 2
    repair = llm.calls[1]["messages"][-1]
    assert repair["role"] == "user"
    assert "could not be used" in repair["content"]
    assert "outline failed validation" in repair["content"]


def test_agent_requires_a_stored_source_before_accepting_an_outline():
    toolbox = _FakeToolbox()
    llm = _FakeToolLLM([_text_response(_final()), _text_response(_final())])

    outcome = run_research_agent(
        _brief(),
        llm,
        toolbox,
        max_rounds=1,
        max_tool_calls=1,
    )

    assert outcome.outline is None
    assert outcome.recon is None
    assert outcome.error == "planning finished without any stored source"
    assert len(llm.calls) == 2
    assert "No search has succeeded yet" in llm.calls[1]["messages"][-1]["content"]


def test_agent_stops_at_the_tool_budget():
    toolbox = _FakeToolbox()
    llm = _FakeToolLLM(
        [
            LLMResponse(
                text="",
                model="fake-agent",
                tool_calls=[
                    LLMToolCall(
                        id="first",
                        name="search_sources",
                        arguments=json.dumps(
                            {"query": "accessibility history", "kind": "web"}
                        ),
                    ),
                    LLMToolCall(
                        id="second",
                        name="search_sources",
                        arguments=json.dumps(
                            {"query": "accessibility law", "kind": "academic"}
                        ),
                    ),
                ],
            ),
            _text_response(_final()),
        ]
    )

    outcome = run_research_agent(
        _brief(),
        llm,
        toolbox,
        max_rounds=1,
        max_tool_calls=1,
    )

    assert outcome.outline is not None
    assert outcome.tool_calls == 1
    assert toolbox.calls == ["search_sources"]
    budget_messages = [
        message
        for message in llm.calls[1]["messages"]
        if message.get("role") == "tool"
    ]
    assert len(budget_messages) == 2
    assert "tool budget exhausted" in budget_messages[-1]["content"]


def test_agent_drops_question_titles_and_requires_a_complete_outline():
    toolbox = _FakeToolbox(records=[_existing_source()])
    question_only = _final(
        sections=[
            {
                "title": "Why did accessibility change?",
                "role": "body",
                "purpose": "Question-shaped titles are dropped.",
                "search_terms": ["accessibility history"],
            }
        ]
    )
    llm = _FakeToolLLM(
        [
            _text_response(question_only),
            _text_response(_final()),
        ]
    )

    outcome = run_research_agent(
        _brief(),
        llm,
        toolbox,
        max_rounds=2,
        max_tool_calls=2,
    )

    assert outcome.outline is not None
    assert [section.title for section in outcome.outline.sections] == [
        "From Braille to screen readers",
        "Law and digital practice",
    ]
    assert "outline failed validation" in llm.calls[1]["messages"][-1]["content"]


def test_agent_summary_must_match_the_project_language():
    toolbox = _FakeToolbox(records=[_existing_source()])
    llm = _FakeToolLLM(
        [
            _text_response(_final(summary="这是中文摘要，不符合英文项目。")),
            _text_response(_final()),
        ]
    )

    outcome = run_research_agent(
        _brief(),
        llm,
        toolbox,
        max_rounds=2,
        max_tool_calls=2,
    )

    assert outcome.outline is not None
    assert len(llm.calls) == 2
    assert "research summary failed validation" in llm.calls[1]["messages"][-1][
        "content"
    ]


def test_agent_keeps_the_prompt_bounded_and_carries_feedback():
    toolbox = _FakeToolbox(records=[_existing_source()])
    llm = _FakeToolLLM([_text_response(_final())])

    outcome = run_research_agent(
        _brief(target_words=12000),
        llm,
        toolbox,
        max_rounds=2,
        max_tool_calls=2,
        version=4,
        feedback="Cover the cost side earlier.",
    )

    assert outcome.outline is not None
    assert outcome.outline.version == 4
    assert llm.calls[0]["max_tokens"] >= 3000
    assert llm.calls[0]["max_tokens"] <= 9000
    prompt = json.loads(llm.calls[0]["messages"][1]["content"])
    assert prompt["limits"]["sections_hint"][1] > 10
    assert "Cover the cost side earlier." in prompt["approver_feedback"]
    assert any("revised outline" in rule for rule in prompt["rules"])
