"""Check stock questions without paid Exa or live Yahoo requests."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

exa_py = pytest.importorskip("exa_py")

from financial_research import ask, radar
from financial_research.prices import _DailyResponse

START, END, TODAY = date(2026, 10, 28), date(2026, 11, 27), date(2026, 11, 27)
QUESTION = "What did analysts say about the 737 ramp?"
ANSWER = {
    "answer": "Analysts said the ramp is slipping.",
    "key_points": [{"point": "Targets were cut.", "evidence": "Several banks cut targets on 29 Oct."},
                   {"point": " ", "evidence": "A point without text is dropped."}],
    "gaps": "Few notes after mid-November.",
}


@pytest.fixture
def services(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "test-only-exa-key")
    now = {"value": datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(radar, "_now", lambda: now["value"])
    monkeypatch.setattr(ask, "_now", lambda: now["value"])
    state = {"searches": [], "fail": False, "clock": now}

    class Client:
        def __init__(self, *, api_key, timeout):
            assert api_key == "test-only-exa-key"

        def search(self, query, **options):
            state["searches"].append((query, options))
            if state["fail"]:
                raise ValueError("HTTP 500 with a private response body")
            grounding = [SimpleNamespace(field="key_points[0].evidence", confidence="medium",
                                         citations=[SimpleNamespace(title="Note", url="https://news.example.com/targets")])]
            results = [SimpleNamespace(title="Targets cut", url="https://news.example.com/targets", published_date="2026-10-29")]
            return SimpleNamespace(output=SimpleNamespace(content=ANSWER, grounding=grounding), results=results)

    def fetch(request, symbol):
        return _DailyResponse(prices={request.start_date: 100.0, request.end_date: 104.0},
                              name="The Boeing Company", currency="USD")

    monkeypatch.setattr(exa_py, "Exa", Client)
    monkeypatch.setattr(radar, "_fetch", fetch)
    return state


def test_answer_schema_stays_within_exa_structured_output_limits():
    def count(schema, depth=0):
        properties = schema.get("properties", {})
        nested = [count(child.get("items", child), depth + 1) for child in properties.values()]
        return len(properties) + sum(total for total, _ in nested), max([depth, *(level for _, level in nested)])

    total, depth = count(ask.ANSWER_SCHEMA)
    assert total <= 10 and depth <= 2


def test_the_question_is_researched_only_within_the_chosen_period(services):
    settings = radar.SearchSettings(exclude_domains=["fool.com"])
    result = ask.ask_about_stock("BA", f"  {QUESTION} ", START, END, settings=settings, today=TODAY)
    query, options = services["searches"][0]
    assert query == f"{QUESTION} About The Boeing Company (BA), between 28 October 2026 and 27 November 2026."
    assert options["type"] == "deep" and options["category"] == "news" and options["exclude_domains"] == ["fool.com"]
    assert options["start_published_date"].startswith("2026-10-28") and options["end_published_date"].startswith("2026-11-28")
    assert result.question == QUESTION and result.answer.text == ANSWER["answer"]
    assert [point.point for point in result.key_points] == ["Targets were cut."]
    assert result.key_points[0].confidence == "medium" and result.gaps.text == ANSWER["gaps"]
    assert result.sources[0].url == "https://news.example.com/targets"
    # The chart shows a week either side of the period, but never past today.
    assert result.prices[0].date == START - ask.CHART_PADDING and result.prices[-1].date == TODAY


def test_a_recent_answer_is_reused_and_a_failure_falls_back_to_it(services, tmp_path):
    cache = radar.ScanCache(tmp_path)
    first = ask.ask_about_stock("BA", QUESTION, START, END, today=TODAY, cache=cache)
    services["clock"]["value"] += timedelta(hours=23)
    assert ask.ask_about_stock("BA", QUESTION.upper(), START, END, today=TODAY, cache=cache) == first
    assert len(services["searches"]) == 1
    # A different period is a different question.
    ask.ask_about_stock("BA", QUESTION, START + timedelta(days=1), END, today=TODAY, cache=cache)
    assert len(services["searches"]) == 2
    services["clock"]["value"] += timedelta(hours=2)
    services["fail"] = True
    fallback = ask.ask_about_stock("BA", QUESTION, START, END, today=TODAY, cache=cache)
    assert fallback.stale and fallback.answer == first.answer


def test_a_failure_without_an_earlier_answer_is_a_safe_error(services):
    services["fail"] = True
    with pytest.raises(ask.AskError) as error:
        ask.ask_about_stock("BA", QUESTION, START, END, today=TODAY)
    assert "500" not in str(error.value)
