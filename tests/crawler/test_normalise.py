"""Normalisation: canonical URL, ISO dates, reference numbers (pure functions + the pipeline)."""

from datetime import date

import pytest
from scrapy.http import HtmlResponse

from app.services import normalise as n
from regcrawler.items import ListingItem
from regcrawler.pipelines import NormalisePipeline
from tests.crawler.conftest import fixture_bytes


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "HTTPS://WWW.RBI.org.in:443/Scripts/X.aspx?Id=1&Mode=0#top",
            "https://www.rbi.org.in/Scripts/X.aspx?Id=1&Mode=0",
        ),
        ("https://rbi.org.in/x.aspx?b=2&a=1", "https://rbi.org.in/x.aspx?a=1&b=2"),
        ("https://rbi.org.in/x.aspx?a=1&b=2", "https://rbi.org.in/x.aspx?a=1&b=2"),
        ("https://rbi.org.in/n/?utm_source=mail&fbclid=z&id=7", "https://rbi.org.in/n?id=7"),
        ("https://rbi.org.in//a///b/", "https://rbi.org.in/a/b"),
        ("https://rbi.org.in", "https://rbi.org.in/"),
        ("http://rbi.org.in:8080/a", "http://rbi.org.in:8080/a"),
        ("  https://rbi.org.in/a\u00a0", "https://rbi.org.in/a"),
    ],
)
def test_canonical_url(raw, expected) -> None:
    assert n.canonical_url(raw) == expected


def test_canonical_url_resolves_relative_links_against_the_page() -> None:
    assert (
        n.canonical_url("../n/5?x=1", base="https://rbi.org.in/a/b/")
        == "https://rbi.org.in/a/n/5?x=1"
    )


def test_different_spellings_of_one_address_agree() -> None:
    a = n.canonical_url("https://RBI.org.in/Scripts/N.aspx?Mode=0&Id=5#frag")
    b = n.canonical_url("https://rbi.org.in:443/Scripts/N.aspx?Id=5&Mode=0&utm_medium=x")
    assert a == b


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-11-20", date(2026, 11, 20)),
        ("20.11.2026", date(2026, 11, 20)),
        ("20/11/2026", date(2026, 11, 20)),  # day first, as Indian regulators write it
        ("05/11/2026", date(2026, 11, 5)),  # never read as May 11
        ("20 Nov 2026", date(2026, 11, 20)),
        ("Nov 20, 2026", date(2026, 11, 20)),
        ("20 November 2026", date(2026, 11, 20)),
        ("Fri, 20 Nov 2026 10:00:00 +0530", date(2026, 11, 20)),  # RSS pubDate
        ("2026-11-20T10:30:00+05:30", date(2026, 11, 20)),
        (date(2026, 11, 20), date(2026, 11, 20)),
    ],
)
def test_dates_become_iso_dates(raw, expected) -> None:
    assert n.parse_date(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "yesterday", "32/13/2026", "Updated recently"])
def test_unreadable_dates_are_none_not_guessed(raw) -> None:
    assert n.parse_date(raw) is None


def test_a_configured_date_format_is_strict() -> None:
    assert n.parse_date("11/20/2026", "%m/%d/%Y") == date(2026, 11, 20)
    assert n.parse_date("20/11/2026", "%m/%d/%Y") is None


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("RBI/2026-27/45 Master Direction on KYC", "RBI/2026-27/45"),
        (
            "Circular DOR.CRE.REC.No.12/03.10.001/2026-27 on lending",
            "DOR.CRE.REC.No.12/03.10.001/2026-27",
        ),
        ("Notification FEMA 23(R)/2026-RB amended", "FEMA 23(R)/2026-RB"),
        ("Extraordinary CG-DL-E-12112026-266901 gazette", "CG-DL-E-12112026-266901"),
        ("A press release with no number", None),
    ],
)
def test_reference_numbers_are_found_in_titles(title, expected) -> None:
    assert n.find_ref_no(title) == expected


def test_a_source_specific_pattern_wins() -> None:
    assert n.find_ref_no("Order ABC-77 of today", r"ABC-\d+") == "ABC-77"


def test_ref_no_is_cleaned() -> None:
    assert n.normalise_ref_no("  RBI/2026-27/45 . ") == "RBI/2026-27/45"
    assert n.normalise_ref_no("  ") is None


# --- the pipeline, on rows read from a saved page ---------------------------------------------


def rows_from_fixture() -> list[ListingItem]:
    response = HtmlResponse(
        "https://rbi.org.in/notifications", body=fixture_bytes("html", "rbi_listing_sample.html")
    )
    out = []
    for tr in response.css("table.listing tr"):
        out.append(
            ListingItem(
                channel="HTML",
                url=response.urljoin(tr.css("a::attr(href)").get()),  # spiders emit absolute URLs
                title=tr.css("a").xpath("string()").get(),
                displayed_date=tr.css("td:last-child::text").get(),
            )
        )
    return out


def test_pipeline_normalises_rows_from_a_saved_page(crawler) -> None:
    pipeline = NormalisePipeline(crawler)
    first, second = (pipeline.process_item(r) for r in rows_from_fixture())
    assert first["url"] == "https://rbi.org.in/Scripts/NotificationUser.aspx?Id=12345&Mode=0"
    assert first["title"] == "Master Direction \u2013 Know Your Customer (KYC) Direction, 2026"
    assert first["displayed_date"] == date(2026, 11, 20)
    assert second["displayed_date"] == date(2026, 11, 20)
    assert second["ref_no"] == "DOR.CRE.REC.No.12/03.10.001/2026-27"
    assert first["raw_extra"]["url_as_found"].endswith("#top")
    assert second["url"] == "https://rbi.org.in/Scripts/NotificationUser.aspx?Id=12346&Mode=0"


def test_pipeline_keeps_the_raw_date_text_when_it_cannot_be_read(crawler) -> None:
    item = NormalisePipeline(crawler).process_item(
        ListingItem(channel="HTML", url="https://rbi.org.in/a", title="T", displayed_date="soon")
    )
    assert item["displayed_date"] is None
    assert item["raw_extra"]["displayed_date_raw"] == "soon"


def test_pipeline_never_drops_a_row_and_falls_back_to_the_url_as_title(crawler) -> None:
    item = NormalisePipeline(crawler).process_item(
        ListingItem(channel="HTML", url="https://rbi.org.in/a", title="  ")
    )
    assert item["title"] == "https://rbi.org.in/a"


def test_a_sources_html_date_format_is_not_applied_to_its_feed(crawler) -> None:
    # rbi's adapter_config says "%b %d, %Y" for its HTML pages; an RSS pubDate is RFC 822.
    crawler.spider.source.adapter_config["date_format"] = "%b %d, %Y"
    pipeline = NormalisePipeline(crawler)
    rss = pipeline.process_item(
        ListingItem(
            channel="RSS",
            url="https://rbi.org.in/a",
            title="T",
            displayed_date="Fri, 20 Nov 2026 17:45:00 +0530",
        )
    )
    html = pipeline.process_item(
        ListingItem(
            channel="HTML", url="https://rbi.org.in/b", title="T", displayed_date="Nov 20, 2026"
        )
    )
    assert rss["displayed_date"] == date(2026, 11, 20)
    assert html["displayed_date"] == date(2026, 11, 20)
    wrong = pipeline.process_item(
        ListingItem(
            channel="HTML", url="https://rbi.org.in/c", title="T", displayed_date="20/11/2026"
        )
    )
    assert wrong["displayed_date"] is None  # strict for HTML, as configured
