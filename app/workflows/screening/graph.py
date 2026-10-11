from langgraph.graph import END, START, StateGraph

from app.schemas.assessment import Recommendation, SkillMatch
from app.workflows.screening.providers import (
    AssessmentProvider,
    OpenAIAssessmentProvider,
)
from app.workflows.screening.state import ScreeningState


def create_screening_graph(provider: AssessmentProvider | None = None):
    """Compile the workflow with dependency injection for deterministic offline tests."""
    assessor = provider or OpenAIAssessmentProvider()

    async def categorize_experience(state: ScreeningState) -> dict:
        assessment = await assessor.assess_experience(state["application_text"])
        return {"experience_assessment": assessment}

    async def assess_skillset(state: ScreeningState) -> dict:
        assessment = await assessor.assess_skills(
            state["application_text"], state["job_title"], state["required_skills"]
        )
        return {"skills_assessment": assessment}

    def route_app(state: ScreeningState) -> str:
        skill_assessment = state["skills_assessment"]
        if skill_assessment.match == SkillMatch.MATCH:
            return "hr_review"
        if state["experience_assessment"].level.value == "Senior-level":
            return "recruiter_review"
        return "manual_review"

    def hr_review(_: ScreeningState) -> dict:
        return {
            "recommendation": Recommendation.HR_REVIEW,
            "recommendation_reason": "Skills evidence supports prioritizing a human HR \
                review; no hiring decision has been made.",
            "human_review_required": True,
        }

    def recruiter_review(_: ScreeningState) -> dict:
        return {
            "recommendation": Recommendation.RECRUITER_REVIEW,
            "recommendation_reason": "Experience appears senior while required-skill evidence \
                is incomplete or not matched; recruiter review is suggested.",
            "human_review_required": True,
        }

    def manual_review(_: ScreeningState) -> dict:
        return {
            "recommendation": Recommendation.MANUAL_REVIEW,
            "recommendation_reason": "Evidence is insufficient or does not clearly support the \
                other review routes; qualified human review is required.",
            "human_review_required": True,
        }

    graph = StateGraph(ScreeningState)

    graph.add_node("categorize_experience", categorize_experience)
    graph.add_node("assess_skillset", assess_skillset)
    graph.add_node("hr_review", hr_review)
    graph.add_node("recruiter_review", recruiter_review)
    graph.add_node("manual_review", manual_review)
    graph.add_edge(START, "categorize_experience")
    graph.add_edge("categorize_experience", "assess_skillset")

    graph.add_conditional_edges(
        "assess_skillset",
        route_app,
        {
            "hr_review": "hr_review",
            "recruiter_review": "recruiter_review",
            "manual_review": "manual_review",
        },
    )
    graph.add_edge("hr_review", END)
    graph.add_edge("recruiter_review", END)
    graph.add_edge("manual_review", END)

    return graph.compile()
