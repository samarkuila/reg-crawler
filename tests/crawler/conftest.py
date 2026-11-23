"""Shared pieces for crawler tests: no network, a seeded repository (memory and SQL), fixtures."""

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from scrapy.http import HtmlResponse, Request
from scrapy.settings import Settings

from app.schemas.enums import CheckChannel, Cycle
from regcrawler.config import CrawlerConfig, load_config
from regcrawler.spiders.base import BaseRegSpider
from tests.conftest import BACKENDS, make_backend

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SOURCE_ID = "rbi"  # an active source in the demo seed


class DemoSpider(BaseRegSpider):
    name = "demo"
    channels = (CheckChannel.HTML,)


@pytest.fixture(params=BACKENDS)
def repo(request, tmp_path, sql_template):
    repository, _url, _files = make_backend(request.param, tmp_path, sql_template)
    return repository


@pytest.fixture
def spider(repo) -> DemoSpider:
    run = repo.create_run(Cycle.ADHOC, date(2026, 11, 23), "tester", "tester")
    return DemoSpider(source=SOURCE_ID, run_id=run.id, repository=repo)


@pytest.fixture
def crawler(spider, repo) -> SimpleNamespace:
    return SimpleNamespace(spider=spider, settings=Settings({"REGCRAWLER_REPOSITORY": repo}))


@pytest.fixture
def config() -> CrawlerConfig:
    return load_config()


def fixture_bytes(*parts: str) -> bytes:
    return FIXTURES.joinpath(*parts).read_bytes()


def html_response(
    body: bytes,
    url: str = "https://rbi.org.in/notifications",
    status: int = 200,
    request: Request | None = None,
    content_type: str = "text/html; charset=utf-8",
) -> HtmlResponse:
    request = request or Request(url, meta={"channel": "HTML"})
    return HtmlResponse(
        url,
        status=status,
        body=body,
        headers={"Content-Type": content_type},
        request=request,
        encoding="utf-8",
    )


@pytest.fixture
def sql_env(tmp_path, sql_template):
    """A SQL backend with a spider and crawler on it, plus its database URL and files folder."""
    repository, url, files = make_backend("sql", tmp_path, sql_template)
    run = repository.create_run(Cycle.ADHOC, date(2026, 11, 23), "tester", "tester")
    demo = DemoSpider(source=SOURCE_ID, run_id=run.id, repository=repository)
    stub = SimpleNamespace(spider=demo, settings=Settings({"REGCRAWLER_REPOSITORY": repository}))
    return SimpleNamespace(repo=repository, url=url, files=files, spider=demo, crawler=stub)


# --- running a spider's parse() on a saved file, through the real pipelines -----------------------


def file_response(path: str, url: str, status: int = 200, content_type: str = "application/xml"):
    """A response built from a saved file, as the downloader would hand it to the spider."""
    from scrapy.http import Response

    channel = "RSS" if path.startswith("rss/") else "XML"
    return Response(
        url,
        status=status,
        body=FIXTURES.joinpath(path).read_bytes(),
        headers={"Content-Type": content_type},
        request=Request(url, meta={"channel": channel}),
    )


def crawl(spider, response, crawler):
    """parse() the response, run every item through Snapshot -> Normalise -> Db, close the spider.

    Returns the listing rows that came out of the pipelines (what a real crawl would store)."""
    from scrapy.exceptions import DropItem

    from regcrawler.items import ListingItem
    from regcrawler.pipelines import DbPipeline, NormalisePipeline, SnapshotPipeline

    snapshot = SnapshotPipeline(crawler, spider.repo)
    normalise = NormalisePipeline(crawler)
    db = DbPipeline(crawler, spider.repo)
    spider.check.mark_attempted(spider.channels[0], response.status)
    out = []
    for item in spider.parse(response):
        try:
            out.append(db.process_item(normalise.process_item(snapshot.process_item(item))))
        except DropItem:
            pass
    db.close_spider()
    return [i for i in out if isinstance(i, ListingItem)]


def check_of(repo, spider, channel=None):
    wanted = channel or spider.channels[0]
    return next(c for c in repo.list_source_checks(spider.run.id) if c.channel == wanted)


def make_spider(cls, repo, source_id, **kwargs):
    run = repo.create_run(Cycle.ADHOC, date(2026, 11, 23), "tester", "tester")
    return cls(source=source_id, run_id=run.id, repository=repo, **kwargs)


def stub_crawler(spider, repo):
    return SimpleNamespace(spider=spider, settings=Settings({"REGCRAWLER_REPOSITORY": repo}))
