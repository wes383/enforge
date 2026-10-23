from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

from dr_core.db.enums import ProjectLanguage, ResearchDepth
from dr_core.providers.models import LLMResponse, LLMToolCall
from dr_core.research.agent import AGENT_TASK, run_research_agent
from dr_core.research.models import (
    ResearchBrief,
    ResearchPlan,
    ResearchQuestion,
    SourceRecord,
)
from dr_core.research.nodes import _research_map_framing

SOURCE_ID = uuid4()
_QUESTIONS = [
    {
        "question": "Which accessibility milestones changed assistive technology?",
        "priority": 900,
        "search_terms": ["accessibility assistive technology milestones"],
    },
    {
        "question": "How did accessibility law change digital design practice?",
        "priority": 700,
        "search_terms": ["accessibility law digital design"],
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
        "questions": list(_QUESTIONS),
        "outline": [
            {
                "title": "From Braille to screen readers",
                "purpose": "Trace the technology line.",
                "question_index": 0,
            },
            {
                "title": "Law and digital practice",
                "purpose": "Explain the regulatory turn.",
                "question_index": 1,
            },
            {
                "title": "Was this a turning point?",
                "purpose": "Question-shaped titles are dropped.",
                "question_index": 0,
            },
        ],
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
                self.records = [
                    SourceRecord(
                        source_id=SOURCE_ID,
                        title="Accessibility timeline",
                        url="https://example.org/timeline",
                        provider="mock",
                        snippet="Blind readers used tactile writing before screen readers.",
                    )
                ]
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


def test_agent_researches_before_committing_to_questions_and_outline():
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
    assert outcome.plan is not None
    assert [item.question for item in outcome.plan.questions] == [
        item["question"] for item in _QUESTIONS
    ]
    assert outcome.research_map is not None
    assert outcome.research_map.outline
    assert [item.title for item in outcome.research_map.outline] == [
        "From Braille to screen readers",
        "Law and digital practice",
    ]
    assert outcome.research_map.source_count == 1
    assert outcome.research_map.gaps
    assert [record.source_id for record in outcome.sources] == [SOURCE_ID]
    assert outcome.tool_calls == 2
    assert outcome.searches == 1
    assert toolbox.calls == ["search_sources", "read_source"]

    prompt = json.loads(llm.calls[0]["messages"][1]["content"])
    assert prompt["task"] == AGENT_TASK
    assert prompt["limits"]["questions"] >= 2
    assert any("A -> B -> C" in rule for rule in prompt["rules"])
    assert llm.calls[0]["tools"] is not None
    assert any(
        tool["function"]["name"] == "follow_citations"
        for tool in llm.calls[0]["tools"]
    )


def _seeded_toolbox() -> _FakeToolbox:
    return _FakeToolbox(records=[_existing_source()])


def _existing_source() -> SourceRecord:
    return SourceRecord(
        source_id=SOURCE_ID,
        title="Accessibility timeline",
        url="https://example.org/timeline",
        provider="mock",
        snippet="Blind readers used tactile writing before screen readers.",
    )


def test_agent_asks_the_model_to_repair_invalid_questions():
    toolbox = _seeded_toolbox()
    llm = _FakeToolLLM(
        [
            _text_response(_final(questions=[])),
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

    assert outcome.plan is not None
    assert outcome.error is None
    assert len(llm.calls) == 2
    repair = llm.calls[1]["messages"][-1]
    assert repair["role"] == "user"
    assert "could not be used" in repair["content"]
    assert "questions failed validation" in repair["content"]


def test_agent_repairs_before_accepting_a_plan_without_sources():
    toolbox = _FakeToolbox()
    llm = _FakeToolLLM([_text_response(_final()), _text_response(_final())])

    outcome = run_research_agent(
        _brief(),
        llm,
        toolbox,
        max_rounds=1,
        max_tool_calls=1,
    )

    assert outcome.plan is None
    assert outcome.research_map is None
    assert outcome.error == "research finished without any stored source"
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
                            {
                                "query": "accessibility history",
                                "kind": "web",
                            }
                        ),
                    ),
                    LLMToolCall(
                        id="second",
                        name="search_sources",
                        arguments=json.dumps(
                            {
                                "query": "accessibility law",
                                "kind": "academic",
                            }
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

    assert outcome.plan is not None
    assert outcome.tool_calls == 1
    assert toolbox.calls == ["search_sources"]
    budget_messages = [
        message
        for message in llm.calls[1]["messages"]
        if message.get("role") == "tool"
    ]
    assert len(budget_messages) == 2
    assert "tool budget exhausted" in budget_messages[-1]["content"]


def test_agent_rejects_a_question_shaped_outline_but_keeps_the_plan():
    toolbox = _FakeToolbox()
    llm = _FakeToolLLM(
        [
            _tool_response(
                "search_sources",
                {"query": "accessibility history", "kind": "web"},
                "call_search",
            ),
            _text_response(
                _final(
                    outline=[
                        {
                            "title": "Why did accessibility change?",
                            "purpose": "Should be dropped.",
                        }
                    ]
                )
            ),
        ]
    )

    outcome = run_research_agent(
        _brief(),
        llm,
        toolbox,
        max_rounds=2,
        max_tool_calls=2,
    )

    assert outcome.plan is not None
    assert outcome.research_map is not None
    assert outcome.research_map.outline == []


def test_agent_summary_must_match_the_project_language():
    toolbox = _FakeToolbox()
    llm = _FakeToolLLM(
        [
            _tool_response(
                "search_sources",
                {"query": "accessibility history", "kind": "web"},
                "call_search",
            ),
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

    assert outcome.plan is not None
    assert len(llm.calls) == 3
    assert "research summary failed validation" in llm.calls[2]["messages"][-1][
        "content"
    ]


def test_research_map_framing_skips_fixed_titles_and_bad_indexes():
    plan = ResearchPlan(
        summary="Plan",
        questions=[
            ResearchQuestion(question="Which accessibility milestones matter?"),
        ],
    )

    framing = _research_map_framing(
        {
            "outline": [
                {"title": "Conclusion", "question_index": 0},
                {"title": "  From Braille   to screen readers ", "purpose": "Trace it.", "question_index": 0},
                {"title": "Out of range", "question_index": 9},
                {"title": "From Braille to screen readers", "purpose": "Duplicate.", "question_index": 0},
                {"title": "Unmapped", "question_index": None},
            ]
        },
        plan,
    )

    assert framing == {0: ("From Braille to screen readers", "Trace it.")}


def test_agent_keeps_the_deterministic_prompt_and_limits_bounded():
    toolbox = _FakeToolbox()
    llm = _FakeToolLLM(
        [
            _tool_response(
                "search_sources",
                {"query": "accessibility history", "kind": "web"},
                "call_search",
            ),
            _text_response(_final()),
        ]
    )

    outcome = run_research_agent(
        _brief(),
        llm,
        toolbox,
        max_rounds=2,
        max_tool_calls=2,
        version=4,
    )

    assert outcome.plan is not None
    assert outcome.plan.version == 4
    assert llm.calls[0]["max_tokens"] >= 2500
    assert llm.calls[0]["max_tokens"] <= 8000
