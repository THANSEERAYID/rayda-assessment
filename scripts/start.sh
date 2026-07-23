#!/usr/bin/env bash
# One-shot setup + run for reviewers (bash/macOS/Linux/Git-Bash equivalent of
# scripts/start.ps1).
#
# Creates the virtualenv if missing, installs Python and npm dependencies if
# missing, makes sure a .env exists, ensures the database schema exists and the
# dataset is ingested, then runs the API and the web UI. Ctrl+C stops both.
#
# Usage:  bash scripts/start.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

step() { echo; echo "==> $*"; }

# -- Python virtualenv --------------------------------------------------------
if [ -f ".venv/Scripts/python.exe" ]; then
    VENV_PY=".venv/Scripts/python.exe"   # Windows venv layout (Git Bash)
elif [ -f ".venv/bin/python" ]; then
    VENV_PY=".venv/bin/python"           # POSIX venv layout
else
    step "No .venv found - creating one"
    command -v python3 >/dev/null 2>&1 && PY=python3 || PY=python
    "$PY" -m venv .venv
    if [ -f ".venv/Scripts/python.exe" ]; then
        VENV_PY=".venv/Scripts/python.exe"
    else
        VENV_PY=".venv/bin/python"
    fi
fi

step "Installing Python dependencies (pyproject.toml)"
"$VENV_PY" -m pip install --upgrade pip --quiet
"$VENV_PY" -m pip install -e ".[dev]" --quiet

# -- .env ----------------------------------------------------------------------
if [ ! -f ".env" ]; then
    step "No .env found - copying .env.example"
    cp .env.example .env
    echo "Edit .env to set OPENAI_API_KEY and DATABASE_URL, then re-run." >&2
fi

# -- Database / dataset --------------------------------------------------------
step "Ensuring the database schema exists"
"$VENV_PY" scripts/create_database.py

step "Ingesting telemetry dataset (idempotent - safe to repeat)"
"$VENV_PY" -m fleet_copilot.ingestion.ingest

# -- Frontend dependencies ------------------------------------------------------
command -v npm >/dev/null 2>&1 || { echo "npm not found on PATH. Install Node.js 18+ and re-run." >&2; exit 1; }

if [ ! -d "web/node_modules" ]; then
    step "No node_modules found - running npm install"
    (cd web && npm install)
else
    step "node_modules already exists - reusing it"
fi

# -- Launch both, stop both on Ctrl+C -------------------------------------------
step "Starting the API on http://localhost:8000 and the UI on http://localhost:5173"

cleanup() {
    echo
    echo "Stopping..."
    kill "${API_PID:-}" "${WEB_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

"$VENV_PY" scripts/run_api.py --reload --port 8000 &
API_PID=$!

(cd web && npm run dev) &
WEB_PID=$!

echo
echo "API : http://localhost:8000/api/health"
echo "UI  : http://localhost:5173"
echo "Press Ctrl+C to stop both."
wait
