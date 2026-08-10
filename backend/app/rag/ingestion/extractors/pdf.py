"""PDF extraction.

Two libraries, each for what it is actually good at: **pypdf** reads metadata and
detects encryption in milliseconds without laying out a single page, and
**pdfplumber** does the content extraction.

The interesting part is heading detection. A PDF has no headings — it has glyphs
at coordinates in a font size. Recovering structure matters because the chunker
splits on section boundaries and the citation carries a section path, so a chunk
that says "Q2 Review > Revenue" is far more useful in an answer than page 7 alone.
The heuristic is font size relative to the document's own body text, which is
robust across templates in a way that any absolute threshold is not.

It is a heuristic, and it is wrong on some documents — a PDF that emphasises with
bold rather than size yields no headings, and the chunker falls back to packing
paragraphs to the token budget. That degrades gracefully and is stated rather than
hidden.

**Tables are not extracted here.** pdfplumber's `extract_text` already includes
the words inside a table, so emitting them again as a table block would duplicate
content and skew retrieval toward whichever chunk happened to contain both copies.
Proper table handling lands with the NovaRetail reports in Phase 5.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import pdfplumber
import pypdf

from app.core.exceptions import DocumentProcessingError
from app.core.logging import get_logger
from app.rag.ingestion.blocks import Block, BlockKind, ExtractedDocument

logger = get_logger(__name__)

#: Below this multiple of body text, a line is body text.
HEADING_SIZE_RATIO = 1.15
#: Headings are short. A long line in a large font is a pull quote, not a heading.
MAX_HEADING_CHARS = 120
#: Words on the same visual line differ in `top` by less than this many points.
LINE_TOLERANCE = 2.5
#: Line-to-line drop, as a multiple of body font size, that reads as a paragraph
#: break. Normal leading is ~1.2x; 1.8x leaves room for tight double-spacing.
PARAGRAPH_GAP_RATIO = 1.8


@dataclass(frozen=True, slots=True)
class _Line:
    text: str
    size: float
    page: int
    #: Distance from the top of the page, used to detect paragraph breaks.
    top: float


#: Metadata titles that carry no information. PDF producers fill `/Title` with
#: placeholders and with the source filename far more often than with a real
#: title, and any of these is worse than the document's own first heading.
_USELESS_TITLES = frozenset({"untitled", "unknown", "document", "none", "-", "title"})
_FILENAME_TITLE = re.compile(r"\.(pdf|docx?|pptx?|xlsx?|rtf|odt|tex)$", re.IGNORECASE)


def _usable_title(raw: object) -> str | None:
    """Reject placeholder and filename-shaped metadata titles."""
    if raw is None:
        return None
    title = str(raw).strip()
    if not title or title.lower() in _USELESS_TITLES:
        return None
    # "Microsoft Word - Q2 Review.docx" and friends.
    if _FILENAME_TITLE.search(title):
        return None
    return title


def _read_metadata(payload: bytes) -> tuple[str | None, int]:
    """Title and page count via pypdf, which does not lay out the pages."""
    try:
        reader = pypdf.PdfReader(BytesIO(payload))
        # An empty user password is common and decrypts silently; a real one
        # cannot be guessed and the document is unusable.
        if reader.is_encrypted and reader.decrypt("") == pypdf.PasswordType.NOT_DECRYPTED:
            raise DocumentProcessingError("The PDF is password-protected and cannot be indexed.")
        metadata = reader.metadata
        title = _usable_title(metadata.title) if metadata is not None else None
        page_count = len(reader.pages)
    except DocumentProcessingError:
        raise
    except Exception as exc:
        raise DocumentProcessingError("The PDF could not be read; it may be corrupt.") from exc

    clean_title = str(title).strip() if title else None
    return (clean_title or None), page_count


def _lines_on_page(page: Any, page_number: int) -> list[_Line]:
    """Group words into visual lines, carrying each line's dominant font size."""
    try:
        words = page.extract_words(extra_attrs=["size"], use_text_flow=False)
    except Exception:  # pragma: no cover - malformed page, skip rather than abort
        logger.warning("pdf_page_extraction_failed", page=page_number)
        return []

    lines: list[_Line] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        if not current:
            return
        text = " ".join(str(word["text"]) for word in current).strip()
        if not text:
            return
        sizes = [float(word.get("size") or 0.0) for word in current]
        lines.append(
            _Line(
                text=text,
                size=statistics.median(sizes) if sizes else 0.0,
                page=page_number,
                top=float(current[0]["top"]),
            )
        )

    for word in sorted(words, key=lambda w: (round(float(w["top"]), 1), float(w["x0"]))):
        if current and abs(float(word["top"]) - float(current[-1]["top"])) > LINE_TOLERANCE:
            flush()
            current = []
        current.append(word)
    flush()
    return lines


