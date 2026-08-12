"""Reject commit messages that are not Conventional Commits.

The repository documents Conventional Commits as its standard, but nothing
enforced it: `.pre-commit-config.yaml` existed while the hooks were never
installed. Eleven commits reached the branch carrying pasted `git status` output
as their message — "On branch feature/knowledge-base", "Changes to be
committed:", "\tmodified:   backend/app/core/config.py" — which is unreadable in
`git log` and useless in a bisect.

Run as a `commit-msg` hook: the path to the message file arrives as argv[1].
Standard library only, so it costs nothing to run on every commit and works
before any environment is provisioned.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: Conventional Commits, plus `merge` for the phase integrations this repo makes
#: on `develop` with an explicit message rather than git's default.
TYPES = (
    "feat",
    "fix",
    "docs",
    "style",
    "refactor",
    "perf",
    "test",
    "build",
    "ci",
    "chore",
    "revert",
    "merge",
)

SUBJECT = re.compile(rf"^(?:{'|'.join(TYPES)})(?:\([^()\n]+\))?!?: .+")

#: Phrases git writes into its own status output. Matched against the subject
#: only: when a status dump reaches `-m` the first line is always one of these,
#: whereas a body that discusses the problem — as the commit adding this hook
#: does — legitimately quotes them. Scanning the whole message rejected that
#: commit, which is how the false positive was found.
STATUS_MARKERS = (
    "On branch ",
    "Changes to be committed:",
    "Changes not staged for commit:",
    "Untracked files:",
    "nothing to commit",
    "Your branch and ",
    'use "git restore --staged',
)

#: Matches the project's ruff line-length. Long enough for the existing history's
#: longest subject, short enough that runaway pasted text is caught.
MAX_SUBJECT_LENGTH = 100

USAGE = "usage: check_commit_message.py <path-to-COMMIT_EDITMSG>"


def _body(raw: str) -> list[str]:
    """The message as git will store it: comments stripped, blanks trimmed."""
    lines = [line for line in raw.splitlines() if not line.startswith("#")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _fail(message: str, *, hint: str) -> int:
    sys.stderr.write(f"commit-msg: {message}\n\n{hint}\n")
    return 1


def check(raw: str) -> int:
    lines = _body(raw)
    if not lines:
        return _fail(
            "the commit message is empty.",
            hint="Describe the change, e.g. 'fix(worker): probe arq, not the API's HTTP port'.",
        )

    subject = lines[0].strip()

    # git generates these itself during merge, revert, and interactive fixups.
    # Rejecting them would block operations the developer did not author.
    if subject.startswith(("Merge ", "Revert ", "fixup!", "squash!")):
        return 0

    for marker in STATUS_MARKERS:
        if marker in subject:
            return _fail(
                f"this looks like pasted `git status` output (found {marker!r}).",
                hint=(
                    "A shell expansion or editor mishap has put the status into the "
                    "message. Re-run with an explicit message:\n"
                    "  git commit -m 'feat(scope): what changed and why'"
                ),
            )

    if not SUBJECT.match(subject):
        return _fail(
            f"subject is not a Conventional Commit: {subject!r}",
            hint=(
                f"Expected '<type>(<scope>): <description>', type one of:\n  {', '.join(TYPES)}\n"
                "Example:\n  fix(worker): probe arq, not the API's HTTP port"
            ),
        )

    if len(subject) > MAX_SUBJECT_LENGTH:
        return _fail(
            f"subject is {len(subject)} characters; the limit is {MAX_SUBJECT_LENGTH}.",
            hint="Move the detail into the body, leaving the subject as the summary.",
        )

    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write(f"{USAGE}\n")
        return 2
    return check(Path(argv[1]).read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
