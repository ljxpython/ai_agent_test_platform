"""Add v1 platform tables (projects/threads/runs/events/approvals/store).

Revision ID: 0002
Revises: 0001
Create Date: 2026-01-29
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "threads",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "thread_id"),
    )

    op.create_table(
        "runs",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("assistant_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["project_id", "thread_id"],
            ["threads.project_id", "threads.thread_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", "run_id"),
    )

    op.create_index(
        "ix_runs_project_thread_idempotency",
        "runs",
        ["project_id", "thread_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_runs_project_approval",
        "runs",
        ["project_id", "approval_id"],
        unique=True,
        postgresql_where=sa.text("approval_id IS NOT NULL"),
    )

    op.create_table(
        "run_seq_counters",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("next_seq", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "run_id"],
            ["runs.project_id", "runs.run_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", "run_id"),
    )

    op.create_table(
        "run_events",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "run_id"],
            ["runs.project_id", "runs.run_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", "run_id", "seq"),
    )

    op.create_index(
        "ix_run_events_project_run_dedupe",
        "run_events",
        ["project_id", "run_id", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text("dedupe_key IS NOT NULL"),
    )
    op.create_index("ix_run_events_ts", "run_events", ["ts"], unique=False)

    op.create_table(
        "approvals",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id_request", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id_execute", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("sql_proposed", sa.Text(), nullable=True),
        sa.Column("sql_approved", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["project_id", "thread_id"],
            ["threads.project_id", "threads.thread_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "run_id_request"],
            ["runs.project_id", "runs.run_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", "approval_id"),
    )

    op.create_table(
        "store_items",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("namespace", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "namespace", "key"),
    )


def downgrade() -> None:
    op.drop_table("store_items")
    op.drop_table("approvals")
    op.drop_index("ix_run_events_ts", table_name="run_events")
    op.drop_index("ix_run_events_project_run_dedupe", table_name="run_events")
    op.drop_table("run_events")
    op.drop_table("run_seq_counters")
    op.drop_index("ix_runs_project_approval", table_name="runs")
    op.drop_index("ix_runs_project_thread_idempotency", table_name="runs")
    op.drop_table("runs")
    op.drop_table("threads")
    op.drop_table("projects")
