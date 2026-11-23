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
| M1 | UI on mock data (Prompts 1–6) | 24–25.11 | 🟡 In progress · Prompt 1 design system done (23.11) · Prompt 2 schemas, mock repository and API done (23.11) | Mockups approved. Open: /styleguide and the new header user switcher not yet visually checked in a browser against the mockups; official logo pending from Brand (placeholder in use) |
| M2 | Database (Prompts 7–8) | 26.11 | ⚪ Not started | |
| M3 | Crawler wave 1 (Prompts 9–11) | 26–27.11 | ⚪ Not started | Needs outbound internet |
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
