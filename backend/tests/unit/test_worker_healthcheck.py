"""The worker's container healthcheck must not be the API's.

Both services are built from the same `runtime` image target, so the worker
inherits the API's `HEALTHCHECK` — an HTTP GET against uvicorn's port — into a
container that runs arq and serves no HTTP. Phase 3 shipped that way. The probe
failed 784 consecutive times over two days while the worker ingested documents
perfectly well; nothing declares `depends_on: worker: condition: service_healthy`,
so nothing broke and the false alarm went unnoticed. That is precisely why it is
pinned here rather than left to be spotted by eye in `docker compose ps`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from app.worker.main import HEALTH_CHECK_INTERVAL_SECONDS

COMPOSE_PATH = Path(__file__).resolve().parents[3] / "docker-compose.yml"

#: Compose accepts Go duration strings; only the units actually used here.
_DURATION = re.compile(r"^(?P<value>\d+)(?P<unit>ms|s|m|h)$")
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


def _duration_seconds(raw: str) -> float:
    match = _DURATION.match(raw.strip())
    assert match is not None, f"unparsable compose duration: {raw!r}"
    return int(match.group("value")) * _UNIT_SECONDS[match.group("unit")]


def _worker_service() -> dict[str, Any]:
    document: Any = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict), "compose file did not parse to a mapping"
    services: Any = document["services"]
    assert isinstance(services, dict)
    worker: Any = services["worker"]
    assert isinstance(worker, dict)
    return worker


def test_worker_overrides_the_inherited_http_healthcheck() -> None:
    """An HTTP probe in a container with no HTTP server can only ever fail."""
    healthcheck: Any = _worker_service().get("healthcheck")
    assert healthcheck is not None, (
        "the worker must override the healthcheck it inherits from the shared "
        "runtime image, which probes the API's HTTP port"
    )

    test: Any = healthcheck["test"]
    assert isinstance(test, list)
    command = " ".join(str(part) for part in test)

    assert "--check" in command, "expected arq's own health check"
    assert "http" not in command.lower(), (
        f"the worker serves no HTTP; this probe cannot succeed: {command!r}"
    )


def test_probe_runs_before_the_health_key_can_expire() -> None:
    """A healthy worker must not flap.

    arq gives its sentinel key a TTL of ``health_check_interval + 1`` seconds and
    refreshes it on that same interval. If Docker probed less often than the key
    lived, a perfectly healthy worker would be marked unhealthy between refreshes.
    """
    healthcheck: Any = _worker_service()["healthcheck"]
    probe_interval = _duration_seconds(str(healthcheck["interval"]))
    key_lifetime = HEALTH_CHECK_INTERVAL_SECONDS + 1

    assert probe_interval < key_lifetime, (
        f"probe every {probe_interval}s against a key that lives {key_lifetime}s "
        "would flap on a healthy worker"
    )
