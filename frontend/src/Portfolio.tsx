import type { ReactNode } from "react";
import type { WatchlistPrices } from "./api";
import { PriceChart } from "./PriceChart";
import type { Row } from "./watchlist";

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
        <h1 className="page-intro__title">Portfolio<span className="page-intro__accent">.</span></h1>
        <p className="page-intro__description">
          How your watchlist moved, and what moved it.
        </p>
      </section>
      {form}
      {notice}
      {explained && (
        <p className="radar-summary">
          {rows.length} events found by Exa · each dot marks when the news came out · Prices by Yahoo Finance
        </p>
      )}
      {prices && (
        <div className="portfolio-grid">
          {prices.series.map((series) => (
            <PriceChart key={series.ticker} series={series} onSelect={onSelect}
              events={rows.filter((row) => row.ticker === series.ticker)} />
          ))}
        </div>
      )}
    </>
  );
}
