import { useEffect, useRef, useState } from "react";
import { A2UIRenderer } from "@/components/a2ui/A2UIRenderer";
import { AgentPanel } from "@/components/chat/AgentPanel";
import { EvidenceDrawer, type EvidenceRef } from "@/components/chat/EvidenceDrawer";
import type { useAgentChat } from "@/hooks/useAgentChat";

export interface FieldUser { user_id: string; name: string; role: string }
type Chat = ReturnType<typeof useAgentChat>;

/** Evidence ids the answer mentions inside its sentences, e.g. INS-0001-0203 or CTX-0001-003. */
const CITE = /\b(?:INS|CTX|MNT|REG)-\d{4}-[A-Za-z0-9-]*\d\b/g;
const citedIds = (text: string) => [...new Set(text.match(CITE) ?? [])];

/** The answer text, with each cited evidence id turned into a button that opens the source record. */
function CitedText({ text, onOpen }: { text: string; onOpen: (id: string) => void }) {
  const parts = text.split(new RegExp(`(${CITE.source})`, "g"));
  return (
    <>
      {parts.map((p, i) =>
        i % 2 ? (
          <button key={i} type="button" onClick={() => onOpen(p)} title="Open the source record"
            className="ui-chip mx-0.5 border border-transparent align-baseline transition hover:border-accent/40 hover:bg-accent/10 hover:text-ink">{p}</button>
        ) : p,
      )}
    </>
  );
}

