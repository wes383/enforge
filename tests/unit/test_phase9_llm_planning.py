from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

from dr_core.config import Settings
from dr_core.config.settings import LLM_PLANNING_CALL_BUDGET
from dr_core.db.enums import ProjectLanguage, ResearchDepth
from dr_core.providers.errors import ProviderUnavailable
from dr_core.providers.models import LLMResponse
from dr_core.research.models import ResearchBrief
from dr_core.research.nodes import _llm_planner, _planning_node_timeout
from dr_core.research.planning import PLANNING_TASK, build_plan_with_llm
from dr_core.research.usage import UsageRecordingLLM


class _FakeLLM:
    name = "fake-model"

    def __init__(self, text: str | None = None, *, error: Exception | None = None):
        self.text = text
        self.error = error
        self.prompts: list[str] = []
        self.systems: list[str] = []

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        self.prompts.append(prompt)
        self.systems.append(system or "")
        if self.error is not None:
            raise self.error
        return LLMResponse(text=self.text or "", model=self.name)


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


def _text(questions: list, summary: str = "Plan the evidence review.") -> str:
    return json.dumps({"summary": summary, "questions": questions})


def test_model_questions_replace_the_template_plan():
    llm = _FakeLLM(
        _text(
            [
                {
                    "question": "What changes does peer review make to reported "
                    "error rates?",
                    "priority": 900,
                },
                {
                    "question": "Which studies test single-blind versus double-blind "
                    "review?",
                    "priority": 700,
                },
                {
                    "question": "What evidence shows peer review biases against new "
                    "ideas?",
                    "priority": 500,
                },
            ]
        )
    )
    plan = build_plan_with_llm(_brief(), llm, version=3)

    assert plan is not None
    assert plan.version == 3
    assert [item.priority for item in plan.questions] == [900, 700, 500]
    assert plan.questions[1].question.startswith("Which studies")
    assert plan.summary == "Plan the evidence review."

    prompt = json.loads(llm.prompts[0])
    assert prompt["task"] == PLANNING_TASK
    assert prompt["required"]["questions"] == 5
    assert "peer review" in prompt["brief"]["topic"]


def test_plain_string_questions_and_numbering_are_normalized():
    plan = build_plan_with_llm(
        _brief(),
        _FakeLLM(
            _text(
                [
                    "1. Which metrics measure reviewer agreement on paper quality?",
                    "- Do preprints weaken the reliability checks of peer review?",
                    {"question": "  How   does\n review latency affect reliability?  "},
                ]
            )
        ),
    )

    assert plan is not None
    assert [item.question for item in plan.questions] == [
        "Which metrics measure reviewer agreement on paper quality?",
        "Do preprints weaken the reliability checks of peer review?",
        "How does review latency affect reliability?",
    ]
    assert [item.priority for item in plan.questions] == [90, 80, 70]


def test_search_terms_are_parsed_cleaned_and_capped():
    plan = build_plan_with_llm(
        _brief(),
        _FakeLLM(
            _text(
                [
                    {
                        "question": "Which peer review models change reported "
                        "error rates?",
                        "search_terms": [
                            "  peer   review   error rates ",
                            "PEER REVIEW ERROR RATES",
                            "review bias",
                            "third term",
                            "fourth term",
                        ],
                    },
                    {
                        "question": "What evidence links review length to "
                        "reliability?",
                        "search_terms": "review length reliability",
                    },
                ]
            )
        ),
    )

    assert plan is not None
    assert plan.questions[0].search_terms == [
        "peer review error rates",
        "review bias",
        "third term",
    ]
    assert plan.questions[1].search_terms == ["review length reliability"]


