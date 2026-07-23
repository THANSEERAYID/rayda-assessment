import { formatTimestamp } from "../format";
import type { Email } from "../types";

/**
 * Emails the system has sent or simulated — a read-only log. Every entry comes
 * from an approved notify_employee action; nothing is composed by hand here,
 * because a message to an employee should go through the agent's proposal and
 * the approval gate, not a free-form form. A "simulated" status means SMTP is
 * not configured on the server, so the message was recorded but not transmitted.
 */
export function EmailsPanel({
  emails,
  loading,
}: {
  emails: Email[];
  loading: boolean;
}) {
  if (loading) return <div className="spinner">Loading emails…</div>;

  if (emails.length === 0) {
    return (
      <div className="empty">
        <strong>No emails yet</strong>
        <div className="small" style={{ marginTop: 6 }}>
          Approving a <em>notify employee</em> action sends one. Each is recorded
          here with its delivery status.
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
              <th>To</th>
              <th>Subject</th>
              <th>Message</th>
              <th>Sent</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {emails.map((e) => (
              <tr key={e.email_id}>
                <td className="mono">{e.to_address}</td>
                <td>{e.subject}</td>
                <td className="wrap">{e.body}</td>
                <td className="muted">{formatTimestamp(e.created_at)}</td>
                <td>
                  <span
                    className={`badge ${statusClass(e.status)}`}
                    title={e.error ?? undefined}
                  >
                    {e.status}
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

function statusClass(status: string): string {
  if (status === "sent") return "executed";
  if (status === "failed") return "high";
  return "proposed"; // simulated
}
