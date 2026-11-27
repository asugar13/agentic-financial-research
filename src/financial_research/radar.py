"""Scan a watchlist for material company events with Exa, then measure each event's price reaction."""

import hashlib
import json
import logging
import re
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, TypeVar
from uuid import uuid4

from pydantic import Field, ValidationError, field_validator

from financial_research.models import HistoricalPriceRequest, Model
from financial_research.news import exa_api_key
from financial_research.prices import _DailyResponse, _fetch, _PriceError

Category = Literal["earnings", "guidance", "legal", "regulatory", "management", "deal", "product", "other"]
Confidence = Literal["low", "medium", "high"]
BENCHMARK = "SPY"
# A small pool keeps a 15-name scan fast without bursting into Exa's rate limit.
MAX_WORKERS = 5
# One Exa scan per company per day serves every visitor; prices stay close to live.
EVENTS_TTL = timedelta(hours=12)
PRICES_TTL = timedelta(minutes=30)
# Bump when cached shapes change so older files are ignored rather than misread.
CACHE_VERSION = 1

# Exa structured output: at most 10 properties in total and 2 levels of nesting.
EVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "description": "Distinct material events for the company in the date range, newest first.",
            "items": {
                "type": "object",
                "properties": {
                    "reported_date": {"type": "string", "description": "Date the news first became public, YYYY-MM-DD."},
                    "scheduled_date": {"type": "string", "description": (
                        "Only if the event itself takes place after it was reported (a launch, deadline, or vote): "
                        "that later date as YYYY-MM-DD. Otherwise an empty string.")},
                    "category": {"type": "string", "enum": list(Category.__args__)},
                    "headline": {"type": "string", "description": "One factual sentence."},
                    "why_it_matters": {"type": "string", "description": "One sentence on the likely impact for shareholders."},
                },
                "required": ["reported_date", "category", "headline", "why_it_matters"],
            },
        }
    },
    "required": ["events"],
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


CachedModel = TypeVar("CachedModel", bound=Model)


class ScanCache:
    """One JSON file per company and lookback, shared by every visitor.

    Fresh copies avoid repeat provider calls; older copies stand in when a live call fails.
    """

    def __init__(self, root: Path):
        self.root = root / f"v{CACHE_VERSION}"

    def _path(self, kind: str, ticker: str, days: int, variant: str = "") -> Path:
        # Tickers become file names, so accept only the characters a ticker can contain.
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,11}", ticker) or not re.fullmatch(r"[0-9a-f]*", variant):
            raise ValueError(f"Unsafe cache file name for {ticker!r}")
        suffix = f"-{variant}" if variant else ""
        return self.root / kind / f"{ticker}-{days}d{suffix}.json"

    def read(self, kind: str, ticker: str, days: int, model: type[CachedModel], variant: str = "") -> CachedModel | None:
        try:
            return model.model_validate_json(self._path(kind, ticker, days, variant).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def write(self, kind: str, ticker: str, days: int, value: Model, variant: str = "") -> None:
        try:
            path = self._path(kind, ticker, days, variant)
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write then rename, so a concurrent reader never sees half a file.
            temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
            temporary.write_text(value.model_dump_json(), encoding="utf-8")
            temporary.replace(path)
        except (OSError, ValueError):
            logging.getLogger(__name__).warning("Could not cache %s for %s", kind, ticker)


def _fresh(stamp: datetime | None, ttl: timedelta) -> bool:
    return stamp is not None and _now() - stamp < ttl


class SearchSettings(Model):
    """Exa controls a broker can tune; the defaults reproduce the standard scan."""

    include_domains: list[str] = Field(default_factory=list, max_length=20)
    exclude_domains: list[str] = Field(default_factory=list, max_length=20)
    focus: str = Field(default="", max_length=200)
    num_results: Literal[10, 25] = 10

    @field_validator("include_domains", "exclude_domains")
    @classmethod
    def clean_domains(cls, domains: list[str]) -> list[str]:
        cleaned = []
        for domain in domains:
            domain = domain.strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")
            if not domain:
                continue
            # Exa accepts a host, a wildcard subdomain, or a host with a path prefix.
            if not re.fullmatch(r"(\*\.)?[a-z0-9-]+(\.[a-z0-9-]+)+(/[\w./-]*)?", domain):
                raise ValueError(f"Enter sources as domains such as reuters.com, not {domain!r}.")
            cleaned.append(domain)
        return list(dict.fromkeys(cleaned))

    def cache_variant(self) -> str:
        """Default settings keep the original cache files; anything else gets its own copy."""
        if self == SearchSettings():
            return ""
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:12]


class MissingExaKey(RuntimeError):
    """The server has no Exa API key, so no scan can run."""


class Citation(Model):
    title: str
    url: str


class PriceReaction(Model):
    """Close before the event to the first close after it, so after-hours news is included."""
    from_date: date
    to_date: date
    stock_pct: float
    benchmark_pct: float | None = None
    excess_pct: float | None = None


