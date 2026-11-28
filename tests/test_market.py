"""Check the shared market news scan without paid Exa or live Yahoo requests."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

exa_py = pytest.importorskip("exa_py")

from financial_research import market, radar
from financial_research.prices import _DailyResponse

TODAY = date(2026, 11, 27)
ROWS = [
    {"reported_date": "2026-11-10", "category": "economic_data", "headline": "CPI rose 0.4%.", "why_it_matters": "Rates."},
    {"reported_date": "2026-11-25", "category": "tariff", "headline": "New tariffs proposed.", "why_it_matters": "Trade."},
    {"reported_date": "2026-09-01", "category": "markets", "headline": "Too old.", "why_it_matters": ""},
    {"reported_date": "2026-11-12", "category": "geopolitics", "headline": "", "why_it_matters": "No headline."},
]


@pytest.fixture
def services(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "test-only-exa-key")
    now = {"value": datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(radar, "_now", lambda: now["value"])
    monkeypatch.setattr(market, "_now", lambda: now["value"])
    state = {"searches": [], "fail": False, "clock": now}

    class Client:
        def __init__(self, *, api_key, timeout):
            assert api_key == "test-only-exa-key"

        def search(self, query, **options):
            state["searches"].append((query, options))
            if state["fail"]:
                raise ValueError("HTTP 500 with a private response body")
            grounding = [SimpleNamespace(field="events[0].headline", confidence="high",
                                         citations=[SimpleNamespace(title="CPI", url="https://news.example.com/cpi")])]
            return SimpleNamespace(output=SimpleNamespace(content={"events": ROWS}, grounding=grounding))

    def fetch(request, symbol):
        assert symbol == "SPY"
        days = [request.start_date + timedelta(days=offset) for offset in range((request.end_date - request.start_date).days + 1)]
        return _DailyResponse(prices={day: 500.0 + index for index, day in enumerate(days)})

    monkeypatch.setattr(exa_py, "Exa", Client)
    monkeypatch.setattr(radar, "_fetch", fetch)
    return state


def test_market_schema_stays_within_exa_structured_output_limits():
    def count(schema, depth=0):
        properties = schema.get("properties", {})
        nested = [count(child.get("items", child), depth + 1) for child in properties.values()]
        return len(properties) + sum(total for total, _ in nested), max([depth, *(level for _, level in nested)])

    total, depth = count(market.MARKET_SCHEMA)
    assert total <= 10 and depth <= 2


def test_one_market_scan_keeps_valid_events_with_the_sp500_move(services):
    scan = market.scan_market(days=30, today=TODAY)
    query, options = services["searches"][0]
    assert query == market.QUERY and options["category"] == "news" and options["num_results"] == 25
    assert options["start_published_date"].startswith("2026-10-28")
    assert [event.headline for event in scan.events] == ["New tariffs proposed.", "CPI rose 0.4%."]
    tariffs, cpi = scan.events
    assert tariffs.category == "other" and cpi.sources[0].url == "https://news.example.com/cpi"
    assert cpi.market_move.from_date == date(2026, 11, 9) and cpi.market_move.to_date == date(2026, 11, 11)
    # The benchmark line only covers the chart's window, though older closes feed the first event's move.
    assert scan.benchmark[0].date == date(2026, 10, 28) and scan.benchmark[-1].date == TODAY


def test_the_market_scan_is_shared_and_falls_back_when_exa_fails(services, tmp_path):
    cache = radar.ScanCache(tmp_path)
    first = market.scan_market(days=30, today=TODAY, cache=cache)
    services["clock"]["value"] += timedelta(hours=11)
    assert market.scan_market(days=30, today=TODAY, cache=cache) == first
    assert len(services["searches"]) == 1
    services["clock"]["value"] += timedelta(hours=2)
    services["fail"] = True
    fallback = market.scan_market(days=30, today=TODAY, cache=cache)
    assert fallback.stale and fallback.events == first.events
    with pytest.raises(market.MarketError) as error:
        market.scan_market(days=7, today=TODAY, cache=cache)
    assert "500" not in str(error.value)
