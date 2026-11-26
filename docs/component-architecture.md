# Component Architecture — Regulatory Monitor (Module 1)

**Version:** 0.2 · **Date:** 23.11.2026 · **Status:** Draft for review
**Read with:** `architecture.md` (system context, data model, API), `system-design.md`, `agents/luna-nbfc-agent.md`

> v0.2 adds the **Luna NBFC Screening Agent** (GPT Luna) as an advisory component. Detection stays deterministic; the agent only ranks, extracts and drafts — Compliance decides.

---

## 1. Component map

```
┌─────────────────────────────────── PRESENTATION ───────────────────────────────────┐
│  Static UI (HTML/CSS/JS, TVS theme)                                                │
│  Pages: Daily Review · Source Health · Email Preview · Item History ·              │
│         Sources · Settings · Audit Log · Styleguide                                │
└───────────────────────────────────────┬────────────────────────────────────────────┘
                                        │ fetch /api/v1 (JSON)
┌───────────────────────────────────── API LAYER ────────────────────────────────────┐
│  Routers: runs · crawl · sources · source-checks · items · daily-update ·          │
│           settings · audit · agent · pages                                         │
│  Concerns: validation (Pydantic), POC user/role resolution, error mapping          │
└───────────────────────────────────────┬────────────────────────────────────────────┘
                                        │ service calls
┌─────────────────────────────────── SERVICE LAYER ──────────────────────────────────┐
│  CrawlRunner   Scheduler   Detection   Documents   Relevance(rules)                │
│  Review        EmailRender Config(versioning/approval)   Audit                     │
│  AgentOrchestrator ──► Luna NBFC Screening Agent (via LlmClient)                   │
└──────────────┬────────────────────────┬──────────────────────────┬─────────────────┘
               │ subprocess             │ Repository protocol      │ HTTPS (approved endpoint)
┌──────────────▼───────────┐ ┌──────────▼──────────────┐ ┌─────────▼─────────────────┐
│ CRAWLER (Scrapy project) │ │ DATA LAYER              │ │ LLM PROVIDER              │
│ spiders: rss · listing · │ │ repositories: memory/sql│ │ GPT Luna (gpt-6-luna)     │
│ unsc_xml                 │ │ SQLAlchemy + Alembic    │ │ via Azure OpenAI/Foundry  │
│ middleware: BlockDetect  │ │ SQLite → Postgres       │ │ or OpenAI API             │
│ pipelines: Snapshot →    │ │ File store (write-once) │ │ (no tools, no browsing)   │
│ Normalise → DbWrite      │ └─────────────────────────┘ └───────────────────────────┘
└──────────────────────────┘
```

---

## 2. Component catalogue

