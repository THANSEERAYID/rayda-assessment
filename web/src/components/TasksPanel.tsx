import { useState } from "react";
import { AnswerText, TurnDetails } from "./ChatPanel";
import { bulkTaskPrompt, taskPrompt } from "../actions";
import { formatTimestamp } from "../format";
import type {
  Evidence,
  PerformedTurn,
  ProgressStep,
  ProposedAction,
  QueuedAction,
  TurnResult,
} from "../types";

/**
 * The Action-Proposals queue.
 *
 * Nothing is auto-listed here — the queue is filled by converting findings on
 * the Insights page. Running a card asks the agent to decide ticket / notify /
 * both / neither from the telemetry. Live steps stay on the card, out of chat.
 */
const BULK_KEY = "__bulk_all__";

export function TasksPanel({
  queue,
  pending,
  performed,
  onRunTask,
  onRemoveFromQueue,
  onClearQueue,
  onShowCitations,
  onDecide,
}: {
  queue: QueuedAction[];
  pending: ProposedAction[];
  performed: PerformedTurn[];
  onRunTask: (
    prompt: string,
    onStep?: (step: ProgressStep) => void,
  ) => Promise<TurnResult | null>;
  onRemoveFromQueue: (id: string) => void;
  onClearQueue: (ids: string[]) => void;
  onShowCitations: (items: Evidence[], focusId?: string) => void;
  onDecide: (
    items: { thread_id: string; action_id: string; approved: boolean }[],
  ) => Promise<void>;
}) {
  const [tab, setTab] = useState<"todo" | "performed">("todo");
  const [running, setRunning] = useState<Set<string>>(new Set());
  const [progress, setProgress] = useState<Record<string, ProgressStep[]>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const runCard = async (task: QueuedAction) => {
    if (running.has(task.id)) return;
    setRunning((prev) => new Set(prev).add(task.id));
    setProgress((prev) => ({ ...prev, [task.id]: [] }));
    setExpanded((prev) => new Set(prev).add(task.id));
    try {
      const result = await onRunTask(taskPrompt(task), (step) =>
        setProgress((prev) => ({
          ...prev,
          [task.id]: [...(prev[task.id] ?? []), step],
        })),
      );
      if (result) {
        onRemoveFromQueue(task.id);
        setTab("performed");
      }
    } finally {
      setRunning((prev) => {
        const next = new Set(prev);
        next.delete(task.id);
        return next;
      });
      setProgress((prev) => {
        const next = { ...prev };
        delete next[task.id];
        return next;
      });
    }
  };

  /** One turn over every staged finding — agent chooses actions per device. */
  const runAll = async () => {
    if (queue.length === 0 || running.has(BULK_KEY)) return;
    const ids = queue.map((t) => t.id);
    setRunning((prev) => new Set(prev).add(BULK_KEY));
    setProgress((prev) => ({ ...prev, [BULK_KEY]: [] }));
    setExpanded((prev) => new Set(prev).add(BULK_KEY));
    try {
      const result = await onRunTask(bulkTaskPrompt(queue), (step) =>
        setProgress((prev) => ({
          ...prev,
          [BULK_KEY]: [...(prev[BULK_KEY] ?? []), step],
        })),
      );
      if (result) {
        onClearQueue(ids);
        setTab("performed");
      }
    } finally {
      setRunning((prev) => {
        const next = new Set(prev);
        next.delete(BULK_KEY);
        return next;
      });
      setProgress((prev) => {
        const next = { ...prev };
        delete next[BULK_KEY];
        return next;
      });
    }
  };

  const toggleExpanded = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const renderCard = (task: QueuedAction) => {
    if (!running.has(task.id)) {
      return (
        <div key={task.id} className="task-card queued">
          <div className="task-card-body">
            <div className="task-card-title">{task.title}</div>
            <div className="task-card-desc">{task.prompt}</div>
          </div>
          <div className="task-card-actions">
            <button
              type="button"
              className="task-run"
              onClick={() => void runCard(task)}
            >
              <span className="task-run-icon" aria-hidden>
                ▶
              </span>
              Run
            </button>
            <button
              type="button"
              className="task-remove"
              aria-label="Remove from queue"
              title="Remove from queue"
              onClick={() => onRemoveFromQueue(task.id)}
            >
              <span aria-hidden>✕</span>
            </button>
          </div>
        </div>
      );
    }
    const isOpen = expanded.has(task.id);
    const steps = progress[task.id] ?? [];
    return (
      <div
        key={task.id}
        className={`performed-card is-live ${isOpen ? "open" : ""}`}
      >
        <button
          type="button"
          className="performed-head"
          aria-expanded={isOpen}
          onClick={() => toggleExpanded(task.id)}
        >
          <span
            className={`performed-caret ${isOpen ? "open" : ""}`}
            aria-hidden
          />
          <span className="task-card-body">
            <span className="task-card-title">{task.title}</span>
            <span className="task-card-desc">
              {steps.length ? steps[steps.length - 1].message : "Starting…"}
            </span>
          </span>
          <span className="task-spinner" aria-label="Working" />
        </button>
        {isOpen && (
          <div className="performed-detail">
            <LiveSteps steps={steps} />
          </div>
        )}
      </div>
    );
  };

  const decideFor = (turn: PerformedTurn) =>
    async (decisions: { action_id: string; approved: boolean }[]) => {
      const items = decisions.map((d) => {
        const action = turn.result.pending_actions.find(
          (a) => a.action_id === d.action_id,
        );
        return {
          thread_id: action?.thread_id ?? turn.thread_id,
          action_id: d.action_id,
          approved: d.approved,
        };
      });
      await onDecide(items);
    };

  const resultWithLiveActions = (turn: PerformedTurn) => {
    const live = pending.filter((a) => a.thread_id === turn.thread_id);
    return {
      ...turn.result,
      pending_actions: live,
      awaiting_approval: live.length > 0,
    };
  };

  const bulkOn = running.has(BULK_KEY);
  const bulkOpen = expanded.has(BULK_KEY);
  const bulkSteps = progress[BULK_KEY] ?? [];

  return (
    <div className="tasks-page">
      <div className="tasks-hero">
        <h2>Action proposals</h2>
        <p className="tasks-lede">
          Findings staged from Insights. When you run them, the agent decides
          whether to raise a ticket, notify the user, both, or neither — nothing
          executes without your approval.
        </p>
      </div>

      <div className="module-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "todo"}
          className={`module-tab ${tab === "todo" ? "on" : ""}`}
          onClick={() => setTab("todo")}
        >
          To do
          <span className="module-tab-count">{queue.length}</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "performed"}
          className={`module-tab ${tab === "performed" ? "on" : ""}`}
          onClick={() => setTab("performed")}
        >
          Action performed
          {performed.length > 0 && (
            <span className="module-tab-count">{performed.length}</span>
          )}
        </button>
      </div>

      {tab === "todo" ? (
        queue.length === 0 ? (
          <div className="empty">
            <strong>Nothing staged</strong>
            <div className="small" style={{ marginTop: 6 }}>
              On Insights &amp; Trends, use <em>Convert to action</em> — the agent
              chooses ticket and/or notify when you run it here.
            </div>
          </div>
        ) : (
          <section className="tasks-category">
            <div className="queue-bulk-bar">
              <span className="tasks-cat-label">Staged · {queue.length}</span>
              <button
                type="button"
                className="btn btn-primary"
                disabled={bulkOn}
                onClick={() => void runAll()}
              >
                {bulkOn ? "Running…" : `Run all ${queue.length} in one trace`}
              </button>
            </div>

            {bulkOn && (
              <div
                className={`performed-card is-live bulk-run-card ${bulkOpen ? "open" : ""}`}
              >
                <button
                  type="button"
                  className="performed-head"
                  aria-expanded={bulkOpen}
                  onClick={() => toggleExpanded(BULK_KEY)}
                >
                  <span
                    className={`performed-caret ${bulkOpen ? "open" : ""}`}
                    aria-hidden
                  />
                  <span className="task-card-body">
                    <span className="task-card-title">
                      Running {queue.length} as one trace
                    </span>
                    <span className="task-card-desc">
                      {bulkSteps.length
                        ? bulkSteps[bulkSteps.length - 1].message
                        : "Starting…"}
                    </span>
                  </span>
                  <span className="task-spinner" aria-label="Working" />
                </button>
                {bulkOpen && (
                  <div className="performed-detail">
                    <LiveSteps steps={bulkSteps} />
                  </div>
                )}
              </div>
            )}

            <div className="tasks-list">{queue.map(renderCard)}</div>
          </section>
        )
      ) : performed.length === 0 ? (
        <div className="empty">
          <strong>Nothing performed yet</strong>
          <div className="small" style={{ marginTop: 6 }}>
            Run a proposal from the To do tab and it will appear here once done.
          </div>
        </div>
      ) : (
        <section className="tasks-category">
          <div className="tasks-list">
            {performed.map((turn) => {
              const isOpen = expanded.has(turn.turn_id);
              const result = resultWithLiveActions(turn);
              const proposalCount = result.pending_actions.length;
              const headline = performedHeadline(turn, result);
              return (
                <div
                  key={turn.turn_id}
                  className={`performed-card ${isOpen ? "open" : ""}`}
                >
                  <button
                    type="button"
                    className="performed-head"
                    aria-expanded={isOpen}
                    onClick={() => toggleExpanded(turn.turn_id)}
                  >
                    <span
                      className={`performed-caret ${isOpen ? "open" : ""}`}
                      aria-hidden
                    />
                    <span className="task-card-body">
                      <span className="task-card-title performed-title">
                        {headline}
                      </span>
                      <span className="task-card-desc performed-meta">
                        Performed {formatTimestamp(turn.created_at)}
                        {proposalCount > 0 &&
                          ` · ${proposalCount} action${proposalCount === 1 ? "" : "s"} awaiting approval`}
                      </span>
                    </span>
                    <span className="task-tag done">done</span>
                  </button>

                  {isOpen && (
                    <div className="performed-detail">
                      {result.refusal ? (
                        <div className="refusal">
                          <div className="reason">
                            {result.refusal.reason.replace(/_/g, " ")}
                          </div>
                          <div className="text">{result.refusal.message}</div>
                        </div>
                      ) : (
                        <>
                          {turn.question.trim() &&
                            oneLine(turn.question) !== headline && (
                              <div className="performed-prompt muted small">
                                {turn.question}
                              </div>
                            )}
                          <AnswerText
                            text={result.answer ?? ""}
                            result={result}
                          />
                          <TurnDetails
                            result={result}
                            busy={false}
                            onShowCitations={onShowCitations}
                            onDecide={decideFor(turn)}
                          />
                        </>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}

function LiveSteps({ steps }: { steps: ProgressStep[] }) {
  if (steps.length === 0) {
    return (
      <div className="muted small">Working out what the question needs…</div>
    );
  }
  return (
    <ol className="live-steps">
      {steps.map((step, index) => {
        const done = index < steps.length - 1;
        return (
          <li
            key={`${step.seq}-${step.node}`}
            className={done ? "done" : "active"}
          >
            <span className="live-step-mark" aria-hidden>
              {done ? "✓" : "•"}
            </span>
            <span className={`working-phase ${step.phase}`}>{step.phase}</span>
            <span className="live-step-text">{step.message}</span>
          </li>
        );
      })}
    </ol>
  );
}

/** Collapse whitespace and keep a single short line for the closed card. */
function oneLine(text: string, max = 140): string {
  const line = text.replace(/\s+/g, " ").trim();
  if (!line) return "";
  if (line.length <= max) return line;
  return `${line.slice(0, max - 1).trimEnd()}…`;
}

/** One-line card title: prefer the answer, fall back to the run prompt. */
function performedHeadline(
  turn: PerformedTurn,
  result: TurnResult,
): string {
  if (result.refusal?.message) return oneLine(result.refusal.message);
  if (result.answer?.trim()) return oneLine(result.answer);
  return oneLine(turn.question) || "Action performed";
}
