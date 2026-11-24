# Project Status — Regulatory Monitor (Module 1 POC)

**As of:** 23.11.2026 · **Overall:** 🟡 On track, with dependencies pending
**Target:** POC build complete 30.11.2026 · Shadow-run testing from 01.12.2026
**Update cadence:** Mon & Thu (owner: D&A lead)

---

## 1. Summary
Planning is complete (brief, PRD, tech stack, architecture, component architecture, system design, constraints & policies, repository etiquette, UI mockups, build prompts). Next step is environment setup and M1 (UI on mock data). New this week: **Luna NBFC Screening Agent (GPT Luna)** added as an optional, feature-flagged advisory component.

## 2. Milestones

| # | Milestone | Planned | Status | Notes |
|---|---|---|---|---|
| M0 | Specs & setup | 21–23.11 | 🟢 Docs done · 🟢 Prompt 0 scaffold (23.11) | Runs on existing venv + pip (Python 3.14); uv/GNU make not installed — see decision 23.11 D11 |
| M1 | UI on mock data (Prompts 1–6) | 24–25.11 | 🟡 In progress · Prompt 1 design system done (23.11) · Prompt 2 schemas, mock repository and API done (23.11) · Prompt 3 Daily review page done (23.11) · Prompt 4 Email preview page and renderer done (23.11) · Prompt 5 Source health and Item history pages done (23.11) · Prompt 6 Sources, Settings and Audit log pages done (23.11) | Mockups approved. Open: /styleguide, the header user switcher and the Daily review, Email preview, Source health, Item history, Sources, Settings and Audit log pages (incl. J/K/Enter/A/D/Esc keys, drag-to-reorder, Copy HTML) not yet visually checked in a browser against the mockups; official logo pending from Brand (placeholder in use) |
| M2 | Database (Prompts 7–8) | 26.11 | 🟢 Done · Prompt 7 models, migrations and immutability triggers (23.11) · Prompt 8 SQL repository, contract tests, source register and import tools (23.11) | Open: PostgreSQL path untested on a server; monitoring Excel not yet received, so the Excel importer is tested only on a made-up layout; selectors in config/sources.yaml are placeholders until Prompt 11; listing URLs marked 'confirm in probe' |
| M3 | Crawler wave 1 (Prompts 9–11) | 26–27.11 | 🟡 In progress · Prompt 9 Scrapy foundation done (23.11) · Prompt 10 RSS and UNSC spiders done, live samples pending (23.11) · Prompt 11 listing spider and dry-run source test done, live samples and real selectors pending (23.11) | Needs outbound internet. Open: see the Prompt 9 row in the change history |
| M4 | Detection & run control (Prompts 12–13) | 28.11 | 🟡 In progress · Prompt 12 detection, documents, relevance and pre-fill rules done (23.11) · Prompt 13 crawl runner, Run now, scheduler and alerts done (23.11) | Open: see the Prompt 12 row in the change history |
| M4b | Luna agent (Prompt 13b) | 28–29.11 | ⚪ Not started | Needs approved LLM endpoint + golden set |
| M5 | Probe, shadow run, readout (Prompts 14–15) | 30.11 → 05.12 | ⚪ Not started | Needs Divya's past emails |

Legend: 🟢 done/on track · 🟡 at risk / in progress · 🔴 blocked · ⚪ not started

## 3. Deliverables

| Deliverable | Status |
|---|---|
| `00-project-brief.md`, `01-PRD.md`, `02-tech-stack.md`, `architecture.md` | 🟢 v0.1 |
| `component-architecture.md`, `system-design.md` | 🟢 v0.2 (adds Luna) |
| `constraints-and-policies.md`, `repository-etiquette.md` | 🟢 v0.1–0.2 |
| `agents/luna-nbfc-agent.md` | 🟢 v0.1 proposed |
| UI mockups (5 screens) | 🟢 Approved |
| `CLAUDE.md`, `BUILD-PROMPTS.md` | 🟢 Ready (13b added for Luna) |
| Codebase | ⚪ Not started |

## 4. Dependencies & asks

