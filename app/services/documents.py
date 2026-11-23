"""Documents (FR-12): fetch the document behind each detected item, store it, extract its text.

- A document is stored write-once as data/documents/{internal_ref}/{sha256}.{ext}; its text beside
  it as {sha256}.txt. Both are immutable (rule 7); the database refuses to change a document row.
- RSS items that carry their text in the feed (RBI notifications) store that text as the document,
  so nothing more is fetched.
- Every other document is downloaded by the crawler (a Scrapy subprocess with the same polite
  settings and block detection as any crawl; rule 10). A block is never worked around: the item
  becomes NEEDS_MANUAL_FETCH and a person uploads the file.
- PDFs: text with PyMuPDF, then pdfplumber if that finds nothing. A PDF with almost no text (a
  scan) is flagged `needs_ocr`; the system does no OCR in the POC.
"""

import hashlib
import io
import json
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.repositories.base import Repository
from app.schemas.entities import Document, Source, now_ist
from app.schemas.enums import FetchStatus, ItemState, SourceType
from app.services.detection import DetectedEvent
from app.services.normalise import html_to_text

ACTOR = "documents"
ROOT = Path(__file__).resolve().parents[2]
MIN_CHARS_PER_PAGE = 25  # fewer than this on average: a scan, not a text document
TIMEOUT_SECONDS = 300


@dataclass
class FetchResult:
    url: str
    status: FetchStatus  # OK | BLOCKED | FAILED
    http_status: int | None = None
    content_type: str | None = None
    content: bytes | None = None
    detail: str | None = None


class DocumentFetcher(Protocol):
    def fetch(self, source: Source, urls: list[str]) -> list[FetchResult]: ...


@dataclass
class DocumentReport:
    stored: int = 0
    blocked: int = 0
    failed: int = 0
    needs_ocr: int = 0
    skipped: int = 0
    notes: list[str] = field(default_factory=list)


# --- text extraction ---------------------------------------------------------------------------


def extract_pdf_text(content: bytes) -> tuple[str, bool]:
    """(text, needs_ocr). PyMuPDF first, pdfplumber if it finds nothing."""
    pages: list[str] = []
    try:
        import pymupdf

        with pymupdf.open(stream=content, filetype="pdf") as doc:
            pages = [page.get_text() or "" for page in doc]
    except Exception:  # corrupt or unusual file: try the second reader
        pages = []
    if not "".join(pages).strip():
        try:
            import pdfplumber

            with pdfplumber.open(io.BytesIO(content)) as pdf:
                pages = [page.extract_text() or "" for page in pdf.pages]
        except Exception:
            pages = pages or []
    text = "\n".join(p.strip() for p in pages if p.strip())
    count = max(len(pages), 1)
    return text, len(text) < MIN_CHARS_PER_PAGE * count


def _kind(content: bytes, content_type: str | None) -> tuple[str, str]:
    """(mime, extension) from the bytes first, then the server's header."""
    if content[:5] == b"%PDF-":
        return "application/pdf", "pdf"
    lowered = (content_type or "").split(";")[0].strip().lower()
    if lowered == "application/pdf":
        return lowered, "pdf"
    if lowered in ("text/html", "application/xhtml+xml") or content.lstrip()[:1] == b"<":
        return "text/html", "html"
    return lowered or "application/octet-stream", "bin"


