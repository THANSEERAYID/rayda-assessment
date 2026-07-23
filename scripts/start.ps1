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
# 3.10 is a hard floor: the code uses PEP 604 unions (`str | None`) that Pydantic
# evaluates at runtime, so on 3.9 this fails at import, not just at install.
# Checked here so an unsupported interpreter is a clear message rather than a
# raw pip resolution error.
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

# No '%' in the probe: `python` is often a .bat shim (pyenv-win and friends), and
# cmd.exe would expand %d before Python ever sees the string.
function Get-PyVersion($exe, $prefix) {
    try {
        $out = & $exe @prefix -c "import sys; v=sys.version_info; print(str(v[0])+'.'+str(v[1]))" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        return [version]($out | Select-Object -Last 1).Trim()
    } catch { return $null }
}

if (Test-Path $VenvPython) {
    $venvVer = Get-PyVersion $VenvPython @()
    if ($null -eq $venvVer -or $venvVer -lt [version]"3.10") {
        Write-Host "The existing .venv uses Python $venvVer, but 3.10+ is required." -ForegroundColor Red
        Write-Host "Delete it and re-run:  Remove-Item -Recurse -Force .venv" -ForegroundColor Yellow
        exit 1
    }
    Write-Step ".venv already exists (Python $venvVer) - reusing it"
} else {
    Write-Step "No .venv found - looking for Python 3.10+"
    # The py launcher is the usual way a newer Python is reachable on Windows
    # even when `python` on PATH is old.
    $candidates = @(
        @{ Exe = "python";  Prefix = @() },
        @{ Exe = "py";      Prefix = @("-3.13") },
        @{ Exe = "py";      Prefix = @("-3.12") },
        @{ Exe = "py";      Prefix = @("-3.11") },
        @{ Exe = "py";      Prefix = @("-3.10") },
        @{ Exe = "python3"; Prefix = @() }
    )
    $chosen = $null
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
        $v = Get-PyVersion $c.Exe $c.Prefix
        if ($null -ne $v -and $v -ge [version]"3.10") {
            $chosen = $c
            Write-Step "Using Python $v ($($c.Exe) $($c.Prefix -join ' '))"
            break
        }
    }
    if ($null -eq $chosen) {
        $found = Get-PyVersion "python" @()
        Write-Host ""
        Write-Host "Python 3.10 or newer is required$(if ($found) { ", but the Python on PATH is $found" })." -ForegroundColor Red
        Write-Host "Install a newer Python, then re-run this script:" -ForegroundColor Yellow
        Write-Host "  winget install Python.Python.3.12" -ForegroundColor Yellow
        Write-Host "  (or download from https://www.python.org/downloads/)" -ForegroundColor Yellow
        Write-Host "Already have one? Make sure it is on PATH, or that 'py -3.12' works." -ForegroundColor Yellow
        exit 1
    }
    & $chosen.Exe @($chosen.Prefix) -m venv "$RepoRoot\.venv"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Could not create the virtualenv - see output above." -ForegroundColor Red
        exit 1
    }
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
