import type { ReactNode } from "react";
import type { WatchlistPrices } from "./api";
import { PriceChart } from "./PriceChart";
import { formatDate, money, SAMPLE_SHARES, signed, type Row } from "./watchlist";

// One hue, so allocation slices never look like event-category dots.
const RAMP = ["#152544", "#1840ed", "#5b7cf0", "#9fb3f6", "#cdd8fb", "#e6ecfd"];
const RADIUS = 40;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

function Summary({ prices }: { prices: WatchlistPrices }) {
  const holdings = prices.series
    .filter((series) => SAMPLE_SHARES[series.ticker] && series.prices.length > 1)
    .map((series) => {
      const shares = SAMPLE_SHARES[series.ticker];
      return { ticker: series.ticker, currency: series.currency, shares,
        value: shares * series.prices[series.prices.length - 1].close, start: shares * series.prices[0].close };
    })
    .sort((left, right) => right.value - left.value);
  if (!holdings.length) return null;
  const total = holdings.reduce((sum, holding) => sum + holding.value, 0);
  const start = holdings.reduce((sum, holding) => sum + holding.start, 0);
  const change = total - start;
  let offset = 0;
  return (
    <section className="portfolio-summary" aria-label="Sample portfolio">
      <div>
        <span className="portfolio-summary__tag">Sample portfolio · live prices</span>
        <p className="portfolio-summary__value">{money(total, "USD")}</p>
        <p className={`portfolio-summary__change move move--${Math.abs(change / start) < 0.01 ? "flat" : change > 0 ? "up" : "down"}`}>
          {money(change, "USD", { sign: true })} ({signed((change / start) * 100)}) since {formatDate(prices.start_date)}
        </p>
      </div>
      <div className="portfolio-summary__allocation">
        <svg className="donut" viewBox="0 0 100 100" aria-hidden="true">
          {holdings.map((holding, index) => {
            const length = (holding.value / total) * CIRCUMFERENCE;
            const slice = <circle key={holding.ticker} cx={50} cy={50} r={RADIUS} fill="none" stroke={RAMP[index % RAMP.length]}
              strokeWidth={16} strokeDasharray={`${length} ${CIRCUMFERENCE - length}`} strokeDashoffset={-offset}
              transform="rotate(-90 50 50)" />;
            offset += length;
            return slice;
          })}
        </svg>
        <ul className="portfolio-summary__legend">
          {holdings.map((holding, index) => (
            <li key={holding.ticker}>
              <i style={{ background: RAMP[index % RAMP.length] }} />
              <strong>{holding.ticker}</strong>
              <span>{((holding.value / total) * 100).toFixed(0)}%</span>
              <span>{money(holding.value, holding.currency, { digits: 0 })}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

interface Props {
  form: ReactNode;
  notice: ReactNode;
  prices: WatchlistPrices | null;
  rows: Row[];
  explained: boolean;
  onSelect: (row: Row) => void;
}

export function Portfolio({ form, notice, prices, rows, explained, onSelect }: Props) {
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
      {prices && <Summary prices={prices} />}
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
