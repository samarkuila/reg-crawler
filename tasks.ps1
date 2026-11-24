# Windows equivalent of the Makefile (GNU make is not required on Windows).
# Usage: .\tasks.ps1 <dev|test|lint|migrate|seed-demo|rebaseline|crawl|probe|demo>
param(
    [Parameter(Position = 0)][string]$Target = "help",
    [Parameter(ValueFromRemainingArguments = $true)]$Rest
)

$py = if ($env:PY) { $env:PY } else { "python" }

switch ($Target) {
    "dev"       { & $py -m uvicorn app.main:app --reload --port 8000 }
    "test"      { & $py -m pytest }
    "lint"      { & $py -m ruff check . }
    "migrate"   { & $py -m alembic upgrade head }
    "seed-demo" { & $py -m app.cli seed-demo @Rest }
    "rebaseline" {
        # .\tasks.ps1 rebaseline -Source <id> [-Actor "<your name>"]
        $src = $null; $actor = "cli"
        for ($i = 0; $i -lt $Rest.Count; $i++) {
            if ($Rest[$i] -eq "-Source") { $src = $Rest[$i + 1] }
            if ($Rest[$i] -eq "-Actor") { $actor = $Rest[$i + 1] }
        }
        if (-not $src) { Write-Host "Usage: .\tasks.ps1 rebaseline -Source <id> [-Actor <name>]"; exit 2 }
        & $py -m app.cli rebaseline --source $src --actor $actor
    }
    "crawl"     { Write-Host "not implemented until Prompt 13" }
    "probe"     { Write-Host "not implemented until Prompt 14" }
    "demo"      { Write-Host "not implemented until Prompt 15" }
    default     { Write-Host "Targets: dev test lint migrate seed-demo rebaseline crawl probe demo" }
}
exit $LASTEXITCODE
