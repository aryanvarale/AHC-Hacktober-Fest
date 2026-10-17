import type { ChatMessage } from "@/hooks/useAgentChat";

/** Saved conversations. Kept per user in this browser, matching the copilot's private per-user memory. */
export interface Thread { id: string; title: string; updatedAt: number; messages: ChatMessage[] }

const key = (userId: string) => `mo-history:${userId}`;
const MAX_THREADS = 30;

export function readThreads(userId: string): Thread[] {
  try {
    const raw = window.localStorage.getItem(key(userId));
    const list = raw ? (JSON.parse(raw) as Thread[]) : [];
    return Array.isArray(list) ? list.sort((a, b) => b.updatedAt - a.updatedAt) : [];
  } catch {
    return [];
  }
}

function write(userId: string, threads: Thread[]) {
  let list = threads.slice(0, MAX_THREADS);
  // storage is small: drop the oldest threads until it fits
  for (;;) {
    try { window.localStorage.setItem(key(userId), JSON.stringify(list)); return; }
    catch { if (list.length <= 1) return; list = list.slice(0, -1); }
  }
}

/** Raw A2UI messages are only for the Agent view and are large; the rendered surface does not need them. */
const slim = (m: ChatMessage): ChatMessage => ({
  ...m, step: undefined, status: m.status === "streaming" ? "error" : m.status, restored: undefined,
  surfaces: m.surfaces.map((s) => ({ ...s, raw: [] })),
});

export function saveThread(userId: string, id: string, messages: ChatMessage[]): Thread[] {
  const all = readThreads(userId);
  const first = messages.find((m) => m.role === "user")?.text ?? "New chat";
  const thread: Thread = { id, title: first.length > 48 ? `${first.slice(0, 47)}…` : first, updatedAt: Date.now(), messages: messages.map(slim) };
  const next = [thread, ...all.filter((t) => t.id !== id)];
  write(userId, next);
  return next;
}

export function deleteThread(userId: string, id: string): Thread[] {
  const next = readThreads(userId).filter((t) => t.id !== id);
  write(userId, next);
  return next;
}

export function ago(ts: number): string {
  const s = Math.max(1, Math.round((Date.now() - ts) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
