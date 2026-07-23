import { useEffect, useMemo, useState } from "react";
import { formatTimestamp } from "../format";
import type { AuditEvent, TraceStep } from "../types";

type Tab = "trace" | "audit";

/**
 * Trace & audit as a module workspace: tabs, KPI cards, search + status
 * filters, a selection banner, and a dense data table — same pattern as an
 * operational ledger, scoped to what the agent did and what was logged.
 */
export function TracePanel({
  companyId,
  steps,
  events,
  threadId,
  loading,
}: {
  companyId: string;
  steps: TraceStep[];
  events: AuditEvent[];
  /** The active conversation, only so its runs can be marked as current. */
  threadId: string | null;
  loading: boolean;
}) {
  const [tab, setTab] = useState<Tab>("trace");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [selectedStep, setSelectedStep] = useState<TraceStep | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<AuditEvent | null>(null);

  // Tenant switch must clear search/status — a filter like "waiting" from the
  // previous company otherwise hides every row while the KPI still shows the
  // full company step count.
  useEffect(() => {
    setQuery("");
    setStatusFilter("all");
    setSelected(new Set());
    setSelectedStep(null);
    setSelectedEvent(null);
  }, [companyId]);

  const errorSteps = steps.filter((s) => s.status === "error").length;
  const waitingSteps = steps.filter((s) =>
    ["waiting", "truncated", "circuit_broken", "no_progress"].includes(s.status),
  ).length;
  const blockedEvents = events.filter((e) =>
    /block|reject|den(y|ied)|cross.?tenant/i.test(
      `${e.event_type} ${e.summary}`,
    ),
  ).length;
  const avgMs =
    steps.length === 0
      ? null
      : Math.round(
          steps.reduce((sum, s) => sum + (s.duration_ms ?? 0), 0) /
            Math.max(1, steps.filter((s) => s.duration_ms != null).length),
        );
  const totalTokens = steps.reduce((sum, s) => {
    const n = stepLlm(s)?.total_tokens;
    return sum + (typeof n === "number" ? n : 0);
  }, 0);
  const runCount = new Set(steps.map((s) => s.turn_id)).size;

  const filteredSteps = useMemo(() => {
    const q = query.trim().toLowerCase();
    return steps.filter((step) => {
      if (statusFilter !== "all" && step.status !== statusFilter) return false;
      if (!q) return true;
      const hay = `${step.node} ${step.status} ${step.turn_id} ${JSON.stringify(step.detail ?? {})}`.toLowerCase();
      return hay.includes(q);
    });
  }, [steps, query, statusFilter]);

  // If a status pill disappeared with the new tenant's data, drop back to All.
  useEffect(() => {
    if (statusFilter === "all" || steps.length === 0) return;
    if (!steps.some((s) => s.status === statusFilter)) {
      setStatusFilter("all");
    }
  }, [steps, statusFilter]);

  const filteredEvents = useMemo(() => {
    const q = query.trim().toLowerCase();
    return events.filter((event) => {
      if (statusFilter === "blocked") {
        const blocked = /block|reject|den(y|ied)|cross.?tenant/i.test(
          `${event.event_type} ${event.summary}`,
        );
        if (!blocked) return false;
      } else if (statusFilter === "action" && !/action/i.test(event.event_type)) {
        return false;
      } else if (statusFilter === "tool" && !/tool|retrieval|ground/i.test(event.event_type)) {
        return false;
      } else if (statusFilter !== "all" && statusFilter !== "blocked" && statusFilter !== "action" && statusFilter !== "tool") {
        // unused for audit
      }
      if (!q) return true;
      const hay =
        `${event.id} ${event.event_type} ${event.actor} ${formatAuditSummary(event)} ${event.thread_id ?? ""}`.toLowerCase();
      return hay.includes(q);
    });
  }, [events, query, statusFilter]);

  const switchTab = (next: Tab) => {
    setTab(next);
    setQuery("");
    setStatusFilter("all");
    setSelected(new Set());
    setSelectedStep(null);
    setSelectedEvent(null);
  };

  const toggleRow = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAllVisible = (ids: string[]) => {
    setSelected((prev) => {
      const allOn = ids.length > 0 && ids.every((id) => prev.has(id));
      if (allOn) return new Set();
      return new Set(ids);
    });
  };

  const exportVisible = () => {
    const payload =
      tab === "trace"
        ? filteredSteps
        : filteredEvents.map((e) => ({
            ...e,
            summary: formatAuditSummary(e),
          }));
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download =
      tab === "trace" ? "fleet-copilot-trace.json" : "fleet-copilot-audit.json";
    a.click();
    URL.revokeObjectURL(url);
  };

  if (loading) return <div className="spinner">Loading trace…</div>;

  const stepIds = filteredSteps.map((s) => stepKey(s));
  const eventIds = filteredEvents.map((e) => String(e.id));

  return (
    <div className="module-page">
      <div className="module-toolbar">
        <div className="module-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "trace"}
            className={`module-tab ${tab === "trace" ? "on" : ""}`}
            onClick={() => switchTab("trace")}
          >
            Run trace
            <span className="module-tab-count">{steps.length}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "audit"}
            className={`module-tab ${tab === "audit" ? "on" : ""}`}
            onClick={() => switchTab("audit")}
          >
            Audit log
            {blockedEvents > 0 && (
              <span className="module-tab-flag">{blockedEvents} blocked</span>
            )}
            {blockedEvents === 0 && (
              <span className="module-tab-count">{events.length}</span>
            )}
          </button>
        </div>
        <div className="module-actions">
          <button type="button" className="btn" onClick={exportVisible}>
            Export
          </button>
        </div>
      </div>

      <div className="module-kpis">
        {tab === "trace" ? (
          <>
            <div className="module-kpi">
              <div className="sc-l">Trace steps</div>
              <div className="sc-v">{steps.length}</div>
              <div className="sc-d">
                {steps.length
                  ? `across ${runCount} ${runCount === 1 ? "run" : "runs"} for this company`
                  : "ask something to start a trace"}
              </div>
            </div>
            <div className="module-kpi">
              <div className="sc-l">Errors</div>
              <div className="sc-v">{errorSteps}</div>
              <div className={`sc-d ${errorSteps ? "danger" : "ok"}`}>
                {errorSteps ? "failed nodes" : "no failed nodes"}
              </div>
            </div>
            <div className="module-kpi">
              <div className="sc-l">Waiting / paused</div>
              <div className="sc-v">{waitingSteps}</div>
              <div className={`sc-d ${waitingSteps ? "warn" : "ok"}`}>
                {waitingSteps ? "approval or circuit pauses" : "none waiting"}
              </div>
            </div>
            <div className="module-kpi">
              <div className="sc-l">Tokens spent</div>
              <div className="sc-v">{totalTokens || "—"}</div>
              <div className="sc-d">
                {totalTokens
                  ? `across LLM steps · avg ${avgMs != null ? `${avgMs} ms` : "—"}`
                  : "run a turn to record model usage"}
              </div>
            </div>
          </>
        ) : (
          <>
            <div className="module-kpi">
              <div className="sc-l">Audit events</div>
              <div className="sc-v">{events.length}</div>
              <div className="sc-d">append-only tenant log</div>
            </div>
            <div className="module-kpi">
              <div className="sc-l">Blocked / rejected</div>
              <div className="sc-v">{blockedEvents}</div>
              <div className={`sc-d ${blockedEvents ? "danger" : "ok"}`}>
                {blockedEvents
                  ? "guardrail decisions worth reviewing"
                  : "no blocks in this window"}
              </div>
            </div>
            <div className="module-kpi">
              <div className="sc-l">Showing</div>
              <div className="sc-v">{filteredEvents.length}</div>
              <div className="sc-d">after search and filters</div>
            </div>
            <div className="module-kpi">
              <div className="sc-l">Selected</div>
              <div className="sc-v">{selected.size}</div>
              <div className="sc-d">
                {selected.size
                  ? "ask the copilot about these rows"
                  : "select rows to scope questions"}
              </div>
            </div>
          </>
        )}
      </div>

      <div className="module-filters">
        <label className="module-search">
          <span className="sr-only">Search</span>
          <input
            type="search"
            placeholder={
              tab === "trace"
                ? "Search nodes, turns, status…"
                : "Search events, actors, summaries…"
            }
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <div className="module-pills">
          {(tab === "trace" ? traceFilters(steps) : AUDIT_FILTERS).map((pill) => (
            <button
              key={pill.id}
              type="button"
              className={`filter-pill ${statusFilter === pill.id ? "on" : ""}`}
              onClick={() => setStatusFilter(pill.id)}
            >
              {pill.label}
            </button>
          ))}
        </div>
      </div>

      {selected.size > 0 && (
        <div className="module-banner">
          <span>
            {selected.size} {selected.size === 1 ? "row" : "rows"} selected — ask
            the copilot to explain, compare, or chase a failure.
          </span>
          <button type="button" className="linkish" onClick={() => setSelected(new Set())}>
            Clear
          </button>
        </div>
      )}

      {tab === "trace" && (
        <TraceTable
          steps={filteredSteps}
          threadId={threadId}
          selected={selected}
          active={selectedStep}
          onToggleAll={() => toggleAllVisible(stepIds)}
          onToggle={toggleRow}
          onOpen={(step) => {
            setSelectedEvent(null);
            setSelectedStep(step);
          }}
        />
      )}

      {tab === "audit" && (
        <AuditTable
          events={filteredEvents}
          selected={selected}
          active={selectedEvent}
          allSelected={
            eventIds.length > 0 && eventIds.every((id) => selected.has(id))
          }
          onToggleAll={() => toggleAllVisible(eventIds)}
          onToggle={toggleRow}
          onOpen={(event) => {
            setSelectedStep(null);
            setSelectedEvent(event);
          }}
        />
      )}

      {/* Selection banner sits above the table when rows are checked. */}

      {selectedStep && (
        <TraceDetailDrawer
          step={selectedStep}
          onClose={() => setSelectedStep(null)}
        />
      )}
      {selectedEvent && (
        <AuditDetailDrawer
          event={selectedEvent}
          onClose={() => setSelectedEvent(null)}
        />
      )}
    </div>
  );
}

