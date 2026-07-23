import { Fragment, useEffect, useMemo, useState } from "react";
import { ProposalReview } from "./ReviewSignalBlock";
import { api } from "../api/client";
import { formatTimestamp } from "../format";
import type { Evidence, ProposedAction } from "../types";

type QueueTab = "awaiting" | "approved";
type ReviewFilter = "all" | "check_carefully" | "routine";

// Section order for the awaiting tab — tickets and emails lead, since those are
// the two the operator most often acts on in bulk.
const GROUP_ORDER: Record<string, number> = {
  open_remediation_ticket: 0,
  notify_employee: 1,
  create_upgrade_order: 2,
  flag_device_for_replacement: 3,
};

const ACTION_LABELS: Record<string, string> = {
  create_upgrade_order: "Upgrade order",
  open_remediation_ticket: "Remediation ticket",
  flag_device_for_replacement: "Replacement flag",
  notify_employee: "Employee notification",
};

/**
 * Approval queue as a module workspace — Awaiting vs Approved tabs, search,
 * review filters, a dense table, and an evidence-style detail sheet.
 *
 * Deciding resumes the thread that proposed the action; nothing takes effect
 * until you approve. Approved rows are the post-decision history (usually
 * executed).
 */
export function ActionsPanel({
  companyId,
  pending,
  approved,
  loading,
  onDecide,
}: {
  companyId: string;
  pending: ProposedAction[];
  approved: ProposedAction[];
  loading: boolean;
  // No `busy` here on purpose: approvals are independent DB decisions, not chat
  // turns, so the page never freezes on the global lock. Per-action state lives
  // in `progressing`.
  onDecide: (
    decisions: {
      thread_id: string;
      action_id: string;
      approved: boolean;
    }[],
  ) => Promise<void>;
}) {
  const [queueTab, setQueueTab] = useState<QueueTab>("awaiting");
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [query, setQuery] = useState("");
  const [reviewFilter, setReviewFilter] = useState<ReviewFilter>("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [active, setActive] = useState<ProposedAction | null>(null);
  const [progressing, setProgressing] = useState<Set<string>>(new Set());
  const [localError, setLocalError] = useState<string | null>(null);

  const isAwaiting = queueTab === "awaiting";
  const source = isAwaiting ? pending : approved;

  const typeCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const action of source) {
      counts[action.action_type] = (counts[action.action_type] ?? 0) + 1;
    }
    return counts;
  }, [source]);

  const carefulCount = pending.filter(
    (a) => a.review?.review_priority === "check_carefully",
  ).length;

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return source.filter((action) => {
      if (typeFilter !== "all" && action.action_type !== typeFilter) return false;
      if (isAwaiting) {
        if (
          reviewFilter === "check_carefully" &&
          action.review?.review_priority !== "check_carefully"
        ) {
          return false;
        }
        if (
          reviewFilter === "routine" &&
          action.review?.review_priority === "check_carefully"
        ) {
          return false;
        }
      }
      if (!q) return true;
      const hay = [
        action.action_id,
        action.action_type,
        formatActionType(action.action_type),
        action.target_label,
        action.target_device_id,
        action.target_employee_id,
        action.justification,
        action.result,
        action.thread_id,
        JSON.stringify(action.params ?? {}),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
  }, [source, typeFilter, reviewFilter, query, isAwaiting]);

  const switchQueue = (next: QueueTab) => {
    setQueueTab(next);
    setTypeFilter("all");
    setReviewFilter("all");
    setQuery("");
    setSelected(new Set());
    setActive(null);
  };

  // On the awaiting tab, cluster by action type so the section headers group
  // cleanly (Ticket raise together, Email send together, …). Approved history
  // stays in its own chronological order.
  const ordered = isAwaiting
    ? [...filtered].sort(
        (a, b) =>
          (GROUP_ORDER[a.action_type] ?? 99) - (GROUP_ORDER[b.action_type] ?? 99),
      )
    : filtered;

  const ids = filtered.map((a) => a.action_id);
  const allOn = ids.length > 0 && ids.every((id) => selected.has(id));
  // Selected proposals not already being decided — what a bulk click would act
  // on. Zero means every selected one is already in flight.
  const selectedIdle = [...selected].filter((id) => !progressing.has(id)).length;

  const toggleRow = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    setSelected(allOn ? new Set() : new Set(ids));
  };

  // Add/remove this action rather than replacing the set, so a decision in
  // flight does not clear the marker on the others running beside it.
  const markProgressing = (ids: string[], on: boolean) =>
    setProgressing((prev) => {
      const next = new Set(prev);
      for (const id of ids) {
        if (on) next.add(id);
        else next.delete(id);
      }
      return next;
    });

  const decideOne = async (action: ProposedAction, approvedDecision: boolean) => {
    // Only this row is gated — another row may be mid-decision at the same time.
    if (progressing.has(action.action_id)) return;
    setLocalError(null);
    setActive(null);
    markProgressing([action.action_id], true);
    try {
      await onDecide([
        {
          thread_id: action.thread_id,
          action_id: action.action_id,
          approved: approvedDecision,
        },
      ]);
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(action.action_id);
        return next;
      });
    } catch (e) {
      setLocalError((e as Error).message);
    } finally {
      markProgressing([action.action_id], false);
    }
  };

  const decideSelected = async (approvedDecision: boolean) => {
    const chosen = pending.filter(
      (a) => selected.has(a.action_id) && !progressing.has(a.action_id),
    );
    if (!chosen.length) return;
    setLocalError(null);
    const ids = chosen.map((a) => a.action_id);
    markProgressing(ids, true);
    // The parent settles these per thread and in parallel; the queue does not
    // freeze while they run.
    try {
      await onDecide(
        chosen.map((a) => ({
          thread_id: a.thread_id,
          action_id: a.action_id,
          approved: approvedDecision,
        })),
      );
      setSelected((prev) => {
        const next = new Set(prev);
        for (const id of ids) next.delete(id);
        return next;
      });
      setActive(null);
    } catch (e) {
      setLocalError((e as Error).message);
    } finally {
      markProgressing(ids, false);
    }
  };

  const exportVisible = () => {
    const blob = new Blob([JSON.stringify(filtered, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = isAwaiting
      ? "fleet-copilot-awaiting-approvals.json"
      : "fleet-copilot-approved-actions.json";
    a.click();
    URL.revokeObjectURL(url);
  };

  if (loading) return <div className="spinner">Loading…</div>;

  return (
    <div className="module-page">
      <div className="module-toolbar">
        <div className="module-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={isAwaiting}
            className={`module-tab ${isAwaiting ? "on" : ""}`}
            onClick={() => switchQueue("awaiting")}
          >
            Awaiting approvals
            {carefulCount > 0 ? (
              <span className="module-tab-flag">{carefulCount} review</span>
            ) : (
              <span className="module-tab-count">{pending.length}</span>
            )}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={!isAwaiting}
            className={`module-tab ${!isAwaiting ? "on" : ""}`}
            onClick={() => switchQueue("approved")}
          >
            Approved
            <span className="module-tab-count">{approved.length}</span>
          </button>
        </div>
        <div className="module-actions">
          <button type="button" className="btn" onClick={exportVisible}>
            Export
          </button>
        </div>
      </div>

      <div className="module-filters">
        <label className="module-search">
          <span className="sr-only">Search</span>
          <input
            type="search"
            placeholder={
              isAwaiting
                ? "Search targets, justifications, action ids…"
                : "Search approved actions, outcomes, targets…"
            }
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <div className="module-pills">
          <button
            type="button"
            className={`filter-pill ${typeFilter === "all" ? "on" : ""}`}
            onClick={() => setTypeFilter("all")}
          >
            All types
          </button>
          {Object.entries(typeCounts).map(([type, count]) => (
            <button
              key={type}
              type="button"
              className={`filter-pill ${typeFilter === type ? "on" : ""}`}
              onClick={() => setTypeFilter(type)}
            >
              {formatActionType(type)} ({count})
            </button>
          ))}
          {isAwaiting &&
            (
              [
                { id: "check_carefully", label: "Check carefully" },
                { id: "routine", label: "Well supported" },
              ] as const
            ).map((pill) => (
              <button
                key={pill.id}
                type="button"
                className={`filter-pill ${reviewFilter === pill.id ? "on" : ""}`}
                onClick={() =>
                  setReviewFilter((prev) =>
                    prev === pill.id ? "all" : pill.id,
                  )
                }
              >
                {pill.label}
              </button>
            ))}
        </div>
      </div>

      {localError && <div className="error-banner">{localError}</div>}

      {isAwaiting && selected.size > 0 && (
        <div className="module-banner">
          <span>
            {selected.size} {selected.size === 1 ? "proposal" : "proposals"}{" "}
            selected — none have taken effect yet.
          </span>
          <div className="module-banner-actions">
            <button
              type="button"
              className="btn btn-no"
              disabled={selectedIdle === 0}
              onClick={() => void decideSelected(false)}
            >
              Reject
            </button>
            <button
              type="button"
              className="btn btn-ok"
              disabled={selectedIdle === 0}
              onClick={() => void decideSelected(true)}
            >
              Approve
            </button>
            <button
              type="button"
              className="linkish"
              onClick={() => setSelected(new Set())}
            >
              Clear
            </button>
          </div>
        </div>
      )}

      {source.length === 0 ? (
        <div className="empty">
          <strong>
            {isAwaiting
              ? "Nothing is awaiting approval"
              : "No approved actions yet"}
          </strong>
          <div className="small" style={{ marginTop: 6 }}>
            {isAwaiting
              ? "When the copilot proposes an action, it will pause here until you decide."
              : "Approved proposals appear here after they are carried out."}
          </div>
        </div>
      ) : filtered.length === 0 ? (
        <div className="empty">
          <strong>No matching {isAwaiting ? "proposals" : "actions"}</strong>
          <div className="small" style={{ marginTop: 6 }}>
            Try clearing filters or searching a different target.
          </div>
        </div>
      ) : (
        <div className="module-table-wrap">
          <table className="module-table">
            <thead>
              <tr>
                {isAwaiting && (
                  <th className="col-check">
                    <input
                      type="checkbox"
                      checked={allOn}
                      onChange={toggleAll}
                      aria-label="Select all proposals"
                    />
                  </th>
                )}
                <th>Action</th>
                <th>Target</th>
                <th>{isAwaiting ? "Justification" : "Outcome"}</th>
                <th>{isAwaiting ? "Proposed" : "When"}</th>
                <th>Status</th>
                {isAwaiting && <th className="col-decide">Decide</th>}
              </tr>
            </thead>
            <tbody>
              {ordered.map((action, index) => {
                // On the awaiting tab, cluster proposals by action type and put a
                // section header before each group — Ticket raise, Email send,
                // and the rest. This is where the agent's chosen actions land, so
                // it is where the ticket/email split belongs (the agent, not the
                // operator, decided each one).
                const showHeader =
                  isAwaiting &&
                  (index === 0 ||
                    ordered[index - 1].action_type !== action.action_type);
                const isChecked = selected.has(action.action_id);
                const isActive = active?.action_id === action.action_id;
                const isProgressing = progressing.has(action.action_id);
                const target =
                  action.target_label ??
                  action.target_device_id ??
                  action.target_employee_id ??
                  "—";
                const bodyText = isAwaiting
                  ? action.justification
                  : action.result || action.justification;
                return (
                  <Fragment key={action.action_id}>
                  {showHeader && (
                    <tr className="table-group">
                      <td colSpan={7}>
                        {formatActionType(action.action_type)}
                      </td>
                    </tr>
                  )}
                  <tr
                    className={[
                      isActive ? "is-active" : "",
                      isChecked ? "is-checked" : "",
                      isProgressing ? "is-progressing" : "",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                    onClick={() => !isProgressing && setActive(action)}
                  >
                    {isAwaiting && (
                      <td
                        className="col-check"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <input
                          type="checkbox"
                          checked={isChecked}
                          disabled={isProgressing}
                          onChange={() => toggleRow(action.action_id)}
                          aria-label={`Select ${formatActionType(action.action_type)}`}
                        />
                      </td>
                    )}
                    <td className="cell-link">
                      {formatActionType(action.action_type)}
                    </td>
                    <td>{target}</td>
                    <td className="col-justify" title={bodyText}>
                      <span className="clamp-2">{bodyText}</span>
                    </td>
                    <td className="muted">
                      {action.created_at
                        ? formatTimestamp(action.created_at)
                        : "—"}
                    </td>
                    <td>
                      {isProgressing ? (
                        <span className="status-dot warn">
                          <i />
                          in progress
                        </span>
                      ) : isAwaiting ? (
                        <ReviewDot review={action.review?.review_priority} />
                      ) : (
                        <StatusDot status={action.status} />
                      )}
                    </td>
                    {isAwaiting && (
                      <td
                        className="col-decide"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <div className="row-actions">
                          {isProgressing ? (
                            <span className="small muted">Working…</span>
                          ) : (
                            <>
                              <button
                                type="button"
                                className="btn btn-no"
                                onClick={() => void decideOne(action, false)}
                              >
                                Reject
                              </button>
                              <button
                                type="button"
                                className="btn btn-ok"
                                onClick={() => void decideOne(action, true)}
                              >
                                Approve
                              </button>
                            </>
                          )}
                        </div>
                      </td>
                    )}
                  </tr>
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {active && (
        <ActionDetailDrawer
          action={active}
          companyId={companyId}
          busy={progressing.has(active.action_id)}
          readOnly={!isAwaiting}
          onClose={() => setActive(null)}
          onDecide={decideOne}
        />
      )}
    </div>
  );
}

function StatusDot({ status }: { status: ProposedAction["status"] }) {
  const tone =
    status === "executed" || status === "approved"
      ? "ok"
      : status === "failed" || status === "rejected"
        ? "danger"
        : "neutral";
  return (
    <span className={`status-dot ${tone}`}>
      <i />
      {status}
    </span>
  );
}

function ReviewDot({
  review,
}: {
  review: "routine" | "check_carefully" | undefined;
}) {
  if (review === "check_carefully") {
    return (
      <span className="status-dot warn">
        <i />
        check carefully
      </span>
    );
  }
  if (review === "routine") {
    return (
      <span className="status-dot ok">
        <i />
        well supported
      </span>
    );
  }
  return (
    <span className="status-dot neutral">
      <i />
      proposed
    </span>
  );
}

function ActionDetailDrawer({
  action,
  companyId,
  busy,
  readOnly = false,
  onClose,
  onDecide,
}: {
  action: ProposedAction;
  companyId: string;
  busy: boolean;
  readOnly?: boolean;
  onClose: () => void;
  onDecide: (action: ProposedAction, approved: boolean) => void | Promise<void>;
}) {
  // Without a backdrop to click, Escape is the keyboard route to dismiss.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const paramEntries = Object.entries(action.params ?? {});
  const target =
    action.target_label ??
    action.target_device_id ??
    action.target_employee_id ??
    "—";

  return (
    <>
      {/* No backdrop: a screen-covering overlay is what made deciding one
          proposal block the rest. This is a non-blocking side sheet — the list
          stays live, clicking another proposal swaps the sheet, and Escape or
          the close button dismisses it. */}
      <aside
        className="drawer evidence-sheet"
        role="dialog"
        aria-label="Proposal detail"
      >
        <header className="drawer-head">
          <div className="drawer-head-text">
            <div className="drawer-kicker">
              {readOnly ? "Approved action" : "Proposal"}
            </div>
            <h2 title={formatActionType(action.action_type)}>
              {formatActionType(action.action_type)}
            </h2>
            <div className="mono muted small">{action.action_id}</div>
          </div>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Close
          </button>
        </header>

        <section className="drawer-hero">
          <div className="drawer-hero-label">
            {readOnly ? "Outcome" : "Target"}
          </div>
          <div className="drawer-hero-value drawer-hero-value-sm">
            {readOnly ? action.result || target : target}
          </div>
          <div className="drawer-hero-meta">
            <span className="drawer-pill">{action.status}</span>
            {action.created_at && (
              <span className="muted small">
                {formatTimestamp(action.created_at)}
              </span>
            )}
          </div>
        </section>

        <section className="drawer-section-block">
          <h3 className="drawer-section">Summary</h3>
          <div className="drawer-fields">
            <div className="drawer-field">
              <span className="drawer-field-label">Justification</span>
              <span className="drawer-field-value">{action.justification}</span>
            </div>
            {readOnly && action.result && (
              <div className="drawer-field">
                <span className="drawer-field-label">Result</span>
                <span className="drawer-field-value">{action.result}</span>
              </div>
            )}
            <div className="drawer-field">
              <span className="drawer-field-label">Device</span>
              <span className="drawer-field-value mono">
                {action.target_device_id ?? "—"}
              </span>
            </div>
            <div className="drawer-field">
              <span className="drawer-field-label">Employee</span>
              <span className="drawer-field-value mono">
                {action.target_employee_id ?? "—"}
              </span>
            </div>
            <div className="drawer-field">
              <span className="drawer-field-label">Thread</span>
              <span className="drawer-field-value mono">{action.thread_id}</span>
            </div>
          </div>
        </section>

        {paramEntries.length > 0 && (
          <section className="drawer-section-block">
            <h3 className="drawer-section">Parameters</h3>
            <div className="drawer-fields">
              {paramEntries.map(([key, value]) => (
                <div className="drawer-field" key={key}>
                  <span className="drawer-field-label">
                    {key.replace(/_/g, " ")}
                  </span>
                  <span className="drawer-field-value">
                    <ParamValue value={value} />
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}

        {action.evidence_ids.length > 0 && (
          <section className="drawer-section-block">
            <h3 className="drawer-section">
              Evidence
              <span className="drawer-section-count">
                {action.evidence_ids.length}
              </span>
            </h3>
            <CitedReadings action={action} companyId={companyId} />
          </section>
        )}

        <section className="drawer-section-block">
          <h3 className="drawer-section">Review signal</h3>
          <ProposalReview review={action.review} />
          {!action.review && (
            <div className="muted small">No review signal attached.</div>
          )}
        </section>

        {!readOnly && (
          <section className="drawer-section-block drawer-decide">
            <div className="actions-row actions-row-end">
              <button
                type="button"
                className="btn btn-no"
                disabled={busy}
                onClick={() => void onDecide(action, false)}
              >
                Reject
              </button>
              <button
                type="button"
                className="btn btn-ok"
                disabled={busy}
                onClick={() => void onDecide(action, true)}
              >
                Approve
              </button>
            </div>
            <div className="small muted" style={{ marginTop: 8 }}>
              Nothing has been carried out. Approving resumes the paused turn.
            </div>
          </section>
        )}
      </aside>
    </>
  );
}

function ParamValue({ value }: { value: unknown }) {
  if (value == null) return <>—</>;
  if (typeof value === "boolean") return <>{value ? "yes" : "no"}</>;
  if (typeof value === "number") return <span className="mono">{value}</span>;
  if (typeof value === "string") {
    return value.trim() ? <>{value}</> : <span className="muted">—</span>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="muted">none</span>;
    if (value.every((item) => item == null || typeof item !== "object")) {
      return (
        <span className="drawer-chip-list">
          {value.map((item, index) => (
            <span className="drawer-chip" key={`${String(item)}-${index}`}>
              {String(item)}
            </span>
          ))}
        </span>
      );
    }
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return <span className="muted">none</span>;
    return (
      <div className="drawer-nested">
        {entries.map(([k, v]) => (
          <div key={k}>
            <span className="muted">{k.replace(/_/g, " ")}: </span>
            <ParamValue value={v} />
          </div>
        ))}
      </div>
    );
  }
  return <span className="mono">{JSON.stringify(value)}</span>;
}

function formatActionType(type: string): string {
  return ACTION_LABELS[type] ?? titleCase(type.replace(/_/g, " "));
}

function titleCase(text: string): string {
  return text.replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * The readings a proposal rests on, resolved from the evidence store.
 *
 * The queue outlives the turn that filled it, so the ledger those ids came from
 * is long gone by review time. Showing the ids alone made the citation a claim
 * rather than something a reviewer could check — which is the opposite of the
 * point.
 */
function CitedReadings({
  action,
  companyId,
}: {
  action: ProposedAction;
  companyId: string;
}) {
  const [records, setRecords] = useState<Evidence[] | null>(null);
  const [failed, setFailed] = useState(false);
  const ids = action.evidence_ids;

  useEffect(() => {
    let cancelled = false;
    setRecords(null);
    setFailed(false);
    api
      .evidence(companyId, ids)
      .then((r) => {
        if (!cancelled) setRecords(r.evidence);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [companyId, ids.join(",")]);

  if (failed) {
    return <div className="muted small">Could not load the cited readings.</div>;
  }
  if (records === null) {
    return <div className="muted small">Loading readings…</div>;
  }

  const byId = new Map(records.map((r) => [r.evidence_id, r]));
  return (
    <ul className="cited-readings">
      {ids.map((id) => {
        const record = byId.get(id);
        if (!record) {
          // Proposals created before evidence was persisted, so the id is real
          // but nothing backs it. Said plainly rather than shown as a bare id.
          return (
            <li key={id} className="cited-reading is-missing">
              <span className="mono">{id}</span>
              <span className="muted small">not stored for this proposal</span>
            </li>
          );
        }
        return (
          <li key={id} className="cited-reading">
            <span className="cited-field mono">{record.field}</span>
            <span className="cited-value">{String(record.value)}</span>
            <span className="cited-where muted small">
              {record.device_label ?? record.device_id ?? "fleet"}
              {record.snapshot_ts ? ` · ${formatTimestamp(record.snapshot_ts)}` : ""}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
