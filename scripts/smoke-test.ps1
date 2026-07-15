# End-to-end smoke test runner (plan §15). The actual assertions live in
# smoke_test.py (DB/Redis/HTTP/WebSocket access is far less awkward in
# Python than PowerShell); this script's job is just: confirm the stack is
# actually up and healthy first, then hand off.
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

Write-Host "Checking docker compose service health..."
$services = @("postgres", "redis", "api", "ai-worker-face", "ai-worker-intrusion")
foreach ($svc in $services) {
    $status = docker compose -f "$RepoRoot\docker\docker-compose.yml" ps --format json $svc | ConvertFrom-Json
    if (-not $status -or $status.Health -notin @("healthy", "")) {
        Write-Host "Service '$svc' is not healthy yet (status: $($status.Health)). Run 'docker compose up -d' and wait for healthchecks first." -ForegroundColor Red
        exit 1
    }
}

Write-Host "Running smoke_test.py..."
& "$RepoRoot\.venv\Scripts\python.exe" "$RepoRoot\scripts\smoke_test.py"
exit $LASTEXITCODE