class CompanyEvent(Model):
    # The market reacts when news becomes public, so price moves start from reported_date.
    reported_date: date
    scheduled_date: date | None = None
    category: Category
    headline: str
    why_it_matters: str
    confidence: Confidence | None = None
    sources: list[Citation] = []
    upcoming: bool = False
    reaction: PriceReaction | None = None


class CompanyScan(Model):
    ticker: str
    name: str | None = None
    events: list[CompanyEvent] = []
    error: str | None = None
    scanned_at: datetime | None = None
    # True when a live search failed and this is the most recent earlier scan.
    stale: bool = False


class WatchlistScan(Model):
    start_date: date
    end_date: date
    benchmark: str
    companies: list[CompanyScan]
    settings: SearchSettings = SearchSettings()


class PricePoint(Model):
    date: date
    close: float


class PriceSeries(Model):
    ticker: str
    name: str | None = None
    currency: str | None = None
    prices: list[PricePoint] = []
    change_pct: float | None = None
    error: str | None = None
    fetched_at: datetime | None = None
    stale: bool = False


class WatchlistPrices(Model):
    start_date: date
    end_date: date
    series: list[PriceSeries]


def _get(item: Any, key: str) -> Any:
    # The SDK returns objects; tests and older SDK versions may return dictionaries.
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def _grounding(output: Any) -> dict[int, tuple[list[Citation], Confidence | None]]:
    """Group Exa's per-field citations by event index, keeping each event's weakest confidence."""
    rank = {"low": 0, "medium": 1, "high": 2}
    grouped: dict[int, tuple[dict[str, Citation], Confidence | None]] = {}
    for entry in _get(output, "grounding") or []:
        match = re.fullmatch(r"events\[(\d+)\]\.\w+", str(_get(entry, "field") or ""))
        if not match:
            continue
        index = int(match.group(1))
        citations, confidence = grouped.get(index, ({}, None))
        for citation in _get(entry, "citations") or []:
            url = _get(citation, "url")
            if isinstance(url, str) and url.startswith(("https://", "http://")) and url not in citations:
                citations[url] = Citation(title=_get(citation, "title") or url, url=url)
        level = _get(entry, "confidence")
        if level in rank and (confidence is None or rank[level] < rank[confidence]):
            confidence = level
        grouped[index] = (citations, confidence)
    return {index: (list(citations.values()), confidence) for index, (citations, confidence) in grouped.items()}


def _optional_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def parse_events(output: Any, *, start: date, end: date) -> list[CompanyEvent]:
    """Validate Exa's structured rows; drop malformed or out-of-window rows, flag scheduled future ones."""
    content = _get(output, "content")
    if isinstance(content, str):
        content = json.loads(content)
    rows = content.get("events", []) if isinstance(content, dict) else []
    grounding = _grounding(output)
    events = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        sources, confidence = grounding.get(index, ([], None))
        if row.get("category") not in Category.__args__:
            row = {**row, "category": "other"}
        reported = _optional_date(row.get("reported_date"))
        if reported is None or not start <= reported <= end or not row.get("headline"):
            continue
        # A vague or earlier "scheduled" date adds nothing; keep only real future dates.
        scheduled = _optional_date(row.get("scheduled_date"))
        if scheduled is not None and scheduled <= reported:
            scheduled = None
        try:
            event = CompanyEvent(
                reported_date=reported, scheduled_date=scheduled, category=row["category"],
                headline=row["headline"], why_it_matters=row.get("why_it_matters", ""),
                confidence=confidence, sources=sources, upcoming=scheduled is not None and scheduled > end,
            )
        except (ValidationError, KeyError):
            continue
        events.append(event)
    return sorted(events, key=lambda event: event.reported_date, reverse=True)


def price_reaction(event_date: date, stock: dict[date, float], benchmark: dict[date, float] | None) -> PriceReaction | None:
    """Return the move from the last close before the event to the first close after it."""
    before = [day for day in stock if day < event_date]
    after = [day for day in stock if day > event_date]
    if not before:
        return None
    # On the latest session there is no later close yet; use the event day's own close.
    end_day = min(after) if after else (event_date if event_date in stock else None)
    if end_day is None:
        return None
    start_day = max(before)
    stock_pct = round((stock[end_day] / stock[start_day] - 1) * 100, 2)
    benchmark_pct = None
    if benchmark and start_day in benchmark and end_day in benchmark:
        benchmark_pct = round((benchmark[end_day] / benchmark[start_day] - 1) * 100, 2)
    return PriceReaction(
        from_date=start_day, to_date=end_day, stock_pct=stock_pct, benchmark_pct=benchmark_pct,
        excess_pct=None if benchmark_pct is None else round(stock_pct - benchmark_pct, 2),
    )


def _prices(symbol: str, start: date, end: date) -> _DailyResponse | None:
    request = HistoricalPriceRequest(
        symbols=[symbol], start_date=start, end_date=end, rationale="Measure price reactions to company events.",
    )
    try:
        return _fetch(request, symbol)
    except (_PriceError, ValidationError, ValueError):
        return None


