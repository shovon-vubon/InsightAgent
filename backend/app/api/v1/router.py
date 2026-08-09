"""Aggregate router for /api/v1.

Later phases attach their routers here: chat and conversations (Phase 2), documents
(Phase 3), agent (Phase 7), datasets (Phase 6), evaluations and admin (Phases 9-11).
"""

from fastapi import APIRouter

from app.api.v1 import auth, health

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
