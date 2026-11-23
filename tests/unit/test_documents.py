"""Documents: store write-once with SHA-256, extract text, NEEDS_OCR, blocked -> NEEDS_MANUAL_FETCH.

The downloader is a fake: no network, no Scrapy process."""

import hashlib
from datetime import date

import pymupdf
import pytest

from app.schemas.enums import FetchStatus, ItemState, SourceType
from app.services.detection import DetectionService
from app.services.documents import DocumentService, FetchResult, extract_pdf_text
from tests.conftest import BACKENDS, make_backend
from tests.detection_helpers import crawl, make_source, row

D1 = date(2026, 11, 20)
SRC = "det-both"


def pdf_bytes(*pages: str) -> bytes:
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    return doc.tobytes()


TEXT_PDF = pdf_bytes(
    "Master Direction on KYC. These Directions come into force with effect from 01.12.2026. "
    "They are applicable to all NBFCs."
)
SCANNED_PDF = pdf_bytes("", "")  # two pages, no text layer at all
HTML_PAGE = (
    b"<html><head><style>p{}</style></head><body><h1>Notice</h1>"
    b"<p>Applicable to NBFCs.</p><script>x()</script></body></html>"
)


class FakeFetcher:
    def __init__(self, answers: dict[str, FetchResult]) -> None:
        self.answers = answers
        self.asked: list[tuple[str, list[str]]] = []

    def fetch(self, source, urls):
        self.asked.append((source.id, list(urls)))
        return [self.answers[u] for u in urls if u in self.answers]


def ok(url, content, content_type="application/pdf"):
    return FetchResult(url, FetchStatus.OK, 200, content_type, content)


@pytest.fixture(params=BACKENDS)
def repo(request, tmp_path, sql_template):
    repository, _url, _files = make_backend(request.param, tmp_path, sql_template)
    make_source(repository, SRC)
    return repository


def detected(repo, *rows):
    run = crawl(repo, SRC, D1, html=list(rows))
    return DetectionService(repo).process(run.id).events


def service(repo, answers):
    fetcher = FakeFetcher(answers)
    return DocumentService(repo, fetcher), fetcher


# --- PDF text ----------------------------------------------------------------------------------


def test_text_is_extracted_from_a_pdf() -> None:
    text, needs_ocr = extract_pdf_text(TEXT_PDF)
    assert "with effect from 01.12.2026" in text and needs_ocr is False


def test_a_pdf_without_a_text_layer_needs_ocr() -> None:
    text, needs_ocr = extract_pdf_text(SCANNED_PDF)
    assert text == "" and needs_ocr is True


def test_a_pdf_with_almost_no_text_needs_ocr() -> None:
    assert extract_pdf_text(pdf_bytes("Page 1", "Page 2", "Page 3"))[1] is True


def test_pdfplumber_is_used_when_pymupdf_cannot_read_the_file(monkeypatch) -> None:
    def broken(*args, **kwargs):
        raise RuntimeError("cannot open")

    monkeypatch.setattr(pymupdf, "open", broken)
    text, needs_ocr = extract_pdf_text(TEXT_PDF)
    assert "Master Direction on KYC" in text and needs_ocr is False


def test_a_corrupt_pdf_is_flagged_not_crashed() -> None:
    assert extract_pdf_text(b"%PDF-1.4 this is not a pdf") == ("", True)


# --- storing -----------------------------------------------------------------------------------


def test_a_downloaded_pdf_is_stored_hashed_with_its_text(repo) -> None:
    (event,) = detected(repo, row("KYC Directions", "A/1", D1, url="https://example.test/a.pdf"))
    svc, fetcher = service(
        repo, {"https://example.test/a.pdf": ok("https://example.test/a.pdf", TEXT_PDF)}
    )
    report = svc.process([event])
    assert (report.stored, report.blocked, report.needs_ocr) == (1, 0, 0)
    assert fetcher.asked == [(SRC, ["https://example.test/a.pdf"])]
    (doc,) = repo.list_documents(event.item.id)
    digest = hashlib.sha256(TEXT_PDF).hexdigest()
    assert (
        doc.sha256 == digest
        and doc.mime == "application/pdf"
        and doc.fetch_status == FetchStatus.OK
    )
    assert doc.path == f"data/documents/{event.item.internal_ref}/{digest}.pdf"
    assert doc.text_path == f"data/documents/{event.item.internal_ref}/{digest}.txt"
    assert repo.read_blob(digest) == TEXT_PDF  # byte for byte
    assert "01.12.2026" in repo.read_text(doc.id)
    assert any(e.action == "document.added" and e.actor == "documents" for e in repo.list_audit())


