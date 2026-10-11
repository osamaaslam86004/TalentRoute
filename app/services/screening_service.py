import logging
import uuid
from datetime import UTC, datetime

from app.audit.service import record_audit_event
from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.privacy.pii_redaction import redact_pii
from app.repositories.screenings import get_screening
from app.schemas.assessment import ExperienceAssessment, SkillsAssessment
from app.workflows.screening.graph import create_screening_graph
from app.workflows.screening.prompts import PROMPT_VERSION

logger = logging.getLogger(__name__)


async def process_screening(
    screening_id: uuid.UUID, *, allow_reclaim_running: bool = False
) -> None:
    """Run the graph over minimized text and persist provenance plus a privacy-safe audit event."""

    settings = get_settings()

    async with AsyncSessionLocal() as session:
        screening = await get_screening(session, screening_id, for_update=True)

        if screening is None:
            logger.warning(
                "screening task references missing record id=%s", screening_id
            )
            return

        if screening.status == "completed":
            return  # Idempotent for task redelivery after a successful commit.

        now = datetime.now(UTC)

        if (
            screening.status == "running"
            and screening.processing_started_at is not None
        ):
            age_seconds = (now - screening.processing_started_at).total_seconds()

            if (
                not allow_reclaim_running
                and age_seconds < settings.stale_processing_seconds
            ):
                logger.info("duplicate screening task ignored id=%s", screening_id)
                return

            if allow_reclaim_running:
                logger.warning(
                    "reclaiming redelivered running task id=%s", screening_id
                )

            else:
                logger.warning(
                    "reclaiming stale screening task id=%s age_seconds=%s",
                    screening_id,
                    int(age_seconds),
                )

        screening.status = "running"
        screening.processing_started_at = now
        screening.error_code = None
        screening.attempt_count += 1
        screening.model_version = settings.openai_model
        screening.prompt_version = PROMPT_VERSION
        raw_application_text = screening.application_text
        job_title = screening.job_title
        required_skills = screening.required_skills
        await session.commit()

    redaction_counts: dict[str, int] = {}
    redacted_categories: list[str] = []

    try:
        redaction = redact_pii(
            raw_application_text,
            enabled=settings.pii_redaction_enabled,
            policy=settings.pii_redaction_policy,
        )

        redaction_counts = redaction.counts
        redacted_categories = redaction.categories

        graph = create_screening_graph()

        result = await graph.ainvoke(
            {
                "screening_id": str(screening_id),
                "application_text": redaction.text[: settings.max_application_chars],
                "job_title": job_title,
                "required_skills": required_skills,
            }
        )

        experience: ExperienceAssessment = result["experience_assessment"]
        skills: SkillsAssessment = result["skills_assessment"]

    except Exception as exc:
        # Store a stable error code only; provider exceptions can contain request details or sensitive text.
        logger.error(
            "screening processing failed id=%s error_type=%s",
            screening_id,
            type(exc).__name__,
        )

        async with AsyncSessionLocal() as session:
            row = await get_screening(session, screening_id, for_update=True)

            if row is not None:
                row.status = "failed"
                row.processing_started_at = None
                row.error_code = "screening_processing_failed"
                row.redaction_summary = redaction_counts
                row.processed_at = datetime.now(UTC)

                await record_audit_event(
                    session,
                    principal=None,
                    tenant_id=row.tenant_id,
                    request_id=row.request_id,
                    action="screening.processing_failed",
                    resource_type="screening",
                    resource_id=str(row.id),
                    outcome="failure",
                    metadata={
                        "attempt_count": row.attempt_count,
                        "model_version": settings.openai_model,
                        "prompt_version": PROMPT_VERSION,
                        "redacted_categories": redacted_categories,
                    },
                )
                await session.commit()
        raise

    async with AsyncSessionLocal() as session:
        row = await get_screening(session, screening_id, for_update=True)

        if row is None:
            return

        row.status = "completed"
        row.processing_started_at = None
        row.experience_level = experience.level.value
        row.experience_confidence = experience.confidence
        row.experience_evidence = experience.evidence
        row.skills_match = skills.match.value
        row.skills_confidence = skills.confidence
        row.skills_evidence = skills.evidence
        row.skill_gaps = skills.gaps
        row.recommendation = result["recommendation"].value
        row.recommendation_reason = result["recommendation_reason"]
        row.human_review_required = True
        row.model_version = settings.openai_model
        row.prompt_version = PROMPT_VERSION
        row.redaction_summary = redaction.counts
        row.error_code = None
        row.processed_at = datetime.now(UTC)

        await record_audit_event(
            session,
            principal=None,
            tenant_id=row.tenant_id,
            request_id=row.request_id,
            action="screening.processing_completed",
            resource_type="screening",
            resource_id=str(row.id),
            metadata={
                "status": row.status,
                "model_version": row.model_version,
                "prompt_version": row.prompt_version,
                "redacted_categories": redaction.categories,
                "attempt_count": row.attempt_count,
            },
        )
        await session.commit()
