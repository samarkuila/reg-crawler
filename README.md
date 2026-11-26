# Regulatory Monitor (TVS Credit — Compliance Automation, Module 1 POC)

See `CLAUDE.md` and `docs/` for scope, rules and architecture.

## Setup (Windows, existing venv)
```powershell
& "D:\Compliance Automation\venv\Scripts\Activate.ps1"
pip install -e ".[dev]"
copy .env.example .env
```
Note: the stack targets Python 3.12 + uv; this machine currently uses the venv's Python 3.14 with pip
(see decisions log in `docs/project-status.md`).

## Commands
`.\tasks.ps1 <target>` (or `make <target>` where GNU make exists):
`dev` · `test` · `lint` · `migrate` · `seed-demo` · `crawl` · `probe` · `demo`
(later targets are stubs until their prompts.)

- Browser fetch (once per machine, for sources set to *Fetch with: Browser*): `.\tasks.ps1 install-browser` installs Chromium for scrapy-playwright
- Run: `.\tasks.ps1 dev` → http://localhost:8000/api/v1/health (JSON) · API docs at http://localhost:8000/docs
- Crawler check: `cd crawler; scrapy list`
