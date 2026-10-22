import json
from types import SimpleNamespace
from uuid import uuid4

from dr_core.db.enums import ProjectLanguage, ResearchDepth
from dr_core.research.models import ResearchBrief
from dr_core.research.polish import DraftArticleSection, polish_draft_sections

INTRO = "## Introduction\n\nRemote work changed how teams measure output [S1]."
BODY = "## Productivity\n\nAudited firms kept output steady [S2]. Costs rose in 2019 [S2]."
LIMITATIONS = "## Limitations\n\nMost studies rely on self-reported hours."

INTRO_POLISHED = "## Introduction\n\nHow teams count output moved with remote work [S1]."
BODY_POLISHED = (
    "## Productivity\n\n"
    "Output held steady in audited firms, while costs rose in 2019 [S2]."
)
LIMITATIONS_POLISHED = (
    "## Limitations\n\nMost of the evidence rests on self-reported hours."
)


def _brief(**overrides) -> ResearchBrief:
    values = {
        "project_id": uuid4(),
        "run_id": uuid4(),
        "title": "Remote work",
        "topic": "Does remote work reduce productivity?",
        "language": ProjectLanguage.EN,
        "research_depth": ResearchDepth.STANDARD,
    }
    values.update(overrides)
    return ResearchBrief(**values)


def _sections() -> list[DraftArticleSection]:
    return [
        DraftArticleSection(index=0, title="Introduction", content=INTRO),
        DraftArticleSection(index=1, title="Productivity", content=BODY),
        DraftArticleSection(index=2, title="Limitations", content=LIMITATIONS),
    ]


class _FakeLLM:
    name = "fake-llm"

    def __init__(self, payload, *, raises: bool = False):
        self.text = payload if isinstance(payload, str) else json.dumps(payload)
        self.raises = raises
        self.prompts: list[str] = []
        self.max_tokens: list[int] = []

    def generate(self, prompt, *, system=None, max_tokens=None, temperature=0.0):
        self.prompts.append(prompt)
        self.max_tokens.append(max_tokens or 0)
        if self.raises:
            raise RuntimeError("model timed out")
        return SimpleNamespace(text=self.text)


def _rewrite(index: int, markdown: str) -> dict:
    return {"index": index, "markdown": markdown}


def _run(rewrites, *, drafts=None, max_input_chars: int = 24000, raises=False):
    llm = _FakeLLM({"sections": list(rewrites)}, raises=raises)
    changes, reason = polish_draft_sections(
        brief=_brief(),
        sections=drafts if drafts is not None else _sections(),
        llm=llm,
        max_input_chars=max_input_chars,
    )
    return changes, reason, llm


def test_polish_rewrites_every_section_it_can_trust():
    changes, reason, _ = _run(
        [
            _rewrite(0, INTRO_POLISHED),
            _rewrite(1, BODY_POLISHED),
            _rewrite(2, LIMITATIONS_POLISHED),
        ]
    )
    assert reason == ""
    assert [item.index for item in changes] == [0, 1, 2]
    assert changes[1].content.startswith("## Productivity\n\nOutput held steady")


def test_polish_prompt_fixes_the_fact_surface_and_marks_sections_untrusted():
    _, _, llm = _run([_rewrite(0, INTRO_POLISHED)])
    prompt = json.loads(llm.prompts[0])
    rules = " ".join(prompt["rules"])
    assert "unify terminology" in rules
    assert "Do not add, drop or change any fact" in rules
    assert "Never invent a marker" in rules
    assert "UNTRUSTED_SOURCE_DATA" in rules
    assert prompt["language"] == "en"
    assert prompt["topic"] == "Does remote work reduce productivity?"
    assert [item["index"] for item in prompt["sections"]] == [0, 1, 2]
    assert '<UNTRUSTED_SOURCE_DATA label="section_1">' in prompt["sections"][1]["markdown"]
    assert BODY in prompt["sections"][1]["markdown"]
    total = sum(len(item.content) for item in _sections())
    assert llm.max_tokens[0] == max(800, int(total * 4 / 5))


def test_polish_keeps_the_marker_set_of_every_section():
    changes, _, _ = _run(
        [
            _rewrite(0, "## Introduction\n\nRemote work changed how teams measure output."),
            _rewrite(1, BODY_POLISHED.replace("[S2]", "[S3]")),
            _rewrite(2, LIMITATIONS_POLISHED),
        ]
    )
    assert [item.index for item in changes] == [2]


def test_polish_keeps_the_numbers_of_every_section():
    changes, _, _ = _run(
        [
            _rewrite(0, "## Introduction\n\nRemote work changed output in 2015 [S1]."),
            _rewrite(1, "## Productivity\n\nOutput held steady in audited firms [S2]."),
            _rewrite(2, LIMITATIONS_POLISHED),
        ]
    )
    assert [item.index for item in changes] == [2]


def test_polish_rejects_paragraphs_that_lose_their_citation():
    changes, _, _ = _run(
        [
            _rewrite(0, INTRO_POLISHED),
            _rewrite(
                1,
                "## Productivity\n\nAudited firms kept output steady [S2].\n"
                "Costs rose in 2019, which managers blamed on tooling.",
            ),
            _rewrite(2, LIMITATIONS_POLISHED),
        ]
    )
    assert [item.index for item in changes] == [0, 2]


def test_polish_rejects_heading_changes_and_added_headings():
    changes, _, _ = _run(
        [
            _rewrite(0, "## Intro\n\nHow teams count output moved with remote work [S1]."),
            _rewrite(
                1,
                "## Productivity\n\nAudited firms kept output steady [S2].\n\n"
                "### Costs\n\nCosts rose in 2019 [S2].",
            ),
            _rewrite(2, LIMITATIONS_POLISHED),
        ]
    )
    assert [item.index for item in changes] == [2]


def test_polish_rejects_truncated_rewrites_and_leaked_wrappers():
    changes, _, _ = _run(
        [
            _rewrite(0, "## Introduction\n\nOutput [S1]."),
            _rewrite(
                1,
                "## Productivity\n\nAudited firms kept output steady [S2] "
                "<UNTRUSTED_SOURCE_DATA> costs rose in 2019 [S2].",
            ),
            _rewrite(2, LIMITATIONS_POLISHED),
        ]
    )
    assert [item.index for item in changes] == [2]


def test_polish_skips_sections_the_model_left_unchanged():
    changes, reason, _ = _run([_rewrite(0, INTRO), _rewrite(1, BODY_POLISHED)])
    assert [item.index for item in changes] == [1]
    assert reason == ""


def test_polish_skips_a_single_section_article():
    changes, reason, llm = _run([], drafts=_sections()[:1])
    assert changes == []
    assert reason == "single_section"
    assert llm.prompts == []


def test_polish_skips_articles_over_the_input_cap():
    changes, reason, llm = _run([], max_input_chars=120)
    assert changes == []
    assert reason.startswith("article_too_long")
    assert llm.prompts == []


def test_polish_keeps_the_article_when_the_model_fails():
    _, reason, llm = _run([], raises=True)
    assert llm.prompts
    assert reason == "model_error"


def test_polish_ignores_indexes_that_are_not_in_the_article():
    changes, reason, _ = _run([_rewrite(7, "## Elsewhere\n\nNothing [S4].")])
    assert changes == []
    assert "section 7 is not in the article" in reason
