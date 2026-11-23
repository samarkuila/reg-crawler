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
| M4 | Detection & run control (Prompts 12–13) | 28.11 | ⚪ Not started | |
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

| 23.11 | D11 (exception) | Scaffold uses the existing venv + pip on Python 3.14 instead of uv + Python 3.12 (uv/make not installed); `tasks.ps1` mirrors Makefile. Revisit when uv is available. | D&A (user) |

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
| 23.11.2026 | Prompt 9 (branch feat/m3-scrapy-core): Scrapy settings (politeness, jitter off), BlockDetectionMiddleware (401/403/429, login redirect, challenge page → BLOCKED, source stopped, never retried), Snapshot → Normalise → Db pipelines, BaseRegSpider (-a source -a run_id, source from DB), app/services/normalise.py, new table raw_listing_item (migration 0003) with record_snapshot / add_raw_items / list_raw_items on both repositories. Merged to main. Open issues: raw_listing_item is an addition to architecture §4 (documented); a crawl needs an existing run, so Prompt 10's `-a run_id=test` needs a run created first; a page under 32 KB that mentions a challenge word is treated as BLOCKED (visible, never hides data); blocked pages are not snapshotted; crawler.contact is still the example.invalid placeholder and must be replaced before any live crawl; dev database needs `.	asks.ps1 migrate` for migration 0003 |
| 23.11.2026 | Prompt 10 (branch feat/m3-rss-unsc): `rss` spider (feedparser; full description HTML kept; ZERO_ITEMS / PARSE_ERROR / FAILED handled), `unsc_xml` spider (snapshot diff to ADDED / REMOVED / AMENDED items, URGENT, baseline on first run), offline `-a fixture=` mode, `app.cli new-run`, synthetic fixtures, docs/source-status.md. Merged to main. Open issues: **no live sample fetched yet** (crawler.contact is still the example.invalid placeholder; fixtures are synthetic, so the real RBI feed and UN file structure are unconfirmed; see docs/source-status.md); item priority URGENT lives in raw_extra, Detection must read it there; new dependency defusedxml; dev database needs `.	asks.ps1 migrate` and approved sources before a manual crawl |
| 23.11.2026 | Prompt 11 (branch feat/m3-listing): `listing` spider driven by adapter_config (selectors, date format, ref no. regex, max_pages lookback, ministry filter, never a search form, optional scrapy-playwright for requires_js), keywords recorded as hints and never used to drop rows, dry-run mode (no DB, no stored files), `POST /sources/{id}/test` runs it as a subprocess and returns parsed rows (proposed rules when a change is pending; SOURCE_TESTER=live|mock), tests on synthetic pages. Merged to main. Open issues: **wave-1 pages not fetched, so no real selectors and no per-source fixtures yet; config/sources.yaml selectors are still placeholders and "Test this source" cannot show real rows until they are set** (blocked on a real crawler.contact, still example.invalid; see docs/source-status.md); scrapy-playwright path untested (not installed); pagination needs a 'next' selector per source; dev database needs `.\tasks.ps1 migrate` |
