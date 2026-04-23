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

function timeLabel(value: string): string {
  if (!value) return "";
  const diff = Date.now() - new Date(value).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  return `${Math.floor(hrs / 24)}d`;
}

export function ThreadSidebar({
  activeThreadId,
  onNewThread,
  onSelectThread,
  search,
  setSearch,
  threads,
}: ThreadSidebarProps) {
  const query = useDeferredValue(search.trim().toLowerCase());
  const filtered = threads.filter((t) =>
    !query ||
    t.thread_id.toLowerCase().includes(query) ||
    t.last_message.toLowerCase().includes(query),
  );

  return (
    <div className="thread-sidebar">
      <div className="thread-sidebar-header">
        <span className="thread-sidebar-label">会话</span>
        <span className="thread-sidebar-label" style={{ color: "var(--text-3)" }}>
          {threads.length}
        </span>
      </div>

      <div className="search-shell">
        <input
          className="search-input"
          onChange={(e) => setSearch(e.target.value)}
          placeholder="搜索对话…"
          value={search}
        />
      </div>

      <div className="thread-list">
        <button
          className={`thread-card ephemeral-thread${activeThreadId.startsWith("web:new:") ? " active" : ""}`}
          onClick={onNewThread}
          type="button"
        >
          <div className="thread-card-id">新对话草稿</div>
          <div className="thread-card-msg">点击开始一次全新的对话</div>
        </button>

        {filtered.map((thread) => (
          <button
            className={`thread-card${thread.thread_id === activeThreadId ? " active" : ""}`}
            key={thread.thread_id}
            onClick={() => onSelectThread(thread.thread_id)}
            type="button"
          >
            <div className="thread-card-id">{thread.thread_id}</div>
            <div className="thread-card-msg">
              {thread.last_message || "暂无消息"}
            </div>
            <div className="thread-card-meta">
              <span>{thread.message_count} 条</span>
              <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                {thread.pending_approval && (
                  <span className="pending-badge">待审批</span>
                )}
                <span>{timeLabel(thread.updated_at)}</span>
              </span>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
