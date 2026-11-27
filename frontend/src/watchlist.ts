import type { CompanyEvent, WatchlistScan } from "./api";

export const DEFAULT_WATCHLIST = "NVDA, AAPL, TSLA, PFE, BA";
export const LOOKBACKS = [7, 30, 90];

export interface Row extends CompanyEvent {
  key: string;
  ticker: string;
  name: string | null;
}

export function parseTickers(watchlist: string): string[] {
  return [...new Set(watchlist.split(/[\s,]+/).map((ticker) => ticker.trim().toUpperCase()).filter(Boolean))];
}

// Both tabs build rows the same way, so a dot on a chart and a table row share one key.
export function eventRows(scan: WatchlistScan | null): Row[] {
  return (scan?.companies ?? [])
    .flatMap((company) => company.events.map((item, index) => ({
      ...item, key: `${company.ticker}-${index}`, ticker: company.ticker, name: company.name,
    })))
    .sort((left, right) => right.reported_date.localeCompare(left.reported_date));
}

// Browser storage is a convenience; it can be blocked, so every access is guarded.
export function readStored<T>(storage: () => Storage, key: string, fallback: T): T {
  try {
    const value = storage().getItem(key);
    return value === null ? fallback : (JSON.parse(value) as T);
  } catch {
    return fallback;
  }
}

export function writeStored(storage: () => Storage, key: string, value: unknown) {
  try {
    storage().setItem(key, JSON.stringify(value));
  } catch {
    // Ignore blocked or full storage; the page still works.
  }
}

const dateFormat = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
export const formatDate = (iso: string) => dateFormat.format(new Date(iso + "T00:00:00Z"));
export const isoDay = (day: Date) => day.toISOString().slice(0, 10);
export const signed = (value: number) => (value > 0 ? "+" : "") + value.toFixed(1) + "%";

export function host(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function moveLabel(row: Row): { text: string; tone: "up" | "down" | "flat" } {
  const reaction = row.reaction;
  if (!reaction) return { text: "—", tone: "flat" };
  const value = reaction.excess_pct ?? reaction.stock_pct;
  return {
    text: signed(value) + (reaction.excess_pct === null ? "" : " vs S&P"),
    tone: Math.abs(value) < 1 ? "flat" : value > 0 ? "up" : "down",
  };
}
