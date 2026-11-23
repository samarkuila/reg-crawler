# Phase 1 Requirements Traceability — Meeting of 20.11.2026 vs Design Pack

**Date:** 23.11.2026 · **Source:** Meeting "Understand the Compliance Automation Process Flow" (Divya — Compliance; Samarendra, Saranya — D&A), 33 min.
**Purpose:** confirm every Phase 1 expectation from Compliance is covered by the PRD, architecture and build prompts.

Legend: ✅ covered · 🟡 partly covered (fixed in v0.3 unless stated) · ❌ missing (fixed in v0.3 unless stated)

---

## 1. Traceability matrix

| # | What Compliance said (paraphrased) | Status | Where covered / fix |
|---|---|---|---|
| R1 | Monitor the regulatory websites on Divya's monitoring list daily | 🟡 | POC covers 5 sources, V2 the rest. **Divya and George expect the full list.** Scope must be explicitly agreed (Decision D3). |
| R2 | List includes RBI, TRAI, UIDAI, CERSAI, CKYC, NPCI, MeitY, eGazette, FIU, NFRA, UNSC, MHA, IRDAI **and SLBC meetings, PIB** | ❌ → fixed | SLBC and PIB were missing from the source register. Added as planned sources (PIB in wave 2; SLBC needs probe). FR-29. |
| R3 | RBI tabs to crawl: **Press releases, Notifications, Circulars, Master Directions, Master Circulars, FAQs** (and RBI Home / What's New per George's email) | ❌ → fixed | Prompt 8 configured only Notifications and Press Releases. The IoRS FAQ example (our own acceptance test) came from **FAQs** and would have been missed. Added all RBI tabs to wave 1. FR-30. |
| R4 | Check today **and the previous day**; regulators update after the 19:30 cut-off | ✅ | FR-14 lookback (2 days), FR-10 BACKDATED, J4 |
| R5 | Runs at 10:00 and 19:30 on working days, detect changes | ✅ | FR-05, FR-06, scheduler |
| R6 | Capture only NBFC-related compliance; anything mentioning NBFC applies to TVS Credit | ✅ | Relevance rules (FR-16), Luna screening, include keyword "NBFC" |
| R7 | Exclude: auction results, variable repo rate, maintenance notices, exam marks, vacancies, tender/bid invitations, citizenship amendments, procurement items (e.g. diesel generators), non-life premium data | 🟡 → fixed | Only a few exclusions were seeded. Full list added to default exclude keywords and Luna screening guidance. FR-31. |
| R8 | **Phase 1: give us everything** in the relevant tabs; Compliance will mark what's not applicable, then filter in Phase 2 | 🟡 → fixed | Nothing is hidden (✅), dismissal reasons feed Phase 2 (✅). Missing: an **export of all captured items per cycle** (Excel/CSV) so Divya can mark them outside the app if she prefers. FR-32. |
| R9 | Divya's Excel: sites, links, tabs/keywords per site; she will add "which documents to fetch under each tab" | ❌ → fixed | The Excel was never listed as a dependency or imported. Added: dependency #9, and import of the Excel into the source register (Prompt 8). |
| R10 | Report in the existing template; circulated to TVSCS Compliance and Home Credit teams | ✅ | FR-22–FR-24, email spec PRD §9 |
| R11 | Template columns: S.No, Reference No. & Date, Authority, Guidelines (clickable link), Applicability, Effective Date, Status (colour legend), Impact (simple terms) | ✅ | FR-17, FR-22 |
| R12 | **Two dates: date of issuance and date of publishing on the website**, so readers see a backdated item isn't a miss | 🟡 → fixed | Captured in the review form, but **not shown in the email**. Added to the email's Reference No. & Date cell ("Issued … · Published on website …") when they differ. FR-33 (confirm wording with Divya). |
| R13 | Effective date rules: explicit date in the document; "effective immediately" = issue date; none stated = issue/publish date; informational items (e.g. cancellation of registration) → issue date; UNSC = publish date unless stated | 🟡 → fixed | Luna had the rules; the **rules-only path (agent off) did not**. Default rule added to pre-fill, with the rule shown to the reviewer. FR-34. |
| R14 | Applicability: the document states which participants it applies to (e.g. "system participants") — TVS Credit doesn't hold that licence, so mark not applicable but still capture | ❌ → fixed | Added an **Entity Profile** setting (TVS Credit's licences/roles) used by rules and Luna to *suggest* applicability. FR-35. Compliance must fill it. |
| R15 | Impact in simple terms — "use AI, Copilot can help" | ✅ | Luna agent (GPT Luna via Azure/Foundry fits the Microsoft/Copilot environment) — advisory, evidence-bound |
| R16 | Identifier per document (press release number, PIB release ID, etc.) in column B with the date | ✅ | ref_no extraction (regex per source + Luna) |
| R17 | FEMA → Treasury; GST → Finance; Labour → HR/Admin; put function head in CC for SME confirmation | ✅ | SME routing, J2, FR-24 |
| R18 | UNSC: very important; capture entity/individual names and **all aliases**; act immediately on the negative list | ✅ | `unsc_xml` diff (names, aliases), URGENT priority, hourly check, J5 |
| R19 | Sanctions updates also come from **RBI and MHA** | 🟡 | RBI covered; MHA is V2. Recommend MHA sanctions/UAPA pages as **first source of wave 2**. |
| R20 | eGazette: search by ministry — Communications, Communications & IT, Finance, Electronics & IT, MSME, Labour & Employment, Department of Telecommunications (more to be added) | 🟡 → fixed | Ministry filter configurable ✅. **Weekly gazettes are multi-ministry** and would be dropped by a ministry filter — now always kept as candidates. |
| R21 | TRAI: regulation, direction, recommendation, consultation paper; ignore vacancies/bids under What's New | 🟡 → fixed | Include/exclude keywords added for TRAI. |
| R22 | CKYC/CERSAI: notifications & announcements; ignore maintenance and internal notices | ✅ (V2) | Exclusions seeded for when the source is enabled |
| R23 | MHA: Orders & Circulars; Acts, Rules & Notifications — only finance-sector-relevant | ✅ (V2) | Source planned; exclusions seeded |
| R24 | IRDAI: ignore non-life premium statistics; capture directions/requirements | ✅ | Exclusions + relevance |
| R25 | NPCI is hard to access even manually | ✅ | Best-effort, BLOCKED → manual path; V2 |
| R26 | RBI PDFs blocked by bot verification | ✅ | No bypass; RSS carries text; manual fetch queue (W1, FR-08, FR-12) |
| R27 | Human in the loop decides what is/isn't NBFC-related | ✅ | Review gate (H1), Luna advisory only |
| R28 | "No relevant updates for the date" standard statement | ✅ | FR-23 |
| R29 | Relevance test: "anything with an economic, social, operational or administrative impact on TVS Credit" | ✅ → strengthened | Added verbatim-in-spirit to the default screening guidance |

