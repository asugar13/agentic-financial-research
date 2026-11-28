"""Explain why a stock moved around one event, with Exa deep search and a source for every field."""

import hashlib
import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from financial_research.models import Model
from financial_research.news import exa_api_key
from financial_research.radar import (
    BENCHMARK, Citation, Confidence, MissingExaKey, PricePoint, PriceReaction, ScanCache, SearchSettings,
    _fresh, _get, _now, _prices, group_grounding, price_reaction,
)

# A past event's explanation barely changes, so one research run serves everyone for a day.
EXPLANATION_TTL = timedelta(hours=24)
# Catch the run-up to the news and the market's reaction afterwards.
NEWS_BEFORE, NEWS_AFTER = timedelta(days=3), timedelta(days=4)
# Enough trading days either side to see the move in context on the chart.
CHART_BEFORE, CHART_AFTER = timedelta(days=21), timedelta(days=14)

# Exa structured output: at most 10 properties in total and 2 levels of nesting.
EXPLANATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "2-3 sentences: why the stock moved around the date, based on the sources."},
        "drivers": {
            "type": "array",
            "description": "2-4 distinct reasons for the move, most important first.",
            "items": {
                "type": "object",
                "properties": {
                    "point": {"type": "string", "description": "One sentence naming the driver."},
                    "evidence": {"type": "string", "description": "The specific reported fact that supports it."},
                },
                "required": ["point", "evidence"],
            },
        },
        "market_context": {"type": "string", "description": (
            "Whether the move was company-specific, sector-wide, or market-wide, with figures if reported.")},
        # Dated facts only: forward-looking opinions would read like investment advice.
        "upcoming": {"type": "string", "description": (
            "Dated upcoming events mentioned in the sources (an earnings date, a court date), or an empty string.")},
    },
    "required": ["summary", "drivers", "market_context", "upcoming"],
}


class ExplainError(RuntimeError):
    """Exa could not research the event and no earlier explanation exists."""


class Finding(Model):
    text: str
    sources: list[Citation] = []
    confidence: Confidence | None = None


class Driver(Model):
    point: str
    evidence: str
    sources: list[Citation] = []
    confidence: Confidence | None = None


class ResultSource(Model):
    title: str
    url: str
    published_date: date | None = None


class EventExplanation(Model):
    ticker: str
    name: str | None = None
    currency: str | None = None
    reported_date: date
    headline: str
    summary: Finding
    drivers: list[Driver]
    market_context: Finding
    upcoming: Finding | None = None
    sources: list[ResultSource] = []
    prices: list[PricePoint] = []
    reaction: PriceReaction | None = None
    settings: SearchSettings = SearchSettings()
    explained_at: datetime
    # True when a live research run failed and this is the most recent earlier one.
    stale: bool = False


def _finding(content: dict, key: str, grounding: dict) -> Finding | None:
    text = str(content.get(key) or "").strip()
    if not text:
        return None
    sources, confidence = grounding.get(key, ([], None))
    return Finding(text=text, sources=sources, confidence=confidence)


def parse_explanation(output: Any) -> tuple[Finding, list[Driver], Finding, Finding | None]:
    """Turn Exa's structured answer into findings, each keeping its own citations and confidence."""
    content = _get(output, "content")
    if isinstance(content, str):
        content = json.loads(content)
    if not isinstance(content, dict):
        raise ValueError("Exa returned no structured explanation.")
    grounding = group_grounding(output, r"(summary|market_context|upcoming|drivers\[\d+\])(?:\.\w+)?")
    drivers = []
    for index, row in enumerate(content.get("drivers") or []):
        if not isinstance(row, dict) or not str(row.get("point") or "").strip():
            continue
        sources, confidence = grounding.get(f"drivers[{index}]", ([], None))
        drivers.append(Driver(point=str(row["point"]).strip(), evidence=str(row.get("evidence") or "").strip(),
                              sources=sources, confidence=confidence))
    summary = _finding(content, "summary", grounding)
    if summary is None:
        raise ValueError("Exa returned an explanation without a summary.")
    context = _finding(content, "market_context", grounding) or Finding(text="Not reported.")
    return summary, drivers, context, _finding(content, "upcoming", grounding)


