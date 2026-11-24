"""Bulk add of sources: preview writes nothing, save makes pending versions, maker–checker holds."""

import csv
import io

from app.services.source_bulk import COLUMNS, HostGate
from tests.api.conftest import DIVYA, GEORGE, SAMARENDRA, as_user

BASE = "/api/v1/sources"


def _csv(rows: list[dict[str, str]], columns: tuple[str, ...] = COLUMNS) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(columns), restval="", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


GOOD_HTML = {
    "authority": "SEBI",
    "page_name": "Bulk circulars",
    "url": "https://www.sebi.gov.in/legal/circulars.html",
    "read_mode": "html",
    "row_selector": "table tr",
    "title_selector": "a",
    "link_selector": "a",
    "date_format": "%d.%m.%Y",
    "include_keywords": "NBFC|KYC",
    "priority": "Normal",
}
GOOD_BROWSER = {
    "authority": "MCA",
    "page_name": "Bulk browser page",
    "url": "https://www.mca.gov.in/notices",
    "read_mode": "browser",
}


def _preview(client, rows, user=SAMARENDRA, name="batch.csv"):
    files = {"file": (name, _csv(rows).encode(), "text/csv")}
    return as_user(client, user).post(f"{BASE}/bulk-import/preview", files=files)


def _by_row(resp):
    return {r["row"]: r for r in resp.json()["rows"]}


def test_template_has_every_column(client):
    r = client.get(f"{BASE}/bulk-import/template.csv")
    assert r.status_code == 200
    assert next(csv.reader(io.StringIO(r.text))) == list(COLUMNS)


def test_preview_writes_nothing_and_flags_each_row(client, audit_count):
    before, count = audit_count(), len(client.get(BASE).json())
    rows = [
        GOOD_HTML,
        GOOD_BROWSER,
        {**GOOD_HTML, "page_name": "No selectors", "row_selector": ""},
        {**GOOD_HTML, "page_name": "Bad date", "date_format": "dd/mm"},
        {**GOOD_HTML, "page_name": "Not http", "url": "ftp://x.example/a"},
        {**GOOD_HTML, "page_name": "Missing mode", "read_mode": ""},
        {
            **GOOD_HTML,
            "page_name": "Token",
            "url": "https://www.rbi.org.in/Scripts/(S(abc123))/Page.aspx",
        },
        {**GOOD_HTML, "page_name": "Odd host", "url": "https://random-site.example.com/list"},
        {**GOOD_HTML, "page_name": "Login", "url": "https://www.sebi.gov.in/login/page"},
        GOOD_HTML,  # duplicate inside the file
    ]
    r = _preview(client, rows)
    assert r.status_code == 200, r.text
    by = _by_row(r)
    assert by[1]["status"] == "OK" and by[2]["status"] == "OK"
    assert by[2]["fields"]["requires_js"] is True and by[2]["fields"]["type"] == "BROWSER"
    assert by[1]["fields"]["requires_js"] is False
    assert by[1]["fields"]["include_keywords"] == ["NBFC", "KYC"]
    assert by[3]["status"] == "ERROR" and "row_selector" in by[3]["reasons"][0]
    assert by[4]["status"] == "ERROR" and "strptime" in by[4]["reasons"][0]
    assert by[5]["status"] == "ERROR" and "http" in by[5]["reasons"][0]
    assert by[6]["status"] == "ERROR" and "read_mode is required" in by[6]["reasons"][0]
    assert by[7]["status"] == "WARNING" and "(S(" not in by[7]["fields"]["url"]
    assert by[8]["status"] == "WARNING" and "known regulator" in by[8]["reasons"][0]
    assert by[9]["status"] == "WARNING" and "login or search" in by[9]["reasons"][0]
    assert by[10]["status"] == "ERROR" and "earlier in the file" in by[10]["reasons"][0]
    assert audit_count() == before and len(client.get(BASE).json()) == count  # nothing written


def test_duplicate_of_existing_source_is_an_error(client):
    existing = next(v["source"] for v in client.get(BASE).json() if v["source"]["active"])
    row = {
        **GOOD_BROWSER,
        "authority": existing["authority"],
        "page_name": existing["page_name"],
        "url": existing["url"],
    }
    assert "Duplicate" in _by_row(_preview(client, [row]))[1]["reasons"][0]


