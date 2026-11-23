"""Document download spider: fetches the files behind detected items, politely.

    scrapy crawl docs -a dry_run=1 -a source_json=<file> -a urls_json=<file> \
        -a out_dir=<folder> -a results=<file>

Started by app/services/documents.py as a subprocess. It has the same politeness settings and
BlockDetectionMiddleware as every crawl (robots.txt, identifying User-Agent, 2 s delay, one request
at a time); it writes nothing to the database. It saves each answer's bytes in `out_dir` and a list
of outcomes in `results`; the Documents service stores them (write-once, hashed).

A block (403/429, bot check, login) stops the rest of the downloads for this source and every
remaining file is reported BLOCKED: the item then needs a manual fetch. robots.txt refusals are
treated the same way. Nothing is retried with altered headers and nothing is worked around.
"""

import json
from pathlib import Path
from typing import Any

from scrapy.exceptions import IgnoreRequest
from scrapy.http import Response
from scrapy.spidermiddlewares.httperror import HttpError
from twisted.python.failure import Failure

from app.schemas.enums import CheckChannel
from regcrawler.spiders.base import BaseRegSpider

CHANNEL = CheckChannel.HTML


class DocsSpider(BaseRegSpider):
    name = "docs"
    channels = (CHANNEL,)

    def __init__(
        self,
        *args: Any,
        urls_json: str | None = None,
        out_dir: str | None = None,
        results: str | None = None,
        **kwargs: Any,
    ) -> None:
        if not urls_json or not out_dir or not results:
            raise ValueError("docs needs -a urls_json=, -a out_dir= and -a results=")
        super().__init__(*args, **kwargs)
        self.urls: list[str] = json.loads(Path(urls_json).read_text(encoding="utf-8"))
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.results_file = Path(results)
        self.records: dict[str, dict[str, Any]] = {}

    async def start(self) -> Any:
        for index, url in enumerate(self.urls):
            yield self.get(self.target(url), CHANNEL, self.parse, index=index, wanted=url)

    def parse(self, response: Response) -> None:
        index = response.meta["index"]
        name = f"{index}.bin"
        (self.out_dir / name).write_bytes(response.body)
        self.records[response.meta["wanted"]] = {
            "url": response.meta["wanted"],
            "status": "OK",
            "http_status": response.status,
            "content_type": (response.headers.get("Content-Type") or b"").decode("latin-1"),
            "file": name,
        }

    def on_error(self, failure: Failure) -> None:
        super().on_error(failure)  # keeps the source check tracker honest
        wanted = failure.request.meta.get("wanted", failure.request.url)  # type: ignore[attr-defined]
        if failure.check(HttpError):
            status = failure.value.response.status
            self.records[wanted] = {
                "url": wanted,
                "status": "FAILED",
                "http_status": status,
                "detail": f"HTTP {status}",
            }
        elif failure.check(IgnoreRequest):
            state = self.check.state(CHANNEL)
            detail = (
                state.error_detail
                if self.check.blocked
                else "robots.txt does not allow automated download"
            )
            self.records[wanted] = {
                "url": wanted,
                "status": "BLOCKED",
                "http_status": state.http_status,
                "detail": detail,
            }
        else:
            self.records[wanted] = {
                "url": wanted,
                "status": "FAILED",
                "detail": f"{failure.type.__name__}",
            }

    def closed(self, reason: str) -> None:
        # Anything neither answered nor failed (for example dropped after a block) is reported
        # as BLOCKED when the source was blocked, FAILED otherwise.
        for url in self.urls:
            self.records.setdefault(
                url,
                {
                    "url": url,
                    "status": "BLOCKED" if self.check.blocked else "FAILED",
                    "detail": "Not downloaded: the source was blocked earlier"
                    if self.check.blocked
                    else "Not downloaded",
                },
            )
        self.results_file.write_text(
            json.dumps([self.records[u] for u in self.urls], ensure_ascii=False), encoding="utf-8"
        )
