"""Aggregate router for /api/v1.

Later phases attach their routers here: documents (Phase 3), datasets (Phase 6),
agent (Phase 7), evaluations (Phase 9).
"""

from fastapi import APIRouter

from app.api.v1 import admin, auth, conversations, health

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(conversations.router)
api_router.include_router(conversations.provider_router)
api_router.include_router(admin.router)