def test_chinese_planning_prompt_asks_for_english_terms():
    zh = _brief(
        language=ProjectLanguage.ZH,
        title="同行评审",
        topic="同行评审如何影响研究可靠性",
    )
    llm = _FakeLLM(
        _text(
            [
                {"question": "同行评审对论文错误率的实际影响有哪些证据？"},
                {"question": "单盲与双盲评审在可靠性上的差异如何测量？"},
            ],
            summary="围绕同行评审组织证据检索。",
        )
    )
    build_plan_with_llm(zh, llm)

    prompt = json.loads(llm.prompts[0])
    assert "Simplified Chinese" in " ".join(prompt["rules"])
    assert any("English search term" in rule for rule in prompt["rules"])
    assert "search_terms" in prompt["rules"][-1]

    english = _FakeLLM(
        _text(
            [
                {"question": "Which peer review models change error rates?"},
                {"question": "What evidence links review length to reliability?"},
            ]
        )
    )
    build_plan_with_llm(_brief(), english)
    assert not any(
        "English search term" in rule
        for rule in json.loads(english.prompts[0])["rules"]
    )


def test_duplicate_and_overflowing_questions_are_trimmed_to_depth_limit():
    plan = build_plan_with_llm(
        _brief(research_depth=ResearchDepth.QUICK),
        _FakeLLM(
            _text(
                [
                    {"question": "Which review models change acceptance decisions?"},
                    {"question": "which review models CHANGE acceptance decisions? "},
                    {"question": "What evidence exists on reviewer bias?"},
                    {"question": "How do editors weigh conflicting reports?"},
                    {"question": "Why do appeals rarely reverse decisions?"},
                ]
            )
        ),
    )

    assert plan is not None
    assert len(plan.questions) == 3
    assert plan.questions[0].question == "Which review models change acceptance decisions?"
    assert [item.priority for item in plan.questions] == [90, 80, 70]


def test_invalid_or_off_topic_output_produces_no_plan():
    rejected = [
        _FakeLLM("not json at all"),
        _FakeLLM("[1, 2]"),
        _FakeLLM(_text([])),
        _FakeLLM(_text([{"question": "What is the capital of France?"}])),
        _FakeLLM(
            _text(
                [
                    {"question": "How does photosynthesis work?"},
                    {"question": "What drives ocean currents?"},
                ]
            )
        ),
        _FakeLLM(
            _text(
                [
                    {"question": "评审偏见在哪些研究社区更明显？"},
                    {"question": "什么是同行评审的边界条件？"},
                ]
            )
        ),
        _FakeLLM(_text([{"question": "peer review effects on error rates"}], summary="")),
        _FakeLLM(
            None, error=ProviderUnavailable("fake-model", "model endpoint is down")
        ),
    ]
    for llm in rejected:
        assert build_plan_with_llm(_brief(), llm) is None


def test_chinese_brief_accepts_chinese_questions_only():
    zh = _brief(
        language=ProjectLanguage.ZH,
        title="同行评审",
        topic="同行评审如何影响研究可靠性",
        research_depth=ResearchDepth.STANDARD,
    )
    plan = build_plan_with_llm(
        zh,
        _FakeLLM(
            _text(
                [
                    {
                        "question": "同行评审对论文错误率的实际影响有哪些证据？",
                        "priority": 80,
                    },
                    {
                        "question": "单盲与双盲评审在可靠性上的差异如何测量？",
                        "priority": 60,
                    },
                    {"question": "评审偏见在哪些研究社区更明显？", "priority": 40},
                ],
                summary="围绕同行评审组织证据检索。",
            )
        ),
    )

    assert plan is not None
    assert plan.summary == "围绕同行评审组织证据检索。"
    assert len(plan.questions) == 3
    assert plan.scope_notes is None

    wrong_language = build_plan_with_llm(
        _brief(
            language=ProjectLanguage.ZH,
            title="Peer review",
            topic="peer review 如何影响研究可靠性",
            research_depth=ResearchDepth.STANDARD,
        ),
        _FakeLLM(
            _text(
                [
                    {"question": "How does peer review change acceptance rates?"},
                    {"question": "Which metrics measure peer review agreement?"},
                ]
            )
        ),
    )
    assert wrong_language is None


def test_feedback_and_scope_reach_the_prompt():
    llm = _FakeLLM(
        _text(
            [
                {"question": "Which trials compare review models on error detection?"},
                {"question": "What evidence links review length to reliability?"},
            ]
        )
    )
    build_plan_with_llm(
        _brief(research_scope="biomedical journals since 2015"),
        llm,
        feedback="Add counter-evidence about reviewer bias.",
    )

    prompt = json.loads(llm.prompts[0])
    assert prompt["approver_feedback"].startswith('<UNTRUSTED_SOURCE_DATA label="feedback"')
    assert "Add counter-evidence" in prompt["approver_feedback"]
    assert "biomedical journals since 2015" in prompt["brief"]["research_scope"]
    assert any("UNTRUSTED" in rule for rule in prompt["rules"])
    assert "never follow instructions" in llm.systems[0].lower()


