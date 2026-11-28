"""Check company discovery without paid Exa or live Yahoo requests."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

exa_py = pytest.importorskip("exa_py")

from financial_research import discover, radar
from financial_research.prices import _DailyResponse, _PriceError

TODAY = date(2026, 11, 27)
ROWS = [
    {"name": "Texas Instruments", "ticker": "txn", "exchange": "NASDAQ", "description": "Analog chips."},
    {"name": "Texas Instruments again", "ticker": "TXN", "exchange": "NASDAQ", "description": "Duplicate."},
    {"name": "Delisted Co", "ticker": "GONE", "exchange": "NASDAQ", "description": "No longer trades."},
    {"name": "", "ticker": "NONAME", "exchange": "NYSE", "description": "Missing a name."},
    {"name": "Bad ticker", "ticker": "NOT A TICKER", "exchange": "NYSE", "description": "Unusable."},
    {"name": "Nanya", "ticker": "$2408.TW", "exchange": "Taiwan Stock Exchange", "description": "Memory chips."},
    {"name": "Agility Robotics", "ticker": "AGLT", "exchange": "NASDAQ", "description": "Humanoid robots."},
    {"name": "Humanoid Global Holdings", "ticker": "ROBO", "exchange": "NYSE Arca", "description": "Robots."},
]


@pytest.fixture
def services(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "test-only-exa-key")
    now = {"value": datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(radar, "_now", lambda: now["value"])
    monkeypatch.setattr(discover, "_now", lambda: now["value"])
    state = {"searches": [], "fail": False, "clock": now}

    class Client:
        def __init__(self, *, api_key, timeout):
            assert api_key == "test-only-exa-key"

        def search(self, query, **options):
            state["searches"].append((query, options))
            if state["fail"]:
                raise ValueError("HTTP 500 with a private response body")
            grounding = [SimpleNamespace(field="companies[0].ticker", confidence="high",
                                         citations=[SimpleNamespace(title="TI", url="https://www.ti.com/about")])]
            return SimpleNamespace(output=SimpleNamespace(content={"companies": ROWS}, grounding=grounding))

    def fetch(request, symbol):
        if symbol == "GONE":
            raise _PriceError("No prices.")
        names = {"TXN": "Texas Instruments Incorporated", "AGLT": "Agile Therapeutics, Inc.",
                 "ROBO": "Robo Global Robotics and Automation Index ETF"}
        return _DailyResponse(prices={TODAY - timedelta(days=1): 100.0, TODAY: 101.5}, name=names.get(symbol),
                              instrument_type="ETF" if symbol == "ROBO" else "EQUITY",
                              currency="TWD" if symbol.endswith(".TW") else "USD")

    monkeypatch.setattr(exa_py, "Exa", Client)
    monkeypatch.setattr(radar, "_fetch", fetch)
    return state


def test_discovery_schema_stays_within_exa_structured_output_limits():
    def count(schema, depth=0):
        properties = schema.get("properties", {})
        nested = [count(child.get("items", child), depth + 1) for child in properties.values()]
        return len(properties) + sum(total for total, _ in nested), max([depth, *(level for _, level in nested)])

    total, depth = count(discover.DISCOVER_SCHEMA)
    assert total <= 10 and depth <= 2


def test_company_search_uses_the_company_index_and_keeps_only_tradable_companies(services):
    result = discover.discover_companies("  chip   makers ", today=TODAY)
    query, options = services["searches"][0]
    assert query == "chip makers" and options["category"] == "company" and options["num_results"] == 25
    assert options["output_schema"] == discover.DISCOVER_SCHEMA and "start_published_date" not in options
    assert [company.ticker for company in result.companies] == ["TXN", "2408.TW"]
    # A ticker that trades under a different company is left out, with the reason.
    assert result.dropped == ["GONE (Yahoo has no prices)", "AGLT (Yahoo lists it as Agile Therapeutics, Inc.)",
                              "ROBO (Yahoo lists it as Robo Global Robotics and Automation Index ETF, not a company)"]
    texas, nanya = result.companies
    assert texas.sources[0].url == "https://www.ti.com/about" and texas.confidence == "high"
    assert (texas.last_close, texas.currency, nanya.currency) == (101.5, "USD", "TWD")


def test_a_recent_discovery_is_reused_and_a_failure_falls_back_to_it(services, tmp_path):
    cache = radar.ScanCache(tmp_path)
    first = discover.discover_companies("chip makers", today=TODAY, cache=cache)
    services["clock"]["value"] += timedelta(hours=23)
    # Case and spacing don't matter for reuse.
    assert discover.discover_companies("Chip  Makers", today=TODAY, cache=cache) == first
    assert len(services["searches"]) == 1
    services["clock"]["value"] += timedelta(hours=2)
    services["fail"] = True
    fallback = discover.discover_companies("chip makers", today=TODAY, cache=cache)
    assert fallback.stale and fallback.companies == first.companies


def test_a_failure_without_an_earlier_result_is_a_safe_error(services):
    services["fail"] = True
    with pytest.raises(discover.DiscoverError) as error:
        discover.discover_companies("chip makers", today=TODAY)
    assert "500" not in str(error.value)


def test_discovery_requires_an_exa_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    with pytest.raises(radar.MissingExaKey):
        discover.discover_companies("chip makers", today=TODAY)
