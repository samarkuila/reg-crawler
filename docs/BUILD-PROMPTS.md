# Build Guide — Claude Code prompts for the Regulatory Monitor POC

Follow these prompts **in order**. They implement `docs/architecture.md` exactly and reproduce the approved UI in `docs/ui-mockups/`.

---

## How to work (read once)

**Setup:** repo `C:\Projects\reg-crawler` (GitHub `samarkuila/reg-crawler`, private) containing
`README.md`, `.gitignore`, `CLAUDE.md`, `docs/`. Start Claude Code there with `claude`.

**First session only** — paste the one-time note and the understanding check:
```
This repository is named reg-crawler (GitHub: samarkuila/reg-crawler). The application is still
called "Regulatory Monitor" as in the docs. Wherever the docs say reg-monitor/, use this repo root.
Never commit .env, data/ or any secrets; respect .gitignore.

Read CLAUDE.md and every file in docs/ (including docs/agents/, docs/ui-mockups/ and
docs/phase1-requirements-traceability.md). Do not write code. Reply with: (1) 10 bullets on what
we're building, (2) the [HARD] policies, (3) the folder structure you'll follow, (4) the prompt
order in docs/BUILD-PROMPTS.md, (5) anything unclear or contradictory across the docs.
```

**For every prompt below — 6 steps, nothing to edit, just copy and paste:**
1. Type `/clear` and press Enter.
2. If the prompt title has ⚙️, press **Shift+Tab** until you see *plan mode*.
3. Copy the **whole grey box** under the prompt → paste → Enter. (Plan mode: read the plan → approve.)
4. When Claude stops, paste the **Checkpoint prompt** (bottom of this file). Fix any ❌.
5. Check it yourself (open the page / run the command in the Acceptance line).
6. Paste the **"After the checkpoint is all ✅"** box under that prompt → it commits, merges, pushes and updates the status file.

**If it drifts:** press Esc, then: "Stop. Re-read CLAUDE.md, docs/architecture.md and this
prompt's acceptance criteria. Undo anything outside this prompt's scope; fix only what fails."

| Milestone | Prompts |
|---|---|
| M0 Setup | 0 |
| M1 UI on mock data | 1 – 6 |
| M2 Database | 7 – 8 |
| M3 Crawler | 9 – 11 |
| M4 Detection & run control | 12 – 13 |
| M4b Luna agent (optional, flagged) | 13b |
| M5 POC test | 14 – 15 |

---

## M0 — Setup

### Prompt 0 — Scaffold ⚙️
```
[Repo: reg-crawler · Branch: feat/m0-scaffold · Milestone: M0]
First: switch to main, pull from origin, then create and switch to branch feat/m0-scaffold.
Follow CLAUDE.md, docs/architecture.md, docs/component-architecture.md,
docs/constraints-and-policies.md and docs/repository-etiquette.md.
We are on Windows: run project commands with .\tasks.ps1 <target>.

TASK:
The repo already contains README.md, .gitignore, CLAUDE.md and docs/ (pushed to GitHub
samarkuila/reg-crawler). Do not overwrite docs/ or CLAUDE.md; extend README.md and .gitignore.
The application name stays "Regulatory Monitor"; the repo root replaces reg-monitor/ in the docs.
Besides the Makefile, create tasks.ps1 with identical targets (dev, test, lint, migrate,
seed-demo, crawl, probe, demo) and document .\tasks.ps1 usage in README.

Read CLAUDE.md and every file in docs/ (including docs/ui-mockups/). Summarise in 10 bullets
what we are building and the non-negotiable rules, then scaffold the project exactly as in
docs/architecture.md §6:

- uv project, Python 3.12. Dependencies: fastapi, uvicorn[standard], pydantic>=2, jinja2,
  python-multipart, sqlalchemy>=2, alembic, scrapy, feedparser, pymupdf, pdfplumber,
  apscheduler, pyyaml, python-dotenv, structlog; dev: pytest, pytest-cov, httpx, ruff.
- Folders: app/{api,services,repositories,db,schemas,templates,static/{css,js/pages,fonts,img}},
  crawler/ (run `scrapy startproject regcrawler` inside it), config/, data/ (gitignored),
  tests/{unit,api,contract,crawler,fixtures/{rss,html,xml}}.
- app/main.py: FastAPI app, mounts /static, a /health endpoint, Jinja2 templates.
- Makefile targets from CLAUDE.md (stubs allowed for later ones), .env.example, .gitignore,
  ruff config, pytest config, README.md with run instructions.
Do NOT build features yet.

Acceptance: `.\tasks.ps1 dev` serves http://localhost:8000/health → {"status":"ok"}; `.\tasks.ps1 test` and
`.\tasks.ps1 lint` pass; `cd crawler; uv run scrapy list` runs without error (PowerShell).

When finished: run .\tasks.ps1 test and .\tasks.ps1 lint, then STOP and wait for my
checkpoint review. Do NOT commit, merge or push yet.
```

**After the checkpoint is all ✅, paste:**
```
Commit all changes on feat/m0-scaffold with message "chore(scaffold): project skeleton [M0]", push the branch to origin,
merge feat/m0-scaffold into main, push main to origin, then update docs/project-status.md
(mark Prompt 0 done with today's date and any open issues), commit that as
"docs(status): prompt 0 done [M0]" and push main.
```


---

## M1 — UI on mock data (FastAPI + HTML/CSS/JS)

