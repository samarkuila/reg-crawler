"""Admin pages: Sources, Settings, Audit log. Maker–checker rules and what the pages rely on."""

from tests.api.conftest import AUDIT, DIVYA, GEORGE, SAMARENDRA, as_user

API = "/api/v1"


def audit(client, **params) -> dict:
    return client.get(f"{API}/audit/view", params=params).json()


# ---------- shells ----------


def test_admin_page_shells_render_with_their_scripts(client) -> None:
    expect = {
        "/sources": (
            'id="so-pending"',
            'id="so-rows"',
            'id="so-test"',
            "/static/js/pages/sources.js",
        ),
        "/settings": (
            'id="se-tabs"',
            'id="se-panel"',
            'id="se-pending"',
            "/static/js/pages/settings.js",
        ),
        "/audit": (
            'id="au-form"',
            'id="au-rows"',
            'id="a-actor"',
            'id="a-action"',
            'id="au-export"',
            "/static/js/pages/audit.js",
        ),
    }
    for path, markers in expect.items():
        html = client.get(path).text
        assert "not built yet" not in html, path
        for marker in markers:
            assert marker in html, (path, marker)
        script = markers[-1]
        assert client.get(script).status_code == 200


# ---------- sources ----------


def test_samarendras_edit_stays_pending_until_george_approves(client) -> None:
    body = {"include_keywords": ["NBFC", "gold loan"], "priority": "URGENT"}
    r = as_user(client, SAMARENDRA).post(f"{API}/sources/trai/versions", json=body)
    assert r.status_code == 201 and r.json()["status"] == "PENDING" and r.json()["version"] == 2
    live = next(v for v in client.get(f"{API}/sources").json() if v["source"]["id"] == "trai")
    assert live["source"]["include_keywords"] == [] and live["source"]["current_version"] == 1
    assert live["pending_version"]["requested_by"] == "Samarendra K."
    labels = {c["label"] for c in live["pending_changes"]}
    assert {"Include keywords", "Priority"} <= labels
    # Samarendra, Divya and a viewer cannot approve it
    for user, code in ((SAMARENDRA, 403), (DIVYA, 403), (AUDIT, 403)):
        assert (
            as_user(client, user).post(f"{API}/sources/trai/versions/2/approve").status_code == code
        )
    done = as_user(client, GEORGE).post(f"{API}/sources/trai/versions/2/approve")
    assert done.status_code == 200 and done.json()["approved_by"] == "George K."
    after = next(v for v in client.get(f"{API}/sources").json() if v["source"]["id"] == "trai")
    assert after["source"]["include_keywords"] == ["NBFC", "gold loan"]
    assert after["source"]["current_version"] == 2 and after["pending_version"] is None


def test_george_cannot_approve_his_own_source_edit(client) -> None:
    as_user(client, GEORGE).post(f"{API}/sources/trai/versions", json={"priority": "URGENT"})
    r = client.post(f"{API}/sources/trai/versions/2/approve")
    assert r.status_code == 403 and "different person" in r.json()["detail"]
    assert (
        client.post(f"{API}/sources/trai/versions/2/reject", json={"reason": "x"}).status_code
        == 403
    )
    view = next(v for v in client.get(f"{API}/sources").json() if v["source"]["id"] == "trai")
    assert view["can_decide"] is False and "different approver" in view["decide_note"]
    assert view["source"]["priority"] == "NORMAL"


def test_pending_card_data_tells_each_user_what_they_may_do(client) -> None:
    def egazette(user):
        rows = as_user(client, user).get(f"{API}/sources").json()
        return next(v for v in rows if v["source"]["id"] == "egazette")

    george = egazette(GEORGE)
    assert george["can_decide"] is True and george["can_edit"] is True
    change = george["pending_changes"][0]
    assert change["label"] == "Ministry filter" and "Labour and Employment" in change["after"]
    assert "Labour" not in change["before"]
    sam = egazette(SAMARENDRA)
    assert sam["can_decide"] is False and "approver" in sam["decide_note"]
    viewer = egazette(AUDIT)
    assert viewer["can_edit"] is False and viewer["can_decide"] is False


