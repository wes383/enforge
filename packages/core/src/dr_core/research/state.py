from __future__ import annotations

from typing import Any, TypedDict


class ResearchState(TypedDict, total=False):
    """LangGraph state.

    Only compact business identifiers and summaries are kept here. Full source
    text and database rows are re-read from PostgreSQL or the shared volume by
    the node that needs them.
    """

    brief: dict[str, Any]
    recon: dict[str, Any]
    queries: list[dict[str, Any]]
    round_index: int
    agent_rounds: int
    evidence_notes: list[dict[str, str]]
    sources: list[dict[str, Any]]
    evidence_ids: list[str]
    matrix: dict[str, Any]
    gaps: list[dict[str, Any]]
    outline: dict[str, Any]
    outline_id: str
    sections: list[dict[str, Any]]
    article_polished: bool
    claims: list[dict[str, Any]]
    citation_audit: dict[str, Any]
    reference_audit: dict[str, Any]
    artifacts: list[dict[str, Any]]
    feedback: dict[str, Any]
    limitations: list[str]
    budget_stopped: bool
    current_node: str
