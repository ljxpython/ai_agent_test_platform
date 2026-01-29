from __future__ import annotations

# pyright: reportMissingImports=false

import datetime
import enum
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import Enum as SAEnum


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    # Persist enum.value strings (lowercase) rather than enum.name (uppercase).
    return [str(member.value) for member in enum_cls]


class Base(DeclarativeBase):
    pass


class ConnectionKind(str, enum.Enum):
    SQLITE = "sqlite"


class RunAuditStatus(str, enum.Enum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ApprovalDecisionStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Project(Base):
    __tablename__ = "project"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.datetime.utcnow
    )

    connections: Mapped[list["Connection"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    run_audits: Mapped[list["RunAudit"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    approval_decisions: Mapped[list["ApprovalDecision"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Connection(Base):
    __tablename__ = "connection"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[ConnectionKind] = mapped_column(
        SAEnum(ConnectionKind, name="connection_kind", values_callable=_enum_values),
        nullable=False,
    )
    config_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.datetime.utcnow
    )

    project: Mapped[Project] = relationship(back_populates="connections")


class RunAudit(Base):
    __tablename__ = "run_audit"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    thread_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    parent_run_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    status: Mapped[RunAuditStatus] = mapped_column(
        SAEnum(RunAuditStatus, name="run_audit_status", values_callable=_enum_values),
        nullable=False,
        default=RunAuditStatus.STARTED,
    )
    sql_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.datetime.utcnow
    )

    project: Mapped[Project] = relationship(back_populates="run_audits")


class ApprovalDecision(Base):
    __tablename__ = "approval_decision"

    id: Mapped[uuid.UUID] = mapped_column(
        "approval_id", PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    thread_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_id_request: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    run_id_execute: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    status: Mapped[ApprovalDecisionStatus] = mapped_column(
        SAEnum(
            ApprovalDecisionStatus,
            name="approval_decision_status",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=ApprovalDecisionStatus.PENDING,
    )
    sql_proposed: Mapped[str | None] = mapped_column(Text, nullable=True)
    sql_approved: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.datetime.utcnow
    )

    project: Mapped[Project] = relationship(back_populates="approval_decisions")
