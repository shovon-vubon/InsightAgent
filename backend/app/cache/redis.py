"""Redis client and a thin namespaced cache wrapper.

Deliberately minimal for Phase 1: Redis is wired up and health-checked, but nothing
caches yet. Embedding and retrieval caching arrive in Phases 3-4, rate limiting in
Phase 12 (brief §6 — do not use Redis unnecessarily).
"""

from __future__ import annotations

import json
from typing import Any, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def create_redis(settings: Settings) -> Redis:
    # `from_url` is untyped in redis-py 5.x, which arq pins us to (arq requires
    # redis<6). The cast keeps the annotation honest instead of leaking `Any`
    # into every caller; drop it if the pin is ever lifted.
    client: Redis = cast(
        "Redis",
        Redis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            health_check_interval=30,
        ),
    )
    return client


class Cache:
    """Namespaced JSON cache.

    A cache miss and a cache backend failure both return ``None``: Redis being down
    must degrade latency, never correctness.
    """

    def __init__(self, redis: Redis, namespace: str = "insightagent") -> None:
        self._redis = redis
        self._namespace = namespace

    def _key(self, key: str) -> str:
        return f"{self._namespace}:{key}"

    async def get_json(self, key: str) -> Any | None:
        try:
            raw = await self._redis.get(self._key(key))
        except RedisError:
            logger.warning("cache_unavailable", operation="get")
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("cache_corrupt_entry", key=key)
            return None

    async def set_json(self, key: str, value: Any, *, ttl_seconds: int) -> bool:
        try:
            await self._redis.set(self._key(key), json.dumps(value), ex=ttl_seconds)
        except (RedisError, TypeError):
            logger.warning("cache_unavailable", operation="set")
            return False
        return True

    async def delete(self, key: str) -> bool:
        try:
            await self._redis.delete(self._key(key))
        except RedisError:
            return False
        return True

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except RedisError:
            return False
