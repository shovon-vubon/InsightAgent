"""Cost arithmetic. Wrong numbers here become wrong numbers in the README."""

from __future__ import annotations

from decimal import Decimal

from app.llm.base import Usage
from app.llm.pricing import PRICING, estimate_cost_usd, get_pricing


class TestCostEstimation:
    def test_priced_model_computes_from_the_table(self) -> None:
        # gpt-4o-mini: $0.15/M input, $0.60/M output.
        cost = estimate_cost_usd(
            "openai", "gpt-4o-mini", Usage(input_tokens=1_000_000, output_tokens=1_000_000)
        )
        assert cost == Decimal("0.750000")

    def test_small_call_keeps_sub_cent_precision(self) -> None:
        cost = estimate_cost_usd(
            "openai", "gpt-4o-mini", Usage(input_tokens=1_000, output_tokens=500)
        )
        # 1000 * 0.15/1e6 + 500 * 0.60/1e6 = 0.00015 + 0.0003
        assert cost == Decimal("0.000450")

    def test_cached_tokens_are_billed_at_the_cached_rate(self) -> None:
        full = estimate_cost_usd("openai", "gpt-4o-mini", Usage(input_tokens=1_000_000))
        half_cached = estimate_cost_usd(
            "openai",
            "gpt-4o-mini",
            Usage(input_tokens=1_000_000, cached_input_tokens=1_000_000),
        )
        assert full == Decimal("0.150000")
        # Entirely cached input bills at $0.075/M.
        assert half_cached == Decimal("0.075000")

    def test_unknown_model_returns_none_rather_than_guessing(self) -> None:
        assert estimate_cost_usd("openai", "some-unreleased-model", Usage(input_tokens=100)) is None

    def test_unknown_provider_returns_none(self) -> None:
        assert estimate_cost_usd("mystery", "whatever", Usage(input_tokens=100)) is None

    def test_local_inference_is_explicitly_zero_not_unknown(self) -> None:
        """Zero and "unknown" must stay distinguishable."""
        cost = estimate_cost_usd(
            "ollama", "qwen2.5:3b-instruct", Usage(input_tokens=5_000, output_tokens=5_000)
        )
        assert cost == Decimal("0.000000")

    def test_wildcard_matches_any_model_for_that_provider(self) -> None:
        assert get_pricing("ollama", "llama3.2:1b") is not None
        assert get_pricing("fake", "anything-at-all") is not None

    def test_zero_usage_costs_nothing(self) -> None:
        assert estimate_cost_usd("openai", "gpt-4o-mini", Usage()) == Decimal("0.000000")


class TestPricingTable:
    def test_every_entry_records_when_it_was_checked_and_where_from(self) -> None:
        for (provider, model), pricing in PRICING.items():
            assert pricing.source, f"{provider}/{model} has no source"
            assert pricing.checked_on is not None, f"{provider}/{model} has no checked_on date"

    def test_rates_are_decimals_not_floats(self) -> None:
        """Float arithmetic on money accumulates error across aggregation."""
        for pricing in PRICING.values():
            assert isinstance(pricing.input_per_million, Decimal)
            assert isinstance(pricing.output_per_million, Decimal)

    def test_output_is_never_cheaper_than_input(self) -> None:
        """A sanity check on transcription errors, not a law of nature."""
        for (provider, model), pricing in PRICING.items():
            assert pricing.output_per_million >= pricing.input_per_million, (
                f"{provider}/{model} prices output below input - verify the table"
            )