### Prompt 1 — Design system extracted from the mockups ⚙️
```
[Repo: reg-crawler · Branch: feat/m1-design-system · Milestone: M1]
First: switch to main, pull from origin, then create and switch to branch feat/m1-design-system.
Follow CLAUDE.md, docs/architecture.md, docs/component-architecture.md,
docs/constraints-and-policies.md and docs/repository-etiquette.md.
We are on Windows: run project commands with .\tasks.ps1 <target>.

TASK:
Read CLAUDE.md "UI rules" and study docs/ui-mockups/*.html (they share one header, nav,
card, badge, button, table, form and drawer style).

Build the reusable design system so every page looks like the mockups:
1. app/static/css/tokens.css — CSS variables for brand colours, status legend colours, greys,
   background/border, spacing (4px scale), radius (6px controls, 8px cards), font sizes.
2. app/static/fonts/ — self-host Jost (400/500/600, woff2) + OFL licence file; no CDN at runtime.
3. app/static/css/components.css — classes reproducing the mockups: header, nav (+ .on state),
   .card, .b badges (new, mod, back, urg, ok, grey), .btn (p, g, o, d), tables, form controls,
   .dot status circles, drawer/aside, toast, modal, empty state, loading skeleton.
4. app/templates/base.html — header (logo placeholder img at /static/img/logo-placeholder.svg
   labelled "Replace with official TVS Credit logo"; never draw the real logo), product name,
   POC tag, cycle selector, Run now button, current user; left nav: Daily review, Source health,
   Email preview, Item history, Admin: Sources, Settings, Audit log. Blocks for title and content.
5. app/static/js/ui.js (toast, drawer, modal, DD.MM.YYYY formatting, escapeHtml) and
   app/static/js/api.js (fetch wrapper for /api/v1 with error toasts).
6. /styleguide page showing every component.
Move styles from the mockups into these files (no inline-style soup in templates).

Acceptance: /styleguide renders every component visually identical to the mockups; no
external network requests; contrast passes WCAG AA.

When finished: run .\tasks.ps1 test and .\tasks.ps1 lint, then STOP and wait for my
checkpoint review. Do NOT commit, merge or push yet.
```

**After the checkpoint is all ✅, paste:**
```
Commit all changes on feat/m1-design-system with message "feat(ui): design system and base layout [M1]", push the branch to origin,
merge feat/m1-design-system into main, push main to origin, then update docs/project-status.md
(mark Prompt 1 done with today's date and any open issues), commit that as
"docs(status): prompt 1 done [M1]" and push main.
```


### Prompt 2 — Schemas, mock repository and API ⚙️
```
[Repo: reg-crawler · Branch: feat/m1-api-mock · Milestone: M1]
First: switch to main, pull from origin, then create and switch to branch feat/m1-api-mock.
Follow CLAUDE.md, docs/architecture.md, docs/component-architecture.md,
docs/constraints-and-policies.md and docs/repository-etiquette.md.
We are on Windows: run project commands with .\tasks.ps1 <target>.

TASK:
Read docs/architecture.md §4 (data model) and §5 (API) and docs/01-PRD.md §4 (concepts, states).

1. app/schemas/: Pydantic v2 models and enums for every entity in architecture §4
   (Source, SourceVersion, CrawlRun, SourceCheck, Snapshot, RegulatoryItem, ItemObservation,
   Document, ReviewDecision, DailyUpdate, DailyUpdateItem, ManualCheck, SmeFunction, Recipient,
   EmailTemplate, Setting, AppUser, AuditEvent) with the exact enum values listed there.
2. app/repositories/base.py: a `Repository` Protocol covering everything the API needs.
   app/repositories/memory.py: in-memory implementation. Every mutating method takes `actor`
   and records an AuditEvent. app/repositories/factory.py: REPO_BACKEND=memory|sql
   (sql → NotImplementedError for now).
3. Seed data that reproduces the mockups: 5 POC sources + 8 planned (inactive); the
   20.11.2026 EOD run with eGazette BLOCKED; items: UNSC amendment (URGENT), IoRS FAQs
   (MODIFIED, BOTH), a BACKDATED RBI item, an RSS-only item (channel mismatch), an IRDAI item
   (DECIDED), a NEEDS_MANUAL_FETCH item, 3 low-relevance items; the PIB 2336151 decision for
   the email; one pending source version for eGazette; users Divya (reviewer), George
   (approver), Samarendra (admin), Audit (viewer).
4. app/services/: thin service classes (review, config, audit, email_render stub) used by routers.
5. app/api/: every endpoint in architecture §5 under /api/v1 (crawl/run returns 501 for now),
   plus page routes returning Jinja shells (/, /review, /health, /email, /history, /sources,
   /settings, /audit, /styleguide).
6. POC user switcher: header dropdown, stores selected user in a cookie, labelled "POC only".
   Role checks enforced in services (approver ≠ requester for approvals).

Acceptance: /docs lists all endpoints; tests in tests/api/ cover each endpoint on mock data;
audit events are produced for every mutation.

When finished: run .\tasks.ps1 test and .\tasks.ps1 lint, then STOP and wait for my
checkpoint review. Do NOT commit, merge or push yet.
```

**After the checkpoint is all ✅, paste:**
```
Commit all changes on feat/m1-api-mock with message "feat(api): schemas, mock repository and API [M1]", push the branch to origin,
merge feat/m1-api-mock into main, push main to origin, then update docs/project-status.md
(mark Prompt 2 done with today's date and any open issues), commit that as
"docs(status): prompt 2 done [M1]" and push main.
```


