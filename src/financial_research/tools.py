"""Typed fixture tools. Every number and news item here is synthetic."""

import re
from datetime import date
from typing import Literal

from pydantic import JsonValue

from financial_research.models import (
    Action,
    HistoricalPriceRequest,
    MacroAction,
    MacroRequest,
    NewsAction,
    NewsRequest,
    Observation,
    PriceAction,
    PriceRequest,
    ToolResult,
)

PERIOD_START = date(2025, 1, 2)
PERIOD_END = date(2025, 3, 31)
FIXTURE_SCOPE = (
    "All tools return synthetic fixtures for 2025-01-02 through 2025-03-31, "
    "not current market data. Prices: XAGUSD (silver), XAUUSD (gold). "
    "Macro: DFII10 (10-year real yield). News: fictional metals demand commentary. "
    "Interpret 'recently' as this fixture period and state that assumption. "
    "Other instruments, periods, and macro series are unavailable."
)


def _observation(
    kind: Literal["prices", "macro", "news"], key: str, content: str, **data: JsonValue,
) -> Observation:
    return Observation(
        source_type=kind,
        source_name=f"Synthetic {kind} fixture",
        source_uri=f"fixture://{kind}/{key}",
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        content=content,
        data=data,
    )


def get_market_prices(request: PriceRequest) -> ToolResult:
    prices = {"XAGUSD": (25.0, 30.0), "XAUUSD": (2000.0, 2100.0)}
    symbols = list(dict.fromkeys(request.symbols))
    missing = sorted(set(symbols) - prices.keys())
    if missing:
        return ToolResult(error=f"Fixture prices unavailable for: {', '.join(missing)}.")
    observations = []
    for symbol in symbols:
        start, end = prices[symbol]
        change = round((end / start - 1) * 100, 4)
        observations.append(_observation(
            "prices", symbol,
            f"Synthetic {symbol} price rose from {start:g} to {end:g} USD "
            f"per troy ounce; simple return was {change:g}% during the fixture period.",
            symbol=symbol, start_price=start, end_price=end, return_pct=change,
        ))
    return ToolResult(observations=observations)


def get_macro_series(request: MacroRequest) -> ToolResult:
    if request.series_id != "DFII10":
        return ToolResult(error=f"Fixture macro series unavailable: {request.series_id}.")
    return ToolResult(observations=[_observation(
        "macro", "DFII10",
        "Synthetic 10-year real yield declined from 2.2% to 2.0% "
        "during the fixture period. These two endpoints do not establish causation.",
        series_id="DFII10", start_value=2.2, end_value=2.0, unit="percent",
    )])


def search_news(request: NewsRequest) -> ToolResult:
    words = set(re.findall(r"[a-z]+", request.query.lower()))
    articles = [
        (
            "industrial-demand", {"silver", "industrial", "solar", "demand", "metals"},
            "Fictional industry commentary describes stronger solar-sector silver demand. "
            "It provides no measured estimate of the effect on silver prices.",
        ),
        (
            "counterpoint", {"silver", "gold", "yield", "yields", "metals", "demand"},
            "Fictional metals commentary notes mixed manufacturing demand and warns "
            "that lower real yields could support both gold and silver, leaving "
            "relative outperformance unexplained.",
        ),
    ]
    return ToolResult(observations=[
        _observation("news", key, content)
        for key, tags, content in articles if words & tags
    ])


def execute_tool(action: Action, price_request: HistoricalPriceRequest | None = None) -> ToolResult:
    """Explicit allowlist: a model can request only one of these typed tools."""
    if price_request is not None:
        if not isinstance(action, PriceAction):
            return ToolResult(error="Only the price tool is available in real-data mode; macro and news are not connected.")
        if action.start_date is not None and (
            action.start_date != price_request.start_date or action.end_date != price_request.end_date
        ):
            return ToolResult(error="Price-tool dates are outside the configured research scope.")
        request = HistoricalPriceRequest(
            symbols=action.symbols, start_date=price_request.start_date, end_date=price_request.end_date,
            rationale=action.rationale,
        )
        if outside_scope := set(request.symbols) - set(price_request.symbols):
            return ToolResult(error=f"Symbols outside the configured research scope: {', '.join(sorted(outside_scope))}.")
        from financial_research.prices import get_market_prices as get_real_prices

        return get_real_prices(request)
    match action:
        case PriceAction():
            if action.start_date is not None and (action.start_date != PERIOD_START or action.end_date != PERIOD_END):
                return ToolResult(error=f"Fixture prices cover only {PERIOD_START} to {PERIOD_END}.")
            return get_market_prices(action)
        case MacroAction():
            return get_macro_series(action)
        case NewsAction():
            return search_news(action)
        case _:
            raise ValueError("finish_research is a routing action, not a data tool.")
