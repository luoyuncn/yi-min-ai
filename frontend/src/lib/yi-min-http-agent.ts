import {
  HttpAgent,
  runHttpRequest,
  transformHttpEventStream,
  type BaseEvent,
  type RunAgentInput,
} from "@copilotkit/react-core/v2";
import type { Observable } from "rxjs";

export class YiMinHttpAgent extends HttpAgent {
  private baseUrl: string;
  private activeRunId: string | null = null;

  constructor({
    baseUrl,
    threadId,
  }: {
    baseUrl: string;
    threadId: string;
  }) {
    super({
      url: `${baseUrl}/api/threads/${encodeURIComponent(threadId)}/runs`,
      threadId,
    });
    this.baseUrl = baseUrl;
  }

  override run(input: RunAgentInput): Observable<BaseEvent> {
    this.activeRunId = input.runId;
    return transformHttpEventStream(
      runHttpRequest(this.threadUrl("runs"), {
        method: "POST",
        headers: this.requestHeaders(),
        body: JSON.stringify(input),
        signal: this.abortController.signal,
      }),
      this.debugLogger,
    );
  }

  protected override connect(input: RunAgentInput): Observable<BaseEvent> {
    return transformHttpEventStream(
      runHttpRequest(this.threadUrl("connect"), {
        method: "POST",
        headers: this.requestHeaders(),
        body: JSON.stringify({ runId: input.runId }),
        signal: this.abortController.signal,
      }),
      this.debugLogger,
    );
  }

  async interruptActiveRun(): Promise<void> {
    if (!this.activeRunId) {
      return;
    }

    const response = await fetch(this.threadUrl(`runs/${encodeURIComponent(this.activeRunId)}/interrupt`), {
      method: "POST",
    });
    if (!response.ok) {
      throw new Error(`Failed to interrupt run: ${response.status}`);
    }
  }

  override clone(): YiMinHttpAgent {
    const cloned = super.clone() as YiMinHttpAgent;
    cloned.baseUrl = this.baseUrl;
    cloned.activeRunId = this.activeRunId;
    return cloned;
  }

  private threadUrl(suffix: string): string {
    return `${this.baseUrl}/api/threads/${encodeURIComponent(this.threadId)}/${suffix}`;
  }

  private requestHeaders(): Record<string, string> {
    return {
      ...this.headers,
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    };
  }
}