### Prompt 3 — Daily Review page (J1, J3, J4, J5, J7) ⚙️
```
[Repo: reg-crawler · Branch: feat/m1-daily-review · Milestone: M1]
First: switch to main, pull from origin, then create and switch to branch feat/m1-daily-review.
Follow CLAUDE.md, docs/architecture.md, docs/component-architecture.md,
docs/constraints-and-policies.md and docs/repository-etiquette.md.
We are on Windows: run project commands with .\tasks.ps1 <target>.

TASK:
Build the Daily Review page to match docs/ui-mockups/01-daily-review.html, using the design
system and data from /api/v1 (app/templates/pages/review.html + app/static/js/pages/review.js).
Implements FR-15 … FR-21.

- Source health strip first; failed/blocked sources in red with reason and
  "Mark checked manually" (modal with note → POST manual-check).
- Count tiles: New, Modified, Backdated, Urgent, Needs manual fetch, Decided x of y.
- "Needs attention" table sorted URGENT → relevance; badges for change type and channel
  mismatch; relevance bar with plain-English reason; filters (authority, search, change type).
- Row click opens the right-hand review panel with fields in email column order
  (Reference No. and date, Authority, Applicability, Issue date, Effective date, Guidelines
  link, Status radio pills with legend colours, Impact on TVSCS, Treatment, SME function,
  Include in today's update) + Source evidence box + Accept / Save / Dismiss (reason required).
- "Needs manual fetch" card with upload (POST upload; shows "manually supplied by …").
- "Probably not relevant" card: collapsed list, checkboxes, Dismiss selected with one reason.
- Keyboard: J/K move, Enter open, A accept, D dismiss, Esc close. Toasts on save.
- Saving records fields_edited vs pre-filled values.

Acceptance: side-by-side with the mockup the page matches; I can accept IoRS, bulk dismiss
the low-relevance items, upload a document, mark eGazette checked; each action appears in
GET /api/v1/audit.

When finished: run .\tasks.ps1 test and .\tasks.ps1 lint, then STOP and wait for my
checkpoint review. Do NOT commit, merge or push yet.
```

**After the checkpoint is all ✅, paste:**
```
Commit all changes on feat/m1-daily-review with message "feat(review): daily review page [M1]", push the branch to origin,
merge feat/m1-daily-review into main, push main to origin, then update docs/project-status.md
(mark Prompt 3 done with today's date and any open issues), commit that as
"docs(status): prompt 3 done [M1]" and push main.
```


### Prompt 4 — Email Preview page and renderer (J1, J2, J6)
```
[Repo: reg-crawler · Branch: feat/m1-email-preview · Milestone: M1]
First: switch to main, pull from origin, then create and switch to branch feat/m1-email-preview.
Follow CLAUDE.md, docs/architecture.md, docs/component-architecture.md,
docs/constraints-and-policies.md and docs/repository-etiquette.md.
We are on Windows: run project commands with .\tasks.ps1 <target>.

TASK:
Build app/services/email_render.py and the Email Preview page matching
docs/ui-mockups/02-email-preview.html. Implements FR-22 … FR-25 and PRD §9.

- Jinja2 template app/templates/email/daily_update.html: table layout with inline CSS that
  renders in Outlook desktop; greeting, intro, 8 columns (S.No | Reference No. and Date |
  Authority | Guidelines (title + link) | Applicability | Effective Date | Status ● |
  Impact on TVSCS), italic disclaimer, colour legend, "Regards," + sender. All text from the
  EmailTemplate settings.
- No-update variant; failed sources must be acknowledged (named in the email) before approval.
- FR-33: when issue date and website publish date differ, the Reference No. and Date cell shows
  "Date: DD.MM.YYYY · Published on website: DD.MM.YYYY".
- FR-32: "Export cycle to Excel" — every captured item of the cycle (incl. low-relevance and
  dismissed) with a blank "Compliance marking" column.
- Subject from pattern setting; CC adds SME heads for OTHER_FUNCTION items.
- Page: pre-send checks card, rows card with drag-to-reorder (S.No), subject/recipients card,
  live preview panel, buttons Copy HTML, Download .eml (unsent draft, X-Unsent: 1),
  Approve (approver role, different from preparer).

Acceptance: preview matches the mockup and Divya's original email; snapshot tests for normal,
no-update, failed-source and SME-CC cases; system has no code path that sends mail.

When finished: run .\tasks.ps1 test and .\tasks.ps1 lint, then STOP and wait for my
checkpoint review. Do NOT commit, merge or push yet.
```

**After the checkpoint is all ✅, paste:**
```
Commit all changes on feat/m1-email-preview with message "feat(email): email preview and renderer [M1]", push the branch to origin,
merge feat/m1-email-preview into main, push main to origin, then update docs/project-status.md
(mark Prompt 4 done with today's date and any open issues), commit that as
"docs(status): prompt 4 done [M1]" and push main.
```


### Prompt 5 — Source Health and Item History pages (J9)
```
[Repo: reg-crawler · Branch: feat/m1-health-history · Milestone: M1]
First: switch to main, pull from origin, then create and switch to branch feat/m1-health-history.
Follow CLAUDE.md, docs/architecture.md, docs/component-architecture.md,
docs/constraints-and-policies.md and docs/repository-etiquette.md.
We are on Windows: run project commands with .\tasks.ps1 <target>.

TASK:
Build two pages:
1. Source Health matching docs/ui-mockups/03-source-health.html: current-run progress panel
   (polls GET /runs/{id} every 3 s while running), table of the last four cycles per source and
   channel with status badges (OK, Zero items, Mismatch, Blocked, Failed), actions (view snapshot,
   compare RSS vs HTML, view log, mark checked manually), status legend card.
2. Item History matching docs/ui-mockups/05-item-history.html: search form (title/ref no.,
   authority, date range), results list, traceability chain as numbered steps:
   email → review decision (who/when/fields edited) → document (SHA-256, open stored copy) →
   change detected (type, before/after hash) → listing snapshot (SHA-256) → source
   configuration version. Export trail as CSV. Implements FR-27.

Acceptance: both pages match the mockups on seed data; the chain for IoRS shows all six steps
with working links.

When finished: run .\tasks.ps1 test and .\tasks.ps1 lint, then STOP and wait for my
checkpoint review. Do NOT commit, merge or push yet.
```

