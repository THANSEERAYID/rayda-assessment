import type { View } from "./Sidebar";
import type { AuditEvent, Company, Finding, ProposedAction, TraceStep } from "../types";

/**
 * Page-scoped fleet state, shown above the active panel.
 *
 * Dashboard carries the full fleet snapshot. Other views keep the stats that
 * belong to that page. Trace & audit and Insights own KPI rows inside the module.
 */
export function SummaryStrip({
  view,
  company,
  findings,
  scanned,
  pending,
  steps: _steps,
  auditEvents: _auditEvents,
}: {
  view: View;
  company: Company | undefined;
  findings: Finding[];
  scanned: boolean;
  pending: ProposedAction[];
  steps: TraceStep[];
  auditEvents: AuditEvent[];
}) {
  const high = findings.filter((f) => f.severity === "high").length;
  const batteries = findings.filter((f) => f.finding_type === "battery_eol").length;
  const drift = findings.filter((f) => f.finding_type === "compliance_drift").length;

  // The tasks page owns its own queue UI and needs no fleet stats above it.
  if (
    view === "trace" ||
    view === "insights" ||
    view === "actions" ||
    view === "tasks" ||
    view === "eval"
  ) {
    return null;
  }

  return (
    <div className="summary">
      {view === "dashboard" && (
        <>
          <div className="scard">
            <div className="sc-l">Devices</div>
            <div className="sc-v">{company?.device_count ?? "—"}</div>
            <div className="sc-d">{company?.name ?? "No company selected"}</div>
          </div>

          {/* Before a scan there is no count to report. Showing 0 would state
              the fleet is clean, which is a different claim from not having
              looked — the same absence-vs-ignorance distinction the grounding
              rules make about an empty query result. */}
          <div className="scard">
            <div className="sc-l">Open findings</div>
            <div className="sc-v">{scanned ? findings.length : "—"}</div>
            <div className={`sc-d ${scanned && high ? "danger" : scanned ? "ok" : ""}`}>
              {!scanned
                ? "run a scan in Insights"
                : high
                  ? `${high} high severity`
                  : "none at high severity"}
            </div>
          </div>

          <div className="scard">
            <div className="sc-l">Needs hardware attention</div>
            <div className="sc-v">{scanned ? batteries : "—"}</div>
            <div className="sc-d">
              {!scanned
                ? "not scanned yet"
                : batteries
                  ? "batteries near end of life"
                  : "no batteries flagged"}
            </div>
          </div>

          <div className="scard">
            <div className="sc-l">Awaiting approval</div>
            <div className="sc-v">{pending.length}</div>
            <div className={`sc-d ${pending.length ? "warn" : ""}`}>
              {pending.length
                ? "nothing has been carried out"
                : drift
                  ? `${drift} compliance regressions`
                  : "queue clear"}
            </div>
          </div>
        </>
      )}

    </div>
  );
}
