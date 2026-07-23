import type { ChartData } from "../../types";
import { formatMeasured } from "../../formatLabels";

const MAX_BARS = 10;

/**
 * Magnitude across devices for one metric. A single hue, per the dataviz
 * guidance: the bars already carry identity via their label, so a different
 * color per bar would be decoration, not information.
 */
export function BarChart({
  chart,
  onSelectDevice,
}: {
  chart: ChartData;
  onSelectDevice?: (deviceId: string) => void;
}) {
  const numeric = chart.points.filter(
    (p): p is typeof p & { value: number } => typeof p.value === "number",
  );
  if (numeric.length === 0) return null;

  const ranked = [...numeric].sort((a, b) => b.value - a.value);
  const shown = ranked.slice(0, MAX_BARS);
  const hidden = ranked.length - shown.length;
  const max = Math.max(...shown.map((p) => p.value), 0.0001);

  return (
    <div className="chart-card">
      <h3 title={chart.title}>{chart.title}</h3>
      <div className="chart-body viz-bars">
        {shown.map((point) => (
          <div className="viz-bar-row" key={point.evidence_id ?? point.label}>
            <span
              className="viz-bar-label"
              onClick={() => onSelectDevice?.(point.device_id ?? point.label)}
              title={point.label}
            >
              {point.label}
            </span>
            <div className="viz-bar-track">
              <div
                className="viz-bar-fill"
                style={{ width: `${(point.value / max) * 100}%` }}
              />
            </div>
            <span className="viz-bar-value">
              {formatMeasured(point.value, point.unit, chart.unit)}
            </span>
          </div>
        ))}
        {hidden > 0 && (
          <div className="chart-more muted small">+{hidden} more</div>
        )}
      </div>
    </div>
  );
}
