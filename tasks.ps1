# Windows equivalent of the Makefile (GNU make is not required on Windows).
# Usage: .\tasks.ps1 <dev|test|lint|migrate|seed-demo|crawl|probe|demo>
param([Parameter(Position = 0)][string]$Target = "help")

$py = if ($env:PY) { $env:PY } else { "python" }

switch ($Target) {
    "dev"       { & $py -m uvicorn app.main:app --reload --port 8000 }
    "test"      { & $py -m pytest }
    "lint"      { & $py -m ruff check . }
    "migrate"   { & $py -m alembic upgrade head }
    "seed-demo" { Write-Host "not implemented until Prompt 8" }
    "crawl"     { Write-Host "not implemented until Prompt 13" }
    "probe"     { Write-Host "not implemented until Prompt 14" }
    "demo"      { Write-Host "not implemented until Prompt 15" }
    default     { Write-Host "Targets: dev test lint migrate seed-demo crawl probe demo" }
}
exit $LASTEXITCODE
