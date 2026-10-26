"""Opt-in writing-quality judge, kept out of the deterministic gate.

The deterministic evaluator answers whether citations resolve, whether a
quotation occurs in its source and whether a claim is supported by the rules.
It cannot answer whether the article reads well. This job asks a model to
score three writing properties only — coherence, specificity discipline and
calibrated tone — because citation validity must stay reproducible.

Running it costs money, so it is a separate entry point:
``python -m dr_core.evaluation.judge --provider real``. It is not part of the
test suite and its scores are a trend, never a release gate.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from dr_core.config import Settings, get_settings
from dr_core.evaluation.cases import CASES, DATASET_VERSION
from dr_core.evaluation.runner import (
    EvaluationOptions,
    evaluation_settings,
    run_evaluation,
    write_report,
)
from dr_core.observability.tracing import build_tracer
from dr_core.providers.base import LLMProvider
from dr_core.providers.models import LLMResponse
from dr_core.providers.registry import build_provider_registry
from dr_core.research.llm_json import parse_llm_json
from dr_core.research.usage import ReasoningBudgetLLM

JUDGE_SYSTEM = """You evaluate the writing quality of one research article.
Score only what the text itself shows. Do not verify citations, sources or
factual truth: a deterministic audit checks those separately.

Return one JSON object and nothing else:
{"coherence": 1-5, "specificity_discipline": 1-5, "calibrated_tone": 1-5,
 "notes": "at most two sentences naming the largest weakness"}

