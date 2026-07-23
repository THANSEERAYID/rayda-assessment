import { useRef, useState } from "react";
import { formatTimestamp } from "../../format";
import { formatMeasured } from "../../formatLabels";
import type { ChartData } from "../../types";

const WIDTH = 600;
const HEIGHT = 200;
const PAD_LEFT = 44;
const PAD_RIGHT = 12;
const PAD_TOP = 12;
const PAD_BOTTOM = 24;

/**
 * A single device's metric over time. One series, one hue — per the dataviz
 * interaction guidance, a line chart ships a crosshair + tooltip by default
 * rather than a label on every point.
 */
export function TrendLineChart({ chart }: { chart: ChartData }) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const points = chart.points.filter(
    (p): p is typeof p & { value: number } => typeof p.value === "number",
  );
  if (points.length < 2) return null;

  const values = points.map((p) => p.value);
  const minV = Math.min(...values);
  const maxV = Math.max(...values);
  const span = maxV - minV || 1;

  const plotW = WIDTH - PAD_LEFT - PAD_RIGHT;
  const plotH = HEIGHT - PAD_TOP - PAD_BOTTOM;

  const xAt = (i: number) => PAD_LEFT + (i / (points.length - 1)) * plotW;
  const yAt = (v: number) => PAD_TOP + plotH - ((v - minV) / span) * plotH;

  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${xAt(i)},${yAt(p.value)}`).join(" ");

  const handleMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const ratio = WIDTH / rect.width;
    const svgX = (e.clientX - rect.left) * ratio;
    const fraction = (svgX - PAD_LEFT) / plotW;
    const index = Math.round(fraction * (points.length - 1));
    setHoverIndex(Math.min(points.length - 1, Math.max(0, index)));
  };

  const hovered = hoverIndex != null ? points[hoverIndex] : null;

  return (
    <div className="chart-card">
      <h3 title={chart.title}>{chart.title}</h3>
      <div className="chart-body viz-trend">
        <div className="viz-trend-plot">
          <svg
            ref={svgRef}
            className="viz-svg"
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            preserveAspectRatio="xMidYMid meet"
            onMouseMove={handleMove}
            onMouseLeave={() => setHoverIndex(null)}
          >
            {[minV, (minV + maxV) / 2, maxV].map((v, i) => (
              <g key={i}>
                <line
                  className="viz-gridline"
                  x1={PAD_LEFT}
                  x2={WIDTH - PAD_RIGHT}
                  y1={yAt(v)}
                  y2={yAt(v)}
                />
                <text className="viz-axis-label" x={4} y={yAt(v) + 3}>
                  {v.toFixed(1)}
                </text>
              </g>
            ))}
            <line
              className="viz-baseline"
              x1={PAD_LEFT}
              x2={WIDTH - PAD_RIGHT}
              y1={HEIGHT - PAD_BOTTOM}
              y2={HEIGHT - PAD_BOTTOM}
            />

            <path
              d={path}
              fill="none"
              stroke="var(--viz-cat-1)"
              strokeWidth={2}
              vectorEffect="non-scaling-stroke"
              strokeLinejoin="round"
              strokeLinecap="round"
            />

            <text className="viz-axis-label" x={PAD_LEFT} y={HEIGHT - 6}>
              {shortDate(points[0].label)}
            </text>
            <text className="viz-axis-label" x={WIDTH - PAD_RIGHT} y={HEIGHT - 6} textAnchor="end">
              {shortDate(points[points.length - 1].label)}
            </text>

            {hovered && (
              <g>
                <line
                  x1={xAt(hoverIndex!)}
                  x2={xAt(hoverIndex!)}
                  y1={PAD_TOP}
                  y2={HEIGHT - PAD_BOTTOM}
                  stroke="var(--viz-axis)"
                  strokeWidth={1}
                  strokeDasharray="3,3"
                />
                <circle
                  cx={xAt(hoverIndex!)}
                  cy={yAt(hovered.value)}
                  r={4}
                  fill="var(--viz-cat-1)"
                  stroke="var(--surface)"
                  strokeWidth={2}
                />
              </g>
            )}
          </svg>

          {hovered && (
            <div
              className="viz-tooltip"
              style={{
                left: `${(xAt(hoverIndex!) / WIDTH) * 100}%`,
                top: `${(yAt(hovered.value) / HEIGHT) * 100}%`,
              }}
            >
              <div className="mono">{formatTimestamp(hovered.label)}</div>
            <div>
              {formatMeasured(hovered.value, hovered.unit, chart.unit)}
            </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function shortDate(iso: string): string {
  // Axis ticks stay terse — the full format belongs in the tooltip and in prose.
  const date = new Date(/(Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`);
  if (Number.isNaN(date.getTime())) return iso.slice(0, 10);
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    timeZone: "Asia/Kolkata",
  }).format(date);
}
