.DEFAULT_GOAL := help
PY := .venv/Scripts/python.exe
ifeq ($(OS),)
PY := .venv/bin/python
endif

.PHONY: help setup db ingest api web dev eval eval-live scorecard clean

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

setup:  ## Create the virtualenv and install dependencies
	python -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"
	@echo "Now copy .env.example to .env and set OPENAI_API_KEY."

db:  ## Create the Postgres database (no-op if it already exists)
	$(PY) scripts/create_database.py

ingest:  ## Load the telemetry dataset into the database
	$(PY) -m fleet_copilot.ingestion.ingest

api:  ## Run the API on :8000
	$(PY) scripts/run_api.py --reload --port 8000

web:  ## Run the React UI on :5173
	cd web && npm install && npm run dev

dev: ## Reminder of the two processes to run
	@echo "Run 'make api' and 'make web' in separate terminals."

eval:  ## Deterministic suite — no API key, no model calls
	$(PY) -m pytest eval/deterministic -q

eval-live:  ## Live agent suite — needs OPENAI_API_KEY, makes real calls
	$(PY) -m pytest eval --live -q

scorecard:  ## Deterministic suite with a category-by-category summary
	$(PY) eval/scorecard.py --tier deterministic

scorecard-live:  ## Live scorecard (real model calls)
	$(PY) eval/scorecard.py --tier live

clean:
	rm -rf .pytest_cache **/__pycache__ data/fixtures/*.sqlite
