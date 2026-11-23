# CLAUDE.md — Regulatory Monitor (TVS Credit Compliance Automation, Module 1 POC)

## Source of truth (read before planning any task)
1. `docs/00-project-brief.md` — goal, milestones, decisions log, success metrics
2. `docs/01-PRD.md` — user journeys (J1–J9), functional requirements (FR-01…FR-28), NFRs, email spec
3. `docs/02-tech-stack.md` — chosen stack and trade-offs
4. `docs/architecture.md` — components, interactions, data model, API, repo structure
5. `docs/ui-mockups/*.html` — approved UI reference. New screens must match these patterns.
6. `docs/component-architecture.md` and `docs/system-design.md` — components, contracts, flows
7. `docs/constraints-and-policies.md` — policies; anything marked [HARD] is never waived
8. `docs/repository-etiquette.md` — branches, commits, PRs, tests, docs
9. `docs/agents/luna-nbfc-agent.md` — Luna NBFC Screening Agent (GPT Luna) spec
10. `docs/project-status.md` — update the milestone table when a prompt is completed

If a task conflicts with these documents, STOP and ask. Never silently change the architecture.
When a task changes behaviour described in a doc, update that doc in the same change.

## Stack (do not substitute)
Python 3.12 + uv · FastAPI + Uvicorn + Pydantic v2 · Jinja2 (page shells + email) ·
plain HTML/CSS/vanilla JS (ES modules, fetch; no frameworks, no build step, no CDN at runtime) ·
Scrapy (+ feedparser; scrapy-playwright only for sources with `requires_js: true`) ·
SQLAlchemy 2.0 + Alembic (SQLite for POC, Postgres-compatible) · APScheduler · pytest · ruff.

## Repository layout (from architecture.md §6)
```
app/{main.py,cli.py,api/,services/,repositories/,db/,schemas/,templates/,static/}
crawler/{scrapy.cfg,regcrawler/{settings.py,items.py,middlewares.py,pipelines.py,spiders/}}
config/{sources.yaml,settings.yaml}   data/ (gitignored)   docs/   tests/{unit,api,contract,crawler,fixtures}
```
Layering: static UI → API routers (validation only) → services (business logic) → repositories (data).
Routers never touch the DB directly. Scrapy only fetches, snapshots and parses; detection, review and email logic live in `app/services/`.

## NON-NEGOTIABLE RULES
1. Never bypass, solve or evade CAPTCHAs, bot checks or logins. No proxy rotation, UA rotation, stealth plugins, fingerprint evasion or header tricks. Blocked → record `BLOCKED`, create a manual-fetch path, move on.
2. Politeness: `ROBOTSTXT_OBEY=True`, identifying `USER_AGENT` with contact email, AutoThrottle on, `DOWNLOAD_DELAY >= 2`, `CONCURRENT_REQUESTS_PER_DOMAIN = 1`, timeouts.
3. Never suppress or delete a detected item. Low relevance = ranked lower / collapsed, never hidden.
4. The system never sends email. It renders and exports drafts only.
5. Nothing appears in the daily update without a recorded human review decision.
6. "New" is decided by fingerprint (source + reference no., else canonical URL) — never by the date shown on a website.
7. Snapshots, documents and audit events are immutable (write-once files with SHA-256; DB triggers block UPDATE/DELETE).
8. Every state change writes an audit event (actor, action, before, after) in the same transaction.
9. Sources, keywords, routing, recipients, email wording and run times are data, editable in the UI, versioned, and need approval by a different user.
10. Scrapy runs as a subprocess launched by the crawl runner — never inside the FastAPI event loop.
11. The Luna agent (GPT Luna, via `LlmClient`) is advisory: no tools, no browsing, text-in/JSON-out only;
    every field needs a verbatim, machine-validated evidence quote or it is null; it can only write
    `ai_extraction` / `agent_flag` / `agent_run`; it never decides, hides, approves or sends.
    Feature flag `AGENT_LUNA_ENABLED`; the system must work fully with it off. Only public regulator text is sent.

## UI rules (match docs/ui-mockups)
- Brand tokens: Dark Blue `#114984`, Emerald Green `#0E8943`, Grey 900 `#252728`, Grey 600 `#646569`, page bg `#F5F7FA`, border `#E3E7ED`.
- Status legend (separate tokens): Not applicable `#808080`, Action over `#0B8A3E`, In progress `#1F5FD6`, Not as per plan `#D93025`, Yet to start `#F2B200`.
- Font: self-hosted Jost (OFL) with fallback "Segoe UI", Arial. Logo: placeholder until Brand supplies the official file; never draw or copy the logo.
- Header (blue, green underline), left nav, cards, badges, tables, drawer form exactly as in mockups.
- Plain language, sentence case, dates DD.MM.YYYY, WCAG AA contrast, real `<button>/<a>/<label>`, keyboard usable, touch targets ≥ 44px, responsive down to 1024px.
- No business logic in JS; JS calls `/api/v1` and renders.

## Commands
`make dev` (uvicorn reload) · `make test` · `make lint` · `make migrate` · `make seed-demo` · `make crawl` · `make probe` · `make demo`

## Definition of done (every task)
- Acceptance criteria in the prompt met; `make test` and `make lint` green.
- No network access in unit tests (use fixtures in `tests/fixtures/`).
- Docs updated if behaviour changed; short summary of what changed and how to verify.
- Commit message: `<milestone>: <what>` (e.g. `M1: daily review page`).
