"""Aggregate router for /api/v1.

Later phases attach their routers here: datasets (Phase 6), agent (Phase 7),
evaluations (Phase 9).
"""

from fastapi import APIRouter

from app.api.route import CommittingRoute
from app.api.v1 import admin, auth, conversations, documents, health

# Every endpoint commits its transaction before the response is sent, rather than
# in dependency teardown afterwards. See `app.api.route`.
api_router = APIRouter(route_class=CommittingRoute)
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(conversations.router)
api_router.include_router(conversations.provider_router)
api_router.include_router(documents.router)
api_router.include_router(admin.router)
