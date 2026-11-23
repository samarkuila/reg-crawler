# Project Brief — Regulatory Monitor (Compliance Automation, Module 1)

**Owner:** Data & Analytics (TVS Credit) · **Business owner:** Compliance
**Status:** Planning · **Version:** 0.1 · **Last updated:** 21.11.2026

---

## 1. Goal definition

> *"What am I actually trying to do with this project?"*

| Possible goal | Applies? | Notes |
|---|---|---|
| Learning a new technology | Secondary | Scrapy and FastAPI are new to parts of the team; learning is a by-product, not the goal. |
| Validating an idea with customers | **Yes — primary** | The "customer" is the Compliance team. We must prove the system finds everything they find manually, in their format, with less effort. |
| Prototyping core functionality | **Yes — primary** | Crawl → detect → review → email draft, end to end, on real regulator websites. |
| Building a production-ready product | **No (not yet)** | Production hardening (SSO, Postgres, Outlook integration, monitoring) is V2. The POC is built so it can grow into production without a rewrite. |

**One-sentence goal**
Prove, within a 5-working-day shadow run, that an automated monitor can detect **every** regulatory update the Compliance team captures manually from the priority sources, present it for quick review, and produce the daily regulatory update email in the existing format — with full traceability to the source.

**Why it matters**
- A missed circular is a regulatory risk; manual monitoring of 13+ websites twice daily is slow and person-dependent.
- The regulatory item record created here becomes the foundation for Modules 2–8 (implementation tracker, comparison, synopsis, policy impact, obligations, CRA, testing).

**Guiding principles (from the Compliance process document)**
1. Automation may identify, extract, draft and suggest; Compliance decides applicability, treatment and final wording.
2. The regulatory document is the single source of truth.
3. Build and test module by module.
4. Every output traces back to the source document.
5. Simple for non-technical users; sources, criteria and templates editable without code.
6. No bypassing CAPTCHAs, bot checks or logins — ever.

---

## 2. Milestones

### Version 1 — POC / MVP (target: end of November 2026, then 1 week of testing)

The **absolute minimum** to prove the idea:

| # | Milestone | Definition of done |
|---|---|---|
| M0 | Specs & setup | This brief, PRD, tech-stack and architecture docs agreed; repo scaffolded with CLAUDE.md rules. |
| M1 | UI on mock data | FastAPI + HTML/CSS/JS in TVS Credit theme: Daily Review, Source Health, Email Preview, Item History, Sources, Settings, Audit Log — all working on mock data. |
| M2 | Database | SQLite via SQLAlchemy + Alembic; same API, now persistent; append-only audit log; real source register imported. |
| M3 | Crawler (wave 1) | Scrapy spiders for RSS + HTML listings + UNSC XML; 5 priority sources live: **RBI, UNSC, TRAI, IRDAI, eGazette**. |
| M4 | Detection & run control | Fingerprint-based change detection, RSS-vs-HTML cross-check, evidence storage, "Run now" + schedule (10:00, 19:30). |
| M5 | POC test | Source probe from TVS network; 5-working-day shadow run vs Divya's manual emails; POC readout. |

**MVP feature list (V1 only)**
- Source register (versioned, approval required for changes)
- Twice-daily + manual crawl of 5 sources (RSS and HTML)
- New / modified / backdated detection; RSS-vs-HTML mismatch flag
- Immutable snapshots and documents with SHA-256
- Source health with failed / blocked / zero-item alerts in the UI
- Review queue: accept / edit / dismiss (with reason), bulk dismiss, manual upload
- Keyword-based relevance ranking (nothing hidden)
- Email preview in the exact current format; approve; download `.eml` / copy HTML
- No-update email variant that names any unchecked sources
- Item history with full traceability chain; audit log with CSV export
- POC-only user switcher (viewer / reviewer / approver / admin)

### Version 2 — Pilot (December 2026 – January 2027)
- Remaining 8 sources (UIDAI, CERSAI, CKYC, NPCI, MeitY/PIB, FIU-IND, NFRA, MHA)
- Microsoft Entra ID single sign-on and real roles
- Outlook draft creation via Microsoft Graph, replying in the existing mail thread
- Ingest official regulator email alerts from a shared mailbox (second detection channel)
- Teams / email alerts for failed sources and URGENT items
- AI-drafted "Impact on TVSCS", effective date and reference no., each with evidence quotes
- PostgreSQL; deployment on TVS-approved server/cloud
- Phase-2 noise reduction: exclusion rules proposed from dismissal history, approved by Compliance

