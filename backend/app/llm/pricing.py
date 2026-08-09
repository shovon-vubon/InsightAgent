"""Model pricing and cost computation (brief §28).

Prices are data, not code, and each entry records the date it was checked and
where it came from. They change; a stale number silently inflating a "cost per
query" figure in the README would be exactly the kind of fabricated metric this
project is meant to avoid.

**An unpriced model yields `None`, never a guess.** Callers store NULL and the
dashboards report coverage, so an unknown price is visible rather than quietly
wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.core.logging import get_logger
from app.llm.base import Usage

logger = get_logger(__name__)

MILLION = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class ModelPricing:
    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal | None
    checked_on: date
    source: str


def _usd(amount: str) -> Decimal:
    return Decimal(amount)


# Keyed by (provider, model). Verify against the provider's pricing page and bump
# `checked_on` when you do.
PRICING: dict[tuple[str, str], ModelPricing] = {
    ("openai", "gpt-4o-mini"): ModelPricing(
        input_per_million=_usd("0.15"),
        output_per_million=_usd("0.60"),
        cached_input_per_million=_usd("0.075"),
        checked_on=date(2026, 8, 9),
        source="https://openai.com/api/pricing/",
    ),
    ("openai", "gpt-4o"): ModelPricing(
        input_per_million=_usd("2.50"),
        output_per_million=_usd("10.00"),
        cached_input_per_million=_usd("1.25"),
        checked_on=date(2026, 8, 9),
        source="https://openai.com/api/pricing/",
    ),
    ("anthropic", "claude-haiku-4-5-20251001"): ModelPricing(
        input_per_million=_usd("1.00"),
        output_per_million=_usd("5.00"),
        cached_input_per_million=_usd("0.10"),
        checked_on=date(2026, 8, 9),
        source="https://www.anthropic.com/pricing",
    ),
    # Local inference has no per-token price. Recorded explicitly as zero so it is
    # distinguishable from "we do not know what this costs".
    ("ollama", "*"): ModelPricing(
        input_per_million=Decimal(0),
        output_per_million=Decimal(0),
        cached_input_per_million=Decimal(0),
        checked_on=date(2026, 8, 9),
        source="local inference — no marginal API cost",
    ),
    ("fake", "*"): ModelPricing(
        input_per_million=Decimal(0),
        output_per_million=Decimal(0),
        cached_input_per_million=Decimal(0),
        checked_on=date(2026, 8, 9),
        source="test double",
    ),
}


def get_pricing(provider: str, model: str) -> ModelPricing | None:
    return PRICING.get((provider, model)) or PRICING.get((provider, "*"))


def estimate_cost_usd(provider: str, model: str, usage: Usage) -> Decimal | None:
    """Cost in USD, or None when the model has no recorded price."""
    pricing = get_pricing(provider, model)
    if pricing is None:
        logger.warning("model_price_unknown", provider=provider, model=model)
        return None

    billable_input = max(usage.input_tokens - usage.cached_input_tokens, 0)
    cost = (
        Decimal(billable_input) * pricing.input_per_million
        + Decimal(usage.output_tokens) * pricing.output_per_million
    ) / MILLION

    if usage.cached_input_tokens and pricing.cached_input_per_million is not None:
        cost += (Decimal(usage.cached_input_tokens) * pricing.cached_input_per_million) / MILLION

    # Six decimal places: a single cheap call can cost well under a cent, and
    # rounding to cents would floor most calls to zero.
    return cost.quantize(Decimal("0.000001"))
