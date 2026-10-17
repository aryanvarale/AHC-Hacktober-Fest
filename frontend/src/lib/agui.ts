/* Minimal AG-UI client: POST a RunAgentInput, read the server-sent event stream. */

export interface AgUiEvent {
  type: string;
  timestamp?: number;
  [key: string]: unknown;
}

export interface RunInput {
  threadId: string;
  runId: string;
  messages: { id: string; role: "user" | "assistant"; content: string }[];
  state: Record<string, unknown>;
  tools: unknown[];
  context: unknown[];
  forwardedProps: Record<string, unknown>;
}

export async function runAgent(url: string, input: RunInput, onEvent: (e: AgUiEvent) => void, signal?: AbortSignal) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(input),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`Agent request failed (HTTP ${res.status})`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const data = frame.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("");
      if (!data) continue;
      try { onEvent(JSON.parse(data) as AgUiEvent); } catch { /* ignore a malformed frame */ }
    }
  }
}

export const uid = (p: string) => `${p}-${Math.random().toString(36).slice(2, 10)}`;
