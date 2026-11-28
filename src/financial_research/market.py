"""Scan market-wide news once for every visitor: central banks, economic data, trade policy, geopolitics."""

import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal

from pydantic import ValidationError

from financial_research.models import Model
from financial_research.news import exa_api_key
from financial_research.radar import (
    BENCHMARK, Citation, Confidence, MissingExaKey, PricePoint, PriceReaction, ScanCache,
    _fresh, _get, _now, _optional_date, _prices, group_grounding, price_reaction,
)

MarketCategory = Literal["monetary_policy", "economic_data", "trade_policy", "geopolitics", "markets", "other"]
# Market news is the same for everyone, so one scan per lookback serves the whole platform.
MARKET_TTL = timedelta(hours=12)
CACHE_NAME = "MARKET"
QUERY = ("Market-moving news for US and global stock markets: central bank and interest rate decisions, "
         "inflation and jobs data, tariffs and trade policy, geopolitical events, major market sell-offs and rallies")

# Exa structured output: at most 10 properties in total and 2 levels of nesting.
MARKET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "description": "Distinct market-wide events in the date range, newest first. Exclude single-company news.",
            "items": {
                "type": "object",
                "properties": {
                    "reported_date": {"type": "string", "description": "Date the news became public, YYYY-MM-DD."},
                    "category": {"type": "string", "enum": list(MarketCategory.__args__)},
                    "headline": {"type": "string", "description": "One factual sentence."},
                    "why_it_matters": {"type": "string", "description": "One sentence on why it moved markets broadly."},
                },
                "required": ["reported_date", "category", "headline", "why_it_matters"],
            },
        }
    },
    "required": ["events"],
}


class MarketError(RuntimeError):
    """Exa could not scan market news and no earlier scan exists."""


class MarketEvent(Model):
    reported_date: date
    category: MarketCategory
    headline: str
    why_it_matters: str
    sources: list[Citation] = []
    confidence: Confidence | None = None
    # The S&P 500 (SPY) from the close before the news to the first close after it.
    market_move: PriceReaction | None = None


class MarketScan(Model):
    start_date: date
    end_date: date
    events: list[MarketEvent]
    benchmark: list[PricePoint] = []
    scanned_at: datetime
    stale: bool = False


def parse_market_events(output: Any, *, start: date, end: date) -> list[MarketEvent]:
    content = _get(output, "content")
    if isinstance(content, str):
        content = json.loads(content)
    rows = content.get("events", []) if isinstance(content, dict) else []
    grounding = group_grounding(output, r"events\[(\d+)\]\.\w+")
    events = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        reported = _optional_date(row.get("reported_date"))
        if reported is None or not start <= reported <= end or not str(row.get("headline") or "").strip():
            continue
        category = row.get("category") if row.get("category") in MarketCategory.__args__ else "other"
        sources, confidence = grounding.get(str(index), ([], None))
        try:
            events.append(MarketEvent(
                reported_date=reported, category=category, headline=str(row["headline"]).strip(),
                why_it_matters=str(row.get("why_it_matters") or "").strip(), sources=sources, confidence=confidence,
            ))
        except ValidationError:
            continue
    return sorted(events, key=lambda event: event.reported_date, reverse=True)


def scan_market(*, days: int = 30, today: date | None = None, cache: ScanCache | None = None) -> MarketScan:
    """One shared scan of market-wide news, reused for 12 hours and falling back to the last good one."""
    cached = cache.read("market", CACHE_NAME, days, MarketScan) if cache else None
    if cached and _fresh(cached.scanned_at, MARKET_TTL):
        return cached
    api_key = exa_api_key()
    if not api_key:
        raise MissingExaKey("Set EXA_API_KEY on the server before scanning market news.")
    end = today or date.today()
    start = end - timedelta(days=days)
    try:
        from exa_py import Exa

        response = Exa(api_key=api_key, timeout=60).search(
            QUERY, type="auto", category="news", num_results=25, output_schema=MARKET_SCHEMA,
            start_published_date=datetime.combine(start, time.min, timezone.utc).isoformat(),
            end_published_date=datetime.combine(end + timedelta(days=1), time.min, timezone.utc).isoformat(),
        )
        events = parse_market_events(response.output, start=start, end=end)
    except Exception as exc:
        # SDK errors can include response bodies; log them and show a safe message.
        logging.getLogger(__name__).exception("Exa market news scan failed")
        if cached:
            return cached.model_copy(update={"stale": True})
        raise MarketError("Exa could not scan market news right now; try again shortly.") from exc

    # Ten extra days give the first event a previous close; the chart only shows the window.
    history = _prices(BENCHMARK, start - timedelta(days=10), end)
    if history:
        events = [event.model_copy(update={"market_move": price_reaction(event.reported_date, history.prices, None)})
                  for event in events]
    scan = MarketScan(
        start_date=start, end_date=end, events=events, scanned_at=_now(),
        benchmark=[PricePoint(date=day, close=round(close, 4)) for day, close in sorted(history.prices.items())
                   if day >= start] if history else [],
    )
    if cache and history:
        cache.write("market", CACHE_NAME, days, scan)
    return scan
