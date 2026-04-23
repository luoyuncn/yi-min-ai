interface ApprovalPayload {
  approval_id?: string;
  tool_name?: string;
  args?: Record<string, unknown>;
  message?: string;
}

interface ApprovalCardProps {
  event: ApprovalPayload;
  onApprove: () => void;
  onReject: () => void;
}

export function ApprovalCard({ event, onApprove, onReject }: ApprovalCardProps) {
  return (
    <section className="approval-card">
      <p className="eyebrow">Human Gate</p>
      <h3>工具执行等待确认</h3>
      <p className="approval-copy">{event.message || "当前工具被策略拦下，需要你确认是否继续。"}</p>

      <dl className="approval-grid">
        <div>
          <dt>工具</dt>
          <dd>{event.tool_name || "unknown"}</dd>
        </div>
        <div>
          <dt>审批 ID</dt>
          <dd>{event.approval_id || "unknown"}</dd>
        </div>
      </dl>

      <pre className="approval-args">{JSON.stringify(event.args ?? {}, null, 2)}</pre>

      <div className="approval-actions">
        <button className="primary-button" onClick={onApprove} type="button">
          允许执行
        </button>
        <button className="secondary-button" onClick={onReject} type="button">
          拒绝执行
        </button>
      </div>
    </section>
  );
}
