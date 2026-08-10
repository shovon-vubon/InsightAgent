"""Cited question answering over the knowledge base.

The Phase 3 vertical slice: a question goes in, and an answer comes back whose
every factual claim points at a chunk of a document the user actually uploaded.

Three properties are worth stating because they are what separate this from a
demo that concatenates search results into a prompt.

**Retrieving nothing is a supported outcome.** If no chunk clears the score floor,
the service returns an explicit "no supporting evidence" answer without calling
the model at all. That is faster, free, and — more importantly — it is the only
way the system can be honest about the limits of its corpus. A pipeline that
always calls the model always produces prose, and prose always reads as an answer.

**Citations are validated deterministically** before the answer is returned, so a
fabricated source cannot reach the client (see `app.rag.citations`).

**Usage is recorded on the same `llm_calls` table as chat**, including failures, so
the cost of a retrieval-augmented answer is comparable with an ungrounded one.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from decimal import Decimal

from app.core.config import Settings
from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.db.session import Database
from app.llm.base import Completion, LLMProvider, Message, Role
from app.llm.errors import LLMProviderError
from app.llm.pricing import estimate_cost_usd
from app.models.llm_call import LLMCall, LLMCallStatus
from app.prompts.registry import Prompt, get_prompt
from app.rag.citations import (
    Citation,
    build_evidence,
    render_context,
    validate_markers,
)
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.retrieval.dense import DenseRetriever
from app.repositories.document import DocumentRepository
from app.repositories.llm_call import LLMCallRepository

logger = get_logger(__name__)

RAG_PROMPT = "rag_answer"
MAX_QUESTION_LENGTH = 2_000

NO_EVIDENCE_ANSWER = (
    "The knowledge base does not contain material that answers this question. "
    "Nothing in the uploaded documents was close enough to the question to cite. "
    "Try rephrasing it, or upload a document that covers the topic."
)


@dataclass(frozen=True, slots=True)
class AnswerResult:
    answer: str
    citations: list[Citation]
    #: True when nothing cleared the score floor and no model was called.
    insufficient_evidence: bool
    retrieval_ms: float
    total_ms: float
    candidates_considered: int
    invalid_markers: list[int]
    provider: str
    model: str
    is_test_double: bool
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None


class KnowledgeService:
    def __init__(
        self,
        *,
        database: Database,
        settings: Settings,
        provider: LLMProvider,
        embedder: EmbeddingProvider,
    ) -> None:
        self._database = database
        self._settings = settings
        self._provider = provider
        self._embedder = embedder

    async def ask(
        self,
        *,
        user_id: uuid.UUID,
        question: str,
        document_ids: list[uuid.UUID] | None = None,
    ) -> AnswerResult:
        question = question.strip()
        if not question:
            raise ValidationError("The question must not be empty.")
        if len(question) > MAX_QUESTION_LENGTH:
            raise ValidationError(
                f"The question exceeds the {MAX_QUESTION_LENGTH} character limit."
            )

        started = time.perf_counter()

        async with self._database.session() as session:
            retriever = DenseRetriever(
                repository=DocumentRepository(session),
                embedder=self._embedder,
                top_k=self._settings.RETRIEVAL_TOP_K,
                score_floor=self._settings.RETRIEVAL_SCORE_FLOOR,
            )
            retrieval = await retriever.retrieve(
                question, user_id=user_id, document_ids=document_ids
            )

        if not retrieval.chunks:
            logger.info(
                "rag_no_evidence",
                candidates=retrieval.candidates,
                score_floor=self._settings.RETRIEVAL_SCORE_FLOOR,
            )
            return AnswerResult(
                answer=NO_EVIDENCE_ANSWER,
                citations=[],
                insufficient_evidence=True,
                retrieval_ms=retrieval.latency_ms,
                total_ms=round((time.perf_counter() - started) * 1000, 2),
                candidates_considered=retrieval.candidates,
                invalid_markers=[],
                provider=self._provider.name,
                model=self._provider.default_model,
                is_test_double=self._provider.name == "fake",
                input_tokens=0,
                output_tokens=0,
                cost_usd=None,
            )

        evidence = build_evidence(retrieval.chunks)
        prompt = get_prompt(RAG_PROMPT)
        rendered = prompt.render(
            context=render_context(evidence),
            valid_ids=", ".join(f"[{item.marker}]" for item in evidence),
            question=question,
        )

        try:
            completion = await self._provider.complete(
                [Message(role=Role.USER, content=rendered)],
                temperature=self._settings.LLM_TEMPERATURE,
                max_tokens=self._settings.LLM_MAX_TOKENS,
            )
        except LLMProviderError as exc:
            await self._record_failure(user_id=user_id, error=exc, prompt=prompt)
            raise

        outcome = validate_markers(completion.text, evidence)
        cost = estimate_cost_usd(completion.provider, completion.model, completion.usage)
        await self._record_success(user_id=user_id, completion=completion, prompt=prompt, cost=cost)

        return AnswerResult(
            answer=outcome.text,
            citations=outcome.citations,
            insufficient_evidence=False,
            retrieval_ms=retrieval.latency_ms,
            total_ms=round((time.perf_counter() - started) * 1000, 2),
            candidates_considered=retrieval.candidates,
            invalid_markers=outcome.invalid_markers,
            provider=completion.provider,
            model=completion.model,
            is_test_double=completion.provider == "fake",
            input_tokens=completion.usage.input_tokens,
            output_tokens=completion.usage.output_tokens,
            cost_usd=cost,
        )

    # --- telemetry ----------------------------------------------------------

    async def _record_success(
        self,
        *,
        user_id: uuid.UUID,
        completion: Completion,
        prompt: Prompt,
        cost: Decimal | None,
    ) -> None:
        async with self._database.session() as session:
            LLMCallRepository(session).record(
                LLMCall(
                    user_id=user_id,
                    provider=completion.provider,
                    model=completion.model,
                    prompt_name=prompt.name,
                    prompt_version=prompt.version,
                    prompt_checksum=prompt.checksum,
                    input_tokens=completion.usage.input_tokens,
                    output_tokens=completion.usage.output_tokens,
                    cached_input_tokens=completion.usage.cached_input_tokens,
                    cost_usd=cost,
                    latency_ms=completion.latency_ms,
                    retries=completion.retries,
                    streamed=False,
                    status=LLMCallStatus.SUCCEEDED,
                    finish_reason=completion.finish_reason,
                )
            )

    async def _record_failure(
        self, *, user_id: uuid.UUID, error: LLMProviderError, prompt: Prompt
    ) -> None:
        async with self._database.session() as session:
            LLMCallRepository(session).record(
                LLMCall(
                    user_id=user_id,
                    provider=self._provider.name,
                    model=self._provider.default_model,
                    prompt_name=prompt.name,
                    prompt_version=prompt.version,
                    prompt_checksum=prompt.checksum,
                    streamed=False,
                    status=LLMCallStatus.FAILED,
                    error_code=error.error_code,
                )
            )
