"""ConfigService.test_source: which configuration is tested, and that nothing is saved."""

import pytest

from app.repositories import seed
from app.repositories.memory import MemoryRepository
from app.schemas.api import SourceTestResult
from app.schemas.entities import Source
from app.schemas.enums import CheckStatus
from app.services.config import ConfigService
from app.services.source_test import MockSourceTester, SubprocessSourceTester


class Recorder:
    def __init__(self) -> None:
        self.tested: list[Source] = []

    def run(self, source: Source) -> SourceTestResult:
        self.tested.append(source)
        return SourceTestResult(
            source_id=source.id, status=CheckStatus.OK, duration_seconds=0.1, rows=[], message="ok"
        )


@pytest.fixture
def setup():
    repo = seed.load_seed(MemoryRepository())
    recorder = Recorder()
    admin = next(u for u in repo.list_users() if u.role.value == "admin")
    return repo, recorder, ConfigService(repo, recorder), admin


def test_a_source_with_a_waiting_change_is_tested_with_the_proposed_rules(setup) -> None:
    repo, recorder, service, admin = setup
    pending = next(v for v in repo.list_source_versions("egazette") if v.status.value == "PENDING")
    service.test_source(admin, "egazette")
    (tested,) = recorder.tested
    assert tested.adapter_config["ministries"] == pending.after["adapter_config"]["ministries"]
    assert repo.get_source("egazette").adapter_config != tested.adapter_config  # not applied


def test_a_source_without_a_waiting_change_is_tested_as_it_is(setup) -> None:
    repo, recorder, service, admin = setup
    service.test_source(admin, "trai")
    assert recorder.tested[0] == repo.get_source("trai")


def test_a_test_writes_nothing(setup) -> None:
    repo, _recorder, service, admin = setup
    before = len(repo.list_audit())
    service.test_source(admin, "trai")
    assert len(repo.list_audit()) == before


def test_the_default_tester_is_the_mock_one() -> None:
    repo = seed.load_seed(MemoryRepository())
    admin = next(u for u in repo.list_users() if u.role.value == "admin")
    assert ConfigService(repo).test_source(admin, "trai").rows  # demo rows, no process started
    assert isinstance(ConfigService(repo)._tester, MockSourceTester)


def test_the_live_tester_reports_a_crash_as_a_failed_test_not_an_error(tmp_path) -> None:
    # No dry-run source file can be built from an invalid adapter, so the spider cannot start.
    source = Source(
        id="bad",
        authority="X",
        page_name="Y",
        type="HTML_LIST",
        url="https://x.test/",
        requires_js=False,
    )
    result = SubprocessSourceTester(timeout=60, fixture=str(tmp_path / "missing.html")).run(source)
    assert result.status == CheckStatus.FAILED and result.rows == []
    assert result.message
