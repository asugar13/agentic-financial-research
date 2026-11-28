import { useState, type PointerEvent } from "react";
import type { MarketEvent } from "./api";
import { formatDate, host, money, signed } from "./watchlist";

const WIDTH = 720;
const HEIGHT = 170;
const PAD = { top: 16, right: 12, bottom: 22, left: 12 };
export const MARKET_LABELS: Record<MarketEvent["category"], string> = {
  monetary_policy: "monetary policy", economic_data: "economic data", trade_policy: "trade policy",
  geopolitics: "geopolitics", markets: "markets", other: "other",
};

interface Props {
  values: { date: string; value: number }[];
  benchmark: { date: string; close: number }[];
  events: MarketEvent[];
}

// The value of the whole portfolio, the S&P 500 scaled to the same start, and market-wide news as dots.
export function PortfolioChart({ values, benchmark, events }: Props) {
  const [active, setActive] = useState<MarketEvent | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
  if (values.length < 2) return null;

  const closes = new Map(benchmark.map((point) => [point.date, point.close]));
  const baseClose = closes.get(values[0].date);
  // Only draw the benchmark where it has a close on the portfolio's own trading days.
  const scaled = baseClose ? values.map((point) => {
    const close = closes.get(point.date);
    return close === undefined ? null : (close / baseClose) * values[0].value;
  }) : [];
  const all = [...values.map((point) => point.value), ...scaled.filter((value): value is number => value !== null)];
  const low = Math.min(...all);
  const high = Math.max(...all);
  const x = (index: number) => PAD.left + (index * (WIDTH - PAD.left - PAD.right)) / (values.length - 1);
  const y = (value: number) => PAD.top + ((high - value) / (high - low || 1)) * (HEIGHT - PAD.top - PAD.bottom);
  const path = (series: (number | null)[]) => series.reduce<string>((line, value, index) => (
    value === null ? line : `${line}${line && series[index - 1] !== null ? "L" : "M"}${x(index).toFixed(1)},${y(value).toFixed(1)}`
  ), "");

  // News on a non-trading day sits on the next session, where the market reacted.
  const stacked = new Map<number, number>();
  const dots = events.flatMap((event) => {
    let index = values.findIndex((point) => point.date >= event.reported_date);
    if (index === -1) index = values.length - 1;
    const level = stacked.get(index) ?? 0;
    stacked.set(index, level + 1);
    return [{ event, cx: x(index), cy: Math.max(PAD.top - 8, y(values[index].value) - level * 11) }];
  });

  function trace(pointer: PointerEvent<SVGRectElement>) {
    const box = pointer.currentTarget.ownerSVGElement!.getBoundingClientRect();
    const position = ((pointer.clientX - box.left) / box.width) * WIDTH;
    const index = Math.round(((position - PAD.left) / (WIDTH - PAD.left - PAD.right)) * (values.length - 1));
    setHovered(Math.max(0, Math.min(values.length - 1, index)));
    setActive(null);
  }

  // What the news window meant for this portfolio, using the same closes as the S&P move.
  const valueOn = (day: string) => values.find((point) => point.date === day)?.value;
  const impact = (event: MarketEvent) => {
    const move = event.market_move;
    const before = move && valueOn(move.from_date);
    const after = move && valueOn(move.to_date);
    return before && after ? after - before : null;
  };
  const point = hovered === null ? null : values[hovered];
  const benchmarkAt = hovered === null ? null : scaled[hovered];

  return (
    <div className="portfolio-chart">
      <svg className="chart-svg" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" onPointerLeave={() => setHovered(null)}
        aria-label={`Portfolio value and the S&P 500 from ${formatDate(values[0].date)} to ${formatDate(values[values.length - 1].date)}`}>
        <path className="portfolio-chart__benchmark" d={path(scaled)} />
        <path className="portfolio-chart__line" d={path(values.map((value) => value.value))} />
        <text className="chart-axis" x={PAD.left} y={HEIGHT - 5}>{formatDate(values[0].date)}</text>
        <text className="chart-axis" x={WIDTH - PAD.right} y={HEIGHT - 5} textAnchor="end">{formatDate(values[values.length - 1].date)}</text>
        {point && hovered !== null && (
          <g className="chart-hover" aria-hidden="true">
            <line x1={x(hovered)} x2={x(hovered)} y1={PAD.top - 8} y2={HEIGHT - PAD.bottom} />
            <circle cx={x(hovered)} cy={y(point.value)} r={3.5} />
          </g>
        )}
        <rect className="chart-trace" x={0} y={0} width={WIDTH} height={HEIGHT - PAD.bottom} onPointerMove={trace} onPointerDown={trace} />
        {dots.map(({ event, cx, cy }) => (
          <circle key={`${event.reported_date}-${event.headline}`} cx={cx} cy={cy} r={5} tabIndex={0}
            className={`dot mdot--${event.category}${active === event ? " dot--active" : ""}`}
            aria-label={`${formatDate(event.reported_date)}, ${MARKET_LABELS[event.category]}: ${event.headline}`}
            onPointerEnter={() => { setActive(event); setHovered(null); }} onFocus={() => setActive(event)} />
        ))}
      </svg>
      <p className="chart-caption" aria-live="polite">
        {active ? (
          <>
            <span className={`category mcat--${active.category}`}>{MARKET_LABELS[active.category]}</span>
            {formatDate(active.reported_date)} · {active.headline}
            {active.market_move && ` · S&P 500 ${signed(active.market_move.stock_pct)}`}
            {impact(active) !== null && ` · your portfolio ${money(impact(active)!, "USD", { sign: true, digits: 0 })}`}
            {active.sources[0] && ` · ${host(active.sources[0].url)}`}
          </>
        ) : point ? (
          <>
            <strong>{formatDate(point.date)}</strong> · {money(point.value, "USD")} · {signed((point.value / values[0].value - 1) * 100)} since start
            {benchmarkAt !== null && benchmarkAt !== undefined && ` · S&P 500 ${signed((benchmarkAt / values[0].value - 1) * 100)}`}
          </>
        ) : events.length ? `${events.length} market-wide events. Hover a dot to see how the market and your portfolio moved.`
          : "Click Explain with Exa to mark market-wide news on this chart."}
      </p>
      <p className="portfolio-chart__legend">
        <span className="portfolio-chart__key portfolio-chart__key--portfolio">Your portfolio</span>
        {scaled.some((value) => value !== null) && (
          <span className="portfolio-chart__key portfolio-chart__key--benchmark">S&P 500, scaled to your starting value</span>
        )}
      </p>
    </div>
  );
}
