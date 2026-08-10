"""Plain-text and Markdown extraction.

Markdown gets real structure for free: `#` prefixes and setext underlines are
unambiguous headings, and list markers are unambiguous list items. Plain text has
none of that, so it is split on blank lines and everything becomes a paragraph.

Decoding is explicit. A file uploaded from Windows is frequently cp1252 rather
than UTF-8, and `bytes.decode()` with the wrong codec either raises or silently
turns quotation marks into mojibake that then gets embedded and retrieved.
"""

from __future__ import annotations

import re

from charset_normalizer import from_bytes

from app.rag.ingestion.blocks import Block, BlockKind, ExtractedDocument

_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*#*$")
_SETEXT_UNDERLINE = re.compile(r"^(=+|-{2,})\s*$")
_LIST_ITEM = re.compile(r"^\s{0,3}(?:[-*+]|\d+[.)])\s+(.*)$")
_FENCE = re.compile(r"^\s{0,3}(?:```|~~~)")


def decode(payload: bytes) -> str:
    """Decode bytes to text, detecting the encoding rather than assuming it."""
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        pass

    best = from_bytes(payload).best()
    if best is not None:
        return str(best)
    # Last resort: cp1252 maps every byte, so this cannot raise. Replacement is
    # preferable to rejecting a document over a handful of stray bytes.
    return payload.decode("cp1252", errors="replace")


def _normalise_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def extract_text(payload: bytes) -> ExtractedDocument:
    text = _normalise_newlines(decode(payload))

    blocks = [
        Block(text=paragraph.strip(), kind=BlockKind.PARAGRAPH)
        for paragraph in re.split(r"\n\s*\n", text)
        if paragraph.strip()
    ]
    title = blocks[0].text[:200] if blocks else None
    return ExtractedDocument(blocks=blocks, page_count=None, title=title)


def extract_markdown(payload: bytes) -> ExtractedDocument:
    lines = _normalise_newlines(decode(payload)).split("\n")

    blocks: list[Block] = []
    paragraph: list[str] = []
    in_fence = False

    def flush() -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append(Block(text=" ".join(paragraph).strip(), kind=BlockKind.PARAGRAPH))
            paragraph = []

    for index, raw_line in enumerate(lines):
        line = raw_line.rstrip()

        if _FENCE.match(line):
            # Inside a fenced code block, `#` is a comment and `-` is a minus
            # sign. Treating them as structure would shred the code.
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            paragraph.append(raw_line)
            continue

        if not line.strip():
            flush()
            continue

        atx = _ATX_HEADING.match(line)
        if atx:
            flush()
            blocks.append(Block(text=atx.group(2), kind=BlockKind.HEADING, level=len(atx.group(1))))
            continue

        # Setext: the *next* line underlines this one.
        following = lines[index + 1].rstrip() if index + 1 < len(lines) else ""
        if paragraph == [] and following and _SETEXT_UNDERLINE.match(following):
            blocks.append(
                Block(
                    text=line.strip(),
                    kind=BlockKind.HEADING,
                    level=1 if following.startswith("=") else 2,
                )
            )
            lines[index + 1] = ""
            continue

        item = _LIST_ITEM.match(line)
        if item:
            flush()
            blocks.append(Block(text=item.group(1).strip(), kind=BlockKind.LIST_ITEM))
            continue

        paragraph.append(line.strip())

    flush()
    title = next((b.text for b in blocks if b.is_heading()), None)
    if title is None and blocks:
        title = blocks[0].text[:200]
    return ExtractedDocument(blocks=blocks, page_count=None, title=title)
