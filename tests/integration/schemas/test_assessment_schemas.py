"""Integration tests for assessment schemas validation, bounds, and evidence capping."""

import pytest
from pydantic import ValidationError

from app.schemas.assessment import (
    ExperienceAssessment,
    ExperienceLevel,
    Recommendation,
    SkillMatch,
    SkillsAssessment,
)


def test_experience_enum_values():
    assert ExperienceLevel.ENTRY_LEVEL == "Entry-level"
    assert ExperienceLevel.MID_LEVEL == "Mid-level"
    assert ExperienceLevel.SENIOR_LEVEL == "Senior-level"
    assert ExperienceLevel.INSUFFICIENT_EVIDENCE == "Insufficient evidence"


def test_skill_match_enum_values():
    assert SkillMatch.MATCH == "Match"
    assert SkillMatch.PARTIAL_MATCH == "Partial match"
    assert SkillMatch.NO_MATCH == "No match"
    assert SkillMatch.INSUFFICIENT_EVIDENCE == "Insufficient evidence"


def test_recommendation_enum_values():
    assert Recommendation.HR_REVIEW == "hr_review"
    assert Recommendation.RECRUITER_REVIEW == "recruiter_review"
    assert Recommendation.MANUAL_REVIEW == "manual_review"


def test_experience_assessment_valid():
    assessment = ExperienceAssessment(
        level=ExperienceLevel.SENIOR_LEVEL,
        confidence=0.95,
        evidence=["Worked 5 years in Python.", "Led cloud architecture."],
        rationale="Candidate demonstrates extensive senior engineering leadership.",
    )
    assert assessment.level == ExperienceLevel.SENIOR_LEVEL
    assert assessment.confidence == 0.95
    assert len(assessment.evidence) == 2


def test_experience_assessment_evidence_length_capping():
    long_string = "A" * 700
    assessment = ExperienceAssessment(
        level=ExperienceLevel.MID_LEVEL,
        confidence=0.8,
        evidence=[long_string],
        rationale="Valid rationale",
    )
    assert len(assessment.evidence[0]) == 500


def test_experience_assessment_invalid_confidence():
    with pytest.raises(ValidationError):
        ExperienceAssessment(
            level=ExperienceLevel.ENTRY_LEVEL,
            confidence=1.5,
            evidence=[],
            rationale="Confidence > 1.0 should fail",
        )

    with pytest.raises(ValidationError):
        ExperienceAssessment(
            level=ExperienceLevel.ENTRY_LEVEL,
            confidence=-0.1,
            evidence=[],
            rationale="Confidence < 0.0 should fail",
        )


def test_experience_assessment_forbidden_extra_fields():
    with pytest.raises(ValidationError):
        ExperienceAssessment(
            level=ExperienceLevel.SENIOR_LEVEL,
            confidence=0.9,
            rationale="Valid rationale",
            unapproved_extra_field="Should trigger extra=forbid",
        )


def test_skills_assessment_valid():
    assessment = SkillsAssessment(
        match=SkillMatch.MATCH,
        confidence=0.88,
        evidence=["Strong FastAPI usage"],
        gaps=["No Kubernetes experience"],
        rationale="Matches technical requirements well.",
    )
    assert assessment.match == SkillMatch.MATCH
    assert assessment.confidence == 0.88
    assert assessment.gaps == ["No Kubernetes experience"]


def test_skills_assessment_bounds_and_capping():
    long_evidence = "B" * 600
    assessment = SkillsAssessment(
        match=SkillMatch.PARTIAL_MATCH,
        confidence=0.5,
        evidence=[long_evidence],
        gaps=["Gap 1"],
        rationale="Partial match rationale",
    )
    assert len(assessment.evidence[0]) == 500


def test_skills_assessment_max_length_exceeded():
    with pytest.raises(ValidationError):
        SkillsAssessment(
            match=SkillMatch.NO_MATCH,
            confidence=0.2,
            evidence=["e"] * 13,  # max_length is 12
            rationale="Exceeds max evidence length",
        )

    with pytest.raises(ValidationError):
        SkillsAssessment(
            match=SkillMatch.NO_MATCH,
            confidence=0.2,
            gaps=["g"] * 21,  # max_length is 20
            rationale="Exceeds max gaps length",
        )


def test_skills_assessment_forbidden_extra_fields():
    with pytest.raises(ValidationError):
        SkillsAssessment(
            match=SkillMatch.MATCH,
            confidence=0.9,
            rationale="Rationale",
            unknown_attribute=123,
        )
