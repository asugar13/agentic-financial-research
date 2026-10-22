"""Retrieve FRED series metadata and historical values without an additional SDK."""

import argparse
import json
import math
import os
import sys
from collections.abc import Sequence
from datetime import date
from typing import Annotated
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field, ValidationError, field_validator

from financial_research.models import HistoricalMacroRequest, Observation, ToolResult

URL = "https://api.stlouisfed.org/fred"


class _MacroError(ValueError):
    """Provider failure that can be displayed without exposing credentials."""


class _Series(BaseModel):
    id: str
    title: str = Field(min_length=1)
    units: str = Field(min_length=1)
    frequency: str = Field(min_length=1)
    seasonal_adjustment: str
    last_updated: str


class _MetadataResponse(BaseModel):
    seriess: list[_Series] = Field(min_length=1, max_length=1)


class _Value(BaseModel):
    date: date
    value: Annotated[float, Field(allow_inf_nan=False)] | None

    @field_validator("value", mode="before")
    @classmethod
    def missing_value(cls, value: object) -> object:
        return None if value == "." else value


class _ValuesResponse(BaseModel):
    observations: list[_Value]
    count: int = Field(ge=0)
    offset: int = Field(ge=0)
    realtime_start: date
    realtime_end: date


def _fetch(endpoint: str, api_key: str, **parameters: str | int) -> dict:
    query = urlencode({"api_key": api_key, "file_type": "json", **parameters})
    request = Request(f"{URL}/{endpoint}?{query}", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except HTTPError as exc:
        if exc.code in {400, 401, 403, 404}:
            raise _MacroError("FRED rejected the request; check the series ID and API key.") from exc
        raise _MacroError(f"FRED HTTP {exc.code}; the service may be unavailable or rate-limited.") from exc
    except (URLError, TimeoutError) as exc:
        raise _MacroError("FRED connection failed or timed out.") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise _MacroError("FRED returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise _MacroError("FRED returned an invalid response.")
    if "error_code" in payload:
        # Error bodies can repeat the request URL; never echo them into evidence.
        raise _MacroError("FRED rejected the request; check the series ID, API key, or rate limit.")
    return payload


def _normalize(request: HistoricalMacroRequest, series: _Series, response: _ValuesResponse) -> Observation:
    if series.id.upper() != request.series_id:
        raise _MacroError("FRED returned a different series than requested.")
    if response.offset != 0 or response.count != len(response.observations):
        raise _MacroError("FRED returned incomplete history; this tool requires the full requested interval.")
    rows = sorted(
        (row for row in response.observations if request.start_date <= row.date <= request.end_date),
        key=lambda row: row.date,
    )
    if len({row.date for row in rows}) != len(rows):
        raise _MacroError("FRED returned duplicate observation dates.")
    valid = [row for row in rows if row.value is not None]
    if len(valid) < 2:
        raise _MacroError(f"{request.series_id}: fewer than two numeric observations in the requested date range.")
    first, last = valid[0], valid[-1]
    change = round(last.value - first.value, 6)
    if not math.isfinite(change):
        raise _MacroError("FRED values produced a non-finite change.")
    change_unit = "percentage points" if series.units == "Percent" else series.units
    missing = len(rows) - len(valid)
    coverage_note = (
        f"Numeric observations cover {first.date} to {last.date}; requested dates are "
        f"{request.start_date} to {request.end_date}. No missing observations are filled."
        if first.date != request.start_date or last.date != request.end_date or missing else None
    )
    content = (
        f"{request.series_id} ({series.title}): changed from {first.value:g} {series.units} on {first.date} "
        f"to {last.value:g} {series.units} on {last.date}, a change of {change:g} {change_unit}. "
        f"Frequency: {series.frequency}; {series.seasonal_adjustment}. "
        f"Missing observations: {missing}. These are the latest available FRED values at retrieval, "
        "which may include revisions; they are not an as-of backtest dataset."
    )
    if coverage_note:
        content += f" {coverage_note}"
    return Observation(
        source_type="macro", source_name=f"FRED {request.series_id}: {series.title}",
        source_uri=f"https://fred.stlouisfed.org/series/{quote(request.series_id, safe='')}",
        period_start=first.date, period_end=last.date, content=content, is_fixture=False,
        data={
            "series_id": request.series_id, "title": series.title, "unit": series.units,
            "frequency": series.frequency, "seasonal_adjustment": series.seasonal_adjustment,
            "last_updated": series.last_updated,
            "requested_start": request.start_date.isoformat(), "requested_end": request.end_date.isoformat(),
            "realtime_start": response.realtime_start.isoformat(), "realtime_end": response.realtime_end.isoformat(),
            "start_value": first.value, "end_value": last.value, "change": change, "change_unit": change_unit,
            "observation_count": len(valid), "missing_count": missing, "coverage_note": coverage_note,
            "values": [row.model_dump(mode="json") for row in rows],
        },
    )


def get_macro_series(request: HistoricalMacroRequest) -> ToolResult:
    """Retrieve one FRED series with its provider metadata and explicit failures."""
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if not api_key:
        return ToolResult(error="Set FRED_API_KEY before fetching real macro data.")
    try:
        metadata = _MetadataResponse.model_validate(_fetch("series", api_key, series_id=request.series_id))
        values = _ValuesResponse.model_validate(_fetch(
            "series/observations", api_key, series_id=request.series_id,
            observation_start=request.start_date.isoformat(), observation_end=request.end_date.isoformat(),
            units="lin", sort_order="asc", limit=100000, offset=0,
        ))
        return ToolResult(observations=[_normalize(request, metadata.seriess[0], values)])
    except _MacroError as exc:
        return ToolResult(error=str(exc))
    except (ValidationError, ValueError, OverflowError):
        return ToolResult(error="FRED returned malformed metadata, dates, or numeric values.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch real historical FRED data independently of the agent.")
    parser.add_argument("--series-id", required=True)
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    args = parser.parse_args(argv)
    try:
        request = HistoricalMacroRequest(
            series_id=args.series_id, start_date=args.start_date, end_date=args.end_date,
            rationale="Retrieve the historical macro data requested through the standalone CLI.",
        )
    except ValidationError:
        parser.error("Provide a nonempty series ID and start-date earlier than end-date.")
    result = get_macro_series(request)
    print(result.model_dump_json(indent=2))
    return 1 if result.error else 0


if __name__ == "__main__":
    sys.exit(main())
