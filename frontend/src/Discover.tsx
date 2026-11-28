import { useState, type FormEvent, type ReactNode } from "react";
import type { Discovery } from "./api";
import { formatTime, host } from "./watchlist";

const EXAMPLES = ["Publicly listed European defence and aerospace companies", "US-listed nuclear energy companies",
  "Publicly listed chip makers", "Arm Holdings"];

interface Props {
  discovery: Discovery | null;
  loading: boolean;
  notice: ReactNode;
  holdings: string[];
  onSearch: (query: string) => void;
  onAdd: (ticker: string) => void;
  onPortfolio: () => void;
}

export function Discover({ discovery, loading, notice, holdings, onSearch, onAdd, onPortfolio }: Props) {
  const [query, setQuery] = useState(discovery?.query ?? "");
  const [added, setAdded] = useState(false);
  const full = holdings.length >= 15;

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (query.trim().length >= 2) onSearch(query.trim());
  }

  return (
    <>
      <section className="page-intro radar-intro">
        <h1 className="page-intro__title">Discover<span className="page-intro__accent">.</span></h1>
        <p className="page-intro__description">
          Find listed companies in plain English. Exa searches its company index; Yahoo confirms each one can be traded.
        </p>
      </section>
      <form className="radar-form" onSubmit={submit}>
        <label className="form-field radar-form__tickers">What are you looking for?
          <input className="form-field__input" value={query} maxLength={200} onChange={(event) => setQuery(event.target.value)}
            placeholder="e.g. publicly listed chip makers, or a company name" />
        </label>
        <button className="button button--primary" disabled={loading || query.trim().length < 2}>
          {loading ? "Searching…" : "Search with Exa"}
        </button>
      </form>
      <p className="discover-examples">
        Try:{" "}
        {EXAMPLES.map((example) => (
          <button key={example} type="button" className="discover-example" disabled={loading}
            onClick={() => { setQuery(example); onSearch(example); }}>{example}</button>
        ))}
      </p>
      {loading && (
        <p className="radar-status event-loading" role="status">
          <span className="spinner" aria-hidden="true" /> Searching Exa's company index, then confirming each company on Yahoo…
        </p>
      )}
      {notice}

      {discovery && !loading && (
        <section aria-label="Companies found">
          <p className="radar-summary">
            {discovery.companies.length} companies for “{discovery.query}” · searched {formatTime(discovery.discovered_at)}
            {" "}· Search by Exa · Prices by Yahoo Finance
          </p>
          {discovery.stale && <p className="feedback feedback--notice">Live search failed, so these are the results from {formatTime(discovery.discovered_at)}.</p>}
          {discovery.dropped.length > 0 && (
            <p className="radar-hint">Left out because Yahoo couldn't confirm them: {discovery.dropped.join("; ")}.</p>
          )}
          {discovery.companies.length === 0 ? <p className="feedback feedback--notice">No listed companies found. Try wording it differently.</p> : (
            <div className="radar-table-wrap">
              <table className="radar-table">
                <thead><tr><th>Company</th><th>What they do</th><th>Last close</th><th>Sources</th><th><span className="visually-hidden">Action</span></th></tr></thead>
                <tbody>
                  {discovery.companies.map((company) => {
                    const held = holdings.includes(company.ticker);
                    return (
                      <tr key={company.ticker}>
                        <td className="radar-company"><strong>{company.ticker}</strong><span>{company.name}{company.exchange ? ` · ${company.exchange}` : ""}</span></td>
                        <td>{company.description}</td>
                        <td className="radar-date">{company.last_close === null ? "—" : `${company.last_close.toLocaleString("en-US", { maximumFractionDigits: 2 })} ${company.currency ?? ""}`}</td>
                        <td className="radar-sources">
                          {[...new Set(company.sources.map((source) => source.url))].slice(0, 2).map((url) => (
                            <a key={url} href={url} target="_blank" rel="noreferrer">{host(url)}</a>
                          ))}
                          {company.confidence && <span className={`event-confidence event-confidence--${company.confidence}`}>{company.confidence}</span>}
                        </td>
                        <td>
                          {held ? <span className="discover-held">In holdings ✓</span> : (
                            <button className="button button--secondary discover-add" type="button" disabled={full}
                              onClick={() => { onAdd(company.ticker); setAdded(true); }}>Add to holdings</button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {full && <p className="radar-hint">Your holdings are full (15 tickers). Remove one on the Portfolio tab to add more.</p>}
          {added && (
            <p className="discover-next">
              <a href="/" onClick={(event) => { event.preventDefault(); onPortfolio(); }}>See your updated portfolio →</a>
            </p>
          )}
        </section>
      )}
    </>
  );
}
