"""Check event explanations without paid Exa or live Yahoo requests."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

exa_py = pytest.importorskip("exa_py")

from financial_research import explain, radar
from financial_research.prices import _DailyResponse

REPORTED, TODAY = date(2026, 11, 23), date(2026, 11, 27)
HEADLINE = "NVIDIA reinstated Jensen Huang as CEO."


def entry(field, url, confidence="high"):
    return SimpleNamespace(field=field, confidence=confidence, citations=[SimpleNamespace(title=url, url=url)])


ANSWER = {
    "summary": "The stock jumped after the CEO returned.",
    "drivers": [
        {"point": "The CEO returned.", "evidence": "The board voted unanimously."},
        {"point": "", "evidence": "A driver without a point is dropped."},
        {"point": "The sector rallied.", "evidence": "Chip stocks rose 14%."},
    ],
    "market_context": "Market-wide rally.",
    "upcoming": "",
}
GROUNDING = [
    entry("summary", "https://investor.example.com/release"),
    entry("drivers[0].point", "https://investor.example.com/release"),
    entry("drivers[0].evidence", "https://news.example.com/story", "medium"),
    entry("drivers[2].point", "https://news.example.com/chips"),
    entry("market_context", "https://news.example.com/markets"),
]


@pytest.fixture
def services(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "test-only-exa-key")
    now = {"value": datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(radar, "_now", lambda: now["value"])
    monkeypatch.setattr(explain, "_now", lambda: now["value"])
    state = {"searches": [], "fail": False}

    class Client:
        def __init__(self, *, api_key, timeout):
            assert api_key == "test-only-exa-key"

        def search(self, query, **options):
            state["searches"].append((query, options))
            if state["fail"]:
                raise ValueError("HTTP 500 with a private response body")
            results = [SimpleNamespace(title="Board reinstates CEO", url="https://investor.example.com/release",
                                       published_date="2026-11-23T12:00:00Z"),
                       SimpleNamespace(title="", url="https://untitled.example.com", published_date=None)]
            return SimpleNamespace(output=SimpleNamespace(content=ANSWER, grounding=GROUNDING), results=results)

    def fetch(request, symbol):
        days = [request.start_date + timedelta(days=offset) for offset in range((request.end_date - request.start_date).days + 1)]
        return _DailyResponse(prices={day: 100.0 + index for index, day in enumerate(days)},
                              name="NVIDIA Corporation" if symbol == "NVDA" else None, currency="USD")

    monkeypatch.setattr(exa_py, "Exa", Client)
    monkeypatch.setattr(radar, "_fetch", fetch)
    state["clock"] = now
    return state


def test_explanation_schema_stays_within_exa_structured_output_limits():
    def count(schema, depth=0):
        properties = schema.get("properties", {})
        nested = [count(child.get("items", child), depth + 1) for child in properties.values()]
        return len(properties) + sum(total for total, _ in nested), max([depth, *(level for _, level in nested)])

    total, depth = count(explain.EXPLANATION_SCHEMA)
    assert total <= 10 and depth <= 2


def test_deep_search_asks_about_the_event_in_its_news_window_with_the_brokers_settings(services):
    settings = radar.SearchSettings(focus="governance", include_domains=["reuters.com"], num_results=25)
    explain.explain_event("NVDA", REPORTED, HEADLINE, settings=settings, today=TODAY)
    query, options = services["searches"][-1]
    assert query.startswith("Why did NVIDIA Corporation (NVDA) stock move around 23 November 2026?")
    assert HEADLINE in query and query.endswith("Focus on: governance")
    assert options["type"] == "deep" and options["category"] == "news" and options["num_results"] == 25
    assert options["output_schema"] == explain.EXPLANATION_SCHEMA and options["include_domains"] == ["reuters.com"]
    assert "exclude_domains" not in options
    # Three days before the news; four after it would pass today, so the window stops at today.
    assert options["start_published_date"].startswith("2026-11-20")
    assert options["end_published_date"].startswith("2026-11-28")


def test_each_finding_keeps_its_own_sources_and_weakest_confidence(services):
    result = explain.explain_event("NVDA", REPORTED, HEADLINE, today=TODAY)
    assert result.summary.sources[0].url == "https://investor.example.com/release"
    first, second = result.drivers
    assert first.confidence == "medium"
    assert [source.url for source in first.sources] == ["https://investor.example.com/release", "https://news.example.com/story"]
    assert second.point == "The sector rallied." and second.sources[0].url == "https://news.example.com/chips"
    assert result.market_context.text == "Market-wide rally." and result.upcoming is None
    assert [source.title for source in result.sources] == ["Board reinstates CEO"]


def test_prices_and_the_move_come_with_the_explanation(services):
    result = explain.explain_event("NVDA", REPORTED, HEADLINE, today=TODAY)
    assert result.name == "NVIDIA Corporation" and result.currency == "USD"
    assert result.prices[0].date == REPORTED - explain.CHART_BEFORE and result.prices[-1].date == TODAY
    assert (result.reaction.from_date, result.reaction.to_date) == (date(2026, 11, 22), date(2026, 11, 24))


def test_a_recent_explanation_is_reused_and_a_failure_falls_back_to_it(services, tmp_path):
    cache = radar.ScanCache(tmp_path)
    first = explain.explain_event("NVDA", REPORTED, HEADLINE, today=TODAY, cache=cache)
    services["clock"]["value"] += timedelta(hours=23)
    assert explain.explain_event("NVDA", REPORTED, HEADLINE, today=TODAY, cache=cache) == first
    assert len(services["searches"]) == 1
    # Different settings are researched separately.
    explain.explain_event("NVDA", REPORTED, HEADLINE, today=TODAY, cache=cache,
                          settings=radar.SearchSettings(focus="governance"))
    assert len(services["searches"]) == 2
    services["clock"]["value"] += timedelta(hours=2)
    services["fail"] = True
    fallback = explain.explain_event("NVDA", REPORTED, HEADLINE, today=TODAY, cache=cache)
    assert fallback.stale and fallback.summary == first.summary


def test_a_failure_without_an_earlier_explanation_is_a_safe_error(services):
    services["fail"] = True
    with pytest.raises(explain.ExplainError) as error:
        explain.explain_event("NVDA", REPORTED, HEADLINE, today=TODAY)
    assert "500" not in str(error.value)


def test_research_requires_an_exa_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    with pytest.raises(radar.MissingExaKey):
        explain.explain_event("NVDA", REPORTED, HEADLINE, today=TODAY)