### Version 3 — Production + Module 2 (Q1 2027)
- Per-circular repository folder in OneDrive/SharePoint
- Module 2: PDF → paragraph-wise implementation tracker (verbatim text, action points, owners)
- Hardening: backups, monitoring dashboards, DR, performance, security review
- Foundations for Modules 3–8 (amendment comparison, synopsis, policy impact, obligations, CRA, testing)

### Explicitly deferred / out of scope for V1
Outlook integration, SSO, AI summaries (optional stretch only), Teams alerts, Postgres, sources beyond the first 5, any Module 2+ functionality, mobile UI.

---

## 3. The three most important questions to answer for a successful MVP

> *"What are the three most important questions I need to answer in order to build an MVP of this successfully?"*

### Q1. What is the ground truth we are measured against?
The MVP's only meaningful success metric is "zero missed items". That needs a reference set.
- **Need from Compliance:** the last 2–3 weeks of daily regulatory update emails **and**, if possible, Divya's list of items she looked at and *rejected* (so we can measure noise, not just misses).
- **Need to agree:** for the 5-day shadow run, Divya keeps her manual process; each day we compare. What counts as a "miss" (e.g. detected but in a later cycle — miss or not?).
- *Default if unanswered:* a miss = an item in the manual email not detected in the same or earlier cycle.

### Q2. Which sources can we legitimately and reliably reach from the TVS network?
Feasibility depends on site access, not code.
- **Need:** run the source probe (Prompt 13) from a TVS machine with outbound internet; confirm RSS feeds, HTML listings, and document downloads per source; identify blocked/CAPTCHA pages.
- **Need from IT/InfoSec:** approval for outbound access to government sites from the POC machine.
- *Default if unanswered:* blocked sources are handled via the manual-fetch queue and listed in every email; the POC proceeds with the sources that work.

### Q3. Where will it run, and who uses it during the POC?
- **Need from IT / D&A:** one machine (VM or desktop) that can run continuously at 10:00 and 19:30, with Python 3.12 and outbound internet; acceptance of SQLite for the POC.
- **Need from Compliance:** named users for the POC (reviewer, approver, admin) and confirmation that a POC-only user switcher (no SSO) is acceptable for the demo.
- *Default if unanswered:* runs on the D&A developer machine; demo with the user switcher.

---

## 4. Decisions log (defaults — change any before build starts)

| # | Decision | Default | Alternative |
|---|---|---|---|
| D1 | Database for POC | SQLite (Postgres-compatible code) | Postgres / SQL Server from day 1 |
| D2 | Crawl execution | One Scrapy subprocess per source, sequential | Parallel per host |
| D3 | POC sources | RBI, UNSC, TRAI, IRDAI, eGazette | Any other 5 |
| D4 | AI summaries | Out of core POC (optional stretch) | In scope for demo |
| D5 | Login | POC user switcher | Entra ID SSO |
| D6 | Email delivery | Download `.eml` / copy HTML; human sends | Outlook draft via Graph |
| D7 | Frontend | Plain HTML/CSS/JS served by FastAPI | HTMX / React |
| D8 | Run times | 10:00 and 19:30, Mon–Fri, TVS holidays excluded | Configurable in Settings |

---

## 5. Success metrics (V1)

| Metric | Target |
|---|---|
| Missed items during 5-day shadow run (priority sources) | **0** |
| Items traceable to stored source snapshot + hash | 100% |
| Field accuracy on matched rows (ref no., authority, link, publish date) | ≥ 98% |
| Reviewer time per cycle | ≤ 20 minutes |
| Source runs reported (no silent failure) | 100% of runs show per-source status |
| Email preview matches current format (Compliance sign-off) | Yes |

## 6. Stakeholders

| Role | Person / team | Responsibility |
|---|---|---|
| Business owner | Compliance (Ritika / George) | Scope, sign-off |
| Primary user & SME | Divya Gopakumar (Compliance) | Ground truth, review, feedback |
| Build | Samarendra Kuila, Saranya Dakshinamurthy (D&A) | Development, testing |
| Recipients of output | TVSCS Compliance team, Home Credit team, function heads (CC) | Consume daily update |
| Approvals | IT / InfoSec | Network access, hosting, later SSO & Graph |
