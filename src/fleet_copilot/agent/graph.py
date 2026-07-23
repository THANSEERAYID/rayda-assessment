"""The agent graph.

    plan ──► manager ──► run_worker ⟲ (loops while agents remain queued)
      │                      │
      │                      └──► ground ──┬─► approval ──► execute_action ⟲──► respond
      │                                    │         ▲______________│
      │                                    └──────────────────────────────────► respond
      └──────────────────────────────────────────────────────────────────────► refuse

Each stage is a separate node with an explicit routing decision, rather than a
single model call that does everything, because each one carries a different
guarantee: ``manager`` decides which specialized agent handles the work,
``run_worker`` bounds the tool loop and binds only that agent's tools,
``ground`` enforces citation, ``approval`` enforces human consent. Collapsing
them would make those guarantees prompt-level requests instead of structural
ones.

``run_worker`` is a self-loop rather than three separate nodes: the loop body is
identical for every agent, and only the prompt and the bound toolset differ. One
node parameterised by the queue keeps the bookkeeping in a single place.

``execute_action`` may return to ``approval`` when the reviewer decided only a
subset of the batch — those run immediately, then the gate opens again for the
rest so partial approvals stay on the same traced path.
"""
from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from .nodes.approval import approval_node, execute_action_node, route_after_execute
from .nodes.ground import ground_node, route_after_ground
from .nodes.manager import manager_node
from .nodes.plan import plan_node, route_after_plan
from .nodes.respond import refuse_node, respond_node
from .nodes.worker import route_after_worker, worker_node
from .state import AgentState


def build_graph(checkpointer=None):
    """Compile the graph.

    A checkpointer is mandatory in practice: without one ``interrupt()`` has
    nowhere to persist the suspended turn, so approval could not survive the gap
    between proposing an action and a human deciding on it. An in-memory saver is
    substituted when none is supplied so tests can run without Postgres.
    """
    builder = StateGraph(AgentState)

    builder.add_node("plan", plan_node)
    builder.add_node("manager", manager_node)
    builder.add_node("run_worker", worker_node)
    builder.add_node("ground", ground_node)
    builder.add_node("approval", approval_node)
    builder.add_node("execute_action", execute_action_node)
    builder.add_node("respond", respond_node)
    builder.add_node("refuse", refuse_node)

    builder.add_edge(START, "plan")
    builder.add_conditional_edges(
        "plan", route_after_plan, {"manager": "manager", "refuse": "refuse"}
    )
    # The manager's schema guarantees a non-empty dispatch, so this is a plain
    # edge — there is no "no agents chosen" branch to handle.
    builder.add_edge("manager", "run_worker")
    builder.add_conditional_edges(
        "run_worker",
        route_after_worker,
        {"run_worker": "run_worker", "ground": "ground", "refuse": "refuse"},
    )
    builder.add_conditional_edges(
        "ground",
        route_after_ground,
        {"approval": "approval", "respond": "respond", "refuse": "refuse"},
    )
    # No edge bypasses approval on the way to execution. Partial decisions loop
    # back so remaining proposals stay behind the same gate.
    builder.add_edge("approval", "execute_action")
    builder.add_conditional_edges(
        "execute_action",
        route_after_execute,
        {"approval": "approval", "respond": "respond"},
    )
    builder.add_edge("respond", END)
    builder.add_edge("refuse", END)

    return builder.compile(checkpointer=checkpointer or InMemorySaver())


# Node names that may write state-changing effects, asserted on by the
# evaluation suite to prove the gate cannot be bypassed.
EXECUTION_NODES = {"execute_action"}
APPROVAL_NODE = "approval"
