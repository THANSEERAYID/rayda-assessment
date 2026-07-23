"""Application settings. The single place environment variables are read."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Runtime uses Postgres (LangGraph checkpointer needs it); the deterministic
    # evaluation tier overrides this with an in-memory SQLite URL so it runs with
    # no external services.
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/fleet_copilot"

    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_temperature: float = 0.0
    openai_seed: int = 7
    openai_timeout_seconds: float = 60.0

    # Telemetry is stored in UTC; everything a person reads is rendered in this
    # zone so one turn never mixes two clocks.
    display_timezone: str = "Asia/Kolkata"

    dataset_path: Path = REPO_ROOT / "data" / "raw" / "snapshots.jsonl"
    fixtures_dir: Path = REPO_ROOT / "data" / "fixtures"

    # -- rate limiting ------------------------------------------------------
    # A token bucket smooths throughput against the provider's per-minute
    # limits; the semaphore bounds how many turns can be in flight at once, so
    # a burst of concurrent users cannot open dozens of simultaneous requests.
    llm_requests_per_second: float = 2.0
    llm_max_bucket_size: int = 5
    llm_max_concurrency: int = 4

    # Agent loop bounds — a runaway tool loop is both a cost and a safety problem.
    # A turn can make a dozen model calls (plan, manager, two workers, grounding),
    # so the per-call timeout above does not bound the turn; this does.
    turn_timeout_seconds: float = 300.0
    max_tool_iterations: int = 6
    max_tool_retries: int = 2
    max_grounding_retries: int = 1

    # The hard ceiling on model calls for one turn, across every node. The
    # per-worker iteration caps bound each loop individually; this bounds their
    # sum, so no combination of dispatch and retries can run away.
    max_llm_calls_per_turn: int = 20
    # Circuit breakers that end a worker loop early rather than burning its
    # whole budget on calls that are getting nowhere.
    max_consecutive_tool_errors: int = 3
    max_unproductive_iterations: int = 2

    # A blanket instruction ("flag everything") should not fill the approval
    # queue. Bounding a turn's proposals keeps it reviewable — nobody reads
    # thirty justifications carefully.
    max_proposals_per_turn: int = 5

    # Detector thresholds. Chosen from the dataset's distribution (see README);
    # exposed here so the evaluation suite and the tools cannot drift apart.
    disk_low_free_pct: float = 15.0
    disk_critical_free_pct: float = 5.0
    ram_high_used_pct: float = 85.0
    ram_sustained_ratio: float = 0.8  # share of window above the threshold
    battery_high_cycle_count: int = 800
    battery_capacity_decline_pct: float = 3.0

    unapproved_software: tuple[str, ...] = ("uTorrent", "TeamViewer", "CleanMyMac X")

    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


settings = Settings()
