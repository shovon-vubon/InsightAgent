from __future__ import annotations

import pytest

from app.prompts.registry import PromptNotFoundError, get_prompt, list_prompts


def test_the_chat_system_prompt_loads() -> None:
    prompt = get_prompt("chat_system")

    assert prompt.name == "chat_system"
    assert prompt.version.startswith("v")
    assert prompt.checksum


def test_latest_resolves_to_the_highest_version() -> None:
    versions = {p.version for p in list_prompts() if p.name == "chat_system"}
    highest = max(versions, key=lambda version: int(version[1:]))

    assert get_prompt("chat_system").version == highest


def test_an_explicit_version_can_be_pinned() -> None:
    assert get_prompt("chat_system", "v1").version == "v1"


def test_a_missing_prompt_raises() -> None:
    with pytest.raises(PromptNotFoundError):
        get_prompt("no_such_prompt")


def test_a_missing_version_raises() -> None:
    with pytest.raises(PromptNotFoundError):
        get_prompt("chat_system", "v99")


def test_rendering_substitutes_placeholders() -> None:
    rendered = get_prompt("chat_system").render(current_date="2026-08-09")
    assert "2026-08-09" in rendered
    assert "$current_date" not in rendered


def test_rendering_without_required_context_fails_loudly() -> None:
    """A prompt silently missing its context produces confidently wrong output."""
    with pytest.raises(KeyError):
        get_prompt("chat_system").render()


def test_checksum_is_stable_across_loads() -> None:
    assert get_prompt("chat_system").checksum == get_prompt("chat_system").checksum


def test_the_chat_prompt_forbids_inventing_citations() -> None:
    """Phase 2 has no retrieval, so the prompt must not license citations."""
    template = get_prompt("chat_system").template.lower()

    assert "never invent evidence" in template
    assert "do not cite" in template
