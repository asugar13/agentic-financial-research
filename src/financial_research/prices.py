"""Fetch daily Yahoo Finance history independently of the research graph."""

import argparse
import math
import sys
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Annotated
from urllib.parse import quote

from pydantic import BaseModel, Field, ValidationError
import yfinance as yf

from financial_research.models import HistoricalPriceRequest, Observation, ToolResult


class _PriceError(ValueError):
    """Safe-to-display provider, transport, or coverage error."""


class _DailyResponse(BaseModel):
    prices: dict[date, Annotated[float, Field(gt=0, allow_inf_nan=False)]] = Field(min_length=1)
    currency: str | None = None
    time_zone: str | None = None


def _fetch(request: HistoricalPriceRequest, symbol: str) -> _DailyResponse:
    try:
        ticker = yf.Ticker(symbol)
        history = ticker.history(
            start=request.start_date.isoformat(),
            # Yahoo's end is exclusive; our public request includes both dates.
            end=(request.end_date + timedelta(days=1)).isoformat(),
            interval="1d", auto_adjust=False, actions=False, repair=False,
            keepna=True, timeout=20,
        )
        if history.empty:
            raise _PriceError(
                f"Yahoo Finance returned no daily prices for {symbol}; "
                "check the symbol, date range, connection, or rate limit."
            )
        if "Close" not in history.columns:
            raise _PriceError(f"Yahoo Finance returned no Close column for {symbol}.")
        metadata = ticker.get_history_metadata()
    except _PriceError:
        raise
    except Exception as exc:
        # yfinance can raise several transport/provider errors. Keep them at
        # this boundary and avoid exposing raw response bodies or session URLs.
        raise _PriceError(
            f"Yahoo Finance could not retrieve {symbol}; check the connection or rate limit."
        ) from exc
    if metadata.get("symbol", symbol).upper() != symbol:
        raise _PriceError(f"Yahoo Finance returned a different symbol than requested: {symbol}.")
    days = [timestamp.date() for timestamp in history.index]
    if len(set(days)) != len(days):
        raise _PriceError(f"Yahoo Finance returned duplicate daily prices for {symbol}.")
    return _DailyResponse(
        prices=dict(zip(days, history["Close"])),
        currency=metadata.get("currency"), time_zone=metadata.get("exchangeTimezoneName"),
    )


def _normalize(request: HistoricalPriceRequest, symbol: str, response: _DailyResponse) -> Observation:
    dates = sorted(day for day in response.prices if request.start_date <= day <= request.end_date)
    if len(dates) < 2:
        raise _PriceError(f"{symbol}: fewer than two closing prices in the requested date range.")
    first, last = dates[0], dates[-1]
    start, end = response.prices[first], response.prices[last]
    change = round((end / start - 1) * 100, 6)
    if not math.isfinite(change):
        raise _PriceError(f"{symbol}: prices produced a non-finite price change.")
    coverage_note = (
        f"Observed dates are {first} to {last}; requested dates are {request.start_date} to {request.end_date}. "
        "Non-trading days or provider coverage can shorten the interval; no missing prices are filled."
        if first != request.start_date or last != request.end_date else None
    )
    content = (
        f"{symbol}: Yahoo daily Close changed from {start:g} on {first} to {end:g} on {last}, "
        f"a {change:g}% close-price change. Currency/unit: {response.currency or 'unknown'}. "
        "Uses the provider Close column without additional dividend adjustment; this is not a total return."
    )
    if coverage_note:
        content += f" {coverage_note}"
    return Observation(
        source_type="prices", source_name="Yahoo Finance daily history via yfinance",
        source_uri=f"https://finance.yahoo.com/quote/{quote(symbol, safe='')}/history/",
        period_start=first, period_end=last, is_fixture=False, content=content,
        data={
            "symbol": symbol, "price_type": "close", "auto_adjust": False,
            "currency": response.currency, "time_zone": response.time_zone,
            "requested_start": request.start_date.isoformat(), "requested_end": request.end_date.isoformat(),
            "start_price": start, "end_price": end, "price_change_pct": change,
            "coverage_note": coverage_note,
            "prices": [{"date": day.isoformat(), "close": response.prices[day]} for day in dates],
        },
    )


def get_market_prices(request: HistoricalPriceRequest) -> ToolResult:
    """Request each symbol's dated history; any failure discards partial results."""
    try:
        observations = [_normalize(request, symbol, _fetch(request, symbol)) for symbol in request.symbols]
    except _PriceError as exc:
        return ToolResult(error=str(exc))
    except (ValidationError, ValueError, TypeError, AttributeError, OverflowError):
        return ToolResult(error="Yahoo Finance returned malformed date or price data.")
    return ToolResult(observations=observations)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch real daily prices independently of the research agent.")
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    args = parser.parse_args(argv)
    try:
        request = HistoricalPriceRequest(
            symbols=args.symbols, start_date=args.start_date, end_date=args.end_date,
            rationale="Retrieve the historical prices requested through the standalone CLI.",
        )
    except ValidationError:
        parser.error("Provide 1–5 nonempty symbols and start-date earlier than end-date.")
    result = get_market_prices(request)
    print(result.model_dump_json(indent=2))
    return 1 if result.error else 0


if __name__ == "__main__":
    sys.exit(main())
