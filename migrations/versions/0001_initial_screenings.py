"""Create initial screenings table.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "screenings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("application_text", sa.Text(), nullable=False),
        sa.Column("job_title", sa.String(length=160), nullable=False),
        sa.Column("required_skills", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("experience_level", sa.String(length=32), nullable=True),
        sa.Column("experience_confidence", sa.Float(), nullable=True),
        sa.Column("experience_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("skills_match", sa.String(length=32), nullable=True),
        sa.Column("skills_confidence", sa.Float(), nullable=True),
        sa.Column("skills_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("skill_gaps", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("recommendation", sa.String(length=32), nullable=True),
        sa.Column("recommendation_reason", sa.Text(), nullable=True),
        sa.Column("human_review_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_screenings_status_created_at", "screenings", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_screenings_status_created_at", table_name="screenings")
    op.drop_table("screenings")
