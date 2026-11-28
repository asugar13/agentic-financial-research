"""Answer a question about one stock over a chosen period, with Exa deep search and a source for every point."""

import hashlib
import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from financial_research.explain import Driver, Finding, ResultSource, _finding, result_sources
from financial_research.models import Model
from financial_research.news import exa_api_key
from financial_research.radar import (
    MissingExaKey, PricePoint, ScanCache, SearchSettings, _fresh, _get, _now, _prices, group_grounding,
)

# Answers about a past period barely change, so one research run serves everyone for a day.
ANSWER_TTL = timedelta(hours=24)
# A little price history either side of the period puts the moves in context.
CHART_PADDING = timedelta(days=7)

# Exa structured output: at most 10 properties in total and 2 levels of nesting.
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "description": "2-4 sentences that directly answer the question, based on the sources."},
        "key_points": {
            "type": "array",
            "description": "2-5 specific findings that support the answer, most important first.",
            "items": {
                "type": "object",
                "properties": {
                    "point": {"type": "string", "description": "One sentence stating the finding."},
                    "evidence": {"type": "string", "description": "The specific reported fact behind it, with its date."},
                },
                "required": ["point", "evidence"],
            },
        },
        "gaps": {"type": "string", "description": (
            "What the sources in this period don't cover about the question, or an empty string.")},
    },
    "required": ["answer", "key_points", "gaps"],
}


class AskError(RuntimeError):
    """Exa could not answer and no earlier answer exists."""


class StockAnswer(Model):
    ticker: str
    name: str | None = None
    currency: str | None = None
    question: str
    start_date: date
    end_date: date
    answer: Finding
    key_points: list[Driver]
    gaps: Finding | None = None
    sources: list[ResultSource] = []
    prices: list[PricePoint] = []
    settings: SearchSettings = SearchSettings()
    answered_at: datetime
    # True when a live research run failed and this is the most recent earlier answer.
    stale: bool = False


def parse_answer(output: Any) -> tuple[Finding, list[Driver], Finding | None]:
    content = _get(output, "content")
    if isinstance(content, str):
        content = json.loads(content)
    if not isinstance(content, dict):
        raise ValueError("Exa returned no structured answer.")
    grounding = group_grounding(output, r"(answer|gaps|key_points\[\d+\])(?:\.\w+)?")
    points = []
    for index, row in enumerate(content.get("key_points") or []):
        if not isinstance(row, dict) or not str(row.get("point") or "").strip():
            continue
        sources, confidence = grounding.get(f"key_points[{index}]", ([], None))
        points.append(Driver(point=str(row["point"]).strip(), evidence=str(row.get("evidence") or "").strip(),
                             sources=sources, confidence=confidence))
    answer = _finding(content, "answer", grounding)
    if answer is None:
        raise ValueError("Exa returned a response without an answer.")
    return answer, points, _finding(content, "gaps", grounding)


def ask_about_stock(ticker: str, question: str, start: date, end: date, *, settings: SearchSettings | None = None,
                    today: date | None = None, cache: ScanCache | None = None) -> StockAnswer:
    """Research one question for one period, reusing a recent answer and falling back to it if Exa fails."""
    settings = settings or SearchSettings()
    question = " ".join(question.split())
    today = today or date.today()
    fingerprint = json.dumps([question.lower(), start.isoformat(), end.isoformat(), settings.model_dump(mode="json")])
    variant = hashlib.sha256(fingerprint.encode()).hexdigest()[:12]
    # Answers aren't tied to a lookback window, so their cache files use 0 days.
    cached = cache.read("answers", ticker, 0, StockAnswer, variant) if cache else None
    if cached and _fresh(cached.answered_at, ANSWER_TTL):
        return cached
    api_key = exa_api_key()
    if not api_key:
        raise MissingExaKey("Set EXA_API_KEY on the server before asking questions.")

    history = _prices(ticker, start - CHART_PADDING, min(end + CHART_PADDING, today))
    name = history.name if history else None
    company = f"{name} ({ticker})" if name else ticker
    query = f"{question} About {company}, between {start:%d %B %Y} and {end:%d %B %Y}."
    if settings.focus:
        query += f" Focus on: {settings.focus}"
    domains = {key: value for key, value in (("include_domains", settings.include_domains),
                                            ("exclude_domains", settings.exclude_domains)) if value}
    try:
        from exa_py import Exa

        response = Exa(api_key=api_key, timeout=90).search(
            query, type="deep", category="news", num_results=settings.num_results, output_schema=ANSWER_SCHEMA,
            start_published_date=datetime.combine(start, time.min, timezone.utc).isoformat(),
            end_published_date=datetime.combine(end + timedelta(days=1), time.min, timezone.utc).isoformat(), **domains,
        )
        answer, points, gaps = parse_answer(response.output)
    except Exception as exc:
        # SDK errors can include response bodies; log them and show a safe message.
        logging.getLogger(__name__).exception("Exa deep search failed for %s: %r", ticker, question)
        if cached:
            return cached.model_copy(update={"stale": True})
        raise AskError("Exa could not answer this right now; try again shortly.") from exc

    result = StockAnswer(
        ticker=ticker, name=name, currency=history.currency if history else None, question=question,
        start_date=start, end_date=end, answer=answer, key_points=points, gaps=gaps,
        sources=result_sources(response.results), settings=settings, answered_at=_now(),
        prices=[PricePoint(date=day, close=round(close, 4)) for day, close in sorted(history.prices.items())] if history else [],
    )
    if cache and history:
        cache.write("answers", ticker, 0, result, variant)
    return result