**After the checkpoint is all ✅, paste:**
```
Commit all changes on feat/m1-health-history with message "feat(ui): source health and item history pages [M1]", push the branch to origin,
merge feat/m1-health-history into main, push main to origin, then update docs/project-status.md
(mark Prompt 5 done with today's date and any open issues), commit that as
"docs(status): prompt 5 done [M1]" and push main.
```


### Prompt 6 — Sources, Settings and Audit Log pages (J8) ⚙️
```
[Repo: reg-crawler · Branch: feat/m1-admin · Milestone: M1]
First: switch to main, pull from origin, then create and switch to branch feat/m1-admin.
Follow CLAUDE.md, docs/architecture.md, docs/component-architecture.md,
docs/constraints-and-policies.md and docs/repository-etiquette.md.
We are on Windows: run project commands with .\tasks.ps1 <target>.

TASK:
Build the admin pages. Sources must match docs/ui-mockups/04-sources-admin.html; Settings and
Audit Log have no mockup — build them from the same components and layout patterns.

1. Sources: pending-approval card with before/after diff and Approve / Reject / Test this source;
   source register table; Add/Edit form (authority, page name, URL, RSS URL, type, selectors
   row/title/link/date/ref_no regex, date format, include/exclude keywords, priority, requires_js,
   active). Saving creates a PENDING version (FR-01, FR-02). "Test this source" shows a result
   card (stub data until Prompt 13).
2. Settings: tabs or sections for run times + working days + holidays + lookback days, SME
   routing table, recipients To/CC, email template texts, dismiss reasons, relevance keywords.
   Each save = new version, audited (FR-04).
3. Audit Log: filter by actor, entity, action, date range; read-only table with before/after
   expandable JSON shown as readable key/value; CSV export (FR-28).

Acceptance: an edit by Samarendra stays PENDING until George approves; George cannot approve
his own edit; every change visible in the audit log.

When finished: run .\tasks.ps1 test and .\tasks.ps1 lint, then STOP and wait for my
checkpoint review. Do NOT commit, merge or push yet.
```

**After the checkpoint is all ✅, paste:**
```
Commit all changes on feat/m1-admin with message "feat(config): sources, settings and audit log pages [M1]", push the branch to origin,
merge feat/m1-admin into main, push main to origin, then update docs/project-status.md
(mark Prompt 6 done with today's date and any open issues), commit that as
"docs(status): prompt 6 done [M1]" and push main.
```


**M1 review:** demo all pages on mock data to Divya for feedback before starting M2.

---

## M2 — Database

### Prompt 7 — SQLAlchemy models, migrations, immutability ⚙️
```
[Repo: reg-crawler · Branch: feat/m2-db · Milestone: M2]
First: switch to main, pull from origin, then create and switch to branch feat/m2-db.
Follow CLAUDE.md, docs/architecture.md, docs/component-architecture.md,
docs/constraints-and-policies.md and docs/repository-etiquette.md.
We are on Windows: run project commands with .\tasks.ps1 <target>.

TASK:
Read docs/architecture.md §4. Implement app/db/models.py (SQLAlchemy 2.0 typed ORM) with every
table and column listed there, app/db/session.py, and Alembic migrations in app/db/migrations/.
- SQLite for POC, Postgres-compatible: UUIDs as strings, JSON columns, no SQLite-only features
  in models.
- internal_ref generator TVSC-REG-YYYY-NNNNNN (counter table, safe under concurrency).
- Triggers rejecting UPDATE/DELETE on snapshot, document, audit_event (SQLite migration);
  put the equivalent Postgres trigger SQL in docs/postgres-migration.md.
- Indexes: regulatory_item.fingerprint (unique), (source_id, first_seen_at), state,
  crawl_run.cycle_date.
- docs/data-model.md with a Mermaid ER diagram generated from the models.

Acceptance: `.\tasks.ps1 migrate` builds a fresh DB; tests prove audit_event/snapshot/document rows
cannot be updated or deleted; schema matches architecture §4 (list any deviation and why).

When finished: run .\tasks.ps1 test and .\tasks.ps1 lint, then STOP and wait for my
checkpoint review. Do NOT commit, merge or push yet.
```

**After the checkpoint is all ✅, paste:**
```
Commit all changes on feat/m2-db with message "feat(db): models, migrations and immutability triggers [M2]", push the branch to origin,
merge feat/m2-db into main, push main to origin, then update docs/project-status.md
(mark Prompt 7 done with today's date and any open issues), commit that as
"docs(status): prompt 7 done [M2]" and push main.
```


