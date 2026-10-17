import { useCallback, useEffect, useRef, useState } from "react";
import MorphOrb from "@/components/ui/ai-thinking-orb-and-input";
import Mascot from "@/components/ui/mascot";
import { ChatScreen, type FieldUser } from "@/components/chat/ChatScreen";
import { HistoryMenu } from "@/components/chat/HistoryMenu";
import { useAgentChat } from "@/hooks/useAgentChat";
import { deleteThread, readThreads, saveThread, type Thread } from "@/lib/history";
import SkeletonPreview from "@/components/a2ui/SkeletonPreview";
import type { SkeletonKind } from "@/components/a2ui/DotMorph";

export default function App() {
  const chat = useAgentChat();
  const [users, setUsers] = useState<FieldUser[]>([]);
  const [userId, setUserId] = useState("U-201");
  const [chatOpen, setChatOpen] = useState(false);
  const [orbGone, setOrbGone] = useState(false);
  const [fast, setFast] = useState(false);
  const [agentView, setAgentView] = useState(false);
  const [phase, setPhase] = useState<string>("idle");
  const [orbKey, setOrbKey] = useState(0);
  const [threads, setThreads] = useState<Thread[]>(() => readThreads("U-201"));
  const timer = useRef<number>(0);

  useEffect(() => {
    fetch("/api/users").then((r) => r.json()).then(setUsers).catch(() => setUsers([]));
    return () => window.clearTimeout(timer.current);
  }, []);

  // history is private to the selected user
  useEffect(() => { setThreads(readThreads(userId)); }, [userId]);

  // save a conversation once the agent has finished a turn
  const { running, messages, threadId } = chat;
  useEffect(() => {
    if (running || messages.length === 0) return;
    const stored = readThreads(userId).find((t) => t.id === threadId);
    if (stored && stored.messages.length === messages.length) return;
    setThreads(saveThread(userId, threadId, messages));
  }, [running, messages, threadId, userId]);

  // The orb's thinking animation runs while the agent streams its answer in the background.
  const onSubmit = (text: string) => {
    chat.reset();
    return chat.send(text, userId);
  };

  // Let the answer card land for a beat, then grow it into the full-screen chat.
  const onAnswered = () => {
    timer.current = window.setTimeout(() => setChatOpen(true), 500);
  };

  const openThread = useCallback((t: Thread) => {
    window.clearTimeout(timer.current);
    chat.load(t.id, t.messages);
    if (!chatOpen) { setFast(true); setOrbGone(true); setChatOpen(true); }
  }, [chat, chatOpen]);

  const removeThread = (id: string) => {
    setThreads(deleteThread(userId, id));
    if (id === threadId) chat.reset();
  };

  // "New chat" goes back to the front page, where the mascot waits
  const newChat = () => {
    window.clearTimeout(timer.current);
    chat.reset();
    setChatOpen(false); setOrbGone(false); setFast(false); setPhase("idle");
    setOrbKey((k) => k + 1);
  };

  const q = new URLSearchParams(window.location.search);
  if (import.meta.env.DEV && q.get("skel")) return <SkeletonPreview kind={q.get("skel") as SkeletonKind} at={Number(q.get("at") ?? 60)} />;

  return (
    <>
      {!orbGone && (
        <>
          <MorphOrb key={orbKey} onSubmit={onSubmit} onAnswered={onAnswered} onPhaseChange={setPhase} minThinkMs={1500} />
          <Mascot active={phase === "idle" && !chatOpen} />
          {!chatOpen && users.length > 0 && (
            <select value={userId} onChange={(e) => setUserId(e.target.value)} aria-label="User"
              className="ui-pill fixed right-4 top-4 z-10 max-w-[200px]">
              {users.map((u) => <option key={u.user_id} value={u.user_id}>{u.name} · {u.role.replaceAll("_", " ")}</option>)}
            </select>
          )}
        </>
      )}
      {chatOpen && (
        <ChatScreen chat={chat} userId={userId} onExpanded={() => setOrbGone(true)} fast={fast} agentView={agentView} onCloseAgentView={() => setAgentView(false)} />
      )}
      <HistoryMenu threads={threads} activeId={threadId} onOpen={openThread} onNew={newChat} onDelete={removeThread}
        users={users} userId={userId} onUserChange={(id) => { setUserId(id); chat.reset(); }}
        agentView={agentView} onToggleAgentView={chatOpen ? () => setAgentView((v) => !v) : undefined} />
    </>
  );
}
