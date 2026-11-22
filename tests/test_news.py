"""Exercise Exa requests, source provenance, and graph integration without paid API calls."""

import json
from datetime import date
from types import SimpleNamespace

import pytest
from requests.exceptions import Timeout

exa_py = pytest.importorskip("exa_py")

from financial_research import news
from financial_research.models import (
    FinishAction, HistoricalPriceRequest, NewsAction, ResearchPlan, WebSearchRequest,
)
from financial_research.research_model import DemoModel, instructions, tool_definitions
from financial_research.runner import run_saved_research
from financial_research.state import ResearchState


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EXA_API_KEY", "test-only-exa-key")


def request():
    return WebSearchRequest(
        query="Apple company announcements in 2025",
        objective="Find company announcements that provide context for Apple's price changes.",
        rationale="Collect source material to examine possible explanations.",
    )


def article(**changes):
    return SimpleNamespace(**{
        "title": "Apple reports quarterly results", "url": "https://example.com/apple-results",
        "published_date": "2025-05-01T09:00:00Z", "highlights": ["Revenue increased year over year."],
        **changes,
    })


def server(monkeypatch, items=None, error=None):
    calls = []

    class Client:
        def __init__(self, *, api_key, timeout):
            assert timeout == 20
            self.api_key = api_key

        def search(self, query, **options):
            calls.append((self.api_key, query, options))
            if error:
                raise error
            return SimpleNamespace(results=items if items is not None else [article()])

    monkeypatch.setattr(exa_py, "Exa", Client)
    return calls


def test_sdk_receives_query_objective_and_highlights_without_extra_filters(monkeypatch):
    calls = server(monkeypatch)
    result = news.search_news(request())
    assert calls == [("test-only-exa-key", request().query, {
        "objective": request().objective, "contents": {"highlights": True},
    })]
    item = result.observations[0]
    assert result.error is None and item.is_fixture is False
    assert item.source_uri == "https://example.com/apple-results"
    assert item.period_start == item.period_end == date(2025, 5, 1)
    assert item.data["highlights"] == ["Revenue increased year over year."]
    assert item.data["objective"] == request().objective
    assert "not exhaustive" in item.data["coverage_note"]
    assert "test-only-exa-key" not in result.model_dump_json()


def test_fixed_dates_include_the_whole_end_date_and_exclude_outside_results(monkeypatch):
    calls = server(monkeypatch, items=[
        article(published_date="2025-05-01T23:59:59Z"),
        article(url="https://example.com/outside", published_date="2025-05-02T00:00:00Z"),
    ])
    result = news.search_news(request(), start_date=date(2025, 4, 1), end_date=date(2025, 5, 1))
    assert calls[0][2]["start_published_date"] == "2025-04-01T00:00:00+00:00"
    assert calls[0][2]["end_published_date"] == "2025-05-02T00:00:00+00:00"
    assert len(result.observations) == 1


@pytest.mark.parametrize("changes", [
    {"published_date": None}, {"published_date": "invalid"}, {"highlights": []},
    {"highlights": ["  "]}, {"url": "javascript:alert(1)"}, {"title": None},
])
def test_unusable_sources_never_become_evidence(monkeypatch, changes):
    server(monkeypatch, items=[article(**changes)])
    result = news.search_news(request())
    assert result.observations == [] and "no usable dated excerpts" in result.error


def test_duplicate_urls_are_not_counted_as_independent_sources(monkeypatch):
    server(monkeypatch, items=[article(), article(highlights=["Another excerpt from the same article."])])
    assert len(news.search_news(request()).observations) == 1


def test_empty_search_is_not_fabricated_evidence(monkeypatch):
    server(monkeypatch, items=[])
    result = news.search_news(request())
    assert result.error is None and result.observations == []


@pytest.mark.parametrize("error", [Timeout("secret test-only-exa-key"), ValueError("401 response with secret")])
def test_provider_failures_do_not_leak_response_bodies_or_credentials(monkeypatch, error):
    server(monkeypatch, error=error)
    result = news.search_news(request())
    assert result.observations == [] and result.error
    assert "secret" not in result.model_dump_json() and "test-only-exa-key" not in result.model_dump_json()


def test_missing_key_makes_no_api_call(monkeypatch):
    calls = server(monkeypatch)
    monkeypatch.delenv("EXA_API_KEY")
    assert "EXA_API_KEY" in news.search_news(request()).error
    assert calls == []


def test_local_dotenv_is_used_without_overriding_environment_credentials(monkeypatch, tmp_path):
    calls = server(monkeypatch)
    (tmp_path / ".env").write_text("EXA_API_KEY=local-file-key\n")
    news.search_news(request())
    assert calls[-1][0] == "test-only-exa-key"
    monkeypatch.delenv("EXA_API_KEY")
    news.search_news(request())
    assert calls[-1][0] == "local-file-key"


def test_graph_assigns_news_evidence_ids_and_saves_cited_report(monkeypatch, tmp_path):
    highlight = "Revenue increased year over year. " * 100
    calls = server(monkeypatch, items=[article(highlights=[highlight])])

    class NewsModel(DemoModel):
        def make_plan(self, state):
            return ResearchPlan(objectives=["Find source material."], assumptions=[])

        def choose_action(self, state):
            if not state.tool_calls:
                return NewsAction(**request().model_dump())
            return FinishAction(rationale="Collected relevant source material.")

    scope = HistoricalPriceRequest(symbols=["AAPL"], start_date="2025-01-01", end_date="2025-12-31", rationale="User scope.")
    state = run_saved_research("Find Apple announcements in 2025.", NewsModel(),
                               price_request=scope, mode="ollama", output=tmp_path / "run")
    assert len(calls) == 1 and state.evidence[0].id == "E001"
    assert state.report.claims[0].evidence_ids == ["E001"]
    assert state.evidence[0].is_fixture is False
    assert "Exa" in (tmp_path / "run/report.md").read_text()
    saved = json.loads((tmp_path / "run/state.json").read_text())
    assert saved["tool_calls"][0]["action"]["objective"] == request().objective
    context = json.loads(state.model_context())
    source = context["evidence"][0]
    assert source["id"] == "E001" and source["source_uri"] == state.evidence[0].source_uri
    assert "Revenue increased" in source["content"] and len(source["content"]) < len(highlight)
    assert "highlights" not in source["data"]
    assert context["tool_calls"][0]["result"]["observations"] == [{"evidence_id": "E001"}]
    assert saved["evidence"][0]["data"]["highlights"] == state.evidence[0].data["highlights"] == [highlight.strip()]
    schemas = {name: schema for name, schema, _ in tool_definitions(ResearchState(question="News?", price_request=scope))}
    assert "objective" in schemas["search_news"].model_json_schema()["required"]
    assert "Call search_news before answering" in instructions(state)


def test_standalone_cli_searches_without_tickers(monkeypatch, capsys):
    server(monkeypatch)
    assert news.main(["Apple announcements", "--objective", "Find the original announcements."]) == 0
    assert json.loads(capsys.readouterr().out)["observations"][0]["source_type"] == "news"
