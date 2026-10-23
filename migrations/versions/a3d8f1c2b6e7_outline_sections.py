"""replace research questions with outline sections

Revision ID: a3d8f1c2b6e7
Revises: f2a6c9d4e8b1
Create Date: 2026-10-23 11:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a3d8f1c2b6e7"
down_revision: Union[str, None] = "f2a6c9d4e8b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SECTION_ROLES = (
    "introduction",
    "body",
    "contradictions",
    "limitations",
    "conclusion",
)
_COVERAGE_STATUSES = ("open", "partial", "covered", "insufficient")


def upgrade() -> None:
    op.create_table(
        "outline_sections",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("section_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                *_SECTION_ROLES,
                name="section_role",
                native_enum=False,
                create_constraint=True,
                length=24,
            ),
            server_default="body",
            nullable=False,
        ),
        sa.Column(
            "purpose",
            sa.Text(),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column(
            "search_terms",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("target_words", sa.Integer(), nullable=True),
        sa.Column(
            "priority",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "coverage_status",
            sa.Enum(
                *_COVERAGE_STATUSES,
                name="section_coverage_status",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            server_default="open",
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "priority >= 0",
            name=op.f("ck_outline_sections_priority_non_negative"),
        ),
        sa.CheckConstraint(
            "target_words IS NULL OR (target_words >= 50 AND target_words <= 5000)",
            name=op.f("ck_outline_sections_target_words_range"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["research_projects.id"],
            name=op.f("fk_outline_sections_project_id_research_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
            name=op.f("fk_outline_sections_run_id_research_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outline_sections")),
        sa.UniqueConstraint(
            "run_id",
            "section_hash",
            name=op.f("uq_outline_sections_run_id_section_hash"),
        ),
    )
    op.create_index(
        "ix_outline_sections_project_id_run_id",
        "outline_sections",
        ["project_id", "run_id"],
        unique=False,
    )
    op.create_index(
        "ix_outline_sections_run_id_coverage_status",
        "outline_sections",
        ["run_id", "coverage_status"],
        unique=False,
    )

    # Existing questions become body sections one-for-one so old runs keep
    # their source and evidence links.
    op.execute(
        """
        INSERT INTO outline_sections (
            id, project_id, run_id, title, section_hash, role, purpose,
            search_terms, target_words, priority, coverage_status,
            created_at, updated_at
        )
        SELECT
            id, project_id, run_id, question, question_hash, 'body', '',
            '[]'::jsonb, NULL, priority, coverage_status,
            created_at, updated_at
        FROM research_questions
        """
    )

    op.add_column(
        "evidence_items",
        sa.Column("outline_section_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        "UPDATE evidence_items SET outline_section_id = research_question_id"
    )
    op.drop_index(
        "ix_evidence_items_research_question_id",
        table_name="evidence_items",
    )
    op.drop_constraint(
        op.f("fk_evidence_items_research_question_id_research_questions"),
        "evidence_items",
        type_="foreignkey",
    )
    op.drop_column("evidence_items", "research_question_id")
    op.create_foreign_key(
        op.f("fk_evidence_items_outline_section_id_outline_sections"),
        "evidence_items",
        "outline_sections",
        ["outline_section_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_evidence_items_outline_section_id",
        "evidence_items",
        ["outline_section_id"],
        unique=False,
    )

    op.add_column(
        "search_queries",
        sa.Column("outline_section_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        "UPDATE search_queries SET outline_section_id = research_question_id"
    )
    op.drop_constraint(
        op.f("fk_search_queries_research_question_id_research_questions"),
        "search_queries",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("uq_search_queries_run_id_round_index_provider_query_hash"),
        "search_queries",
        type_="unique",
    )
    op.drop_column("search_queries", "research_question_id")
    op.create_foreign_key(
        op.f("fk_search_queries_outline_section_id_outline_sections"),
        "search_queries",
        "outline_sections",
        ["outline_section_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        op.f(
            "uq_search_queries_run_id_round_index_provider_outline_section_id_query_hash"
        ),
        "search_queries",
        ["run_id", "round_index", "provider", "outline_section_id", "query_hash"],
    )

    op.drop_index(
        "ix_research_questions_run_id_coverage_status",
        table_name="research_questions",
    )
    op.drop_index(
        "ix_research_questions_project_id_run_id",
        table_name="research_questions",
    )
    op.drop_table("research_questions")


def downgrade() -> None:
    op.create_table(
        "research_questions",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("question_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "priority",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "coverage_status",
            sa.Enum(
                *_COVERAGE_STATUSES,
                name="question_coverage_status",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            server_default="open",
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "priority >= 0",
            name=op.f("ck_research_questions_priority_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["research_questions.id"],
            name=op.f("fk_research_questions_parent_id_research_questions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["research_projects.id"],
            name=op.f("fk_research_questions_project_id_research_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
            name=op.f("fk_research_questions_run_id_research_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_questions")),
        sa.UniqueConstraint(
            "run_id",
            "question_hash",
            name=op.f("uq_research_questions_run_id_question_hash"),
        ),
    )
    op.create_index(
        "ix_research_questions_project_id_run_id",
        "research_questions",
        ["project_id", "run_id"],
        unique=False,
    )
    op.create_index(
        "ix_research_questions_run_id_coverage_status",
        "research_questions",
        ["run_id", "coverage_status"],
        unique=False,
    )
    op.execute(
        """
        INSERT INTO research_questions (
            id, project_id, run_id, parent_id, question, question_hash,
            priority, coverage_status, created_at, updated_at
        )
        SELECT
            id, project_id, run_id, NULL, title, section_hash,
            priority, coverage_status, created_at, updated_at
        FROM outline_sections
        """
    )

    op.add_column(
        "evidence_items",
        sa.Column("research_question_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        "UPDATE evidence_items SET research_question_id = outline_section_id"
    )
    op.drop_index(
        "ix_evidence_items_outline_section_id",
        table_name="evidence_items",
    )
    op.drop_constraint(
        op.f("fk_evidence_items_outline_section_id_outline_sections"),
        "evidence_items",
        type_="foreignkey",
    )
    op.drop_column("evidence_items", "outline_section_id")
    op.create_foreign_key(
        op.f("fk_evidence_items_research_question_id_research_questions"),
        "evidence_items",
        "research_questions",
        ["research_question_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_evidence_items_research_question_id",
        "evidence_items",
        ["research_question_id"],
        unique=False,
    )

    op.add_column(
        "search_queries",
        sa.Column("research_question_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        "UPDATE search_queries SET research_question_id = outline_section_id"
    )
    op.drop_constraint(
        op.f(
            "uq_search_queries_run_id_round_index_provider_outline_section_id_query_hash"
        ),
        "search_queries",
        type_="unique",
    )
    op.drop_constraint(
        op.f("fk_search_queries_outline_section_id_outline_sections"),
        "search_queries",
        type_="foreignkey",
    )
    op.drop_column("search_queries", "outline_section_id")
    op.create_foreign_key(
        op.f("fk_search_queries_research_question_id_research_questions"),
        "search_queries",
        "research_questions",
        ["research_question_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        op.f("uq_search_queries_run_id_round_index_provider_query_hash"),
        "search_queries",
        ["run_id", "round_index", "provider", "query_hash"],
    )

    op.drop_index(
        "ix_outline_sections_run_id_coverage_status",
        table_name="outline_sections",
    )
    op.drop_index(
        "ix_outline_sections_project_id_run_id",
        table_name="outline_sections",
    )
    op.drop_table("outline_sections")
