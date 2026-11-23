"""Source imports (sources.yaml and the monitoring Excel) and the command line tools.

Imports only propose: new sources and changes are PENDING until a different person approves.
Runs on the SQL repository (the one the CLI uses) with a temporary database; no network.
"""

from pathlib import Path

import pytest
import yaml
from openpyxl import Workbook

from app import cli
from app.schemas.enums import VersionStatus
from app.services.config import ConfigService
from app.services.source_import import ImportError_, SourceImportService
from tests.conftest import ROOT, make_backend

SOURCES_YAML = ROOT / "config" / "sources.yaml"
SAM, GEORGE = "Samarendra K.", "George K."


@pytest.fixture(scope="module")
def bare_template(tmp_path_factory) -> Path:
    """A migrated database with the users and default settings but NO sources (a fresh install)."""
    from alembic import command

    from app.db.session import make_engine
    from app.repositories import seed
    from app.repositories.sql import SqlRepository
    from tests.conftest import _alembic

    folder = tmp_path_factory.mktemp("bare")
    url = f"sqlite:///{(folder / 'reg_monitor.db').as_posix()}"
    command.upgrade(_alembic(url), "head")
    engine = make_engine(url)
    SqlRepository(engine, files_root=folder / "files").load([*seed.USERS, *seed.settings()])
    engine.dispose()
    return folder


@pytest.fixture
def repo(tmp_path, bare_template):
    """A fresh install: users and settings, no sources yet."""
    repository, *_ = make_backend("sql", tmp_path, bare_template)
    return repository


@pytest.fixture
def seeded(tmp_path, sql_template):
    """The demo database: 13 sources, some with the same ids as the register file."""
    repository, *_ = make_backend("sql", tmp_path / "demo", sql_template)
    return repository


@pytest.fixture
def service(repo) -> SourceImportService:
    return SourceImportService(repo)


def pending(repo, source_id):
    return [v for v in repo.list_source_versions(source_id) if v.status == VersionStatus.PENDING]


# ---------- config/sources.yaml ----------


def specs(service):
    return {s.id: s for s in service.parse_yaml(SOURCES_YAML.read_text(encoding="utf-8"))}


def test_the_register_file_has_the_poc_and_planned_sources(service) -> None:
    all_specs = specs(service)
    active = [s for s in all_specs.values() if s.active]
    planned = [s for s in all_specs.values() if s.planned]
    assert len(active) == 11 and len(planned) == 10
    rbi = [s for s in all_specs.values() if s.authority == "RBI"]
    assert {s.page_name for s in rbi} == {
        "Notifications",
        "Press Releases",
        "Circulars",
        "Master Directions",
        "Master Circulars",
        "FAQs",
        "Home / What's New",
    }
    assert all(s.type.value == "HTML_LIST" for s in rbi)
    assert all_specs["rbi-notifications"].rss_url == "https://rbi.org.in/notifications_rss.xml"
    assert all_specs["rbi-press-releases"].rss_url == "https://rbi.org.in/pressreleases_rss.xml"
    assert "Non-Banking" in all_specs["rbi-faqs"].adapter_config["sections"]  # the IoRS FAQ


def test_register_details_from_the_brief(service) -> None:
    s = specs(service)
    unsc = s["unsc-consolidated"]
    assert unsc.type.value == "STRUCTURED_DATA" and unsc.priority.value == "URGENT"
    assert unsc.url == "https://scsanctions.un.org/resources/xml/en/consolidated.xml"
    trai = s["trai-press-releases"]
    assert trai.url == "https://trai.gov.in/notifications/press-release"
    assert trai.include_keywords == [
        "regulation",
        "direction",
        "recommendation",
        "consultation paper",
    ]
    assert "policyholder.gov.in" in s["irdai-circulars"].url
    eg = s["egazette-extraordinary"]
    assert eg.url == "https://egazette.gov.in" and eg.adapter_config["use_search_form"] is False
    assert (
        eg.adapter_config["ministries"]
        and "multiple ministries" in eg.adapter_config["always_keep"]
    )
    glob = s["rbi-circulars"]
    assert glob.include_keywords == [
        "NBFC",
        "non-banking",
        "regulated entities",
        "all lending institutions",
        "KYC",
        "AML",
        "sanctions",
        "FEMA",
        "DPDP",
    ]
    assert glob.exclude_keywords[-1] == "gross direct premium" and "bid" in glob.exclude_keywords


def test_planned_sources_are_inactive_with_no_url(service) -> None:
    planned = {s.id: s for s in specs(service).values() if s.planned}
    assert set(planned) == {
        "uidai",
        "cersai",
        "ckyc",
        "npci",
        "meity",
        "pib",
        "fiu-ind",
        "nfra",
        "mha",
        "slbc",
    }
    assert all(s.url == "TBD" and not s.active for s in planned.values())


