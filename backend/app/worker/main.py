"""arq worker: runs document ingestion off the request path.

Chosen over Celery because it is async-native — the ingestion pipeline is `async`
end to end, and Celery would mean either a sync rewrite or an event loop bolted
into a worker process. arq is also one dependency and one Redis connection rather
than a broker plus a result backend.

The worker builds its **own** database engine, Redis client, and embedding
provider. It shares code with the API but not process state, which is what lets it
be scaled or restarted independently — and is why `DocumentIngestionService` takes
its collaborators as arguments rather than reaching for `app.state`.

Retries are bounded and only transient failures raise. A permanent failure marks
the document `FAILED` and returns normally, so arq does not retry it: a scanned
PDF will not acquire a text layer on the third attempt.
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from arq.connections import RedisSettings

from app.cache.redis import create_redis
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import create_database
from app.rag.embeddings.cache import CachingEmbeddingProvider
from app.rag.embeddings.factory import create_embedding_provider
from app.rag.storage import DocumentStore
from app.services.ingestion import DocumentIngestionService

logger = get_logger(__name__)

#: Attempts per job, including the first. Transient embedding failures get a
#: second and third chance; permanent ones never raise and so never retry.
MAX_TRIES = 3
#: A large PDF on a slow embedding provider is legitimately slow.
JOB_TIMEOUT_SECONDS = 900


async def ingest_document(ctx: dict[str, Any], document_id: str) -> None:
    service: DocumentIngestionService = ctx["ingestion_service"]
    await service.process(uuid.UUID(document_id))


async def startup(ctx: dict[str, Any]) -> None:
    settings: Settings = get_settings()
    configure_logging(settings)

    database = create_database(settings)
    redis = create_redis(settings)
    store = DocumentStore(settings.storage_path)
    store.ensure_ready()

    embedder = CachingEmbeddingProvider(
        create_embedding_provider(settings),
        redis,
        ttl_seconds=settings.EMBEDDING_CACHE_TTL_SECONDS,
    )

    ctx["database"] = database
    ctx["redis"] = redis
    ctx["embedder"] = embedder
    ctx["ingestion_service"] = DocumentIngestionService(
        database=database,
        settings=settings,
        store=store,
        embedder=embedder,
        queue=None,  # the worker consumes the queue; it does not enqueue
    )

    logger.info(
        "worker_started",
        embedding_provider=embedder.provider_label,
        embedding_model=embedder.default_model,
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["embedder"].aclose()
    await ctx["database"].dispose()
    await ctx["redis"].aclose()
    logger.info("worker_stopped")


class WorkerSettings:
    """arq entry point: `arq app.worker.main.WorkerSettings`."""

    functions: ClassVar[list[Any]] = [ingest_document]
    on_startup = startup
    on_shutdown = shutdown
    max_tries = MAX_TRIES
    job_timeout = JOB_TIMEOUT_SECONDS
    #: Modest: each job holds a database connection and an embedding request in
    #: flight, and the local Ollama tier is the bottleneck well before this is.
    max_jobs = 4
    # arq reads this as a value, not a callable.
    redis_settings = RedisSettings.from_dsn(get_settings().REDIS_URL)
