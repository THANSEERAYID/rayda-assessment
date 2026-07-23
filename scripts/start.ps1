# One-shot setup + run for reviewers.
#
# Creates the virtualenv if missing, installs Python and npm dependencies if
# missing, makes sure a .env exists, ingests the dataset if the database is
# empty, then launches the API and the web UI each in their own window so both
# logs stay visible. Close either window (or Ctrl+C the one running this
# script) to stop.
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\start.ps1

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

# -- Python virtualenv -------------------------------------------------------
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Step "No .venv found - creating one"
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCmd) {
        Write-Host "python was not found on PATH. Install Python 3.10+ and re-run." -ForegroundColor Red
        exit 1
    }
    python -m venv "$RepoRoot\.venv"
} else {
    Write-Step ".venv already exists - reusing it"
}

Write-Step "Installing Python dependencies (pyproject.toml)"
& $VenvPython -m pip install --upgrade pip --quiet
& $VenvPython -m pip install -e ".[dev]" --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip install failed - see output above." -ForegroundColor Red
    exit 1
}

# -- .env ---------------------------------------------------------------------
$EnvFile = Join-Path $RepoRoot ".env"
if (-not (Test-Path $EnvFile)) {
    Write-Step "No .env found - copying .env.example"
    Copy-Item (Join-Path $RepoRoot ".env.example") $EnvFile
    Write-Host "Edit .env to set OPENAI_API_KEY and DATABASE_URL, then re-run." -ForegroundColor Yellow
}

# -- Database / dataset -------------------------------------------------------
Write-Step "Ensuring the database schema exists"
& $VenvPython "scripts\create_database.py"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Could not reach the configured database. Check DATABASE_URL in .env." -ForegroundColor Red
    Write-Host "(SQLite needs no server - set DATABASE_URL=sqlite+pysqlite:///./data/fixtures/dev.sqlite)" -ForegroundColor Yellow
    exit 1
}

Write-Step "Ingesting telemetry dataset (idempotent - safe to repeat)"
& $VenvPython -m fleet_copilot.ingestion.ingest

# -- Frontend dependencies -----------------------------------------------------
$WebDir = Join-Path $RepoRoot "web"
$NodeModules = Join-Path $WebDir "node_modules"

$npmCmd = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npmCmd) {
    Write-Host "npm was not found on PATH. Install Node.js 18+ and re-run." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $NodeModules)) {
    Write-Step "No node_modules found - running npm install"
    Push-Location $WebDir
    npm install
    Pop-Location
} else {
    Write-Step "node_modules already exists - reusing it"
}

# -- Launch both processes, each in its own window ----------------------------
Write-Step "Starting the API on http://localhost:8000"
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$RepoRoot'; & '$VenvPython' scripts/run_api.py --reload --port 8000"
)

Write-Step "Starting the web UI on http://localhost:5173"
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$WebDir'; npm run dev"
)

Write-Host ""
Write-Host "Both started in separate windows:" -ForegroundColor Green
Write-Host "  API : http://localhost:8000/api/health"
Write-Host "  UI  : http://localhost:5173"
Write-Host ""
Write-Host "Close either window to stop that process." -ForegroundColor DarkGray
