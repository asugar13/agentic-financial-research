import { useEffect, useState } from "react";
import { api, type Citation, type EventCategory, type EventExplanation, type Finding, type SearchSettings } from "./api";
import { PriceChart } from "./PriceChart";
import { describeSettings } from "./SearchSettings";
import { formatDate, formatTime, host, signed, type Row } from "./watchlist";

interface Props {
  ticker: string;
  date: string;
  headline: string;
  category: EventCategory;
  settings: SearchSettings;
  onBack: () => void;
}

function Sources({ sources, confidence }: { sources: Citation[]; confidence?: Finding["confidence"] }) {
  const unique = [...new Map(sources.map((source) => [host(source.url), source])).values()];
  if (!unique.length && !confidence) return null;
  return (
    <p className="event-sources">
      {unique.map((source) => (
        <a key={source.url} href={source.url} target="_blank" rel="noreferrer" title={source.title}>{host(source.url)}</a>
      ))}
      {confidence && <span className={`event-confidence event-confidence--${confidence}`}>{confidence} confidence</span>}
    </p>
  );
}

// The day's own move and the two-session window can differ a lot; show both with their dates.
function Moves({ result }: { result: EventExplanation }) {
  const before = [...result.prices].reverse().find((point) => point.date < result.reported_date);
  const onDay = result.prices.find((point) => point.date >= result.reported_date);
  const reaction = result.reaction;
  if (!before || !onDay) return null;
  const dayMove = (onDay.close / before.close - 1) * 100;
  const tone = (value: number) => (Math.abs(value) < 1 ? "flat" : value > 0 ? "up" : "down");
  return (
    <p className="event-moves">
      <span>{formatDate(onDay.date)}: <strong className={`move move--${tone(dayMove)}`}>{signed(dayMove)}</strong></span>
      {reaction && reaction.to_date !== onDay.date && (
        <span>
          {formatDate(reaction.from_date)} → {formatDate(reaction.to_date)}:{" "}
          <strong className={`move move--${tone(reaction.stock_pct)}`}>{signed(reaction.stock_pct)}</strong>
          {reaction.benchmark_pct !== null && ` · S&P 500 ${signed(reaction.benchmark_pct)}`}
        </span>
      )}
    </p>
  );
}

export function EventPage({ ticker, date, headline, category, settings, onBack }: Props) {
  const [result, setResult] = useState<EventExplanation | null>(null);
  const [error, setError] = useState("");
  const request = JSON.stringify({ ticker, reported_date: date, headline, settings });

  useEffect(() => {
    let current = true;
    setResult(null);
    setError("");
    api<EventExplanation>("explain", { method: "POST", headers: { "Content-Type": "application/json" }, body: request })
      .then((value) => current && setResult(value))
      .catch((reason) => current && setError(reason instanceof Error ? reason.message : "Research failed."));
    return () => { current = false; };
  }, [request]);

  // The chart reuses the portfolio chart, with one dot on the day the news came out.
  const marker: Row | null = result && {
    key: "event", ticker, name: result.name, reported_date: result.reported_date, scheduled_date: null, category, headline,
    why_it_matters: "", confidence: null, sources: [], upcoming: false, reaction: result.reaction,
  };
  const searched = result ? describeSettings(result.settings) : "";

  return (
    <>
      <a className="back-link" href="/?view=radar" onClick={(event) => { event.preventDefault(); onBack(); }}>← Event radar</a>
      <section className="page-intro page-intro--run">
        <p className="event-meta">
          <span className={`category category--${category}`}>{category}</span>
          {ticker}{result?.name ? ` · ${result.name}` : ""} · {formatDate(date)}
        </p>
        <h1 className="page-intro__title page-intro__title--question">{headline}</h1>
        {result && <Moves result={result} />}
      </section>

      {!result && !error && (
        <p className="radar-status event-loading" role="status">
          <span className="spinner" aria-hidden="true" />
          Exa is researching this event with deep search, usually 5–15 seconds…
        </p>
      )}
      {error && <p className="feedback feedback--error" role="alert">{error}</p>}

      {result && (
        <div className="event-layout">
          <div className="event-main">
            {result.stale && (
              <p className="feedback feedback--notice">Live research failed, so this is the explanation from {formatTime(result.explained_at)}.</p>
            )}
            <section className="event-card" aria-labelledby="why-title">
              <h2 className="event-card__title" id="why-title">Why it moved</h2>
              <p className="event-summary">{result.summary.text}</p>
              <Sources sources={result.summary.sources} confidence={result.summary.confidence} />
              <ol className="event-drivers">
                {result.drivers.map((driver) => (
                  <li key={driver.point}>
                    <strong>{driver.point}</strong>
                    {driver.evidence && <p>{driver.evidence}</p>}
                    <Sources sources={driver.sources} confidence={driver.confidence} />
                  </li>
                ))}
              </ol>
            </section>
            <section className="event-card" aria-labelledby="context-title">
              <h2 className="event-card__title" id="context-title">Market context</h2>
              <p>{result.market_context.text}</p>
              <Sources sources={result.market_context.sources} confidence={result.market_context.confidence} />
            </section>
            {result.upcoming && (
              <section className="event-card" aria-labelledby="upcoming-title">
                <h2 className="event-card__title" id="upcoming-title">Upcoming</h2>
                <p>{result.upcoming.text}</p>
                <Sources sources={result.upcoming.sources} confidence={result.upcoming.confidence} />
              </section>
            )}
          </div>
          <aside className="event-side">
            {marker && result.prices.length > 1 && (
              <PriceChart events={[marker]} onSelect={() => {}} hint="The dot marks the day the news came out."
                series={{
                  ticker, name: result.name, currency: result.currency, prices: result.prices, error: null,
                  fetched_at: null, stale: false,
                  change_pct: (result.prices[result.prices.length - 1].close / result.prices[0].close - 1) * 100,
                }} />
            )}
            <section className="event-card" aria-labelledby="sources-title">
              <h2 className="event-card__title" id="sources-title">Sources Exa read</h2>
              <ul className="event-source-list">
                {result.sources.map((source) => (
                  <li key={source.url}>
                    <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a>
                    <span>{host(source.url)}{source.published_date ? ` · ${formatDate(source.published_date)}` : ""}</span>
                  </li>
                ))}
              </ul>
            </section>
          </aside>
        </div>
      )}
      {result && (
        <p className="radar-hint">
          Researched by Exa deep search · {formatTime(result.explained_at)}{searched && ` · ${searched}`}
        </p>
      )}
    </>
  );
}
