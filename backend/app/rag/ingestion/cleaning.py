"""Block cleaning.

Three problems, each of which measurably degrades retrieval if left alone.

**Hyphenation.** PDF text extraction preserves the line-break hyphen, so "revenue"
split across two lines arrives as "reve-\\nnue". Embedded and indexed, that is two
tokens that match nothing.

**Repeated headers and footers.** A page header repeated on all 40 pages of a
report is 40 near-identical fragments competing with real content for retrieval
slots, and the model then cites a running header as evidence. They are detected by
recurrence across pages rather than by position, which is robust to templates that
put the header anywhere.

**Whitespace.** Extractors emit ragged spacing that inflates token counts and,
because chunk boundaries are token-bounded, shifts them for no reason.

Cleaning happens before chunking so that character offsets recorded on a chunk
refer to the cleaned text the user is shown — otherwise a citation highlight would
be off by however much whitespace was removed.
"""

from __future__ import annotations

import re
from collections import Counter

from app.rag.ingestion.blocks import Block, BlockKind, ExtractedDocument

#: A hyphen at end of line followed by a lowercase continuation. Restricted to
#: lowercase so "COVID-\n19" and "Q2-\nQ3" survive intact.
_LINE_HYPHEN = re.compile(r"(\w)-\s*\n\s*([a-z])")
#: Space, tab, and U+00A0. The non-breaking space is the point of the class:
#: PDF extraction emits it constantly, it is visually identical to a space,
#: and leaving it in place breaks token matching in a way that is invisible in
#: any diff you would use to debug it. Written as an escape so the source
#: cannot be misread as containing an ordinary space.
_MULTI_SPACE = re.compile("[ \t\xa0]+")
_MULTI_NEWLINE = re.compile(r"\n{3,}")
#: Zero-width and bidirectional marks. They survive extraction, break token
#: matching, and are invisible in any diff you would use to debug it.
_INVISIBLE = re.compile(r"[​-\u200F\u202A-\u202E﻿]")

#: A line must appear on at least this fraction of pages to count as furniture.
BOILERPLATE_PAGE_RATIO = 0.5
#: ...and on at least this many pages, so a 2-page document is left alone.
MIN_PAGES_FOR_BOILERPLATE = 4
#: Long lines are content that happens to repeat, not a running header.
MAX_BOILERPLATE_CHARS = 90


def normalise_whitespace(text: str) -> str:
    text = _INVISIBLE.sub("", text)
    text = _LINE_HYPHEN.sub(r"\1\2", text)
    text = _MULTI_SPACE.sub(" ", text)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def _page_number_like(line: str) -> bool:
    """Bare page numbers and "Page 3 of 40"."""
    stripped = line.strip()
    if stripped.isdigit() and len(stripped) <= 4:
        return True
    return bool(re.fullmatch(r"(?i)page\s+\d+(\s+of\s+\d+)?", stripped))


def find_boilerplate(blocks: list[Block]) -> set[str]:
    """Lines that recur across enough distinct pages to be furniture."""
    pages = {block.page for block in blocks if block.page is not None}
    if len(pages) < MIN_PAGES_FOR_BOILERPLATE:
        return set()

    seen_on: dict[str, set[int]] = {}
    for block in blocks:
        if block.page is None:
            continue
        for line in block.text.split("\n"):
            candidate = line.strip()
            if not candidate or len(candidate) > MAX_BOILERPLATE_CHARS:
                continue
            seen_on.setdefault(candidate, set()).add(block.page)

    threshold = max(MIN_PAGES_FOR_BOILERPLATE - 1, int(len(pages) * BOILERPLATE_PAGE_RATIO))
    return {line for line, page_set in seen_on.items() if len(page_set) >= threshold}


def _strip_lines(text: str, boilerplate: set[str]) -> str:
    kept = [
        line
        for line in text.split("\n")
        if line.strip() not in boilerplate and not _page_number_like(line)
    ]
    return "\n".join(kept)


def clean(extracted: ExtractedDocument) -> ExtractedDocument:
    """Normalise text and drop repeated page furniture."""
    boilerplate = find_boilerplate(extracted.blocks)

    cleaned: list[Block] = []
    for block in extracted.blocks:
        # Tables are left structurally intact: their rows repeat by nature, and a
        # boilerplate filter would eat legitimate values.
        text = (
            block.text if block.kind is BlockKind.TABLE else _strip_lines(block.text, boilerplate)
        )
        text = normalise_whitespace(text)
        if not text:
            continue
        cleaned.append(Block(text=text, kind=block.kind, page=block.page, level=block.level))

    title = normalise_whitespace(extracted.title) if extracted.title else None
    return ExtractedDocument(blocks=cleaned, page_count=extracted.page_count, title=title or None)


def boilerplate_report(blocks: list[Block]) -> Counter[str]:  # pragma: no cover - debugging aid
    """What the filter would remove, for inspecting a badly-cleaned document."""
    return Counter(line for line in find_boilerplate(blocks))
