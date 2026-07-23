import type { ChartData, TurnResult } from "../types";
import { BarChart } from "./charts/BarChart";
import { DataTable } from "./charts/DataTable";
import { PieChart } from "./charts/PieChart";
import { chartHasContent } from "./charts/renderable";
import { SeverityDistributionChart } from "./charts/SeverityDistributionChart";
import { StatTile } from "./charts/StatTile";
import { TrendLineChart } from "./charts/TrendLineChart";

/**
 * Visual half of the split view.
 *
 * Answer-driven only: nothing renders until a question has been asked, and then
 * only the charts the copilot chose for that answer. A standing fleet overview
 * used to sit here, but it made the page look identical before and after a
 * question — which hid the thing worth showing, that the visualisation is
 * reasoned from the answer rather than a fixed dashboard.
 */
export function DashboardPanel({
  result,
  busy = false,
  onSelectDevice,
}: {
  result: TurnResult | undefined;
  busy?: boolean;
  onSelectDevice: (deviceId: string) => void;
}) {
  // Also drops charts that would render nothing, so the section heading never
  // appears above an empty grid — see chartHasContent.
  const answerCharts =
    result && !result.refusal
      ? result.charts.filter((c) => c.chart_type !== "none" && chartHasContent(c))
      : [];
  const selectDevice = busy ? () => undefined : onSelectDevice;

  if (answerCharts.length === 0) {
    return (
      <div className="empty">
        <strong>
          {result ? "This answer did not call for a chart" : "Ask a question in the chat"}
        </strong>
        <div className="small" style={{ marginTop: 6 }}>
          {result
            ? "The copilot proposes a chart only when one adds to the answer."
            : "The copilot will suggest charts (bar, pie, donut, trend, table, …) that fit the answer, and they will render here."}
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="dash-section">
        <h2>From the last answer</h2>
        <div className="small muted">
          Charts the copilot chose for this question — including trend lines when
          history was retrieved.
        </div>
      </div>
      <div className={`chart-grid${busy ? " is-busy" : ""}`}>
        {answerCharts.map((chart, index) => (
          <Chart
            key={`answer-${chart.chart_type}-${chart.title}-${index}`}
            chart={chart}
            onSelectDevice={selectDevice}
          />
        ))}
      </div>
    </>
  );
}

function Chart({
  chart,
  onSelectDevice,
}: {
  chart: ChartData;
  onSelectDevice: (deviceId: string) => void;
}) {
  switch (chart.chart_type) {
    case "bar":
      return <BarChart chart={chart} onSelectDevice={onSelectDevice} />;
    case "pie":
    case "donut":
      return <PieChart chart={chart} />;
    case "data_table":
      return <DataTable chart={chart} onSelectDevice={onSelectDevice} />;
    case "severity_distribution":
      return <SeverityDistributionChart chart={chart} />;
    case "trend_line":
      return <TrendLineChart chart={chart} />;
    case "stat_tile":
      return <StatTile chart={chart} />;
    default:
      return null;
  }
}
