import { useState } from "react";
import type { PriceSeries } from "./api";
import { formatDate, host, moveLabel, signed, type Row } from "./watchlist";

const WIDTH = 320;
const HEIGHT = 130;
const PAD = { top: 14, right: 10, bottom: 20, left: 10 };

interface Props {
  series: PriceSeries;
  events: Row[];
  onSelect: (row: Row) => void;
}

export function PriceChart({ series, events, onSelect }: Props) {
  const [active, setActive] = useState<Row | null>(null);
  const points = series.prices;

  if (series.error || points.length < 2) {
    return (
      <article className="chart-card">
        <header className="chart-card__header"><strong>{series.ticker}</strong></header>
        <p className="chart-card__error">{series.error ?? "Not enough prices to draw a chart."}</p>
      </article>
    );
  }

  const closes = points.map((point) => point.close);
  const low = Math.min(...closes);
  const high = Math.max(...closes);
  // Trading days are evenly spaced, so weekends and holidays leave no flat gaps.
  const x = (index: number) => PAD.left + (index * (WIDTH - PAD.left - PAD.right)) / (points.length - 1);
  const y = (close: number) => PAD.top + ((high - close) / (high - low || 1)) * (HEIGHT - PAD.top - PAD.bottom);
  const line = points.map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(point.close).toFixed(1)}`).join("");
  const area = `${line}L${x(points.length - 1).toFixed(1)},${HEIGHT - PAD.bottom}L${x(0).toFixed(1)},${HEIGHT - PAD.bottom}Z`;

  // News on a weekend or after the last close sits on the next available session.
  const stacked = new Map<number, number>();
  const dots = events.flatMap((row) => {
    let index = points.findIndex((point) => point.date >= row.reported_date);
    if (index === -1) index = points.length - 1;
    const level = stacked.get(index) ?? 0;
    stacked.set(index, level + 1);
    return [{ row, cx: x(index), cy: Math.max(PAD.top - 6, y(points[index].close) - level * 11) }];
  });

  const change = series.change_pct ?? 0;
  const last = points[points.length - 1];
  return (
    <article className="chart-card">
      <header className="chart-card__header">
        <div><strong>{series.ticker}</strong><span>{series.name}</span></div>
        <div className="chart-card__stats">
          <span className={`move move--${Math.abs(change) < 1 ? "flat" : change > 0 ? "up" : "down"}`}>{signed(change)}</span>
          <span>{last.close.toFixed(2)} {series.currency ?? ""}</span>
        </div>
      </header>
      <svg className="chart-svg" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img"
        aria-label={`${series.ticker} closing prices from ${formatDate(points[0].date)} to ${formatDate(last.date)}`}>
        <path className="chart-area" d={area} />
        <path className="chart-line" d={line} />
        <text className="chart-axis" x={PAD.left} y={HEIGHT - 5}>{formatDate(points[0].date)}</text>
        <text className="chart-axis" x={WIDTH - PAD.right} y={HEIGHT - 5} textAnchor="end">{formatDate(last.date)}</text>
        {dots.map(({ row, cx, cy }) => (
          <circle key={row.key} className={`dot dot--${row.category}${active?.key === row.key ? " dot--active" : ""}`}
            cx={cx} cy={cy} r={5} tabIndex={0} role="button" aria-label={`${formatDate(row.reported_date)}: ${row.headline}`}
            onMouseEnter={() => setActive(row)} onFocus={() => setActive(row)}
            onClick={() => onSelect(row)}
            onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(row); } }} />
        ))}
      </svg>
      <p className="chart-caption" aria-live="polite">
        {active ? (
          <>
            <span className={`category category--${active.category}`}>{active.category}</span>
            {formatDate(active.reported_date)} · {active.headline}{" "}
            <span className={`move move--${moveLabel(active).tone}`}>{moveLabel(active).text}</span>
            {active.sources[0] && <span className="chart-caption__source"> · {host(active.sources[0].url)}</span>}
          </>
        ) : events.length ? `${events.length} events. Hover a dot to read it, click to open it in Event radar.` : ""}
      </p>
    </article>
  );
}
