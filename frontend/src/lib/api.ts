export interface ThreadSummary {
  thread_id: string;
  channel: string;
  message_count: number;
  created_at: string;
  updated_at: string;
  last_message: string;
  pending_approval: boolean;
}

export async function fetchThreads(): Promise<ThreadSummary[]> {
  const response = await fetch("/api/threads");
  if (!response.ok) {
    throw new Error(`Failed to load threads: ${response.status}`);
  }

  const payload = (await response.json()) as { items: ThreadSummary[] };
  return payload.items;
}
