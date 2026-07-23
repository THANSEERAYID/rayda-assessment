import type { ChartData, ChartPoint } from "../../types";
import { formatMeasured } from "../../formatLabels";

const SIZE = 180;
const CX = SIZE / 2;
const CY = SIZE / 2;
const OUTER = 76;
const INNER = 44;

const CAT_COLORS = [
  "var(--viz-cat-1)",
  "var(--viz-cat-2)",
  "var(--viz-cat-3)",
  "var(--viz-cat-4)",
  "var(--viz-cat-5)",
  "var(--viz-cat-6)",
  "var(--viz-cat-7)",
  "var(--viz-cat-8)",
];

const SEVERITY_COLOR: Record<string, string> = {
  high: "var(--viz-critical)",
  medium: "var(--viz-warning)",
  low: "var(--viz-good)",
};

const MAX_SLICES = 6;

/**
 * Share-of-a-whole visualisation. Uses the status palette when slices are
 * severities; otherwise the categorical series colours.
 *
 * ``pie`` is a solid disc; ``donut`` is a ring with the total in the hole.
 */
export function PieChart({ chart }: { chart: ChartData }) {
  const isDonut = chart.chart_type === "donut";
  const slices = chart.points.filter(
    (p): p is ChartPoint & { value: number } =>
      typeof p.value === "number" && p.value > 0,
  );
  if (slices.length === 0) return null;

  const ranked = [...slices].sort((a, b) => b.value - a.value);
  const head = ranked.slice(0, MAX_SLICES);
  const rest = ranked.slice(MAX_SLICES);
  const restTotal = rest.reduce((sum, p) => sum + p.value, 0);
  const display =
    restTotal > 0
      ? [
          ...head,
          {
            label: "other",
            value: restTotal,
            device_id: null,
            unit: null,
            evidence_id: null,
            severity: null,
          },
        ]
      : head;

  const total = display.reduce((sum, p) => sum + p.value, 0);
  if (total <= 0) return null;

  let angle = -Math.PI / 2;
  const arcs = display.map((point, index) => {
    const sweep = (point.value / total) * Math.PI * 2;
    const start = angle;
    const end = angle + sweep;
    angle = end;
    return {
      point,
      path: isDonut
        ? describeRing(CX, CY, OUTER, INNER, start, end)
        : describeArc(CX, CY, OUTER, start, end),
      color:
        (point.severity && SEVERITY_COLOR[point.severity]) ||
        CAT_COLORS[index % CAT_COLORS.length],
      pct: Math.round((point.value / total) * 100),
    };
  });

  return (
    <div className="chart-card">
      <h3 title={chart.title}>{chart.title}</h3>
      <div className="chart-body viz-pie-layout">
        <div className="viz-pie-wrap">
          <svg
            className="viz-pie"
            viewBox={`0 0 ${SIZE} ${SIZE}`}
            role="img"
            aria-label={chart.title}
          >
            {arcs.map((arc) => (
              <path
                key={arc.point.label}
                d={arc.path}
                fill={arc.color}
                stroke="var(--surface)"
                strokeWidth={2}
              >
                <title>
                  {arc.point.label}:{" "}
                  {formatMeasured(arc.point.value, arc.point.unit, chart.unit)} (
                  {arc.pct}%)
                </title>
              </path>
            ))}
          </svg>
          {isDonut && (
            <div className="viz-donut-center" aria-hidden>
              <div className="viz-donut-total mono">{total}</div>
              <div className="viz-donut-unit muted">
                {chart.unit ?? "total"}
              </div>
            </div>
          )}
        </div>
        <div className="viz-pie-legend">
          {arcs.map((arc) => (
            <div
              className="viz-legend-item"
              key={arc.point.label}
              title={arc.point.label}
            >
              <span
                className="viz-legend-swatch"
                style={{ background: arc.color }}
              />
              <span className="viz-legend-text">{arc.point.label}</span>
              <span className="viz-legend-meta mono muted">
                {formatMeasured(arc.point.value, arc.point.unit, chart.unit)} ·{" "}
                {arc.pct}%
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function describeArc(
  cx: number,
  cy: number,
  r: number,
  start: number,
  end: number,
): string {
  const sweep = end - start;
  if (sweep >= Math.PI * 2 - 1e-6) {
    return [
      `M ${cx} ${cy - r}`,
      `A ${r} ${r} 0 1 1 ${cx} ${cy + r}`,
      `A ${r} ${r} 0 1 1 ${cx} ${cy - r}`,
      "Z",
    ].join(" ");
  }
  const x1 = cx + r * Math.cos(start);
  const y1 = cy + r * Math.sin(start);
  const x2 = cx + r * Math.cos(end);
  const y2 = cy + r * Math.sin(end);
  const large = sweep > Math.PI ? 1 : 0;
  return [
    `M ${cx} ${cy}`,
    `L ${x1} ${y1}`,
    `A ${r} ${r} 0 ${large} 1 ${x2} ${y2}`,
    "Z",
  ].join(" ");
}

function describeRing(
  cx: number,
  cy: number,
  outer: number,
  inner: number,
  start: number,
  end: number,
): string {
  const sweep = end - start;
  if (sweep >= Math.PI * 2 - 1e-6) {
    return [
      `M ${cx} ${cy - outer}`,
      `A ${outer} ${outer} 0 1 1 ${cx} ${cy + outer}`,
      `A ${outer} ${outer} 0 1 1 ${cx} ${cy - outer}`,
      "Z",
      `M ${cx} ${cy - inner}`,
      `A ${inner} ${inner} 0 1 0 ${cx} ${cy + inner}`,
      `A ${inner} ${inner} 0 1 0 ${cx} ${cy - inner}`,
      "Z",
    ].join(" ");
  }
  const ox1 = cx + outer * Math.cos(start);
  const oy1 = cy + outer * Math.sin(start);
  const ox2 = cx + outer * Math.cos(end);
  const oy2 = cy + outer * Math.sin(end);
  const ix1 = cx + inner * Math.cos(end);
  const iy1 = cy + inner * Math.sin(end);
  const ix2 = cx + inner * Math.cos(start);
  const iy2 = cy + inner * Math.sin(start);
  const large = sweep > Math.PI ? 1 : 0;
  return [
    `M ${ox1} ${oy1}`,
    `A ${outer} ${outer} 0 ${large} 1 ${ox2} ${oy2}`,
    `L ${ix1} ${iy1}`,
    `A ${inner} ${inner} 0 ${large} 0 ${ix2} ${iy2}`,
    "Z",
  ].join(" ");
}
