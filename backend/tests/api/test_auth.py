from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from app.api.v1.auth import REFRESH_COOKIE_NAME

AUTH = "/api/v1/auth"
EMAIL = "analyst@example.com"
PASSWORD = "a-sufficiently-long-password"


async def register(client: AsyncClient, **overrides: Any) -> Any:
    payload = {"email": EMAIL, "password": PASSWORD, "full_name": "Test Analyst"} | overrides
    return await client.post(f"{AUTH}/register", json=payload)


async def login(client: AsyncClient, **overrides: Any) -> Any:
    payload = {"email": EMAIL, "password": PASSWORD} | overrides
    return await client.post(f"{AUTH}/login", json=payload)


class TestRegistration:
    async def test_creates_an_account(self, client: AsyncClient) -> None:
        response = await register(client)

        assert response.status_code == 201
        body = response.json()
        assert body["email"] == EMAIL
        assert body["role"] == "USER"
        assert body["is_active"] is True
        assert "password" not in body
        assert "password_hash" not in body

    async def test_email_is_normalised_to_lowercase(self, client: AsyncClient) -> None:
        response = await register(client, email="Mixed.Case@Example.COM")
        assert response.json()["email"] == "mixed.case@example.com"

    async def test_duplicate_email_conflicts(self, client: AsyncClient) -> None:
        await register(client)
        response = await register(client)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    async def test_duplicate_detection_is_case_insensitive(self, client: AsyncClient) -> None:
        await register(client, email="Someone@Example.com")
        response = await register(client, email="someone@example.com")
        assert response.status_code == 409

    @pytest.mark.parametrize(
        ("field", "value"),
        [("password", "short"), ("email", "not-an-email"), ("password", "")],
    )
    async def test_invalid_payloads_are_rejected(
        self, client: AsyncClient, field: str, value: str
    ) -> None:
        response = await register(client, **{field: value})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    async def test_validation_errors_do_not_echo_the_password(self, client: AsyncClient) -> None:
        secret = "short"
        response = await register(client, password=secret)
        assert secret not in response.text


class TestLogin:
    async def test_returns_access_token_and_sets_refresh_cookie(self, client: AsyncClient) -> None:
        await register(client)
        response = await login(client)

        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["access_token"]
        assert body["expires_at"]

        cookie = response.cookies.get(REFRESH_COOKIE_NAME)
        assert cookie
        # The refresh token must never appear in the body.
        assert cookie not in response.text

    async def test_refresh_cookie_is_httponly_and_path_scoped(self, client: AsyncClient) -> None:
        await register(client)
        response = await login(client)

        header = next(
            value
            for key, value in response.headers.multi_items()
            if key.lower() == "set-cookie" and value.startswith(REFRESH_COOKIE_NAME)
        )
        assert "HttpOnly" in header
        assert "Path=/api/v1/auth" in header
        assert "SameSite=lax" in header.lower().replace("samesite=lax", "SameSite=lax")

    async def test_wrong_password_is_rejected(self, client: AsyncClient) -> None:
        await register(client)
        response = await login(client, password="the-wrong-password-entirely")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "authentication_failed"

    async def test_unknown_email_gives_the_same_error_as_a_wrong_password(
        self, client: AsyncClient
    ) -> None:
        """No user enumeration: both failures are indistinguishable to a client."""
        await register(client)
        unknown = await login(client, email="nobody@example.com")
        wrong_password = await login(client, password="the-wrong-password-entirely")

        assert unknown.status_code == wrong_password.status_code == 401
        assert unknown.json()["error"]["message"] == wrong_password.json()["error"]["message"]


class TestCurrentUser:
    async def test_returns_the_authenticated_user(self, client: AsyncClient) -> None:
        await register(client)
        token = (await login(client)).json()["access_token"]

        response = await client.get(f"{AUTH}/me", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert response.json()["email"] == EMAIL

    async def test_requires_a_token(self, client: AsyncClient) -> None:
        response = await client.get(f"{AUTH}/me")
        assert response.status_code == 401

    async def test_rejects_a_garbage_token(self, client: AsyncClient) -> None:
        response = await client.get(f"{AUTH}/me", headers={"Authorization": "Bearer not.a.jwt"})
        assert response.status_code == 401


class TestRefreshAndLogout:
    async def test_refresh_issues_a_new_access_token_and_rotates_the_cookie(
        self, client: AsyncClient
    ) -> None:
        await register(client)
        first_login = await login(client)
        original_cookie = first_login.cookies.get(REFRESH_COOKIE_NAME)

        response = await client.post(f"{AUTH}/refresh")

        assert response.status_code == 200
        assert response.json()["access_token"]
        rotated = response.cookies.get(REFRESH_COOKIE_NAME)
        assert rotated and rotated != original_cookie

    async def test_refresh_without_a_cookie_is_rejected(self, client: AsyncClient) -> None:
        response = await client.post(f"{AUTH}/refresh")
        assert response.status_code == 401

    async def test_the_rotated_token_keeps_working(self, client: AsyncClient) -> None:
        await register(client)
        await login(client)

        for _ in range(3):
            assert (await client.post(f"{AUTH}/refresh")).status_code == 200

    async def test_logout_revokes_the_session(self, client: AsyncClient) -> None:
        await register(client)
        await login(client)

        assert (await client.post(f"{AUTH}/logout")).status_code == 204
        assert (await client.post(f"{AUTH}/refresh")).status_code == 401

    async def test_logout_without_a_session_is_not_an_error(self, client: AsyncClient) -> None:
        assert (await client.post(f"{AUTH}/logout")).status_code == 204