def test_unknown_columns_and_limits_are_refused_clearly(client):
    bad = _csv(
        [
            GOOD_BROWSER,
        ],
        columns=(*COLUMNS, "colour"),
    )
    r = as_user(client, SAMARENDRA).post(
        f"{BASE}/bulk-import/preview", files={"file": ("a.csv", bad.encode(), "text/csv")}
    )
    assert r.status_code == 422 and "colour" in r.json()["detail"]
    many = [{**GOOD_BROWSER, "page_name": f"P{n}"} for n in range(51)]
    r = _preview(client, many)
    assert r.status_code == 422 and "50" in r.json()["detail"]
    no_col = "authority,page_name\nA,B\n"
    r = as_user(client, SAMARENDRA).post(
        f"{BASE}/bulk-import/preview", files={"file": ("a.csv", no_col.encode(), "text/csv")}
    )
    assert r.status_code == 422 and "read_mode" in r.json()["detail"]
    r = as_user(client, SAMARENDRA).post(
        f"{BASE}/bulk-import/preview", files={"file": ("a.txt", b"x", "text/plain")}
    )
    assert r.status_code == 422


def test_pasted_rows_and_roles(client):
    text = _csv([GOOD_BROWSER])
    ok = as_user(client, SAMARENDRA).post(f"{BASE}/bulk-import/preview", data={"text": text})
    assert ok.status_code == 200 and ok.json()["ok"] == 1
    assert (
        as_user(client, DIVYA).post(f"{BASE}/bulk-import/preview", data={"text": text}).status_code
        == 403
    )


def test_save_creates_pending_versions_and_audit_and_skips_errors(client, audit_count):
    rows = [GOOD_HTML, GOOD_BROWSER, {**GOOD_HTML, "page_name": "Broken", "url": "nope"}]
    prev = _preview(client, rows).json()
    before = audit_count()
    r = as_user(client, SAMARENDRA).post(
        f"{BASE}/bulk-import/save",
        json={"file_name": "batch.csv", "rows": [x["raw"] for x in prev["rows"]]},
    )
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["saved"] == 2 and out["skipped"] == 1
    # 2 sources x (source + version) events + 1 batch event
    assert audit_count() - before == 5
    events = [e for e in get_audit() if e.action == "source_import.batch"]
    assert events[-1].after["file_name"] == "batch.csv"
    assert events[-1].after["rows_saved"] == 2 and events[-1].after["rows_error"] == 1
    assert events[-1].actor == "Samarendra K."
    views = {v["source"]["id"]: v for v in client.get(BASE).json()}
    for row in out["rows"]:
        if row["source_id"]:
            v = views[row["source_id"]]
            assert v["pending_version"]["status"] == "PENDING" and v["source"]["active"] is False
            assert v["source"]["requires_js"] == (row["fields"]["type"] == "BROWSER")


def _events(action: str):
    return [e for e in get_audit() if e.action == action]


def get_audit():
    from app.api.deps import get_services

    return get_services().repo.list_audit()


def test_errors_csv_has_reason_column(client):
    rows = [GOOD_HTML, {**GOOD_HTML, "page_name": "Broken", "url": "nope"}]
    r = as_user(client, SAMARENDRA).post(f"{BASE}/bulk-import/errors.csv", json={"rows": rows})
    assert r.status_code == 200
    parsed = list(csv.DictReader(io.StringIO(r.text)))
    assert len(parsed) == 1 and parsed[0]["page_name"] == "Broken"
    assert "http" in parsed[0]["reason"]


def _save_two(client):
    out = as_user(client, SAMARENDRA).post(
        f"{BASE}/bulk-import/save",
        json={"file_name": "b.csv", "rows": [GOOD_HTML, GOOD_BROWSER]},
    )
    return [(r["source_id"], 1) for r in out.json()["rows"]]


def test_uploader_cannot_approve_but_another_approver_can(client, audit_count):
    items = [{"source_id": s, "version": v} for s, v in _save_two(client)]
    own = as_user(client, SAMARENDRA).post(f"{BASE}/bulk-decision/approve", json={"items": items})
    assert own.status_code == 403  # an admin is not an approver at all
    uploader = as_user(client, GEORGE)  # an approver who is also the uploader
    out = uploader.post(
        f"{BASE}/bulk-import/save",
        json={"file_name": "g.csv", "rows": [GOOD_BROWSER | {"page_name": "G1"}]},
    ).json()
    mine = [{"source_id": r["source_id"], "version": 1} for r in out["rows"]]
    refused = as_user(client, GEORGE).post(f"{BASE}/bulk-decision/approve", json={"items": mine})
    assert refused.status_code == 200 and refused.json()[0]["ok"] is False
    assert "different" in refused.json()[0]["message"]

    before = len(_events("source_version.approved"))
    done = as_user(client, GEORGE).post(f"{BASE}/bulk-decision/approve", json={"items": items})
    assert [r["ok"] for r in done.json()] == [True, True]
    assert len(_events("source_version.approved")) - before == 2  # one decision event per source
    views = {v["source"]["id"]: v for v in client.get(BASE).json()}
    assert all(views[i["source_id"]]["source"]["active"] for i in items)