### Prompt 8 — SQL repository, contract tests, real source register
```
[Repo: reg-crawler · Branch: feat/m2-sql-repo · Milestone: M2]
First: switch to main, pull from origin, then create and switch to branch feat/m2-sql-repo.
Follow CLAUDE.md, docs/architecture.md, docs/component-architecture.md,
docs/constraints-and-policies.md and docs/repository-etiquette.md.
We are on Windows: run project commands with .\tasks.ps1 <target>.

TASK:
1. app/repositories/sql.py implementing the same Repository Protocol; each mutation and its
   audit event in one transaction. Factory: REPO_BACKEND=sql.
2. tests/contract/: one test suite parametrised over memory AND sql backends — identical results.
3. `.\tasks.ps1 seed-demo` loads the mockup seed data into SQLite.
4. config/sources.yaml with the 5 POC sources in the architecture §6 format (FR-29, FR-30):
   RBI Notifications (rss_url https://rbi.org.in/notifications_rss.xml + HTML listing),
   RBI Press Releases (rss_url https://rbi.org.in/pressreleases_rss.xml + HTML listing),
   RBI Circulars, RBI Master Directions, RBI Master Circulars, RBI FAQs, RBI Home/What's New
   (HTML_LIST each — FAQs must include the Non-Banking section; the IoRS FAQ is our test case),
   UNSC consolidated list (STRUCTURED_DATA, https://scsanctions.un.org/resources/xml/en/consolidated.xml,
   priority URGENT), TRAI press releases (HTML_LIST https://trai.gov.in/notifications/press-release),
   IRDAI circulars/guidelines (HTML_LIST, policyholder.gov.in), eGazette recent extraordinary
   gazettes (HTML_LIST https://egazette.gov.in, ministry filter; never the search form);
   planned sources inactive with url TBD: UIDAI, CERSAI, CKYC, NPCI, MeitY, PIB, FIU-IND, NFRA,
   MHA, SLBC meetings. Selectors are placeholders until Prompt 11.
   Weekly gazettes ("multiple ministries") are always kept as candidates — never dropped by the
   ministry filter.
   Seed keywords (FR-31): global include [NBFC, non-banking, regulated entities, all lending
   institutions, KYC, AML, sanctions, FEMA, DPDP]; TRAI include [regulation, direction,
   recommendation, consultation paper]; global exclude [auction, variable rate repo,
   money market operations, maintenance, downtime, marks obtained, vacancy, recruitment,
   tender, bid, procurement, citizenship, gross direct premium].
   Default screening guidance text: "A regulatory update is anything where a regulator requires
   or informs regulated entities like TVS Credit (an NBFC) and that has an economic, social,
   operational or administrative impact on TVS Credit. Ignore regulators' own vacancies, tenders,
   events and maintenance notices."
   Entity Profile setting (FR-35) seeded with placeholders for Compliance to complete.
5. CLI: `python -m app.cli import-sources config/sources.yaml --actor <name>` — idempotent,
   creates PENDING versions.
6. CLI `python -m app.cli import-monitoring-excel <file.xlsx> --actor <name>`: reads Compliance's
   website monitoring Excel (site, link, tabs/keywords, documents to fetch) and creates PENDING
   source versions / keyword settings from it, reporting rows it could not map.

Acceptance: contract tests green for both backends; UI works unchanged with REPO_BACKEND=sql;
imported sources appear as pending approval.

When finished: run .\tasks.ps1 test and .\tasks.ps1 lint, then STOP and wait for my
checkpoint review. Do NOT commit, merge or push yet.
```

**After the checkpoint is all ✅, paste:**
```
Commit all changes on feat/m2-sql-repo with message "feat(db): SQL repository, contract tests, source register [M2]", push the branch to origin,
merge feat/m2-sql-repo into main, push main to origin, then update docs/project-status.md
(mark Prompt 8 done with today's date and any open issues), commit that as
"docs(status): prompt 8 done [M2]" and push main.
```


---

## M3 — Crawler (Scrapy: RSS + HTML + XML)

### Prompt 9 — Scrapy foundation: settings, block detection, pipelines ⚙️
```
[Repo: reg-crawler · Branch: feat/m3-scrapy-core · Milestone: M3]
First: switch to main, pull from origin, then create and switch to branch feat/m3-scrapy-core.
Follow CLAUDE.md, docs/architecture.md, docs/component-architecture.md,
docs/constraints-and-policies.md and docs/repository-etiquette.md.
We are on Windows: run project commands with .\tasks.ps1 <target>.

TASK:
Read CLAUDE.md rules 1, 2, 6, 7, 10 and docs/architecture.md §2–3.1. First check the installed
Scrapy version's docs for spider start API, pipelines and FilesPipeline, and follow that version.

In crawler/regcrawler/:
1. settings.py: ROBOTSTXT_OBEY=True; USER_AGENT "TVSCredit-RegMonitor/0.1 (+<contact from
   config/settings.yaml>)"; AUTOTHROTTLE_ENABLED=True; DOWNLOAD_DELAY=2;
   CONCURRENT_REQUESTS_PER_DOMAIN=1; DOWNLOAD_TIMEOUT=30; RETRY_TIMES=2 (5xx/timeouts only);
   HTTPCACHE only in a "dev" profile; logs to data/logs/.
2. items.py: ListingItem(source_id, run_id, channel RSS|HTML|XML, title, url, ref_no,
   displayed_date, raw_extra).
3. middlewares.py BlockDetectionMiddleware: 403/429, CAPTCHA/challenge markers (list in config),
   login redirects → source_check BLOCKED, stop further requests for that source. Never retry
   with altered headers, cookies or proxies.
4. pipelines.py: SnapshotPipeline (raw bytes → data/snapshots/{source}/{date}/{sha256}, snapshot
   row) → NormalisePipeline (canonical URL, ISO dates, ref_no regex) → DbPipeline (writes via
   app.repositories so DB access stays in one place).
5. Spider args: -a source=<id> -a run_id=<id>; source config loaded from the DB.

Acceptance: unit tests with saved fixture responses (no network) for block detection,
snapshot immutability and normalisation.

When finished: run .\tasks.ps1 test and .\tasks.ps1 lint, then STOP and wait for my
checkpoint review. Do NOT commit, merge or push yet.
```

