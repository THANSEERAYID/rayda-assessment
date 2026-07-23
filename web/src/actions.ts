import { formatFindingType } from "./formatLabels";
import type { Finding, QueuedAction } from "./types";

/**
 * Stage a finding for the Action Proposals queue.
 *
 * The operator does not pick ticket vs notify up front — the agent decides
 * which proposals (ticket, notify, both, or neither) the evidence justifies
 * when the card is run. Always name the canonical ``device_id`` (serial);
 * labels alone fail tenant checks on action tools.
 */
export function queuedActionFromFinding(finding: Finding): QueuedAction {
  return {
    id: findingKey(finding),
    findingType: finding.finding_type,
    deviceId: finding.device_id,
    deviceLabel: finding.device_label,
    title: `${finding.title} — ${finding.device_label ?? finding.device_id}`,
    prompt: taskPrompt({
      deviceId: finding.device_id,
      deviceLabel: finding.device_label,
      findingType: finding.finding_type,
    }),
  };
}

/** Rebuild the run prompt from structured fields (survives legacy queue rows). */
export function taskPrompt(task: {
  deviceId: string;
  deviceLabel?: string | null;
  findingType: string;
}): string {
  const type = formatFindingType(task.findingType).toLowerCase();
  const label = task.deviceLabel?.trim();
  const named = label ? `${task.deviceId} (${label})` : task.deviceId;
  return (
    `Investigate the ${type} finding on device_id=${task.deviceId}` +
    (label ? ` (${label})` : "") +
    `. Based only on the telemetry, decide which operational actions are ` +
    `justified — open a remediation ticket, notify the employee who uses the ` +
    `device, both, or neither. Propose only what the evidence supports. ` +
    `Use exactly device_id=${task.deviceId} in every tool argument and proposal ` +
    `(not the display name alone). Cite the telemetry. Device: ${named}.`
  );
}

/** One-trace bulk prompt — agent chooses ticket / notify / both per finding. */
export function bulkTaskPrompt(tasks: QueuedAction[]): string {
  const lines = tasks
    .map((t, i) => {
      const type = formatFindingType(t.findingType).toLowerCase();
      const label = t.deviceLabel ? ` (${t.deviceLabel})` : "";
      return `${i + 1}. device_id=${t.deviceId}${label} — ${type}: ${t.title}`;
    })
    .join("\n");
  return (
    `For each finding below, investigate the telemetry and decide which ` +
    `actions are justified: open a remediation ticket, notify the employee, ` +
    `both, or neither. Propose only what the evidence supports. Cite the ` +
    `telemetry for each. Use the given device_id values exactly in every ` +
    `tool call and proposal.\n\n${lines}`
  );
}

/** Stable finding / queue id: `type:device`. */
export function findingKey(finding: Finding): string {
  return `${finding.finding_type}:${finding.device_id}`;
}
