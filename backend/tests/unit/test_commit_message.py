"""The commit-msg hook that would have stopped eleven unreadable commits.

The `git status` cases below are not invented: they are the actual messages of
commits `36d1430`, `cf45be9`, `804b5af` and their siblings, which reached the
branch because `.pre-commit-config.yaml` was present but never installed.
"""

from __future__ import annotations

import pytest

from scripts.check_commit_message import check

PASSING = [
    "feat(rag): knowledge base with ingestion, dense retrieval, and cited answers",
    "fix(worker): probe arq, not the API's HTTP port",
    "docs: record the Phase 3 outcome",
    "chore: normalise line endings to LF via .gitattributes",
    "merge: Phase 2 LLM layer and streaming chat",
    "refactor(api)!: drop the legacy route module",
]

#: git writes these itself; the hook must not block a merge or a fixup.
GENERATED = [
    "Merge branch 'develop' into feature/knowledge-base",
    'Revert "feat(rag): knowledge base"',
    "fixup! fix(worker): probe arq",
    "squash! docs: record the Phase 3 outcome",
]

#: Verbatim from `git log` on the nine commits pushed to origin.
STATUS_DUMPS = [
    "On branch feature/knowledge-base",
    "Changes to be committed:",
    "Changes to be committed: \tmodified:   backend/app/core/config.py",
    "\tmodified:   backend/app/rag/ingestion/cleaning.py",
]


@pytest.mark.parametrize("message", PASSING)
def test_conventional_subjects_are_accepted(message: str) -> None:
    assert check(message) == 0


@pytest.mark.parametrize("message", GENERATED)
def test_git_generated_messages_are_left_alone(message: str) -> None:
    assert check(message) == 0


@pytest.mark.parametrize("message", STATUS_DUMPS)
def test_pasted_status_output_is_rejected(message: str) -> None:
    assert check(message) == 1


@pytest.mark.parametrize("message", ["", "   \n\n", "#  a comment only\n"])
def test_empty_messages_are_rejected(message: str) -> None:
    assert check(message) == 1


@pytest.mark.parametrize(
    "message",
    ["wip", "update stuff", "Fixed the worker", "feat missing colon", "feat:no space"],
)
def test_unconventional_subjects_are_rejected(message: str) -> None:
    assert check(message) == 1


def test_comments_and_trailing_blanks_are_ignored() -> None:
    """git appends its template as `#` comments; they are not part of the message."""
    raw = (
        "fix(worker): probe arq, not the API's HTTP port\n"
        "\n"
        "The runtime image is shared with the API.\n"
        "\n"
        "# Please enter the commit message for your changes. Lines starting\n"
        "# with '#' will be ignored, and an empty message aborts the commit.\n"
        "#\n"
        "# On branch feature/knowledge-base\n"
    )
    # The commented-out "On branch" must not trip the status-dump detector.
    assert check(raw) == 0


def test_a_body_may_discuss_the_status_dump_it_is_fixing() -> None:
    """The commit that introduced this hook was itself rejected by it.

    Scanning every line for the markers meant any message quoting the bad
    commits — the natural way to explain the fix — was refused. Only the subject
    is checked now, because that is where a real status dump always lands.
    """
    raw = (
        "build(hooks): enforce Conventional Commit messages\n"
        "\n"
        "Eleven commits carry pasted `git status` output as their message:\n"
        '"On branch feature/knowledge-base", "Changes to be committed:",\n'
        '"\tmodified:   backend/app/core/config.py".\n'
    )
    assert check(raw) == 0


def test_overlong_subject_is_rejected() -> None:
    assert check(f"feat(rag): {'x' * 120}") == 1
