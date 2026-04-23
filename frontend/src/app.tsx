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

function AppShell({
  selectedThreadId,
  onNewThread,
  onSelectThread,
  threads,
  refreshThreads,
}: {
  selectedThreadId: string;
  onNewThread: () => void;
  onSelectThread: (threadId: string) => void;
  refreshThreads: () => void;
  threads: ThreadSummary[];
}) {
  const [search, setSearch] = useState("");
  const [runState, setRunState] = useState<{
    active: boolean;
    runId: string | null;
    error: string | null;
  }>({
    active: false,
    runId: null,
    error: null,
  });
  const { agent } = useAgent();
  const refreshThreadsEvent = useEffectEvent(() => {
    startTransition(() => {
      refreshThreads();
    });
  });

  useEffect(() => {
    const subscription = agent.subscribe({
      onRunStartedEvent: ({ event }) => {
        setRunState({ active: true, runId: event.runId, error: null });
      },
      onRunFinishedEvent: () => {
        setRunState((current) => ({ ...current, active: false }));
        refreshThreadsEvent();
      },
      onRunErrorEvent: ({ event }) => {
        setRunState((current) => ({
          ...current,
          active: false,
          error: event.message,
        }));
        refreshThreadsEvent();
      },
    });

    return () => subscription.unsubscribe();
  }, [agent, refreshThreadsEvent]);

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
    const yiMinAgent = agent as YiMinHttpAgent;
    try {
      await yiMinAgent.interruptActiveRun();
    } catch (error) {
      console.error("Failed to interrupt active run", error);
    }
  });

  const activeThread = threads.find((thread) => thread.thread_id === selectedThreadId) ?? null;

  return (
    <div className="console-shell">
      <header className="hero-panel">
        <div>
          <p className="eyebrow">Copilot Console</p>
          <h1>yi-min-ai Agent Atelier</h1>
          <p className="hero-copy">
            用 CopilotKit 承接对话体验，用 Python runtime 继续作为执行内核。左侧切 thread，中间看 agent，对右侧做审批。
          </p>
        </div>

        <div className="hero-status-grid">
          <article className={`status-card ${runState.active ? "live" : ""}`}>
            <span>运行状态</span>
            <strong>{runState.active ? "执行中" : "空闲"}</strong>
            <small>{runState.runId ?? "等待下一次 run"}</small>
          </article>
          <article className="status-card">
            <span>当前 Thread</span>
            <strong>{selectedThreadId}</strong>
            <small>{activeThread ? `${activeThread.message_count} 条消息` : "新线程"}</small>
          </article>
          <article className="status-card">
            <span>审批状态</span>
            <strong>{activeThread?.pending_approval ? "待处理" : "清爽"}</strong>
            <small>{activeThread?.pending_approval ? "等待你的决策" : "没有挂起工具"}</small>
          </article>
        </div>
      </header>

      <main className="workspace-grid">
        <ThreadSidebar
          activeThreadId={selectedThreadId}
          onNewThread={onNewThread}
          onSelectThread={onSelectThread}
          search={search}
          setSearch={setSearch}
          threads={threads}
        />

        <section className="chat-stage">
          <div className="panel-title-row chat-panel-header">
            <div>
              <p className="eyebrow">Agent Surface</p>
              <h2>实时对话</h2>
            </div>
            {runState.error ? <span className="error-pill">{runState.error}</span> : null}
          </div>

          <div className="chat-shell">
            <CopilotChat
              labels={{
                title: "yi-min-ai",
                initial: "想让 Agent 帮你处理什么？",
              }}
              onStop={() => {
                void handleStop();
              }}
            />
          </div>
        </section>

        <aside className="inspector-rail">
          <section className="context-card">
            <p className="eyebrow">Thread Notes</p>
            <h3>当前会话摘要</h3>
            <p>{activeThread?.last_message || "这个 thread 还没有历史摘要，发送第一条消息后这里会自动刷新。"}</p>
          </section>

          {approvalElement ?? (
            <section className="context-card muted-card">
              <p className="eyebrow">Approval Deck</p>
              <h3>当前没有挂起审批</h3>
              <p>命中 `file_write` / `memory_write` 之类的受控工具时，这里会弹出可恢复的审批卡片。</p>
            </section>
          )}
        </aside>
      </main>
    </div>
  );
}

export default function App() {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [selectedThreadId, setSelectedThreadId] = useState<string>(() => {
    return localStorage.getItem("yiminai.activeThreadId") || createThreadId();
  });
  const baseUrl = window.location.origin;

  const baseAgent = useMemo(
    () =>
      new YiMinHttpAgent({
        baseUrl,
        threadId: selectedThreadId,
      }),
    [baseUrl],
  );

  const refreshThreads = useEffectEvent(async () => {
    try {
      const nextThreads = await fetchThreads();
      setThreads(nextThreads);
    } catch (error) {
      console.error("Failed to refresh threads", error);
    }
  });

  useEffect(() => {
    void refreshThreads();
  }, [refreshThreads]);

  return (
    <CopilotKitProvider agents__unsafe_dev_only={{ default: baseAgent }}>
      <CopilotChatConfigurationProvider threadId={selectedThreadId}>
        <AppShell
          onNewThread={() => setSelectedThreadId(createThreadId())}
          onSelectThread={setSelectedThreadId}
          refreshThreads={() => {
            void refreshThreads();
          }}
          selectedThreadId={selectedThreadId}
          threads={threads}
        />
      </CopilotChatConfigurationProvider>
    </CopilotKitProvider>
  );
}
