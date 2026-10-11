from fastapi import APIRouter

from app.api.v1.endpoints import audit, health, reviews, screenings

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(screenings.router)
api_router.include_router(reviews.router)
api_router.include_router(audit.router)
