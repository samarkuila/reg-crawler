"""Detected items reach the Daily Review: list by cycle, detail with the FR-34/FR-35 pre-fill."""

from datetime import date

from app.api.deps import get_services
from app.schemas.entities import Document
from app.schemas.enums import FetchStatus
from app.services.detection import DetectionService
from tests.api.conftest import SAMARENDRA, as_user
from tests.detection_helpers import crawl, make_source, row

API = "/api/v1"
D = date(2026, 12, 1)
CYCLE = "2026-12-01-ADHOC"
TEXT = (
    "Directions on Know Your Customer. These Directions shall come into force with effect from "
    "05.12.2026. They are applicable to all system participants."
)


def detect_one(repo, **extra):
    make_source(repo, "det-both")
    run = crawl(
        repo, "det-both", D, html=[row("KYC Directions for NBFCs", "RBI/2026-27/77", D, **extra)]
    )
    (event,) = DetectionService(repo).process(run.id).events
    return event.item


def test_a_detected_item_is_in_the_daily_review_list(client) -> None:
    repo = get_services().repo
    item = detect_one(repo)
    body = as_user(client, SAMARENDRA).get(f"{API}/items", params={"cycle": CYCLE}).json()
    found = next(i for i in body["items"] if i["id"] == item.id)
    assert (
        found["title"] == "KYC Directions for NBFCs" and found["internal_ref"] == item.internal_ref
    )
    assert found["change_type"] == "NEW" and found["state"] == "DETECTED"
    assert found["relevance_score"] >= 50 and "KYC" in item.relevance_reason


def test_a_low_relevance_item_is_listed_collapsed_never_hidden(client) -> None:
    repo = get_services().repo
    make_source(repo, "det-both")
    run = crawl(
        repo, "det-both", D, html=[row("Auction of Government Securities", "RBI/2026-27/78", D)]
    )
    (event,) = DetectionService(repo).process(run.id).events
    body = client.get(f"{API}/items", params={"cycle": CYCLE}).json()
    summary = next(i for i in body["items"] if i["id"] == event.item.id)
    assert summary["section"] == "low_relevance" and body["counts"]["low_relevance"] >= 1


def test_the_prefill_uses_the_document_text(client) -> None:
    repo = get_services().repo
    item = detect_one(repo)
    repo.add_document(
        Document(
            id="doc-det-1",
            item_id=item.id,
            fetch_status=FetchStatus.OK,
            sha256="a" * 64,
            path="data/documents/x/a.html",
            text_path="data/documents/x/a.txt",
        ),
        None,
        None,
        "documents",
        text=TEXT,
    )
    detail = as_user(client, SAMARENDRA).get(f"{API}/items/{item.id}").json()
    prefill = detail["prefill"]
    assert prefill["effective_date"] == "2026-12-05"
    assert prefill["effective_date_rule"].startswith("Date stated in the text")
    assert "with effect from 05.12.2026" in prefill["effective_date_rule"]
    hint = prefill["applicability_hint"]
    assert hint["suggested"] in ("NO", "TBC") and "system participant" in hint["text"]
    assert hint["phrase"].startswith("applicable to system participants")


def test_without_text_the_default_rule_is_shown_and_there_is_no_hint(client) -> None:
    repo = get_services().repo
    item = detect_one(repo)
    prefill = as_user(client, SAMARENDRA).get(f"{API}/items/{item.id}").json()["prefill"]
    assert prefill["effective_date"] == "2026-12-01"
    assert prefill["effective_date_rule"] == "No effective date found: using the issue date"
    assert prefill["applicability_hint"] is None