def _search(ticker: str, name: str | None, start: date, end: date, api_key: str,
            settings: SearchSettings) -> Any:
    from exa_py import Exa

    company = f"{name} ({ticker})" if name else f"{ticker} stock"
    query = (f"{company} material company news: earnings, guidance, lawsuits, regulatory actions, "
             "executive changes, deals, product launches")
    if settings.focus:
        query += f". Focus on: {settings.focus}"
    # Empty domain lists are omitted so the default request is unchanged.
    domains = {key: value for key, value in (("include_domains", settings.include_domains),
                                            ("exclude_domains", settings.exclude_domains)) if value}
    response = Exa(api_key=api_key, timeout=60).search(
        query, type="auto", category="news", num_results=settings.num_results, output_schema=EVENT_SCHEMA, **domains,
        start_published_date=datetime.combine(start, time.min, timezone.utc).isoformat(),
        end_published_date=datetime.combine(end + timedelta(days=1), time.min, timezone.utc).isoformat(),
    )
    return response.output


def _live_scan(ticker: str, start: date, end: date, api_key: str, benchmark: dict[date, float] | None,
               settings: SearchSettings) -> tuple[CompanyScan, bool]:
    """Search one company now; the flag says whether the result is complete enough to share."""
    # Ten extra calendar days give the first event in the window a previous close.
    history = _prices(ticker, start - timedelta(days=10), end)
    name = history.name if history else None
    try:
        events = parse_events(_search(ticker, name, start, end, api_key, settings), start=start, end=end)
    except Exception:
        # SDK errors can include response bodies; log them server-side and show a safe message.
        logging.getLogger(__name__).exception("Exa event search failed for %s", ticker)
        return CompanyScan(ticker=ticker, name=name, error="Exa search failed; check the API key, credits, or rate limits."), False
    if history:
        events = [
            event.model_copy(update={"reaction": price_reaction(event.reported_date, history.prices, benchmark)})
            for event in events
        ]
    # Without Yahoo data the events lack price moves, so don't share them for the whole day.
    return CompanyScan(ticker=ticker, name=name, events=events, scanned_at=_now()), history is not None


def scan_watchlist(tickers: Sequence[str], *, days: int = 30, today: date | None = None,
                   cache: ScanCache | None = None, settings: SearchSettings | None = None) -> WatchlistScan:
    """Scan each ticker independently so one failed company does not discard the others."""
    settings = settings or SearchSettings()
    # Different settings give different results, so each combination is cached separately.
    variant = settings.cache_variant()
    api_key = exa_api_key()
    if not api_key:
        raise MissingExaKey("Set EXA_API_KEY on the server before scanning.")
    end = today or date.today()
    start = end - timedelta(days=days)
    cached = {ticker: cache.read("events", ticker, days, CompanyScan, variant) if cache else None for ticker in tickers}
    companies = {ticker: scan for ticker, scan in cached.items() if scan and _fresh(scan.scanned_at, EVENTS_TTL)}
    pending = [ticker for ticker in tickers if ticker not in companies]
    if pending:
        # The benchmark is only needed for live scans; a fully cached watchlist skips Yahoo entirely.
        benchmark = _prices(BENCHMARK, start - timedelta(days=10), end)
        closes = benchmark.prices if benchmark else None
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(pending))) as pool:
            results = pool.map(lambda ticker: _live_scan(ticker, start, end, api_key, closes, settings), pending)
            for ticker, (scan, shareable) in zip(pending, results):
                if scan.error is None:
                    if shareable and cache:
                        cache.write("events", ticker, days, scan, variant)
                    companies[ticker] = scan
                elif cached[ticker]:
                    companies[ticker] = cached[ticker].model_copy(update={"stale": True})
                else:
                    companies[ticker] = scan
    return WatchlistScan(start_date=start, end_date=end, benchmark=BENCHMARK, settings=settings,
                         companies=[companies[ticker] for ticker in tickers])


def _series(ticker: str, start: date, end: date) -> PriceSeries:
    history = _prices(ticker, start, end)
    points = [PricePoint(date=day, close=round(close, 4)) for day, close in sorted(history.prices.items())] if history else []
    if len(points) < 2:
        return PriceSeries(ticker=ticker, error="Yahoo Finance returned no daily prices for this ticker.")
    return PriceSeries(
        ticker=ticker, name=history.name, currency=history.currency, prices=points, fetched_at=_now(),
        change_pct=round((points[-1].close / points[0].close - 1) * 100, 2),
    )


def price_history(tickers: Sequence[str], *, days: int = 30, today: date | None = None,
                  cache: ScanCache | None = None) -> WatchlistPrices:
    """Daily closes for the chart; Yahoo only, so it needs no Exa key and costs nothing."""
    end = today or date.today()
    start = end - timedelta(days=days)

    def one(ticker: str) -> PriceSeries:
        cached = cache.read("prices", ticker, days, PriceSeries) if cache else None
        if cached and _fresh(cached.fetched_at, PRICES_TTL):
            return cached
        live = _series(ticker, start, end)
        if live.error is None:
            if cache:
                cache.write("prices", ticker, days, live)
            return live
        return cached.model_copy(update={"stale": True}) if cached else live

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(tickers))) as pool:
        series = list(pool.map(one, tickers))
    return WatchlistPrices(start_date=start, end_date=end, series=series)