def test_selectors_are_marked_as_placeholders(service) -> None:
    for s in specs(service).values():
        if s.active:
            assert s.adapter_config["selectors_confirmed"] is False


@pytest.mark.parametrize(
    "entry, message",
    [
        (
            {"id": "x1", "authority": "A", "page_name": "P", "type": "HTML_LIST", "url": "ftp://x"},
            "http",
        ),
        (
            {"id": "x1", "authority": "A", "page_name": "P", "type": "HTML_LIST", "url": "TBD"},
            "TBD",
        ),
        (
            {
                "id": "x1",
                "authority": "A",
                "page_name": "P",
                "type": "HTML_LIST",
                "url": "https://x",
                "planned": True,
            },
            "planned",
        ),
        (
            {
                "id": "Bad Id",
                "authority": "A",
                "page_name": "P",
                "type": "HTML_LIST",
                "url": "https://x",
            },
            "id",
        ),
        (
            {"id": "x1", "authority": "A", "page_name": "P", "type": "PDF", "url": "https://x"},
            "HTML_LIST",
        ),
    ],
)
def test_bad_entries_are_refused_with_the_reason(service, entry, message) -> None:
    with pytest.raises(ImportError_) as err:
        service.parse_yaml(yaml.safe_dump([entry]))
    assert message in str(err.value)


def test_duplicate_ids_and_non_lists_are_refused(service) -> None:
    one = {"id": "x1", "authority": "A", "page_name": "P", "type": "HTML_LIST", "url": "https://x"}
    with pytest.raises(ImportError_, match="twice"):
        service.parse_yaml(yaml.safe_dump([one, one]))
    with pytest.raises(ImportError_, match="list"):
        service.parse_yaml("a: 1")


def test_import_creates_pending_versions_and_nothing_goes_live(service, repo) -> None:
    report = service.import_yaml(SOURCES_YAML, SAM)
    assert len(report.proposed_new) == 21 and not report.skipped and not report.unmapped
    for spec in specs(service).values():
        source = repo.get_source(spec.id)
        assert source.active is False and source.current_version == 0  # not live
        (version,) = pending(repo, spec.id)
        assert version.requested_by == SAM and version.version == 1
        assert version.after["active"] is spec.active
    assert repo.get_source("rbi-faqs").planned is False and repo.get_source("slbc").planned is True
    actions = {(e.actor, e.action) for e in repo.list_audit() if e.entity_id.startswith("rbi-faqs")}
    assert (SAM, "source.created") in actions


def test_imported_sources_show_as_pending_approval_and_need_a_different_approver(
    service, repo
) -> None:
    service.import_yaml(SOURCES_YAML, SAM)
    views = {v.source.id: v for v in ConfigService(repo).list_sources(repo.get_user("u-george"))}
    assert views["rbi-notifications"].pending_version is not None
    assert views["rbi-notifications"].status_label == "Waiting for approval"
    assert views["rbi-notifications"].can_decide is True  # George did not import them
    sam_view = ConfigService(repo).list_sources(repo.get_user("u-samarendra"))
    assert next(v for v in sam_view if v.source.id == "rbi-notifications").can_decide is False
    config = ConfigService(repo)
    config.approve_version(repo.get_user("u-george"), "rbi-notifications", 1)
    live = repo.get_source("rbi-notifications")
    assert live.active is True and live.current_version == 1 and live.rss_url.endswith("_rss.xml")


def test_importing_twice_proposes_nothing_new(service, repo) -> None:
    service.import_yaml(SOURCES_YAML, SAM)
    events = len(repo.list_audit())
    again = service.import_yaml(SOURCES_YAML, SAM)
    assert again.proposed_new == [] and again.proposed_changes == [] and len(again.unchanged) == 21
    assert len(repo.list_audit()) == events


def test_a_changed_file_proposes_only_the_difference(service, repo, tmp_path) -> None:
    service.import_yaml(SOURCES_YAML, SAM)
    config = ConfigService(repo)
    config.approve_version(repo.get_user("u-george"), "trai-press-releases", 1)
    data = yaml.safe_load(SOURCES_YAML.read_text(encoding="utf-8"))
    for entry in data:
        if entry["id"] == "trai-press-releases":
            entry["include_keywords"] = [*entry["include_keywords"], "tariff"]
    changed = tmp_path / "sources.yaml"
    changed.write_text(yaml.safe_dump(data), encoding="utf-8")
    report = service.import_yaml(changed, SAM)
    assert [c.split(": ")[1] for c in report.proposed_changes] == ["include_keywords"]
    (version,) = pending(repo, "trai-press-releases")
    assert version.version == 2 and version.after == {
        "include_keywords": [
            "regulation",
            "direction",
            "recommendation",
            "consultation paper",
            "tariff",
        ]
    }
    assert version.before["include_keywords"][0] == "regulation"
    # a second run leaves it alone: the change is already waiting for approval
    again = service.import_yaml(changed, SAM)
    assert again.proposed_changes == [] and "trai-press-releases" in again.unchanged[0:21].__str__()


