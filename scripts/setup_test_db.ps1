# One-time script to create seventh_ai_vision_test + migrate it.
# Run after `docker compose up -d` and healthchecks are green.
#
# Usage:
#   cd "D:\Virtual Patrolling"
#   .\scripts\setup_test_db.ps1

$ErrorActionPreference = "Stop"

$POSTGRES_CONTAINER = "docker-postgres-1"
$POSTGRES_SUPERUSER = "postgres"
$POSTGRES_SUPERPASSWORD = "change_me_dev_only"
$APP_USER = "svc_app"
$TEST_DB = "seventh_ai_vision_test"
$ALEMBIC_URL = "postgresql+psycopg://${POSTGRES_SUPERUSER}:${POSTGRES_SUPERPASSWORD}@localhost:5432/${TEST_DB}"

Write-Host "==> Checking Docker / postgres container..." -ForegroundColor Cyan
$state = docker inspect --format "{{.State.Status}}" $POSTGRES_CONTAINER 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Container '$POSTGRES_CONTAINER' not found. Run: docker compose -f docker/docker-compose.yml up -d" -ForegroundColor Red
    exit 1
}
if ($state -ne "running") {
    Write-Host "ERROR: Container '$POSTGRES_CONTAINER' is '$state', not running." -ForegroundColor Red
    exit 1
}
Write-Host "    Container is running." -ForegroundColor Green

Write-Host ""
Write-Host "==> Creating database '$TEST_DB' (idempotent)..." -ForegroundColor Cyan
$env:PGPASSWORD = $POSTGRES_SUPERPASSWORD
docker exec -e PGPASSWORD=$POSTGRES_SUPERPASSWORD $POSTGRES_CONTAINER `
    psql -U $POSTGRES_SUPERUSER -tc `
    "SELECT 1 FROM pg_database WHERE datname = '$TEST_DB'" | ForEach-Object { $_.Trim() } | Set-Variable dbExists
if ($dbExists -eq "1") {
    Write-Host "    Database already exists, skipping CREATE." -ForegroundColor Yellow
} else {
    docker exec -e PGPASSWORD=$POSTGRES_SUPERPASSWORD $POSTGRES_CONTAINER `
        psql -U $POSTGRES_SUPERUSER -c "CREATE DATABASE $TEST_DB"
    Write-Host "    Created." -ForegroundColor Green
}

Write-Host ""
Write-Host "==> Granting '$APP_USER' access to '$TEST_DB'..." -ForegroundColor Cyan
docker exec -e PGPASSWORD=$POSTGRES_SUPERPASSWORD $POSTGRES_CONTAINER `
    psql -U $POSTGRES_SUPERUSER -d $TEST_DB -c @"
GRANT CONNECT ON DATABASE $TEST_DB TO $APP_USER;
GRANT USAGE, CREATE ON SCHEMA public TO $APP_USER;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO $APP_USER;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO $APP_USER;
"@
Write-Host "    Done." -ForegroundColor Green

Write-Host ""
Write-Host "==> Running Alembic migrations against '$TEST_DB'..." -ForegroundColor Cyan
$env:ALEMBIC_DATABASE_URL = $ALEMBIC_URL
Push-Location "backend"
try {
    # alembic.exe lives in the root .venv (note the dot prefix), not backend/.venv
    ..\.venv\Scripts\alembic.exe upgrade head
    if ($LASTEXITCODE -ne 0) { throw "alembic upgrade head failed (see above)" }
    Write-Host "    Migrations applied." -ForegroundColor Green
} finally {
    Pop-Location
    $env:ALEMBIC_DATABASE_URL = $null
}

Write-Host ""
Write-Host "==> Test database is ready." -ForegroundColor Green
Write-Host ""
Write-Host "Run the test suites with:" -ForegroundColor White
Write-Host "  # Backend tests (uses .venv):" -ForegroundColor Gray
Write-Host "  .venv\Scripts\pytest.exe backend/tests/ -v" -ForegroundColor Cyan
Write-Host "  # AI-worker tests (uses .venv-worker):" -ForegroundColor Gray
Write-Host "  .venv-worker\Scripts\pytest.exe ai-worker/tests/ -v" -ForegroundColor Cyan
