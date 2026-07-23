/** Browser session for tenant + conversation so refresh keeps run traces. */
import type { QueuedAction } from "./types";

const SESSION_KEY = "fleet-copilot.session";
// The Action-Proposals staging queue, per company. Kept here rather than in the
// DB because it is intent not yet acted on — the moment a card is run its result
// becomes a persisted turn, which is the durable record.
const QUEUE_KEY = "fleet-copilot.actionQueue";

type Session = {
  companyId: string;
  threadsByCompany: Record<string, string>;
};

function readSession(): Session | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Session;
    if (!parsed || typeof parsed.companyId !== "string") return null;
    if (!parsed.threadsByCompany || typeof parsed.threadsByCompany !== "object") {
      return { companyId: parsed.companyId, threadsByCompany: {} };
    }
    return parsed;
  } catch {
    return null;
  }
}

function writeSession(session: Session): void {
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  } catch {
    // Private mode / quota — ignore; session restore is best-effort.
  }
}

export function getSavedCompanyId(): string | null {
  return readSession()?.companyId ?? null;
}

export function getSavedThreadId(companyId: string): string | null {
  return readSession()?.threadsByCompany[companyId] ?? null;
}

export function rememberCompany(companyId: string): void {
  const current = readSession();
  writeSession({
    companyId,
    threadsByCompany: current?.threadsByCompany ?? {},
  });
}

export function rememberThread(companyId: string, threadId: string): void {
  const current = readSession();
  writeSession({
    companyId,
    threadsByCompany: {
      ...(current?.threadsByCompany ?? {}),
      [companyId]: threadId,
    },
  });
}

export function forgetThread(companyId: string): void {
  const current = readSession();
  if (!current) return;
  const next = { ...current.threadsByCompany };
  delete next[companyId];
  writeSession({ companyId: current.companyId, threadsByCompany: next });
}

/**
 * Older queue rows may carry a removed `kind` prefix (`ticket:` / `notify:`)
 * or lack fields. Normalize to `type:device` ids so one finding stages once.
 */
function normalizeQueued(raw: unknown): QueuedAction | null {
  if (!raw || typeof raw !== "object") return null;
  const item = raw as Partial<QueuedAction> & { id?: string; kind?: string };
  if (typeof item.id !== "string" || !item.id) return null;

  let findingType =
    typeof item.findingType === "string" ? item.findingType : "";
  let deviceId = typeof item.deviceId === "string" ? item.deviceId : "";
  let id = item.id;

  // Strip legacy kind prefixes: ticket:type:device / notify:type:device
  const kindPrefixed = /^(ticket|notify):(.+)$/.exec(id);
  if (kindPrefixed) {
    id = kindPrefixed[2];
  }

  if ((!findingType || !deviceId) && id.includes(":")) {
    const parts = id.split(":");
    if (parts.length >= 2) {
      deviceId = deviceId || parts[parts.length - 1];
      findingType = findingType || parts.slice(0, -1).join(":");
    }
  }

  if (findingType && deviceId) {
    id = `${findingType}:${deviceId}`;
  }

  return {
    id,
    findingType,
    deviceId,
    deviceLabel: item.deviceLabel ?? null,
    title: typeof item.title === "string" ? item.title : id,
    prompt: typeof item.prompt === "string" ? item.prompt : "",
  };
}

export function getActionQueue(companyId: string): QueuedAction[] {
  try {
    const raw = localStorage.getItem(QUEUE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as Record<string, unknown[]>;
    const rows = Array.isArray(parsed?.[companyId]) ? parsed[companyId] : [];
    return rows
      .map(normalizeQueued)
      .filter((row): row is QueuedAction => row != null);
  } catch {
    return [];
  }
}

export function setActionQueue(companyId: string, queue: QueuedAction[]): void {
  try {
    const raw = localStorage.getItem(QUEUE_KEY);
    const all = raw ? (JSON.parse(raw) as Record<string, QueuedAction[]>) : {};
    all[companyId] = queue;
    localStorage.setItem(QUEUE_KEY, JSON.stringify(all));
  } catch {
    // Private mode / quota — the queue simply won't persist across refresh.
  }
}

/** Finding keys (`type:device`) marked Converted on Insights, per company. */
const CONVERTED_KEY = "fleet-copilot.convertedFindings";

export function getConvertedFindings(companyId: string): string[] {
  try {
    const raw = localStorage.getItem(CONVERTED_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const rows = parsed?.[companyId];
    if (!Array.isArray(rows)) return [];
    return rows.filter((id): id is string => typeof id === "string" && id.length > 0);
  } catch {
    return [];
  }
}

export function setConvertedFindings(companyId: string, ids: string[]): void {
  try {
    const raw = localStorage.getItem(CONVERTED_KEY);
    const all = raw ? (JSON.parse(raw) as Record<string, string[]>) : {};
    all[companyId] = [...new Set(ids)];
    localStorage.setItem(CONVERTED_KEY, JSON.stringify(all));
  } catch {
    // Best-effort persistence.
  }
}
