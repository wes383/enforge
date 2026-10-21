"""phase 4 mock search provider

Revision ID: c4e8f1a2b7d3
Revises: 8d2a7c4e9f10
Create Date: 2026-10-21 04:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = "c4e8f1a2b7d3"
down_revision: Union[str, None] = "8d2a7c4e9f10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_PROVIDERS = ("tavily", "exa", "crossref", "semantic_scholar")
_NEW_PROVIDERS = (*_OLD_PROVIDERS, "mock")


def _provider_check(values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"provider IN ({joined})"


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_search_queries_search_provider"),
        "search_queries",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_search_queries_search_provider"),
        "search_queries",
        _provider_check(_NEW_PROVIDERS),
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_search_queries_search_provider"),
        "search_queries",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_search_queries_search_provider"),
        "search_queries",
        _provider_check(_OLD_PROVIDERS),
    )
