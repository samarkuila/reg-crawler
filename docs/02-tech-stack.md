# Tech Stack Report — Regulatory Monitor

**Version:** 0.1 · **Date:** 21.11.2026
**Requirements driving the choice:** ~30 known URLs crawled twice daily (not web-scale); RSS + HTML + XML sources; strict politeness and no bot-evasion; small internal user base (5–20); non-technical users; Python skills in D&A; end-of-November POC deadline; must grow into a multi-module compliance platform.

---

## Summary of recommendation

| Layer | POC (V1) | Pilot / production (V2–V3) |
|---|---|---|
| Crawling | **Scrapy** (+ scrapy-playwright only where JS is unavoidable) | Same |
| RSS / feeds | Scrapy spider + **feedparser** for parsing | Same |
| Backend | **FastAPI** + Pydantic v2 | Same |
| Frontend | **Plain HTML, CSS, vanilla JS** (served by FastAPI, Jinja2 shells) | Same, or HTMX for richer interactions; React only if Modules 2–8 need complex editors |
| Database | **SQLite** via SQLAlchemy 2.0 + Alembic | **PostgreSQL** (or SQL Server if mandated by IT) |
| File storage | Local disk, write-once, SHA-256 named | OneDrive/SharePoint for circular folders; blob storage with retention for evidence |
| Scheduling | **APScheduler** in the app (single worker) | APScheduler or Prefect if job volume grows |
| Auth | POC user switcher | **Microsoft Entra ID** (OIDC) with role mapping |
| Email | Render HTML + export `.eml`; human sends | **Microsoft Graph** draft in Outlook, reply in existing thread |
| AI (optional) | Off by default; behind `LlmClient` interface | Claude via TVS-approved route (Anthropic API, AWS Bedrock, Google Vertex or Microsoft Foundry) |
| Hosting | D&A machine / single VM in TVS network | TVS-approved VM or cloud container service |
| Payments | Not applicable (internal tool) | Not applicable |

---

## 1. Crawling

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Scrapy** | Mature; built-in robots.txt, AutoThrottle, per-domain concurrency, retries, item pipelines, FilesPipeline for documents, spider contracts for testing | Runs on its own event loop (Twisted/asyncio) — awkward to embed inside a web server; learning curve | **Chosen** (team request). Run as a **subprocess** from FastAPI to avoid event-loop conflicts. |
| httpx + BeautifulSoup/selectolax | Very simple; easy to embed | Politeness, retries, pipelines all hand-built | Good fallback for one-off scripts |
| Playwright alone | Handles JavaScript pages | Heavy, slow, more likely to look like automation | Only via scrapy-playwright for sources a person sets to `fetch_engine = browser` |
| Managed scraping services | No infra | Data leaves TVS; cost; many market "anti-bot bypass" — conflicts with our rules | Rejected |

**Rules regardless of tool:** obey robots.txt, identify the user agent, ≥2 s delay per host, no proxy/UA rotation, no CAPTCHA solving, no stealth plugins.

## 2. RSS / feeds

| Option | Pros | Cons |
|---|---|---|
| Scrapy XMLFeedSpider | Native to Scrapy | Less forgiving with malformed feeds |
| **Scrapy spider + feedparser** | feedparser handles RSS/Atom quirks and dates robustly; Scrapy still does fetching/politeness | One extra dependency |

**Recommendation:** fetch with Scrapy, parse with feedparser. RBI's notification feed carries notification text in the description — store it as document text.

## 3. Backend

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **FastAPI** | Fast to build, typed (Pydantic), automatic OpenAPI docs, async-friendly, easy for Claude Code | Fewer batteries (admin, auth) than Django | **Chosen** |
| Django | Built-in admin, auth, ORM | Heavier; admin UI not suited to Compliance users without customisation | Viable alternative |
| Flask | Minimal | More hand-wiring than FastAPI | No advantage |

## 4. Frontend

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Vanilla HTML/CSS/JS** | No build step; easy hosting; full brand control; simple for internal IT | More manual DOM code as screens grow | **Chosen for POC** |
| HTMX + Jinja | Server-rendered interactivity with little JS | New concept for team | Consider in V2 |
| React/Vue | Rich components, state management | Build tooling, larger skill needs | Only if Modules 2–8 need heavy editors (e.g. comparison views) |

**Theme:** TVS Credit Dark Blue `#114984`, Emerald Green `#0E8943`, greys `#252728` / `#646569` (brand kit). Logo typeface is Futura (licensed) → UI uses self-hosted **Jost** (open licence, Futura-like). Official logo files from Brand team.

## 5. Database

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **SQLite** | Zero setup; single file; perfect for POC | Single writer; not for multi-server | **POC** |
| **PostgreSQL** | Robust, JSONB, triggers, concurrency, backups | Needs a server | **V2+** |
| SQL Server | Possibly TVS standard | Licence; slightly different SQL | If IT mandates |

Use SQLAlchemy 2.0 + Alembic and avoid DB-specific types so switching is a config change.

## 6. Scheduling

| Option | Pros | Cons |
|---|---|---|
| **APScheduler (in app)** | Simple; settings-driven times; no extra service | Must run a single app worker (or a separate scheduler process) |
| OS cron / Task Scheduler | Very simple | Config outside the app; not editable by Compliance |
| Celery beat | Distributed | Needs broker (Redis); overkill |
| Prefect / Airflow | Run history, retries, UI | Extra platform to operate |

## 7. Authentication

| Stage | Choice | Notes |
|---|---|---|
| POC | User switcher from `config/settings.yaml` | Clearly labelled "POC only" |
| V2 | Microsoft Entra ID (OIDC via MSAL) | Map AD groups to viewer/reviewer/approver/admin |

## 8. Email

| Stage | Choice | Notes |
|---|---|---|
| POC | Jinja2 HTML (table layout, inline CSS for Outlook) + `.eml` export | Human sends |
| V2 | Microsoft Graph: create draft as reply in existing thread | Needs IT app registration and mailbox permissions |

## 9. Hosting

| Option | Pros | Cons |
|---|---|---|
| **Single VM / workstation in TVS network** | Fast to start; data stays internal | Single point of failure |
| Cloud containers (Azure/AWS) | Scalable, managed | Approvals, cost, setup time |

## 10. Testing & quality

pytest (unit, API, crawler fixtures), Scrapy spider contracts, saved real responses as fixtures (no network in unit tests), ruff lint, contract tests across in-memory and SQL repositories, shadow-comparison tool against manual emails.

## Key trade-offs to confirm
1. SQLite now vs Postgres from day 1 (speed vs zero migration later).
2. Vanilla JS vs HTMX (simplicity now vs less JS later).
3. Subprocess per source (isolation, simplicity) vs one Scrapy process for all (faster runs).
4. AI drafting in or out of POC (wow factor vs scope risk and approvals).
