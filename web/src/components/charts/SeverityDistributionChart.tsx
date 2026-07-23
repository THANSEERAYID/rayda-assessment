import type { ChartData } from "../../types";
import { formatMeasured } from "../../formatLabels";

/**
 * Severity is a status, not an arbitrary category, so this is the one chart
 * that uses the reserved status palette (good/warning/critical) rather than
 * the categorical series colors — status color, icon-equivalent label, never
 * color alone to carry the meaning.
 */
const SEVERITY_COLOR: Record<string, string> = {
  high: "var(--viz-critical)",
  medium: "var(--viz-warning)",
  low: "var(--viz-good)",
};

const SEVERITY_ORDER = ["high", "medium", "low"];

export function SeverityDistributionChart({ chart }: { chart: ChartData }) {
  const byLabel = new Map(chart.points.map((p) => [p.label, p]));
  const ordered = SEVERITY_ORDER.map((sev) => byLabel.get(sev)).filter(
    (p): p is NonNullable<typeof p> => p != null,
  );
  if (ordered.length === 0) return null;

  const max = Math.max(...ordered.map((p) => Number(p.value)), 1);
  const total = ordered.reduce((sum, p) => sum + Number(p.value), 0);
  const unit = chart.unit ?? ordered[0]?.unit ?? "findings";

  return (
    <div className="chart-card">
      <h3 title={chart.title}>{chart.title}</h3>
      <div className="chart-body viz-severity">
        <div className="viz-severity-total">
          <span className="viz-severity-total-value">{total}</span>
          <span className="viz-severity-total-label">{unit}</span>
        </div>
        <div className="viz-bars">
          {ordered.map((point) => (
            <div className="viz-bar-row" key={point.label}>
              <span className="viz-bar-label viz-sev-label">{point.label}</span>
              <div className="viz-bar-track">
                <div
                  className="viz-bar-fill"
                  style={{
                    width: `${(Number(point.value) / max) * 100}%`,
                    background: SEVERITY_COLOR[point.label] ?? "var(--viz-cat-1)",
                  }}
                />
              </div>
              <span className="viz-bar-value">
                {formatMeasured(Number(point.value), point.unit, unit)}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
