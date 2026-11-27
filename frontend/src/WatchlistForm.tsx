import type { FormEvent, ReactNode } from "react";
import { LOOKBACKS } from "./watchlist";

interface Props {
  watchlist: string;
  days: number;
  onWatchlist: (value: string) => void;
  onDays: (value: number) => void;
  onSubmit: () => void;
  children: ReactNode;
}

// Shared by Portfolio and Event radar so both tabs always work on the same list.
export function WatchlistForm({ watchlist, days, onWatchlist, onDays, onSubmit, children }: Props) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmit();
  }
  return (
    <>
      <form className="radar-form" onSubmit={submit}>
        <label className="form-field radar-form__tickers">Watchlist
          <input className="form-field__input" value={watchlist} onChange={(event) => onWatchlist(event.target.value)}
            placeholder="NVDA, AAPL, TSLA" aria-describedby="watchlist-hint" />
        </label>
        <label className="form-field">Look back
          <select className="form-field__input" value={days} onChange={(event) => onDays(Number(event.target.value))}>
            {LOOKBACKS.map((value) => <option key={value} value={value}>{value} days</option>)}
          </select>
        </label>
        {children}
      </form>
      <p className="radar-hint" id="watchlist-hint">Up to 15 stock tickers, separated by commas, e.g. NVDA, BRK-B, SAP.DE.</p>
    </>
  );
}
