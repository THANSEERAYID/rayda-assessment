import { useEffect, useRef, useState } from "react";
import { AnswerQualityBlock } from "./ReviewSignalBlock";
import type {
  ChatEntry,
  Evidence,
  ProgressStep,
  ProposedAction,
  TurnResult,
  WorkflowPrompt,
} from "../types";

/**
 * The agent rail — always present rather than behind navigation, because the
 * conversation is the product.
 *
 * One agent across every page, not a differently-branded one per workspace. The
 * conversation was always shared — the same thread and the same history — so
 * relabelling it "Insights agent" on one page and "Approvals agent" on the next
 * implied a handover that never happened, and made the starter prompts look
 * like the only questions that page would accept. It answers the same way
 * wherever you are, so it now says so.
 *
 * Every assistant turn renders its claims with clickable citations, so the
 * evidence behind a statement is one click away rather than something the
 * reader has to take on trust.
 */
export function ChatPanel({
  entries,
  busy,
  progress,
  prompts,
  onSend,
  onRunPrompt,
  onShowCitations,
  onDecide,
}: {
  entries: ChatEntry[];
  busy: boolean;
  progress: ProgressStep[];
  prompts: WorkflowPrompt[];
  onSend: (message: string) => void;
  onRunPrompt: (prompt: WorkflowPrompt) => void;
  onShowCitations: (items: Evidence[], focusId?: string) => void;
  onDecide: (decisions: { action_id: string; approved: boolean }[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const [elapsedSec, setElapsedSec] = useState(0);
  const feedRef = useRef<HTMLDivElement>(null);

  // Follow the conversation as it grows, the way a chat is expected to behave.
  useEffect(() => {
    feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight, behavior: "smooth" });
  }, [entries.length, busy, elapsedSec]);

  useEffect(() => {
    if (!busy) {
      setElapsedSec(0);
      return;
    }
    setElapsedSec(0);
    const started = Date.now();
    const id = window.setInterval(() => {
      setElapsedSec(Math.floor((Date.now() - started) / 1000));
    }, 1000);
    return () => window.clearInterval(id);
  }, [busy]);

  const submit = () => {
    const text = draft.trim();
    if (!text || busy) return;
    onSend(text);
    setDraft("");
  };


  return (
    <aside className="agent">
      <div className="agent-head">
        <div className="agent-title">
          <span className="orb" />
          <h2>Fleet Copilot</h2>
        </div>
        <div className="agent-sub">
          Grounded in telemetry · actions need approval
        </div>
      </div>

      <div className="feed" ref={feedRef}>
        {entries.length === 0 && (
          <>
            <div className="starter-group">
              <div className="starter-label">Grounded Q&amp;A</div>
              <div className="suggestion-chips">
                {GROUNDED_QA.map((question) => (
                  <button
                    key={question}
                    className="suggestion-chip"
                    onClick={() => onSend(question)}
                    disabled={busy}
                  >
                    {question}
                  </button>
                ))}
              </div>
            </div>

            {prompts.length > 0 && (
              <div className="starter-group">
                <div className="starter-label">Workflows</div>
                <div className="suggestion-chips">
                  {prompts.map((prompt) => (
                    <button
                      key={prompt.name}
                      className="suggestion-chip"
                      title={prompt.description ?? undefined}
                      onClick={() => onRunPrompt(prompt)}
                      disabled={busy}
                    >
                      {prompt.title}
                      {prompt.arguments.some((a) => a.required) && "…"}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        {entries.map((entry, index) => (
          <div key={index} className={`bubble ${entry.role}`}>
            <div className="role">{entry.role === "user" ? "You" : "Copilot"}</div>
            {entry.role === "assistant" && entry.result?.refusal ? (
              <div className="refusal">
                <div className="reason">
                  {entry.result.refusal.reason.replace(/_/g, " ")}
                </div>
                <div className="text">{entry.result.refusal.message}</div>
              </div>
            ) : (
              <AnswerText text={entry.text} result={entry.result} />
            )}

            {entry.result && (
              <TurnDetails
                result={entry.result}
                busy={busy}
                onShowCitations={onShowCitations}
                onDecide={onDecide}
              />
            )}
          </div>
        ))}

        {busy && (
          <div className="working-status" role="status" aria-live="polite">
            <div className="working-head">
              <Spinner />
              <strong className="working-now">
                {progress.length > 0
                  ? progress[progress.length - 1].message
                  : "Working out what the question needs"}
              </strong>
              <span className="working-elapsed mono">{elapsedSec}s</span>
            </div>

            {/* Completed steps stay visible so the reader can see the route
                taken, not just where it currently is. */}
            {progress.length > 1 && (
              <ol className="working-steps">
                {progress.slice(0, -1).map((step) => (
                  <li key={`${step.seq}-${step.node}`}>
                    <span className="working-tick" aria-hidden>
                      ✓
                    </span>
                    <span className={`working-phase ${step.phase}`}>
                      {step.phase}
                    </span>
                    <span className="working-step-text">{step.message}</span>
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}
      </div>

      <div className="composer">
        <div className="composer-box">
          <textarea
            value={draft}
            placeholder={
              busy
                ? "Waiting for the current reply…"
                : "Ask about this company's fleet…"
            }
            disabled={busy}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
          />
          <button
            className="composer-send"
            onClick={submit}
            disabled={busy || !draft.trim()}
            aria-label="Send"
            title="Send"
          >
            ↑
          </button>
        </div>
      </div>
    </aside>
  );
}

/**
 * The three questions the assessment is judged on, one click away.
 *
 * Deliberately not named after a company — the tenant is bound server-side from
 * the sidebar selection, so hardcoding "at Acme" would put the wrong name in the
 * transcript the moment someone switches to Globex, while the answer correctly
 * described whichever company was selected.
 */
const GROUNDED_QA = [
  "Which devices are low on disk space?",
  "Show me laptops failing high-severity compliance checks.",
  "How many devices are running an OS older than macOS 15?",
];

export function TurnDetails({
  result,
  busy,
  onShowCitations,
  onDecide,
}: {
  result: TurnResult;
  busy: boolean;
  onShowCitations: (items: Evidence[], focusId?: string) => void;
  onDecide: (decisions: { action_id: string; approved: boolean }[]) => void;
}) {
  const byId = new Map(result.evidence.map((e) => [e.evidence_id, e]));

  return (
    <>
      {result.claims.length > 0 && (
        <div className="claims">
          <div className="small muted" style={{ marginBottom: 7 }}>
            Click Citations to open every reading behind that statement.
          </div>
          <ul className="claim-list">
            {result.claims.map((claim, index) => (
              <li key={index} className="claim">
                <span className="claim-text">{claim.text}</span>
                <CitationChips
                  ids={claim.evidence_ids}
                  byId={byId}
                  onShowCitations={onShowCitations}
                />
              </li>
            ))}
          </ul>
        </div>
      )}

      {result.pending_actions.length > 0 &&
        (result.awaiting_approval ||
          result.pending_actions.some((a) => a.status !== "proposed")) && (
        <ApprovalRequest
          actions={result.pending_actions}
          busy={busy}
          byId={byId}
          onShowCitations={onShowCitations}
          onDecide={onDecide}
        />
      )}

      <AnswerQualityBlock quality={result.quality} />
    </>
  );
}

/**
 * The frames morph rather than rotate, so the shape reads as "thinking" instead
 * of "downloading" — a spinning ring implies a measurable job with an end, and
 * an agent turn is neither.
 */
const SPINNER_FRAMES = ["·", "✢", "✳", "∗", "✻", "✽", "✻", "∗", "✳", "✢"];

/**
 * Its own component because it ticks ~9× a second; keeping the state here means
 * each frame re-renders one span rather than the whole transcript above it.
 */
function Spinner() {
  const [frame, setFrame] = useState(0);
  const [reduced] = useState(
    () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false,
  );

  useEffect(() => {
    if (reduced) return;
    const id = window.setInterval(() => setFrame((f) => f + 1), 110);
    return () => window.clearInterval(id);
  }, [reduced]);

  return (
    <span className="working-spinner" aria-hidden>
      {reduced ? "✻" : SPINNER_FRAMES[frame % SPINNER_FRAMES.length]}
    </span>
  );
}

/**
 * The answer, minus the sentences the cited claims below already state.
 *
 * The grounder writes the same facts twice by design: once as readable prose,
 * once as claims carrying the evidence ids. Rendering both verbatim tells the
 * reader "acme-macbook-4 has 2% free" and then immediately tells them again, so
 * the bubble reads as padding. The prose keeps whatever the claims do not cover
 * — the framing sentence, the closing recommendation — and the specifics live
 * once, in the cited bullets, where the evidence is attached to them.
 */
export function AnswerText({ text, result }: { text: string; result?: TurnResult | null }) {
  const claims = result?.claims ?? [];
  if (claims.length === 0) return <div className="text">{text}</div>;

  const kept = splitSentences(text).filter(
    (sentence) => !claims.some((claim) => statesTheSame(sentence, claim.text)),
  );
  if (kept.length === 0) return null;
  return <div className="text">{kept.join(" ")}</div>;
}

function splitSentences(text: string): string[] {
  // Split on sentence punctuation followed by a space, so a decimal like
  // "2.59%" stays intact.
  return text
    .split(/(?<=[.!?])\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Identifiers like `acme-macbook-4`, whose trailing digit is not a figure. */
const _IDENTIFIER = /[A-Za-z]+(?:-[A-Za-z0-9]+)+/g;
const _NUMBER = /\d+(?:\.\d+)?/g;

/**
 * Two sentences state the same fact when they name the same device and quote
 * the same figure.
 *
 * Wording is not comparable — "is nearly out of disk with only 2% free" and
 * "has 2% free disk space" are the same statement — but a device and a
 * measurement together are specific enough that a match is not a coincidence,
 * and requiring both means a sentence about a *different* device survives.
 */
function statesTheSame(a: string, b: string): boolean {
  const devicesA = new Set(a.toLowerCase().match(_IDENTIFIER) ?? []);
  const devicesB = new Set(b.toLowerCase().match(_IDENTIFIER) ?? []);
  const sharedDevice = [...devicesA].some((d) => devicesB.has(d));
  if (!sharedDevice) return false;

  const figures = (text: string) =>
    new Set(
      // Identifiers go first: the "4" in acme-macbook-4 is a name, not a
      // reading, and would otherwise match anything.
      (text.replace(_IDENTIFIER, " ").match(_NUMBER) ?? []).filter(
        (n) => !(Number(n) >= 1900 && Number(n) <= 2100), // years, not readings
      ),
    );
  const figuresA = figures(a);
  const figuresB = figures(b);
  return [...figuresA].some((n) => figuresB.has(n));
}

/**
 * One chip per claim that opens the full citation list in the side sheet.
 * Individual readings are shown there, not as separate chips in chat.
 */
function CitationChips({
  ids,
  byId,
  onShowCitations,
}: {
  ids: string[];
  byId: Map<string, Evidence>;
  onShowCitations: (items: Evidence[], focusId?: string) => void;
}) {
  const items = ids
    .map((id) => byId.get(id))
    .filter((e): e is Evidence => e != null);
  if (items.length === 0) return null;

  const n = items.length;
  return (
    <span className="citation-chips">
      <button
        type="button"
        className="chip citation-chip"
        title={`Open ${n} cited reading${n === 1 ? "" : "s"}`}
        onClick={() => onShowCitations(items)}
      >
        {/* <span className="citation-chip-n">{n}</span> */}
        {n === 1 ? "Citation" : "Citations"}
      </button>
    </span>
  );
}

/**
 * The approval gate.
 *
 * Each proposal leads with the device it would touch, because that is what the
 * approver is actually deciding about — the action type repeats across every
 * card in a batch and so carries almost no information on its own. Everything
 * needed to say yes or no sits in the collapsed card; the supporting detail
 * (full reasoning, parameters, the readings themselves) opens on demand so a
 * three-proposal batch stays readable in a narrow rail.
 */
function ApprovalRequest({
  actions,
  busy,
  byId,
  onShowCitations,
  onDecide,
}: {
  actions: ProposedAction[];
  busy: boolean;
  byId: Map<string, Evidence>;
  onShowCitations: (items: Evidence[], focusId?: string) => void;
  onDecide: (decisions: { action_id: string; approved: boolean }[]) => void;
}) {
  // Optimistic overrides so Approve/Reject flip to a verdict before the round-trip.
  const [localStatus, setLocalStatus] = useState<
    Partial<Record<string, ProposedAction["status"]>>
  >({});

  // Drop overrides once the round-trip finishes; parent status (or a failed
  // decide leaving status as proposed) is then the source of truth.
  useEffect(() => {
    if (!busy) setLocalStatus({});
  }, [busy]);

  const statusOf = (action: ProposedAction): ProposedAction["status"] =>
    localStatus[action.action_id] ?? action.status;

  const open = actions.filter((a) => statusOf(a) === "proposed");
  const decided = actions.length - open.length;

  const decideOpen = (approved: boolean) => {
    if (!open.length) return;
    setLocalStatus((prev) => {
      const next = { ...prev };
      for (const action of open) {
        next[action.action_id] = approved ? "approved" : "rejected";
      }
      return next;
    });
    onDecide(open.map((a) => ({ action_id: a.action_id, approved })));
  };

  const decideOne = (actionId: string, approved: boolean) => {
    setLocalStatus((prev) => ({
      ...prev,
      [actionId]: approved ? "approved" : "rejected",
    }));
    onDecide([{ action_id: actionId, approved }]);
  };

  const heading =
    open.length === 0
      ? `✓ ${actions.length} action${actions.length === 1 ? "" : "s"} decided`
      : decided === 0
        ? `⚠ ${open.length} action${open.length > 1 ? "s need" : " needs"} your approval`
        : `⚠ ${open.length} of ${actions.length} still need${
            open.length === 1 ? "s" : ""
          } approval`;

  const sub =
    open.length === 0
      ? "Every proposal in this batch has a verdict."
      : decided === 0
        ? "Nothing has been carried out. Each proposal cites the telemetry behind it."
        : `${decided} decided · ${open.length} still waiting. Remaining proposals have not run.`;

  return (
    <div className={`gate${open.length === 0 ? " gate-done" : ""}`}>
      <div className="gate-head">
        <div className="gate-headings">
          <div className="gt">{heading}</div>
          <div className="gate-sub">{sub}</div>
        </div>
        {open.length > 1 && (
          <div className="gate-bulk">
            <button
              className="btn btn-no btn-sm"
              disabled={busy}
              onClick={() => decideOpen(false)}
            >
              Reject all
            </button>
            <button
              className="btn btn-ok btn-sm"
              disabled={busy}
              onClick={() => decideOpen(true)}
            >
              Approve all {open.length}
            </button>
          </div>
        )}
      </div>

      <div className="proposals">
        {actions.map((action) => (
          <ProposalCard
            key={action.action_id}
            action={action}
            status={statusOf(action)}
            busy={busy}
            byId={byId}
            onShowCitations={onShowCitations}
            onDecide={decideOne}
          />
        ))}
      </div>
    </div>
  );
}

function ProposalCard({
  action,
  status,
  busy,
  byId,
  onShowCitations,
  onDecide,
}: {
  action: ProposedAction;
  status: ProposedAction["status"];
  busy: boolean;
  byId: Map<string, Evidence>;
  onShowCitations: (items: Evidence[], focusId?: string) => void;
  onDecide: (actionId: string, approved: boolean) => void;
}) {
  const [open, setOpen] = useState(false);

  const target =
    action.target_label ??
    action.target_device_id ??
    action.target_employee_id ??
    "the fleet";

  // The device name is already the card title, so repeating it inside the
  // reasoning wastes the narrow line length it would otherwise get.
  const reasoning = withoutSubject(action.justification, action, target);
  const params = Object.entries(action.params ?? {}).filter(
    ([key]) => key !== "reason",
  );
  const summary = String(action.params?.reason ?? "") || reasoning;
  const review = action.review;
  const supportLabel =
    review?.review_priority === "routine" ? "well supported" : "check carefully";
  const isOpen = status === "proposed";
  const isRejected = status === "rejected";

  return (
    <div className={`proposal ${open ? "open" : ""}${isOpen ? "" : " decided"}`}>
      <div className="proposal-kicker">{humanizeAction(action.action_type)}</div>
      <div className="proposal-target">{target}</div>
      <p className="proposal-summary">{summary}</p>

      <div className="proposal-meta">
        {review && (
          <span className={`badge ${review.review_priority}`}>{supportLabel}</span>
        )}
        <button
          type="button"
          className="proposal-toggle"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          <span className={`proposal-caret ${open ? "open" : ""}`} aria-hidden />
          {open ? "Hide detail" : "Why this"}
        </button>
      </div>

      {open && (
        <div className="proposal-detail">
          {summary !== reasoning && reasoning && (
            <p className="proposal-reasoning">{reasoning}</p>
          )}

          {params.length > 0 && (
            <dl className="proposal-params">
              {params.map(([key, value]) => (
                <div key={key} className="proposal-param">
                  <dt>{key.replace(/_/g, " ")}</dt>
                  <dd>{String(value)}</dd>
                </div>
              ))}
            </dl>
          )}

          <div className="proposal-evidence">
            {action.evidence_ids.length > 0 ? (
              <CitationChips
                ids={action.evidence_ids}
                byId={byId}
                onShowCitations={onShowCitations}
              />
            ) : (
              <span className="muted small">no readings attached</span>
            )}
          </div>

          {review && review.notes.length > 0 && (
            <ul className="proposal-notes">
              {review.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="actions-row actions-row-end">
        {isOpen ? (
          <>
            <button
              className="btn btn-no btn-sm"
              disabled={busy}
              onClick={() => onDecide(action.action_id, false)}
            >
              Reject
            </button>
            <button
              className="btn btn-ok btn-sm"
              disabled={busy}
              onClick={() => onDecide(action.action_id, true)}
            >
              Approve
            </button>
          </>
        ) : (
          <span
            className={`proposal-verdict ${isRejected ? "is-rejected" : "is-approved"}`}
          >
            {isRejected ? "Rejected" : "Approved"}
          </span>
        )}
      </div>
    </div>
  );
}

/** "flag_device_for_replacement" → "Flag device for replacement". */
function humanizeAction(actionType: string): string {
  const words = actionType.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * Drop a leading "<device> has ..." from the model's justification. Only the
 * exact target strings are stripped, so a sentence that genuinely opens some
 * other way is left alone rather than mangled.
 */
function withoutSubject(
  justification: string,
  action: ProposedAction,
  target: string,
): string {
  let text = justification.trim();
  const subjects = [target, action.target_label, action.target_device_id]
    .filter((s): s is string => Boolean(s))
    .sort((a, b) => b.length - a.length);

  for (const subject of subjects) {
    if (text.toLowerCase().startsWith(subject.toLowerCase())) {
      text = text.slice(subject.length).trim();
      text = text.replace(/^(has|is|was|shows|reports)\s+/i, "");
      break;
    }
  }
  return text.charAt(0).toUpperCase() + text.slice(1);
}
