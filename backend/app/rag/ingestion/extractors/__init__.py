"""Format-specific extraction, dispatched on the sniffed format.

Every extractor returns an `ExtractedDocument`, so nothing downstream branches on
file type. Adding a format means adding one module and one dispatch entry.

All four libraries here are permissively licensed — pypdf (BSD), pdfplumber (MIT),
python-docx (MIT), openpyxl (MIT). PyMuPDF is the more convenient PDF library and
is deliberately not used: it is AGPL-3.0, which would be incompatible with
publishing this repository under MIT (plan P5).
"""

from __future__ import annotations

from app.core.exceptions import DocumentProcessingError
from app.rag.ingestion.blocks import ExtractedDocument
from app.rag.ingestion.extractors.docx import extract_docx
from app.rag.ingestion.extractors.pdf import extract_pdf
from app.rag.ingestion.extractors.tabular import extract_csv, extract_xlsx
from app.rag.ingestion.extractors.text import extract_markdown, extract_text
from app.rag.ingestion.validation import DocumentFormat

__all__ = ["extract"]


def extract(payload: bytes, document_format: DocumentFormat) -> ExtractedDocument:
    """Normalise raw bytes into blocks."""
    match document_format:
        case DocumentFormat.PDF:
            extracted = extract_pdf(payload)
        case DocumentFormat.DOCX:
            extracted = extract_docx(payload)
        case DocumentFormat.XLSX:
            extracted = extract_xlsx(payload)
        case DocumentFormat.CSV:
            extracted = extract_csv(payload)
        case DocumentFormat.MARKDOWN:
            extracted = extract_markdown(payload)
        case DocumentFormat.TEXT:
            extracted = extract_text(payload)

    if extracted.is_empty:
        # The commonest real cause is a scanned PDF with no text layer. Saying so
        # is far more useful than "processing failed", and OCR is out of scope.
        raise DocumentProcessingError(
            "No text could be extracted. If this is a scanned document, it needs "
            "OCR before it can be indexed."
        )
    return extracted
