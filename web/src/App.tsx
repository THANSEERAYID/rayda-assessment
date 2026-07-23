import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api/client";
import { ActionsPanel } from "./components/ActionsPanel";
import { ChatPanel } from "./components/ChatPanel";
import { DashboardPanel } from "./components/DashboardPanel";
import { EvidenceDrawer, type CitationsSheet } from "./components/EvidenceDrawer";
import { InsightsPanel } from "./components/InsightsPanel";
import { Sidebar, type View } from "./components/Sidebar";
import { useRoute } from "./routes";
import { findingKey, queuedActionFromFinding } from "./actions";
import { SummaryStrip } from "./components/SummaryStrip";
import { TasksPanel } from "./components/TasksPanel";
import { EmailsPanel } from "./components/EmailsPanel";
import { EvalPanel } from "./components/EvalPanel";
import { TicketsPanel } from "./components/TicketsPanel";
import { TracePanel } from "./components/TracePanel";
import {
  getActionQueue,
  getConvertedFindings,
  getSavedCompanyId,
  getSavedThreadId,
  rememberCompany,
  rememberThread,
  setActionQueue,
  setConvertedFindings,
} from "./session";
import type {
  AuditEvent,
  ChatEntry,
  Company,
  Finding,
  ProgressStep,
  PerformedTurn,
  Email,
  ProposedAction,
  QueuedAction,
  Ticket,
  TraceStep,
  TurnResult,
  WorkflowPrompt,
} from "./types";

const VIEW_TITLES: Record<View, { title: string; sub: string }> = {
  dashboard: {
    title: "Grounded Q&A",
    sub: "Fleet overview and charts from the last answer",
  },
  tasks: {
    title: "Action Proposals",
    sub: "Staged findings — the agent chooses ticket and/or notify when run",
  },
  insights: {
    title: "Insights & Trends",
    sub: "Deterministic detectors — no model in the path",
  },
  actions: {
    title: "Approvals",
    sub: "Nothing here has been carried out",
  },
  tickets: {
    title: "Tickets",
    sub: "Remediation tickets from approved actions",
  },
  emails: {
    title: "Emails",
    sub: "Messages sent from notify actions, or composed here",
  },
  trace: {
    title: "Trace & audit",
    sub: "Every step the agent took, and every guardrail decision",
  },
  eval: {
    title: "Evaluation",
    sub: "Deterministic and live agent scorecards",
  },
};

