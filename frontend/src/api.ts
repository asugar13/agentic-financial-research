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

export type EventCategory =
  "earnings" | "guidance" | "legal" | "regulatory" | "management" | "deal" | "product" | "other";

export interface CompanyEvent {
  reported_date: string;
  scheduled_date: string | null;
  category: EventCategory;
  headline: string;
  why_it_matters: string;
  confidence: "low" | "medium" | "high" | null;
  sources: { title: string; url: string }[];
  upcoming: boolean;
  reaction: {
    from_date: string;
    to_date: string;
    stock_pct: number;
    benchmark_pct: number | null;
    excess_pct: number | null;
  } | null;
}

export interface SearchSettings {
  focus: string;
  include_domains: string[];
  exclude_domains: string[];
  num_results: 10 | 25;
}

export interface WatchlistScan {
  start_date: string;
  end_date: string;
  benchmark: string;
  // Missing on scans saved by older versions of the page.
  settings?: SearchSettings;
  companies: {
    ticker: string;
    name: string | null;
    events: CompanyEvent[];
    error: string | null;
    scanned_at: string | null;
    stale: boolean;
  }[];
}

export interface PriceSeries {
  ticker: string;
  name: string | null;
  currency: string | null;
  prices: { date: string; close: number }[];
  change_pct: number | null;
  error: string | null;
  fetched_at: string | null;
  stale: boolean;
}

export interface WatchlistPrices {
  start_date: string;
  end_date: string;
  series: PriceSeries[];
}

export interface Citation {
  title: string;
  url: string;
}

export interface Finding {
  text: string;
  sources: Citation[];
  confidence: "low" | "medium" | "high" | null;
}

export interface EventExplanation {
  ticker: string;
  name: string | null;
  currency: string | null;
  reported_date: string;
  headline: string;
  summary: Finding;
  drivers: { point: string; evidence: string; sources: Citation[]; confidence: Finding["confidence"] }[];
  market_context: Finding;
  upcoming: Finding | null;
  sources: { title: string; url: string; published_date: string | null }[];
  prices: { date: string; close: number }[];
  reaction: CompanyEvent["reaction"];
  settings: SearchSettings;
  explained_at: string;
  stale: boolean;
}
