"""Versioned prompt registry (brief §25).

Prompts are files, not string literals scattered through the code. Every LLM call
records the prompt name, version, and content checksum, so an evaluation result
can be attributed to the exact text that produced it and comparing `planner_v1`
against `planner_v2` is a query rather than an archaeology exercise.

Templates use `$placeholder` substitution rather than `str.format`, because prompt
text routinely contains JSON braces that `format` would try to interpret.
"""

from __future__ import annotations

import functools
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from string import Template

from app.core.exceptions import InsightAgentError

TEMPLATE_DIR = Path(__file__).parent / "templates"
#: `<name>_v<number>.md`, e.g. `chat_system_v1.md`
FILENAME_PATTERN = re.compile(r"^(?P<name>[a-z0-9_]+)_v(?P<version>\d+)\.md$")

LATEST = "latest"


class PromptNotFoundError(InsightAgentError):
    error_code = "prompt_not_found"
    default_message = "The requested prompt template does not exist."


@dataclass(frozen=True, slots=True)
class Prompt:
    name: str
    version: str
    template: str
    checksum: str

    def render(self, **values: object) -> str:
        """Substitute placeholders. Raises `KeyError` if any are missing.

        Failing loudly is deliberate: a prompt silently missing its context is the
        kind of bug that produces plausible, confidently wrong output.
        """
        return Template(self.template).substitute(**values)


@functools.lru_cache(maxsize=1)
def _load_all() -> dict[tuple[str, str], Prompt]:
    prompts: dict[tuple[str, str], Prompt] = {}
    if not TEMPLATE_DIR.is_dir():  # pragma: no cover - packaging error
        return prompts

    for path in sorted(TEMPLATE_DIR.glob("*.md")):
        match = FILENAME_PATTERN.match(path.name)
        if match is None:
            raise PromptNotFoundError(
                f"Prompt file '{path.name}' does not follow the <name>_v<number>.md convention."
            )
        text = path.read_text(encoding="utf-8").strip()
        name = match["name"]
        version = f"v{match['version']}"
        prompts[(name, version)] = Prompt(
            name=name,
            version=version,
            template=text,
            checksum=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
        )
    return prompts


def get_prompt(name: str, version: str = LATEST) -> Prompt:
    prompts = _load_all()

    if version == LATEST:
        candidates = [key for key in prompts if key[0] == name]
        if not candidates:
            raise PromptNotFoundError(f"No prompt named '{name}'.")
        version = max(candidates, key=lambda key: int(key[1][1:]))[1]

    prompt = prompts.get((name, version))
    if prompt is None:
        raise PromptNotFoundError(f"No prompt '{name}' at version '{version}'.")
    return prompt


def list_prompts() -> list[Prompt]:
    return sorted(_load_all().values(), key=lambda p: (p.name, p.version))
