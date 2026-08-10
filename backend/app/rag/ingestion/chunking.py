"""Structure-aware chunking.

The single most consequential piece of a RAG system, and the one most often
reduced to `text[i:i+1000]`. Fixed-width slicing cuts sentences in half, strands
a figure from the sentence that gives it meaning, and produces citations that
point at a fragment nobody can verify.

The strategy here:

1. **Split on section boundaries first.** A heading starts a new chunk. Sections
   are what the author considered a unit of meaning, and a heading is a strong
   signal that the topic changed.
2. **Pack blocks up to a token budget.** Within a section, whole blocks are
   accumulated until adding the next would exceed `chunk_size`.
3. **Split oversized blocks on sentence boundaries.** A single block larger than
   the budget is divided between sentences, never mid-sentence.
4. **Overlap by whole sentences.** The tail of one chunk is prepended to the next
   so that a fact spanning a boundary is retrievable from either side. Overlapping
   by tokens would reintroduce exactly the mid-sentence cuts step 3 avoids.

Every chunk carries its section path, its page range, and its character offsets
into the cleaned document text. The offsets are what let the UI highlight the
cited sentence rather than the whole chunk — cheap to compute here, impossible to
recover later.

Token counts come from `tiktoken`, so the budget is in the same units the model
bills in. It is an approximation for non-OpenAI models, which is fine: it is used
to size chunks consistently, not to predict cost.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field

import tiktoken

from app.rag.ingestion.blocks import Block, BlockKind, ExtractedDocument

#: cl100k_base is the tokeniser for GPT-4-class models and a reasonable proxy for
#: the others. Chosen for stability, not because the corpus is OpenAI-specific.
TOKENISER = "cl100k_base"

#: Sentence terminator, optional closing punctuation, whitespace, then something
#: that can open a sentence. Because the terminator must be followed by
#: whitespace, decimals ("4.2 million") and version numbers never match.
#:
#: Abbreviations are handled by merging afterwards rather than by a lookbehind:
#: Python's `re` only supports fixed-width lookbehind, so "Dr" and "Prof" cannot
#: be excluded in one expression.
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]*[A-Z0-9])")

#: Tokens that end in a period without ending a sentence.
_ABBREVIATIONS = frozenset(
    {
        # titles
        *("mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "rev", "hon"),
        # company suffixes
        *("inc", "ltd", "llc", "llp", "corp", "co", "plc", "gmbh"),
        # cross-references, common in the reports this indexes
        *("fig", "figs", "eq", "eqs", "no", "nos", "vol", "ch", "sec"),
        # months
        *("jan", "feb", "mar", "apr", "jun", "jul", "aug", "sept", "sep", "oct", "nov", "dec"),
        # measurement and general
        *("approx", "est", "max", "min", "avg", "dept", "vs", "etc", "al", "cf", "ibid"),
        *("e.g", "i.e"),
    }
)


@functools.lru_cache(maxsize=1)
def _encoder() -> tiktoken.Encoding:
    return tiktoken.get_encoding(TOKENISER)


def count_tokens(text: str) -> int:
    return len(_encoder().encode(text, disallowed_special=()))


def _ends_with_abbreviation(text: str) -> bool:
    tail = text.rstrip().rsplit(maxsplit=1)
    if not tail:
        return False
    token = tail[-1].strip("\"')]([").lower()
    if not token.endswith("."):
        return False
    token = token[:-1]
    # A lone letter is an initial: "J. Smith", "A. N. Other".
    return token in _ABBREVIATIONS or (len(token) == 1 and token.isalpha())


def split_sentences(text: str) -> list[str]:
    """Split on sentence boundaries, keeping the terminator with its sentence."""
    parts = [part.strip() for part in _SENTENCE_END.split(text) if part.strip()]

    merged: list[str] = []
    for part in parts:
        if merged and _ends_with_abbreviation(merged[-1]):
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)

    return merged or ([text.strip()] if text.strip() else [])


@dataclass(frozen=True, slots=True)
class Chunk:
    content: str
    index: int
    token_count: int
    char_start: int
    char_end: int
    page_from: int | None = None
    page_to: int | None = None
    section_path: str | None = None


@dataclass(slots=True)
class _Pending:
    """A chunk under construction."""

    texts: list[str] = field(default_factory=list)
    tokens: int = 0
    pages: list[int] = field(default_factory=list)
    section: str | None = None
    char_start: int = 0
    char_end: int = 0
    #: Non-heading pieces accumulated. A chunk holding only headings is not worth
    #: emitting — the heading belongs at the top of the content it introduces.
    body_pieces: int = 0

    def is_empty(self) -> bool:
        return not self.texts

    def has_body(self) -> bool:
        return self.body_pieces > 0


@dataclass(frozen=True, slots=True)
class ChunkingResult:
    chunks: list[Chunk]
    #: The cleaned document as one string. Chunk offsets index into this, and it
    #: is what a highlight in the UI is applied to.
    full_text: str


class Chunker:
    def __init__(
        self,
        *,
        chunk_size: int = 512,
        chunk_overlap: int = 64,
        #: A tail smaller than this is merged into the previous chunk rather than
        #: left as a fragment that cannot answer anything on its own.
        min_chunk_tokens: int = 48,
    ) -> None:
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.min_chunk_tokens = min_chunk_tokens

    # --- section paths ------------------------------------------------------

    @staticmethod
    def _push_heading(stack: list[tuple[int, str]], block: Block) -> None:
        level = block.level or 1
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, block.text))

    @staticmethod
    def _path(stack: list[tuple[int, str]]) -> str | None:
        return " > ".join(text for _, text in stack) if stack else None

    # --- chunking -----------------------------------------------------------

    def chunk(self, extracted: ExtractedDocument) -> ChunkingResult:
        # The document is materialised once so that every chunk's offsets refer to
        # one canonical string rather than to a reconstruction.
        segments: list[str] = []
        offsets: list[tuple[int, int]] = []
        cursor = 0
        for block in extracted.blocks:
            segments.append(block.text)
            offsets.append((cursor, cursor + len(block.text)))
            cursor += len(block.text) + 2  # the "\n\n" join below
        full_text = "\n\n".join(segments)

        chunks: list[Chunk] = []
        pending = _Pending()
        stack: list[tuple[int, str]] = []
        overlap_tail: list[str] = []

        def emit(*, force: bool = False) -> None:
            nonlocal pending, overlap_tail
            if pending.is_empty():
                return
            # A trailing run of headings with nothing under it carries no
            # answerable content. `force` covers the degenerate document that is
            # nothing but headings, which would otherwise chunk to nothing at all.
            if not pending.has_body() and not force:
                pending = _Pending()
                return
            content = "\n\n".join(pending.texts).strip()
            if not content:
                pending = _Pending()
                return

            tokens = count_tokens(content)
            # Merge a runt into its predecessor instead of emitting a fragment —
            # but only within the same section, and only if the result stays near
            # the budget. Merging across a section boundary would file the text
            # under a heading it does not belong to.
            if (
                chunks
                and tokens < self.min_chunk_tokens
                and chunks[-1].section_path == pending.section
                and chunks[-1].token_count + tokens <= self.chunk_size * 1.5
            ):
                previous = chunks[-1]
                merged = f"{previous.content}\n\n{content}"
                chunks[-1] = Chunk(
                    content=merged,
                    index=previous.index,
                    token_count=count_tokens(merged),
                    char_start=previous.char_start,
                    char_end=max(previous.char_end, pending.char_end),
                    page_from=previous.page_from,
                    page_to=max(pending.pages) if pending.pages else previous.page_to,
                    section_path=previous.section_path,
                )
            else:
                chunks.append(
                    Chunk(
                        content=content,
                        index=len(chunks),
                        token_count=tokens,
                        char_start=pending.char_start,
                        char_end=pending.char_end,
                        page_from=min(pending.pages) if pending.pages else None,
                        page_to=max(pending.pages) if pending.pages else None,
                        section_path=pending.section,
                    )
                )
            overlap_tail = self._tail_sentences(content)
            pending = _Pending()

        def start(section: str | None, char_start: int) -> None:
            nonlocal pending
            pending = _Pending(section=section, char_start=char_start, char_end=char_start)
            if overlap_tail and self.chunk_overlap > 0:
                carried = " ".join(overlap_tail)
                pending.texts.append(carried)
                pending.tokens = count_tokens(carried)

        for block, (block_start, block_end) in zip(extracted.blocks, offsets, strict=True):
            if block.is_heading():
                # A heading closes the previous section only if that section
                # actually accumulated text. Consecutive headings (a title
                # followed by its first subheading) stay together and lead the
                # content that follows, instead of being emitted as a standalone
                # chunk that answers nothing.
                if pending.has_body():
                    emit()
                # Overlap exists to preserve continuity inside flowing prose.
                # Carrying it over a section boundary prepends the previous
                # section's text to a chunk labelled with the new heading, which
                # misattributes the provenance of every fact in it. Cleared
                # unconditionally, because `emit` refreshes it.
                overlap_tail = []

                self._push_heading(stack, block)
                if pending.is_empty():
                    start(self._path(stack), block_start)
                # Re-read the path: nested headings extend it as they arrive.
                pending.section = self._path(stack)
                pending.texts.append(block.text)
                pending.tokens += count_tokens(block.text)
                pending.char_end = block_end
                if block.page is not None:
                    pending.pages.append(block.page)
                continue

            for piece, piece_start, piece_end in self._fit(block, block_start, block_end):
                piece_tokens = count_tokens(piece)

                if not pending.is_empty() and pending.tokens + piece_tokens > self.chunk_size:
                    emit()
                    start(self._path(stack), piece_start)
                if pending.is_empty():
                    start(self._path(stack), piece_start)

                pending.texts.append(piece)
                pending.tokens += piece_tokens
                pending.body_pieces += 1
                pending.char_end = piece_end
                if block.page is not None:
                    pending.pages.append(block.page)

        emit(force=not chunks)
        return ChunkingResult(chunks=chunks, full_text=full_text)

    def _fit(self, block: Block, start: int, end: int) -> list[tuple[str, int, int]]:
        """Break a block into pieces that each fit the budget.

        Offsets are approximated by walking the sentence lengths through the
        block's own span, which is exact as long as `split_sentences` only
        removes the whitespace between sentences.
        """
        if count_tokens(block.text) <= self.chunk_size:
            return [(block.text, start, end)]

        # A table is split on rows; splitting it on sentences would merge
        # unrelated rows into one run-on line.
        units = (
            block.text.split("\n") if block.kind is BlockKind.TABLE else split_sentences(block.text)
        )

        pieces: list[tuple[str, int, int]] = []
        current: list[str] = []
        current_tokens = 0
        cursor = start

        for unit in units:
            unit_tokens = count_tokens(unit)
            if current and current_tokens + unit_tokens > self.chunk_size:
                text = " ".join(current)
                pieces.append((text, cursor, min(cursor + len(text), end)))
                cursor = min(cursor + len(text) + 1, end)
                current = []
                current_tokens = 0
            current.append(unit)
            current_tokens += unit_tokens

        if current:
            text = " ".join(current)
            pieces.append((text, cursor, min(cursor + len(text), end)))
        return pieces

    def _tail_sentences(self, content: str) -> list[str]:
        """The trailing whole sentences that fit within the overlap budget."""
        if self.chunk_overlap <= 0:
            return []
        tail: list[str] = []
        budget = 0
        for sentence in reversed(split_sentences(content)):
            tokens = count_tokens(sentence)
            if budget + tokens > self.chunk_overlap:
                break
            tail.insert(0, sentence)
            budget += tokens
        return tail
