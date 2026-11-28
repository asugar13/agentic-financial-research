import type { ReactNode } from "react";
import type { MarketScan, WatchlistPrices } from "./api";
import { PortfolioChart } from "./PortfolioChart";
import { PriceChart } from "./PriceChart";
import { formatDate, money, SAMPLE_SHARES, signed, type Row } from "./watchlist";

function Summary({ prices, market }: { prices: WatchlistPrices; market: MarketScan | null }) {
  const held = prices.series.filter((series) => SAMPLE_SHARES[series.ticker] && series.prices.length > 1);
  if (!held.length) return null;
  const holdings = held
    .map((series) => {
      const shares = SAMPLE_SHARES[series.ticker];
      return { ticker: series.ticker, value: shares * series.prices[series.prices.length - 1].close,
        start: shares * series.prices[0].close };
    })
    .sort((left, right) => right.value - left.value);
  const total = holdings.reduce((sum, holding) => sum + holding.value, 0);
  const start = holdings.reduce((sum, holding) => sum + holding.start, 0);
  const change = total - start;
  // The portfolio's value on each day every holding traded.
  const books = held.map((series) => ({
    shares: SAMPLE_SHARES[series.ticker], closes: new Map(series.prices.map((point) => [point.date, point.close])),
  }));
  const values = held[0].prices.map((point) => point.date)
    .filter((day) => books.every((book) => book.closes.has(day)))
    .map((day) => ({ date: day, value: books.reduce((sum, book) => sum + book.shares * book.closes.get(day)!, 0) }));
  return (
    <section className="portfolio-summary" aria-label="Sample portfolio">
      <div className="portfolio-summary__head">
        <div>
          <span className="portfolio-summary__tag">Sample portfolio · live prices</span>
          <p className="portfolio-summary__value">{money(total, "USD")}</p>
          <p className={`portfolio-summary__change move move--${Math.abs(change / start) < 0.01 ? "flat" : change > 0 ? "up" : "down"}`}>
            {money(change, "USD", { sign: true })} ({signed((change / start) * 100)}) since {formatDate(prices.start_date)}
          </p>
        </div>
        <p className="portfolio-summary__weights">
          {holdings.map((holding) => `${holding.ticker} ${((holding.value / total) * 100).toFixed(0)}%`).join(" · ")}
        </p>
      </div>
      <PortfolioChart values={values} benchmark={market?.benchmark ?? []} events={market?.events ?? []} />
    </section>
  );
}

interface Props {
  form: ReactNode;
  notice: ReactNode;
  prices: WatchlistPrices | null;
  market: MarketScan | null;
  rows: Row[];
  explained: boolean;
  onSelect: (row: Row) => void;
}

export function Portfolio({ form, notice, prices, market, rows, explained, onSelect }: Props) {
  return (
    <>
      <section className="page-intro radar-intro">
        <h1 className="page-intro__title">Your portfolio<span className="page-intro__accent">.</span></h1>
        <p className="page-intro__description">
          How your holdings moved, and what moved them.
        </p>
      </section>
      {form}
      {notice}
      {prices && <Summary prices={prices} market={market} />}
      {explained && (
        <p className="radar-summary">
          {rows.length} events found by Exa · each dot marks when the news came out · Prices by Yahoo Finance
        </p>
      )}
      {prices && (
        <div className="portfolio-grid">
          {prices.series.map((series) => (
            <PriceChart key={series.ticker} series={series} onSelect={onSelect} shares={SAMPLE_SHARES[series.ticker]}
              events={rows.filter((row) => row.ticker === series.ticker)} />
          ))}
        </div>
      )}
    </>
  );
}
