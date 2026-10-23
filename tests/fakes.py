from app.schemas.assessment import (
    ExperienceAssessment,
    ExperienceLevel,
    SkillMatch,
    SkillsAssessment,
)


class FakeAssessmentProvider:
    def __init__(self, *, level=ExperienceLevel.MID_LEVEL, match=SkillMatch.MATCH):
        self.level = level
        self.match = match

    async def assess_experience(self, application_text: str) -> ExperienceAssessment:
        return ExperienceAssessment(
            level=self.level,
            confidence=0.8,
            evidence=["Example evidence from supplied application"],
            rationale="Based on explicit work history in the text.",
        )

    async def assess_skills(
        self, application_text: str, job_title: str, required_skills: list[str]
    ) -> SkillsAssessment:
        return SkillsAssessment(
            match=self.match,
            confidence=0.75,
            evidence=[f"Evidence relating to {required_skills[0]}"],
            gaps=[] if self.match == SkillMatch.MATCH else list(required_skills[1:]),
            rationale="Compared only role-related evidence with listed requirements.",
        )
