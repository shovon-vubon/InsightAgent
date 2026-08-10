"""Citation validation.

The claim under test is the project's strongest honesty guarantee: a citation
that points at nothing cannot reach the user. These tests are the proof, so they
are written against the *behaviour* — what comes out of `validate_markers` — not
against the regex.
"""

from __future__ import annotations

import uuid

from app.rag.citations import (
    build_evidence,
    extract_markers,
    render_context,
    validate_markers,
)
from app.repositories.document import RetrievedChunk


def chunk(
    content: str = "Revenue fell 12% in Q2.",
    *,
    score: float = 0.9,
    page_from: int | None = 4,
    page_to: int | None = None,
    section: str | None = "Q2 Review > Revenue",
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_title="Q2 Report",
        filename="q2.pdf",
        chunk_index=0,
        content=content,
        score=score,
        page_from=page_from,
        page_to=page_to,
        section_path=section,
        char_start=0,
        char_end=len(content),
    )


class TestMarkerExtraction:
    def test_single_markers(self) -> None:
        assert extract_markers("Revenue fell [1] sharply [2].") == [1, 2]

    def test_grouped_markers(self) -> None:
        assert extract_markers("Both sources agree [1, 3].") == [1, 3]

    def test_duplicates_are_reported_once_in_order(self) -> None:
        assert extract_markers("[2] then [1] then [2] again.") == [2, 1]

    def test_ordinary_brackets_are_not_citations(self) -> None:
        assert extract_markers("The array [x] and the note [see appendix].") == []

    def test_no_markers(self) -> None:
        assert extract_markers("A plain sentence.") == []


class TestValidation:
    def test_valid_markers_become_citations(self) -> None:
        evidence = build_evidence([chunk(), chunk(content="EMEA held flat.")])
        outcome = validate_markers("Revenue fell [1]. EMEA held flat [2].", evidence)

        assert outcome.invalid_markers == []
        assert [c.marker for c in outcome.citations] == [1, 2]
        assert outcome.citations[0].page_from == 4
        assert outcome.citations[0].section_path == "Q2 Review > Revenue"

    def test_fabricated_marker_is_reported_and_stripped(self) -> None:
        # The central guarantee: the model cites [7] when only two sources exist.
        evidence = build_evidence([chunk(), chunk()])
        outcome = validate_markers("Revenue fell [1]. Margins improved [7].", evidence)

        assert outcome.invalid_markers == [7]
        assert "[7]" not in outcome.text
        assert [c.marker for c in outcome.citations] == [1]

    def test_partially_valid_group_keeps_the_valid_members(self) -> None:
        evidence = build_evidence([chunk(), chunk()])
        outcome = validate_markers("Both agree [1, 9].", evidence)

        assert outcome.invalid_markers == [9]
        assert "[1]" in outcome.text
        assert "9" not in outcome.text

    def test_stripping_does_not_leave_dangling_whitespace(self) -> None:
        evidence = build_evidence([chunk()])
        outcome = validate_markers("Margins improved [4].", evidence)
        assert outcome.text == "Margins improved."

    def test_zero_marker_is_invalid(self) -> None:
        # 1-based ids: [0] is always a fabrication.
        evidence = build_evidence([chunk()])
        outcome = validate_markers("Claimed [0].", evidence)
        assert outcome.invalid_markers == [0]

    def test_unused_evidence_is_reported(self) -> None:
        evidence = build_evidence([chunk(), chunk(), chunk()])
        outcome = validate_markers("Only the first matters [1].", evidence)
        assert outcome.unused_markers == [2, 3]

    def test_answer_with_no_citations_yields_none(self) -> None:
        evidence = build_evidence([chunk()])
        outcome = validate_markers("The documents do not cover this.", evidence)
        assert outcome.citations == []
        assert outcome.invalid_markers == []


class TestContextRendering:
    def test_each_source_is_delimited_and_labelled(self) -> None:
        evidence = build_evidence([chunk(), chunk(content="Second source.")])
        rendered = render_context(evidence)

        assert '<source id="1">' in rendered
        assert '<source id="2">' in rendered
        assert rendered.count("</source>") == 2
        assert "p. 4" in rendered

    def test_page_range_is_rendered_when_a_chunk_spans_pages(self) -> None:
        evidence = build_evidence([chunk(page_from=4, page_to=6)])
        assert "pp. 4" in render_context(evidence)

    def test_long_chunks_are_truncated(self) -> None:
        evidence = build_evidence([chunk(content="x" * 9000)])
        rendered = render_context(evidence, max_chars_per_chunk=100)
        assert rendered.count("x") == 100

    def test_marker_ids_are_one_based_and_in_rank_order(self) -> None:
        evidence = build_evidence([chunk(score=0.9), chunk(score=0.5)])
        assert [item.marker for item in evidence] == [1, 2]
        assert evidence[0].chunk.score == 0.9
