"""Chunker behaviour.

These assertions encode decisions, not implementation details: where a chunk is
allowed to start, what a citation's section path is allowed to claim, and that
overlap never crosses a section boundary. Each of these was a real bug during
Phase 3 before it was a test.
"""

from __future__ import annotations

from app.rag.ingestion.blocks import Block, BlockKind, ExtractedDocument
from app.rag.ingestion.chunking import Chunker, count_tokens, split_sentences


def _doc(*blocks: Block, page_count: int | None = None) -> ExtractedDocument:
    return ExtractedDocument(blocks=list(blocks), page_count=page_count)


def heading(text: str, level: int = 1, page: int | None = None) -> Block:
    return Block(text=text, kind=BlockKind.HEADING, level=level, page=page)


def para(text: str, page: int | None = None) -> Block:
    return Block(text=text, kind=BlockKind.PARAGRAPH, page=page)


class TestSentenceSplitting:
    def test_splits_on_terminators(self) -> None:
        assert split_sentences("One. Two! Three?") == ["One.", "Two!", "Three?"]

    def test_decimals_do_not_split(self) -> None:
        # The commonest failure mode in financial text: "4.2 million" must not
        # become two sentences.
        assert split_sentences("Revenue was 4.2 million in Q2.") == [
            "Revenue was 4.2 million in Q2."
        ]

    def test_abbreviations_do_not_split(self) -> None:
        assert split_sentences("See Fig. 3 for detail.") == ["See Fig. 3 for detail."]
        assert split_sentences("Dr. Smith reported it. She left.") == [
            "Dr. Smith reported it.",
            "She left.",
        ]

    def test_initials_do_not_split(self) -> None:
        assert split_sentences("Signed by J. Smith today.") == ["Signed by J. Smith today."]

    def test_empty_text(self) -> None:
        assert split_sentences("") == []


class TestSectionPaths:
    def test_heading_leads_its_content(self) -> None:
        result = Chunker(chunk_size=200, chunk_overlap=0).chunk(
            _doc(heading("Report"), heading("Revenue", 2), para("Revenue grew."))
        )
        assert len(result.chunks) == 1
        chunk = result.chunks[0]
        assert chunk.section_path == "Report > Revenue"
        # The heading appears once, not duplicated by the overlap machinery.
        assert chunk.content.count("Report") == 1

    def test_heading_only_document_still_chunks(self) -> None:
        result = Chunker().chunk(_doc(heading("Nothing Below This")))
        assert len(result.chunks) == 1

    def test_trailing_heading_is_not_emitted_alone(self) -> None:
        result = Chunker(chunk_size=100, chunk_overlap=0).chunk(
            _doc(heading("A"), para("Body text here."), heading("Dangling"))
        )
        assert len(result.chunks) == 1
        assert "Dangling" not in result.chunks[0].content

    def test_nested_headings_extend_the_path(self) -> None:
        result = Chunker(chunk_size=60, chunk_overlap=0).chunk(
            _doc(
                heading("Top", 1),
                heading("Middle", 2),
                para("First body. " * 12),
                heading("Sibling", 2),
                para("Second body. " * 12),
            )
        )
        paths = {chunk.section_path for chunk in result.chunks}
        assert "Top > Middle" in paths
        assert "Top > Sibling" in paths
        # The level-2 sibling replaces its peer rather than nesting under it.
        assert "Top > Middle > Sibling" not in paths


class TestOverlap:
    def test_overlap_carries_within_a_section(self) -> None:
        sentence = "EMEA revenue held flat at 1.1 million pounds. "
        result = Chunker(chunk_size=60, chunk_overlap=20).chunk(
            _doc(heading("EMEA"), para(sentence * 20))
        )
        assert len(result.chunks) > 1
        # Consecutive chunks in one section share text.
        first_tail = result.chunks[0].content[-40:]
        assert any(word in result.chunks[1].content for word in first_tail.split())

    def test_overlap_does_not_cross_a_section_boundary(self) -> None:
        result = Chunker(chunk_size=80, chunk_overlap=32).chunk(
            _doc(
                heading("Alpha"),
                para("Alpha facts are distinctive marmalade. " * 6),
                heading("Beta"),
                para("Beta facts are distinctive porcupine. " * 6),
            )
        )
        # Two level-1 headings are siblings, so Beta replaces Alpha in the path
        # rather than nesting beneath it.
        beta_chunks = [c for c in result.chunks if c.section_path == "Beta"]
        assert beta_chunks
        # A chunk filed under Beta must not open with Alpha's text, or every
        # citation to it would misattribute the source.
        assert "marmalade" not in beta_chunks[0].content

    def test_zero_overlap_produces_no_repetition(self) -> None:
        result = Chunker(chunk_size=50, chunk_overlap=0).chunk(
            _doc(para("Unique sentence number one. Unique sentence number two. " * 8))
        )
        joined = " ".join(chunk.content for chunk in result.chunks)
        # With no overlap the total content is not inflated by repeats.
        assert joined.count("Unique sentence number one.") == 8


class TestBudget:
    def test_chunks_respect_the_token_budget(self) -> None:
        result = Chunker(chunk_size=100, chunk_overlap=0, min_chunk_tokens=0).chunk(
            _doc(para("This is a sentence with several words in it. " * 60))
        )
        assert len(result.chunks) > 1
        for chunk in result.chunks:
            # Some slack: a chunk is only closed once adding the *next* whole
            # sentence would exceed the budget.
            assert chunk.token_count <= 150

    def test_oversized_block_splits_on_sentences(self) -> None:
        result = Chunker(chunk_size=40, chunk_overlap=0).chunk(
            _doc(para("Alpha beta gamma delta. " * 30))
        )
        for chunk in result.chunks:
            # Never cut mid-sentence.
            assert chunk.content.strip().endswith(".")

    def test_token_count_matches_content(self) -> None:
        result = Chunker(chunk_size=100, chunk_overlap=0).chunk(_doc(para("Some text here.")))
        chunk = result.chunks[0]
        assert chunk.token_count == count_tokens(chunk.content)


class TestProvenance:
    def test_page_range_spans_the_source_pages(self) -> None:
        result = Chunker(chunk_size=400, chunk_overlap=0).chunk(
            _doc(para("Page one text.", page=1), para("Page two text.", page=2))
        )
        assert result.chunks[0].page_from == 1
        assert result.chunks[0].page_to == 2

    def test_offsets_index_into_full_text(self) -> None:
        result = Chunker(chunk_size=400, chunk_overlap=0).chunk(
            _doc(heading("Title"), para("The body of the document."))
        )
        chunk = result.chunks[0]
        window = result.full_text[chunk.char_start : chunk.char_end]
        assert "body of the document" in window

    def test_chunk_indexes_are_contiguous(self) -> None:
        result = Chunker(chunk_size=50, chunk_overlap=0).chunk(
            _doc(para("Filler sentence for the corpus. " * 30))
        )
        assert [chunk.index for chunk in result.chunks] == list(range(len(result.chunks)))