coherence: the article reads as one argument, sections connect, and nothing
repeats or dangles.
specificity_discipline: concrete names, dates and numbers are attributed to a
named source or clearly framed as examples; the text does not invent
plausible-sounding precision.
calibrated_tone: confidence matches the strength and disagreement of the
evidence; no overclaiming and no promotional language.
"""

_EXCLUDED_CASES = frozenset({"worker_interrupt", "rate_limited", "budget_exhausted"})
# Reasoning models bill their thinking against the same budget as the answer,
# so a small limit returns an empty answer with no JSON in it.
_JUDGE_MAX_TOKENS = 6000


@dataclass(frozen=True, slots=True)
class JudgeScore:
    case_id: str
    coherence: int | None
    specificity_discipline: int | None
    calibrated_tone: int | None
    notes: str
    model: str
    input_tokens: int
    output_tokens: int
    error: str | None = None


@dataclass(frozen=True, slots=True)
class JudgeOptions:
    # `real` calls the configured LLM. `fake` is a dry run over existing
    # articles; it never builds a provider from .env.
    provider: str = "fake"
    case_ids: list[str] | None = None
    limit: int = 10
    articles_dir: str | None = None
    export_dir: str | None = None
    output: str | None = None


def judge_settings() -> Settings:
    """Bound a judged run so ten articles fit inside a small token budget.

    Writing, outlining and polish use the real model; planning agents, claim
    review and hybrid retrieval stay on their deterministic paths. The token
    cap is per run and stops evidence expansion before it can crowd out the
    article itself.
    """
    return evaluation_settings().model_copy(
        update={
            "research_llm_writing_enabled": True,
            "research_llm_outline_enabled": True,
            "research_llm_polish_enabled": True,
            "research_claim_llm_review": False,
            "research_agent_enabled": False,
            "research_outline_approval_required": False,
            "research_max_search_rounds": 1,
            "research_max_queries_per_round": 6,
            "research_max_sources": 16,
            "research_max_fetches_per_round": 8,
            "research_max_evidence": 80,
            "research_token_budget": 90000,
            "research_polish_max_input_chars": 16000,
            "research_node_timeout_seconds": 180,
            # A whole-article polish occasionally exceeds the default 240s
            # request timeout. Give it one longer attempt plus a retry so a
            # slow call does not silently ship an unpolished article to the
            # judge; the node budget scales with this value.
            "llm_request_timeout_seconds": 360,
            "llm_max_retries": 1,
            "langfuse_enabled": False,
        }
    )


def default_case_ids() -> list[str]:
    """Cases that produce a finished article rather than a failure path."""
    return [
        case.id
        for case in CASES
        if case.scenario not in {"worker_interrupt", "rate_limited"}
        and case.id not in _EXCLUDED_CASES
    ]


def judge_article(
    llm: LLMProvider,
    article: str,
    *,
    case_id: str,
) -> JudgeScore:
    if not article.strip():
        return JudgeScore(
            case_id=case_id,
            coherence=None,
            specificity_discipline=None,
            calibrated_tone=None,
            notes="",
            model=getattr(llm, "name", ""),
            input_tokens=0,
            output_tokens=0,
            error="empty article",
        )
    try:
        response = llm.generate(
            f"Article id: {case_id}\n\n<article>\n{article}\n</article>",
            system=JUDGE_SYSTEM,
            max_tokens=_JUDGE_MAX_TOKENS,
            # Fixed so two runs over the same article are comparable.
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001 - one failure must not kill the job
        return JudgeScore(
            case_id=case_id,
            coherence=None,
            specificity_discipline=None,
            calibrated_tone=None,
            notes="",
            model=getattr(llm, "name", ""),
            input_tokens=0,
            output_tokens=0,
            error=f"{type(exc).__name__}: {exc}"[:500],
        )
    try:
        payload = _parse_judge_payload(response.text)
    except (TypeError, ValueError) as exc:
        return JudgeScore(
            case_id=case_id,
            coherence=None,
            specificity_discipline=None,
            calibrated_tone=None,
            notes="",
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            error=f"unusable judge output: {exc}"[:500],
        )
    return JudgeScore(
        case_id=case_id,
        coherence=payload["coherence"],
        specificity_discipline=payload["specificity_discipline"],
        calibrated_tone=payload["calibrated_tone"],
        notes=str(payload.get("notes") or "")[:600],
        model=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
    )


def aggregate_scores(scores: list[JudgeScore]) -> dict[str, Any]:
    def mean(field: str) -> float | None:
        values = [
            getattr(score, field)
            for score in scores
            if getattr(score, field) is not None
        ]
        return round(sum(values) / len(values), 4) if values else None

    return {
        "cases": len(scores),
        "judged": sum(1 for score in scores if score.error is None),
        "errors": sum(1 for score in scores if score.error is not None),
        "coherence": mean("coherence"),
        "specificity_discipline": mean("specificity_discipline"),
        "calibrated_tone": mean("calibrated_tone"),
        "input_tokens": sum(score.input_tokens for score in scores),
        "output_tokens": sum(score.output_tokens for score in scores),
    }


def run_judge(
    options: JudgeOptions | None = None,
    *,
    llm: LLMProvider | None = None,
    settings: Settings | None = None,
    database: Any = None,
) -> dict[str, Any]:
    options = options or JudgeOptions()
    if options.provider not in {"real", "fake"}:
        raise ValueError(f"Unknown judge provider: {options.provider}")
    settings = settings or judge_settings()
    selected = options.case_ids or default_case_ids()
    if len(selected) > options.limit:
        selected = selected[: options.limit]
    generation: dict[str, Any] | None = None
    articles_dir: Path | None = None
    if options.articles_dir:
        llm = llm or resolve_llm(options.provider, settings=settings)
        articles_dir = Path(options.articles_dir)
        articles = _read_articles(
            articles_dir,
            selected if options.case_ids else [],
        )
        articles = dict(list(articles.items())[: options.limit])
    else:
        if options.provider != "real":
            raise ValueError(
                "The fake provider only scores existing articles; pass "
                "--articles DIR or use --provider real"
            )
        llm = llm or resolve_llm("real", settings=settings)
        articles_dir = Path(
            options.export_dir or tempfile.mkdtemp(prefix="dr-judge-")
        )
        report = run_evaluation(
            case_ids=selected,
            options=EvaluationOptions(
                keep_data=False,
                include_human_sample=False,
                export_dir=str(articles_dir),
            ),
            database=database,
            settings=settings,
            llm=llm,
        )
        generation = _generation_summary(report)
        articles = _read_articles(articles_dir, selected)
    scores = [
        judge_article(llm, article, case_id=case_id)
        for case_id, article in articles.items()
    ]
    report = {
        "dataset": DATASET_VERSION,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "provider": options.provider,
        "model": getattr(llm, "name", None),
        "articles_dir": str(articles_dir),
        "cases": [asdict(score) for score in scores],
        "summary": aggregate_scores(scores),
        "generation": generation,
        "note": (
            "Writing-quality trend only. Coherence, specificity discipline and "
            "calibrated tone are scored by a model at temperature 0; citation "
            "validity and claim support stay in the deterministic audit and "
            "are not judged here."
        ),
    }
    if options.output:
        write_report(report, options.output)
    return report


def resolve_llm(
    provider: str,
    *,
    settings: Settings | None = None,
) -> LLMProvider:
    if provider == "fake":
        return _FakeJudgeLLM()
    resolved = settings or get_settings()
    registry = build_provider_registry(resolved)
    if registry.llm is None:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured; the real judge has no model"
        )
    return ReasoningBudgetLLM(
        registry.llm,
        factor=resolved.llm_reasoning_retry_factor,
        ceiling=resolved.llm_reasoning_retry_ceiling,
    )


def _read_articles(
    directory: Path,
    case_ids: list[str],
) -> dict[str, str]:
    if not directory.is_dir():
        raise FileNotFoundError(f"Article directory does not exist: {directory}")
    if case_ids:
        paths = [directory / f"{case_id}.md" for case_id in case_ids]
    else:
        paths = sorted(directory.glob("*.md"))
    articles: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            continue
        articles[path.stem] = path.read_text(encoding="utf-8")
    if not articles:
        raise ValueError(f"No .md articles found in {directory}")
    return articles


def _generation_summary(report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary") or {}
    polish = {"applied": 0, "failed": 0, "other": 0}
    for case in report.get("cases") or []:
        status = (case.get("polish") or {}).get("status")
        if status == "applied":
            polish["applied"] += 1
        elif status in {"model_error", "rejected"}:
            polish["failed"] += 1
        else:
            polish["other"] += 1
    return {
        "cases": summary.get("cases"),
        "completed": sum(
            1
            for case in report.get("cases") or []
            if case.get("status") == "completed"
        ),
        "evidence": summary.get("total_evidence"),
        "claims": summary.get("total_claims"),
        "polish": polish,
        "input_tokens": summary.get("total_input_tokens"),
        "output_tokens": summary.get("total_output_tokens"),
    }


def _parse_judge_payload(text: str) -> dict[str, Any]:
    payload = parse_llm_json(text)
    return {
        "coherence": _clamp_score(payload.get("coherence")),
        "specificity_discipline": _clamp_score(
            payload.get("specificity_discipline")
        ),
        "calibrated_tone": _clamp_score(payload.get("calibrated_tone")),
        "notes": payload.get("notes"),
    }


def _clamp_score(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(1, min(5, int(round(number))))


def _send_to_langfuse(report: dict[str, Any]) -> None:
    settings = get_settings()
    if not settings.langfuse_enabled:
        return
    summary = report.get("summary") or {}
    if not summary.get("judged"):
        return
    tracer = build_tracer(
        run_id=UUID(int=3),
        project_id=UUID(int=4),
        settings=settings,
    )
    tracer.trace_name = "evaluation.judge"
    tracer.start(
        input_metadata={
            "provider": report.get("provider"),
            "model": report.get("model"),
            "cases": summary.get("cases"),
        }
    )
    for name in ("coherence", "specificity_discipline", "calibrated_tone"):
        value = summary.get(name)
        if value is not None:
            tracer.score(f"judge_{name}", value)
    tracer.finish(status="completed", output=summary)
    tracer.close()


class _FakeJudgeLLM:
    """Deterministic output for dry runs; never used by real scoring."""

    name = "fake-judge"

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        return LLMResponse(
            text=json.dumps(
                {
                    "coherence": 3,
                    "specificity_discipline": 3,
                    "calibrated_tone": 3,
                    "notes": "dry run",
                }
            ),
            model=self.name,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Score writing quality only. Citation validity stays in the "
            "deterministic audit; this job is an opt-in trend, not a gate."
        )
    )
    parser.add_argument(
        "--provider",
        choices=("real", "fake"),
        default="fake",
        help="real calls the configured LLM and spends tokens; fake is a dry run",
    )
    parser.add_argument(
        "--articles",
        help="Score existing <case>.md files instead of generating them",
    )
    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="Case id to generate and score (repeatable)",
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--export",
        help="Directory for generated articles (default: a temporary directory)",
    )
    parser.add_argument("--output", help="Write the judge report to this path")
    parser.add_argument(
        "--no-langfuse",
        action="store_true",
        help="Do not send scores to Langfuse",
    )
    args = parser.parse_args(argv)
    if args.provider == "real" and not args.articles:
        print(
            "Calling the configured LLM; this consumes API tokens.",
            file=sys.stderr,
        )
    report = run_judge(
        JudgeOptions(
            provider=args.provider,
            case_ids=args.case_ids,
            limit=args.limit,
            articles_dir=args.articles,
            export_dir=args.export,
            output=args.output,
        )
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.no_langfuse:
        _send_to_langfuse(report)
    return 0 if report["summary"]["judged"] else 1


if __name__ == "__main__":
    sys.exit(main())
