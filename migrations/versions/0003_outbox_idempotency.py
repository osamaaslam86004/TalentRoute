"""Add idempotent submissions and a transactional outbox for Celery delivery.

Revision ID: 0003_outbox_idempotency
Revises: 0002_enterprise
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_outbox_idempotency"
down_revision = "0002_enterprise"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "screenings", sa.Column("idempotency_key", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "screenings",
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "screenings",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_screenings_tenant_idempotency",
        "screenings",
        ["tenant_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "outbox_events",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("aggregate_id", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="pending"
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_outbox_events_tenant_id", "outbox_events", ["tenant_id"])
    op.create_index("ix_outbox_events_status", "outbox_events", ["status"])
    op.create_index(
        "ix_outbox_events_status_available", "outbox_events", ["status", "available_at"]
    )
    op.create_index(
        "ix_outbox_events_tenant_created", "outbox_events", ["tenant_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_events_tenant_created", table_name="outbox_events")
    op.drop_index("ix_outbox_events_status_available", table_name="outbox_events")
    op.drop_index("ix_outbox_events_status", table_name="outbox_events")
    op.drop_index("ix_outbox_events_tenant_id", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("uq_screenings_tenant_idempotency", table_name="screenings")
    op.drop_column("screenings", "processing_started_at")
    op.drop_column("screenings", "request_fingerprint")
    op.drop_column("screenings", "idempotency_key")
