import { useEffect, useRef, useState } from "react";
import type { AgentState, ChatMessage } from "@/hooks/useAgentChat";
import type { AgUiEvent } from "@/lib/agui";

const tone: Record<string, string> = {
  RUN: "bg-white/10 text-ink-2",
  STEP: "bg-violet-400/15 text-violet-200",
  TOOL: "bg-amber-400/15 text-amber-200",
  TEXT: "bg-sky-400/15 text-sky-200",
  STATE: "bg-accent/15 text-[#cfc8ff]",
  CUSTOM: "bg-pink-400/15 text-pink-200",
};
const chip = (t: string) => tone[t.split("_")[0]] ?? tone.RUN;

function summary(e: AgUiEvent) {
  switch (e.type) {
    case "TEXT_MESSAGE_CONTENT": return JSON.stringify(e.delta);
    case "STEP_STARTED": case "STEP_FINISHED": return String(e.stepName);
    case "TOOL_CALL_START": return String(e.toolCallName);
    case "TOOL_CALL_ARGS": return String(e.delta);
    case "TOOL_CALL_RESULT": return String(e.content).slice(0, 90);
    case "CUSTOM": return `${e.name} · ${(e.value as { messages?: unknown[] })?.messages?.length ?? 0} messages`;
    case "STATE_SNAPSHOT": return "memory + latency";
    case "RUN_ERROR": return String(e.message);
    default: return "";
  }
}

/** The agent's side of the conversation: what travelled over AG-UI and A2UI. */
export function AgentPanel({ events, state, messages, onClose }: { events: AgUiEvent[]; state: AgentState; messages: ChatMessage[]; onClose: () => void }) {
  const [tab, setTab] = useState<"events" | "a2ui" | "state">("events");
  const [compact, setCompact] = useState(true);
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [events.length, tab]);

  const shown = compact ? events.filter((e) => e.type !== "TEXT_MESSAGE_CONTENT" && e.type !== "TOOL_CALL_ARGS") : events;
  const surfaces = messages.flatMap((m) => m.surfaces);
  const lastSurface = surfaces[surfaces.length - 1];
  const t0 = events[0]?.timestamp ?? 0;

  return (
    <aside className="flex h-full w-full flex-col border-l border-line bg-[#0d1114] md:w-[380px]">
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <div>
          <div className="text-sm font-medium text-ink">Agent view</div>
          <div className="text-[11px] text-ink-3">AG-UI events · A2UI surface · state</div>
        </div>
        <button onClick={onClose} aria-label="Close agent view" className="rounded-lg px-2 py-1 text-ink-3 hover:bg-white/10 hover:text-ink">✕</button>
      </div>
      <div className="flex gap-1 border-b border-line px-3 py-2 text-xs">
        {(["events", "a2ui", "state"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`rounded-full px-3 py-1 capitalize ${tab === t ? "bg-white text-black" : "text-ink-3 hover:bg-white/10"}`}>
            {t === "a2ui" ? "A2UI JSON" : t}
          </button>
        ))}
        {tab === "events" && (
          <button onClick={() => setCompact(!compact)} className="ml-auto text-ink-3 hover:text-ink">{compact ? "show all" : "compact"}</button>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-3 text-xs">
        {tab === "events" && (
          events.length === 0 ? <p className="text-ink-3">No events yet.</p> : (
            <ol className="flex flex-col gap-1.5">
              {shown.map((e, i) => (
                <li key={i} className="flex items-start gap-2">
                  <span className="w-12 shrink-0 pt-0.5 text-right text-[10px] tabular-nums text-ink-3">+{((e.timestamp ?? t0) - t0)}ms</span>
                  <span className={`shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] ${chip(e.type)}`}>{e.type}</span>
                  <span className="min-w-0 break-words text-ink-3">{summary(e)}</span>
                </li>
              ))}
            </ol>
          )
        )}
        {tab === "a2ui" && (
          lastSurface ? (
            <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-[#e4e0ff]/80">{JSON.stringify(lastSurface.raw, null, 2)}</pre>
          ) : <p className="text-ink-3">The agent has not generated a surface yet.</p>
        )}
        {tab === "state" && (
          <div className="flex flex-col gap-3">
            <Row k="Path" v={state.path ?? "—"} />
            <Row k="Last tool" v={state.lastTool ?? "—"} />
            <Row k="Latency" v={state.latencyMs != null ? `${state.latencyMs} ms` : "—"} />
            <Row k="Focus well" v={state.memory?.asset ?? "—"} />
            <Row k="Focus area" v={state.memory?.area ?? "—"} />
            <div>
              <div className="mb-1 text-ink-3">Wells in this user's memory</div>
              <div className="flex flex-wrap gap-1.5">
                {(state.memory?.wells ?? []).map((w) => <code key={w} className="rounded bg-white/5 px-1.5 py-0.5 text-ink-2">{w}</code>)}
                {!state.memory?.wells?.length && <span className="text-ink-3">none yet</span>}
              </div>
            </div>
            <div>
              <div className="mb-1 text-ink-3">Tool calls</div>
              {messages.flatMap((m) => m.tools).map((t) => (
                <details key={t.id} className="mb-1 rounded-lg bg-white/5 px-2.5 py-1.5">
                  <summary className="cursor-pointer text-ink-2">{t.name} <span className="text-ink-3">{t.done ? "✓" : "…"}</span></summary>
                  <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[10.5px] text-ink-3">args {t.args}{"\n"}result {t.result}</pre>
                </details>
              ))}
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>
    </aside>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return <div className="flex justify-between gap-3"><span className="text-ink-3">{k}</span><span className="text-ink-2">{v}</span></div>;
}
