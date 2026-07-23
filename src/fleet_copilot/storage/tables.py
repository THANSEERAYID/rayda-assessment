"""Database schema.

Deliberately portable between SQLite and Postgres: the deterministic evaluation
tier seeds an in-memory SQLite database so it runs with no external services,
while the application runs on Postgres alongside the LangGraph checkpointer.

Portability rules observed here and in the repositories:
  * raw telemetry is stored as TEXT (JSON string), never JSONB — no dialect-only
    operators leak into queries;
  * every value used in a filter is promoted to a real typed column at ingest;
  * "latest snapshot per device" uses ``ROW_NUMBER()``, not Postgres ``DISTINCT ON``.
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData()

companies = Table(
    "companies",
    metadata,
    Column("company_id", String(64), primary_key=True),
    Column("name", String(128), nullable=False),
)

employees = Table(
    "employees",
    metadata,
    Column("employee_id", String(64), primary_key=True),
    Column("company_id", String(64), ForeignKey("companies.company_id"), nullable=False),
)

devices = Table(
    "devices",
    metadata,
    Column("device_id", String(64), primary_key=True),
    Column("company_id", String(64), ForeignKey("companies.company_id"), nullable=False),
    Column("employee_id", String(64), nullable=False),
    Column("model_name", String(128), nullable=False),
    Column("platform", String(32), nullable=False),
    Column("hostname", String(128), nullable=False),
    Index("ix_devices_company", "company_id"),
)

snapshots = Table(
    "snapshots",
    metadata,
    Column("snapshot_id", String(96), primary_key=True),
    Column("device_id", String(64), ForeignKey("devices.device_id"), nullable=False),
    Column("company_id", String(64), nullable=False),
    Column("employee_id", String(64), nullable=False),
    Column("collected_at", DateTime(timezone=False), nullable=False),
    Column("platform", String(32), nullable=False),
    Column("os_product_name", String(64), nullable=False),
    Column("os_product_version", String(64), nullable=False),
    Column("model_name", String(128), nullable=False),
    Column("hostname", String(128), nullable=False),
    # Byte counts exceed 2^31 (32 GB RAM = 34359738368), so these must be
    # BigInteger — Postgres INTEGER would overflow.
    Column("ram_total_bytes", BigInteger, nullable=False),
    Column("ram_used_bytes", BigInteger, nullable=False),
    Column("ram_used_pct", Float, nullable=False),
    Column("disk_size_bytes", BigInteger, nullable=False),
    Column("disk_available_bytes", BigInteger, nullable=False),
    Column("disk_free_pct", Float, nullable=False),
    Column("disk_encrypted", Boolean, nullable=False),
    Column("disk_mount_point", String(64), nullable=False),
    Column("battery_present", Boolean, nullable=False, default=False),
    Column("battery_percentage", Integer),
    Column("battery_condition", String(64)),
    Column("battery_cycle_count", Integer),
    Column("battery_full_charge_capacity", Integer),
    Column("battery_charging_status", String(32)),
    Column("raw", Text, nullable=False),
    Index("ix_snapshots_company_collected", "company_id", "collected_at"),
    Index("ix_snapshots_device_collected", "device_id", "collected_at"),
)

# compliance_results is unnested from the snapshot JSON so drift queries are real
# SQL rather than a full-table JSON scan.
compliance_results = Table(
    "compliance_results",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("snapshot_id", String(96), ForeignKey("snapshots.snapshot_id"), nullable=False),
    Column("device_id", String(64), nullable=False),
    Column("company_id", String(64), nullable=False),
    Column("collected_at", DateTime(timezone=False), nullable=False),
    Column("check_id", String(64), nullable=False),
    Column("status", String(16), nullable=False),
    Column("severity", String(16), nullable=False),
    Index("ix_compliance_company_check", "company_id", "check_id"),
    Index("ix_compliance_device_check", "device_id", "check_id", "collected_at"),
)

installed_software = Table(
    "installed_software",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("snapshot_id", String(96), ForeignKey("snapshots.snapshot_id"), nullable=False),
    Column("device_id", String(64), nullable=False),
    Column("company_id", String(64), nullable=False),
    Column("collected_at", DateTime(timezone=False), nullable=False),
    Column("name", String(128), nullable=False),
    Column("version", String(64), nullable=False),
    Column("publisher", String(128), nullable=False),
    Index("ix_software_company_name", "company_id", "name"),
)

# --------------------------------------------------------------------------
# Operational tables
# --------------------------------------------------------------------------
pending_actions = Table(
    "pending_actions",
    metadata,
    Column("action_id", String(64), primary_key=True),
    Column("thread_id", String(64), nullable=False),
    Column("company_id", String(64), nullable=False),
    Column("action_type", String(64), nullable=False),
    Column("target_device_id", String(64)),
    # How the target is named in the approval queue and the audit trail. An
    # action record that says only "MT7PJB7N5LRE" is hard to review months later.
    Column("target_label", String(160)),
    Column("target_employee_id", String(64)),
    Column("params", Text, nullable=False, default="{}"),
    Column("justification", Text, nullable=False),
    Column("evidence_ids", Text, nullable=False, default="[]"),
    # The reviewer's signal, computed once at proposal time while the evidence
    # ledger is still in hand. Recomputing it later is impossible — the ledger
    # lives for one turn — and the approval queue needs it most.
    Column("review", Text),
    Column("status", String(16), nullable=False),
    Column("created_at", DateTime(timezone=False), nullable=False),
    Column("decided_at", DateTime(timezone=False)),
    Column("decided_by", String(64)),
    Column("result", Text),
    Index("ix_actions_company_status", "company_id", "status"),
    Index("ix_actions_thread", "thread_id"),
)

# Append-only. Nothing in the codebase issues UPDATE or DELETE against this table.
audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("thread_id", String(64)),
    Column("company_id", String(64)),
    Column("event_type", String(48), nullable=False),
    Column("actor", String(64), nullable=False),
    Column("summary", Text, nullable=False),
    Column("detail", Text, nullable=False, default="{}"),
    Column("created_at", DateTime(timezone=False), nullable=False),
    Index("ix_audit_company_created", "company_id", "created_at"),
    Index("ix_audit_thread", "thread_id"),
)

# Per-node execution record backing the trace viewer.
run_steps = Table(
    "run_steps",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("thread_id", String(64), nullable=False),
    Column("turn_id", String(64), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("node", String(48), nullable=False),
    Column("status", String(16), nullable=False),
    Column("detail", Text, nullable=False, default="{}"),
    Column("duration_ms", Integer),
    Column("created_at", DateTime(timezone=False), nullable=False),
    Index("ix_steps_thread_turn", "thread_id", "turn_id", "seq"),
)

# Every citable reading a tool emitted, kept so a citation outlives its turn.
#
# Without this, ``evidence_id`` resolves only inside the turn that produced it —
# fine in chat, useless on the Approvals page, where a proposal from an old
# thread cites ids pointing at nothing a reviewer can open. The premise there is
# "each proposal cites the telemetry behind it", so the citation has to be
# checkable later, not merely present.
#
# ``evidence_id`` is content-derived: the same reading from the same tool always
# hashes to the same id, which makes re-recording it a no-op rather than a
# duplicate.
evidence = Table(
    "evidence",
    metadata,
    Column("evidence_id", String(32), primary_key=True),
    Column("company_id", String(64), nullable=False),
    Column("thread_id", String(64)),
    Column("turn_id", String(64)),
    Column("tool", String(48), nullable=False),
    Column("device_id", String(64)),
    Column("device_label", String(128)),
    Column("snapshot_ts", DateTime(timezone=False)),
    Column("field", String(64), nullable=False),
    Column("value", Text),
    Column("detail", Text, nullable=False, default="{}"),
    Column("created_at", DateTime(timezone=False), nullable=False),
    Index("ix_evidence_company", "company_id"),
    Index("ix_evidence_turn", "turn_id"),
)

# A completed turn's result, kept so it can be shown again after a refresh.
#
# The trace (run_steps) records how a turn ran; this records what it produced —
# the grounded answer and its claims — which otherwise survive only inside the
# LangGraph checkpoint, unqueryable. ``kind`` distinguishes a chat turn from a
# task-card investigation so the Action-performed view can list only the latter.
# ``result`` is the whole response snapshot; live proposal status is overlaid
# from ``pending_actions`` at read time so nothing shown here goes stale.
turns = Table(
    "turns",
    metadata,
    Column("turn_id", String(64), primary_key=True),
    Column("thread_id", String(64), nullable=False),
    Column("company_id", String(64), nullable=False),
    Column("kind", String(16), nullable=False, default="chat"),
    Column("question", Text, nullable=False),
    Column("result", Text, nullable=False, default="{}"),
    Column("created_at", DateTime(timezone=False), nullable=False),
    Index("ix_turns_company_kind", "company_id", "kind"),
)

# Remediation tickets — the concrete artifact an executed open_remediation_ticket
# action produces. This is the "real integration" seam the action service names:
# instead of calling an external ticketing system, an approved ticket action
# writes a row here, which the Tickets page lists. One ticket per executed
# action, linked by ``action_id`` so it traces back to its proposal and evidence.
tickets = Table(
    "tickets",
    metadata,
    Column("ticket_id", String(64), primary_key=True),
    Column("company_id", String(64), nullable=False),
    Column("action_id", String(64), nullable=False, unique=True),
    Column("device_id", String(64)),
    Column("device_label", String(128)),
    Column("check_id", String(64)),
    Column("note", Text),
    Column("status", String(16), nullable=False, default="open"),
    Column("created_at", DateTime(timezone=False), nullable=False),
    Index("ix_tickets_company_created", "company_id", "created_at"),
)

# Emails the system has sent (or simulated). Written when a notify_employee
# action executes, and by the manual compose form. ``action_id`` links a message
# back to the proposal that triggered it, or is null for a hand-composed one.
# ``status`` records what actually happened at the SMTP boundary — sent when a
# real server accepted it, simulated when mail is not configured, failed on
# error — so the Emails page is a truthful log, not a claim.
emails = Table(
    "emails",
    metadata,
    Column("email_id", String(64), primary_key=True),
    Column("company_id", String(64), nullable=False),
    Column("action_id", String(64)),
    Column("employee_id", String(64)),
    Column("to_address", String(256), nullable=False),
    Column("subject", Text, nullable=False),
    Column("body", Text, nullable=False),
    Column("status", String(16), nullable=False, default="simulated"),
    Column("error", Text),
    Column("created_at", DateTime(timezone=False), nullable=False),
    Index("ix_emails_company_created", "company_id", "created_at"),
)

# Threads are bound to a tenant at creation; later turns cannot switch tenant.
threads = Table(
    "threads",
    metadata,
    Column("thread_id", String(64), primary_key=True),
    Column("company_id", String(64), nullable=False),
    Column("created_at", DateTime(timezone=False), nullable=False),
    Column("title", String(256)),
)
