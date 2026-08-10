"""The common representation every format is normalised into.

Extraction's job is to turn a PDF, a DOCX, a spreadsheet, or a text file into the
same `Block` list. Everything downstream — cleaning, chunking, and therefore
citation precision — is written once against `Block` and is format-independent.
That is the reason this type exists rather than each extractor emitting its own
shape.

A block carries where it came from (`page`) and what it is (`kind`). `kind` is
what lets the chunker split on section boundaries rather than every N characters,
and what stops a table being torn in half mid-row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class BlockKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"


@dataclass(frozen=True, slots=True)
class Block:
    text: str
    kind: BlockKind = BlockKind.PARAGRAPH
    #: 1-based. `None` for formats with no pagination (text, spreadsheets).
    page: int | None = None
    #: 1-6 for headings, `None` otherwise. Drives the section path.
    level: int | None = None

    def is_heading(self) -> bool:
        return self.kind is BlockKind.HEADING


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    blocks: list[Block] = field(default_factory=list)
    #: `None` where the format has no concept of pages.
    page_count: int | None = None
    #: From document metadata where available, else the first heading.
    title: str | None = None

    @property
    def is_empty(self) -> bool:
        return not any(block.text.strip() for block in self.blocks)