const duration = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`);

function took(m: { latencyMs?: number; decisionMs?: number; answerMs?: number }) {
  const total = `Responded in ${duration(m.latencyMs ?? 0)}`;
  if (m.decisionMs == null || m.answerMs == null) return total;
  return `${total} · ${duration(m.decisionMs)} to choose the data · ${duration(m.answerMs)} to write the answer`;
}

const STEP_LABEL: Record<string, string> = {
  starting: "Starting", understand: "Understanding the question", retrieve: "Searching field records", compose: "Composing the answer",
};

interface Props {
  chat: Chat;
  userId: string;
  onExpanded: () => void;
  /** opened from history: no grow-from-card animation */
  fast?: boolean;
  /** the agent's side of the conversation (AG-UI events, A2UI JSON, state), toggled from the history menu */
  agentView: boolean;
  onCloseAgentView: () => void;
}

/** Full-screen chat. It grows out of the answer card, so the orb's last frame leads straight into it. */
export function ChatScreen({ chat, userId, onExpanded, fast, agentView, onCloseAgentView }: Props) {
  const { messages, events, agentState, running, send } = chat;
  const [value, setValue] = useState("");
  const [evidence, setEvidence] = useState<EvidenceRef | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  useEffect(() => { if (!running) input.current?.focus({ preventScroll: true }); }, [running]);

  const ask = (q: string) => {
    const text = q.trim();
    if (!text || running) return;
    setValue("");
    if (input.current) input.current.style.height = "";
    void send(text, userId);
  };


  return (
    <div className={fast ? "mo-chat mo-chat-fast" : "mo-chat"} onAnimationEnd={(e) => { if (e.target === e.currentTarget) onExpanded(); }}>
      <div className="mo-chat-inner flex">
        <div className="mo-chat-body flex min-w-0 flex-1 flex-col">

          <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto px-4 pb-6 pt-16 sm:px-6">
            <div className="mx-auto flex max-w-3xl flex-col gap-6">
              {messages.length === 0 && (
                <div className="mo-msg flex flex-col items-center gap-4 pt-[18vh] text-center">
                  <h2 className="text-lg font-semibold text-ink">What do you want to know about your wells?</h2>
                  <div className="flex flex-wrap justify-center gap-2">
                    {["Which wells in Northeast Block 7 are not producing?", "Show spills heatmap for 2026", "Is WELL-0001 producing right now?"].map((q) => (
                      <button key={q} className="ui-pill" onClick={() => ask(q)}>{q}</button>
                    ))}
                  </div>
                </div>
              )}
              {messages.map((m) =>
                m.role === "user" ? (
                  <div key={m.id} className="mo-msg flex justify-end">
                    <div className="max-w-[85%] rounded-2xl rounded-br-md bg-white/10 px-4 py-2.5 text-sm text-ink">{m.text}</div>
                  </div>
                ) : (
                  <div key={m.id} className="mo-msg flex gap-3">
                    <div className="mt-0.5 size-6 shrink-0 rounded-full bg-gradient-to-br from-[#8a7dff] to-[#4d39f2]" />
                    <div className="flex min-w-0 flex-1 flex-col gap-3">
                      {m.status === "streaming" && !m.text && (
                        <div className="flex items-center gap-2 text-xs text-ink-3">
                          <span className="mo-lab-t">{STEP_LABEL[m.step ?? "starting"] ?? m.step}</span>
                          <span className="mo-dots" aria-hidden="true"><i /><i /><i /></span>
                        </div>
                      )}
                      {m.text && (
                        <p className={`text-sm leading-relaxed ${m.status === "error" ? "text-red-300" : "text-ink-2"}`}>
                          <CitedText text={m.text} onOpen={(id) => setEvidence({ id, ids: citedIds(m.text), tool: m.tools[0]?.name ?? "" })} />
                          {m.status === "streaming" && <span className="mo-caret" />}
                        </p>
                      )}
                      {m.status !== "streaming" && m.latencyMs != null && <p className="text-[11px] text-ink-3">{took(m)}</p>}
                      {m.surfaces.map((s) => (
                        <A2UIRenderer key={s.id} surface={s} disabled={running} instant={m.restored} onAction={(name, ctx) => (name === "evidence" ? setEvidence({ id: ctx.id, ids: ctx.ids, tool: ctx.tool }) : ask(String(ctx.question)))} />
                      ))}
                    </div>
                  </div>
                ),
              )}
            </div>
          </div>

          <form onSubmit={(e) => { e.preventDefault(); ask(value); }} className="px-4 pb-5 pt-2 sm:px-6">
            <div className="mo-chat-pill mx-auto flex max-w-3xl items-end gap-2 rounded-[28px] border border-line-strong bg-raised py-2 pl-5 pr-2">
              <textarea ref={input} rows={1} value={value} maxLength={1000} disabled={running}
                onChange={(e) => {
                  const el = e.target;
                  const flat = el.value.replace(/\s*\n+\s*/g, " "); // one question, one paragraph
                  setValue(flat);
                  el.style.height = "auto";
                  el.style.height = `${Math.min(el.scrollHeight, 110)}px`; // up to five lines, then it scrolls inside
                }}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); if (!e.shiftKey) ask(value); } }}
                placeholder={running ? "Working on it…" : "Ask a follow-up…"} aria-label="Ask a follow-up"
                className="mo-chat-field mb-[9px] min-w-0 flex-1 resize-none bg-transparent text-[15px] leading-[22px] text-ink outline-none placeholder:text-ink-3" />
              <button type="submit" disabled={running || !value.trim()} aria-label="Send"
                className="grid size-10 place-items-center rounded-full bg-white text-black transition active:scale-95 disabled:bg-white/10 disabled:text-ink-3">
                <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M12 19V5" /><path d="M5.5 11.5L12 5l6.5 6.5" />
                </svg>
              </button>
            </div>
          </form>
        </div>

        {(evidence || agentView) && (
          <div className="absolute inset-y-0 right-0 z-10 w-full md:static md:w-auto">
            {evidence ? <EvidenceDrawer refd={evidence} onNavigate={setEvidence} onClose={() => setEvidence(null)} /> : <AgentPanel events={events} state={agentState} messages={messages} onClose={onCloseAgentView} />}
          </div>
        )}
      </div>
    </div>
  );
}
