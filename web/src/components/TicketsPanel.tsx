import { formatFindingType } from "../formatLabels";
import { formatTimestamp } from "../format";
import type { Ticket } from "../types";

/**
 * Remediation tickets — the concrete record an approved `open_remediation_ticket`
 * action leaves behind. Read-only: a ticket is created by executing an action
 * and traces back to it, so it is history, not something edited here.
 */
export function TicketsPanel({
  tickets,
  loading,
}: {
  tickets: Ticket[];
  loading: boolean;
}) {
  if (loading) return <div className="spinner">Loading tickets…</div>;

  if (tickets.length === 0) {
    return (
      <div className="empty">
        <strong>No tickets yet</strong>
        <div className="small" style={{ marginTop: 6 }}>
          A ticket is created when you approve an <em>open remediation ticket</em>{" "}
          action. Convert a compliance finding on Insights &amp; Trends, run it,
          then approve the proposal.
        </div>
      </div>
    );
  }

  return (
    <div className="module-page">
      <div className="module-table-wrap">
        <table className="module-table">
          <thead>
            <tr>
              <th>Ticket</th>
              <th>Device</th>
              <th>Check</th>
              <th>Note</th>
              <th>Raised</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {tickets.map((t) => (
              <tr key={t.ticket_id}>
                <td className="mono muted">{t.ticket_id}</td>
                <td className="cell-link">{t.device_label ?? t.device_id ?? "—"}</td>
                <td>{t.check_id ? formatFindingType(t.check_id) : "—"}</td>
                <td className="wrap">{t.note ?? "—"}</td>
                <td className="muted">{formatTimestamp(t.created_at)}</td>
                <td>
                  <span className={`badge ${t.status === "open" ? "proposed" : "executed"}`}>
                    {t.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