def test_rejecting_needs_a_reason_and_leaves_the_live_version(client) -> None:
    as_user(client, GEORGE)
    assert (
        client.post(f"{API}/sources/egazette/versions/3/reject", json={"reason": " "}).status_code
        == 422
    )
    r = client.post(f"{API}/sources/egazette/versions/3/reject", json={"reason": "Too broad"})
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    view = next(v for v in client.get(f"{API}/sources").json() if v["source"]["id"] == "egazette")
    assert view["source"]["current_version"] == 2 and view["pending_version"] is None


def test_only_one_change_per_source_can_wait_for_approval(client) -> None:
    r = as_user(client, SAMARENDRA).post(
        f"{API}/sources/egazette/versions", json={"priority": "URGENT"}
    )
    assert r.status_code == 409 and "already waiting" in r.json()["detail"]


def test_new_source_gets_an_id_and_waits_for_approval(client) -> None:
    body = {
        "authority": "SEBI",
        "page_name": "Circulars",
        "type": "HTML_LIST",
        "url": "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes",
        "adapter_config": {
            "selectors": {
                "row": "table tr",
                "title": "a",
                "link": "a",
                "date": "td.date",
                "ref_no_regex": r"SEBI/\S+",
            },
            "date_format": "%b %d, %Y",
        },
        "include_keywords": ["NBFC"],
    }
    r = as_user(client, SAMARENDRA).post(f"{API}/sources", json=body)
    assert r.status_code == 201
    view = r.json()
    assert (
        view["source"]["id"] == "sebi-circulars" and view["status_label"] == "Waiting for approval"
    )
    assert view["source"]["active"] is False and view["pending_version"]["version"] == 1
    assert as_user(client, SAMARENDRA).post(f"{API}/sources", json=body).status_code == 409
    assert (
        as_user(client, GEORGE).post(f"{API}/sources/sebi-circulars/versions/1/approve").status_code
        == 200
    )
    done = next(
        v for v in client.get(f"{API}/sources").json() if v["source"]["id"] == "sebi-circulars"
    )
    assert done["source"]["active"] is True
    assert done["source"]["adapter_config"]["selectors"]["row"] == "table tr"


def test_source_form_values_are_checked(client) -> None:
    c = as_user(client, SAMARENDRA)
    url = f"{API}/sources/trai/versions"
    assert c.post(url, json={"url": "ftp://x"}).status_code == 422
    assert (
        c.post(url, json={"adapter_config": {"selectors": {"ref_no_regex": "("}}}).status_code
        == 422
    )
    assert c.post(url, json={"adapter_config": {"date_format": "dd/mm"}}).status_code == 422
    assert c.post(url, json={"authority": " "}).status_code == 422
    assert c.post(url, json={}).status_code == 422  # nothing would change
    assert as_user(client, DIVYA).post(url, json={"priority": "URGENT"}).status_code == 403


def test_test_this_source_returns_a_dry_run_card(client) -> None:
    ok = as_user(client, SAMARENDRA).post(f"{API}/sources/trai/test").json()
    assert ok["status"] == "OK" and len(ok["rows"]) == 3 and "Mock" in ok["message"]
    blocked = client.post(f"{API}/sources/egazette/test").json()
    assert blocked["status"] == "BLOCKED" and blocked["rows"] == []


# ---------- settings ----------


def get_setting(client, key) -> dict:
    return client.get(f"{API}/settings/{key}").json()


def test_settings_carry_sme_recipients_and_email_wording(client) -> None:
    keys = {s["setting"]["key"] for s in client.get(f"{API}/settings").json()}
    assert {"sme-routing", "recipients", "email-template"} <= keys
    assert get_setting(client, "email-template")["title"] == "Email template"


def test_setting_view_shows_who_can_edit_and_decide(client) -> None:
    as_user(client, SAMARENDRA).put(f"{API}/settings/lookback-days", json={"value": 3})
    sam = get_setting(client, "lookback-days")
    assert (
        sam["can_edit"] is True and sam["can_decide"] is False and sam["changes"][0]["after"] == "3"
    )
    george = as_user(client, GEORGE).get(f"{API}/settings/lookback-days").json()
    assert george["can_decide"] is True
    assert as_user(client, DIVYA).get(f"{API}/settings/lookback-days").json()["can_edit"] is False