def _body_size(lines: list[_Line]) -> float:
    """The document's body font size, weighted by how much text is set in it.

    Weighting by character count stops a title page — a handful of very large
    lines — from being mistaken for the body.
    """
    weighted: list[float] = []
    for line in lines:
        if line.size > 0:
            weighted.extend([line.size] * max(1, len(line.text) // 10))
    return statistics.median(weighted) if weighted else 0.0


def _heading_level(size: float, body: float) -> int:
    ratio = size / body if body else 1.0
    if ratio >= 1.6:
        return 1
    if ratio >= 1.35:
        return 2
    return 3


def _is_heading(line: _Line, body: float) -> bool:
    if body <= 0 or not line.text:
        return False
    if len(line.text) > MAX_HEADING_CHARS:
        return False
    if line.size < body * HEADING_SIZE_RATIO:
        return False
    # A sentence that happens to be set large is still a sentence.
    return not line.text.rstrip().endswith((".", ";", ","))


def _starts_new_paragraph(previous: _Line, line: _Line, body: float) -> bool:
    """Whether a visible break separates two consecutive lines."""
    if line.page != previous.page:
        return True
    if body <= 0:
        return False
    # Normal leading is roughly 1.2x the font size; anything appreciably larger
    # is deliberate whitespace between paragraphs.
    return (line.top - previous.top) > body * PARAGRAPH_GAP_RATIO


def extract_pdf(payload: bytes) -> ExtractedDocument:
    title, page_count = _read_metadata(payload)

    all_lines: list[_Line] = []
    try:
        with pdfplumber.open(BytesIO(payload)) as pdf:
            for index, page in enumerate(pdf.pages, start=1):
                all_lines.extend(_lines_on_page(page, index))
    except Exception as exc:
        raise DocumentProcessingError("The PDF could not be parsed.") from exc

    body = _body_size(all_lines)

    blocks: list[Block] = []
    paragraph: list[str] = []
    paragraph_page: int | None = None

    def flush_paragraph() -> None:
        nonlocal paragraph, paragraph_page
        if paragraph:
            blocks.append(
                Block(
                    text=" ".join(paragraph),
                    kind=BlockKind.PARAGRAPH,
                    page=paragraph_page,
                )
            )
            paragraph = []
            paragraph_page = None

    previous: _Line | None = None
    for line in all_lines:
        if _is_heading(line, body):
            flush_paragraph()
            blocks.append(
                Block(
                    text=line.text,
                    kind=BlockKind.HEADING,
                    page=line.page,
                    level=_heading_level(line.size, body),
                )
            )
            previous = line
            continue

        # Without this, everything between two headings — often a whole page —
        # becomes a single block, and the chunker loses the paragraph boundaries
        # it would otherwise prefer to split on.
        if previous is not None and _starts_new_paragraph(previous, line, body):
            flush_paragraph()

        if paragraph_page is None:
            paragraph_page = line.page
        paragraph.append(line.text)
        previous = line
    flush_paragraph()

    if title is None:
        title = next((b.text for b in blocks if b.is_heading()), None)

    return ExtractedDocument(blocks=blocks, page_count=page_count, title=title)
