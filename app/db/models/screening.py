import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base

json_type = JSON().with_variant(JSONB(), "postgresql")


class Screening(Base):
    __tablename__ = "screenings"
    __table_args__ = (
        Index("ix_screenings_status_created_at", "status", "created_at"),
        Index("ix_screenings_tenant_created_at", "tenant_id", "created_at"),
        Index(
            "uq_screenings_tenant_idempotency",
            "tenant_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[str] = mapped_column(
        String(128), nullable=False, default="default", index=True
    )
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    application_text: Mapped[str] = mapped_column(Text, nullable=False)
    job_title: Mapped[str] = mapped_column(String(160), nullable=False)
    required_skills: Mapped[list[str]] = mapped_column(
        json_type, nullable=False, default=list
    )

    experience_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    experience_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    experience_evidence: Mapped[list[str] | None] = mapped_column(
        json_type, nullable=True
    )
    skills_match: Mapped[str | None] = mapped_column(String(32), nullable=True)
    skills_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    skills_evidence: Mapped[list[str] | None] = mapped_column(json_type, nullable=True)
    skill_gaps: Mapped[list[str] | None] = mapped_column(json_type, nullable=True)
    recommendation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    recommendation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    human_review_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    # Audit-friendly provenance for every model-produced assessment.
    model_version: Mapped[str | None] = mapped_column(String(160), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    redaction_summary: Mapped[dict[str, int] | None] = mapped_column(
        json_type, nullable=True
    )

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
