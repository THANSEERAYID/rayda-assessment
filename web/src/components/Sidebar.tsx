import { MenuSelect } from "./MenuSelect";
import { VIEW_PATHS } from "../routes";
import type { Company } from "../types";

export type View =
  | "dashboard"
  | "tasks"
  | "insights"
  | "actions"
  | "tickets"
  | "emails"
  | "trace"
  | "eval";

/**
 * Navigation rail.
 *
 * The tenant selector lives here rather than in the topbar because it is the
 * single most consequential control in the product — everything below it is
 * scoped to that choice, and switching it starts a new conversation.
 */
export function Sidebar({
  companies,
  companyId,
  onCompanyChange,
  view,
  onViewChange,
  pendingCount,
  findingCount,
  taskCount,
  ticketCount,
  emailCount,
  threadId,
  busy = false,
}: {
  companies: Company[];
  companyId: string;
  onCompanyChange: (id: string) => void;
  view: View;
  onViewChange: (view: View) => void;
  pendingCount: number;
  findingCount: number;
  taskCount?: number;
  ticketCount?: number;
  emailCount?: number;
  threadId: string | null;
  busy?: boolean;
}) {
  const company = companies.find((c) => c.company_id === companyId);

  type NavItem = { id: View; label: string; badge?: number; warn?: boolean };
  const sections: { label: string; items: NavItem[] }[] = [
    {
      label: "Workspaces",
      items: [
        { id: "dashboard", label: "Grounded Q&A" },
        { id: "insights", label: "Insights & Trends", badge: findingCount || undefined },
        { id: "tasks", label: "Action Proposals", badge: taskCount || undefined, warn: true },
        { id: "actions", label: "Approvals", badge: pendingCount || undefined, warn: true },
        { id: "trace", label: "Trace & audit" },
      ],
    },
    {
      // What the agent has actually carried out, kept apart from the queues
      // above where nothing has happened yet.
      label: "Actions performed",
      items: [
        { id: "tickets", label: "Tickets", badge: ticketCount || undefined },
        { id: "emails", label: "Emails", badge: emailCount || undefined },
      ],
    },
    {
      label: "Quality",
      items: [{ id: "eval", label: "Evaluation" }],
    },
  ];

  return (
    <aside className="side">
      <div className="brand">
        <div className="logo">FC</div>
        <div>
          <div className="brand-name">Fleet Copilot</div>
          <div className="brand-sub">IT asset intelligence</div>
        </div>
      </div>

      <div className="side-tenant">
        <MenuSelect
          label="Company"
          tone="dark"
          value={companyId}
          disabled={busy}
          emptyLabel="Select company"
          options={companies.map((c) => ({
            value: c.company_id,
            label: c.name,
            meta: `${c.device_count} device${c.device_count === 1 ? "" : "s"}`,
          }))}
          onChange={onCompanyChange}
        />
      </div>

      <nav>
        {/* Anchors rather than buttons: the browser then does what it does for
            any link — show the target on hover, open in a new tab on
            middle-click or ctrl/cmd-click, offer "copy link address". A button
            with an onClick looks the same and supports none of it. The default
            navigation is prevented for ordinary clicks so the SPA keeps its
            state; modified clicks are left to the browser. */}
        {sections.map((section) => (
          <div className="nav-section" key={section.label}>
            <div className="nl">{section.label}</div>
            {section.items.map((item) => (
              <a
                key={item.id}
                href={VIEW_PATHS[item.id]}
                className={`ni ${view === item.id ? "on" : ""}`}
                aria-current={view === item.id ? "page" : undefined}
                onClick={(event) => {
                  if (
                    event.defaultPrevented ||
                    event.button !== 0 ||
                    event.metaKey ||
                    event.ctrlKey ||
                    event.shiftKey ||
                    event.altKey
                  ) {
                    return;
                  }
                  event.preventDefault();
                  onViewChange(item.id);
                }}
              >
                {item.label}
                {item.badge != null && (
                  <span className={`bdg ${item.warn ? "warn" : ""}`}>{item.badge}</span>
                )}
              </a>
            ))}
          </div>
        ))}
      </nav>

      <div className="side-foot">
        <div className="row">
          <div className="av">IT</div>
          <div>
            <div className="nm">IT administrator</div>
            <div className="sb">{company?.name ?? "—"}</div>
          </div>
        </div>
        {threadId && (
          <div className="sb" style={{ marginTop: 8, fontFamily: "var(--mono)" }}>
            {threadId}
          </div>
        )}
      </div>
    </aside>
  );
}
