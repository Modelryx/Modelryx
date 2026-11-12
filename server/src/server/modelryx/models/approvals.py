from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from modelryx.core.db import Base
from modelryx.models.common import utcnow


class ApprovalReviewerGroup(Base):
    __tablename__ = "approval_reviewer_groups"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApprovalReviewerGroupMember(Base):
    __tablename__ = "approval_reviewer_group_members"

    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("approval_reviewer_groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApprovalReviewerDelegation(Base):
    __tablename__ = "approval_reviewer_delegations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    grantor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    delegate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        CheckConstraint("grantor_id <> delegate_id", name="ck_approval_delegation_distinct_users"),
        CheckConstraint("ends_at > starts_at", name="ck_approval_delegation_time_window"),
    )


class RevisionApprovalRequest(Base):
    """A review decision bound to one immutable revision content snapshot."""

    __tablename__ = "revision_approval_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revisions.id"), index=True
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revision_content_snapshots.id"), index=True
    )
    reviewer_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approval_reviewer_groups.id")
    )
    reviewer_group_name: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    policy_version: Mapped[str] = mapped_column(String(32), default="1")
    policy_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    required_approvals: Mapped[int] = mapped_column(default=1)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    request_comment: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decision_comment: Mapped[str | None] = mapped_column(Text)
    warnings_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED', 'WITHDRAWN', 'STALE', 'EXPIRED')",
            name="ck_revision_approval_requests_status",
        ),
        CheckConstraint(
            "decided_by IS NULL OR decided_by != requested_by",
            name="ck_revision_approval_requests_separation_of_duties",
        ),
        CheckConstraint(
            "required_approvals BETWEEN 1 AND 5",
            name="ck_revision_approval_requests_required_approvals",
        ),
    )


class RevisionApprovalDecision(Base):
    __tablename__ = "revision_approval_decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    approval_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("revision_approval_requests.id", ondelete="CASCADE"),
        index=True,
    )
    decision: Mapped[str] = mapped_column(String(24))
    comment: Mapped[str] = mapped_column(Text)
    warnings_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    decided_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        CheckConstraint(
            "decision IN ('APPROVED', 'REJECTED')",
            name="ck_revision_approval_decisions_decision",
        ),
        UniqueConstraint(
            "approval_request_id",
            "decided_by",
            name="uq_revision_approval_decisions_reviewer",
        ),
    )
