"""The politeness rules (CLAUDE.md rule 2) are fixed in settings and must not drift."""

import importlib

import pytest

from regcrawler import settings
from regcrawler.config import load_config


def test_politeness_settings() -> None:
    assert settings.ROBOTSTXT_OBEY is True
    assert settings.AUTOTHROTTLE_ENABLED is True
    assert settings.DOWNLOAD_DELAY >= 2
    assert settings.DOWNLOAD_DELAY_JITTER == 0  # no random shortening of the 2 second delay
    assert settings.CONCURRENT_REQUESTS_PER_DOMAIN == 1
    assert settings.DOWNLOAD_TIMEOUT == 30
    assert settings.RETRY_TIMES == 2
    assert settings.RETRY_HTTP_CODES == [500, 502, 503, 504, 522, 524]


def test_user_agent_identifies_us_with_the_contact_from_config() -> None:
    assert settings.USER_AGENT == f"TVSCredit-RegMonitor/0.1 (+{load_config().contact})"
    assert "@" in settings.USER_AGENT


def test_nothing_that_evades_a_block_is_configured() -> None:
    forbidden = ("ROTATING", "PROXY_LIST", "STEALTH", "FINGERPRINT", "CAPTCHA")
    assert not [n for n in vars(settings) if any(f in n.upper() for f in forbidden)]
    assert not any("proxy" in k.lower() for k in settings.DOWNLOADER_MIDDLEWARES)
    assert settings.COOKIES_ENABLED is False


def test_pipelines_run_snapshot_then_normalise_then_db() -> None:
    order = sorted(settings.ITEM_PIPELINES, key=settings.ITEM_PIPELINES.get)
    assert [p.rsplit(".", 1)[1] for p in order] == [
        "SnapshotPipeline",
        "NormalisePipeline",
        "DbPipeline",
    ]


def test_http_cache_is_dev_profile_only(monkeypatch) -> None:
    assert settings.HTTPCACHE_ENABLED is False
    monkeypatch.setenv("REGCRAWLER_PROFILE", "dev")
    try:
        assert importlib.reload(settings).HTTPCACHE_ENABLED is True
    finally:
        monkeypatch.delenv("REGCRAWLER_PROFILE")
        importlib.reload(settings)


def test_logs_go_under_data_logs() -> None:
    assert settings.LOG_FILE.replace("\\", "/").endswith("data/logs/crawler.log")


def test_a_missing_contact_is_an_error(tmp_path) -> None:
    f = tmp_path / "settings.yaml"
    f.write_text("crawler:\n  contact: ''\n", encoding="utf-8")
    with pytest.raises(ValueError, match="contact"):
        load_config(f)


def test_scrapy_can_load_the_project_settings() -> None:
    from scrapy.settings import Settings

    loaded = Settings()
    loaded.setmodule("regcrawler.settings")
    assert loaded.getbool("ROBOTSTXT_OBEY") and loaded.getfloat("DOWNLOAD_DELAY") == 2
