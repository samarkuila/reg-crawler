# Constraints and Policies — Regulatory Monitor

**Version:** 0.2 · **Date:** 23.11.2026 · **Applies to:** everyone building, operating or using the system, and to Claude Code sessions (mirrored in `CLAUDE.md`).

Policies marked **[HARD]** cannot be waived inside the project. Changing one needs written approval from Compliance (business owner) and IT/InfoSec.

---

## 1. Operating principles (from the Compliance process)
1. **[HARD]** Automation may identify, extract, draft and suggest. **Compliance decides** applicability, treatment and final wording.
2. **[HARD]** The regulatory document is the single source of truth for every output.
3. Build and test module by module.
4. **[HARD]** Every output traces back to the source document and paragraph/location.
5. Usable by non-technical Compliance users; routine changes without developers.

## 2. Web crawling policy
| # | Policy |
|---|---|
| W1 **[HARD]** | Never bypass, solve or evade CAPTCHAs, bot checks, logins or paywalls. No CAPTCHA-solving services, proxy/IP rotation, user-agent rotation, stealth/undetected browsers, or fingerprint spoofing. |
| W2 **[HARD]** | Obey robots.txt. If a path is disallowed, use the official alternative (RSS, data file, email alert) or the manual path. |
| W3 | Identify honestly: `User-Agent: TVSCredit-RegMonitor/<ver> (+<compliance contact email>)`. |
| W4 | Politeness: ≥ 2 s delay per host, 1 concurrent request per domain, AutoThrottle, timeouts, max 2 retries for 5xx/timeouts only. |
| W5 | Frequency: scheduled runs only (2/day + hourly for URGENT sources); "Run now" is rate-limited (1 per source per 10 min). |
| W6 | Prefer official machine channels in this order: data file/API → RSS → HTML listing → JS rendering → email alert → manual. |
| W7 | Blocked = record BLOCKED, notify, manual-fetch path. Never treat a blocked source as "no updates". |
| W8 | Terms of use: check each site's terms; Legal confirms use of UN consolidated list data for internal screening. |

## 3. AI / agent policy (Luna)
| # | Policy |
|---|---|
| A1 **[HARD]** | AI is advisory. It never approves, sends, publishes, hides or deletes anything. |
| A2 **[HARD]** | Detection of new items never depends on AI. |
| A3 **[HARD]** | Every AI-extracted field must carry a verbatim evidence quote that is machine-validated; otherwise null. |
| A4 | Only public regulator text is sent to the LLM in Module 1. No customer data, PII, credentials or internal documents. |
| A5 | Model endpoint must be approved by IT/InfoSec (preferred: Azure OpenAI / Microsoft Foundry deployment of GPT Luna). Data-retention terms reviewed. |
| A6 | Prompts and screening guidance are versioned files/settings; guidance changes need Compliance approval; every change re-runs the agent eval. |
| A7 | Document text is untrusted input: delimited, never executed as instructions. No tools/browsing for the agent. |
| A8 | Daily budget cap; usage and cost logged per call. |
| A9 | Agent output is labelled "Suggested by Luna" in the UI and recorded separately from human decisions. |

## 4. Data and evidence policy
| # | Policy |
|---|---|
| D1 **[HARD]** | Snapshots, documents, audit events and AI extractions are immutable (write-once files, DB triggers). |
| D2 | Retention: evidence and audit kept for at least the TVS records-retention period (confirm with Compliance; default 8 years). |
| D3 | Files named by SHA-256; hashes shown in Item History. |
| D4 | Backups: daily copy of `data/` and DB (V2: managed backups). |
| D5 | No secrets in code or git. `.env` locally; secret store in V2. |

## 5. Human-in-the-loop & approvals
| # | Policy |
|---|---|
| H1 **[HARD]** | Nothing appears in the daily update without a recorded review decision. |
| H2 **[HARD]** | The system never sends email. A person sends from Outlook. |
| H3 | Maker–checker: source, settings, guidance and template changes need approval by a different user. |
| H4 | Daily update approval by an approver different from the preparer (configurable for POC if only one Compliance user is available — logged). |
| H5 | Dismissals require a reason. Dismissed items stay visible and searchable. |
| H6 | A "no updates" email cannot be approved while any source failure is unacknowledged. |

## 6. Security & access
| # | Policy |
|---|---|
| S1 | Runs inside the TVS network; not exposed to the internet. |
| S2 | Roles: viewer, reviewer, approver, admin. POC uses a labelled user switcher; V2 uses Entra ID SSO. |
| S3 | Least privilege on the host; app runs as a service account; `data/` readable only by the app. |
| S4 | Dependencies pinned (uv lock); monthly update check; no unvetted packages. |

## 7. Technical constraints
| Constraint | Detail |
|---|---|
| Stack | Python 3.12, FastAPI, vanilla HTML/CSS/JS, Scrapy, SQLAlchemy/Alembic, APScheduler; no frontend frameworks in V1 |
| Process model | One app process (single worker) in POC; Scrapy only as subprocess |
| DB | SQLite (POC) with Postgres-compatible models |
| Network | Outbound HTTPS to regulator sites and the approved LLM endpoint only |
| OS | Windows laptop/VM (provide `tasks.ps1` alongside Makefile) or Linux VM |
| Timeline | POC build by 30.11.2026; testing week of 01.12.2026 |

## 8. Business constraints
- Compliance users are non-technical: plain language, DD.MM.YYYY, minimal clicks.
- Output must match the existing daily email format exactly.
- Ground truth comes from Divya's manual process during the shadow run.

## 9. Policy exceptions
Record any exception in `docs/project-status.md` → *Decisions & exceptions* with: policy ID, reason, approver, expiry date.
