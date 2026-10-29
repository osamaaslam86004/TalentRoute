"""Fixtures and test data generators for offline evaluation and pairwise blinding tests."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
def sample_gold_dataset() -> list[dict[str, Any]]:
    """Return a representative gold-labeled evaluation dataset."""
    return [
        {
            "case_id": "case-001",
            "slice": "backend_engineering",
            "source_text": "Experienced Python and FastAPI developer with 5 years in backend systems. Microservices and PostgreSQL knowledge.",
            "gold": {
                "experience_level": "Senior-level",
                "skills_match": "Match",
                "recommendation": "recruiter_review",
            },
        },
        {
            "case_id": "case-002",
            "slice": "frontend_engineering",
            "source_text": "Junior developer with basic React and JavaScript experience. Looking for entry level roles.",
            "gold": {
                "experience_level": "Entry-level",
                "skills_match": "Partial match",
                "recommendation": "hr_review",
            },
        },
        {
            "case_id": "case-003",
            "slice": "backend_engineering",
            "source_text": "Software engineer with Mid-level experience in Python, AWS, and Docker.",
            "gold": {
                "experience_level": "Mid-level",
                "skills_match": "Match",
                "recommendation": "recruiter_review",
            },
        },
    ]


@pytest.fixture
def sample_prediction_rows() -> list[dict[str, Any]]:
    """Return matching prediction rows with schema-valid responses and operational metrics."""
    return [
        {
            "case_id": "case-001",
            "model_version": "gpt-4o-2024-08-06",
            "prompt_version": "v1.2.0",
            "latency_ms": 1200.0,
            "estimated_cost_usd": 0.005,
            "prediction": {
                "experience_level": "Senior-level",
                "skills_match": "Match",
                "recommendation": "recruiter_review",
                "experience_evidence": ["5 years in backend systems"],
                "skills_evidence": ["Python and FastAPI developer"],
                "skill_gaps": [],
                "experience_confidence": 0.95,
                "skills_confidence": 0.90,
                "schema_valid": True,
            },
            "judge_assessment": {
                "experience_level": "Senior-level",
                "skills_match": "Match",
                "recommendation": "recruiter_review",
            },
            "human_review_assessment": {
                "experience_level": "Senior-level",
                "skills_match": "Match",
                "recommendation": "recruiter_review",
            },
        },
        {
            "case_id": "case-002",
            "model_version": "gpt-4o-2024-08-06",
            "prompt_version": "v1.2.0",
            "latency_ms": 800.0,
            "estimated_cost_usd": 0.003,
            "prediction": {
                "experience_level": "Entry-level",
                "skills_match": "Partial match",
                "recommendation": "hr_review",
                "experience_evidence": ["Junior developer"],
                "skills_evidence": ["basic React and JavaScript"],
                "skill_gaps": ["TypeScript"],
                "experience_confidence": 0.85,
                "skills_confidence": 0.80,
                "schema_valid": True,
            },
            "judge_assessment": {
                "experience_level": "Entry-level",
                "skills_match": "Partial match",
                "recommendation": "hr_review",
            },
            "human_review_assessment": {
                "experience_level": "Entry-level",
                "skills_match": "Partial match",
                "recommendation": "hr_review",
            },
        },
        {
            "case_id": "case-003",
            "model_version": "gpt-4o-2024-08-06",
            "prompt_version": "v1.2.0",
            "latency_ms": 1000.0,
            "estimated_cost_usd": 0.004,
            "prediction": {
                "experience_level": "Mid-level",
                "skills_match": "Match",
                "recommendation": "recruiter_review",
                "experience_evidence": ["Mid-level experience"],
                "skills_evidence": ["Python, AWS, and Docker"],
                "skill_gaps": [],
                "experience_confidence": 0.90,
                "skills_confidence": 0.88,
                "schema_valid": True,
            },
            "judge_assessment": {
                "experience_level": "Mid-level",
                "skills_match": "Match",
                "recommendation": "recruiter_review",
            },
            "human_review_assessment": {
                "experience_level": "Mid-level",
                "skills_match": "Match",
                "recommendation": "recruiter_review",
            },
        },
    ]


@pytest.fixture
def baseline_eval_report() -> dict[str, Any]:
    """Return a baseline evaluation report dictionary for release gate comparison."""
    return {
        "report_version": "screening-evaluation-report-v1",
        "model_version": "gpt-4o-2024-08-06",
        "prompt_version": "v1.2.0",
        "metrics": {
            "schema_validity_rate": 1.0,
            "experience_accuracy": {"accuracy": 0.90},
            "skills_accuracy": {"accuracy": 0.88},
            "recommendation_accuracy": {"accuracy": 0.92},
            "mean_latency_ms": 1000.0,
            "mean_estimated_cost_usd": 0.004,
        },
        "slices": {
            "backend": {
                "sample_count": 10,
                "experience_accuracy": 0.90,
                "skills_accuracy": 0.88,
                "recommendation_accuracy": 0.92,
            },
            "frontend": {
                "sample_count": 10,
                "experience_accuracy": 0.88,
                "skills_accuracy": 0.86,
                "recommendation_accuracy": 0.90,
            },
        },
        "prediction_distributions": {
            "experience_level": {"Entry-level": 3, "Mid-level": 4, "Senior-level": 3},
            "skills_match": {"Match": 6, "Partial match": 3, "No match": 1},
            "recommendation": {"recruiter_review": 7, "hr_review": 3},
        },
    }
