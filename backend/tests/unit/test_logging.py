"""The log redaction processor is a security control, so it gets real tests."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from app.core.logging import REDACTED, redact_sensitive


def process(event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    return redact_sensitive(None, "info", event)


def test_password_field_is_redacted() -> None:
    assert process({"event": "login", "password": "hunter2"})["password"] == REDACTED


def test_matching_is_case_insensitive_and_substring_based() -> None:
    result = process({"Authorization": "Bearer abc", "db_password": "x", "API_KEY": "y"})
    assert result == {"Authorization": REDACTED, "db_password": REDACTED, "API_KEY": REDACTED}


def test_nested_structures_are_redacted() -> None:
    result = process({"request": {"headers": {"cookie": "session=abc"}, "path": "/login"}})
    request = result["request"]
    assert isinstance(request, dict)
    assert request["headers"] == {"cookie": REDACTED}
    assert request["path"] == "/login"


def test_values_inside_lists_are_redacted() -> None:
    result = process({"attempts": [{"refresh_token": "leak-me"}, {"user_id": "u1"}]})
    assert result["attempts"] == [{"refresh_token": REDACTED}, {"user_id": "u1"}]


def test_non_sensitive_fields_pass_through_untouched() -> None:
    event = {"event": "request_completed", "status_code": 200, "duration_ms": 12.5}
    assert process(dict(event)) == event


def test_deeply_nested_structure_does_not_recurse_forever() -> None:
    """A self-referential dict must not hang the logger."""
    payload: dict[str, object] = {"level": 0}
    node = payload
    for depth in range(1, 20):
        child: dict[str, object] = {"level": depth}
        node["child"] = child
        node = child

    assert process(payload)["level"] == 0