def test_existing_source_with_a_waiting_change_is_left_alone_and_reported(
    service, repo, tmp_path
) -> None:
    service.import_yaml(SOURCES_YAML, SAM)
    config = ConfigService(repo)
    config.approve_version(repo.get_user("u-george"), "unsc-consolidated", 1)
    repo.add_source_version("unsc-consolidated", {"requires_js": True}, SAM)
    report = service.import_yaml(SOURCES_YAML, SAM)
    assert any("unsc-consolidated" in line and "waiting" in line for line in report.skipped)


def test_dry_run_writes_nothing(service, repo) -> None:
    events = len(repo.list_audit())
    report = service.import_yaml(SOURCES_YAML, SAM, dry_run=True)
    assert len(report.proposed_new) == 21 and "Would propose" in report.lines(True)[0]
    assert repo.get_source("rbi-faqs") is None and len(repo.list_audit()) == events


def test_actor_must_be_a_known_user(service) -> None:
    with pytest.raises(ImportError_, match="Unknown actor"):
        service.import_yaml(SOURCES_YAML, "Nobody")
    assert service.check_actor("samarendra k.") == SAM  # case does not matter


def test_missing_file(service) -> None:
    with pytest.raises(ImportError_, match="not found"):
        service.import_yaml("nope.yaml", SAM)


# ---------- the monitoring Excel ----------


def workbook(path: Path, rows: list[list], header=None) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.append(header or ["Site", "Link", "Tabs / Keywords", "Keywords", "Documents to Fetch"])
    for row in rows:
        ws.append(row)
    wb.save(path)
    return path


EXCEL_ROWS = [
    [
        "RBI",
        "https://www.rbi.org.in/",
        "Press releases",
        "gold loan; digital lending",
        "Circular PDF, annexures",
    ],
    ["RBI", "https://www.rbi.org.in/", "Notifications", "co-lending", None],
    ["SEBI", "sebi.gov.in/legal/circulars", "Circulars", "NBFC", "Circular PDF"],
    ["TRAI", None, "Press releases", None, None],  # no link
    [None, "https://x.gov.in", None, None, None],  # no site
    ["ABC", "not a web address", None, None, None],
    ["All", None, None, "co-lending; fair practices", None],
    [None, None, None, None, None],  # blank row
]


def test_excel_rows_become_pending_versions_and_settings(seeded, tmp_path) -> None:
    repo = seeded
    service = SourceImportService(repo)
    report = service.import_excel(workbook(tmp_path / "m.xlsx", EXCEL_ROWS), SAM)
    # RBI (two rows) -> ONE pending change on the existing RBI source
    (version,) = pending(repo, "rbi")
    assert version.requested_by == SAM and version.version == 3
    after = version.after
    assert after["include_keywords"][-3:] == ["gold loan", "digital lending", "co-lending"]
    assert after["adapter_config"]["documents_to_fetch"] == ["Circular PDF", "annexures"]
    tabs = after["adapter_config"]["tabs"]
    assert {"Press releases", "Notifications"} <= set(tabs) and len(tabs) == len(set(tabs))
    assert repo.get_source("rbi").current_version == 2  # nothing live yet
    # an unknown site becomes a NEW source, pending, inactive
    (new,) = [s for s in repo.list_sources() if s.authority == "SEBI"]
    assert new.active is False and new.url == "https://sebi.gov.in/legal/circulars"
    assert pending(repo, new.id)[0].after["adapter_config"]["documents_to_fetch"] == [
        "Circular PDF"
    ]
    # global keywords become a pending setting
    setting = repo.get_setting("relevance-keywords")
    assert setting.pending.value[-2:] == ["co-lending", "fair practices"]
    assert setting.pending.requested_by == SAM and "co-lending" not in setting.value
    assert report.proposed_settings and len(report.proposed_new) == 1


def test_rows_that_cannot_be_mapped_are_reported_with_the_reason(seeded, tmp_path) -> None:
    service = SourceImportService(seeded)
    report = service.import_excel(workbook(tmp_path / "m.xlsx", EXCEL_ROWS), SAM, dry_run=True)
    assert len(report.unmapped) == 3
    assert any("(TRAI)" in u and "no usable web address" in u for u in report.unmapped)
    assert any("(ABC)" in u and "no usable web address" in u for u in report.unmapped)
    assert any("no site name" in u for u in report.unmapped)
    assert all(u.startswith("Sheet row ") for u in report.unmapped)  # names the sheet and row