def test_reject_selected_needs_reason_and_audits_each(client, audit_count):
    items = [{"source_id": s, "version": v} for s, v in _save_two(client)]
    no_reason = as_user(client, GEORGE).post(f"{BASE}/bulk-decision/reject", json={"items": items})
    assert no_reason.status_code == 422
    before = len(_events("source_version.rejected"))
    r = as_user(client, GEORGE).post(
        f"{BASE}/bulk-decision/reject", json={"items": items, "reason": "wrong pages"}
    )
    assert [x["ok"] for x in r.json()] == [True, True]
    assert len(_events("source_version.rejected")) - before == 2


def test_polite_test_endpoint_runs_and_pending_source_can_be_tested(client):
    items = _save_two(client)
    sid = items[1][0]
    from app.api.deps import get_services

    get_services().config._gate = HostGate(min_gap=0, sleep=lambda _s: None)
    r = as_user(client, SAMARENDRA).post(f"{BASE}/{sid}/test?polite=true")
    assert r.status_code == 200 and r.json()["status"] == "OK"


class _Result:
    def __init__(self, status):
        self.status = status


def test_host_gate_waits_between_requests_and_trips_the_breaker():
    from app.schemas.entities import Source
    from app.schemas.enums import CheckStatus

    now = [0.0]
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    gate = HostGate(min_gap=2.0, trip_after=2, cool_down=100, clock=lambda: now[0], sleep=sleep)
    src = Source(id="s", authority="A", page_name="P", type="BROWSER", url="https://h.example/x")
    calls = []

    def blocked(source):
        calls.append(source.id)
        from app.schemas.api import SourceTestResult

        return SourceTestResult(
            source_id=source.id,
            status=CheckStatus.BLOCKED,
            duration_seconds=0,
            rows=[],
            message="b",
        )

    gate.run(src, blocked)
    gate.run(src, blocked)
    assert slept and slept[0] >= 2.0  # the second request waited
    third = gate.run(src, blocked)
    assert len(calls) == 2 and "Skipped" in third.message  # breaker open: no request made
    now[0] += 101
    gate.run(src, blocked)
    assert len(calls) == 3  # cool-down over


def test_the_old_sixteen_column_file_still_imports_unchanged(client):
    old = _csv([GOOD_HTML, GOOD_BROWSER])  # COLUMNS = the original 16 columns
    assert next(csv.reader(io.StringIO(old))) == list(COLUMNS) and len(COLUMNS) == 16
    prev = as_user(client, SAMARENDRA).post(
        f"{BASE}/bulk-import/preview", files={"file": ("old.csv", old.encode(), "text/csv")}
    )
    assert prev.status_code == 200 and prev.json()["ok"] == 2
    assert "first_run_mode" not in prev.json()["rows"][0]["fields"]  # nothing new is added


def test_optional_first_run_columns_are_accepted_and_checked(client):
    from app.services.source_bulk import OPTIONAL_COLUMNS

    columns = (*COLUMNS, *OPTIONAL_COLUMNS)
    rows = [
        {**GOOD_HTML, "first_run_mode": "last_n_days", "first_run_days": "10",
         "sorted_newest_first": "false"},
        {**GOOD_BROWSER, "first_run_mode": "last_week"},
        {**GOOD_HTML, "page_name": "Bad days", "first_run_days": "abc"},
    ]  # fmt: skip
    r = as_user(client, SAMARENDRA).post(
        f"{BASE}/bulk-import/preview",
        files={"file": ("new.csv", _csv(rows, columns).encode(), "text/csv")},
    )
    assert r.status_code == 200, r.text
    by = _by_row(r)
    assert by[1]["status"] == "OK"
    assert (
        by[1]["fields"]["first_run_mode"] == "last_n_days"
        and by[1]["fields"]["first_run_days"] == 10
    )
    assert by[1]["fields"]["sorted_newest_first"] is False
    assert by[2]["status"] == "ERROR" and "first_run_mode" in by[2]["reasons"][0]
    assert by[3]["status"] == "ERROR" and "first_run_days" in by[3]["reasons"][0]
