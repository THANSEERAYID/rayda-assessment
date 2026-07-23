/**
 * One timestamp format across the whole UI: `22 Jul 2026, 04:35 PM IST`.
 *
 * The API renders the same format in prose, so a reading quoted in an answer
 * and the same reading shown in a table read identically. Mixing raw ISO in
 * one place and a friendly format in another makes them look like different
 * facts.
 */
const ZONE = "Asia/Kolkata";

function parseTimestamp(value: string): Date | null {
  // Naive timestamps from the API are UTC; without the marker the browser
  // would read them as local time and silently shift every reading.
  const normalised = /(Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`;
  const date = new Date(normalised);
  return Number.isNaN(date.getTime()) ? null : date;
}

function timestampParts(date: Date) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
    timeZone: ZONE,
  }).formatToParts(date);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return {
    date: `${get("day")} ${get("month")} ${get("year")}`,
    // Browsers often render Asia/Kolkata as "GMT+5:30"; pin the label to IST.
    time: `${get("hour")}:${get("minute")} ${get("dayPeriod").toUpperCase()} IST`,
  };
}

export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  const date = parseTimestamp(value);
  if (!date) return value;
  const parts = timestampParts(date);
  return `${parts.date}, ${parts.time}`;
}

/** Date and time on separate lines — used where a stacked stamp reads cleaner. */
export function formatTimestampParts(
  value: string | null | undefined,
): { date: string; time: string } | null {
  if (!value) return null;
  const date = parseTimestamp(value);
  if (!date) return null;
  return timestampParts(date);
}
