"""Find listed companies from a plain-English request with Exa's company index, then confirm each on Yahoo."""

import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any

from financial_research.models import Model
from financial_research.news import exa_api_key
from financial_research.radar import (
    MAX_WORKERS, Citation, Confidence, MissingExaKey, ScanCache, _fresh, _get, _now, _prices, group_grounding,
)

# Company facts change slowly, so one search per request serves everyone for a day.
DISCOVER_TTL = timedelta(hours=24)
# Discoveries aren't tied to a ticker; the cache files share this placeholder name.
CACHE_NAME = "DISCOVER"

# Exa structured output: at most 10 properties in total and 2 levels of nesting.
DISCOVER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "companies": {
            "type": "array",
            "description": "Up to 10 publicly listed companies that match the request, best matches first.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "ticker": {"type": "string", "description": (
                        "Yahoo Finance ticker, with the exchange suffix outside the US, e.g. NVDA, ASML.AS, 2330.TW.")},
                    "exchange": {"type": "string", "description": "Primary stock exchange, e.g. NASDAQ."},
                    "description": {"type": "string", "description": "One sentence on what the company does."},
                },
                "required": ["name", "ticker", "exchange", "description"],
            },
        }
    },
    "required": ["companies"],
}


class DiscoverError(RuntimeError):
    """Exa could not search companies and no earlier result exists."""


class DiscoveredCompany(Model):
    name: str
    ticker: str
    exchange: str | None = None
    description: str
    sources: list[Citation] = []
    confidence: Confidence | None = None
    last_close: float | None = None
    currency: str | None = None


class Discovery(Model):
    query: str
    companies: list[DiscoveredCompany]
    # Suggestions left out because Yahoo couldn't price the ticker or lists it under another company.
    dropped: list[str] = []
    discovered_at: datetime
    stale: bool = False


def parse_companies(output: Any) -> list[DiscoveredCompany]:
    content = _get(output, "content")
    if isinstance(content, str):
        content = json.loads(content)
    rows = content.get("companies", []) if isinstance(content, dict) else []
    grounding = group_grounding(output, r"companies\[(\d+)\]\.\w+")
    companies, seen = [], set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper().removeprefix("$")
        name = str(row.get("name") or "").strip()
        if not name or not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,11}", ticker) or ticker in seen:
            continue
        seen.add(ticker)
        sources, confidence = grounding.get(str(index), ([], None))
        companies.append(DiscoveredCompany(
            name=name, ticker=ticker, exchange=str(row.get("exchange") or "").strip() or None,
            description=str(row.get("description") or "").strip(), sources=sources, confidence=confidence,
        ))
    return companies


# Legal-form words carry no identity, so they don't count when comparing names.
_LEGAL_WORDS = {"inc", "incorporated", "corp", "corporation", "co", "company", "plc", "ltd", "limited", "ag", "sa",
                "se", "nv", "ab", "publ", "holdings", "holding", "group", "the", "technologies", "technology"}


def _name_words(name: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", name.lower()) if word not in _LEGAL_WORDS and len(word) > 1}


def _confirm(company: DiscoveredCompany, today: date) -> tuple[DiscoveredCompany | None, str | None]:
    """Keep only companies Yahoo can price under the same name, so "Add to holdings" adds the right stock."""
    history = _prices(company.ticker, today - timedelta(days=10), today)
    if not history:
        return None, f"{company.ticker} (Yahoo has no prices)"
    # Funds and indexes can share a company's keywords; only listed companies belong here.
    if history.instrument_type and history.instrument_type.upper() != "EQUITY":
        return None, f"{company.ticker} (Yahoo lists it as {history.name or 'a fund'}, not a company)"
    # A ticker can trade yet belong to a different company than the one Exa described.
    if history.name and not _name_words(company.name) & _name_words(history.name):
        return None, f"{company.ticker} (Yahoo lists it as {history.name})"
    last_day = max(history.prices)
    return company.model_copy(update={"last_close": round(history.prices[last_day], 4), "currency": history.currency}), None


def discover_companies(query: str, *, today: date | None = None, cache: ScanCache | None = None) -> Discovery:
    """Search Exa's company index, reusing a recent result and falling back to it if Exa fails."""
    query = " ".join(query.split())
    today = today or date.today()
    variant = hashlib.sha256(query.lower().encode()).hexdigest()[:12]
    cached = cache.read("discover", CACHE_NAME, 0, Discovery, variant) if cache else None
    if cached and _fresh(cached.discovered_at, DISCOVER_TTL):
        return cached
    api_key = exa_api_key()
    if not api_key:
        raise MissingExaKey("Set EXA_API_KEY on the server before searching companies.")
    try:
        from exa_py import Exa

        # Extra raw results give the structured answer more companies to choose from.
        response = Exa(api_key=api_key, timeout=60).search(
            query, type="auto", category="company", num_results=25, output_schema=DISCOVER_SCHEMA,
        )
        suggested = parse_companies(response.output)
    except Exception as exc:
        # SDK errors can include response bodies; log them and show a safe message.
        logging.getLogger(__name__).exception("Exa company search failed for %r", query)
        if cached:
            return cached.model_copy(update={"stale": True})
        raise DiscoverError("Exa could not search companies right now; try again shortly.") from exc

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        checks = list(pool.map(lambda company: _confirm(company, today), suggested))
    discovery = Discovery(
        query=query, discovered_at=_now(),
        companies=[company for company, _ in checks if company],
        dropped=[reason for _, reason in checks if reason],
    )
    if cache:
        cache.write("discover", CACHE_NAME, 0, discovery, variant)
    return discovery
