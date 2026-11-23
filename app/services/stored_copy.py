"""Stored copies (snapshots and documents) are served only after their SHA-256 is re-checked.

Rule 7: stored evidence is immutable. If the bytes no longer match the recorded hash we refuse to
show them rather than show something that could have been changed.
"""

import hashlib
from dataclasses import dataclass

from app.repositories.base import Repository
from app.services.errors import ConflictError, NotFoundError


@dataclass
class StoredFile:
    content: bytes
    filename: str
    media_type: str


def _media_type(mime: str | None) -> str:
    # Never serve fetched HTML as HTML: it is shown as text so nothing in it can run.
    if mime == "application/pdf":
        return "application/pdf"
    if mime is None or mime.startswith(("text/", "application/xml")):
        return "text/plain; charset=utf-8"
    return "application/octet-stream"


def load_verified(repo: Repository, sha256: str | None, name: str, mime: str | None) -> StoredFile:
    content = repo.read_blob(sha256) if sha256 else None
    if content is None:
        raise NotFoundError("The stored copy is not available.")
    if hashlib.sha256(content).hexdigest() != sha256:
        raise ConflictError(
            "The stored copy does not match its recorded SHA-256, so it is not shown."
        )
    return StoredFile(content, name, _media_type(mime))
