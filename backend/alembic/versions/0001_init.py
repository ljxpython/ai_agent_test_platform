"""Initial schema (MVP tables).

Revision ID: 0001
Revises:
Create Date: 2026-01-26

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


connection_kind_enum = postgresql.ENUM(
    "sqlite",
    name="connection_kind",
)

run_audit_status_enum = postgresql.ENUM(
    "started",
    "succeeded",
    "failed",
    name="run_audit_status",
)

approval_decision_status_enum = postgresql.ENUM(
    "pending",
    "approved",
    "rejected",
    name="approval_decision_status",
)


def upgrade() -> None:
    """Upgrade schema."""

    op.create_table(
        "project",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "connection",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", connection_kind_enum, nullable=False),
        sa.Column("config_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_connection_project_id", "connection", ["project_id"], unique=False)

    op.create_table(
        "run_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("thread_id", sa.Text(), nullable=True),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("parent_run_id", sa.Text(), nullable=True),
        sa.Column("status", run_audit_status_enum, nullable=False),
        sa.Column("sql_text", sa.Text(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_run_audit_project_id", "run_audit", ["project_id"], unique=False)
    op.create_index("ix_run_audit_run_id", "run_audit", ["run_id"], unique=False)
    op.create_index(
        "ix_run_audit_parent_run_id", "run_audit", ["parent_run_id"], unique=False
    )

    op.create_table(
        "approval_decision",
        sa.Column(
            "approval_id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("thread_id", sa.Text(), nullable=True),
        sa.Column("run_id_request", sa.Text(), nullable=False),
        sa.Column("run_id_execute", sa.Text(), nullable=True),
        sa.Column("status", approval_decision_status_enum, nullable=False),
        sa.Column("sql_proposed", sa.Text(), nullable=True),
        sa.Column("sql_approved", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_approval_decision_project_id",
        "approval_decision",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_approval_decision_run_id_request",
        "approval_decision",
        ["run_id_request"],
        unique=False,
    )
    op.create_index(
        "ix_approval_decision_run_id_execute",
        "approval_decision",
        ["run_id_execute"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_table("approval_decision")
    op.drop_table("run_audit")
    op.drop_table("connection")
    op.drop_table("project")

    # Drop enum types explicitly (Postgres keeps them after dropping tables).
    op.execute("DROP TYPE IF EXISTS approval_decision_status")
    op.execute("DROP TYPE IF EXISTS run_audit_status")
    op.execute("DROP TYPE IF EXISTS connection_kind")
