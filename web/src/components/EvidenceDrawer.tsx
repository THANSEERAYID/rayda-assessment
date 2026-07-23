import { useState } from "react";
import { formatTimestamp } from "../format";
import { formatMeasured } from "../formatLabels";
import type { Evidence } from "../types";

export type CitationsSheet = {
  items: Evidence[];
  /** Citation the user clicked — used to highlight in the list, not auto-open detail. */
  focusId?: string | null;
};

/**
 * Citations side sheet.
 *
 * Clicking a citation chip in chat opens the full set for that claim as a list.
 * Choosing one row drills into the telemetry record; Back returns to the list.
 */
export function EvidenceDrawer({
  sheet,
  onClose,
}: {
  sheet: CitationsSheet | null;
  onClose: () => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Reset drill-down when the sheet's contents change or it closes.
  const itemsKey = sheet?.items.map((e) => e.evidence_id).join("|") ?? "";
  const [seenKey, setSeenKey] = useState(itemsKey);
  if (itemsKey !== seenKey) {
    setSeenKey(itemsKey);
    setSelectedId(null);
  }

  if (!sheet || sheet.items.length === 0) return null;

  const selected =
    selectedId != null
      ? sheet.items.find((e) => e.evidence_id === selectedId) ?? null
      : null;

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} aria-hidden />
      <aside
        className="drawer evidence-sheet"
        role="dialog"
        aria-label={selected ? "Citation detail" : "Citations"}
      >
        {selected ? (
          <CitationDetail
            evidence={selected}
            total={sheet.items.length}
            onBack={() => setSelectedId(null)}
            onClose={onClose}
          />
        ) : (
          <CitationList
            items={sheet.items}
            focusId={sheet.focusId}
            onSelect={setSelectedId}
            onClose={onClose}
          />
        )}
      </aside>
    </>
  );
}

function CitationList({
  items,
  focusId,
  onSelect,
  onClose,
}: {
  items: Evidence[];
  focusId?: string | null;
  onSelect: (id: string) => void;
  onClose: () => void;
}) {
  return (
    <>
      <header className="drawer-head">
        <div className="drawer-head-text">
          <div className="drawer-kicker">Citations</div>
          <h2>
            {items.length} cited reading{items.length === 1 ? "" : "s"}
          </h2>
          <div className="muted small">
            Open one to see the telemetry behind it.
          </div>
        </div>
        <button type="button" className="btn btn-ghost" onClick={onClose}>
          Close
        </button>
      </header>

      <div className="citation-list">
        {items.map((evidence, index) => {
          const focused = focusId === evidence.evidence_id;
          return (
            <button
              key={evidence.evidence_id}
              type="button"
              className={`citation-row${focused ? " is-focus" : ""}`}
              onClick={() => onSelect(evidence.evidence_id)}
            >
              <span className="citation-index mono">{index + 1}</span>
              <span className="citation-row-body">
                <span className="citation-row-title">
                  {humanField(evidence.field)}
                </span>
                <span className="citation-row-meta muted small">
                  {evidence.device_label ?? evidence.device_id ?? "Fleet"}
                  {" · "}
                  {formatEvidenceValue(evidence.field, evidence.value)}
                </span>
              </span>
              <span className="citation-row-chevron" aria-hidden>
                ›
              </span>
            </button>
          );
        })}
      </div>
    </>
  );
}

function CitationDetail({
  evidence,
  total,
  onBack,
  onClose,
}: {
  evidence: Evidence;
  total: number;
  onBack: () => void;
  onClose: () => void;
}) {
  const detailEntries = Object.entries(evidence.detail ?? {});

  return (
    <>
      <header className="drawer-head">
        <div className="drawer-head-text">
          <button type="button" className="citation-back" onClick={onBack}>
            ← All {total} citation{total === 1 ? "" : "s"}
          </button>
          <div className="drawer-kicker">Citation</div>
          <h2 title={evidence.field}>{humanField(evidence.field)}</h2>
          <div className="mono muted small">{evidence.evidence_id}</div>
        </div>
        <button type="button" className="btn btn-ghost" onClick={onClose}>
          Close
        </button>
      </header>

      <section className="drawer-hero">
        <div className="drawer-hero-label">Value</div>
        <div className="drawer-hero-value">
          {formatEvidenceValue(evidence.field, evidence.value)}
        </div>
        <div className="drawer-hero-meta">
          <span className="drawer-pill">{evidence.tool.replace(/_/g, " ")}</span>
          <span className="muted small">
            {formatTimestamp(evidence.snapshot_ts)}
          </span>
        </div>
      </section>

      <section className="drawer-section-block">
        <h3 className="drawer-section">Source</h3>
        <div className="drawer-fields">
          <div className="drawer-field">
            <span className="drawer-field-label">Device</span>
            <span className="drawer-field-value">
              {evidence.device_label ?? evidence.device_id ?? "—"}
            </span>
          </div>
          <div className="drawer-field">
            <span className="drawer-field-label">Serial</span>
            <span className="drawer-field-value mono">
              {evidence.device_id ?? "—"}
            </span>
          </div>
          <div className="drawer-field">
            <span className="drawer-field-label">Field</span>
            <span className="drawer-field-value mono">{evidence.field}</span>
          </div>
        </div>
      </section>

      {detailEntries.length > 0 && (
        <section className="drawer-section-block">
          <h3 className="drawer-section">Context</h3>
          <div className="drawer-fields">
            {detailEntries.map(([key, value]) => (
              <div className="drawer-field" key={key}>
                <span className="drawer-field-label">{humanField(key)}</span>
                <span className="drawer-field-value">
                  {formatEvidenceValue(key, value)}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}
    </>
  );
}

function humanField(field: string): string {
  return field
    .replace(/[._]/g, " ")
    .replace(/\bPct\b/gi, "%")
    .replace(/\bId\b/gi, "ID");
}

function formatEvidenceValue(field: string, value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") {
    if (field.endsWith("_pct") || field.endsWith(".pct") || field.includes("pct")) {
      return formatMeasured(value, "%");
    }
    if (field.includes("cycle")) return formatMeasured(value, "cycles");
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
