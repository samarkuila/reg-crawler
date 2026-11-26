# Product Requirements Document — Regulatory Monitor POC (V1)

**Version:** 0.1 · **Date:** 21.11.2026 · **Read with:** `00-project-brief.md`, `architecture.md`

---

## 1. Problem

Every working day, Compliance manually checks 13+ regulatory and government websites (RBI, TRAI, UIDAI, CERSAI, CKYC, NPCI, MeitY, eGazette, FIU-IND, NFRA, UNSC, MHA, IRDAI), decides which publications matter to TVS Credit (an NBFC), and emails a Daily Regulatory Update to the TVSCS Compliance and Home Credit teams. The process is slow, person-dependent, and a single miss is a regulatory risk. Regulators also publish late (after the 19:30 cut-off) and backdate documents.

## 2. Users and personas

| Persona | Who | Needs | Permissions |
|---|---|---|---|
| **Reviewer** | Divya (Compliance) | See everything new since last cycle, decide fast, produce the email | Review items, upload documents, prepare email |
| **Approver** | Senior Compliance (e.g. George) | Approve the email and source/config changes | Reviewer rights + approve email, approve config versions |
| **Admin** | D&A (Samarendra / Saranya) | Add/edit sources and selectors, test sources, run crawls | Manage sources/settings (changes still need approver) |
| **Viewer** | Compliance team members | Look up history and evidence | Read-only |
| **Recipients** | TVSCS Compliance, Home Credit team, SME function heads | Receive the daily email | No system access in V1 |

## 3. Scope (V1)

**In:** 5 sources (RBI, UNSC, TRAI, IRDAI, eGazette); RSS + HTML crawling with Scrapy; change detection; evidence storage; review UI; email preview/export; audit and traceability; schedule + manual run.
**Out:** see brief §2 "deferred".

---

## 4. Key concepts

| Term | Meaning |
|---|---|
| **Source** | One monitored page or feed, e.g. "RBI › Notifications (RSS)". |
| **Cycle** | One reporting window: MORNING (10:00 run) or EOD (19:30 run) for a date. |
| **Item** | One regulatory publication detected on a source. |
| **Fingerprint** | Hash of source + reference no. (or canonical URL). Decides whether an item is new. |
| **Change type** | NEW, MODIFIED, BACKDATED, REAPPEARED. |
| **Channel** | RSS, HTML, or BOTH (seen in both). |
| **Treatment** | Compliance's classification: Information only · Other function (SME) · Future/potential relevance · Implementation required · Not relevant. |
| **Status colour** | Email legend: Not applicable (grey), Action over (green), In progress (blue), Not as per plan (red), Yet to start (amber). |

### Item states
```
DETECTED ──► IN_REVIEW ──► DECIDED ──► PUBLISHED (included in an approved email)
    │                         ▲
    └──► NEEDS_MANUAL_FETCH ──┘ (after reviewer uploads the document)
```
Dismissed items are DECIDED with `treatment = NOT_RELEVANT` — never deleted.

---

## 5. User journeys

### J1 — Morning review (happy path)
1. 10:00 — the scheduled MORNING run starts automatically. Header shows "Crawl in progress (3/5 sources)".
2. Run ends. Divya opens **Daily Review**. Top strip: "5 of 5 sources checked ✓". Tiles: New 9, Modified 1, Backdated 0, Urgent 0, Needs manual fetch 0.
3. "Needs attention" list is sorted by relevance. First row: *"Inter-operable Regulatory Sandbox: IoRS – FAQs"*, RBI, badge **MODIFIED**, channel BOTH, relevance high ("mentions 'regulated entities'; FAQ update").
4. She clicks the row → right drawer opens with the form in email column order: Reference No. & Date, Authority, Guidelines, Applicability, Effective Date, Status, Impact on TVSCS, plus Treatment, SME function, Issue/Publish dates, *Include in today's update*.
5. Fields pre-filled by rules (title, link, authority, publish date, ref no. where parsed). She types the Impact text, sets Applicability = Yes, Status = amber, Treatment = Information only, ticks *Include*. Clicks **Accept**. Toast: "Saved". Row moves to Decided.
6. She expands **Probably not relevant** (collapsed): 6 rows such as "Money Market Operations", "Auction result". Selects all → **Bulk dismiss** → reason "Market operations – not NBFC". Done.
7. She opens **Email Preview**: one row, rendered exactly like today's email. Clicks **Approve** (as approver, or requests approval). Clicks **Download .eml**, opens in Outlook, adds recipients if needed, sends.
8. Audit log shows each action with her name and time.