**After the checkpoint is all ✅, paste:**
```
Commit all changes on feat/m3-scrapy-core with message "feat(crawler): scrapy settings, block detection, pipelines [M3]", push the branch to origin,
merge feat/m3-scrapy-core into main, push main to origin, then update docs/project-status.md
(mark Prompt 9 done with today's date and any open issues), commit that as
"docs(status): prompt 9 done [M3]" and push main.
```


### Prompt 10 — RSS spider and UNSC XML spider
```
[Repo: reg-crawler · Branch: feat/m3-rss-unsc · Milestone: M3]
First: switch to main, pull from origin, then create and switch to branch feat/m3-rss-unsc.
Follow CLAUDE.md, docs/architecture.md, docs/component-architecture.md,
docs/constraints-and-policies.md and docs/repository-etiquette.md.
We are on Windows: run project commands with .\tasks.ps1 <target>.

TASK:
1. Spider `rss`: fetch source.rss_url with Scrapy, parse with feedparser; emit ListingItem
   (channel RSS) with title, link, pubDate, guid; keep full description HTML as document text
   (RBI notifications carry the text there). Handle malformed XML, empty feed (ZERO_ITEMS),
   HTTP errors (FAILED).
2. Spider `unsc_xml`: download the consolidated XML, store snapshot, compare with the previous
   snapshot and emit ADDED / REMOVED / AMENDED entries (reference no., names, aliases) as items
   with priority URGENT.
3. For each, fetch ONE live sample politely, save to tests/fixtures/{rss,xml}/, write tests
   against fixtures. If a live fetch is blocked, stop for that source and record it in
   docs/source-status.md — no workarounds.

Acceptance: `uv run scrapy crawl rss -a source=rbi-notifications -a run_id=test` (from crawler\) works on fixtures
and live; UNSC diff tests cover added, removed and amended.

When finished: run .\tasks.ps1 test and .\tasks.ps1 lint, then STOP and wait for my
checkpoint review. Do NOT commit, merge or push yet.
```

**After the checkpoint is all ✅, paste:**
```
Commit all changes on feat/m3-rss-unsc with message "feat(crawler): RSS and UNSC XML spiders [M3]", push the branch to origin,
merge feat/m3-rss-unsc into main, push main to origin, then update docs/project-status.md
(mark Prompt 10 done with today's date and any open issues), commit that as
"docs(status): prompt 10 done [M3]" and push main.
```


### Prompt 11 — Config-driven HTML listing spider (wave 1)
```
[Repo: reg-crawler · Branch: feat/m3-listing · Milestone: M3]
First: switch to main, pull from origin, then create and switch to branch feat/m3-listing.
Follow CLAUDE.md, docs/architecture.md, docs/component-architecture.md,
docs/constraints-and-policies.md and docs/repository-etiquette.md.
We are on Windows: run project commands with .\tasks.ps1 <target>.

TASK:
Spider `listing`: reads per-source adapter_config (row/title/link/date selectors, date format,
ref_no regex, pagination depth = lookback, ministry/keyword filter). scrapy-playwright only if
requires_js=true, with no stealth options.

Configure and test wave 1, fetching ONE live sample per page into tests/fixtures/html/<source>/:
RBI Notifications HTML listing, RBI Press Releases HTML listing, TRAI press releases,
IRDAI circulars/guidelines, eGazette recent extraordinary gazettes (filter ministries:
Finance, MSME, Communications, Telecommunications, Electronics and IT, Labour and Employment).
Update config/sources.yaml selectors. Record outcomes in docs/source-status.md.

Wire POST /api/v1/sources/{id}/test to run this spider in dry-run mode (no DB writes) and
return parsed rows to the Sources page test card.

Acceptance: fixture tests pass for every wave-1 page; "Test this source" in the UI shows real
parsed rows.

When finished: run .\tasks.ps1 test and .\tasks.ps1 lint, then STOP and wait for my
checkpoint review. Do NOT commit, merge or push yet.
```

**After the checkpoint is all ✅, paste:**
```
Commit all changes on feat/m3-listing with message "feat(crawler): config-driven HTML listing spider wave 1 [M3]", push the branch to origin,
merge feat/m3-listing into main, push main to origin, then update docs/project-status.md
(mark Prompt 11 done with today's date and any open issues), commit that as
"docs(status): prompt 11 done [M3]" and push main.
```


---

## M4 — Detection and run control

### Prompt 12 — Detection, RSS-vs-HTML reconciliation, documents, relevance ⚙️
```
[Repo: reg-crawler · Branch: feat/m4-detection · Milestone: M4]
First: switch to main, pull from origin, then create and switch to branch feat/m4-detection.
Follow CLAUDE.md, docs/architecture.md, docs/component-architecture.md,
docs/constraints-and-policies.md and docs/repository-etiquette.md.
We are on Windows: run project commands with .\tasks.ps1 <target>.

TASK:
Implement app/services/detection.py and app/services/documents.py exactly per
docs/architecture.md §4 "Fingerprint and change rules" and FR-10 … FR-14, FR-16.

1. Fingerprints; change types NEW, MODIFIED, BACKDATED, REAPPEARED; item_observation per channel.
2. RSS-vs-HTML: same fingerprint → channel BOTH; else channel_mismatch=true.
3. Sanity checks: ZERO_ITEMS; >50% drop vs 7-run median → warning.
4. Documents: download linked PDF/HTML (via the crawler's polite settings), SHA-256 write-once
   storage, text extraction (PyMuPDF, pdfplumber fallback), NEEDS_OCR flag; blocked →
   NEEDS_MANUAL_FETCH.
5. Relevance: include/exclude keywords (per source + global settings) → score 0–1 and
   plain-English reason; ranking only, never hiding.
6. Pre-fill review fields by rules: title, link, authority, publish date, ref no.
7. FR-34 effective-date defaults (agent off): explicit date phrase in text → that date;
   "with immediate effect"/"effective immediately" → issue date; none → issue date, else publish
   date; UNSC → publish date. Store and show which rule was used.
8. FR-35 applicability hint: match "applicable to …" phrases against the Entity Profile
   (e.g. "system participants" while profile says not a system participant → suggest
   "Not applicable — not a system participant"); suggestion only.

Acceptance: fixture sequences (run 1 → run 2) cover every change type, a backdated insert, an
RSS-only item, zero items and a blocked document; Daily Review shows real detected items.

When finished: run .\tasks.ps1 test and .\tasks.ps1 lint, then STOP and wait for my
checkpoint review. Do NOT commit, merge or push yet.
```