def test_a_scanned_pdf_is_stored_and_flagged_for_ocr(repo) -> None:
    (event,) = detected(repo, row("Scanned", "A/1", D1, url="https://example.test/s.pdf"))
    svc, _ = service(
        repo, {"https://example.test/s.pdf": ok("https://example.test/s.pdf", SCANNED_PDF)}
    )
    report = svc.process([event])
    (doc,) = repo.list_documents(event.item.id)
    assert report.needs_ocr == 1 and doc.needs_ocr is True and doc.text_path is None
    assert doc.fetch_status == FetchStatus.OK and repo.read_blob(doc.sha256) == SCANNED_PDF


def test_an_html_page_is_stored_and_its_text_extracted(repo) -> None:
    (event,) = detected(repo, row("Page", "A/1", D1, url="https://example.test/p"))
    svc, _ = service(
        repo,
        {
            "https://example.test/p": ok(
                "https://example.test/p", HTML_PAGE, "text/html; charset=utf-8"
            )
        },
    )
    svc.process([event])
    (doc,) = repo.list_documents(event.item.id)
    text = repo.read_text(doc.id)
    assert doc.mime == "text/html" and doc.path.endswith(".html")
    assert "Applicable to NBFCs." in text and "x()" not in text and "p{}" not in text


def test_feed_text_is_stored_as_the_document_without_downloading(repo) -> None:
    (event,) = detected(
        repo,
        row("RBI notice", "A/1", D1, description_html="<p>RBI/2026-27/45</p><p>All NBFCs ...</p>"),
    )
    svc, fetcher = service(repo, {})
    report = svc.process([event])
    (doc,) = repo.list_documents(event.item.id)
    assert report.stored == 1 and fetcher.asked == []  # nothing was fetched
    assert doc.filename == "feed-description.html" and "All NBFCs" in repo.read_text(doc.id)


def test_the_same_document_is_not_stored_twice(repo) -> None:
    (event,) = detected(repo, row("KYC", "A/1", D1, url="https://example.test/a.pdf"))
    svc, _ = service(
        repo, {"https://example.test/a.pdf": ok("https://example.test/a.pdf", TEXT_PDF)}
    )
    svc.process([event])
    report = svc.process([event])
    assert report.stored == 0 and report.skipped == 1
    assert len(repo.list_documents(event.item.id)) == 1


def test_a_changed_document_is_a_new_stored_copy_and_the_old_one_stays(repo) -> None:
    (event,) = detected(repo, row("KYC", "A/1", D1, url="https://example.test/a.pdf"))
    first = service(
        repo, {"https://example.test/a.pdf": ok("https://example.test/a.pdf", TEXT_PDF)}
    )[0]
    first.process([event])
    newer = pdf_bytes(
        "Amended KYC directions, effective immediately. Applicable to all NBFCs and more text."
    )
    second = service(repo, {"https://example.test/a.pdf": ok("https://example.test/a.pdf", newer)})[
        0
    ]
    second.process([event])
    docs = repo.list_documents(event.item.id)
    assert len(docs) == 2 and repo.read_blob(docs[0].sha256) == TEXT_PDF


# --- blocked and failed ------------------------------------------------------------------------


def test_a_blocked_download_needs_a_manual_fetch(repo) -> None:
    (event,) = detected(repo, row("Blocked", "A/1", D1, url="https://example.test/b.pdf"))
    blocked = FetchResult("https://example.test/b.pdf", FetchStatus.BLOCKED, 403, detail="HTTP 403")
    svc, _ = service(repo, {"https://example.test/b.pdf": blocked})
    report = svc.process([event])
    (doc,) = repo.list_documents(event.item.id)
    assert report.blocked == 1 and "manual" in report.notes[0]
    assert doc.fetch_status == FetchStatus.BLOCKED and doc.sha256 is None
    assert repo.get_item(event.item.id).state == ItemState.NEEDS_MANUAL_FETCH


def test_a_failed_download_is_recorded_and_the_item_stays_detected(repo) -> None:
    (event,) = detected(repo, row("Broken", "A/1", D1, url="https://example.test/c.pdf"))
    failed = FetchResult("https://example.test/c.pdf", FetchStatus.FAILED, 500, detail="HTTP 500")
    svc, _ = service(repo, {"https://example.test/c.pdf": failed})
    report = svc.process([event])
    assert report.failed == 1
    assert repo.list_documents(event.item.id)[0].fetch_status == FetchStatus.FAILED
    assert repo.get_item(event.item.id).state == ItemState.DETECTED


def test_a_missing_answer_counts_as_failed(repo) -> None:
    (event,) = detected(repo, row("Silent", "A/1", D1, url="https://example.test/d.pdf"))
    svc, _ = service(repo, {})
    assert svc.process([event]).failed == 1


def test_a_data_file_source_is_not_downloaded(repo) -> None:
    make_source(repo, "det-xml", rss_url=None, type=SourceType.STRUCTURED_DATA)
    from tests.detection_helpers import crawl_xml

    run = crawl_xml(repo, "det-xml", D1, [row("UNSC added", "QDi.9", None)])
    events = DetectionService(repo).process(run.id).events
    svc, fetcher = service(repo, {})
    assert svc.process(events).skipped == 1 and fetcher.asked == []
