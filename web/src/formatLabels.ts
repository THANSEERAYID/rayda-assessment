/** Human-readable labels for detector finding types. */
const FINDING_TYPE_LABELS: Record<string, string> = {
  battery_eol: "Battery end of life",
  disk_pressure: "Disk pressure",
  ram_pressure: "RAM pressure",
  compliance_drift: "Compliance drift",
  unapproved_software: "Unapproved software",
};

export function formatFindingType(type: string): string {
  return FINDING_TYPE_LABELS[type] ?? titleCase(type.replace(/_/g, " "));
}

/**
 * Render a chart number with its unit. Bare figures are meaningless on a
 * dashboard — always prefer a unit from the point, falling back to the chart.
 */
export function formatMeasured(
  value: number | string,
  unit?: string | null,
  chartUnit?: string | null,
): string {
  const u = (unit || chartUnit || "").trim();
  const numeric = typeof value === "number";
  const text = numeric
    ? Number.isInteger(value)
      ? String(value)
      : value.toFixed(1)
    : String(value);

  if (!u) return text;
  if (u === "%") return `${text}%`;

  if (numeric) {
    const countUnit = pluralizeCountUnit(u, value);
    if (countUnit) return `${text} ${countUnit}`;
  }
  return `${text} ${u}`;
}

function pluralizeCountUnit(unit: string, value: number): string | null {
  const key = unit.toLowerCase();
  const pairs: Record<string, [string, string]> = {
    finding: ["finding", "findings"],
    findings: ["finding", "findings"],
    device: ["device", "devices"],
    devices: ["device", "devices"],
    cycle: ["cycle", "cycles"],
    cycles: ["cycle", "cycles"],
    day: ["day", "days"],
    days: ["day", "days"],
  };
  const pair = pairs[key];
  if (!pair) return null;
  return value === 1 ? pair[0] : pair[1];
}

function titleCase(value: string): string {
  return value.replace(/\b\w/g, (ch) => ch.toUpperCase());
}
