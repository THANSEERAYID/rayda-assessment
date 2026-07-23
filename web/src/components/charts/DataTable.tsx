import type { ChartData } from "../../types";

const MAX_ROWS = 10;

/** The "detailed text data" view — one row per device, every cited field. */
export function DataTable({
  chart,
  onSelectDevice,
}: {
  chart: ChartData;
  onSelectDevice?: (deviceId: string) => void;
}) {
  if (chart.table_rows.length === 0) return null;

  const rows = chart.table_rows.slice(0, MAX_ROWS);
  const hidden = chart.table_rows.length - rows.length;
  const columns = chart.columns;

  return (
    <div className="chart-card">
      <h3 title={chart.title}>{chart.title}</h3>
      <div className="chart-body viz-table-wrap">
        <table className="viz-table">
          <colgroup>
            {columns.map((col) => (
              <col key={col} className={`col-${col}`} />
            ))}
          </colgroup>
          <thead>
            <tr>
              {columns.map((col) => (
                <th key={col}>{col.replace(/[._]/g, " ")}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                {columns.map((col) => (
                  <td
                    key={col}
                    className={cellClass(col)}
                    title={String(row[col] ?? "")}
                    onClick={
                      col === "device"
                        ? () =>
                            onSelectDevice?.(
                              String(row.device_id ?? row[col]),
                            )
                        : undefined
                    }
                  >
                    {renderCell(col, row[col])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {hidden > 0 && (
          <div className="chart-more muted small">+{hidden} more</div>
        )}
      </div>
    </div>
  );
}

function cellClass(col: string): string {
  if (col === "device") return "cell-device";
  if (col === "type") return "cell-type";
  if (col === "severity") return "cell-severity";
  if (col === "finding" || col === "title") return "cell-finding";
  return "cell-default";
}

function renderCell(col: string, value: unknown) {
  if (col === "severity" && typeof value === "string") {
    return <span className={`badge ${value}`}>{value}</span>;
  }
  if (value == null) return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }
  return String(value);
}