/**
 * Filters built from the statuses actually present, not a fixed list.
 *
 * A hardcoded set silently hides whatever it forgot: `done` was missing, so
 * "Ok" showed 32 of 37 steps and every worker-completion row was unreachable.
 * The same would happen to `circuit_broken`, `budget_exceeded` and
 * `no_progress` — the statuses you most want to filter for when a turn went
 * wrong. Deriving them means a new status is filterable the day it is added.
 */
function traceFilters(steps: TraceStep[]): { id: string; label: string }[] {
  const counts = new Map<string, number>();
  for (const step of steps) {
    counts.set(step.status, (counts.get(step.status) ?? 0) + 1);
  }
  const ordered = [...counts.entries()].sort((a, b) => {
    // Failures first — they are why someone opened this page.
    const weight = (s: string) => (INTERESTING.has(s) ? 0 : 1);
    return weight(a[0]) - weight(b[0]) || b[1] - a[1];
  });
  return [
    { id: "all", label: "All" },
    ...ordered.map(([status, count]) => ({
      id: status,
      label: `${humaniseStatus(status)} ${count}`,
    })),
  ];
}

const INTERESTING = new Set([
  "error",
  "waiting",
  "truncated",
  "circuit_broken",
  "budget_exceeded",
  "no_progress",
]);