### J2 — Item for another function (SME routing)
1. A FEMA-related RBI notification appears. Divya isn't sure it applies.
2. She sets Treatment = *Other function*, SME function = *Treasury* (from Settings routing table), Applicability = *TBC*, ticks *Include*.
3. Email preview shows the row with Applicability "TBC" and adds the Treasury head to **CC** in the exported draft.

### J3 — Blocked source / manual fetch
1. EOD run: eGazette listing returns a CAPTCHA page. Crawler marks the source **BLOCKED** (no retries with tricks).
2. Daily Review strip shows "4 of 5 sources checked — eGazette: blocked (verification page)" in red, first.
3. Divya visits eGazette herself, checks it, clicks **Mark checked manually** with note "No relevant gazette for 20.11.2026".
4. If an RBI PDF link was blocked, the item appears under **Needs manual fetch**; she downloads it in her browser and **uploads** it; the item proceeds to review with "manually supplied by Divya, 19:52".
5. The email cannot claim "no updates" while a failed source is unacknowledged; if acknowledged, the email states "eGazette checked manually".

### J4 — Backdated publication
1. On 20.11 EOD, RBI publishes a circular dated 19.11.
2. Detector: fingerprint never seen → item; displayed date older than the last successful run → badge **BACKDATED**.
3. Drawer shows Issue date 19.11.2026 and Publish date 20.11.2026, so the email reader sees it wasn't a miss.

### J5 — UNSC sanctions amendment (urgent)
1. UNSC consolidated list changes (one entry amended: new alias).
2. Item created with badge **URGENT** and a summary "AMENDED: QDe.xxx — alias added". It is pinned at the top of Daily Review and a banner appears.
3. Divya sets Treatment = *Implementation required*, SME = *AML/Operations* (negative list update), includes it.

### J6 — No-update day
1. EOD run: all sources OK; all new items dismissed.
2. Email Preview shows the no-update variant: "There were no relevant regulatory updates for 20.11.2026." Approve → export.

### J7 — RSS vs HTML mismatch
1. RBI notification appears in the RSS feed but not on the HTML listing (or vice versa).
2. Item shows channel RSS only with a **Channel mismatch** flag. It is still reviewable — the flag tells D&A to check the parser.

### J8 — Admin adds or edits a source
1. Samarendra opens **Sources** → **Add source**: authority, page name, URL, type (RSS / HTML list / Structured data), selectors, date format, keywords, priority.
2. Clicks **Test this source** → dry run shows parsed rows (title, link, date) so he can verify selectors.
3. Saves → version status **Pending approval**. The source is not crawled yet.
4. George (approver) opens the pending change, sees before/after, **Approves**. From the next run, it is crawled. All logged.

### J9 — Audit lookup
1. Internal audit asks: "When did you first see circular X and what did you do?"
2. Viewer searches **Item History** by title/ref no. → detail shows: first seen (run, time, channel), snapshot hash, document hash, review decision (who, when, edits), emails it appeared in, source configuration version at that time. Export as CSV/PDF print.

---

## 6. Functional requirements

### Sources & configuration
- **FR-01** Maintain a source register: authority, page name, URL(s), type (RSS, HTML_LIST, STRUCTURED_DATA), fetch engine (standard or browser, HTML_LIST only; M5), selectors, date format, include/exclude keywords, priority (NORMAL/URGENT), active.
- **FR-02** Any source/setting change creates a version in *Pending approval*; only an approver other than the editor can approve. Before/after diff visible.
- **FR-02b** Bulk add: upload a CSV/.xlsx (or paste up to 50 rows); every row is checked and shown as OK / Warning / Error before anything is saved; valid rows become pending versions that a different approver approves or rejects (singly or selected), each with its own audit event; failed rows download as errors.csv; "Test selected" dry-runs pending sources one at a time with the normal politeness rules.
- **FR-03** "Test this source" dry run shows parsed rows without saving items.
- **FR-04** Settings editable in UI: run times, working days, holidays, lookback days, SME routing, recipients, email template texts, dismiss reasons, relevance keywords.

