import type { ChartData } from "../../types";

/**
 * Whether a chart will actually draw something.
 *
 * Every chart component bails to `null` on input it cannot plot — a bar over a
 * non-numeric field, a table with no rows, a trend with a single point. That is
 * the right call inside the component, but it is invisible to the panel above,
 * which would otherwise print a section heading over an empty grid.
 *
 * The model can legitimately ask for an undrawable chart: it proposed a `bar` of
 * `os_version` here, whose values are strings like "14.5". Rather than let that
 * become a blank panel, the conditions below mirror each component's guard so
 * the caller can decide before rendering. Keep them in step with the components.
 */
export function chartHasContent(chart: ChartData): boolean {
  const numeric = chart.points.filter(
    (p): p is typeof p & { value: number } => typeof p.value === "number",
  );

  switch (chart.chart_type) {
    case "bar":
      return numeric.length > 0;
    case "pie":
    case "donut":
      return numeric.some((p) => p.value > 0);
    case "data_table":
      return chart.table_rows.length > 0;
    case "severity_distribution":
      return chart.points.some((p) => ["high", "medium", "low"].includes(p.label));
    case "trend_line":
      return numeric.length >= 2;
    case "stat_tile":
      return chart.stat_value != null;
    default:
      return false;
  }
}
