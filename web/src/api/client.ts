import type {
  AuditEvent,
  ChartData,
  Company,
  Email,
  Evidence,
  EvalReport,
  EvalTier,
  PerformedTurn,
  Ticket,
  Finding,
  ProgressStep,
  ProposedAction,
  ThreadSummary,
  TraceStep,
  TurnResult,
  WorkflowPrompt,
} from "../types";

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(formatApiDetail(body.detail, response.status));
  }
  return response.json() as Promise<T>;
}

function formatApiDetail(detail: unknown, status: number): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) =>
        typeof item === "object" && item && "msg" in item
          ? String((item as { msg: string }).msg)
          : JSON.stringify(item),
      )
      .join("; ");
  }
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return `Request failed (${status})`;
}

export const api = {
  companies: () => request<Company[]>("/companies"),

  startThread: (companyId: string, title?: string) =>
    request<{ thread_id: string; company_id: string }>("/threads", {
      method: "POST",
      body: JSON.stringify({ company_id: companyId, title }),
    }),

  sendMessage: (
    threadId: string,
    companyId: string,
    message: string,
    source: "chat" | "task" = "chat",
  ) =>
    request<TurnResult>("/messages", {
      method: "POST",
      body: JSON.stringify({
        thread_id: threadId,
        company_id: companyId,
        message,
        source,
      }),
    }),

  /**
   * Run a turn, reporting each step as it happens.
   *
   * `EventSource` cannot issue a POST, so the SSE frames are read off the
   * response body directly. Falls back to nothing special on failure — the
   * caller still gets a rejected promise, same as `sendMessage`.
   */
  streamMessage: async (
    threadId: string,
    companyId: string,
    message: string,
    onStep: (step: ProgressStep) => void,
    source: "chat" | "task" = "chat",
  ): Promise<TurnResult> => {
    const response = await fetch(`${BASE}/messages/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        thread_id: threadId,
        company_id: companyId,
        message,
        source,
      }),
    });
    if (!response.ok || !response.body) {
      const body = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(body.detail ?? `Request failed (${response.status})`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let result: TurnResult | null = null;

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Frames are separated by a blank line; a partial tail stays buffered.
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";

      for (const raw of frames) {
        const line = raw.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue; // keep-alive comment
        const event = JSON.parse(line.slice(6));

        if (event.type === "step") onStep(event as ProgressStep);
        else if (event.type === "result") result = event.result as TurnResult;
        else if (event.type === "error") throw new Error(event.detail);
      }
    }

    if (!result) throw new Error("The turn ended without returning an answer.");
    return result;
  },

  decide: (
    threadId: string,
    companyId: string,
    decisions: { action_id: string; approved: boolean; note?: string }[],
  ) =>
    request<TurnResult>("/actions/decide", {
      method: "POST",
      body: JSON.stringify({
        thread_id: threadId,
        company_id: companyId,
        decisions,
      }),
    }),

  prompts: (companyId: string) =>
    request<WorkflowPrompt[]>(
      `/prompts?company_id=${encodeURIComponent(companyId)}`,
    ),

  renderPrompt: (companyId: string, name: string, args: Record<string, string> = {}) =>
    request<{ name: string; text: string }>(
      `/prompts/${encodeURIComponent(name)}?company_id=${encodeURIComponent(companyId)}`,
      { method: "POST", body: JSON.stringify(args) },
    ),

  insights: (companyId: string, windowDays = 30) =>
    request<{ findings: Finding[]; detectors_available: string[] }>(
      `/insights?company_id=${encodeURIComponent(companyId)}&window_days=${windowDays}`,
    ),

  insightTrends: (companyId: string, windowDays = 30) =>
    request<{ charts: ChartData[] }>(
      `/insights/trends?company_id=${encodeURIComponent(companyId)}&window_days=${windowDays}`,
    ),

  pendingActions: (companyId: string) =>
    request<{ actions: ProposedAction[] }>(
      `/actions?company_id=${encodeURIComponent(companyId)}`,
    ),

  actions: (companyId: string, status?: string) => {
    const params = new URLSearchParams({ company_id: companyId });
    if (status) params.set("status", status);
    return request<{ actions: ProposedAction[] }>(`/actions?${params}`);
  },

  listThreads: (companyId: string) =>
    request<ThreadSummary[]>(`/threads?company_id=${encodeURIComponent(companyId)}`),

  /** Completed turns for a tenant — task investigations survive a refresh. */
  turns: (companyId: string, kind?: "chat" | "task") =>
    request<{ turns: PerformedTurn[] }>(
      `/turns?company_id=${encodeURIComponent(companyId)}${
        kind ? `&kind=${kind}` : ""
      }`,
    ),

  /** Emails the system sent or simulated. */
  emails: (companyId: string) =>
    request<{ emails: Email[] }>(
      `/emails?company_id=${encodeURIComponent(companyId)}`,
    ),

  /** Send (or simulate) a hand-composed email and record it. */
  sendEmail: (
    companyId: string,
    to_address: string,
    subject: string,
    body: string,
  ) =>
    request<{ email_id: string; status: string }>("/emails", {
      method: "POST",
      body: JSON.stringify({ company_id: companyId, to_address, subject, body }),
    }),

  /** Remediation tickets created by executed ticket actions. */
  tickets: (companyId: string) =>
    request<{ tickets: Ticket[] }>(
      `/tickets?company_id=${encodeURIComponent(companyId)}`,
    ),

  /** Every run this tenant has performed — what the audit page shows. */
  traces: (companyId: string) =>
    request<{ steps: TraceStep[] }>(
      `/traces?company_id=${encodeURIComponent(companyId)}`,
    ),

  /**
   * Resolve a proposal's citation ids to the readings behind them.
   *
   * Needed because the Approvals queue outlives the turn: the in-memory ledger
   * that backed those ids during the run is gone by the time anyone reviews.
   */
  evidence: (companyId: string, ids: string[]) =>
    request<{ evidence: Evidence[] }>(
      `/evidence?company_id=${encodeURIComponent(companyId)}&ids=${encodeURIComponent(ids.join(","))}`,
    ),

  trace: (threadId: string, companyId: string) =>
    request<{ steps: TraceStep[] }>(
      `/threads/${encodeURIComponent(threadId)}/trace?company_id=${encodeURIComponent(companyId)}`,
    ),

  audit: (companyId: string) =>
    request<{ events: AuditEvent[] }>(
      `/audit?company_id=${encodeURIComponent(companyId)}`,
    ),

  evalStatus: () => request<EvalReport>("/eval"),

  evalRun: (tier: EvalTier) =>
    request<EvalReport>("/eval/run", {
      method: "POST",
      body: JSON.stringify({ tier }),
    }),
};