def test_second_change_to_the_same_setting_waits_its_turn(client) -> None:
    c = as_user(client, SAMARENDRA)
    assert c.put(f"{API}/settings/lookback-days", json={"value": 3}).status_code == 200
    r = c.put(f"{API}/settings/lookback-days", json={"value": 4})
    assert r.status_code == 409 and "already waiting" in r.json()["detail"]
    assert c.put(f"{API}/settings/holidays", json={"value": []}).status_code == 422  # no change


def test_sme_routing_change_is_pending_then_drives_the_review_form_and_cc(client) -> None:
    current = get_setting(client, "sme-routing")["setting"]["value"]
    new = [
        *current,
        {"name": "Legal", "head_name": "Legal head", "head_email": "legal@example.invalid"},
    ]
    r = as_user(client, SAMARENDRA).put(f"{API}/settings/sme-routing", json={"value": new})
    assert r.status_code == 200 and r.json()["changes"][0]["label"] == "Added: Legal"
    assert "Legal" not in {
        f["name"] for f in client.get(f"{API}/sme-functions").json()
    }  # not live yet
    assert as_user(client, GEORGE).post(f"{API}/settings/sme-routing/approve").status_code == 200
    functions = {f["id"]: f["name"] for f in client.get(f"{API}/sme-functions").json()}
    assert functions["legal"] == "Legal"


def test_sme_function_cannot_be_deleted_only_deactivated(client) -> None:
    current = get_setting(client, "sme-routing")["setting"]["value"]
    r = as_user(client, SAMARENDRA).put(f"{API}/settings/sme-routing", json={"value": current[1:]})
    assert r.status_code == 422 and "inactive" in r.json()["detail"]
    off = [{**current[0], "active": False}, *current[1:]]
    assert client.put(f"{API}/settings/sme-routing", json={"value": off}).status_code == 200


def test_recipients_need_valid_emails_and_one_active_to(client) -> None:
    current = get_setting(client, "recipients")["setting"]["value"]
    c = as_user(client, SAMARENDRA)
    bad = [{"name": "X", "email": "not-an-email", "kind": "CC"}]
    assert c.put(f"{API}/settings/recipients", json={"value": bad}).status_code == 422
    only_cc = [{**r, "kind": "CC"} for r in current]
    assert c.put(f"{API}/settings/recipients", json={"value": only_cc}).status_code == 422
    ok = [*current, {"name": "Risk", "email": "risk@example.invalid", "kind": "CC"}]
    assert c.put(f"{API}/settings/recipients", json={"value": ok}).status_code == 200


def test_email_wording_change_reaches_the_email_only_after_approval(client) -> None:
    value = dict(get_setting(client, "email-template")["setting"]["value"])
    value["subject_pattern"] = "Regulatory digest {date} ({cycle})"
    r = as_user(client, SAMARENDRA).put(f"{API}/settings/email-template", json={"value": value})
    assert r.status_code == 200 and r.json()["changes"][0]["label"] == "Subject pattern"

    def preview():
        return client.get(f"{API}/daily-update/preview", params={"cycle": "2026-11-20-EOD"}).json()

    assert preview()["subject"].startswith("Daily Regulatory Update")
    as_user(client, GEORGE).post(f"{API}/settings/email-template/approve")
    assert preview()["subject"] == "Regulatory digest 20.11.2026 (EOD)"


def test_email_template_rejects_unknown_placeholders_and_blanks(client) -> None:
    value = dict(get_setting(client, "email-template")["setting"]["value"])
    c = as_user(client, SAMARENDRA)
    assert (
        c.put(
            f"{API}/settings/email-template",
            json={"value": {**value, "subject_pattern": "Update {when}"}},
        ).status_code
        == 422
    )
    assert (
        c.put(
            f"{API}/settings/email-template", json={"value": {**value, "greeting": " "}}
        ).status_code
        == 422
    )


