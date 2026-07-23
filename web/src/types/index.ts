export interface Company {
  company_id: string;
  name: string;
  device_count: number;
}

export interface Evidence {
  evidence_id: string;
  tool: string;
  device_id: string | null;
  device_label: string | null;
  snapshot_ts: string | null;
  field: string;
  value: unknown;
  detail: Record<string, unknown>;
}

export interface Claim {
  text: string;
  evidence_ids: string[];
}

export interface Finding {
  finding_type: string;
  device_id: string;
  device_label: string | null;
  company_id: string;
  severity: "low" | "medium" | "high";
  title: string;
  metrics: Record<string, unknown>;
  evidence_ids: string[];
  explanation: string | null;
}

export interface ReviewSignal {
  evidence_count: number;
  distinct_fields: string[];
  supports_action_directly: boolean;
  review_priority: "routine" | "check_carefully";
  notes: string[];
}

export interface AnswerQuality {
  claims_kept: number;
  claims_rejected: number;
  grounding_retries: number;
  tool_errors: number;
  evidence_records: number;
  degraded: boolean;
  notes: string[];
}

export interface ProposedAction {
  action_id: string;
  thread_id: string;
  company_id: string;
  action_type: string;
  target_device_id: string | null;
  target_label: string | null;
  target_employee_id: string | null;
  params: Record<string, unknown>;
  justification: string;
  evidence_ids: string[];
  status: "proposed" | "approved" | "rejected" | "executed" | "failed";
  review: ReviewSignal | null;
  created_at: string | null;
  result: string | null;
}

export interface Refusal {
  reason: string;
  message: string;
}

export type ChartType =
  | "none"
  | "data_table"
  | "bar"
  | "pie"
  | "donut"
  | "severity_distribution"
  | "trend_line"
  | "stat_tile";

export interface ChartPoint {
  label: string;
  device_id: string | null;
  value: number | string;
  unit: string | null;
  evidence_id: string | null;
  severity: string | null;
}

export interface ChartData {
  chart_type: ChartType;
  title: string;
  unit: string | null;
  points: ChartPoint[];
  table_rows: Record<string, unknown>[];
  columns: string[];
  stat_value: number | string | null;
  stat_label: string | null;
  source_evidence_ids: string[];
}

export interface TurnResult {
  thread_id: string;
  company_id: string;
  answer: string | null;
  claims: Claim[];
  evidence: Evidence[];
  findings: Finding[];
  charts: ChartData[];
  pending_actions: ProposedAction[];
  refusal: Refusal | null;
  awaiting_approval: boolean;
  quality: AnswerQuality | null;
}

export interface TraceStep {
  seq: number;
  turn_id: string;
  /** Set on the company-wide listing so a run can name its conversation. */
  thread_id?: string | null;
  node: string;
  status: string;
  detail: Record<string, unknown>;
  duration_ms: number | null;
  created_at: string;
}

export interface AuditEvent {
  id: number;
  event_type: string;
  actor: string;
  summary: string;
  detail: Record<string, unknown>;
  thread_id: string | null;
  created_at: string;
}

export interface PromptArgument {
  name: string;
  description: string | null;
  required: boolean;
}

export interface WorkflowPrompt {
  name: string;
  title: string;
  description: string | null;
  arguments: PromptArgument[];
}

/** One narrated step of a turn, streamed while it runs. */
export interface ProgressStep {
  type: "step";
  seq: number;
  node: string;
  status: string;
  phase: string;
  message: string;
  duration_ms: number | null;
}

export interface ChatEntry {
  role: "user" | "assistant";
  text: string;
  result?: TurnResult;
}

/**
 * A conversation, with enough context to choose between them in a picker.
 *
 * `title` is the thread's own title when it has one, otherwise the question
 * that opened it — an opaque `thr-` id is not something anyone recognises.
 */
export interface ThreadSummary {
  thread_id: string;
  company_id: string;
  title: string | null;
  step_count: number | null;
  last_activity: string | null;
}

/**
 * A completed turn, reloaded from the DB so the Action-performed view survives a
 * refresh. `result` is the stored snapshot; live proposal status is overlaid
 * from the actions endpoint at render time.
 */
export interface PerformedTurn {
  turn_id: string;
  thread_id: string;
  kind: string;
  question: string;
  result: TurnResult;
  created_at: string;
}

/**
 * A finding the operator has converted into an intended action, staged on the
 * Action Proposals page until they run it. Not a proposal yet — running it
 * launches the investigation; the agent chooses ticket / notify / both / none.
 */
export interface QueuedAction {
  id: string; // `${finding_type}:${device_id}` — converting twice is a no-op
  findingType: string;
  deviceId: string;
  deviceLabel: string | null;
  title: string;
  prompt: string;
}

/** A remediation ticket, created when an open_remediation_ticket action executes. */
export interface Ticket {
  ticket_id: string;
  action_id: string;
  device_id: string | null;
  device_label: string | null;
  check_id: string | null;
  note: string | null;
  status: string;
  created_at: string;
}

/** An email the system sent or simulated, from a notify action or the compose form. */
export interface Email {
  email_id: string;
  action_id: string | null;
  employee_id: string | null;
  to_address: string;
  subject: string;
  body: string;
  status: string;
  error: string | null;
  created_at: string;
}

export type EvalTier = "deterministic" | "live" | "both";

export type EvalCaseStatus =
  | "pending"
  | "running"
  | "passed"
  | "failed"
  | "skipped"
  | "error";

export interface EvalCaseResult {
  id: string;
  name: string;
  category: string;
  status: EvalCaseStatus;
  /** What this case proves — shown when the row is expanded. */
  description: string | null;
  message: string | null;
  duration_s: number | null;
}

export interface EvalCategoryResult {
  name: string;
  description: string;
  tier: "deterministic" | "live";
  passed: number;
  failed: number;
  skipped: number;
  cases: EvalCaseResult[];
}

/** Latest evaluation scorecard from GET /api/eval. */
export interface EvalReport {
  run_id: string | null;
  tier: EvalTier | null;
  status: "idle" | "running" | "completed" | "failed";
  started_at: string | null;
  finished_at: string | null;
  llm_configured: boolean;
  total_passed: number;
  total_failed: number;
  total_skipped: number;
  total_pending?: number;
  categories: EvalCategoryResult[];
  /** Flat listing of every test case — updates live while a run is in flight. */
  cases: EvalCaseResult[];
  error: string | null;
  log_tail: string[];
}
