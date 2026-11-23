# System Design — Regulatory Monitor (Module 1)

**Version:** 0.2 · **Date:** 23.11.2026 · **Read with:** `architecture.md`, `component-architecture.md`, `agents/luna-nbfc-agent.md`

---

## 1. Requirements recap

| Type | Requirement |
|---|---|
| Functional | Crawl 5 sources (POC) twice daily + on demand; detect new/changed/backdated; RSS-vs-HTML check; store evidence; review; email draft; audit; Luna suggestions (optional) |
| Scale | ~30 URLs, ~50–200 listing rows per run, ~5–30 new items/day, 5–20 users |
| Latency | Full run < 10 min; agent enrichment < 5 min after run; UI pages < 2 s |
| Availability | Business hours, must not miss the 10:00 / 19:30 runs (missed run → catch-up on start) |
| Integrity | Immutable evidence and audit; full traceability |
| Compliance | Polite crawling, no bot-check evasion, human decision gate, no auto-send |

Scale is small; the design optimises for **correctness, traceability and simplicity**, not throughput.

---

## 2. High-level design

```
               ┌───────────────┐   10:00 / 19:30 / hourly(URGENT) / Run now
               │  Scheduler    │──────────────┐
               └───────────────┘              ▼
┌──────────┐  HTTPS  ┌──────────────────────────────────────────────┐
│ Browser  │◄───────►│ FastAPI app (single process, 1 worker)       │
│ (UI)     │         │  API ─ Services ─ Repositories               │
└──────────┘         │  CrawlRunner ─ Detection ─ Documents         │
                     │  AgentOrchestrator ─ LlmClient ──────────────┼──► GPT Luna endpoint
                     └───────┬───────────────────────┬──────────────┘    (Azure OpenAI / OpenAI)
                 subprocess  │                       │ SQLAlchemy
                             ▼                       ▼
                 ┌──────────────────┐      ┌──────────────────┐   ┌────────────────────┐
                 │ Scrapy crawler   │─────►│ SQLite (→ PG)    │   │ File store data/   │
                 │ rss/listing/unsc │      └──────────────────┘   │ snapshots/documents│
                 └────────┬─────────┘                             └────────────────────┘
                          │ polite HTTP (robots, 2s delay, 1 req/domain)
                          ▼
             RBI (RSS+HTML) · UNSC (XML) · TRAI · IRDAI · eGazette
```

---

## 3. Key flows

### 3.1 Scheduled cycle (EOD)
1. Scheduler fires at 19:30 (working day) → `CrawlRunner.start(EOD)`; lock acquired (refuse overlap).
2. For each active, approved source (sequential per host): spawn Scrapy subprocess; timeout 5 min/source.
3. Pipelines write snapshot (hash), raw items, `source_check` (OK/FAILED/BLOCKED/ZERO_ITEMS/PARSE_ERROR).
4. Detection: fingerprint → item + observation; reconcile channels; sanity checks.
5. Documents: download/extract for new/modified items; blocked → NEEDS_MANUAL_FETCH.
6. Relevance rules: baseline score + reason.
7. Agent (if enabled): Luna screen → extract (≥ MEDIUM) → sweep snapshots; validated results stored.
8. Run closed (COMPLETED/PARTIAL); UI banners for failures/URGENT; lock released.
9. Reviewer works in Daily Review → decisions → Email Preview → approve → export `.eml` → sends from Outlook.

### 3.2 Missed schedule
On app start, if the last expected run for today has no `crawl_run`, start a catch-up run labelled with the missed cycle and show a banner.

### 3.3 Blocked source
Middleware detects block → BLOCKED; no retry with altered requests → UI red strip → reviewer "Mark checked manually" (note) → email names the source.

### 3.4 Agent failure
Provider error / invalid JSON twice / budget reached → `agent_run.status` recorded → items show "Luna unavailable"; review continues with rule scores.

---

## 4. Data design highlights

- Core tables per `architecture.md` §4, plus `ai_extraction`, `agent_flag`, `agent_run` (see agent spec §7).
- **Fingerprint:** `sha256(source_id | normalised_ref_no)` else `sha256(source_id | canonical_url)`.
- **Item states:** DETECTED → (NEEDS_MANUAL_FETCH) → ENRICHED (agent done, optional) → IN_REVIEW → DECIDED → PUBLISHED.
- **Immutability:** snapshot, document, audit_event, ai_extraction are append-only (DB triggers + write-once files).
- **Versioning:** source, settings, screening guidance, email template — every change is a version with maker/checker.

---

## 5. Non-functional design

| Concern | Design |
|---|---|
| Reliability | Per-source isolation (subprocess, timeout); one failure never stops others; run status PARTIAL; catch-up run |
| Concurrency | Single app worker in POC (scheduler + lock in one process); SQLite WAL mode |
| Performance | Sequential per host with 2 s delay → ~1–3 min per source; agent calls in a small worker pool (max 4 concurrent) |
| Security | TVS network only; secrets in `.env`/secret store; POC user switcher only in dev; HTTPS for LLM; no PII sent to LLM |
| Observability | Structured JSON logs (run_id, source_id, item_id); Scrapy logs per source; `agent_run` metrics; Source Health page |
| Cost control | Agent daily budget; low reasoning effort for screening; text truncation; skip agent for unchanged items |
| Portability | SQLAlchemy/Alembic → Postgres by config; LlmClient → any provider |
| Testability | Fixtures for every source; no network in unit tests; memory vs SQL contract tests; agent golden-set eval |

---

## 6. Capacity estimate (POC)

| Item | Estimate |
|---|---|
| Listing snapshots | ~10 per run × 3 runs/day × ~200 KB = ~6 MB/day |
| Documents | ~10–30/day × ~500 KB = ~15 MB/day |
| DB growth | < 50 MB in 3 months |
| Luna calls | ~20–60 items/day × (screen + some extract) + ~10 sweeps/run → ~100–150 calls/day |

---

## 7. Failure modes and handling

| Failure | Detection | Handling |
|---|---|---|
| Site layout change | ZERO_ITEMS / count drop / Luna sweep flags | Red/amber status; D&A fixes selectors via Sources (approved version) |
| RSS and HTML disagree | channel_mismatch | Item shown with flag; D&A checks parser |
| CAPTCHA / 403 / 429 | BlockDetection | BLOCKED + manual path; never evaded |
| Document blocked | download status | NEEDS_MANUAL_FETCH → upload |
| Backdated publication | displayed_date < last run | BACKDATED badge; issue vs publish dates shown |
| App down at run time | no crawl_run for slot | Catch-up run on start + banner |
| LLM unavailable / over budget | agent_run status | Continue without suggestions |
| Hallucinated field | quote validation | Field nulled; never displayed as validated |

---

## 8. Evolution path

V2: Postgres, Entra ID SSO, Graph Outlook drafts, Teams alerts, email-alert ingestion, remaining 8 sources, Luna tuned with production feedback.
V3: Module 2 (paragraph-wise tracker) reuses `regulatory_item`, `document` and the agent framework (new tasks, same guardrails).