### Crawling & detection
- **FR-05** Scheduled runs at configured times (default 10:00, 19:30, Mon–Fri excl. holidays); URGENT sources additionally hourly 08:00–20:00.
- **FR-06** Manual "Run now" (whole set or one source); no overlapping runs.
- **FR-07** RSS and HTML listings crawled with Scrapy; robots.txt obeyed; ≥2 s delay per host; identifying user agent.
- **FR-08** Blocked responses (CAPTCHA, 403/429, challenge, login redirect) → source status BLOCKED; no evasion; manual path offered.
- **FR-09** Every fetched listing saved as an immutable snapshot with SHA-256.
- **FR-10** New items decided by fingerprint, never by displayed date; change types NEW, MODIFIED, BACKDATED, REAPPEARED.
- **FR-11** Sources with both channels are reconciled; one-channel-only items flagged.
- **FR-12** Linked documents downloaded and stored with SHA-256; text extracted; blocked downloads → NEEDS_MANUAL_FETCH.
- **FR-13** Sanity checks: ZERO_ITEMS when a usually non-empty listing is empty; warning when item count drops >50% vs recent median.
- **FR-14** Lookback: each run re-reads listings for the previous N days (default 2).

### Review
- **FR-15** Daily Review shows source health first, then counters, then items sorted URGENT → relevance.
- **FR-16** Keyword relevance score with plain-English reason; low-relevance items collapsed, never hidden.
- **FR-17** Review form fields as per email columns plus treatment, SME function, issue/publish dates, include flag.
- **FR-18** Accept / Save / Dismiss (reason mandatory); bulk dismiss with one reason.
- **FR-19** Manual document upload for NEEDS_MANUAL_FETCH items, recorded as manually supplied.
- **FR-20** "Mark checked manually" for failed/blocked sources with note.
- **FR-21** System records which fields the reviewer changed versus pre-filled values.

### Email
- **FR-22** Render the daily update exactly in the current format (greeting, intro, 8-column table, disclaimer, colour legend, sign-off); texts editable in Settings.
- **FR-23** No-update variant; any failed source must be acknowledged and named.
- **FR-24** Reorder rows; approve (records approver); export `.eml` (unsent draft) and copy HTML; CC includes SME heads for OTHER_FUNCTION items.
- **FR-25** The system never sends email.

### Traceability & audit
- **FR-26** Every state change writes an append-only audit event (actor, time, action, before, after).
- **FR-27** Item History shows the full chain: email → decision → document → detection → snapshot → source version → run.
- **FR-28** Audit log filter and CSV export.