def test_schedule_values_are_checked(client) -> None:
    c = as_user(client, SAMARENDRA)
    times = dict(get_setting(client, "run-times")["setting"]["value"])
    assert (
        c.put(f"{API}/settings/run-times", json={"value": {**times, "eod": "25:00"}}).status_code
        == 422
    )
    assert (
        c.put(
            f"{API}/settings/run-times", json={"value": {**times, "urgent_hourly_from": "21:00"}}
        ).status_code
        == 422
    )
    assert c.put(f"{API}/settings/working-days", json={"value": []}).status_code == 422
    assert (
        c.put(f"{API}/settings/working-days", json={"value": ["MON", "FUNDAY"]}).status_code == 422
    )
    assert c.put(f"{API}/settings/holidays", json={"value": ["31.02.2026"]}).status_code == 422
    ok = c.put(f"{API}/settings/holidays", json={"value": ["26.01.2027", "26.01.2027"]})
    assert ok.status_code == 200 and ok.json()["setting"]["pending"]["value"] == ["26.01.2027"]
    assert c.put(f"{API}/settings/dismiss-reasons", json={"value": []}).status_code == 422


# ---------- audit log ----------


def test_every_admin_change_is_visible_in_the_audit_log(client) -> None:
    as_user(client, SAMARENDRA).post(f"{API}/sources/trai/versions", json={"priority": "URGENT"})
    client.put(f"{API}/settings/lookback-days", json={"value": 3})
    as_user(client, GEORGE).post(f"{API}/sources/trai/versions/2/approve")
    client.post(f"{API}/settings/lookback-days/approve")
    actions = {(r["actor"], r["action"]) for r in audit(client)["rows"]}
    assert {
        ("Samarendra K.", "source_version.requested"),
        ("Samarendra K.", "setting.change_requested"),
        ("George K.", "source_version.approved"),
        ("George K.", "source.updated"),
        ("George K.", "setting.change_approved"),
    } <= actions


def test_audit_rows_show_before_and_after_as_readable_key_values(client) -> None:
    as_user(client, GEORGE).post(f"{API}/sources/egazette/versions/3/approve")
    row = next(
        r for r in audit(client, action="source.updated")["rows"] if r["entity_id"] == "egazette"
    )
    assert row["action_label"] == "Source updated"
    before = {kv["key"]: kv["value"] for kv in row["before"]}
    after = {kv["key"]: kv["value"] for kv in row["after"]}
    assert before["Ministry filter"] != after["Ministry filter"]
    assert "Labour and Employment" in after["Ministry filter"] and after["Authority"] == "eGazette"
    assert "{" not in after["Ministry filter"]  # not raw JSON


def test_audit_filters_by_actor_entity_action_and_dates(client) -> None:
    as_user(client, SAMARENDRA).post(f"{API}/sources/trai/versions", json={"priority": "URGENT"})
    by_actor = audit(client, actor="Samarendra K.")
    assert by_actor["total"] >= 1 and {r["actor"] for r in by_actor["rows"]} == {"Samarendra K."}
    by_entity = audit(client, entity="source_version")
    assert by_entity["total"] >= 1 and {r["entity"] for r in by_entity["rows"]} == {
        "source_version"
    }
    by_action = audit(client, action="source_version.requested")
    assert {r["action"] for r in by_action["rows"]} == {"source_version.requested"}
    today = audit(client, **{"from": "2026-11-20", "to": "2026-11-20"})
    assert today["total"] >= 1 and all(r["ts"].startswith("2026-11-20") for r in today["rows"])
    assert audit(client, **{"from": "2020-01-01", "to": "2020-01-02"})["total"] == 0
    assert (
        client.get(
            f"{API}/audit/view", params={"from": "2026-12-01", "to": "2026-11-01"}
        ).status_code
        == 422
    )
    options = audit(client)["options"]
    assert "Samarendra K." in options["actors"] and "source_version.requested" in options["actions"]


def test_audit_is_read_only_and_exports_with_the_same_filters(client) -> None:
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)(f"{API}/audit").status_code == 405
    as_user(client, SAMARENDRA).post(f"{API}/sources/trai/versions", json={"priority": "URGENT"})
    r = client.get(f"{API}/audit/export.csv", params={"action": "source_version.requested"})
    lines = r.text.strip().splitlines()
    assert r.status_code == 200 and lines[0].startswith("id,ts,actor,action")
    assert len(lines) >= 2 and all("source_version.requested" in line for line in lines[1:])


def test_viewer_can_read_the_audit_log(client) -> None:
    assert as_user(client, AUDIT).get(f"{API}/audit/view").status_code == 200