| # | Ask | From | Needed by | Status |
|---|---|---|---|---|
| 1 | Claude Code access (plan/account) and install permission | IT | 24.11 | 🟡 Requested? |
| 2 | Outbound internet to regulator sites from POC machine | IT/InfoSec | 26.11 | 🟡 |
| 3 | Approved GPT Luna endpoint (Azure OpenAI / Foundry deployment) + key | IT/InfoSec | 28.11 | 🟡 |
| 4 | Last 2–3 weeks of daily update emails (.eml/.msg) + rejected items | Compliance (Divya) | 27.11 | 🟡 |
| 5 | 50+ labelled items for Luna golden set | Compliance (Divya) | 28.11 | 🟡 |
| 6 | Named POC users (reviewer, approver, admin) | Compliance | 25.11 | 🟡 |
| 7 | Official TVS Credit logo files | Brand/Marketing | 29.11 | 🟡 |
| 8 | Legal view on UN consolidated list usage | Legal | 05.12 | ⚪ |
| 9 | Latest **website monitoring Excel** (sites, links, tabs, keywords, "documents to fetch" column) | Compliance (Divya) | 25.11 | 🔴 Not received |
| 10 | **Entity Profile**: licences/roles TVS Credit holds or doesn't (e.g. not a system participant) | Compliance (Divya) | 27.11 | 🟡 |
| 11 | Confirm Phase 1 scope (D3) and email date wording (D10) | Compliance (Divya/George) | 25.11 | 🟡 |

## 5. Risks

| Risk | Impact | Likelihood | Mitigation | Owner |
|---|---|---|---|---|
| IT approvals delayed | Build/test slip | Medium | Build on mock data + fixtures first; request today | D&A lead |
| Sites block automated access | Coverage gaps | Medium | Official RSS/XML first; manual path; probe early | Samarendra |
| No ground-truth emails | Can't prove "zero missed" | Medium | Ask now; fall back to daily side-by-side with Divya | Saranya |
| LLM endpoint not approved in time | No Luna in demo | Medium | Feature flag; POC passes without it | D&A lead |
| Luna misses relevant items | Trust loss | Low–Med | Advisory only; never hides; recall ≥ 98% eval gate | D&A + Divya |
| Tight timeline (6 working days) | Partial scope | High | Strict prompt order; sources in waves; defer Settings polish | Team |

## 6. Decisions & exceptions log

| Date | ID | Decision | By |
|---|---|---|---|
| 21.11 | D1 | SQLite for POC, Postgres-compatible | D&A |
| 21.11 | D3 | POC sources: RBI, UNSC, TRAI, IRDAI, eGazette | Compliance + D&A |
| 21.11 | D5–D6 | POC user switcher; `.eml` export, human sends | Compliance + D&A |
| 21.11 | D7 | Vanilla HTML/CSS/JS + FastAPI; Scrapy for crawling | D&A |
| 23.11 | D4 (changed) | AI moved **into POC** as **Luna NBFC Screening Agent (GPT Luna)** — feature-flagged, advisory only, eval-gated | Requested by D&A; Compliance to confirm |
| 23.11 | D9 | LLM behind `LlmClient`; provider via IT (Azure OpenAI preferred) | D&A |

