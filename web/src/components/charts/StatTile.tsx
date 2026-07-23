import type { ChartData } from "../../types";
import { formatMeasured } from "../../formatLabels";

/** A single headline number — the "is this even a chart?" answer of no. */
export function StatTile({ chart }: { chart: ChartData }) {
  if (chart.stat_value == null) return null;

  return (
    <div className="chart-card">
      <h3 title={chart.title}>{chart.title}</h3>
      <div className="chart-body viz-stat-tile">
        <div className="viz-stat-value">
          {formatMeasured(chart.stat_value as number | string, chart.unit)}
        </div>
        {chart.stat_label && chart.stat_label !== chart.unit && (
          <div className="viz-stat-label">{chart.stat_label}</div>
        )}
      </div>
    </div>
  );
}
