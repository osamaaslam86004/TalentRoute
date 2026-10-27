import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base

json_type = JSON().with_variant(JSONB(), "postgresql")


class ReviewerFeedback(Base):
    """Append-only reviewer/appeal event kept separate from the model-generated suggestion."""

    __tablename__ = "reviewer_feedback"
    __table_args__ = (
        Index("ix_reviewer_feedback_tenant_created", "tenant_id", "created_at"),
        Index("ix_reviewer_feedback_screening_created", "screening_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    screening_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("screenings.id", ondelete="SET NULL"),
        nullable=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    reviewer_subject: Mapped[str] = mapped_column(String(255), nullable=False)

    reviewer_roles: Mapped[list[str]] = mapped_column(
        json_type, nullable=False, default=list
    )

    record_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="review"
    )
    label: Mapped[str | None] = mapped_column(String(40), nullable=True)

    reason_codes: Mapped[list[str]] = mapped_column(
        json_type, nullable=False, default=list
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    rubric_version: Mapped[str] = mapped_column(String(80), nullable=False)

    parent_feedback_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reviewer_feedback.id", ondelete="RESTRICT"),
        nullable=True,
    )
    appeal_outcome: Mapped[str | None] = mapped_column(String(40), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
