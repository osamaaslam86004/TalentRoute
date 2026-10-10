import logging
import uuid
from datetime import datetime

from app.core.config import get_settings
from app.db.models.screening import Screening
from app.repositories.screenings import get_screening
from app.schemas.assessment import ExperienceAssessment, SkillsAssessment
from app.workflows.screening.graph import create_screening_graph

logger = logging.getLogger(__name__)


async def process_screening(screening_id: uuid.UUID) -> None:
    settings = get_settings()
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        screening = await get_screening(session, screening_id, for_update=True)
        if screening is None:
            logger.warning(
                "screening task references missing record id=%s", screening_id
            )
            return
        if screening.status == "completed":
            return  # Idempotent for task redelivery after a successful commit.
        screening.status = "running"
        screening.error_code = None
        screening.attempt_count += 1
        await session.commit()
        application_text = screening.application_text
        job_title = screening.job_title
        required_skills = screening.required_skills

    try:
        graph = create_screening_graph()
        result = await graph.ainvoke(
            {
                "screening_id": str(screening_id),
                "application_text": application_text[: settings.max_application_chars],
                "job_title": job_title,
                "required_skills": required_skills,
            }
        )
        experience: ExperienceAssessment = result["experience_assessment"]
        skills: SkillsAssessment = result["skills_assessment"]
    except Exception as exc:
        # Store a stable code only. Provider exceptions can contain request details or sensitive text.
        logger.error(
            "screening processing failed id=%s error_type=%s",
            screening_id,
            type(exc).__name__,
        )
        async with AsyncSessionLocal() as session:
            row = await get_screening(session, screening_id, for_update=True)
            if row is not None:
                row.status = "failed"
                row.error_code = "screening_processing_failed"
                row.processed_at = datetime.now(datetime.UTC)
                await session.commit()
        raise

    async with AsyncSessionLocal() as session:
        row = await get_screening(session, screening_id, for_update=True)
        if row is None:
            return
        row.status = "completed"
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
        row.error_code = None
        row.processed_at = datetime.now(datetime.UTC)
        await session.commit()
