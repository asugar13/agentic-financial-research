import { Fragment, useState, type FormEvent } from "react";
import { api, type CompanyEvent, type Config, type WatchlistScan } from "./api";

const DEFAULT_WATCHLIST = "NVDA, AAPL, TSLA, PFE, BA";
const STORAGE_KEY = "radar-watchlist";
const LOOKBACKS = [7, 30, 90];

interface Row extends CompanyEvent {
  key: string;
  ticker: string;
  name: string | null;
}

// The watchlist is a per-browser convenience; storage can be blocked, so never depend on it.
function loadWatchlist(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) || DEFAULT_WATCHLIST;
  } catch {
    return DEFAULT_WATCHLIST;
  }
}

function saveWatchlist(value: string) {
  try {
    localStorage.setItem(STORAGE_KEY, value);
  } catch {
    // Ignore blocked storage; the scan still works.
  }
}

const dateFormat = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
const formatDate = (iso: string) => dateFormat.format(new Date(iso + "T00:00:00Z"));
const isoDay = (day: Date) => day.toISOString().slice(0, 10);
const signed = (value: number) => (value > 0 ? "+" : "") + value.toFixed(1) + "%";

function host(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function Move({ row }: { row: Row }) {
  const reaction = row.reaction;
  if (!reaction) return <span className="move move--flat">—</span>;
  const value = reaction.excess_pct ?? reaction.stock_pct;
  const tone = Math.abs(value) < 1 ? "flat" : value > 0 ? "up" : "down";
  return <span className={`move move--${tone}`}>{signed(value)}{reaction.excess_pct === null ? "" : " vs S&P"}</span>;
}

function Detail({ row, onResearch, starting }: { row: Row; onResearch: () => void; starting: boolean }) {
  const reaction = row.reaction;
  return (
    <div className="radar-detail">
      <p className="radar-detail__why">{row.why_it_matters}</p>
      {reaction && (
        <p className="radar-detail__reaction">
          {row.ticker} moved {signed(reaction.stock_pct)} from the {formatDate(reaction.from_date)} close to
          the {formatDate(reaction.to_date)} close
          {reaction.benchmark_pct !== null && `; the S&P 500 (SPY) moved ${signed(reaction.benchmark_pct)}`}.
          {" "}Events close together share this window.
        </p>
      )}
      <ul className="radar-detail__sources">
        {row.sources.map((source) => (
          <li key={source.url}>
            <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a>
            <span className="radar-detail__host">{host(source.url)}</span>
          </li>
        ))}
      </ul>
      <div className="radar-detail__footer">
        {row.confidence && <span className="radar-detail__confidence">Exa grounding confidence: {row.confidence}</span>}
        <button className="button button--primary" type="button" onClick={onResearch} disabled={starting}>
          {starting ? "Starting…" : "Research this event"}
        </button>
      </div>
    </div>
  );
}

export function Radar({ config }: { config: Config }) {
  const [watchlist, setWatchlist] = useState(loadWatchlist);
  const [days, setDays] = useState(30);
  const [scan, setScan] = useState<WatchlistScan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [starting, setStarting] = useState<string | null>(null);

  const tickers = [...new Set(watchlist.split(/[\s,]+/).map((ticker) => ticker.trim().toUpperCase()).filter(Boolean))];

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    saveWatchlist(watchlist);
    try {
      setScan(await api<WatchlistScan>("scan", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tickers, days }),
      }));
      setOpen(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The scan failed.");
    } finally {
      setBusy(false);
    }
  }

  // Hand the event to the existing research agent with a two-week price window around it.
  async function research(row: Row) {
    setStarting(row.key);
    setError("");
    const mode = config.modes.includes("bedrock") ? "bedrock" : config.modes.find((item) => item !== "demo");
    const day = new Date(row.reported_date + "T00:00:00Z");
    const start = new Date(day.getTime() - 7 * 86_400_000);
    const end = new Date(Math.min(day.getTime() + 7 * 86_400_000, Date.now()));
    try {
      const result = await api<{ id: string }>("runs", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: `Why did ${row.ticker} move around ${row.reported_date}? Reported event: ${row.headline} `
            + "Explain the price reaction with cited sources and separate reported facts from inference.",
          mode, scope: { symbols: [row.ticker], start_date: isoDay(start), end_date: isoDay(end) },
          max_tool_calls: 4, review: false,
        }),
      });
      window.location.assign("?run=" + encodeURIComponent(result.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Research could not start.");
      setStarting(null);
    }
  }

  const rows: Row[] = (scan?.companies ?? [])
    .flatMap((company) => company.events.map((item, index) => ({
      ...item, key: `${company.ticker}-${index}`, ticker: company.ticker, name: company.name,
    })))
    .sort((left, right) => right.reported_date.localeCompare(left.reported_date));
  const failures = scan?.companies.filter((company) => company.error) ?? [];

  return (
    <>
      <section className="page-intro radar-intro">
        <h1 className="page-intro__title">Event radar<span className="page-intro__accent">.</span></h1>
        <p className="page-intro__description">
          Material news across your holdings, a source for every event, and how the stock reacted.
        </p>
      </section>

      <form className="radar-form" onSubmit={submit}>
        <label className="form-field radar-form__tickers">Watchlist
          <input className="form-field__input" value={watchlist} onChange={(event) => setWatchlist(event.target.value)}
            placeholder="NVDA, AAPL, TSLA" aria-describedby="watchlist-hint" />
        </label>
        <label className="form-field">Look back
          <select className="form-field__input" value={days} onChange={(event) => setDays(Number(event.target.value))}>
            {LOOKBACKS.map((value) => <option key={value} value={value}>{value} days</option>)}
          </select>
        </label>
        <button className="button button--primary" disabled={busy || tickers.length === 0 || tickers.length > 15}>
          {busy ? "Scanning…" : "Scan"}
        </button>
      </form>
      <p className="radar-hint" id="watchlist-hint">Up to 15 stock tickers, separated by commas, e.g. NVDA, BRK-B, SAP.DE.</p>

      {busy && <p className="radar-status" role="status">Searching recent news for {tickers.length} companies with Exa…</p>}
      {error && <p className="feedback feedback--error" role="alert">{error}</p>}

      {scan && !busy && (
        <section aria-label="Scan results">
          <p className="radar-summary">
            {rows.length} events across {scan.companies.length - failures.length} companies ·{" "}
            {formatDate(scan.start_date)} – {formatDate(scan.end_date)} · Search by Exa · Prices by Yahoo Finance
          </p>
          {failures.map((company) => (
            <p className="feedback feedback--error" key={company.ticker}>{company.ticker}: {company.error}</p>
          ))}
          {rows.length === 0 ? <p className="feedback feedback--notice">No material events found in this window.</p> : (
            <div className="radar-table-wrap">
              <table className="radar-table">
                <thead>
                  <tr><th>Date</th><th>Company</th><th>Event</th><th>Move</th><th>Sources</th></tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <Fragment key={row.key}>
                      <tr className={open === row.key ? "radar-row radar-row--open" : "radar-row"}>
                        <td className="radar-date">{formatDate(row.reported_date)}</td>
                        <td className="radar-company"><strong>{row.ticker}</strong><span>{row.name}</span></td>
                        <td>
                          <button className="radar-toggle" type="button" aria-expanded={open === row.key}
                            onClick={() => setOpen(open === row.key ? null : row.key)}>
                            <span className={`category category--${row.category}`}>{row.category}</span>
                            <span className="radar-headline">{row.headline}</span>
                            {row.scheduled_date && (
                              <span className={row.upcoming ? "scheduled scheduled--upcoming" : "scheduled"}>
                                {row.upcoming ? "Upcoming" : "Scheduled"} · {formatDate(row.scheduled_date)}
                              </span>
                            )}
                          </button>
                        </td>
                        <td><Move row={row} /></td>
                        <td className="radar-sources">
                          {row.sources.slice(0, 2).map((source) => (
                            <a key={source.url} href={source.url} target="_blank" rel="noreferrer">{host(source.url)}</a>
                          ))}
                          {row.sources.length > 2 && <span>+{row.sources.length - 2}</span>}
                        </td>
                      </tr>
                      {open === row.key && (
                        <tr className="radar-detail-row">
                          <td colSpan={5}>
                            <Detail row={row} starting={starting === row.key} onResearch={() => research(row)} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="radar-hint">
            Move: close before the news came out to the first close after it, minus the S&P 500 (SPY) over the same days.
          </p>
        </section>
      )}
    </>
  );
}