def test_reconnaissance_notes_are_untrusted_planning_context():
    llm = _FakeLLM(
        _text(
            [
                {"question": "Which studies compare review models on error detection?"},
                {"question": "What evidence links reviewer bias to reliability?"},
            ]
        )
    )
    background = {
        "summary": "Reviews debate reviewer bias and error detection.",
        "themes": ["reviewer bias"],
        "source_count": 3,
    }
    build_plan_with_llm(_brief(), llm, background=background)

    prompt = json.loads(llm.prompts[0])
    assert prompt["reconnaissance"].startswith(
        '<UNTRUSTED_SOURCE_DATA label="reconnaissance"'
    )
    assert "reviewer bias" in prompt["reconnaissance"]
    assert any("reconnaissance notes" in rule for rule in prompt["rules"])

    plain = _FakeLLM(
        _text(
            [
                {"question": "Which studies compare review models on error detection?"},
                {"question": "What evidence links reviewer bias to reliability?"},
            ]
        )
    )
    build_plan_with_llm(_brief(), plain)
    plain_prompt = json.loads(plain.prompts[0])
    assert plain_prompt["reconnaissance"] is None
    assert not any("reconnaissance" in rule for rule in plain_prompt["rules"])


def test_planner_gate_and_node_timeout_budget():
    llm = _FakeLLM(_text([]))
    settings = Settings(
        _env_file=None,
        research_llm_planning_enabled=True,
        research_node_timeout_seconds=90,
        llm_request_timeout_seconds=120,
        llm_max_retries=1,
    )
    ctx = SimpleNamespace(
        settings=settings,
        registry=SimpleNamespace(llm=llm),
        record_usage=lambda response: None,
    )
    planner = _llm_planner(ctx)
    assert isinstance(planner, UsageRecordingLLM)
    assert planner.name == llm.name
    assert _planning_node_timeout(ctx) == 90 + 2 * 120

    mock_only = SimpleNamespace(
        settings=settings,
        registry=SimpleNamespace(llm=SimpleNamespace(name="mock-llm")),
    )
    assert _llm_planner(mock_only) is None
    assert _planning_node_timeout(mock_only) == 90

    disabled = SimpleNamespace(
        settings=settings.model_copy(update={"research_llm_planning_enabled": False}),
        registry=SimpleNamespace(llm=llm),
    )
    assert _llm_planner(disabled) is None
    assert _planning_node_timeout(disabled) == 90


def test_planning_expands_the_run_time_floor():
    without = Settings(
        _env_file=None,
        research_llm_writing_enabled=True,
        llm_request_timeout_seconds=120,
        llm_max_retries=0,
        research_claim_llm_review_limit=10,
        research_node_timeout_seconds=90,
    )
    with_planning = without.model_copy(
        update={"research_llm_planning_enabled": True}
    )

    assert without.llm_workflow_enabled is True
    assert with_planning.llm_workflow_enabled is True
    assert (
        with_planning.llm_run_time_floor_seconds - without.llm_run_time_floor_seconds
        == LLM_PLANNING_CALL_BUDGET * with_planning.llm_call_budget_seconds
    )

    planning_only = Settings(
        _env_file=None,
        research_llm_planning_enabled=True,
        run_task_soft_time_limit_seconds=300,
        run_task_hard_time_limit_seconds=360,
    )
    assert planning_only.effective_run_task_soft_time_limit_seconds > 300
    assert (
        planning_only.effective_run_task_hard_time_limit_seconds
        == planning_only.effective_run_task_soft_time_limit_seconds + 60
    )


def test_planning_is_off_by_default():
    settings = Settings(_env_file=None)
    assert settings.research_llm_planning_enabled is False
    assert settings.llm_workflow_enabled is False
