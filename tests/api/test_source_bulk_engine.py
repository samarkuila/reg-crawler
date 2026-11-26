"""CSV import: the optional fetch_engine, wait_for_selector and wait_timeout_s columns."""

import csv
import io

from app.services.source_bulk import COLUMNS, OPTIONAL_COLUMNS
from tests.api.conftest import SAMARENDRA, as_user
from tests.api.test_source_bulk import BASE, GOOD_HTML, _by_row, _csv, _preview

ENGINE_COLUMNS = ("fetch_engine", "wait_for_selector", "wait_timeout_s")
ALL = (*COLUMNS, *OPTIONAL_COLUMNS)


def preview(client, rows):
    files = {"file": ("engine.csv", _csv(rows, ALL).encode(), "text/csv")}
    return as_user(client, SAMARENDRA).post(f"{BASE}/bulk-import/preview", files=files)


def test_the_new_columns_are_optional_and_listed() -> None:
    assert set(ENGINE_COLUMNS) <= set(OPTIONAL_COLUMNS)
    assert len(COLUMNS) == 16  # the original file layout is unchanged


def test_the_old_sixteen_column_file_imports_with_the_standard_engine(client) -> None:
    old = _csv([GOOD_HTML])
    assert next(csv.reader(io.StringIO(old))) == list(COLUMNS)
    r = _preview(client, [GOOD_HTML])
    assert r.status_code == 200 and r.json()["ok"] == 1
    fields = _by_row(r)[1]["fields"]
    assert fields["fetch_engine"] == "http"
    assert "wait_for_selector" not in fields and "wait_timeout_s" not in fields


def test_a_browser_row_with_wait_settings(client) -> None:
    row = {
        **GOOD_HTML,
        "fetch_engine": "browser",
        "wait_for_selector": "div.loaded",
        "wait_timeout_s": "30",
    }
    r = preview(client, [row])
    assert r.status_code == 200, r.text
    by = _by_row(r)[1]
    assert by["status"] == "OK", by["reasons"]
    assert by["fields"]["fetch_engine"] == "browser" and by["fields"]["type"] == "HTML_LIST"
    assert by["fields"]["wait_for_selector"] == "div.loaded"
    assert by["fields"]["wait_timeout_s"] == 30


def test_an_old_read_mode_browser_is_accepted_as_html_plus_browser_with_a_warning(client) -> None:
    row = {**GOOD_HTML, "read_mode": "browser"}  # the old way of asking for a browser
    by = _by_row(_preview(client, [row]))[1]
    assert by["status"] == "WARNING"
    assert any("old way of asking for a browser" in why for why in by["reasons"])
    assert by["fields"]["type"] == "HTML_LIST" and by["fields"]["fetch_engine"] == "browser"


def test_an_old_browser_row_still_needs_selectors_and_a_date_format(client) -> None:
    row = {
        "authority": "MCA",
        "page_name": "Old browser page",
        "url": "https://www.mca.gov.in/notices",
        "read_mode": "browser",
    }
    by = _by_row(_preview(client, [row]))[1]
    assert by["status"] == "ERROR" and any("row_selector" in why for why in by["reasons"])


def test_old_browser_read_mode_cannot_be_combined_with_the_standard_engine(client) -> None:
    row = {**GOOD_HTML, "read_mode": "browser", "fetch_engine": "http"}
    by = _by_row(preview(client, [row]))[1]
    assert by["status"] == "ERROR" and any("not http" in why for why in by["reasons"])


def test_browser_is_refused_for_rss_html_and_data_file_rows(client) -> None:
    rows = [
        {**GOOD_HTML, "read_mode": "rss_html", "rss_url": "https://x.example/rss.xml",
         "fetch_engine": "browser"},
        {"authority": "UN", "page_name": "Data", "url": "https://un.example/list.xml",
         "read_mode": "data_file", "fetch_engine": "browser"},
    ]  # fmt: skip
    by = _by_row(preview(client, rows))
    for n in (1, 2):
        assert by[n]["status"] == "ERROR"
        assert any("only for read_mode html" in why for why in by[n]["reasons"])


def test_bad_engine_and_wait_values_are_errors(client) -> None:
    rows = [
        {**GOOD_HTML, "fetch_engine": "chrome"},
        {**GOOD_HTML, "page_name": "Zero", "fetch_engine": "browser", "wait_timeout_s": "0"},
        {**GOOD_HTML, "page_name": "Big", "fetch_engine": "browser", "wait_timeout_s": "61"},
        {**GOOD_HTML, "page_name": "Text", "fetch_engine": "browser", "wait_timeout_s": "soon"},
        {**GOOD_HTML, "page_name": "Css", "fetch_engine": "browser", "wait_for_selector": "a[["},
    ]
    by = _by_row(preview(client, rows))
    assert "fetch_engine must be http or browser" in by[1]["reasons"][0]
    for n in (2, 3, 4):
        assert any("wait_timeout_s must be a whole number" in w for w in by[n]["reasons"])
    assert any("wait_for_selector is not a valid CSS selector" in w for w in by[5]["reasons"])
    assert all(by[n]["status"] == "ERROR" for n in range(1, 6))


def test_a_saved_browser_row_becomes_a_pending_version(client) -> None:
    row = {**GOOD_HTML, "page_name": "Saved browser", "fetch_engine": "browser"}
    prev = preview(client, [row]).json()
    saved = as_user(client, SAMARENDRA).post(
        f"{BASE}/bulk-import/save",
        json={"file_name": "engine.csv", "rows": [r["raw"] for r in prev["rows"]]},
    )
    assert saved.status_code == 201, saved.text
    sid = saved.json()["rows"][0]["source_id"]
    version = client.get(f"{BASE}/{sid}/versions").json()[0]
    assert version["status"] == "PENDING" and version["after"]["fetch_engine"] == "browser"
    views = {v["source"]["id"]: v for v in client.get(BASE).json()}
    assert views[sid]["source"]["fetch_engine"] == "browser"
    assert views[sid]["source"]["active"] is False  # nothing is live until it is approved


def test_the_template_row_is_a_valid_html_listing(client) -> None:
    text = client.get(f"{BASE}/bulk-import/template.csv").text
    rows = list(csv.DictReader(io.StringIO(text)))
    assert "browser" not in {r["read_mode"] for r in rows}
    files = {"file": ("template.csv", text.encode(), "text/csv")}
    r = as_user(client, SAMARENDRA).post(f"{BASE}/bulk-import/preview", files=files)
    assert r.status_code == 200 and r.json()["errors"] == 0