def result_sources(results: Any) -> list[ResultSource]:
    """Every titled web page Exa read, with its publication date when known."""
    sources = []
    for item in results or []:
        url, title = getattr(item, "url", None), getattr(item, "title", None)
        if isinstance(url, str) and url.startswith(("https://", "http://")) and title:
            published = str(getattr(item, "published_date", "") or "")[:10]
            sources.append(ResultSource(title=title, url=url, published_date=published or None))
    return sources


def _research(ticker: str, name: str | None, reported: date, headline: str, today: date,
              api_key: str, settings: SearchSettings) -> tuple[Any, list[ResultSource]]:
    from exa_py import Exa

    company = f"{name} ({ticker})" if name else ticker
    query = f"Why did {company} stock move around {reported:%d %B %Y}? Reported: {headline}"
    if settings.focus:
        query += f" Focus on: {settings.focus}"
    domains = {key: value for key, value in (("include_domains", settings.include_domains),
                                            ("exclude_domains", settings.exclude_domains)) if value}
    start, end = reported - NEWS_BEFORE, min(reported + NEWS_AFTER, today)
    # Deep search runs several searches and synthesizes one answer; in testing it was the most
    # specific engine for this question at about $0.012 and 5-10 seconds.
    response = Exa(api_key=api_key, timeout=90).search(
        query, type="deep", category="news", num_results=settings.num_results, output_schema=EXPLANATION_SCHEMA,
        start_published_date=datetime.combine(start, time.min, timezone.utc).isoformat(),
        end_published_date=datetime.combine(end + timedelta(days=1), time.min, timezone.utc).isoformat(), **domains,
    )
    return response.output, result_sources(response.results)


def _variant(reported: date, headline: str, settings: SearchSettings) -> str:
    fingerprint = json.dumps([reported.isoformat(), headline, settings.model_dump(mode="json")], sort_keys=True)
    return hashlib.sha256(fingerprint.encode()).hexdigest()[:12]


def explain_event(ticker: str, reported: date, headline: str, *, settings: SearchSettings | None = None,
                  today: date | None = None, cache: ScanCache | None = None) -> EventExplanation:
    """Research one event, reusing a recent explanation and falling back to the last good one if Exa fails."""
    settings = settings or SearchSettings()
    today = today or date.today()
    variant = _variant(reported, headline, settings)
    # Explanations aren't tied to a lookback window, so their cache files use 0 days.
    cached = cache.read("explanations", ticker, 0, EventExplanation, variant) if cache else None
    if cached and _fresh(cached.explained_at, EXPLANATION_TTL):
        return cached
    api_key = exa_api_key()
    if not api_key:
        raise MissingExaKey("Set EXA_API_KEY on the server before researching events.")

    window_start, window_end = reported - CHART_BEFORE, min(reported + CHART_AFTER, today)
    history = _prices(ticker, window_start, window_end)
    benchmark = _prices(BENCHMARK, window_start, window_end)
    name = history.name if history else None
    try:
        output, sources = _research(ticker, name, reported, headline, today, api_key, settings)
        summary, drivers, context, upcoming = parse_explanation(output)
    except Exception as exc:
        # SDK errors can include response bodies; log them and show a safe message.
        logging.getLogger(__name__).exception("Exa deep search failed for %s on %s", ticker, reported)
        if cached:
            return cached.model_copy(update={"stale": True})
        raise ExplainError("Exa could not research this event right now; try again shortly.") from exc

    explanation = EventExplanation(
        ticker=ticker, name=name, currency=history.currency if history else None, reported_date=reported,
        headline=headline, summary=summary, drivers=drivers, market_context=context, upcoming=upcoming,
        sources=sources, settings=settings, explained_at=_now(),
        prices=[PricePoint(date=day, close=round(close, 4)) for day, close in sorted(history.prices.items())] if history else [],
        reaction=price_reaction(reported, history.prices, benchmark.prices if benchmark else None) if history else None,
    )
    # Without prices the page lacks its chart and move, so don't share it for a whole day.
    if cache and history:
        cache.write("explanations", ticker, 0, explanation, variant)
    return explanation
