"""Upload validation — security control S4.

Format is decided by **content**, never by the filename. An attacker controls the
extension and the `Content-Type` header; they do not control the first four bytes
of a PDF. The declared extension is used only to break the tie between the two
OOXML formats, and even then it is checked against the archive's contents.

`python-magic` is deliberately not used. It needs `libmagic`, which means
`python-magic-bin` on Windows and `python-magic` on Linux — a split dependency for
a four-format allowlist. Sniffing four signatures by hand is a dozen lines, has no
platform story, and is easier to audit than a wrapped C library.

Zip-bomb defence lives here too: a DOCX or XLSX is a ZIP archive, and its declared
uncompressed size is checked before anything is decompressed.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from enum import StrEnum
from io import BytesIO
from pathlib import PurePath

from app.core.exceptions import ValidationError


class DocumentFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    TEXT = "text"
    MARKDOWN = "markdown"
    CSV = "csv"


#: What each format is stored and served as.
CONTENT_TYPES: dict[DocumentFormat, str] = {
    DocumentFormat.PDF: "application/pdf",
    DocumentFormat.DOCX: (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    DocumentFormat.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    DocumentFormat.TEXT: "text/plain",
    DocumentFormat.MARKDOWN: "text/markdown",
    DocumentFormat.CSV: "text/csv",
}

#: Extensions accepted at the door. Sniffing still has the final say.
ALLOWED_EXTENSIONS: dict[str, DocumentFormat] = {
    ".pdf": DocumentFormat.PDF,
    ".docx": DocumentFormat.DOCX,
    ".xlsx": DocumentFormat.XLSX,
    ".txt": DocumentFormat.TEXT,
    ".md": DocumentFormat.MARKDOWN,
    ".markdown": DocumentFormat.MARKDOWN,
    ".csv": DocumentFormat.CSV,
}

TEXT_FORMATS = frozenset({DocumentFormat.TEXT, DocumentFormat.MARKDOWN, DocumentFormat.CSV})

PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"
#: An empty archive, and a "spanned" one. Neither is a document.
ZIP_EMPTY_MAGIC = (b"PK\x05\x06", b"PK\x07\x08")

#: Expansion ratio above which an archive is treated as a decompression bomb.
MAX_ZIP_EXPANSION_RATIO = 200
MAX_ZIP_UNCOMPRESSED_BYTES = 512 * 1024 * 1024

#: Control characters that never appear in real text and reliably indicate a
#: binary file that was renamed to `.txt`.
_BINARY_CONTROL = bytes(range(0, 9)) + bytes(range(14, 32))

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]")
_COLLAPSE_DOTS = re.compile(r"\.{2,}")


def sanitise_filename(raw: str) -> str:
    """Reduce a client-supplied filename to something safe to store and display.

    The result is **never** used to build a path — uploads are written under a
    generated name — but it is echoed back into the UI and into prompts, so it is
    stripped of anything that could be used for traversal or injection.
    """
    # Take the basename under both separators: a Windows client sends
    # "C:\\Users\\x\\report.pdf" and PurePath alone would not split that on Linux.
    name = PurePath(raw.replace("\\", "/")).name
    name = _UNSAFE_FILENAME.sub("_", name).strip(" .")
    name = _COLLAPSE_DOTS.sub(".", name)
    if not name:
        return "document"
    return name[:200]


def _looks_like_text(payload: bytes) -> bool:
    sample = payload[:8192]
    if not sample:
        return False
    if b"\x00" in sample:
        return False
    if sum(sample.count(byte) for byte in _BINARY_CONTROL) > len(sample) * 0.02:
        return False
    # UTF-8 is the common case; anything that decodes as some 8-bit encoding
    # without control-character noise is accepted by the check above.
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        try:
            payload[:8192].decode("cp1252")
        except UnicodeDecodeError:
            return False
    return True


def _ooxml_kind(payload: bytes) -> DocumentFormat | None:
    """Distinguish DOCX from XLSX by what is inside the archive."""
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            names = set(archive.namelist())
            total_uncompressed = sum(info.file_size for info in archive.infolist())
    except (zipfile.BadZipFile, OSError):
        return None

    if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES or (
        len(payload) > 0 and total_uncompressed > len(payload) * MAX_ZIP_EXPANSION_RATIO
    ):
        raise ValidationError("The uploaded archive expands to an implausible size.")

    if "word/document.xml" in names:
        return DocumentFormat.DOCX
    if "xl/workbook.xml" in names:
        return DocumentFormat.XLSX
    return None


def sniff_format(payload: bytes, filename: str) -> DocumentFormat:
    """Determine the format from content, using the extension only as a hint."""
    extension = PurePath(filename.replace("\\", "/")).suffix.lower()
    declared = ALLOWED_EXTENSIONS.get(extension)
    if declared is None:
        raise ValidationError(
            "Unsupported file type. Accepted: " + ", ".join(sorted(ALLOWED_EXTENSIONS))
        )

    if payload.startswith(PDF_MAGIC):
        return DocumentFormat.PDF

    if payload.startswith(ZIP_MAGIC):
        detected = _ooxml_kind(payload)
        if detected is None:
            raise ValidationError("The file is a ZIP archive but not a .docx or .xlsx document.")
        if detected is not declared:
            raise ValidationError(
                f"File content is a {detected.value} document but the extension says "
                f"{declared.value}."
            )
        return detected

    if payload.startswith(ZIP_EMPTY_MAGIC):
        raise ValidationError("The uploaded archive is empty or spanned.")

    if declared in TEXT_FORMATS and _looks_like_text(payload):
        return declared

    # A .pdf that does not start with %PDF-, or a .txt full of NUL bytes.
    raise ValidationError(
        f"File content does not match its .{declared.value} extension, or is not readable text."
    )


def validate_upload(
    payload: bytes, filename: str, *, max_bytes: int
) -> tuple[DocumentFormat, str, str]:
    """Validate an upload and return `(format, sanitised_filename, sha256)`."""
    if not payload:
        raise ValidationError("The uploaded file is empty.")
    if len(payload) > max_bytes:
        limit_mb = max_bytes / (1024 * 1024)
        raise ValidationError(f"File exceeds the {limit_mb:.0f} MB upload limit.")

    safe_name = sanitise_filename(filename)
    document_format = sniff_format(payload, safe_name)
    digest = hashlib.sha256(payload).hexdigest()
    return document_format, safe_name, digest