def test_excel_import_twice_proposes_nothing_new(seeded, tmp_path) -> None:
    repo = seeded
    service = SourceImportService(repo)
    file = workbook(tmp_path / "m.xlsx", EXCEL_ROWS)
    service.import_excel(file, SAM)
    events = len(repo.list_audit())
    again = service.import_excel(file, SAM)
    assert not again.proposed_new and not again.proposed_changes and not again.proposed_settings
    assert len(repo.list_audit()) == events


def test_excel_headers_may_be_named_differently(seeded, tmp_path) -> None:
    repo = seeded
    service = SourceImportService(repo)
    file = workbook(
        tmp_path / "m.xlsx",
        [["TRAI", "https://trai.gov.in/", "consultation papers"]],
        header=["Website Name", "Website Link", "Keywords"],
    )
    report = service.import_excel(file, SAM)
    assert any("trai" in line.lower() for line in report.proposed_changes)
    assert pending(repo, "trai")[0].after["include_keywords"] == ["consultation papers"]


def test_excel_without_a_usable_header_is_refused(service, tmp_path) -> None:
    wb = Workbook()
    wb.active.append(["Name", "Colour"])
    wb.active.append(["a", "b"])
    wb.save(tmp_path / "bad.xlsx")
    with pytest.raises(ImportError_, match="header row"):
        service.import_excel(tmp_path / "bad.xlsx", SAM)
    (tmp_path / "text.xlsx").write_text("not excel")
    with pytest.raises(ImportError_, match="Could not open"):
        service.import_excel(tmp_path / "text.xlsx", SAM)
    with pytest.raises(ImportError_, match="not found"):
        service.import_excel(tmp_path / "missing.xlsx", SAM)


# ---------- command line ----------


@pytest.fixture
def cli_env(tmp_path, bare_template, monkeypatch):
    _repo, url, files = make_backend("sql", tmp_path, bare_template)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("FILES_ROOT", str(files))
    return url


@pytest.fixture
def cli_env_demo(tmp_path, sql_template, monkeypatch):
    _repo, url, files = make_backend("sql", tmp_path, sql_template)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("FILES_ROOT", str(files))
    return url


def test_cli_import_sources(cli_env, capsys) -> None:
    code = cli.main(["import-sources", str(SOURCES_YAML), "--actor", SAM])
    out = capsys.readouterr().out
    assert code == 0 and "Proposed: 21 new source(s)" in out and "pending approval" in out
    assert cli.main(["import-sources", str(SOURCES_YAML), "--actor", SAM]) == 0
    assert "Proposed: 0 new source(s)" in capsys.readouterr().out


def test_cli_import_excel_and_errors(cli_env_demo, tmp_path, capsys) -> None:
    file = workbook(tmp_path / "m.xlsx", EXCEL_ROWS)
    assert cli.main(["import-monitoring-excel", str(file), "--actor", SAM, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Would propose" in out and "Rows not mapped: 3" in out
    assert cli.main(["import-monitoring-excel", str(file), "--actor", "Nobody"]) == 2
    assert "Unknown actor" in capsys.readouterr().err
    assert cli.main(["import-sources", "missing.yaml", "--actor", SAM]) == 2


def test_cli_seed_demo_builds_a_database_from_nothing(tmp_path, monkeypatch, capsys) -> None:
    url = f"sqlite:///{(tmp_path / 'fresh' / 'reg.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("FILES_ROOT", str(tmp_path / "files"))
    assert cli.main(["seed-demo"]) == 0 and "Demo data loaded" in capsys.readouterr().out
    from app.repositories.factory import make_repository

    repo = make_repository("sql")
    assert len(repo.list_sources()) == 13 and len(repo.list_audit()) >= 5
    assert repo.read_blob(repo.list_documents("item-iors-faqs")[0].sha256) is not None
    repo._engine.dispose()  # Windows will not delete a database file that is still open
    assert cli.main(["seed-demo"]) == 2  # never into a database that already has data
    assert "already has data" in capsys.readouterr().err
    assert cli.main(["seed-demo", "--reset"]) == 0
    assert len(make_repository("sql").list_sources()) == 13


def test_importing_onto_the_demo_data_proposes_changes_to_matching_ids(seeded) -> None:
    service = SourceImportService(seeded)
    report = service.import_yaml(SOURCES_YAML, SAM)
    # the demo already has 8 planned sources with the same ids as the register file
    assert len(report.proposed_new) == 13 and len(report.proposed_changes) == 8
    assert all("planned" not in c or True for c in report.proposed_changes)
    assert seeded.get_source("npci").current_version == 0  # still not live
