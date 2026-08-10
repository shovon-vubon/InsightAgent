"""Upload validation — security control S4.

These are security tests, not format tests: each one asserts that a specific
attack is refused. The theme is that **the filename is never trusted**. An
attacker controls the extension, the declared content type, and the name; they do
not control the file's first bytes.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.core.exceptions import ValidationError
from app.rag.ingestion.validation import (
    DocumentFormat,
    sanitise_filename,
    sniff_format,
    validate_upload,
)

PDF_BYTES = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\ntrailer\n"
ELF_BYTES = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 64


def _ooxml(marker: str, *, payload_size: int = 512) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(marker, "x" * payload_size)
    return buffer.getvalue()


class TestFilenameSanitisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # Only the basename survives, so traversal segments are discarded
            # rather than flattened into the stored name.
            ("../../../etc/passwd", "passwd"),
            (r"..\..\windows\system32\config", "config"),
            ("C:\\Users\\victim\\report.pdf", "report.pdf"),
            ("/absolute/path/report.pdf", "report.pdf"),
            ("normal report (final).pdf", "normal report _final_.pdf"),
            ("....pdf", "pdf"),
            ("", "document"),
            ("   ", "document"),
        ],
    )
    def test_traversal_and_separators_are_stripped(self, raw: str, expected: str) -> None:
        assert sanitise_filename(raw) == expected

    def test_no_separator_survives(self) -> None:
        for raw in ["a/b/c.pdf", r"a\b\c.pdf", "..%2f..%2fetc.pdf"]:
            cleaned = sanitise_filename(raw)
            assert "/" not in cleaned
            assert "\\" not in cleaned
            assert not cleaned.startswith(".")

    def test_length_is_bounded(self) -> None:
        assert len(sanitise_filename("a" * 500 + ".pdf")) <= 200


class TestContentSniffing:
    def test_pdf_is_recognised(self) -> None:
        assert sniff_format(PDF_BYTES, "report.pdf") is DocumentFormat.PDF

    def test_executable_renamed_as_pdf_is_rejected(self) -> None:
        # The core of S4: extension says pdf, content says ELF binary.
        with pytest.raises(ValidationError, match="does not match"):
            sniff_format(ELF_BYTES, "payload.pdf")

    def test_executable_renamed_as_text_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            sniff_format(ELF_BYTES, "payload.txt")

    def test_unknown_extension_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Unsupported file type"):
            sniff_format(PDF_BYTES, "payload.exe")

    def test_docx_is_distinguished_from_xlsx_by_content(self) -> None:
        assert sniff_format(_ooxml("word/document.xml"), "a.docx") is DocumentFormat.DOCX
        assert sniff_format(_ooxml("xl/workbook.xml"), "a.xlsx") is DocumentFormat.XLSX

    def test_xlsx_renamed_as_docx_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="content is a xlsx"):
            sniff_format(_ooxml("xl/workbook.xml"), "disguised.docx")

    def test_plain_zip_renamed_as_docx_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"not a \.docx or \.xlsx"):
            sniff_format(_ooxml("random/file.txt"), "archive.docx")

    def test_text_with_null_bytes_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            sniff_format(b"hello\x00\x00\x00world" + b"\x00" * 100, "notes.txt")

    def test_utf8_text_is_accepted(self) -> None:
        assert sniff_format("Revenue rose 12% — notably in EMEA.".encode(), "n.txt") is (
            DocumentFormat.TEXT
        )


class TestZipBomb:
    def test_absurd_expansion_ratio_is_rejected(self) -> None:
        # A tiny archive declaring an enormous uncompressed payload.
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", "0" * (60 * 1024 * 1024))
        with pytest.raises(ValidationError, match="implausible size"):
            sniff_format(buffer.getvalue(), "bomb.docx")


class TestUploadLimits:
    def test_empty_upload_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="empty"):
            validate_upload(b"", "empty.pdf", max_bytes=1024)

    def test_oversized_upload_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="upload limit"):
            validate_upload(PDF_BYTES + b"x" * 4096, "big.pdf", max_bytes=1024)

    def test_returns_format_name_and_digest(self) -> None:
        document_format, name, digest = validate_upload(
            PDF_BYTES, "../../report.pdf", max_bytes=1_000_000
        )
        assert document_format is DocumentFormat.PDF
        assert name == "report.pdf"
        assert len(digest) == 64

    def test_digest_is_content_addressed(self) -> None:
        _, _, first = validate_upload(PDF_BYTES, "a.pdf", max_bytes=1_000_000)
        _, _, second = validate_upload(PDF_BYTES, "b.pdf", max_bytes=1_000_000)
        # Same bytes under a different name must dedupe.
        assert first == second
