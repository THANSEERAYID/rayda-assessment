import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { formatTimestamp } from "../format";
import type { EvalCaseResult, EvalCaseStatus, EvalReport, EvalTier } from "../types";

/**
 * Run the deterministic and/or live agent evaluation suites.
 *
 * The main surface is a live case listing: every collected test appears as a
 * row and flips from pending → running → passed/failed as the suite advances.
 */
export function EvalPanel() {
  const [report, setReport] = useState<EvalReport | null>(null);
  const [tier, setTier] = useState<EvalTier>("deterministic");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | EvalCaseStatus>("all");
  const [openId, setOpenId] = useState<string | null>(null);
  const logRef = useRef<HTMLPreElement>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await api.evalStatus();
      setReport(next);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Poll often while running so each finished case shows up quickly.
  useEffect(() => {
    if (report?.status !== "running") return;
    const id = window.setInterval(() => {
      void refresh();
    }, 1000);
    return () => window.clearInterval(id);
  }, [report?.status, refresh]);

  // Keep the side log pinned to the latest output while a run is live.
  useEffect(() => {
    if (!logRef.current) return;
    logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [report?.log_tail, report?.status]);

  const start = async () => {
    setError(null);
    setFilter("all");
    setOpenId(null);
    try {
      const next = await api.evalRun(tier);
      setReport(next);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const running = report?.status === "running";
  const llmOk = report?.llm_configured ?? false;
  const cases = report?.cases ?? [];

  const filtered = useMemo(() => {
    if (filter === "all") return cases;
    return cases.filter((c) => c.status === filter);
  }, [cases, filter]);

  // Group by category for section headers in the listing.
  const groups = useMemo(() => {
    const order: string[] = [];
    const map = new Map<string, EvalCaseResult[]>();
    for (const item of filtered) {
      if (!map.has(item.category)) {
        map.set(item.category, []);
        order.push(item.category);
      }
      map.get(item.category)!.push(item);
    }
    return order.map((category) => ({
      category,
      items: map.get(category)!,
    }));
  }, [filtered]);

  const pending = report?.total_pending ?? cases.filter((c) =>
    c.status === "pending" || c.status === "running",
  ).length;

  return (
    <div className="eval-page">
      <div className="tasks-hero">
        <h2>Evaluation</h2>
        <p className="tasks-lede">
          Prove the challenge gates: grounding, insights, actions, and
          guardrails. Each test case appears in the list below and updates as
          it finishes.
        </p>
      </div>

      <div className="eval-toolbar">
        <div className="eval-tiers" role="group" aria-label="Eval tier">
          {(
            [
              ["deterministic", "Deterministic", "Free · no model"],
              ["live", "Live agent", "Needs API key"],
              ["both", "Both", "Full scorecard"],
            ] as const
          ).map(([id, label, hint]) => (
            <button
              key={id}
              type="button"
              className={`eval-tier ${tier === id ? "on" : ""}`}
              disabled={running}
              onClick={() => setTier(id)}
            >
              <span className="eval-tier-label">{label}</span>
              <span className="eval-tier-hint">{hint}</span>
            </button>
          ))}
        </div>

        <div className="eval-toolbar-actions">
          {!llmOk && (tier === "live" || tier === "both") && (
            <span className="eval-warn">OPENAI_API_KEY not configured</span>
          )}
          <button
            type="button"
            className="btn btn-primary"
            disabled={running || (!llmOk && tier !== "deterministic")}
            onClick={() => void start()}
          >
            {running ? "Running…" : "Run evaluation"}
          </button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {loading && !report ? (
        <div className="empty">Loading last scorecard…</div>
      ) : (
        <>
          <div className="eval-summary">
            <div className="eval-stat">
              <div className="sc-l">Status</div>
              <div className={`eval-status ${report?.status ?? "idle"}`}>
                {report?.status ?? "idle"}
              </div>
            </div>
            <div className="eval-stat">
              <div className="sc-l">Passed</div>
              <div className="sc-v ok">{report?.total_passed ?? 0}</div>
            </div>
            <div className="eval-stat">
              <div className="sc-l">Failed</div>
              <div className={`sc-v ${report?.total_failed ? "bad" : ""}`}>
                {report?.total_failed ?? 0}
              </div>
            </div>
            <div className="eval-stat">
              <div className="sc-l">Pending</div>
              <div className="sc-v">{pending}</div>
            </div>
            <div className="eval-stat">
              <div className="sc-l">Last run</div>
              <div className="sc-d">
                {report?.finished_at
                  ? formatTimestamp(report.finished_at)
                  : report?.started_at
                    ? `Started ${formatTimestamp(report.started_at)}`
                    : "—"}
              </div>
            </div>
          </div>

          {report?.error && (
            <div className="error-banner">{report.error}</div>
          )}

          <div className="eval-body">
            <div className="eval-main">
              {cases.length > 0 ? (
                <section className="eval-listing">
                  <div className="eval-listing-head">
                    <h3 className="tasks-cat-label">Test cases</h3>
                    <div className="eval-filters" role="tablist" aria-label="Filter cases">
                      {(
                        [
                          ["all", "All"],
                          ["running", "Running"],
                          ["pending", "Pending"],
                          ["passed", "Passed"],
                          ["failed", "Failed"],
                          ["skipped", "Skipped"],
                        ] as const
                      ).map(([id, label]) => (
                        <button
                          key={id}
                          type="button"
                          role="tab"
                          aria-selected={filter === id}
                          className={`module-tab ${filter === id ? "on" : ""}`}
                          onClick={() => setFilter(id)}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                  </div>

                  {groups.length === 0 ? (
                    <div className="empty">No cases match this filter.</div>
                  ) : (
                    groups.map((group) => (
                      <div key={group.category} className="eval-group">
                        <h4 className="eval-group-label">{group.category}</h4>
                        <div className="eval-case-list">
                          {group.items.map((item) => {
                            const open = openId === item.id;
                            return (
                              <div
                                key={item.id}
                                className={`eval-case-card ${item.status}${open ? " open" : ""}`}
                              >
                                <button
                                  type="button"
                                  className="eval-case-row"
                                  aria-expanded={open}
                                  onClick={() =>
                                    setOpenId(open ? null : item.id)
                                  }
                                >
                                  <StatusMark status={item.status} />
                                  <span className="eval-case-main">
                                    <span className="eval-case-title">{item.name}</span>
                                    <span className="eval-case-id">{item.id}</span>
                                  </span>
                                  {item.duration_s != null && (
                                    <span className="eval-case-dur">
                                      {item.duration_s.toFixed(2)}s
                                    </span>
                                  )}
                                  {item.status === "running" && (
                                    <span className="task-spinner" aria-label="Running" />
                                  )}
                                </button>
                                {open && (
                                  <div className="eval-case-detail">
                                    <p className="eval-case-desc">{caseDescription(item)}</p>
                                    {item.message && (
                                      <pre className="eval-case-msg">{item.message}</pre>
                                    )}
                                    {!item.message && item.status === "pending" && (
                                      <div className="eval-case-empty muted small">
                                        Waiting to run…
                                      </div>
                                    )}
                                    {!item.message && item.status === "running" && (
                                      <div className="eval-case-empty muted small">
                                        In progress…
                                      </div>
                                    )}
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    ))
                  )}
                </section>
              ) : running ? (
                <div className="empty">
                  <strong>Collecting test cases…</strong>
                  <div className="small" style={{ marginTop: 6 }}>
                    The suite is starting; cases will fill this list shortly.
                  </div>
                </div>
              ) : (
                <div className="empty">
                  <strong>No test cases yet</strong>
                  <div className="small" style={{ marginTop: 6 }}>
                    Choose a tier and run evaluation. Cases appear in this list as
                    soon as each category is collected, then update as they finish.
                  </div>
                </div>
              )}
            </div>

            <aside className="eval-log">
              <h3 className="tasks-cat-label">Run log</h3>
              <pre className="eval-log-body" ref={logRef}>
                {(report?.log_tail ?? []).join("\n")
                  || (running ? "Waiting for output…" : "Run an evaluation to see live output here.")}
              </pre>
            </aside>
          </div>
        </>
      )}
    </div>
  );
}

function caseDescription(item: EvalCaseResult): string {
  if (item.description?.trim()) return item.description.trim();
  const base = item.name.split("[")[0]?.replace(/^test_/, "") ?? item.name;
  return base.replace(/_/g, " ").trim() || item.name;
}

function StatusMark({ status }: { status: EvalCaseStatus }) {
  const label =
    status === "passed"
      ? "PASS"
      : status === "failed" || status === "error"
        ? "FAIL"
        : status === "running"
          ? "RUN"
          : status === "skipped"
            ? "SKIP"
            : "WAIT";
  return <span className={`eval-mark ${status}`}>{label}</span>;
}