**Result:** 18 ✅ · 9 🟡 · 4 ❌ — all ❌ and most 🟡 fixed in pack v0.3. Two items need a **decision**, not a code change: R1 (scope) and R12 (email wording).

---

## 2. Changes made in v0.3

1. **PRD:** new FR-29 … FR-35; R1 scope note.
2. **BUILD-PROMPTS Prompt 8:** full RBI tab list; SLBC and PIB added; exclusion/inclusion keyword seeds; weekly-gazette rule; import of Divya's monitoring Excel.
3. **Prompt 4 (email):** issue vs publish date shown when different.
4. **Prompt 12 (detection):** rules-only effective-date defaults; entity-profile applicability hint.
5. **Project status:** dependency #9 (monitoring Excel), #10 (entity profile), decision items D3 and D10.

## 3. Decisions needed from Compliance

| # | Question | Recommendation |
|---|---|---|
| D3 | Phase 1 scope: 5 sources (all RBI tabs + UNSC + TRAI + IRDAI + eGazette) for the POC, the rest in wave 2 — or all sources from day one? | Keep 5 for the POC deadline; add PIB and MHA first in wave 2; explicitly tell Divya/George. |
| D10 | Show "Issued on / Published on website" in the email's Reference No. & Date cell when the dates differ? | Yes — it is exactly the reason Divya gave for capturing both dates. |
| — | Fill the Entity Profile (licences/roles TVS Credit holds and doesn't hold) | Divya to provide; used only for suggestions. |