### Added in v0.3 (from the 20.11.2026 meeting — see `phase1-requirements-traceability.md`)
- **FR-29** Source register includes every site on Compliance's monitoring list, including **SLBC meetings** and **PIB**; sources not in the POC are present as *planned* (inactive).
- **FR-30** RBI is monitored on all tabs Compliance uses: Press releases, Notifications, Circulars, Master Directions, Master Circulars, FAQs, and RBI Home / What's New.
- **FR-31** Default exclusions (ranking only, never hiding): auction results, variable rate repo/reverse repo, money market operations, maintenance/downtime notices, exam results/marks, vacancies/recruitment, tenders/bid invitations, procurement notices, citizenship matters, insurer premium statistics. Editable in Settings.
- **FR-32** Export all captured items for a cycle (including low-relevance and dismissed) to Excel/CSV, so Compliance can mark applicability in Phase 1 and the marks feed Phase 2 filters.
- **FR-33** Email shows both **issue date** and **date published on the website** in the Reference No. & Date cell when they differ (e.g. "Date: 19.11.2026 · Published on website: 20.11.2026").
- **FR-34** Effective-date default rules apply even with the agent off: explicit date in the text → that date; "with immediate effect" → issue date; none stated → issue date (else publish date); UNSC/UN notices → publish date unless stated. The rule used is shown to the reviewer.
- **FR-35** Entity Profile setting (licences, roles and registrations TVS Credit holds or doesn't hold, e.g. NBFC: yes; system participant: no) used by rules and the agent to *suggest* applicability. Compliance owns and approves it.
- **FR-37** (added 23.11.2026) **Delete a source.** The Sources page has a Delete action. It proposes removal as a pending version that a different approver must approve (rule 9). After approval the source is *retired*: it is no longer crawled and leaves the register, but its runs, items, snapshots and audit events stay (rules 3 and 7). A new source may reuse the name; its id gets a `-2` suffix. Hard deletion is not offered because crawl history refers to the source.
- **FR-36** (added 23.11.2026) **Database admin page** (`/database`, admin role only): browse every table, search, edit and delete single rows (optionally with the rows that point to them, each audited; a write-once table that points to the row always blocks it), and reset the database. Write-once tables (snapshot, document, audit_event, ai_extraction) are browse only. Every edit, delete and reset writes an audit event. A reset first moves the old database and stored files to `data/backups/<timestamp>` and keeps users and default settings. SQL backend only; off when `APP_ENV=prod` unless `DB_ADMIN_ENABLED=true`.

**Scope note (R1):** Compliance expects the full monitoring list. The POC covers 5 sources (all RBI tabs, UNSC, TRAI, IRDAI, eGazette); the remaining sources follow in wave 2, starting with PIB and MHA (sanctions). This must be confirmed with Compliance (Decision D3).

---

## 7. Non-functional requirements

| ID | Area | Requirement |
|---|---|---|
| NFR-01 | Completeness | Zero silent failures: every run records a status per source. |
| NFR-02 | Latency | A full 5-source run completes in < 10 minutes; UI pages load < 2 s on POC data. |
| NFR-03 | Usability | Non-technical users complete J1 without training material beyond a 1-page guide; DD.MM.YYYY dates; plain language. |
| NFR-04 | Accessibility | WCAG AA contrast; keyboard navigation on Daily Review. |
| NFR-05 | Integrity | Snapshots, documents, audit events cannot be updated or deleted (DB triggers + write-once files). |
| NFR-06 | Security | POC runs inside TVS network; no secrets in code; `.env` for config; only public regulator data processed. |
| NFR-07 | Politeness & legality | robots.txt obeyed, rate-limited, no CAPTCHA/bot-check evasion. |
| NFR-08 | Portability | SQLite in POC; switching to Postgres is a configuration change. |
| NFR-09 | Branding | TVS Credit colours (Dark Blue #114984, Emerald Green #0E8943) and official logo supplied by Brand team. |

---

## 8. Screens

| Screen | Purpose |
|---|---|
| Daily Review | Main workspace: health strip, counters, attention list, drawer form, low-relevance section, manual-fetch section |
| Source Health | Per-run, per-source status history; failures and manual checks |
| Email Preview | Rendered draft, reorder, approve, export |
| Item History | Search and full traceability chain |
| Sources (Admin) | Register, add/edit, test, versions, approvals |
| Settings (Admin) | Run times, routing, recipients, templates, keywords, reasons |
| Audit Log | Read-only, filter, export |

## 9. Email specification (from the current manual email)

- **Subject:** `Daily Regulatory Update – DD.MM.YYYY (Morning|EOD)` (pattern editable)
- **Body:** "Dear All," → intro paragraph → table:
  `S.No | Reference No. and Date | Authority | Guidelines (title + link) | Applicability | Effective Date | Status (● colour) | Impact on TVSCS`
- **Footer:** italic disclaimer → colour legend (Not Applicable, Action over, Action in Progress, Progress not as per plan, Action yet to start) → "Regards," + sender name.
- **No-update:** "There were no relevant regulatory updates for DD.MM.YYYY." (+ any manually checked or unchecked sources).

## 10. Acceptance for V1 (POC exit criteria)
1. J1–J9 demonstrable end to end on live data for the 5 sources (blocked paths may use fixtures).
2. 5-working-day shadow run: 0 missed items vs manual emails.
3. Compliance confirms the email output matches their format.
4. POC readout delivered with V2 recommendations.
