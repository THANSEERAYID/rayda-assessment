"""The specialized worker agents and their tool scoping.

A worker's toolset is a capability boundary, not a hint. The manager dispatches
work to these agents, and each one is bound only to the tools listed here — a
tool it does not hold is not in its tool list at all, so it cannot be talked
into calling one.

The scoping that matters is ``action_agent``: it holds no discovery tool and no
evidence-emitting tool. It physically cannot find a device or manufacture a
citation, so it can only act on what a prior worker established and handed over.
That is what makes the manager's sequencing decision load-bearing rather than
decorative, and it is enforced by the binding rather than by a prompt.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..config import settings

# Tools that return an ``evidence`` array. Only these can produce a citation the
# grounding validator will accept, so any worker expected to justify a claim or
# an action needs at least one — or must inherit evidence via handoff.
# (See ``mcp_server/tools/read.py``: get_device_snapshot and list_fleet_summary
# deliberately return no evidence.)
EVIDENCE_EMITTING_TOOLS = frozenset(
    {
        "query_devices",
        "get_compliance_status",
        "get_device_history",
        "run_insight_scan",
        "run_read_query",
    }
)


class WorkerName(str, Enum):
    QA = "qa_agent"
    INSIGHT = "insight_agent"
    ACTION = "action_agent"


@dataclass(frozen=True)
class WorkerConfig:
    name: WorkerName
    prompt: str
    allowed_tools: tuple[str, ...]
    max_iterations: int
    description: str

    @property
    def emits_evidence(self) -> bool:
        return bool(set(self.allowed_tools) & EVIDENCE_EMITTING_TOOLS)


WORKER_REGISTRY: dict[WorkerName, WorkerConfig] = {
    WorkerName.QA: WorkerConfig(
        name=WorkerName.QA,
        prompt="qa_agent",
        allowed_tools=(
            "list_fleet_summary",
            "query_devices",
            "get_compliance_status",
            "get_device_history",
            "get_device_snapshot",
            "run_read_query",
        ),
        max_iterations=settings.max_tool_iterations,
        description=(
            "Point-in-time fleet questions: which devices match criteria, current "
            "compliance state, a specific device's readings."
        ),
    ),
    WorkerName.INSIGHT: WorkerConfig(
        name=WorkerName.INSIGHT,
        prompt="insight_agent",
        allowed_tools=(
            "run_insight_scan",
            "get_device_history",
            "get_device_snapshot",
            "list_fleet_summary",
        ),
        max_iterations=settings.max_tool_iterations,
        description=(
            "Patterns and change over time: failing batteries, storage trends, "
            "sustained memory pressure, compliance drift. The only agent that can "
            "run the deterministic detectors."
        ),
    ),
    WorkerName.ACTION: WorkerConfig(
        name=WorkerName.ACTION,
        prompt="action_agent",
        allowed_tools=(
            "create_upgrade_order",
            "open_remediation_ticket",
            "flag_device_for_replacement",
            "notify_employee",
            "list_pending_actions",
            # No get_device_snapshot: looking up by id is still a way to thrash
            # on a misread label from the user message. Evidence and device ids
            # come only from the prior worker's handoff.
        ),
        # Proposing is not exploratory — a smaller budget than the discovery
        # agents, which keeps a two-worker turn's cost bounded.
        max_iterations=3,
        description=(
            "Proposes operational actions for human approval. Has no discovery "
            "tools: it acts only on devices a previous agent already identified, "
            "citing that agent's evidence."
        ),
    ),
}

DISCOVERY_WORKERS = (WorkerName.QA, WorkerName.INSIGHT)


def config_for(name: WorkerName | str) -> WorkerConfig:
    return WORKER_REGISTRY[WorkerName(name)]


def describe_workers() -> str:
    """Roster rendered for the manager's prompt."""
    return "\n".join(
        f"- `{cfg.name.value}` — {cfg.description}\n"
        f"  tools: {', '.join(cfg.allowed_tools)}"
        for cfg in WORKER_REGISTRY.values()
    )