**After the checkpoint is all ✅, paste:**
```
Commit all changes on feat/m4-detection with message "feat(detection): change detection, documents, relevance [M4]", push the branch to origin,
merge feat/m4-detection into main, push main to origin, then update docs/project-status.md
(mark Prompt 12 done with today's date and any open issues), commit that as
"docs(status): prompt 12 done [M4]" and push main.
```


### Prompt 13 — Crawl runner, Run now, scheduler, alerts
```
[Repo: reg-crawler · Branch: feat/m4-runner · Milestone: M4]
First: switch to main, pull from origin, then create and switch to branch feat/m4-runner.
Follow CLAUDE.md, docs/architecture.md, docs/component-architecture.md,
docs/constraints-and-policies.md and docs/repository-etiquette.md.
We are on Windows: run project commands with .\tasks.ps1 <target>.

TASK:
Implement app/services/crawl_runner.py and app/services/scheduler.py per architecture §3.1.

- Start run: create crawl_run, acquire lock (no overlapping runs), launch
  `scrapy crawl <spider> -a source=<id> -a run_id=<id>` as a SUBPROCESS per active approved
  source, sequentially per host; capture exit codes and logs into source_check; one failure never
  stops others; then detection + documents; close run COMPLETED or PARTIAL.
- POST /api/v1/crawl/run (all or one source, records who triggered) runs in the background;
  GET /runs/{id} gives live progress for the Source Health panel; enable the header Run now button.
- APScheduler (single worker) from settings: 10:00 and 19:30 Mon–Fri excluding holidays;
  URGENT sources hourly 08:00–20:00. Settings changes reload the schedule.
- After each run: UI banner for FAILED/BLOCKED/ZERO_ITEMS sources or URGENT items; alert rows
  stored for V2 Teams/email notifications.

Acceptance: Run now completes a full run (fixture mode in tests, live in dev); Source Health and
Daily Review update; a second Run now while running is refused politely.

When finished: run .\tasks.ps1 test and .\tasks.ps1 lint, then STOP and wait for my
checkpoint review. Do NOT commit, merge or push yet.
```

**After the checkpoint is all ✅, paste:**
```
Commit all changes on feat/m4-runner with message "feat(runner): crawl runner, run now, scheduler [M4]", push the branch to origin,
merge feat/m4-runner into main, push main to origin, then update docs/project-status.md
(mark Prompt 13 done with today's date and any open issues), commit that as
"docs(status): prompt 13 done [M4]" and push main.
```


### Prompt 13b — Luna NBFC Screening Agent (GPT Luna) ⚙️
```
[Repo: reg-crawler · Branch: feat/m4b-luna · Milestone: M4b]
First: switch to main, pull from origin, then create and switch to branch feat/m4b-luna.
Follow CLAUDE.md, docs/architecture.md, docs/component-architecture.md,
docs/constraints-and-policies.md and docs/repository-etiquette.md.
We are on Windows: run project commands with .\tasks.ps1 <target>.

TASK:
Read docs/agents/luna-nbfc-agent.md end to end, plus CLAUDE.md rule 11 and
docs/constraints-and-policies.md §3 (AI policy). Implement the agent exactly as specified.

1. app/services/llm.py: `LlmClient` protocol + implementations for Azure OpenAI and OpenAI
   (provider, model `gpt-6-luna`, endpoint, deployment, API version, key from .env), structured
   JSON output against a schema, one retry on invalid JSON, timeout, and per-call logging to
   `agent_run` (model, deployment, prompt_version, guidance_version, tokens, latency, cost, status).
   Add a `FakeLlmClient` for tests.
2. app/agents/luna/: agent.py (screen, extract, sweep), schemas.py (JSON schemas from the spec),
   prompts/ (system_v1.md, screen_v1.md, extract_v1.md, sweep_v1.md). Wrap all document/snapshot
   text in <untrusted_document> delimiters. No tools or function calling.
3. Evidence validator: every quote must appear verbatim (whitespace-normalised) in the input text,
   else the field is null and validated=false.
4. app/agents/orchestrator.py: after detection/documents, queue screen for new/modified items,
   extract for relevance >= MEDIUM, sweep once per snapshot; max 4 concurrent calls; daily budget
   cap; agent off/unavailable → items continue without suggestions. Disagreement flag when rule
   score and Luna differ by >= 2 levels (sort higher, never lower).
5. DB: Alembic migration for ai_extraction (append-only trigger), agent_flag, agent_run.
6. UI per spec §8: Luna chip + disagreement badge in Daily Review; suggestions with validated
   quotes and "Use suggestion" per field; "Ask Luna" button; possible-miss count in Source Health;
   Settings: screening guidance (versioned, needs approval), agent on/off, daily budget;
   agent step in Item History chain.
7. CLI `python -m app.cli agent-eval --golden tests/fixtures/agent/golden.jsonl` →
   docs/agent-eval-report.md with recall, precision, quote validity, field accuracy, cost.

Acceptance: all tests pass with FakeLlmClient (incl. fabricated-quote rejection and
prompt-injection text in a document being ignored); with AGENT_LUNA_ENABLED=false the whole app
works unchanged; with a real endpoint, an EOD run shows Luna suggestions on Daily Review.

When finished: run .\tasks.ps1 test and .\tasks.ps1 lint, then STOP and wait for my
checkpoint review. Do NOT commit, merge or push yet.
```