| 23.11 | D3 (to confirm) | POC = 5 sources incl. **all RBI tabs** (Press releases, Notifications, Circulars, Master Directions, Master Circulars, FAQs, What's New); wave 2 starts with PIB and MHA; SLBC added as planned | D&A → Compliance to confirm |
| 23.11 | D10 (to confirm) | Email shows issue date and website publish date when different | D&A → Compliance to confirm |
| 23.11 | — | Meeting traceability check: 18 covered, 9 partial, 4 missing → fixed in pack v0.3 (`phase1-requirements-traceability.md`) | D&A |

| 24.11 | D12 (to confirm) | Items stored by a source's first crawl (origin BASELINE, the current-month window) are kept out of the Daily review queue by default; on 24.11 the setting was switched to `true` so the first crawl's items show in the Daily review (`crawler.first_run.baseline_items_in_review`); they stay in Item history and behind `?origin=BASELINE`. Numbered D12 because D11 is already the scaffold exception | D&A → Compliance to confirm |
| 23.11 | D11 (exception) | Scaffold uses the existing venv + pip on Python 3.14 instead of uv + Python 3.12 (uv/make not installed); `tasks.ps1` mirrors Makefile. Revisit when uv is available. | D&A (user) |
| 23.11 | D-DBADMIN | Admin Database page: admin edits/deletes rows and resets the DB directly (bypasses rule 9 maker-checker for data); write-once tables stay read only; every change audited; SQL backend only; off in prod unless DB_ADMIN_ENABLED=true. Needs Compliance/IT sign-off before use outside the POC | Owner request |

Exceptions: D11 deviates from the "uv, Python 3.12" stack line in CLAUDE.md.

## 7. This week's plan (24–29.11)
- Mon–Tue: env setup, Prompt 0, M1 (Prompts 1–6), demo screens to Divya.
- Wed: M2 (Prompts 7–8); M3 start (probe RBI/UNSC early).
- Thu: M3 finish; M4 (Prompt 12).
- Fri: Prompt 13 (runner/scheduler); Prompt 13b (Luna) if endpoint approved.
- Sat: Prompt 14 probe from TVS network; prep shadow run.

## 8. Metrics (fill during shadow run)

| Metric | Target | Day 1 | Day 2 | Day 3 | Day 4 | Day 5 |
|---|---|---|---|---|---|---|
| Missed items vs manual | 0 | | | | | |
| Sources checked / total | 5/5 | | | | | |
| Reviewer time per cycle (min) | ≤ 20 | | | | | |
| Luna recall on relevant items | ≥ 98% | | | | | |
| Luna fields accepted without edit | track | | | | | |

## 9. Change history
| Date | Change |
|---|---|
| 21.11.2026 | Planning pack v0.1 |
| 23.11.2026 | Added component architecture, system design, constraints & policies, repo etiquette, project status; Luna agent spec; Prompt 13b |
| 23.11.2026 | Prompt 1 done: design system (tokens, components, Jost, base layout, ui.js/api.js, /styleguide); tests and lint green |
| 23.11.2026 | Prompt 2 (branch feat/m1-api-mock): Pydantic schemas, Repository protocol + in-memory implementation with seed data, services, every architecture §5 endpoint under /api/v1, page shells, POC user switcher. Merged to main. Open issues: pages not checked in a browser; seed counts differ slightly from mockup tiles; architecture additions (is_draft, prepared_by, settings approval endpoints) need D&A sign-off before Prompt 7 models |
| 23.11.2026 | Prompt 4 (branch feat/m1-email-preview): email renderer and Email preview page (FR-22…FR-25), FR-33 date wording, FR-32 cycle export, snapshot tests. Merged to main. Open issues: page not checked in a browser or in Outlook desktop; FR-32 export is CSV (UTF-8 BOM), not .xlsx, as openpyxl is not in the stack; Download .eml is enabled only after approval; D10 date wording still to be confirmed with Compliance |
| 23.11.2026 | Prompt 5 (branch feat/m1-health-history): Source health page (grid of last four cycles, run progress with 3 s polling, compare, log, manual check) and Item history page (search, six-step evidence chain, CSV export, FR-27); stored snapshot/document copies served as text after a SHA-256 re-check. Merged to main. Open issues: pages not checked in a browser; on untouched seed data IoRS steps 1–2 (email, decision) show as not yet because the seeded accepted item is PIB; Run now / Run one source return 501 until Prompt 13; no before/after content stored, so no Show differences link; the 3 s polling is untested against a live run |
| 23.11.2026 | Prompt 6 (branch feat/m1-admin): Sources page (pending card with before/after, approve/reject/test, register, add/edit drawer), Settings page (six tabs, every save a pending version), Audit log page (filters, readable before/after, CSV). SME routing, recipients and email template now stored as versioned settings. Merged to main. Open issues: pages not checked in a browser; SME/recipients/template as settings needs D&A sign-off before Prompt 7 models; Test this source returns stub data until Prompt 13; one pending change per source or setting at a time (rule to confirm with Compliance) |
| 23.11.2026 | Prompt 7 (branch feat/m2-db): app/db models for every architecture §4 table, session, Alembic migration 0001, append-only triggers on snapshot/document/audit_event (SQLite; PostgreSQL SQL in the migration and docs/postgres-migration.md), internal_ref counter, indexes, docs/data-model.md with generated ER diagram. Merged to main. Open issues: PostgreSQL path untested on a server; sme_function/recipient/email_template tables unused until the Prompt 8 decision; no ORM relationships yet; app still runs on the in-memory backend until Prompt 8 |
| 23.11.2026 | Prompt 8 (branch feat/m2-sql-repo): SQL repository (REPO_BACKEND=sql), contract tests run on memory and SQL, the whole API suite runs on both backends, seed-demo, config/sources.yaml (11 active + 10 planned sources), import-sources and import-monitoring-excel (pending versions only, safe to re-run), default keywords, screening guidance and Entity Profile placeholders, migration 0002 (source.position). Merged to main. Open issues: all 21 imported sources (including 10 planned) arrive as pending approval, so a first import creates many approvals; on the demo database 8 planned ids already exist and get a pending change instead; SME routing, recipients and email template stay in `setting` (the three config tables are unused); PostgreSQL untested; two writers adding a version to the same source at once could hit the unique rule (rare, error not data loss) |
| 23.11.2026 | Prompt 9 (branch feat/m3-scrapy-core): Scrapy settings (politeness, jitter off), BlockDetectionMiddleware (401/403/429, login redirect, challenge page → BLOCKED, source stopped, never retried), Snapshot → Normalise → Db pipelines, BaseRegSpider (-a source -a run_id, source from DB), app/services/normalise.py, new table raw_listing_item (migration 0003) with record_snapshot / add_raw_items / list_raw_items on both repositories. Merged to main. Open issues: raw_listing_item is an addition to architecture §4 (documented); a crawl needs an existing run, so Prompt 10's `-a run_id=test` needs a run created first; a page under 32 KB that mentions a challenge word is treated as BLOCKED (visible, never hides data); blocked pages are not snapshotted; crawler.contact is still the example.invalid placeholder and must be replaced before any live crawl; dev database needs `.\tasks.ps1 migrate` for migration 0003 |
| 23.11.2026 | Prompt 10 (branch feat/m3-rss-unsc): `rss` spider (feedparser; full description HTML kept; ZERO_ITEMS / PARSE_ERROR / FAILED handled), `unsc_xml` spider (snapshot diff to ADDED / REMOVED / AMENDED items, URGENT, baseline on first run), offline `-a fixture=` mode, `app.cli new-run`, synthetic fixtures, docs/source-status.md. Merged to main. Open issues: **no live sample fetched yet** (crawler.contact is still the example.invalid placeholder; fixtures are synthetic, so the real RBI feed and UN file structure are unconfirmed; see docs/source-status.md); item priority URGENT lives in raw_extra, Detection must read it there; new dependency defusedxml; dev database needs `.\tasks.ps1 migrate` and approved sources before a manual crawl |
| 23.11.2026 | Prompt 11 (branch feat/m3-listing): `listing` spider driven by adapter_config (selectors, date format, ref no. regex, max_pages lookback, ministry filter, never a search form, optional scrapy-playwright for requires_js), keywords recorded as hints and never used to drop rows, dry-run mode (no DB, no stored files), `POST /sources/{id}/test` runs it as a subprocess and returns parsed rows (proposed rules when a change is pending; SOURCE_TESTER=live|mock), tests on synthetic pages. Merged to main. Open issues: **wave-1 pages not fetched, so no real selectors and no per-source fixtures yet; config/sources.yaml selectors are still placeholders and "Test this source" cannot show real rows until they are set** (blocked on a real crawler.contact, still example.invalid; see docs/source-status.md); scrapy-playwright path untested (not installed); pagination needs a 'next' selector per source; dev database needs `.\tasks.ps1 migrate` |
| 23.11.2026 | Prompt 12 (branch feat/m4-detection): app/services/detection.py (fingerprints, NEW / MODIFIED / BACKDATED / REAPPEARED, per-channel observations, RSS-vs-HTML reconciliation, ZERO_ITEMS and >50% drop warnings), documents.py plus a `docs` Scrapy spider (polite download, write-once SHA-256 storage, text extraction with PyMuPDF then pdfplumber, needs_ocr, blocked becomes NEEDS_MANUAL_FETCH), relevance.py (keyword score 0-100 with a plain-English reason, ranking only), prefill_rules.py (FR-34 effective date, FR-35 applicability hint) shown in the review drawer, `app.cli detect`, migration 0004 (document.needs_ocr), new repository methods. Merged to main. Open issues: no live crawl yet, so detection and the `docs` downloader are tested on fixtures and a fake fetcher only (the real download subprocess has no network test); a MODIFIED item that was already decided returns to DETECTED (confirm this rule with Compliance); the effective date and its rule are derived on read, not stored; the drop warning needs 3 earlier runs (value chosen, not in the spec); the Entity Profile is still the placeholder, so applicability hints say 'To be confirmed' until Compliance completes it; OCR is not done (scans are flagged only); nothing calls detection automatically until the Crawl runner (Prompt 13); dev database needs `.\tasks.ps1 migrate` for migrations 0003 and 0004 |
| 23.11.2026 | Prompt 13 (branch feat/m4-runner): app/services/crawl_runner.py (run per active source as a Scrapy subprocess, one at a time, 5-minute timeout, per-run logs, FAILED check with exit code or log tail when the crawler leaves none, one failure never stops the rest, then detection and documents, run always closed COMPLETED or PARTIAL), exclusive lock file so runs never overlap (second Run now gets a polite 409; stale lock taken over), POST /crawl/run returns 202 and runs in the background, header Run now works on every page, scheduler.py (APScheduler: 10:00 and 19:30 on working days excluding holidays, hourly URGENT 08:00-20:00, reload when a schedule setting is approved, catch-up run after a missed slot), alert table (migration 0005) with a banner and GET /alerts, crawl_run.sources so one-source progress is right. Merged to main. Open issues: the scheduler is off unless SCHEDULER_ENABLED=true and needs a single uvicorn worker (the lock and thread are per process); Run now needs REPO_BACKEND=sql (the demo data refuses with a message); the whole run is sequential across all hosts, not parallel by host; hourly urgent runs follow the same working days and holidays as the others (confirm with Compliance, sanctions can change on any day); catch-up covers only today's latest missed slot and starts 15 minutes after it; alerts are stored but nothing sends them until V2 (delivered_at stays empty); no live crawl has been run (real sites untouched, crawler.contact is still the example.invalid placeholder, listing selectors are still placeholders so listing sources report PARSE_ERROR); the banner and Run now button were not checked in a browser, only through the API and tests; dev database needs `.\tasks.ps1 migrate` for migration 0005 |
| 24.11.2026 | Sources page: Bulk add (branch feat/m3-lookback). Upload/paste, row-by-row preview, pending versions, batch + per-source audit, select-by-uploader with Approve/Reject selected, polite "Test selected" (host gate + circuit breaker). Open issues: priority High/Low from the request does not exist in the system (High = Urgent with a warning, Low = error; confirm with Compliance); the allow-list is a new `crawler.allowed_hosts` setting in settings.yaml (not versioned in the UI); the circuit breaker is in memory and resets on restart; UI checked only through API tests and `node --check`, not in a browser |
| 24.11.2026 | Live crawl status: `GET /crawl/status`, a status strip on every page (progress bar, step, page being read), Run now shows "Crawling…", Daily review reloads when a crawl ends. First crawl of a source reads 7 days, later crawls the `lookback-days` setting; scheduler enabled in .env. Open issues: the page being read is known only when the crawl runs in the same process as the page (a crawl started elsewhere shows progress without the page name); not checked in a browser |
| 24.11.2026 | M4 first-run window (branch feat/m4-first-run-window): a source's first healthy crawl stores only the current-month window (settings.yaml `crawler.first_run`, per-source overrides), older rows become `seen_fingerprint`; later crawls detect NEW / BACKDATED / MODIFIED (title, date, attachments) / REAPPEARED; run counters on `source_check.stats`; page-stop and 50-requests-per-host cap; baseline items out of the review queue by default (D12); rebaseline task and approver endpoint; migration 0008. Open issues: **TODO backfill of older months is not built** (only the current-month window is stored; older items are fingerprints only); the listing spider reads attachments only if a source sets `selectors.attachments` (not on the Sources form yet); raw rows of the whole page are still stored in `raw_listing_item` and each fetched page is still snapshotted (the per-item rule "no snapshot" is met, the page bytes are not); sources already in a live database start NOT_BASELINED, so their next crawl applies the window to unseen rows only (existing items are untouched) |
| 24.11.2026 | UI and run checks (branch feat/m4-first-run-window-ui): Sources bulk add (CSV/xlsx/paste, preview, maker-checker), live crawl status, plain-language errors and compact alert summary, baseline note and `?origin=` filter on Daily review, audit log paging, Admin database page, robots.txt pre-check (a source its robots.txt refuses is recorded BLOCKED and not requested), `baseline_items_in_review` now `true`. Open issues: scheduled MORNING/EOD runs not yet seen (server must stay running); 12 of 16 sources did not baseline on the first try; RBI/FIU/eGazette date selectors still to set; Admin user-management tab not built; `tests/unit/test_source_import.py` fails (sources.yaml commented out) |
