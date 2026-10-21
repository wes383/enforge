from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from dr_core.db.enums import ProjectLanguage, ResearchDepth


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    id: str
    category: str
    topic: str
    scenario: str
    language: ProjectLanguage = ProjectLanguage.EN
    depth: ResearchDepth = ResearchDepth.STANDARD
    budget_usd: Decimal | None = None
    expected: dict[str, object] = field(default_factory=dict)


DATASET_VERSION = "phase9-v1"

CASES: tuple[EvaluationCase, ...] = (
    EvaluationCase(
        id="general_knowledge",
        category="一般知识研究",
        topic="How does remote work affect team collaboration and trust?",
        scenario="baseline",
        expected={"refute_evidence": False},
    ),
    EvaluationCase(
        id="academic_review",
        category="学术文献综述",
        topic="What do empirical studies report about retrieval augmented generation quality?",
        scenario="academic_review",
        expected={"min_academic_sources": 2},
    ),
    EvaluationCase(
        id="controversial_topic",
        category="存在明显争议的话题",
        topic="Does regulatory enforcement improve compliance in regulated industries?",
        scenario="controversial",
        expected={"min_refute_evidence": 1},
    ),
    EvaluationCase(
        id="abstract_only",
        category="仅摘要无法获取全文",
        topic="What are the health effects of urban green space?",
        scenario="abstract_only",
        expected={"min_snippet_evidence": 1},
    ),
    EvaluationCase(
        id="incomplete_metadata",
        category="来源元数据不完整",
        topic="How reliable are open-access citation counts?",
        scenario="incomplete_metadata",
        expected={"metadata_accuracy_below": 1.0},
    ),
    EvaluationCase(
        id="snippet_fulltext_conflict",
        category="原文与搜索摘要不一致",
        topic="Does enforcement improve compliance in regulated industries?",
        scenario="snippet_conflict",
        expected={"min_full_text_evidence": 1},
    ),
    EvaluationCase(
        id="conflicting_literature",
        category="文献相互矛盾",
        topic="Does a higher minimum wage reduce employment?",
        scenario="conflicting_literature",
        expected={"min_refute_evidence": 1},
    ),
    EvaluationCase(
        id="worker_interrupt",
        category="worker 中途中断",
        topic="How does sleep duration affect cognitive performance?",
        scenario="worker_interrupt",
        expected={"must_resume": True},
    ),
    EvaluationCase(
        id="rate_limited",
        category="外部 API 限流",
        topic="What evidence supports the effectiveness of carbon pricing?",
        scenario="rate_limited",
        expected={"min_failed_queries": 1},
    ),
    EvaluationCase(
        id="budget_exhausted",
        category="研究预算耗尽",
        topic="What are the limitations of remote work productivity research?",
        scenario="budget_exhausted",
        budget_usd=Decimal("0.01"),
        expected={"report_mentions_budget": True},
    ),
)


def case_by_id(case_id: str) -> EvaluationCase:
    for case in CASES:
        if case.id == case_id:
            return case
    raise KeyError(f"Unknown evaluation case: {case_id}")


__all__ = ["CASES", "DATASET_VERSION", "EvaluationCase", "case_by_id"]
