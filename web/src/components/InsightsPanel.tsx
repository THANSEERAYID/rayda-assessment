import { useMemo, useState } from "react";
import { findingKey } from "../actions";
import { formatFindingType } from "../formatLabels";
import { formatTimestampParts } from "../format";
import type { Finding } from "../types";
import { MenuSelect } from "./MenuSelect";

type SeverityFilter = "all" | "high" | "medium" | "low";
type StatusTab = "open" | "converted";

/**
 * Detector output as a module workspace — same pattern as Trace & audit:
 * status tabs, type filter, search + severity, and a dense findings table.
 *
 * Converting a finding stages it for Action Proposals and moves it to the
 * Converted tab. Findings still come from deterministic Python with no model.
 */
export function InsightsPanel({
  findings,
  loading,
  scanning,
  lastScanAt,
  convertedIds,
  onRunScan,
  onConvert,
  onConvertAll,
}: {
  findings: Finding[];
  loading: boolean;
  scanning: boolean;
  lastScanAt: string | null;
  /** Finding ids (`type:device`) already converted to actions. */
  convertedIds: Set<string>;
  onRunScan: () => void;
  onConvert: (finding: Finding) => void;
  onConvertAll: (findings: Finding[]) => void;
}) {
  const [statusTab, setStatusTab] = useState<StatusTab>("open");
  const [typeTab, setTypeTab] = useState<string>("all");
  const [query, setQuery] = useState("");
  const [severity, setSeverity] = useState<SeverityFilter>("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [active, setActive] = useState<Finding | null>(null);

  const openFindings = useMemo(
    () => findings.filter((f) => !convertedIds.has(findingKey(f))),
    [findings, convertedIds],
  );
  const convertedFindings = useMemo(
    () => findings.filter((f) => convertedIds.has(findingKey(f))),
    [findings, convertedIds],
  );
  const scoped = statusTab === "open" ? openFindings : convertedFindings;

  const typeCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const finding of scoped) {
      counts[finding.finding_type] = (counts[finding.finding_type] ?? 0) + 1;
    }
    return counts;
  }, [scoped]);

  const typeTabs = useMemo(
    () =>
      Object.entries(typeCounts)
        .sort((a, b) => b[1] - a[1])
        .map(([type, count]) => ({
          id: type,
          label: formatFindingType(type),
          count,
        })),
    [typeCounts],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return scoped.filter((finding) => {
      if (typeTab !== "all" && finding.finding_type !== typeTab) return false;
      if (severity !== "all" && finding.severity !== severity) return false;
      if (!q) return true;
      const hay = [
        finding.device_label,
        finding.device_id,
        finding.title,
        finding.finding_type,
        finding.explanation,
        formatFindingType(finding.finding_type),
        JSON.stringify(finding.metrics ?? {}),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
  }, [scoped, typeTab, severity, query]);

  const switchStatus = (next: StatusTab) => {
    setStatusTab(next);
    setTypeTab("all");
    setSelected(new Set());
    setActive(null);
  };

  const switchType = (next: string) => {
    setTypeTab(next);
    setSelected(new Set());
    setActive(null);
  };

  const toggleRow = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const ids = filtered.map(findingKey);
  const allOn = ids.length > 0 && ids.every((id) => selected.has(id));

  const toggleAll = () => {
    setSelected(allOn ? new Set() : new Set(ids));
  };

  const exportVisible = () => {
    const blob = new Blob([JSON.stringify(filtered, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "fleet-copilot-insights.json";
    a.click();
    URL.revokeObjectURL(url);
  };

  if (loading) return <div className="spinner">Scanning telemetry…</div>;

  const scanParts = lastScanAt ? formatTimestampParts(lastScanAt) : null;
  const isConvertedView = statusTab === "converted";

  return (
    <div className="module-page">
      <div className="module-toolbar">
        <div className="module-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={statusTab === "open"}
            className={`module-tab ${statusTab === "open" ? "on" : ""}`}
            onClick={() => switchStatus("open")}
          >
            Open
            <span className="module-tab-count">{openFindings.length}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={statusTab === "converted"}
            className={`module-tab ${statusTab === "converted" ? "on" : ""}`}
            onClick={() => switchStatus("converted")}
          >
            Converted
            <span className="module-tab-count">{convertedFindings.length}</span>
          </button>
        </div>
        <MenuSelect
          tone="light"
          showMark={false}
          value={typeTab}
          className="module-type-picker"
          options={[
            {
              value: "all",
              label: isConvertedView
                ? `All converted — ${scoped.length} total`
                : `All open — ${scoped.length} total`,
            },
            ...typeTabs.map((t) => ({
              value: t.id,
              label: t.label,
              meta: `${t.count} finding${t.count === 1 ? "" : "s"}`,
            })),
          ]}
          onChange={switchType}
        />
        <div className="module-actions">
          <span className="scan-stamp">
            {scanning ? (
              "Scanning…"
            ) : scanParts ? (
              <>
                <span className="scan-stamp-row">
                  <span className="scan-stamp-label">Last scan</span>
                  <span className="scan-stamp-date">{scanParts.date}</span>
                </span>
                <span className="scan-stamp-time">{scanParts.time}</span>
              </>
            ) : (
              "Not scanned yet"
            )}
          </span>
          <button
            type="button"
            className="btn btn-primary"
            onClick={onRunScan}
            disabled={scanning}
          >
            {scanning ? "Scanning…" : lastScanAt ? "Rescan" : "Run scan"}
          </button>
          {!isConvertedView && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => onConvertAll(filtered)}
              disabled={filtered.length === 0}
              title="Stage every listed finding; the agent chooses ticket and/or notify when run"
            >
              Convert all to actions
            </button>
          )}
          <button
            type="button"
            className="btn"
            onClick={exportVisible}
            disabled={findings.length === 0}
          >
            Export
          </button>
        </div>
      </div>

      <div className="module-filters">
        <label className="module-search">
          <span className="sr-only">Search</span>
          <input
            type="search"
            placeholder="Search devices, titles, metrics…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <div className="module-pills">
          {(
            [
              { id: "all", label: "All" },
              { id: "high", label: "High" },
              { id: "medium", label: "Medium" },
              { id: "low", label: "Low" },
            ] as const
          ).map((pill) => (
            <button
              key={pill.id}
              type="button"
              className={`filter-pill ${severity === pill.id ? "on" : ""}`}
              onClick={() => setSeverity(pill.id)}
            >
              {pill.label}
            </button>
          ))}
        </div>
      </div>

      {selected.size > 0 && (
        <div className="module-banner">
          <span>
            {selected.size} {selected.size === 1 ? "finding" : "findings"} selected
            — ask the copilot to explain, remediate, or prioritise.
          </span>
          <button
            type="button"
            className="linkish"
            onClick={() => setSelected(new Set())}
          >
            Clear
          </button>
        </div>
      )}

      {findings.length === 0 && scanning ? (
        <div className="spinner">Scanning telemetry…</div>
      ) : findings.length === 0 && !lastScanAt ? (
        <div className="empty">
          <strong>Scan did not complete</strong>
          <div className="small" style={{ marginTop: 6 }}>
            Nothing was retrieved for this company. Run the scan again to check
            the last 30 days for battery wear, storage and memory pressure,
            compliance drift and unapproved software.
          </div>
          <div style={{ marginTop: 12 }}>
            <button type="button" className="btn btn-primary" onClick={onRunScan}>
              Run scan
            </button>
          </div>
        </div>
      ) : findings.length === 0 ? (
        <div className="empty">
          <strong>No findings for this company</strong>
          <div className="small" style={{ marginTop: 6 }}>
            That is a complete result, not a failed scan.
          </div>
        </div>
      ) : scoped.length === 0 ? (
        <div className="empty">
          <strong>
            {isConvertedView ? "Nothing converted yet" : "Nothing open"}
          </strong>
          <div className="small" style={{ marginTop: 6 }}>
            {isConvertedView
              ? "Convert a finding from the Open tab to stage it for Action Proposals."
              : "Every finding for this company has been converted. Open the Converted tab to review them."}
          </div>
        </div>
      ) : filtered.length === 0 ? (
        <div className="empty">
          <strong>No matching findings</strong>
          <div className="small" style={{ marginTop: 6 }}>
            Try clearing filters or searching a different device.
          </div>
        </div>
      ) : (
        <div className="module-table-wrap">
          <table className="module-table">
            <thead>
              <tr>
                <th className="col-check">
                  <input
                    type="checkbox"
                    checked={allOn}
                    onChange={toggleAll}
                    aria-label="Select all findings"
                  />
                </th>
                <th>Device</th>
                <th>Type</th>
                <th>Finding</th>
                <th>Serial</th>
                <th>Severity</th>
                <th className="col-action">
                  {isConvertedView ? "Status" : ""}
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((finding) => {
                const id = findingKey(finding);
                const isChecked = selected.has(id);
                const isActive =
                  active?.finding_type === finding.finding_type &&
                  active?.device_id === finding.device_id;
                return (
                  <tr
                    key={id}
                    className={[
                      isActive ? "is-active" : "",
                      isChecked ? "is-checked" : "",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                    onClick={() => setActive(finding)}
                  >
                    <td
                      className="col-check"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <input
                        type="checkbox"
                        checked={isChecked}
                        onChange={() => toggleRow(id)}
                        aria-label={`Select ${finding.device_label ?? finding.device_id}`}
                      />
                    </td>
                    <td className="cell-link">
                      {finding.device_label ?? finding.device_id}
                    </td>
                    <td>{formatFindingType(finding.finding_type)}</td>
                    <td className="wrap">
                      <div className="finding-title">{finding.title}</div>
                      {finding.explanation && (
                        <div className="finding-summary">
                          {finding.explanation}
                        </div>
                      )}
                    </td>
                    <td className="mono muted">{finding.device_id}</td>
                    <td>
                      <SeverityDot severity={finding.severity} />
                    </td>
                    <td
                      className="col-action"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {isConvertedView ? (
                        <span className="status-dot ok">
                          <i />
                          Converted
                        </span>
                      ) : (
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          onClick={() => onConvert(finding)}
                          title="Stage for Action Proposals — moves this finding to Converted"
                        >
                          Convert to action
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {active && (
        <FindingDetailDrawer finding={active} onClose={() => setActive(null)} />
      )}
    </div>
  );
}

function SeverityDot({ severity }: { severity: Finding["severity"] }) {
  return (
    <span
      className={`status-dot ${severity === "high" ? "danger" : severity === "medium" ? "warn" : "neutral"}`}
    >
      <i />
      {severity}
    </span>
  );
}

function FindingDetailDrawer({
  finding,
  onClose,
}: {
  finding: Finding;
  onClose: () => void;
}) {
  const metricEntries = Object.entries(finding.metrics ?? {});

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} aria-hidden />
      <aside
        className="drawer evidence-sheet"
        role="dialog"
        aria-label="Finding detail"
      >
        <header className="drawer-head">
          <div className="drawer-head-text">
            <div className="drawer-kicker">Finding</div>
            <h2 title={finding.device_label ?? finding.device_id}>
              {finding.device_label ?? finding.device_id}
            </h2>
            <div className="mono muted small">{finding.device_id}</div>
          </div>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Close
          </button>
        </header>

        <section className="drawer-hero">
          <div className="drawer-hero-label">Severity</div>
          <div
            className={`drawer-hero-value status-tone ${
              finding.severity === "high"
                ? "danger"
                : finding.severity === "medium"
                  ? "warn"
                  : ""
            }`}
          >
            {finding.severity}
          </div>
          <div className="drawer-hero-meta">
            <span className="drawer-pill">
              {formatFindingType(finding.finding_type)}
            </span>
          </div>
        </section>

        <section className="drawer-section-block">
          <h3 className="drawer-section">Summary</h3>
          <div className="drawer-fields">
            <div className="drawer-field">
              <span className="drawer-field-label">Title</span>
              <span className="drawer-field-value">{finding.title}</span>
            </div>
            {finding.explanation && (
              <div className="drawer-field">
                <span className="drawer-field-label">Why</span>
                <span className="drawer-field-value">{finding.explanation}</span>
              </div>
            )}
            <div className="drawer-field">
              <span className="drawer-field-label">Type</span>
              <span className="drawer-field-value">
                {formatFindingType(finding.finding_type)}
              </span>
            </div>
          </div>
        </section>

        {metricEntries.length > 0 && (
          <section className="drawer-section-block">
            <h3 className="drawer-section">Metrics</h3>
            <div className="drawer-fields">
              {metricEntries.map(([key, value]) => (
                <div key={key} className="drawer-field">
                  <span className="drawer-field-label">{key}</span>
                  <span className="drawer-field-value mono">
                    {typeof value === "object"
                      ? JSON.stringify(value)
                      : String(value)}
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}
      </aside>
    </>
  );
}
