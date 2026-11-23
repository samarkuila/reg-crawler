# Repository Etiquette — reg-monitor

**Version:** 0.1 · **Date:** 23.11.2026 · For humans **and** Claude Code sessions.

---

## 1. Branching
| Branch | Purpose | Rules |
|---|---|---|
| `main` | Always runnable, demo-ready | Protected; changes only via PR; tests + lint green |
| `feat/<milestone>-<short-name>` | New work, e.g. `feat/m1-daily-review` | One prompt/feature per branch |
| `fix/<short-name>` | Bug fixes | Link the issue/finding |
| `docs/<short-name>` | Documentation-only changes | No code |
| `spike/<short-name>` | Throwaway experiments | Never merged as-is |

Keep branches short-lived (≤ 2 days). Rebase on `main` before opening a PR.

## 2. Commits
Format: `<type>(<scope>): <what>` with milestone tag.
```
feat(review): daily review panel with status pills [M1]
fix(crawler): handle empty RBI RSS feed as ZERO_ITEMS [M3]
docs(arch): add Luna agent tables [M4]
test(detection): backdated insert fixture [M4]
```
Types: `feat` `fix` `docs` `test` `refactor` `chore` `ci`. Scopes: `ui` `api` `review` `email` `config` `db` `crawler` `detection` `agent` `docs`.
One logical change per commit. Never commit generated data, `.env`, `data/`, or secrets.

## 3. Pull requests
- Title = commit style. Description template:
  ```
  ## What
  ## Why (FR/J/NFR ids, prompt #)
  ## How to verify (steps, screenshots for UI)
  ## Architecture check (files in correct layer/location? docs updated?)
  ## Policy check (crawling, AI, HITL — any impact?)
  ```
- Size: ≤ 400 changed lines where possible; split otherwise.
- At least **one human reviewer** (D&A peer). UI changes: screenshot next to the mockup. Changes to screening guidance, email wording or policies: Compliance reviewer too.
- Claude Code may open PRs; a human always reviews and merges.

## 4. Code conventions
- Python: ruff (lint + format), type hints everywhere, Pydantic models at boundaries, no bare `except`.
- Layering: UI → API → services → repositories. Routers never import DB models; spiders never import services (only the repository write API).
- Names: snake_case modules, PascalCase classes, enums UPPER_CASE values exactly as in `architecture.md`.
- JS: ES modules, no global state beyond page module, `escapeHtml` for any data rendered as HTML.
- CSS: tokens only (`tokens.css`); no hard-coded colours in templates.
- Config is data: never hard-code URLs, keywords, run times, recipients or prompts in code.

## 5. Tests
- Every PR adds/updates tests. Unit tests never hit the network — use `tests/fixtures/`.
- New source = fixture + parser test. New agent prompt version = eval run attached.
- `make test` and `make lint` must pass locally before pushing.

## 6. Documentation
- Behaviour change → update the relevant doc in the same PR (`01-PRD.md`, `architecture.md`, `component-architecture.md`, `system-design.md`, agent spec).
- Architecture decisions → add an entry to `docs/project-status.md` *Decisions*.
- Keep `docs/project-status.md` updated at least twice a week.

## 7. Working with Claude Code
- Start each task with `/clear`; use plan mode (Shift+Tab) for ⚙️ prompts in `BUILD-PROMPTS.md`.
- One prompt → one branch → one PR.
- Always run the **checkpoint prompt** before committing.
- Review `git diff` yourself; never merge code you have not read.
- Don't paste secrets or internal data into prompts.
- If Claude proposes changing architecture or a [HARD] policy → stop and raise it with the team.

## 8. Secrets & data
- `.env` is git-ignored; `.env.example` lists keys without values.
- LLM keys only in `.env`/secret store; rotate if exposed.
- `data/` (DB, snapshots, documents, logs) is never committed.

## 9. Releases (POC)
- Tag demo-ready states: `v0.1.0-m1`, `v0.2.0-m2`, … `v0.5.0-poc`.
- `CHANGELOG.md` updated on each tag (Added/Changed/Fixed).

## 10. Definition of done
Acceptance criteria met · tests + lint green · docs updated · PR reviewed · status file updated · no policy violations.
