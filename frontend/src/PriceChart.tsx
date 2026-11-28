import { useState, type PointerEvent } from "react";
import type { PriceSeries } from "./api";
import { formatDate, host, moveLabel, signed, type Row } from "./watchlist";

const WIDTH = 320;
const HEIGHT = 130;
const PAD = { top: 14, right: 10, bottom: 20, left: 10 };

interface Props {
  series: PriceSeries;
  events: Row[];
  onSelect: (row: Row) => void;
  // Replaces the default caption, e.g. on a page where dots don't link anywhere.
  hint?: string;
}

export function PriceChart({ series, events, onSelect, hint }: Props) {
  const [active, setActive] = useState<Row | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
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

  // Map the pointer to the nearest trading day; dots sit above this layer, so they win.
  function trace(event: PointerEvent<SVGRectElement>) {
    const box = event.currentTarget.ownerSVGElement!.getBoundingClientRect();
    const position = ((event.clientX - box.left) / box.width) * WIDTH;
    const index = Math.round(((position - PAD.left) / (WIDTH - PAD.left - PAD.right)) * (points.length - 1));
    setHovered(Math.max(0, Math.min(points.length - 1, index)));
    setActive(null);
  }

  const change = series.change_pct ?? 0;
  const last = points[points.length - 1];
  const point = hovered === null ? null : points[hovered];
  return (
    <article className="chart-card">
      <header className="chart-card__header">
        <div><strong>{series.ticker}</strong><span>{series.name}</span></div>
        <div className="chart-card__stats">
          <span className={`move move--${Math.abs(change) < 1 ? "flat" : change > 0 ? "up" : "down"}`}>{signed(change)}</span>
          <span>{last.close.toFixed(2)} {series.currency ?? ""}</span>
        </div>
      </header>
      <svg className="chart-svg" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" onPointerLeave={() => setHovered(null)}
        aria-label={`${series.ticker} closing prices from ${formatDate(points[0].date)} to ${formatDate(last.date)}`}>
        <path className="chart-area" d={area} />
        <path className="chart-line" d={line} />
        <text className="chart-axis" x={PAD.left} y={HEIGHT - 5}>{formatDate(points[0].date)}</text>
        <text className="chart-axis" x={WIDTH - PAD.right} y={HEIGHT - 5} textAnchor="end">{formatDate(last.date)}</text>
        {point && hovered !== null && (
          <g className="chart-hover" aria-hidden="true">
            <line x1={x(hovered)} x2={x(hovered)} y1={PAD.top - 6} y2={HEIGHT - PAD.bottom} />
            <circle cx={x(hovered)} cy={y(point.close)} r={3.5} />
          </g>
        )}
        <rect className="chart-trace" x={0} y={0} width={WIDTH} height={HEIGHT - PAD.bottom}
          onPointerMove={trace} onPointerDown={trace} />
        {dots.map(({ row, cx, cy }) => (
          <circle key={row.key} className={`dot dot--${row.category}${active?.key === row.key ? " dot--active" : ""}`}
            cx={cx} cy={cy} r={5} tabIndex={0} role="button" aria-label={`${formatDate(row.reported_date)}: ${row.headline}`}
            onPointerEnter={() => { setActive(row); setHovered(null); }} onFocus={() => setActive(row)}
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
        ) : point ? (
          <>
            <strong>{formatDate(point.date)}</strong> · {point.close.toFixed(2)} {series.currency ?? ""} ·{" "}
            {signed((point.close / points[0].close - 1) * 100)} since {formatDate(points[0].date)}
          </>
        ) : hint ?? (events.length ? `${events.length} events. Hover a dot to read it, click to open it in Event radar.`
          : "Hover the chart to see each day's close.")}
      </p>
    </article>
  );
}
