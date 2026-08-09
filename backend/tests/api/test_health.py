from __future__ import annotations

from httpx import AsyncClient

PREFIX = "/api/v1/health"


async def test_liveness_reports_ok(client: AsyncClient) -> None:
    response = await client.get(f"{PREFIX}/live")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["environment"] == "testing"
    assert body["version"]


async def test_readiness_reports_every_dependency(client: AsyncClient) -> None:
    response = await client.get(f"{PREFIX}/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok", "redis": "ok"}


async def test_every_response_carries_a_request_id(client: AsyncClient) -> None:
    response = await client.get(f"{PREFIX}/live")
    assert response.headers["X-Request-ID"]


async def test_inbound_request_id_is_echoed_for_correlation(client: AsyncClient) -> None:
    response = await client.get(f"{PREFIX}/live", headers={"X-Request-ID": "trace-me-123"})
    assert response.headers["X-Request-ID"] == "trace-me-123"


async def test_oversized_request_id_is_truncated(client: AsyncClient) -> None:
    response = await client.get(f"{PREFIX}/live", headers={"X-Request-ID": "x" * 500})
    assert len(response.headers["X-Request-ID"]) == 64
