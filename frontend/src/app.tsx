import {
  CopilotChat,
  CopilotChatConfigurationProvider,
  CopilotKitProvider,
  useAgent,
  useInterrupt,
} from "@copilotkit/react-core/v2";
import { startTransition, useEffect, useEffectEvent, useMemo, useState } from "react";
import { ApprovalCard } from "./components/approval-card";
import { ThreadSidebar } from "./components/thread-sidebar";
import { fetchThreads, type ThreadSummary } from "./lib/api";
import { YiMinHttpAgent } from "./lib/yi-min-http-agent";

function createThreadId(): string {
  return `web:new:${crypto.randomUUID()}`;
}

const IconSidebar = () => (
  <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
    <rect x="1.5" y="1.5" width="15" height="15" rx="3" stroke="currentColor" strokeWidth="1.4"/>
    <line x1="6" y1="1.5" x2="6" y2="16.5" stroke="currentColor" strokeWidth="1.4"/>
  </svg>
);

const IconCompose = () => (
  <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
    <path d="M9 14H15" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
    <path d="M3 14L3.6 11.4L12 3L15 6L6.6 14.4L3 14Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"/>
  </svg>
);

function AppShell({
  selectedThreadId,
  onNewThread,
  onSelectThread,
  threads,
  refreshThreads,
}: {
  selectedThreadId: string;
  onNewThread: () => void;
  onSelectThread: (id: string) => void;
  threads: ThreadSummary[];
  refreshThreads: () => void;
}) {
  const [panelOpen, setPanelOpen] = useState(true);
  const [search, setSearch] = useState("");
  const [runError, setRunError] = useState<string | null>(null);
  const [runActive, setRunActive] = useState(false);
  const { agent } = useAgent();

  const refreshEvent = useEffectEvent(() => {
    startTransition(() => refreshThreads());
  });

  useEffect(() => {
    const sub = agent.subscribe({
      onRunStartedEvent: () => { setRunActive(true); setRunError(null); },
      onRunFinishedEvent: () => { setRunActive(false); refreshEvent(); },
      onRunErrorEvent: ({ event }) => { setRunActive(false); setRunError(event.message); refreshEvent(); },
    });
    return () => sub.unsubscribe();
  }, [agent, refreshEvent]);

  useEffect(() => {
    localStorage.setItem("yiminai.activeThreadId", selectedThreadId);
  }, [selectedThreadId]);

  const approvalElement = useInterrupt({
    renderInChat: false,
    render: ({ event, resolve }) => (
      <ApprovalCard
        event={(event.value ?? {}) as Record<string, unknown>}
        onApprove={() => resolve({ approved: true })}
        onReject={() => resolve({ approved: false })}
      />
    ),
  });

  const handleStop = useEffectEvent(async () => {
    try {
      await (agent as YiMinHttpAgent).interruptActiveRun();
    } catch (e) {
      console.error("Failed to interrupt", e);
    }
  });

  return (
    <div className="app-shell">
      <nav className="app-nav">
        <div className="nav-section">
          <button
            className={`nav-btn${panelOpen ? " nav-btn--active" : ""}`}
            onClick={() => setPanelOpen((v) => !v)}
            title="会话列表"
            type="button"
          >
            <IconSidebar />
          </button>
          <button className="nav-btn" onClick={onNewThread} title="新对话" type="button">
            <IconCompose />
          </button>
        </div>
        <div className="nav-section">
          {runActive && <span className="nav-pulse" title="运行中" />}
        </div>
      </nav>

      <aside className={`thread-panel${panelOpen ? " thread-panel--open" : ""}`}>
        <ThreadSidebar
          activeThreadId={selectedThreadId}
          onNewThread={onNewThread}
          onSelectThread={onSelectThread}
          search={search}
          setSearch={setSearch}
          threads={threads}
        />
      </aside>

      <main className="chat-main">
        {runError && <div className="chat-error-bar">{runError}</div>}
        <div className="chat-wrapper">
          <CopilotChat
            labels={{ welcomeMessageText: "有什么我可以帮你的？" }}
            onStop={() => { void handleStop(); }}
          />
        </div>
      </main>

      {approvalElement && (
        <div className="approval-overlay">
          <div className="approval-modal">
            {approvalElement}
          </div>
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [selectedThreadId, setSelectedThreadId] = useState<string>(
    () => localStorage.getItem("yiminai.activeThreadId") || createThreadId(),
  );
  const baseUrl = window.location.origin;

  const baseAgent = useMemo(
    () => new YiMinHttpAgent({ baseUrl, threadId: selectedThreadId }),
    [baseUrl],
  );

  const refreshThreads = useEffectEvent(async () => {
    try {
      setThreads(await fetchThreads());
    } catch (e) {
      console.error("Failed to refresh threads", e);
    }
  });

  useEffect(() => {
    void refreshThreads();
  }, []);

  return (
    <CopilotKitProvider agents__unsafe_dev_only={{ default: baseAgent }}>
      <CopilotChatConfigurationProvider threadId={selectedThreadId}>
        <AppShell
          onNewThread={() => setSelectedThreadId(createThreadId())}
          onSelectThread={setSelectedThreadId}
          refreshThreads={() => { void refreshThreads(); }}
          selectedThreadId={selectedThreadId}
          threads={threads}
        />
      </CopilotChatConfigurationProvider>
    </CopilotKitProvider>
  );
}
