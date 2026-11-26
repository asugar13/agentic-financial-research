export type Mode = "demo" | "ollama" | "bedrock";
export const providerNames: Record<Mode, string> = {
  ollama: "Local Ollama", bedrock: "Amazon Bedrock", demo: "Offline demo",
};

export interface Config {
  modes: Mode[];
  demo_question: string;
}

export interface ResearchRunRequest {
  question: string;
  mode: Mode;
  scope?: { symbols: string[]; start_date: string; end_date: string };
  max_tool_calls: number;
  review: boolean;
}

export interface Source {
  id: string;
  title: string;
  publisher: string;
  kind: string;
  url: string;
  excerpt: string;
  period_start: string;
  period_end: string;
}

export interface ResearchRunResponse {
  id: string;
  question: string;
  mode: Mode;
  status: "running" | "succeeded" | "failed";
  saved_at: string | null;
  events: string[];
  sources: Source[];
  report: {
    title: string;
    claims: { statement: string; kind: string; evidence_ids: string[] }[];
    limitations: string[];
  } | null;
  error: string | null;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch("/api/" + path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = Array.isArray(body.detail)
      ? body.detail.map((issue: { msg: string }) => issue.msg).join(" ")
      : body.detail;
    throw new Error(detail || `Request failed (HTTP ${response.status}).`);
  }
  return response.json();
}