export default function App() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [companyId, setCompanyId] = useState<string>("");
  const [threadId, setThreadId] = useState<string | null>(null);
  const [view, setView] = useRoute();
  const [error, setError] = useState<string | null>(null);

  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<ProgressStep[]>([]);
  const [citations, setCitations] = useState<CitationsSheet | null>(null);
  // Synchronous lock — React state alone cannot stop a double-click that
  // lands before the re-render with busy=true.
  const inFlight = useRef(false);

  const [prompts, setPrompts] = useState<WorkflowPrompt[]>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [lastScanAt, setLastScanAt] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [actions, setActions] = useState<ProposedAction[]>([]);
  const [approvedActions, setApprovedActions] = useState<ProposedAction[]>([]);
  const [steps, setSteps] = useState<TraceStep[]>([]);
  const [taskTurns, setTaskTurns] = useState<PerformedTurn[]>([]);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [emails, setEmails] = useState<Email[]>([]);
  const [actionQueue, setActionQueueState] = useState<QueuedAction[]>([]);
  const [convertedIds, setConvertedIds] = useState<Set<string>>(new Set());
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [panelLoading, setPanelLoading] = useState(false);

  const beginWork = useCallback(() => {
    if (inFlight.current) return false;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    return true;
  }, []);

  const endWork = useCallback(() => {
    inFlight.current = false;
    setBusy(false);
  }, []);

  useEffect(() => {
    api
      .companies()
      .then((list) => {
        setCompanies(list);
        if (!list.length) return;
        const saved = getSavedCompanyId();
        const match = list.find((c) => c.company_id === saved);
        setCompanyId(match?.company_id ?? list[0].company_id);
      })
      .catch((e) => setError(e.message));
  }, []);

  // Prefer a fresh conversation for chat after load when the restored thread
  // still has open proposals — those threads may be paused at the approval gate.
  useEffect(() => {
    if (!companyId) return;
    rememberCompany(companyId);
    setEntries([]);
    setSteps([]);

    let cancelled = false;
    const savedThread = getSavedThreadId(companyId);

    (async () => {
      try {
        const [threads, pending] = await Promise.all([
          api.listThreads(companyId),
          api.actions(companyId),
        ]);
        if (cancelled) return;
        const paused = new Set(
          pending.actions.map((a) => a.thread_id).filter(Boolean),
        );
        const preferred =
          (savedThread &&
            !paused.has(savedThread) &&
            threads.find((t) => t.thread_id === savedThread)?.thread_id) ||
          threads.find((t) => !paused.has(t.thread_id))?.thread_id ||
          // All conversations may be waiting on approval (common on Globex) —
          // still bind one so Trace can mark "this conversation" and chat can
          // open a fresh thread on the next send if needed.
          (savedThread &&
            threads.find((t) => t.thread_id === savedThread)?.thread_id) ||
          threads[0]?.thread_id ||
          null;
        setThreadId(preferred);
        if (preferred) rememberThread(companyId, preferred);
      } catch (e) {
        if (!cancelled) {
          setThreadId(savedThread);
          setError((e as Error).message);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [companyId]);

  useEffect(() => {
    if (companyId && threadId) rememberThread(companyId, threadId);
  }, [companyId, threadId]);

  const loadActions = useCallback(async () => {
    try {
      const [pending, executed, approved] = await Promise.all([
        api.actions(companyId),
        api.actions(companyId, "executed"),
        api.actions(companyId, "approved"),
      ]);
      setActions(pending.actions);
      // Approved decisions execute immediately, so the history tab is mostly
      // executed rows; include any that are still mid-transition.
      const seen = new Set<string>();
      const history: ProposedAction[] = [];
      for (const row of [...approved.actions, ...executed.actions]) {
        if (seen.has(row.action_id)) continue;
        seen.add(row.action_id);
        history.push(row);
      }
      history.sort((a, b) =>
        (b.created_at ?? "").localeCompare(a.created_at ?? ""),
      );
      setApprovedActions(history);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [companyId]);

  useEffect(() => {
    if (!companyId) return;
    api.prompts(companyId).then(setPrompts).catch(() => setPrompts([]));
    api
      .turns(companyId, "task")
      .then((r) => setTaskTurns(r.turns))
      .catch(() => setTaskTurns([]));
    api
      .tickets(companyId)
      .then((r) => setTickets(r.tickets))
      .catch(() => setTickets([]));
    api
      .emails(companyId)
      .then((r) => setEmails(r.emails))
      .catch(() => setEmails([]));
    void loadActions();
  }, [companyId, loadActions]);

  // A scan belongs to the tenant it ran against, so switching company clears
  // both the findings and the timestamp rather than showing one company's
  // results under another's name.
  useEffect(() => {
    setFindings([]);
    setLastScanAt(null);
  }, [companyId]);

  // The Action-Proposals queue is per company and persisted, so it survives a
  // refresh and a company switch loads that tenant's own staged actions.
  useEffect(() => {
    if (!companyId) return;
    // Normalize on read so legacy kind-prefixed ids migrate to type:device.
    const queue = getActionQueue(companyId);
    setActionQueueState(queue);
    setActionQueue(companyId, queue);
    // Converted Insights stick across queue runs; seed from storage + queue.
    const converted = new Set([
      ...getConvertedFindings(companyId),
      ...queue.map((a) => a.id),
    ]);
    setConvertedIds(converted);
    setConvertedFindings(companyId, [...converted]);
  }, [companyId]);

  const updateQueue = useCallback(
    (next: QueuedAction[]) => {
      setActionQueueState(next);
      if (companyId) setActionQueue(companyId, next);
    },
    [companyId],
  );

  const markConverted = useCallback(
    (ids: string[]) => {
      setConvertedIds((prev) => {
        const next = new Set(prev);
        for (const id of ids) next.add(id);
        if (companyId) setConvertedFindings(companyId, [...next]);
        return next;
      });
    },
    [companyId],
  );

  const convertFindings = useCallback(
    (toConvert: Finding[]) => {
      const existing = new Set(actionQueue.map((a) => a.id));
      const added = toConvert
        .map((f) => queuedActionFromFinding(f))
        .filter((a) => !existing.has(a.id));
      if (added.length) updateQueue([...added, ...actionQueue]);
      markConverted(toConvert.map(findingKey));
    },
    [actionQueue, updateQueue, markConverted],
  );

  const removeFromQueue = useCallback(
    (id: string) => updateQueue(actionQueue.filter((a) => a.id !== id)),
    [actionQueue, updateQueue],
  );

  const clearFromQueue = useCallback(
    (ids: string[]) => {
      const drop = new Set(ids);
      updateQueue(actionQueue.filter((a) => !drop.has(a.id)));
    },
    [actionQueue, updateQueue],
  );

  const runScan = useCallback(async () => {
    if (!companyId || scanning) return;
    setScanning(true);
    try {
      const r = await api.insights(companyId);
      setFindings(r.findings);
      setLastScanAt(new Date().toISOString());
      // Drop converted markers for findings that no longer appear in the scan.
      const live = new Set(r.findings.map(findingKey));
      setConvertedIds((prev) => {
        const next = new Set([...prev].filter((id) => live.has(id)));
        setConvertedFindings(companyId, [...next]);
        return next;
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setScanning(false);
    }
  }, [companyId, scanning]);

  // Scan once per tenant, only because there is nothing to list yet. Moving
  // between pages keeps whatever is already in state, so the detectors do not
  // re-run just because a panel remounted — a rescan after that is the
  // operator's call.
  //
  // Keyed on a ref rather than on `findings.length`, because a scan that
  // legitimately finds nothing also leaves the list empty; retrying on that
  // would loop forever, as would retrying a scan that failed.
  const autoScannedFor = useRef<string | null>(null);
  useEffect(() => {
    if (!companyId || autoScannedFor.current === companyId) return;
    autoScannedFor.current = companyId;
    void runScan();
  }, [companyId, runScan]);

  // Trace and audit are both tenant-scoped: the page answers "what has the
  // agent done for this company", so it must not depend on which conversation
  // happens to be selected. Reloaded when the thread changes too, so a turn
  // just taken shows up without a refresh.
  useEffect(() => {
    if (!companyId) return;
    let cancelled = false;
    if (view === "trace") setPanelLoading(true);

    const loadTraces = () =>
      api.traces(companyId).catch(async (err) => {
        // Older API processes only expose per-thread traces — keep the page
        // usable instead of painting "Not Found" across every workspace.
        if (threadId) return api.trace(threadId, companyId);
        throw err;
      });

    Promise.all([loadTraces(), api.audit(companyId)])
      .then(([t, a]) => {
        if (cancelled) return;
        setSteps(t.steps);
        setAuditEvents(a.events);
        if (view === "trace") setError(null);
      })
      .catch((e) => {
        // Only the Trace page owns this failure — other listings must not
        // inherit a red banner from a background refresh.
        if (!cancelled && view === "trace") setError((e as Error).message);
      })
      .finally(() => {
        if (!cancelled) setPanelLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [view, companyId, threadId]);

  const ensureThread = useCallback(async () => {
    if (threadId) return threadId;
    const created = await api.startThread(companyId);
    setThreadId(created.thread_id);
    return created.thread_id;
  }, [threadId, companyId]);

  const send = async (message: string) => {
    if (!companyId) {
      setError("Select a company before asking a question.");
      return;
    }
    if (!beginWork()) return;
    setEntries((prev) => [...prev, { role: "user", text: message }]);
    setProgress([]);
    try {
      let thread = await ensureThread();
      // Each step replaces the last: the chat narrates what is happening now,
      // while the full ordered history stays in Trace & audit.
      const onStep = (step: ProgressStep) =>
        setProgress((prev) => [...prev, step]);

      let result;
      try {
        result = await api.streamMessage(thread, companyId, message, onStep);
      } catch (first) {
        // Conversation may be paused at approvals — open a fresh thread once.
        const detail = (first as Error).message || "";
        if (!/waiting on an approval|no longer active/i.test(detail)) throw first;
        const created = await api.startThread(companyId);
        thread = created.thread_id;
        setThreadId(thread);
        setProgress([]);
        result = await api.streamMessage(thread, companyId, message, onStep);
      }
      setEntries((prev) => [
        ...prev,
        { role: "assistant", text: result.answer ?? "", result },
      ]);
      if (result.pending_actions.length) await loadActions();
      // Keep Trace & audit in sync with the turn that just finished.
      void api.traces(companyId).then((t) => setSteps(t.steps)).catch(() => {});
      // A turn that produced charts is worth showing straight away.
      if (result.charts.length) setView("dashboard");
    } catch (e) {
      const messageText = (e as Error).message || "The request failed.";
      setError(messageText);
      setEntries((prev) => [
        ...prev,
        {
          role: "assistant",
          text: `I could not finish that turn. ${messageText}`,
        },
      ]);
    } finally {
      setProgress([]);
      endWork();
    }
  };

  const decide = async (
    decisions: { action_id: string; approved: boolean }[],
    thread?: string,
  ) => {
    const target = thread ?? threadId;
    if (!target) {
      throw new Error("No conversation is linked to this approval.");
    }
    if (!beginWork()) {
      throw new Error("Another request is already in progress.");
    }
    try {
      const result = await api.decide(target, companyId, decisions);
      setThreadId(target);
      setEntries((prev) => {
        const patched = patchActionStatuses(prev, result.pending_actions);
        // Still waiting on the rest of the batch — update the gate in place
        // rather than appending another assistant bubble.
        if (result.awaiting_approval) return patched;
        return [
          ...patched,
          { role: "assistant", text: result.answer ?? "", result },
        ];
      });
      await loadActions();
      void api.traces(companyId).then((t) => setSteps(t.steps)).catch(() => {});
      void api.audit(companyId).then((a) => setAuditEvents(a.events)).catch(() => {});
    } catch (e) {
      setError((e as Error).message);
      throw e;
    } finally {
      endWork();
    }
  };

  /** Approvals may span threads — decide each thread's batch in sequence. */
  // Approvals are not chat turns, so they do not take the global `beginWork`
  // lock — one decision must not freeze the whole page or block the others.
  // The only real hazard is resuming the *same* thread's suspended graph twice
  // at once, so decisions serialise per thread and run in parallel across
  // threads: a thread that proposed three actions settles them in order, while
  // a different thread's action settles at the same time.
  const threadChains = useRef<Map<string, Promise<unknown>>>(new Map());

  const onThread = useCallback(
    <T,>(thread: string, task: () => Promise<T>): Promise<T> => {
      const prev = threadChains.current.get(thread) ?? Promise.resolve();
      const next = prev.then(task, task);
      // Swallow rejection on the *chain* so one failure does not poison the
      // next decision on that thread; the caller still sees its own result.
      threadChains.current.set(
        thread,
        next.then(
          () => undefined,
          () => undefined,
        ),
      );
      return next;
    },
    [],
  );

  const applyDecision = useCallback(
    async (thread: string, decisions: { action_id: string; approved: boolean }[]) => {
      const result = await api.decide(thread, companyId, decisions);
      // Reflect the outcome in any open chat gate for this thread, without
      // moving the chat to it — an approval is not a change of conversation.
      setEntries((prev) => patchActionStatuses(prev, result.pending_actions));
      await loadActions();
      void api.traces(companyId).then((t) => setSteps(t.steps)).catch(() => {});
      void api.audit(companyId).then((a) => setAuditEvents(a.events)).catch(() => {});
      // Approving a remediation-ticket action creates a ticket — pick it up.
      void api.tickets(companyId).then((r) => setTickets(r.tickets)).catch(() => {});
      void api.emails(companyId).then((r) => setEmails(r.emails)).catch(() => {});
      return result;
    },
    [companyId, loadActions],
  );

  const decideAcrossThreads = async (
    items: { thread_id: string; action_id: string; approved: boolean }[],
  ) => {
    if (!items.length) return;
    const byThread = items.reduce<
      Record<string, { action_id: string; approved: boolean }[]>
    >((acc, item) => {
      (acc[item.thread_id] ??= []).push({
        action_id: item.action_id,
        approved: item.approved,
      });
      return acc;
    }, {});

    const results = await Promise.allSettled(
      Object.entries(byThread).map(([thread, decisions]) =>
        onThread(thread, () => applyDecision(thread, decisions)),
      ),
    );
    const failure = results.find((r) => r.status === "rejected");
    if (failure && failure.status === "rejected") {
      const message = (failure.reason as Error).message;
      setError(message);
      throw new Error(message);
    }
  };

  const runPrompt = async (prompt: WorkflowPrompt) => {
    if (inFlight.current) return;
    const args: Record<string, string> = {};
    for (const argument of prompt.arguments.filter((a) => a.required)) {
      const value = window.prompt(argument.description ?? `Value for ${argument.name}`);
      if (!value) return; // cancelled
      args[argument.name] = value;
    }
    if (!beginWork()) return;
    try {
      const { text } = await api.renderPrompt(companyId, prompt.name, args);
      // send() also takes the lock — release first so it can claim it.
      endWork();
      await send(text);
    } catch (e) {
      setError((e as Error).message);
      endWork();
    }
  };

  const askAboutDevice = (deviceId: string) => {
    if (inFlight.current) return;
    void send(`Show me full detail for device ${deviceId}.`);
  };

  const changeCompany = (id: string) => {
    if (inFlight.current) return;
    setCompanyId(id);
  };

  const lastResult = [...entries].reverse().find((e) => e.role === "assistant")?.result;
  const company = companies.find((c) => c.company_id === companyId);
  const heading = VIEW_TITLES[view];
  const taskCount = actionQueue.length;

  // A task card starts an independent investigation, kept separate from the
  // chat: its own thread, run in the background, and the result is returned to
  // the card rather than appended to the conversation. Each is a fresh thread,
  // so several run at once and the chat stays free to use meanwhile.
  const runTask = useCallback(
    async (
      prompt: string,
      onStep?: (step: ProgressStep) => void,
    ): Promise<TurnResult | null> => {
      if (!companyId) {
        setError("Select a company before starting an investigation.");
        return null;
      }
      setError(null);
      try {
        // A dedicated thread: two turns must never share one graph, and these
        // run concurrently by design. Streamed so the card can show its own
        // progress live — each investigation's steps route to its own card
        // through this callback, never into the chat.
        const created = await api.startThread(companyId);
        const result = await api.streamMessage(
          created.thread_id,
          companyId,
          prompt,
          onStep ?? (() => undefined),
          "task",
        );
        if (result.pending_actions.length) void loadActions();
        void api.traces(companyId).then((t) => setSteps(t.steps)).catch(() => {});
        // The turn is now persisted; refresh the durable performed list.
        void api
          .turns(companyId, "task")
          .then((r) => setTaskTurns(r.turns))
          .catch(() => {});
        return result;
      } catch (e) {
        setError((e as Error).message || "The investigation failed.");
        return null;
      }
    },
    [companyId, loadActions],
  );

  return (
    <div className={`shell${busy ? " is-busy" : ""}${view !== "dashboard" ? " no-agent" : ""}`}>
      <Sidebar
        companies={companies}
        companyId={companyId}
        onCompanyChange={changeCompany}
        view={view}
        onViewChange={setView}
        pendingCount={actions.length}
        findingCount={findings.length}
        taskCount={taskCount || undefined}
        ticketCount={tickets.length || undefined}
        emailCount={emails.length || undefined}
        threadId={threadId}
        busy={busy}
      />

      <main className="main">
        <header className="topbar">
          <h1>{heading.title}</h1>
          <span className="sub">{heading.sub}</span>
          <div className="spacer" />
        </header>

        <SummaryStrip
          view={view}
          company={company}
          findings={findings}
          scanned={lastScanAt !== null}
          pending={actions}
          steps={steps}
          auditEvents={auditEvents}
        />

        <div className="workspace">
          {error && <div className="error-banner">{error}</div>}

          {view === "dashboard" && (
            <DashboardPanel
              result={lastResult}
              busy={busy}
              onSelectDevice={askAboutDevice}
            />
          )}
          {view === "tasks" && (
            <TasksPanel
              queue={actionQueue}
              pending={actions}
              performed={taskTurns}
              onRunTask={runTask}
              onRemoveFromQueue={removeFromQueue}
              onClearQueue={clearFromQueue}
              onShowCitations={(items, focusId) => setCitations({ items, focusId })}
              onDecide={decideAcrossThreads}
            />
          )}
          {view === "insights" && (
            <InsightsPanel
              findings={findings}
              loading={panelLoading}
              scanning={scanning}
              lastScanAt={lastScanAt}
              convertedIds={convertedIds}
              onRunScan={runScan}
              onConvert={(f) => convertFindings([f])}
              onConvertAll={(fs) => convertFindings(fs)}
            />
          )}
          {view === "actions" && (
            <ActionsPanel
              companyId={companyId}
              pending={actions}
              approved={approvedActions}
              loading={panelLoading}
              onDecide={decideAcrossThreads}
            />
          )}
          {view === "tickets" && (
            <TicketsPanel tickets={tickets} loading={panelLoading} />
          )}
          {view === "emails" && (
            <EmailsPanel emails={emails} loading={panelLoading} />
          )}
          {view === "trace" && (
            <TracePanel
              companyId={companyId}
              steps={steps}
              events={auditEvents}
              threadId={threadId}
              loading={panelLoading}
            />
          )}
          {view === "eval" && <EvalPanel />}
        </div>
      </main>

      {view === "dashboard" && (
      <ChatPanel
        entries={entries}
        busy={busy}
        progress={progress}
        prompts={prompts}
        onSend={send}
        onRunPrompt={runPrompt}
        onShowCitations={(items, focusId) => setCitations({ items, focusId })}
        onDecide={(decisions) => {
          void decide(decisions).catch(() => {
            /* error already surfaced via setError */
          });
        }}
      />
      )}

      <EvidenceDrawer sheet={citations} onClose={() => setCitations(null)} />
    </div>
  );
}

/** Stamp decide results onto earlier chat gates so Approved replaces the buttons. */
function patchActionStatuses(
  entries: ChatEntry[],
  decided: ProposedAction[],
): ChatEntry[] {
  if (!decided.length) return entries;
  const byId = new Map(decided.map((a) => [a.action_id, a]));
  return entries.map((entry) => {
    const pending = entry.result?.pending_actions;
    if (!pending?.length) return entry;
    let changed = false;
    const nextPending = pending.map((action) => {
      const update = byId.get(action.action_id);
      if (!update) return action;
      changed = true;
      return {
        ...action,
        status: update.status,
        result: update.result ?? action.result,
      };
    });
    if (!changed || !entry.result) return entry;
    const stillOpen = nextPending.some((a) => a.status === "proposed");
    return {
      ...entry,
      result: {
        ...entry.result,
        pending_actions: nextPending,
        awaiting_approval: stillOpen,
      },
    };
  });
}
