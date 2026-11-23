"""Scrapy settings for the regulatory crawler.

Politeness is fixed here (CLAUDE.md rule 2) and is not configurable per source: robots.txt is
obeyed, the User-Agent identifies us with a contact address, requests to one domain are sequential
and at least 2 seconds apart, and nothing is retried except server errors and timeouts.

Not done here, on purpose (rule 1): no proxies, no User-Agent or header rotation, no cookie reuse,
no stealth. A blocked source is recorded as BLOCKED and left alone.

Profiles: REGCRAWLER_PROFILE=dev turns the HTTP cache on for local development (fewer requests to
real sites while building selectors). Production runs never use it.
"""

import os
import sys

from regcrawler.config import DATA_DIR, ROOT, load_config

# The crawler imports app.repositories / app.services, and is started from the crawler/ folder.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# Paths are anchored to the repository root so the database and stored files are the same ones the
# web app uses, whichever folder the crawl is started from.
os.environ.setdefault("FILES_ROOT", str(ROOT))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'reg_monitor.db').as_posix()}")
os.environ.setdefault("REPO_BACKEND", "sql")

PROFILE = os.environ.get("REGCRAWLER_PROFILE", "prod").strip().lower()

BOT_NAME = "regcrawler"
SPIDER_MODULES = ["regcrawler.spiders"]
NEWSPIDER_MODULE = "regcrawler.spiders"
ADDONS = {}

# --- Politeness (rule 2) -----------------------------------------------------------------------
ROBOTSTXT_OBEY = True
USER_AGENT = f"TVSCredit-RegMonitor/0.1 (+{load_config().contact})"
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 2
AUTOTHROTTLE_MAX_DELAY = 30
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
DOWNLOAD_DELAY = 2
# Scrapy 2.20: the default jitter would shorten some delays to 1 second; keep every gap >= 2 s.
DOWNLOAD_DELAY_JITTER = 0
CONCURRENT_REQUESTS = 1
CONCURRENT_REQUESTS_PER_DOMAIN = 1
DOWNLOAD_TIMEOUT = 30
DOWNLOAD_MAXSIZE = 25 * 1024 * 1024

# --- Retries: server errors and timeouts only, with the request exactly as it was ---------------
RETRY_ENABLED = True
RETRY_TIMES = 2
RETRY_HTTP_CODES = [500, 502, 503, 504, 522, 524]  # never 403/429: those are blocks, not faults

# --- Statelessness: no cookies are kept between requests -------------------------------------
COOKIES_ENABLED = False
TELNETCONSOLE_ENABLED = False

# --- Pipeline order: Snapshot (raw bytes) -> Normalise -> DB write -------------------------------
DOWNLOADER_MIDDLEWARES = {
    # 560: after the redirect (600) and decompression (590) middlewares, so we judge the final,
    # decoded response, and before the retry middleware (550), so a block is never retried.
    "regcrawler.middlewares.BlockDetectionMiddleware": 560,
}
ITEM_PIPELINES = {
    "regcrawler.pipelines.SnapshotPipeline": 100,
    "regcrawler.pipelines.NormalisePipeline": 200,
    "regcrawler.pipelines.DbPipeline": 300,
}

# --- Cache (dev profile only) ------------------------------------------------------------------
HTTPCACHE_ENABLED = PROFILE == "dev"
HTTPCACHE_DIR = str(DATA_DIR / "httpcache")
HTTPCACHE_EXPIRATION_SECS = 6 * 60 * 60
HTTPCACHE_IGNORE_HTTP_CODES = [401, 403, 429, 500, 502, 503, 504]

# --- Logs go to data/logs/ ---------------------------------------------------------------------
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = str(LOG_DIR / "crawler.log")  # the crawl runner overrides it per run with -s LOG_FILE=
LOG_LEVEL = "INFO"

DOWNLOADER_MIDDLEWARE_RESPONSE_EXCEPTIONS = True
FEED_EXPORT_ENCODING = "utf-8"
FEED_MODE = "create"
