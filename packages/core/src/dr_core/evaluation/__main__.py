from __future__ import annotations

import argparse
import json
import sys
from uuid import UUID

from dr_core.config import get_settings
from dr_core.evaluation.cases import CASES
from dr_core.evaluation.runner import (
    EvaluationOptions,
    load_human_review,
    run_evaluation,
    write_report,
)
from dr_core.observability.tracing import build_tracer


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the deterministic Phase 9 research quality evaluation."
    )
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--output", help="Write the JSON report to this path")
    parser.add_argument("--keep", action="store_true", help="Keep evaluation data")
    parser.add_argument(
        "--human-review",
        help="JSON list with claim_id and reviewer_label fields",
    )
    parser.add_argument(
        "--no-human-sample",
        action="store_true",
        help="Do not include a human review sample",
    )
    parser.add_argument("--list", action="store_true", help="List cases and exit")
    args = parser.parse_args()

    if args.list:
        for case in CASES:
            print(f"{case.id}\t{case.category}\t{case.scenario}")
        return 0

    reviewed = load_human_review(args.human_review) if args.human_review else None
    report = run_evaluation(
        case_ids=args.case_ids,
        options=EvaluationOptions(
            keep_data=args.keep,
            include_human_sample=not args.no_human_sample,
        ),
        reviewed_claims=reviewed,
    )
    if args.output:
        write_report(report, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    _send_to_langfuse(report)
    return 0 if all(case["passed"] for case in report["cases"]) else 1


def _send_to_langfuse(report: dict) -> None:
    settings = get_settings()
    if not settings.langfuse_enabled:
        return
    tracer = build_tracer(
        run_id=UUID(int=1),
        project_id=UUID(int=2),
        settings=settings,
    )
    tracer.trace_name = "evaluation.run"
    tracer.start(
        input_metadata={
            "dataset": report.get("dataset"),
            "cases": len(report.get("cases") or []),
        }
    )
    summary = report.get("summary") or {}
    for name in (
        "task_success_rate",
        "recovery_success_rate",
        "citation_validity_rate",
        "citation_support_accuracy",
        "section_coverage_rate",
        "unsupported_claim_ratio",
        "metadata_accuracy_rate",
        "assertion_pass_rate",
    ):
        value = summary.get(name)
        if value is not None:
            tracer.score(name, value)
    tracer.finish(
        status=(
            "completed"
            if all(case.get("passed") for case in report.get("cases") or [])
            else "failed"
        ),
        output=summary,
    )
    tracer.close()


if __name__ == "__main__":
    sys.exit(main())
