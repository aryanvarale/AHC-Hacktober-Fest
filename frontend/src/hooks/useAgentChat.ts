import { useCallback, useRef, useState } from "react";
import { applyA2UI, type Surface } from "@/lib/a2ui";
import { runAgent, uid, type AgUiEvent } from "@/lib/agui";

export interface ToolCall { id: string; name: string; args: string; result?: string; done: boolean }

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  tools: ToolCall[];
  surfaces: Surface[];
  step?: string;
  status: "streaming" | "done" | "error";
  /** loaded from history: show at once, no entrance animation */
  restored?: boolean;
  latencyMs?: number;
  decisionMs?: number;
  answerMs?: number;
}

export interface AgentState {
  memory?: { asset?: string | null; area?: string | null; wells?: string[] };
  lastTool?: string | null;
  latencyMs?: number;
  decisionMs?: number;
  answerMs?: number;
  path?: string;
  [k: string]: unknown;
}

export function useAgentChat(endpoint = "/api/agui") {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [events, setEvents] = useState<AgUiEvent[]>([]);
  const [agentState, setAgentState] = useState<AgentState>({});
  const [running, setRunning] = useState(false);
  const [threadId, setThreadId] = useState(() => uid("thread"));
  const threadRef = useRef(threadId);
  const historyRef = useRef<{ id: string; role: "user" | "assistant"; content: string }[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    threadRef.current = uid("thread");
    setThreadId(threadRef.current);
    historyRef.current = [];
    setMessages([]);
    setEvents([]);
    setAgentState({});
    setRunning(false);
  }, []);

  /** Opens a saved conversation; the agent gets its text back as context. */
  const load = useCallback((id: string, saved: ChatMessage[]) => {
    abortRef.current?.abort();
    threadRef.current = id;
    setThreadId(id);
    historyRef.current = saved.map((m) => ({ id: m.id, role: m.role, content: m.text }));
    setMessages(saved.map((m) => ({ ...m, restored: true })));
    setEvents([]);
    setAgentState({});
    setRunning(false);
  }, []);

  /** Runs the agent for one user turn. Resolves with the assistant's text. */
  const send = useCallback(async (text: string, userId: string): Promise<string> => {
    const aId = uid("a");
    const userMsg = { id: uid("u"), role: "user" as const, content: text };
    historyRef.current = [...historyRef.current, userMsg];
    setMessages((m) => [
      ...m,
      { id: userMsg.id, role: "user", text, tools: [], surfaces: [], status: "done" },
      { id: aId, role: "assistant", text: "", tools: [], surfaces: [], status: "streaming", step: "starting" },
    ]);
    setRunning(true);

    const patch = (fn: (m: ChatMessage) => ChatMessage) => setMessages((all) => all.map((m) => (m.id === aId ? fn(m) : m)));
    let finalText = "";
    const ac = new AbortController();
    abortRef.current = ac;

    const handle = (e: AgUiEvent) => {
      setEvents((all) => [...all, e]);
      switch (e.type) {
        case "STEP_STARTED":
          patch((m) => ({ ...m, step: String(e.stepName) }));
          break;
        case "TEXT_MESSAGE_CONTENT":
          finalText += String(e.delta);
          patch((m) => ({ ...m, text: m.text + String(e.delta) }));
          break;
        case "TOOL_CALL_START":
          patch((m) => ({ ...m, tools: [...m.tools, { id: String(e.toolCallId), name: String(e.toolCallName), args: "", done: false }] }));
          break;
        case "TOOL_CALL_ARGS":
          patch((m) => ({ ...m, tools: m.tools.map((t) => (t.id === e.toolCallId ? { ...t, args: t.args + String(e.delta) } : t)) }));
          break;
        case "TOOL_CALL_END":
          patch((m) => ({ ...m, tools: m.tools.map((t) => (t.id === e.toolCallId ? { ...t, done: true } : t)) }));
          break;
        case "TOOL_CALL_RESULT":
          patch((m) => ({ ...m, tools: m.tools.map((t) => (t.id === e.toolCallId ? { ...t, result: String(e.content), done: true } : t)) }));
          break;
        case "STATE_SNAPSHOT": {
          const snap = e.snapshot as AgentState;
          setAgentState(snap);
          if (typeof snap.latencyMs === "number") {
            patch((m) => ({ ...m, latencyMs: snap.latencyMs, decisionMs: snap.decisionMs, answerMs: snap.answerMs }));
          }
          break;
        }
        case "CUSTOM":
          if (e.name === "a2ui") {
            const v = e.value as { surfaceId: string; messages: Record<string, unknown>[] };
            patch((m) => {
              let s = m.surfaces.find((x) => x.id === v.surfaceId);
              for (const msg of v.messages) s = applyA2UI(s, v.surfaceId, msg);
              return { ...m, surfaces: [...m.surfaces.filter((x) => x.id !== v.surfaceId), s!] };
            });
          }
          break;
        case "RUN_ERROR":
          finalText = String(e.message);
          patch((m) => ({ ...m, status: "error", text: m.text || `Something went wrong: ${e.message}`, step: undefined }));
          break;
        case "RUN_FINISHED":
          patch((m) => ({ ...m, status: m.status === "error" ? "error" : "done", step: undefined }));
          break;
      }
    };

    try {
      await runAgent(endpoint, {
        threadId: threadRef.current,
        runId: uid("run"),
        messages: historyRef.current,
        state: { userId },
        tools: [], context: [], forwardedProps: {},
      }, handle, ac.signal);
    } catch (err) {
      if (!ac.signal.aborted) {
        finalText = err instanceof Error ? err.message : "The agent could not be reached.";
        patch((m) => ({ ...m, status: "error", text: `I couldn't reach the agent. ${finalText}`, step: undefined }));
      }
    } finally {
      setRunning(false);
    }
    historyRef.current = [...historyRef.current, { id: aId, role: "assistant", content: finalText }];
    return finalText;
  }, [endpoint]);

  return { messages, events, agentState, running, send, reset, load, threadId };
}