def _decode(content: bytes) -> str:
    for encoding in ("utf-8", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


class DocumentService:
    def __init__(self, repo: Repository, fetcher: DocumentFetcher) -> None:
        self._repo = repo
        self._fetcher = fetcher

    def process(self, events: list[DetectedEvent]) -> DocumentReport:
        report = DocumentReport()
        to_download: dict[str, list[DetectedEvent]] = {}
        for event in events:
            source = self._repo.get_source(event.item.source_id)
            if source is None or source.type == SourceType.STRUCTURED_DATA:
                report.skipped += 1  # the stored snapshot is the evidence for a data file
                continue
            description = event.row.raw_extra.get("description_html")
            if description:
                self._store(event, event.row.url, description.encode("utf-8"), "text/html",
                            "feed-description.html", report)  # fmt: skip
            elif event.row.url:
                to_download.setdefault(source.id, []).append(event)
        for source_id, group in to_download.items():
            source = self._repo.get_source(source_id)
            assert source is not None
            results = {r.url: r for r in self._fetcher.fetch(source, [e.row.url for e in group])}
            for event in group:
                self._apply(event, results.get(event.row.url), report)
        return report

    # --- one result ----------------------------------------------------------------------------

    def _apply(
        self, event: DetectedEvent, result: FetchResult | None, report: DocumentReport
    ) -> None:
        url = event.row.url
        if result is None or result.status == FetchStatus.FAILED:
            detail = result.detail if result else "No answer from the downloader"
            self._record(event, url, FetchStatus.FAILED, None, report)
            report.failed += 1
            report.notes.append(f"{event.item.title}: download failed ({detail})")
        elif result.status == FetchStatus.BLOCKED:
            self._record(event, url, FetchStatus.BLOCKED, ItemState.NEEDS_MANUAL_FETCH, report)
            report.blocked += 1
            report.notes.append(
                f"{event.item.title}: blocked ({result.detail}); needs a manual fetch"
            )
        else:
            assert result.content is not None
            self._store(event, url, result.content, result.content_type, None, report)

    def _record(
        self,
        event: DetectedEvent,
        url: str,
        status: FetchStatus,
        new_state: ItemState | None,
        report: DocumentReport,
    ) -> None:
        doc = Document(
            id=f"doc-{uuid.uuid4().hex[:10]}",
            item_id=event.item.id,
            url=url,
            fetch_status=status,
            fetched_at=now_ist(),
        )
        self._repo.add_document(doc, None, new_state, ACTOR)

    def _store(
        self,
        event: DetectedEvent,
        url: str,
        content: bytes,
        content_type: str | None,
        filename: str | None,
        report: DocumentReport,
    ) -> None:
        digest = hashlib.sha256(content).hexdigest()
        if any(
            d.sha256 == digest and d.fetch_status == FetchStatus.OK
            for d in self._repo.list_documents(event.item.id)
        ):
            report.skipped += 1  # exactly this document is already stored for the item
            return
        mime, ext = _kind(content, content_type)
        needs_ocr = False
        if ext == "pdf":
            text, needs_ocr = extract_pdf_text(content)
        elif ext == "html":
            text = html_to_text(_decode(content))
        else:
            text = ""
        base = f"data/documents/{event.item.internal_ref}/{digest}"
        doc = Document(
            id=f"doc-{uuid.uuid4().hex[:10]}",
            item_id=event.item.id,
            url=url,
            sha256=digest,
            path=f"{base}.{ext}",
            mime=mime,
            text_path=f"{base}.txt" if text.strip() else None,
            fetch_status=FetchStatus.OK,
            fetched_at=now_ist(),
            filename=filename,
            needs_ocr=needs_ocr,
        )
        self._repo.add_document(doc, content, None, ACTOR, text=text if text.strip() else None)
        report.stored += 1
        if needs_ocr:
            report.needs_ocr += 1
            report.notes.append(f"{event.item.title}: little or no text found (scan?); needs OCR")


class SubprocessDocumentFetcher:
    """Downloads with the crawler's `docs` spider, as a separate process, one source at a time."""

    def __init__(self, timeout: int = TIMEOUT_SECONDS) -> None:
        self._timeout = timeout

    def fetch(self, source: Source, urls: list[str]) -> list[FetchResult]:
        with tempfile.TemporaryDirectory(prefix="documents-") as tmp:
            folder = Path(tmp)
            (folder / "source.json").write_text(source.model_dump_json(), encoding="utf-8")
            (folder / "urls.json").write_text(json.dumps(urls), encoding="utf-8")
            command = [
                sys.executable, "-m", "scrapy", "crawl", "docs",
                "-a", "dry_run=1",
                "-a", f"source_json={folder / 'source.json'}",
                "-a", f"urls_json={folder / 'urls.json'}",
                "-a", f"out_dir={folder / 'files'}",
                "-a", f"results={folder / 'results.json'}",
                "-s", "LOG_FILE=", "-s", "LOG_LEVEL=WARNING",
            ]  # fmt: skip
            try:
                subprocess.run(  # noqa: S603 - fixed argument list, no shell
                    command, cwd=ROOT / "crawler", capture_output=True, text=True,
                    timeout=self._timeout, check=False,
                )  # fmt: skip
            except subprocess.TimeoutExpired:
                return [
                    FetchResult(u, FetchStatus.FAILED, detail="The download timed out")
                    for u in urls
                ]
            results_file = folder / "results.json"
            if not results_file.is_file():
                return [
                    FetchResult(u, FetchStatus.FAILED, detail="The downloader did not run")
                    for u in urls
                ]
            out: list[FetchResult] = []
            for row in json.loads(results_file.read_text(encoding="utf-8")):
                file = folder / "files" / row["file"] if row.get("file") else None
                out.append(
                    FetchResult(
                        url=row["url"],
                        status=FetchStatus(row["status"]),
                        http_status=row.get("http_status"),
                        content_type=row.get("content_type"),
                        content=file.read_bytes() if file and file.is_file() else None,
                        detail=row.get("detail"),
                    )
                )
            return out
