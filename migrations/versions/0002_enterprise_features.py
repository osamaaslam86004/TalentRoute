"""Add tenant isolation, model provenance, hash-chained audit events and reviewer feedback.

Revision ID: 0002_enterprise
Revises: 0001_initial
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_enterprise"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "screenings",
        sa.Column("tenant_id", sa.String(length=128), nullable=False, server_default="default"),
    )
    op.add_column("screenings", sa.Column("request_id", sa.String(length=64), nullable=True))
    op.add_column("screenings", sa.Column("model_version", sa.String(length=160), nullable=True))
    op.add_column("screenings", sa.Column("prompt_version", sa.String(length=80), nullable=True))
    op.add_column(
        "screenings",
        sa.Column("redaction_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_screenings_tenant_created_at", "screenings", ["tenant_id", "created_at"])
    op.create_index("ix_screenings_tenant_id", "screenings", ["tenant_id"])

    op.create_table(
        "audit_chain_heads",
        sa.Column("tenant_id", sa.String(length=128), primary_key=True, nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "last_hash",
            sa.String(length=64),
            nullable=False,
            server_default="0000000000000000000000000000000000000000000000000000000000000000",
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_audit_chain_heads"),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("previous_hash", sa.String(length=64), nullable=False),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.Column("actor_subject", sa.String(length=255), nullable=False),
        sa.Column("actor_roles", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        sa.Column("outcome", sa.String(length=40), nullable=False, server_default="success"),
        sa.Column(
            "metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
        sa.UniqueConstraint("tenant_id", "sequence_no", name="uq_audit_events_tenant_sequence_no"),
    )
    op.create_index("ix_audit_events_tenant_id", "audit_events", ["tenant_id"])
    op.create_index(
        "ix_audit_events_tenant_occurred_at", "audit_events", ["tenant_id", "occurred_at"]
    )
    op.create_index("ix_audit_events_resource", "audit_events", ["resource_type", "resource_id"])

    op.create_table(
        "reviewer_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("screening_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("reviewer_subject", sa.String(length=255), nullable=False),
        sa.Column("reviewer_roles", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("record_type", sa.String(length=32), nullable=False, server_default="review"),
        sa.Column("label", sa.String(length=40), nullable=True),
        sa.Column(
            "reason_codes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("rubric_version", sa.String(length=80), nullable=False),
        sa.Column("parent_feedback_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("appeal_outcome", sa.String(length=40), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["screening_id"],
            ["screenings.id"],
            ondelete="SET NULL",
            name="fk_reviewer_feedback_screening_id_screenings",
        ),
        sa.ForeignKeyConstraint(
            ["parent_feedback_id"],
            ["reviewer_feedback.id"],
            ondelete="RESTRICT",
            name="fk_reviewer_feedback_parent_feedback_id_reviewer_feedback",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reviewer_feedback"),
    )
    op.create_index("ix_reviewer_feedback_tenant_id", "reviewer_feedback", ["tenant_id"])
    op.create_index(
        "ix_reviewer_feedback_tenant_created", "reviewer_feedback", ["tenant_id", "created_at"]
    )
    op.create_index(
        "ix_reviewer_feedback_screening_created",
        "reviewer_feedback",
        ["screening_id", "created_at"],
    )

    # Audit log mutation protection at the database layer. Application DB role should have INSERT/SELECT,
    # not UPDATE/DELETE, in production; this trigger provides a second guardrail for accidental writes.
    op.execute("""
        CREATE OR REPLACE FUNCTION reject_audit_event_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events are append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION reject_audit_event_mutation()
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION protect_reviewer_feedback_history() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'reviewer_feedback is append-only';
            END IF;
            -- Permit only the FK's ON DELETE SET NULL action; all reviewer/appeal content stays immutable.
            IF OLD.screening_id IS NOT NULL AND NEW.screening_id IS NULL
               AND (to_jsonb(OLD) - 'screening_id') = (to_jsonb(NEW) - 'screening_id') THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'reviewer_feedback is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_reviewer_feedback_append_only
        BEFORE UPDATE OR DELETE ON reviewer_feedback
        FOR EACH ROW EXECUTE FUNCTION protect_reviewer_feedback_history()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_reviewer_feedback_append_only ON reviewer_feedback")
    op.execute("DROP FUNCTION IF EXISTS protect_reviewer_feedback_history()")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS reject_audit_event_mutation()")
    op.drop_index("ix_reviewer_feedback_screening_created", table_name="reviewer_feedback")
    op.drop_index("ix_reviewer_feedback_tenant_created", table_name="reviewer_feedback")
    op.drop_index("ix_reviewer_feedback_tenant_id", table_name="reviewer_feedback")
    op.drop_table("reviewer_feedback")
    op.drop_index("ix_audit_events_resource", table_name="audit_events")
    op.drop_index("ix_audit_events_tenant_occurred_at", table_name="audit_events")
    op.drop_index("ix_audit_events_tenant_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("audit_chain_heads")
    op.drop_index("ix_screenings_tenant_id", table_name="screenings")
    op.drop_index("ix_screenings_tenant_created_at", table_name="screenings")
    op.drop_column("screenings", "redaction_summary")
    op.drop_column("screenings", "prompt_version")
    op.drop_column("screenings", "model_version")
    op.drop_column("screenings", "request_id")
    op.drop_column("screenings", "tenant_id")
