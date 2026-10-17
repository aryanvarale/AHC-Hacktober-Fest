import { useEffect, useRef, useState } from "react";
import { ago, type Thread } from "@/lib/history";
import type { FieldUser } from "@/components/chat/ChatScreen";

type State = "closed" | "peek" | "open";

interface Props {
  threads: Thread[];
  activeId: string;
  onOpen: (t: Thread) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  users: FieldUser[];
  userId: string;
  onUserChange: (id: string) => void;
  /** only inside a chat: show or hide the agent's side of the conversation */
  agentView?: boolean;
  onToggleAgentView?: () => void;
}

/**
 * Floating chat history. It grows out of the hamburger button:
 * hover peeks at the latest chats, click opens the full list.
 */
export function HistoryMenu({ threads, activeId, onOpen, onNew, onDelete, users, userId, onUserChange, agentView, onToggleAgentView }: Props) {
  const [state, setState] = useState<State>("closed");
  const root = useRef<HTMLDivElement>(null);
  const leave = useRef<number>(0);

  useEffect(() => {
    if (state !== "open") return;
    const onDown = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setState("closed"); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setState("closed"); };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("pointerdown", onDown); document.removeEventListener("keydown", onKey); };
  }, [state]);

  useEffect(() => () => window.clearTimeout(leave.current), []);

  const peekRows = Math.min(3, Math.max(1, threads.length));
  const height =
    state === "closed" ? "48px"
    : state === "peek" ? `${72 + peekRows * 56 + 20}px`
    : `min(calc(100dvh - 28px), ${(onToggleAgentView ? 330 : 290) + threads.length * 68}px)`;
  const width = state === "closed" ? "48px" : state === "peek" ? "304px" : "372px";
  const radius = state === "closed" ? "24px" : state === "peek" ? "28px" : "32px";

  const close = () => setState("closed");
  const hidden = state === "closed";

  return (
    <div
      ref={root}
      className="mo-hist"
      data-state={state}
      style={{ width, height, borderRadius: radius }}
      onPointerEnter={(e) => { if (e.pointerType === "mouse") { window.clearTimeout(leave.current); setState((s) => (s === "closed" ? "peek" : s)); } }}
      onPointerLeave={(e) => { if (e.pointerType === "mouse") { window.clearTimeout(leave.current); leave.current = window.setTimeout(() => setState((s) => (s === "peek" ? "closed" : s)), 220); } }}
    >
      <button type="button" className="mo-hist-btn" aria-label="Chat history" aria-expanded={state === "open"}
        onClick={() => setState((s) => (s === "open" ? "closed" : "open"))}>
        <span className="mo-burger" aria-hidden="true"><i /><i /><i /></span>
      </button>

      <div className="mo-hist-body" inert={hidden} aria-hidden={hidden}>
        <div className="mo-hist-head">
          <span className="text-[15px] font-semibold text-ink">Chat history</span>
          <button type="button" className="ui-pill mo-hist-new" data-tone="accent" onClick={() => { onNew(); close(); }}>+ New chat</button>
        </div>

        <div className="mo-hist-list" role="list">
          {threads.length === 0 && <p className="px-3 py-4 text-[13px] text-ink-3">Your conversations will appear here.</p>}
          {threads.map((t, i) => (
            <div key={t.id} role="listitem" className="mo-hist-item group" data-active={t.id === activeId ? "" : undefined} style={{ ["--i" as string]: i }}>
              <button type="button" className="min-w-0 flex-1 text-left" onClick={() => { onOpen(t); close(); }}>
                <span className="block truncate text-sm text-ink">{t.title}</span>
                <span className="mt-0.5 block text-xs text-ink-3">{ago(t.updatedAt)} · {Math.ceil(t.messages.length / 2)} {t.messages.length <= 2 ? "turn" : "turns"}</span>
              </button>
              <button type="button" aria-label={`Delete ${t.title}`} className="mo-hist-del" onClick={() => onDelete(t.id)}>✕</button>
            </div>
          ))}
        </div>

        <div className="mo-hist-foot">
          <label className="flex items-center justify-between gap-3 text-[13px] text-ink-3">
            <span>User</span>
            <select value={userId} onChange={(e) => onUserChange(e.target.value)} aria-label="User" className="ui-pill min-w-0 max-w-[220px] flex-1">
              {users.map((u) => <option key={u.user_id} value={u.user_id}>{u.name} · {u.role.replaceAll("_", " ")}</option>)}
              {!users.length && <option value={userId}>{userId}</option>}
            </select>
          </label>
          {onToggleAgentView && (
            <button type="button" className="ui-pill w-full" aria-pressed={agentView} onClick={() => { onToggleAgentView(); close(); }}>
              {agentView ? "Hide agent view" : "Show agent view"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
