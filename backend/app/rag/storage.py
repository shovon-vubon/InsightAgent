"""Artifact store for uploaded originals.

A local filesystem store behind a narrow interface, so the S3/MinIO swap in Phase
13 is one class rather than a search-and-replace.

Security control S4 lives here. Files are written under a **generated** name
derived from the document's UUID, never from anything the client sent. The user's
filename is metadata in the database and is never a path component, which removes
directory traversal as a category rather than trying to filter for it — there is
no code path in which client input reaches the filesystem.

Every returned path is re-checked against the storage root before use, so even a
corrupted database row cannot make a read escape the directory.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from app.core.exceptions import DocumentProcessingError, NotFoundError
from app.core.logging import get_logger
from app.rag.ingestion.validation import DocumentFormat

logger = get_logger(__name__)

#: Extension per format, applied to the generated name. Cosmetic — nothing
#: downstream trusts it — but it makes the store browsable during debugging.
_SUFFIXES: dict[DocumentFormat, str] = {
    DocumentFormat.PDF: ".pdf",
    DocumentFormat.DOCX: ".docx",
    DocumentFormat.XLSX: ".xlsx",
    DocumentFormat.TEXT: ".txt",
    DocumentFormat.MARKDOWN: ".md",
    DocumentFormat.CSV: ".csv",
}


class DocumentStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def ensure_ready(self) -> None:
        """Create the storage root. Called once at startup."""
        try:
            self._root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DocumentProcessingError(
                "The document storage directory is not writable."
            ) from exc

    def _relative_path(self, user_id: uuid.UUID, document_id: uuid.UUID, suffix: str) -> str:
        # Sharded by user so one directory never accumulates every upload on the
        # instance, which makes listing and per-user deletion cheap.
        return f"{user_id}/{document_id}{suffix}"

    def resolve(self, relative_path: str) -> Path:
        """Turn a stored relative path into an absolute one, verifying containment."""
        candidate = (self._root / relative_path).resolve()
        root = self._root.resolve()
        if not candidate.is_relative_to(root):
            # Only reachable if a stored path was tampered with; treated as a
            # security event rather than a bad request.
            logger.error("storage_path_escape_attempt", relative_path=relative_path)
            raise NotFoundError("The document file is unavailable.")
        return candidate

    async def write(
        self,
        payload: bytes,
        *,
        user_id: uuid.UUID,
        document_id: uuid.UUID,
        document_format: DocumentFormat,
    ) -> str:
        """Persist the original and return the path to store on the row."""
        relative = self._relative_path(user_id, document_id, _SUFFIXES[document_format])
        target = self.resolve(relative)

        def _write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Write-then-rename: a crash mid-write leaves a .part file rather
            # than a truncated document that would later parse as corrupt.
            temporary = target.with_suffix(target.suffix + ".part")
            temporary.write_bytes(payload)
            temporary.replace(target)

        try:
            await asyncio.to_thread(_write)
        except OSError as exc:
            logger.error("storage_write_failed", document_id=str(document_id))
            raise DocumentProcessingError("The uploaded file could not be stored.") from exc

        return relative

    async def read(self, relative_path: str) -> bytes:
        target = self.resolve(relative_path)
        try:
            return await asyncio.to_thread(target.read_bytes)
        except FileNotFoundError as exc:
            raise NotFoundError("The document file is no longer available.") from exc
        except OSError as exc:
            raise DocumentProcessingError("The document file could not be read.") from exc

    async def delete(self, relative_path: str) -> None:
        """Remove a stored file. Missing is success — deletion is idempotent."""
        target = self.resolve(relative_path)

        def _delete() -> None:
            target.unlink(missing_ok=True)

        try:
            await asyncio.to_thread(_delete)
        except OSError:
            # The database row is already gone by this point. Logging and moving
            # on beats failing a delete the user has been told succeeded.
            logger.warning("storage_delete_failed", relative_path=relative_path)