**After the checkpoint is all ✅, paste:**
```
Commit all changes on feat/m4b-luna with message "feat(agent): Luna NBFC screening agent [M4b]", push the branch to origin,
merge feat/m4b-luna into main, push main to origin, then update docs/project-status.md
(mark Prompt 13b done with today's date and any open issues), commit that as
"docs(status): prompt 13b done [M4b]" and push main.
```


---

## M5 — POC test

### Prompt 14 — Source probe (run on the TVS network)
```
[Repo: reg-crawler · Branch: feat/m5-probe · Milestone: M5]
First: switch to main, pull from origin, then create and switch to branch feat/m5-probe.
Follow CLAUDE.md, docs/architecture.md, docs/component-architecture.md,
docs/constraints-and-policies.md and docs/repository-etiquette.md.
We are on Windows: run project commands with .\tasks.ps1 <target>.

TASK:
Create `.\tasks.ps1 probe` (python -m app.cli probe): for every source (active or not) make ONE polite
request to the listing URL, the RSS URL if any, and one linked document. Write
docs/probe-report.md and .csv: source, channel, URL, HTTP status, response time, robots allowed,
blocked/captcha detected, items parsed, 3 sample titles, document downloadable, recommended
channel (RSS / HTML / JS-rendered / Email alert / Manual), notes. Never retry blocked pages.
Save responses as fixtures.

Acceptance: report generated; I will run it from the TVS office network and share results.

When finished: run .\tasks.ps1 test and .\tasks.ps1 lint, then STOP and wait for my
checkpoint review. Do NOT commit, merge or push yet.
```

**After the checkpoint is all ✅, paste:**
```
Commit all changes on feat/m5-probe with message "feat(cli): source probe [M5]", push the branch to origin,
merge feat/m5-probe into main, push main to origin, then update docs/project-status.md
(mark Prompt 14 done with today's date and any open issues), commit that as
"docs(status): prompt 14 done [M5]" and push main.
```


### Prompt 15 — Demo script, shadow comparison, POC readout ⚙️
```
[Repo: reg-crawler · Branch: feat/m5-demo · Milestone: M5]
First: switch to main, pull from origin, then create and switch to branch feat/m5-demo.
Follow CLAUDE.md, docs/architecture.md, docs/component-architecture.md,
docs/constraints-and-policies.md and docs/repository-etiquette.md.
We are on Windows: run project commands with .\tasks.ps1 <target>.

TASK:
Prepare the POC evaluation per docs/00-project-brief.md §5 and docs/01-PRD.md §10.

1. `.\tasks.ps1 demo`: fresh DB → import sources → approve as George → live EOD run → open UI.
   docs/demo-script.md: a 10-minute walkthrough of J1–J9 for Compliance, in plain language.
2. `python -m app.cli compare --manual <folder of .eml/.msg/.html daily emails> --from DD.MM.YYYY
   --to DD.MM.YYYY`: parse the manual email tables; match rows to detected items (ref no. →
   URL → fuzzy title); write docs/shadow-report.md: missed items (target 0), extra items (noise),
   field accuracy on matches, per-source health, RSS-vs-HTML mismatches.
3. docs/poc-readout.md: results vs success metrics, blocked sources and manual fallback, top 5
   fixes before pilot, V2 recommendations.
4. docs/user-guide.md: one page for Compliance users (NFR-03).

Acceptance: compare works on the sample (IoRS FAQs; PIB 2336151); demo runs end to end.

When finished: run .\tasks.ps1 test and .\tasks.ps1 lint, then STOP and wait for my
checkpoint review. Do NOT commit, merge or push yet.
```

**After the checkpoint is all ✅, paste:**
```
Commit all changes on feat/m5-demo with message "feat(cli): demo, shadow comparison, POC readout [M5]", push the branch to origin,
merge feat/m5-demo into main, push main to origin, then update docs/project-status.md
(mark Prompt 15 done with today's date and any open issues), commit that as
"docs(status): prompt 15 done [M5]" and push main.
```


---

## Checkpoint prompt (paste after every step)
```
Before I commit, review your own work against: (1) this prompt's acceptance criteria,
(2) CLAUDE.md non-negotiable rules, (3) docs/architecture.md (layering, file locations, data model,
API paths), (4) docs/ui-mockups for any UI work. Run .\tasks.ps1 test and .\tasks.ps1 lint.
Report as a checklist: ✅/❌ for each acceptance item, any deviation from the architecture and why,
any doc you updated, and exact steps for me to verify manually. Do not start the next step.
```

## Useful one-liners
- **Architecture guard:** "List every file you changed and confirm each is in the location given by docs/architecture.md §6."
- **Rule guard:** "Search the codebase for any retry-with-different-headers, proxy, user-agent rotation or CAPTCHA handling and remove it."
- **UI parity:** "Open docs/ui-mockups/0X-….html and the built page side by side; list every visual difference and fix them."
- **Doc sync:** "Update docs/architecture.md and docs/01-PRD.md for any behaviour that changed in this step."