| # | Component | Location | Responsibility | Depends on | Must NOT |
|---|---|---|---|---|---|
| C1 | **Static UI** | `app/static/`, `app/templates/` | Render pages matching `docs/ui-mockups`; call API; keyboard shortcuts | API | Hold business logic; call DB or LLM |
| C2 | **API routers** | `app/api/` | Validate requests, resolve user/role, call services, map errors | Services | Touch DB directly |
| C3 | **CrawlRunner** | `app/services/crawl_runner.py` | Create run, lock, launch Scrapy subprocess per source (sets `REGCRAWLER_FETCH_ENGINE=browser` for a browser source's `listing` crawl only), collect status, trigger Detection → Documents → Agent | Crawler, Repos, Detection, AgentOrchestrator | Run Scrapy in-process |
| C4 | **Scheduler** | `app/services/scheduler.py` | APScheduler jobs from settings (10:00, 19:30, urgent hourly), holiday calendar | CrawlRunner, Config | Hard-code times |
| C5 | **Detection** | `app/services/detection.py` | Fingerprints, change types, RSS-vs-HTML reconciliation, sanity checks | Repos | Use AI or website dates to decide "new" |
| C6 | **Documents** | `app/services/documents.py` | Download, hash, store, extract text; NEEDS_MANUAL_FETCH | Repos, file store | Bypass blocks |
| C7 | **Relevance (rules)** | `app/services/relevance.py` | Keyword score + reason (baseline, always on) | Config | Hide items |
| C8 | **AgentOrchestrator** | `app/agents/orchestrator.py` | Queue agent tasks per item, budget/rate control, persist results, fallback when agent off | LlmClient, Repos | Change item state beyond "ENRICHED"; decide applicability |
| C9 | **Luna NBFC Agent** | `app/agents/luna/` | Screen NBFC relevance, extract fields with evidence, draft impact, sweep listings for possible misses | LlmClient, prompts | Browse, call tools, send email, approve anything |
| C10 | **LlmClient** | `app/services/llm.py` | Provider-agnostic interface (Azure OpenAI / OpenAI); structured JSON output; retries; token/cost logging | Provider SDK | Leak secrets; log document text to external systems |
| C11 | **Review** | `app/services/review.py` | Decisions, bulk dismiss, uploads, manual source checks, fields_edited tracking | Repos, Audit | Auto-accept agent output |
| C12 | **EmailRender** | `app/services/email_render.py` | Jinja email, no-update variant, `.eml` export, CC from SME routing | Repos, Config | Send email |
| C13 | **Config** | `app/services/config.py` | Versioned sources/settings/agent guidance; maker–checker approval | Repos, Audit | Apply unapproved versions |
| C14 | **Audit** | `app/services/audit.py` | Append-only events inside the caller's transaction | Repos | Update/delete events |
| C15 | **Repositories** | `app/repositories/` | `Repository` protocol; memory + SQL implementations | DB | Contain business rules |
| C16 | **Scrapy spiders** | `crawler/regcrawler/spiders/` | `rss`, `listing`, `unsc_xml`: fetch + parse. `listing` fetches with the source's engine: `http`, or `browser` (scrapy-playwright renders, then the same parsing runs; `crawler/regcrawler/browser.py`) | Scrapy, feedparser, scrapy-playwright (browser sources only) | Evade blocks; switch engines by itself |
| C17 | **BlockDetection** | `crawler/regcrawler/middlewares.py` | Classify CAPTCHA/403/429/challenge/login → BLOCKED | — | Retry with altered headers/proxies |
| C18 | **Pipelines** | `crawler/regcrawler/pipelines.py` | Snapshot (SHA-256) → Normalise → DbWrite | Repos | Overwrite files |
| C19 | **CLI** | `app/cli.py` | import-sources, crawl, probe, compare, agent-eval | Services | Bypass services |

---

## 3. Interfaces (contracts between components)

```python
# app/repositories/base.py (excerpt)
class Repository(Protocol):
    def create_run(self, cycle: Cycle, actor: str) -> CrawlRun: ...
    def record_source_check(self, check: SourceCheck, actor: str) -> None: ...
    def upsert_item(self, parsed: ParsedItem, run_id: str) -> tuple[RegulatoryItem, ChangeType]: ...
    def save_ai_extraction(self, ext: AiExtraction, actor: str = "agent:luna") -> None: ...
    def save_decision(self, d: ReviewDecision, actor: str) -> None: ...
    def list_items(self, f: ItemFilter) -> list[RegulatoryItem]: ...
    # … every mutation writes an AuditEvent in the same transaction

# app/services/llm.py
class LlmClient(Protocol):
    def complete_json(self, *, task: str, system: str, user: str,
                      schema: dict, reasoning_effort: str) -> LlmResult: ...
# LlmResult: data (dict), model, deployment, prompt_version, tokens_in, tokens_out, latency_ms, cost_inr

# app/agents/luna/agent.py
class LunaAgent:
    def screen(self, item: RegulatoryItem, text: str, guidance: str) -> ScreenResult: ...
    def extract(self, item: RegulatoryItem, text: str) -> ExtractResult: ...
    def sweep(self, snapshot_text: str, parsed: list[ParsedItem]) -> list[PossibleMiss]: ...
```

---

## 4. Component interaction — one crawl cycle

```
Scheduler ─► CrawlRunner.start(EOD)
  CrawlRunner ─► Scrapy subprocess (per source) ─► Pipelines ─► Repos (snapshots, raw items, checks)
  CrawlRunner ─► Detection.process(run)          ─► items + observations + mismatch flags
  CrawlRunner ─► Documents.fetch(new items)      ─► documents + text (or NEEDS_MANUAL_FETCH)
  CrawlRunner ─► Relevance.score(items)          ─► rule score (baseline)
  CrawlRunner ─► AgentOrchestrator.enqueue(run)  ─► Luna.screen / Luna.extract / Luna.sweep
                                                  ─► ai_extraction rows (validated quotes)
  UI Daily Review ◄─ API ◄─ Review (items + rule score + agent suggestions, side by side)
  Reviewer Accept/Edit/Dismiss ─► Review ─► review_decision + audit (fields_edited vs agent)
```

---

## 5. Ownership

| Area | Owner |
|---|---|
| UI, API, services | D&A — Saranya (lead), Samarendra |
| Crawler, source configs | D&A — Samarendra |
| Luna agent prompts & screening guidance | D&A builds; **Compliance (Divya) owns the guidance text** |
| Approvals of config/guidance | Compliance approver (George) |
| Infra, network, LLM endpoint | TVS IT / InfoSec |
