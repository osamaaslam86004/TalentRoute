from typing import NotRequired, TypedDict

from app.schemas.assessment import ExperienceAssessment, Recommendation, SkillsAssessment


class ScreeningState(TypedDict):
    screening_id: str
    application_text: str
    job_title: str
    required_skills: list[str]
    experience_assessment: NotRequired[ExperienceAssessment]
    skills_assessment: NotRequired[SkillsAssessment]
    recommendation: NotRequired[Recommendation]
    recommendation_reason: NotRequired[str]
    human_review_required: NotRequired[bool]
