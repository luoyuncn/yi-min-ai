import { useDeferredValue } from "react";
import type { ThreadSummary } from "../lib/api";

interface ThreadSidebarProps {
  activeThreadId: string;
  onNewThread: () => void;
  onSelectThread: (threadId: string) => void;
  search: string;
  setSearch: (value: string) => void;
  threads: ThreadSummary[];
}

function formatUpdatedAt(value: string): string {
  if (!value) {
    return "刚刚";
  }

  const date = new Date(value);
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function ThreadSidebar({
  activeThreadId,
  onNewThread,
  onSelectThread,
  search,
  setSearch,
  threads,
}: ThreadSidebarProps) {
  const deferredSearch = useDeferredValue(search.trim().toLowerCase());
  const filteredThreads = threads.filter((thread) => {
    if (!deferredSearch) {
      return true;
    }
    return (
      thread.thread_id.toLowerCase().includes(deferredSearch) ||
      thread.last_message.toLowerCase().includes(deferredSearch)
    );
  });

  return (
    <aside className="thread-sidebar">
      <div className="panel-title-row">
        <div>
          <p className="eyebrow">Thread Deck</p>
          <h2>会话档案</h2>
        </div>
        <button className="ghost-button" onClick={onNewThread} type="button">
          新建
        </button>
      </div>

      <label className="search-shell">
        <span>筛选</span>
        <input
          className="search-input"
          onChange={(event) => setSearch(event.target.value)}
          placeholder="按 thread 或最近消息搜索"
          value={search}
        />
      </label>

      <div className="thread-list">
        <button
          className={`thread-card ephemeral-thread ${activeThreadId.startsWith("web:new:") ? "active" : ""}`}
          onClick={onNewThread}
          type="button"
        >
          <div className="thread-card-head">
            <strong>新对话草稿</strong>
            <span>未落库</span>
          </div>
          <p>切换到一个全新的 thread，首次发言后会自动进入归档列表。</p>
        </button>

        {filteredThreads.map((thread) => (
          <button
            className={`thread-card ${thread.thread_id === activeThreadId ? "active" : ""}`}
            key={thread.thread_id}
            onClick={() => onSelectThread(thread.thread_id)}
            type="button"
          >
            <div className="thread-card-head">
              <strong>{thread.thread_id}</strong>
              <span>{formatUpdatedAt(thread.updated_at)}</span>
            </div>
            <p>{thread.last_message || "这个 thread 还没有可显示的文本内容。"}</p>
            <div className="thread-card-meta">
              <span>{thread.message_count} 条消息</span>
              {thread.pending_approval ? <span className="pending-badge">待审批</span> : null}
            </div>
          </button>
        ))}
      </div>
    </aside>
  );
}
