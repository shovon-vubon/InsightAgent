"""Authentication endpoints.

The refresh token travels only in an HttpOnly, SameSite=Lax cookie scoped to this
router's path. The frontend reaches the API through a same-origin Next.js rewrite,
which is what lets `SameSite=Lax` work in development over plain HTTP while still
keeping the token out of reach of JavaScript.
"""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Request, Response, status

from app.api.deps import AppSettings, CurrentUser, DbSession
from app.core.config import Settings
from app.core.exceptions import AuthenticationError
from app.schemas.auth import AccessToken, LoginRequest, RegisterRequest, UserRead
from app.services.auth import AuthService, IssuedTokens

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE_NAME = "insightagent_refresh"


def _cookie_path(settings: Settings) -> str:
    return f"{settings.API_V1_PREFIX}/auth"


def _set_refresh_cookie(response: Response, settings: Settings, tokens: IssuedTokens) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=tokens.refresh_token,
        max_age=settings.REFRESH_TOKEN_TTL_SECONDS,
        path=_cookie_path(settings),
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=_cookie_path(settings),
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
    )


def _client_metadata(request: Request) -> tuple[str | None, str | None]:
    """User agent and client IP for the token audit trail.

    Behind a reverse proxy this records the proxy's address unless the ASGI server
    is run with proxy headers enabled — deliberately not trusting `X-Forwarded-For`
    by default, since it is client-controlled.
    """
    user_agent = request.headers.get("user-agent")
    ip_address = request.client.host if request.client else None
    return (user_agent[:256] if user_agent else None), ip_address


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
)
async def register(payload: RegisterRequest, session: DbSession, settings: AppSettings) -> UserRead:
    user = await AuthService(session, settings).register(
        email=payload.email, password=payload.password, full_name=payload.full_name
    )
    return UserRead.model_validate(user)


@router.post("/login", response_model=AccessToken, summary="Exchange credentials for tokens")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
) -> AccessToken:
    service = AuthService(session, settings)
    user = await service.authenticate(email=payload.email, password=payload.password)
    user_agent, ip_address = _client_metadata(request)
    tokens = await service.issue_tokens(user, user_agent=user_agent, ip_address=ip_address)
    _set_refresh_cookie(response, settings, tokens)
    return AccessToken(access_token=tokens.access_token, expires_at=tokens.access_expires_at)


@router.post("/refresh", response_model=AccessToken, summary="Rotate the refresh token")
async def refresh(
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    insightagent_refresh: str | None = Cookie(default=None),
) -> AccessToken:
    if not insightagent_refresh:
        raise AuthenticationError("Missing refresh token.")

    user_agent, ip_address = _client_metadata(request)
    tokens = await AuthService(session, settings).rotate_refresh_token(
        insightagent_refresh, user_agent=user_agent, ip_address=ip_address
    )
    _set_refresh_cookie(response, settings, tokens)
    return AccessToken(access_token=tokens.access_token, expires_at=tokens.access_expires_at)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Revoke the session")
async def logout(
    response: Response,
    session: DbSession,
    settings: AppSettings,
    insightagent_refresh: str | None = Cookie(default=None),
) -> None:
    await AuthService(session, settings).logout(insightagent_refresh)
    _clear_refresh_cookie(response, settings)


@router.get("/me", response_model=UserRead, summary="Current authenticated user")
async def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)