function humaniseStatus(status: string): string {
  const words = status.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const AUDIT_FILTERS = [
  { id: "all", label: "All" },
  { id: "action", label: "Actions" },
  { id: "tool", label: "Tools" },
  { id: "blocked", label: "Blocked" },
];

function stepKey(step: TraceStep): string {
  return `${step.turn_id}:${step.seq}`;
}

type StepLlmUsage = {
  model?: string;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
};

function stepLlm(step: TraceStep): StepLlmUsage | null {
  const raw = step.detail?.llm;
  if (!raw || typeof raw !== "object") return null;
  return raw as StepLlmUsage;
}

function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 8)}…` : id;
}

function TraceTable({
  steps,
  threadId,
  selected,
  active,
  onToggleAll,
  onToggle,
  onOpen,
}: {
  steps: TraceStep[];
  threadId: string | null;
  selected: Set<string>;
  active: TraceStep | null;
  onToggleAll: () => void;
  onToggle: (id: string) => void;
  onOpen: (step: TraceStep) => void;
}) {
  // Steps are company-scoped (same as the KPI). Do not gate the list on the
  // active chat thread — Globex (and any tenant with only paused threads) can
  // have a full audit history while threadId is still null.
  if (steps.length === 0) {
    return (
      <div className="empty">
        <strong>No matching steps</strong>
        <div className="small" style={{ marginTop: 6 }}>
          {threadId
            ? "Try clearing filters, or send another question."
            : "Ask something in the agent panel to start a run trace."}
        </div>
      </div>
    );
  }

  const ids = steps.map(stepKey);
  const allOn = ids.length > 0 && ids.every((id) => selected.has(id));

  // Group by turn, keep steps in execution order, then put the newest run on
  // top. Sorting by turn_id string is wrong — ids are random, not chronological.
  const byTurn = new Map<string, TraceStep[]>();
  for (const step of steps) {
    const bucket = byTurn.get(step.turn_id);
    if (bucket) bucket.push(step);
    else byTurn.set(step.turn_id, [step]);
  }
  const runs = [...byTurn.entries()].map(([turnId, turnSteps]) => ({
    turnId,
    steps: [...turnSteps].sort((a, b) => a.seq - b.seq),
  }));
  runs.sort((a, b) => {
    const aAt = a.steps[a.steps.length - 1]?.created_at ?? "";
    const bAt = b.steps[b.steps.length - 1]?.created_at ?? "";
    return bAt.localeCompare(aAt);
  });

  return (
    <div className="run-list">
      <div className="run-list-head">
        <label className="run-select-all">
          <input
            type="checkbox"
            checked={allOn}
            onChange={onToggleAll}
            aria-label="Select all steps"
          />
          <span>
            {runs.length} {runs.length === 1 ? "run" : "runs"} · {steps.length}{" "}
            {steps.length === 1 ? "step" : "steps"}
          </span>
        </label>
      </div>

      {runs.map((run, index) => (
        <RunGroup
          key={run.turnId}
          run={run}
          currentThreadId={threadId}
          defaultOpen={index === 0}
          selected={selected}
          active={active}
          onToggle={onToggle}
          onOpen={onOpen}
        />
      ))}
    </div>
  );
}

function RunGroup({
  run,
  currentThreadId,
  defaultOpen,
  selected,
  active,
  onToggle,
  onOpen,
}: {
  run: { turnId: string; steps: TraceStep[] };
  currentThreadId: string | null;
  defaultOpen: boolean;
  selected: Set<string>;
  active: TraceStep | null;
  onToggle: (id: string) => void;
  onOpen: (step: TraceStep) => void;
}) {
  const first = run.steps[0];
  const last = run.steps[run.steps.length - 1];
  const question = runQuestion(run.steps);
  const outcome = runOutcome(run.steps);
  const isCurrentThread =
    currentThreadId != null && run.steps[0]?.thread_id === currentThreadId;

  const durationMs = run.steps.reduce((sum, s) => sum + (s.duration_ms ?? 0), 0);
  const tokens = run.steps.reduce((sum, s) => {
    const n = stepLlm(s)?.total_tokens;
    return sum + (typeof n === "number" ? n : 0);
  }, 0);
  const toolCalls = run.steps.filter((s) => s.node.includes("/")).length;
  const modelCalls = run.steps.filter((s) => stepLlm(s) != null).length;

  return (
    <details className="run" open={defaultOpen}>
      <summary>
        <div className="run-head">
          <span className={`run-outcome ${outcome.tone}`}>{outcome.label}</span>
          <span className="run-question" title={question ?? undefined}>
            {question ?? "Run"}
          </span>
          <span className="run-toggle">
            {run.steps.length} {run.steps.length === 1 ? "step" : "steps"}
          </span>
        </div>
        <div className="run-meta mono">
          {formatTimestamp(first.created_at)}
          {" · "}
          {modelCalls} model {modelCalls === 1 ? "call" : "calls"}
          {" · "}
          {toolCalls} tool {toolCalls === 1 ? "call" : "calls"}
          {tokens > 0 ? ` · ${tokens} tokens` : ""}
          {durationMs > 0 ? ` · ${(durationMs / 1000).toFixed(1)}s` : ""}
          {" · "}
          {shortId(run.turnId)}
          {/* The page spans every conversation this tenant has had, so a run
              has to say which one it came from — and mark the one the chat is
              currently attached to. */}
          {run.steps[0]?.thread_id && (
            <>
              {" · "}
              <span className={isCurrentThread ? "run-thread is-current" : "run-thread"}>
                {isCurrentThread ? "this conversation" : shortId(run.steps[0].thread_id)}
              </span>
            </>
          )}
        </div>
      </summary>

      {/* Steps in the order they ran: plan, dispatch, each worker and every
          tool it called, grounding, then the terminal node. */}
      <ol className="run-steps">
        {run.steps.map((step) => {
          const id = stepKey(step);
          const isActive =
            active?.turn_id === step.turn_id && active?.seq === step.seq;
          const isChecked = selected.has(id);
          const llm = stepLlm(step);
          return (
            <li
              key={id}
              className={[
                "run-step",
                isActive ? "is-active" : "",
                isChecked ? "is-checked" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              onClick={() => onOpen(step)}
            >
              <span className="run-step-check" onClick={(e) => e.stopPropagation()}>
                <input
                  type="checkbox"
                  checked={isChecked}
                  onChange={() => onToggle(id)}
                  aria-label={`Select step ${step.seq}`}
                />
              </span>
              <span className="run-step-seq mono">{step.seq}</span>
              <span className="run-step-kind">{stepKind(step)}</span>
              <span className="run-step-node mono">{stepDisplayName(step)}</span>
              <span className="run-step-note">{stepNote(step)}</span>
              <span className="run-step-meta mono muted">
                {llm?.total_tokens != null ? `${llm.total_tokens} tok` : ""}
                {step.duration_ms != null
                  ? `${llm?.total_tokens != null ? " · " : ""}${step.duration_ms} ms`
                  : ""}
              </span>
              <StatusDot status={step.status} />
            </li>
          );
        })}
      </ol>

      <div className="run-foot mono muted">
        Finished at {formatTimestamp(last.created_at)}
      </div>
    </details>
  );
}

/** The question that started this run, recorded on its planning step. */
function runQuestion(steps: TraceStep[]): string | null {
  for (const step of steps) {
    const q = step.detail?.question;
    if (typeof q === "string" && q.trim()) return q.trim();
  }
  return null;
}

/** How the run ended, taken from its terminal node. */
function runOutcome(steps: TraceStep[]): { label: string; tone: string } {
  const nodes = steps.map((s) => s.node);
  const last = steps[steps.length - 1];
  if (nodes.includes("refuse")) return { label: "Refused", tone: "warn" };
  if (steps.some((s) => s.status === "error"))
    return { label: "Errors", tone: "danger" };
  // Partial approvals re-enter the gate; the latest waiting step wins.
  if (last?.node === "human_approval" && last.status === "waiting")
    return { label: "Awaiting approval", tone: "warn" };
  if (nodes.includes("human_approval") && !nodes.includes("execute_action"))
    return { label: "Awaiting approval", tone: "warn" };
  if (nodes.includes("execute_action")) return { label: "Executed", tone: "ok" };
  if (nodes.includes("respond")) return { label: "Answered", tone: "ok" };
  return { label: "In progress", tone: "neutral" };
}

/** What kind of work a step represents, for the reader rather than the code. */
function stepKind(step: TraceStep): string {
  if (step.node.includes("/")) return "Tool";
  if (step.node.startsWith("worker:")) return "Agent";
  if (step.node === "plan") return "Plan";
  if (step.node === "manager") return "Dispatch";
  if (step.node.startsWith("ground")) return "Grounding";
  if (step.node === "human_approval") return "Approval";
  if (step.node === "execute_action") return "Execution";
  if (step.node === "respond" || step.node === "refuse") return "Response";
  return "Step";
}

function stepDisplayName(step: TraceStep): string {
  if (step.node.includes("/")) return step.node.split("/")[1];
  if (step.node.startsWith("worker:")) return step.node.slice("worker:".length);
  return step.node;
}

/** A one-line summary of what actually happened, pulled from the step detail. */
function stepNote(step: TraceStep): string {
  const d = step.detail ?? {};
  const summary = d.summary as Record<string, unknown> | undefined;

  if (typeof d.question === "string" && d.intent) return `intent: ${String(d.intent)}`;
  if (Array.isArray(d.dispatched)) return `to ${(d.dispatched as string[]).join(" → ")}`;
  if (summary) {
    if (typeof summary.match_count === "number")
      return `${summary.match_count} match${summary.match_count === 1 ? "" : "es"}`;
    if (typeof summary.finding_count === "number")
      return `${summary.finding_count} finding${summary.finding_count === 1 ? "" : "s"}`;
    if (typeof summary.error === "string")
      return String(summary.message ?? summary.error);
    if (typeof summary.action_id === "string") return "proposal created";
  }
  if (typeof d.valid_claims === "number")
    return `${d.valid_claims} of ${d.claims ?? d.valid_claims} claims kept`;
  if (Array.isArray(d.action_ids))
    return `${(d.action_ids as unknown[]).length} awaiting decision`;
  if (Array.isArray(d.executed) || Array.isArray(d.rejected)) {
    const done = (d.executed as unknown[] | undefined)?.length ?? 0;
    const no = (d.rejected as unknown[] | undefined)?.length ?? 0;
    const still =
      typeof d.still_awaiting === "number" && d.still_awaiting > 0
        ? ` · ${d.still_awaiting} still open`
        : "";
    return `${done} executed, ${no} rejected${still}`;
  }
  if (typeof d.reason === "string") return d.reason;
  if (typeof d.message === "string") return d.message;
  if (typeof d.claims === "number") return `${d.claims} claims`;
  return "";
}

function AuditTable({
  events,
  selected,
  active,
  allSelected,
  onToggleAll,
  onToggle,
  onOpen,
}: {
  events: AuditEvent[];
  selected: Set<string>;
  active: AuditEvent | null;
  allSelected: boolean;
  onToggleAll: () => void;
  onToggle: (id: string) => void;
  onOpen: (event: AuditEvent) => void;
}) {
  if (events.length === 0) {
    return (
      <div className="empty">
        <strong>No matching events</strong>
        <div className="small" style={{ marginTop: 6 }}>
          Guardrail decisions and tool outcomes appear here as they happen.
        </div>
      </div>
    );
  }

  return (
    <div className="module-table-wrap">
      <table className="module-table">
        <thead>
          <tr>
            <th className="col-check">
              <input
                type="checkbox"
                checked={allSelected}
                onChange={onToggleAll}
                aria-label="Select all events"
              />
            </th>
            <th>ID</th>
            <th>Time</th>
            <th>Event</th>
            <th>Actor</th>
            <th>Summary</th>
            <th>Thread</th>
          </tr>
        </thead>
        <tbody>
          {events.map((event) => {
            const id = String(event.id);
            const isChecked = selected.has(id);
            const isActive = active?.id === event.id;
            return (
              <tr
                key={event.id}
                className={[isActive ? "is-active" : "", isChecked ? "is-checked" : ""]
                  .filter(Boolean)
                  .join(" ")}
                onClick={() => onOpen(event)}
              >
                <td className="col-check" onClick={(e) => e.stopPropagation()}>
                  <input
                    type="checkbox"
                    checked={isChecked}
                    onChange={() => onToggle(id)}
                    aria-label={`Select event ${event.id}`}
                  />
                </td>
                <td className="cell-link mono">{event.id}</td>
                <td className="muted">{formatTimestamp(event.created_at)}</td>
                <td>
                  <span className="event-type">
                    {formatEventType(event.event_type)}
                  </span>
                </td>
                <td className="mono">{event.actor}</td>
                <td className="wrap">{formatAuditSummary(event)}</td>
                <td className="mono muted">
                  {event.thread_id ? shortId(event.thread_id) : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  const tone = statusTone(status);
  return (
    <span className={`status-dot ${tone}`}>
      <i />
      {status.replace(/_/g, " ")}
    </span>
  );
}

function statusTone(status: string): string {
  if (status === "ok" || status === "done") return "ok";
  if (status === "error" || status === "rejected") return "danger";
  if (
    status === "waiting" ||
    status === "truncated" ||
    status === "circuit_broken" ||
    status === "no_progress"
  ) {
    return "warn";
  }
  return "neutral";
}

function TraceDetailDrawer({
  step,
  onClose,
}: {
  step: TraceStep;
  onClose: () => void;
}) {
  const llm = stepLlm(step);
  const detailEntries = Object.entries(step.detail ?? {}).filter(
    ([key]) => key !== "llm",
  );

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} aria-hidden />
      <aside
        className="drawer evidence-sheet"
        role="dialog"
        aria-label="Trace step detail"
      >
        <header className="drawer-head">
          <div className="drawer-head-text">
            <div className="drawer-kicker">Trace step</div>
            <h2 title={step.node}>{humanLabel(step.node)}</h2>
            <div className="mono muted small">
              seq {step.seq} · {shortId(step.turn_id)}
            </div>
          </div>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Close
          </button>
        </header>

        <section className="drawer-hero">
          <div className="drawer-hero-label">
            {llm?.total_tokens != null ? "Tokens" : "Status"}
          </div>
          <div
            className={`drawer-hero-value ${
              llm?.total_tokens != null
                ? ""
                : `status-tone ${statusTone(step.status)}`
            }`}
          >
            {llm?.total_tokens != null
              ? llm.total_tokens
              : step.status.replace(/_/g, " ")}
          </div>
          <div className="drawer-hero-meta">
            {llm?.model && <span className="drawer-pill">{llm.model}</span>}
            {step.duration_ms != null && (
              <span className="drawer-pill">{step.duration_ms} ms</span>
            )}
            <span className={`status-dot ${statusTone(step.status)}`}>
              <i />
              {step.status.replace(/_/g, " ")}
            </span>
            <span className="muted small">{formatTimestamp(step.created_at)}</span>
          </div>
        </section>

        {llm && (
          <section className="drawer-section-block">
            <h3 className="drawer-section">Model usage</h3>
            <div className="drawer-fields">
              <div className="drawer-field">
                <span className="drawer-field-label">Model</span>
                <span className="drawer-field-value mono">
                  {llm.model ?? "—"}
                </span>
              </div>
              <div className="drawer-field">
                <span className="drawer-field-label">Prompt</span>
                <span className="drawer-field-value mono">
                  {llm.prompt_tokens != null ? `${llm.prompt_tokens} tokens` : "—"}
                </span>
              </div>
              <div className="drawer-field">
                <span className="drawer-field-label">Completion</span>
                <span className="drawer-field-value mono">
                  {llm.completion_tokens != null
                    ? `${llm.completion_tokens} tokens`
                    : "—"}
                </span>
              </div>
              <div className="drawer-field">
                <span className="drawer-field-label">Total</span>
                <span className="drawer-field-value mono">
                  {llm.total_tokens != null ? `${llm.total_tokens} tokens` : "—"}
                </span>
              </div>
            </div>
          </section>
        )}

        <section className="drawer-section-block">
          <h3 className="drawer-section">Run</h3>
          <div className="drawer-fields">
            <div className="drawer-field">
              <span className="drawer-field-label">Sequence</span>
              <span className="drawer-field-value mono">{step.seq}</span>
            </div>
            <div className="drawer-field">
              <span className="drawer-field-label">Node</span>
              <span className="drawer-field-value mono">{step.node}</span>
            </div>
            <div className="drawer-field">
              <span className="drawer-field-label">Turn</span>
              <span className="drawer-field-value mono">{step.turn_id}</span>
            </div>
            <div className="drawer-field">
              <span className="drawer-field-label">Recorded</span>
              <span className="drawer-field-value">
                {formatTimestamp(step.created_at)}
              </span>
            </div>
          </div>
        </section>

        <section className="drawer-section-block">
          <h3 className="drawer-section">Detail</h3>
          {detailEntries.length > 0 ? (
            <div className="drawer-fields">
              {detailEntries.map(([key, value]) => (
                <div className="drawer-field" key={key}>
                  <span className="drawer-field-label">{humanLabel(key)}</span>
                  <span className="drawer-field-value">
                    <DetailValue value={value} />
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="muted small">No detail payload for this step.</div>
          )}
        </section>
      </aside>
    </>
  );
}

function AuditDetailDrawer({
  event,
  onClose,
}: {
  event: AuditEvent;
  onClose: () => void;
}) {
  const detailEntries = Object.entries(event.detail ?? {}).filter(
    ([key]) => key !== "result",
  );
  const summary = formatAuditSummary(event);

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} aria-hidden />
      <aside
        className="drawer evidence-sheet"
        role="dialog"
        aria-label="Audit event detail"
      >
        <header className="drawer-head">
          <div className="drawer-head-text">
            <div className="drawer-kicker">Audit event</div>
            <h2 title={event.event_type}>{formatEventType(event.event_type)}</h2>
            <div className="mono muted small">#{event.id}</div>
          </div>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Close
          </button>
        </header>

        <section className="drawer-hero">
          <div className="drawer-hero-label">Summary</div>
          <div className="drawer-hero-value drawer-hero-value-sm">{summary}</div>
          <div className="drawer-hero-meta">
            <span className="drawer-pill">{event.actor.replace(/_/g, " ")}</span>
            <span className="muted small">{formatTimestamp(event.created_at)}</span>
          </div>
        </section>

        <section className="drawer-section-block">
          <h3 className="drawer-section">Record</h3>
          <div className="drawer-fields">
            <div className="drawer-field">
              <span className="drawer-field-label">Event</span>
              <span className="drawer-field-value">
                {formatEventType(event.event_type)}
              </span>
            </div>
            <div className="drawer-field">
              <span className="drawer-field-label">Actor</span>
              <span className="drawer-field-value mono">{event.actor}</span>
            </div>
            <div className="drawer-field">
              <span className="drawer-field-label">Thread</span>
              <span className="drawer-field-value mono">
                {event.thread_id ?? "—"}
              </span>
            </div>
            <div className="drawer-field">
              <span className="drawer-field-label">Recorded</span>
              <span className="drawer-field-value">
                {formatTimestamp(event.created_at)}
              </span>
            </div>
          </div>
        </section>

        {detailEntries.length > 0 && (
          <section className="drawer-section-block">
            <h3 className="drawer-section">Detail</h3>
            <div className="drawer-fields">
              {detailEntries.map(([key, value]) => (
                <div className="drawer-field" key={key}>
                  <span className="drawer-field-label">{humanLabel(key)}</span>
                  <span className="drawer-field-value">
                    <DetailValue value={value} />
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}
      </aside>
    </>
  );
}

function DetailValue({ value }: { value: unknown }) {
  if (value == null) return <>—</>;
  if (typeof value === "boolean") return <>{value ? "yes" : "no"}</>;
  if (typeof value === "number") return <span className="mono">{value}</span>;
  if (typeof value === "string") {
    return value.trim() ? <>{value}</> : <span className="muted">—</span>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="muted">none</span>;
    if (value.every((item) => typeof item === "string" || typeof item === "number")) {
      return (
        <span className="drawer-chip-list">
          {value.map((item) => (
            <span className="drawer-chip" key={String(item)}>
              {humanLabel(String(item))}
            </span>
          ))}
        </span>
      );
    }
    return <span className="mono">{JSON.stringify(value)}</span>;
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return <span className="muted">none</span>;
    return (
      <div className="drawer-nested">
        {entries.map(([k, v]) => (
          <div key={k}>
            <span className="muted">{humanLabel(k)}: </span>
            <DetailValue value={v} />
          </div>
        ))}
      </div>
    );
  }
  return <>{String(value)}</>;
}

function humanLabel(raw: string): string {
  return raw
    .replace(/[._]/g, " ")
    .replace(/\bId\b/gi, "ID")
    .replace(/\bagent\b/gi, "agent");
}

const ACTION_LABELS: Record<string, string> = {
  create_upgrade_order: "upgrade order",
  open_remediation_ticket: "remediation ticket",
  flag_device_for_replacement: "replacement flag",
  notify_employee: "employee notification",
};

/** Prefer the stored outcome text; fall back to a humanised summary line. */
function formatAuditSummary(event: AuditEvent): string {
  const result = event.detail?.result;
  if (typeof result === "string" && result.trim()) return result.trim();

  let text = event.summary;
  text = text.replace(
    /\b(create_upgrade_order|open_remediation_ticket|flag_device_for_replacement|notify_employee)\b/g,
    (match) => ACTION_LABELS[match] ?? match.replace(/_/g, " "),
  );
  text = text.replace(/\s*\(act-[a-f0-9]+\)\s*/gi, " ").replace(/\s+/g, " ").trim();
  return text;
}

function formatEventType(eventType: string): string {
  const label = eventType.replace(/_/g, " ");
  return label.charAt(0).toUpperCase() + label.slice(1);
}
