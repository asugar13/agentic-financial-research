import { Fragment, useEffect, useState, type ReactNode } from "react";
import { api, type Config, type WatchlistScan } from "./api";
import { formatDate, host, isoDay, moveLabel, signed, type Row } from "./watchlist";

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

interface Props {
  config: Config;
  form: ReactNode;
  notice: ReactNode;
  scan: WatchlistScan | null;
  rows: Row[];
  open: string | null;
  onOpen: (key: string | null) => void;
}

export function Radar({ config, form, notice, scan, rows, open, onOpen }: Props) {
  const [starting, setStarting] = useState<string | null>(null);
  const [error, setError] = useState("");

  // Arriving from a chart dot opens that row; bring it into view.
  useEffect(() => {
    if (open) document.getElementById(`event-${open}`)?.scrollIntoView({ block: "center" });
  }, [open]);

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

  const failures = scan?.companies.filter((company) => company.error) ?? [];

  return (
    <>
      <section className="page-intro radar-intro">
        <h1 className="page-intro__title">Event radar<span className="page-intro__accent">.</span></h1>
        <p className="page-intro__description">
          Material news across your holdings, a source for every event, and how the stock reacted.
        </p>
      </section>
      {form}
      {notice}
      {error && <p className="feedback feedback--error" role="alert">{error}</p>}

      {scan && (
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
                  {rows.map((row) => {
                    const move = moveLabel(row);
                    return (
                      <Fragment key={row.key}>
                        <tr id={`event-${row.key}`} className={open === row.key ? "radar-row radar-row--open" : "radar-row"}>
                          <td className="radar-date">{formatDate(row.reported_date)}</td>
                          <td className="radar-company"><strong>{row.ticker}</strong><span>{row.name}</span></td>
                          <td>
                            <button className="radar-toggle" type="button" aria-expanded={open === row.key}
                              onClick={() => onOpen(open === row.key ? null : row.key)}>
                              <span className={`category category--${row.category}`}>{row.category}</span>
                              <span className="radar-headline">{row.headline}</span>
                              {row.scheduled_date && (
                                <span className={row.upcoming ? "scheduled scheduled--upcoming" : "scheduled"}>
                                  {row.upcoming ? "Upcoming" : "Scheduled"} · {formatDate(row.scheduled_date)}
                                </span>
                              )}
                            </button>
                          </td>
                          <td><span className={`move move--${move.tone}`}>{move.text}</span></td>
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
                    );
                  })}
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
