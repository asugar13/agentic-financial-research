import { useEffect, useState, type FormEvent } from "react";
import { api, type SearchSettings, type StockAnswer } from "./api";
import { Sources } from "./EventPage";
import { PriceChart } from "./PriceChart";
import { describeSettings } from "./SearchSettings";
import { formatDate, formatTime, host, isoDay } from "./watchlist";

const EXAMPLES = [
  "Why did the stock move?",
  "What did analysts say about the latest guidance?",
  "What legal or regulatory issues came up?",
];

interface Question { ticker: string; from: string; to: string; question: string }

function initial(): Question {
  const params = new URLSearchParams(window.location.search);
  const today = new Date();
  return {
    ticker: params.get("ticker") ?? "",
    from: params.get("from") ?? isoDay(new Date(today.getTime() - 30 * 86_400_000)),
    to: params.get("to") ?? isoDay(today),
    question: params.get("q") ?? "",
  };
}

export function AskPage({ settings }: { settings: SearchSettings }) {
  const [form, setForm] = useState<Question>(initial);
  const [result, setResult] = useState<StockAnswer | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function ask(question: Question) {
    setLoading(true);
    setError("");
    setResult(null);
    // The question lives in the address, so a reload or a shared link asks it again (from the cache).
    const params = new URLSearchParams({ view: "ask", ticker: question.ticker, from: question.from, to: question.to, q: question.question });
    window.history.replaceState(null, "", `/?${params}`);
    try {
      setResult(await api<StockAnswer>("ask", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker: question.ticker, question: question.question, start_date: question.from,
          end_date: question.to, settings }),
      }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Exa could not answer this.");
    } finally {
      setLoading(false);
    }
  }

  // Opening a link that already carries a question answers it straight away.
  useEffect(() => {
    if (form.ticker && form.question) void ask(form);
  }, []);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void ask({ ...form, ticker: form.ticker.trim().toUpperCase(), question: form.question.trim() });
  }

  const ready = form.ticker.trim().length > 0 && form.question.trim().length >= 3 && form.from <= form.to;
  const searched = result ? describeSettings(result.settings) : "";
  return (
    <>
      <section className="page-intro radar-intro">
        <h1 className="page-intro__title">Ask<span className="page-intro__accent">.</span></h1>
        <p className="page-intro__description">
          Ask anything about a stock over a period you choose. Exa researches the news from those dates, with a source for every point.
        </p>
      </section>
      <form className="ask-form" onSubmit={submit}>
        <label className="form-field">Ticker
          <input className="form-field__input" value={form.ticker} placeholder="NVDA" maxLength={12}
            onChange={(event) => setForm({ ...form, ticker: event.target.value })} />
        </label>
        <label className="form-field">From
          <input className="form-field__input" type="date" value={form.from} max={form.to}
            onChange={(event) => setForm({ ...form, from: event.target.value })} />
        </label>
        <label className="form-field">To
          <input className="form-field__input" type="date" value={form.to} max={isoDay(new Date())}
            onChange={(event) => setForm({ ...form, to: event.target.value })} />
        </label>
        <label className="form-field ask-form__question">Question
          <input className="form-field__input" value={form.question} maxLength={300}
            placeholder="e.g. What did analysts say about the latest guidance?"
            onChange={(event) => setForm({ ...form, question: event.target.value })} />
        </label>
        <button className="button button--primary" disabled={loading || !ready}>{loading ? "Researching…" : "Ask Exa"}</button>
      </form>
      <p className="discover-examples">
        Try:{" "}
        {EXAMPLES.map((example) => (
          <button key={example} type="button" className="discover-example" disabled={loading}
            onClick={() => setForm({ ...form, question: example })}>{example}</button>
        ))}
      </p>

      {loading && (
        <p className="radar-status event-loading" role="status">
          <span className="spinner" aria-hidden="true" /> Exa is researching the news from those dates with deep search, usually 5–15 seconds…
        </p>
      )}
      {error && <p className="feedback feedback--error" role="alert">{error}</p>}

      {result && !loading && (
        <>
          <p className="radar-summary">
            {result.ticker}{result.name ? ` · ${result.name}` : ""} · {formatDate(result.start_date)} – {formatDate(result.end_date)}
          </p>
          <div className="event-layout">
            <div className="event-main">
              {result.stale && (
                <p className="feedback feedback--notice">Live research failed, so this is the answer from {formatTime(result.answered_at)}.</p>
              )}
              <section className="event-card" aria-labelledby="answer-title">
                <h2 className="event-card__title" id="answer-title">{result.question}</h2>
                <p className="event-summary">{result.answer.text}</p>
                <Sources sources={result.answer.sources} confidence={result.answer.confidence} />
                <ol className="event-drivers">
                  {result.key_points.map((point) => (
                    <li key={point.point}>
                      <strong>{point.point}</strong>
                      {point.evidence && <p>{point.evidence}</p>}
                      <Sources sources={point.sources} confidence={point.confidence} />
                    </li>
                  ))}
                </ol>
              </section>
              {result.gaps && (
                <section className="event-card" aria-labelledby="gaps-title">
                  <h2 className="event-card__title" id="gaps-title">What the sources don't cover</h2>
                  <p>{result.gaps.text}</p>
                </section>
              )}
            </div>
            <aside className="event-side">
              {result.prices.length > 1 && (
                <PriceChart events={[]} onSelect={() => {}} hint="Prices around the period you asked about."
                  series={{
                    ticker: result.ticker, name: result.name, currency: result.currency, prices: result.prices,
                    error: null, fetched_at: null, stale: false,
                    change_pct: (result.prices[result.prices.length - 1].close / result.prices[0].close - 1) * 100,
                  }} />
              )}
              <section className="event-card" aria-labelledby="ask-sources-title">
                <h2 className="event-card__title" id="ask-sources-title">Sources Exa read</h2>
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
          <p className="radar-hint">
            Answered by Exa deep search · {formatTime(result.answered_at)}{searched && ` · ${searched}`}
          </p>
        </>
      )}
    </>
  );
}
