"""Test local-provider decisions and failures without a running model server."""

import io
import json
from urllib.error import HTTPError, URLError

import pandas as pd
import pytest

from financial_research import macro, ollama_model, prices
from financial_research.cli import main
from financial_research.ollama_model import OllamaModel
from financial_research.research_model import ModelError
from financial_research.state import ResearchState
from financial_research.workflow import run_research


def _text(data):
    return {"content": json.dumps(data)}


def _call(name, **arguments):
    return {"tool_calls": [{"function": {"name": name, "arguments": arguments}}]}


def _server(monkeypatch, messages):
    requests = []
    remaining = iter(messages)

    def respond(request, timeout):
        assert request.full_url == "http://127.0.0.1:11434/api/chat"
        assert timeout == 120
        requests.append(json.loads(request.data))
        return io.BytesIO(json.dumps({
            "message": next(remaining), "done": True, "done_reason": "stop",
        }).encode())

    monkeypatch.setattr(ollama_model, "urlopen", respond)
    return requests


def test_native_local_tool_calls_drive_the_existing_graph(monkeypatch):
    requests = _server(monkeypatch, [
        _text({"objectives": ["Check the fixture prices."], "assumptions": ["Synthetic data only."]}),
        _call("get_market_prices", symbols=["XAGUSD"], rationale="Verify silver returns."),
        _call("finish_research", rationale="Enough for a limited report."),
        _text({
            "title": "Fixture report",
            "claims": [{"statement": "Synthetic silver returned 20%.", "kind": "observation", "evidence_ids": ["E001"]}],
            "limitations": ["Synthetic data only."],
        }),
        _text({"issues": []}),
    ])
    state = run_research("Investigate fixture silver returns.", OllamaModel(), review=True)
    assert len(state.tool_calls) == 1
    assert len(state.evidence) == 1
    assert state.stop_reason.startswith("model_finished:")
    assert state.report.claims[0].evidence_ids == ["E001"]
    assert requests[0]["format"]["required"] == ["objectives", "assumptions"]
    assert {tool["function"]["name"] for tool in requests[1]["tools"]} == {
        "get_market_prices", "get_macro_series", "search_news", "finish_research",
    }
    assert json.loads(requests[2]["messages"][1]["content"])["evidence"][0]["id"] == "E001"


@pytest.mark.parametrize("message", [
    {"content": "I would get prices."},
    {"tool_calls": [
        {"function": {"name": "get_market_prices", "arguments": {}}},
        {"function": {"name": "search_news", "arguments": {}}},
    ]},
    _call("run_python", rationale="Unexpected tool."),
    _call("get_market_prices", symbols=[], rationale="Invalid request."),
])
def test_invalid_tool_decisions_fail_before_execution(monkeypatch, message):
    _server(monkeypatch, [message])
    with pytest.raises((ModelError, ValueError)):
        OllamaModel().choose_action(ResearchState(question="Test question."))


@pytest.mark.parametrize("error, message", [
    (URLError("Connection refused"), "Cannot reach Ollama"),
    (TimeoutError(), "timed out"),
    (HTTPError(ollama_model.URL, 404, "Not found", {}, None), "ollama list"),
])
def test_server_failures_are_explicit(monkeypatch, error, message):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(ollama_model, "urlopen", fail)
    with pytest.raises(ModelError, match=message):
        OllamaModel().make_plan(ResearchState(question="Test question."))


def test_truncated_responses_are_rejected(monkeypatch):
    monkeypatch.setattr(ollama_model, "urlopen", lambda *args, **kwargs: io.BytesIO(json.dumps({
        "message": {"content": "{}"}, "done": True, "done_reason": "length",
    }).encode()))
    with pytest.raises(ModelError, match="did not complete normally"):
        OllamaModel().make_plan(ResearchState(question="Test question."))


@pytest.mark.parametrize("environment_model, arguments, expected", [
    (None, [], "qwen2.5:7b"),
    ("qwen2.5:3b", [], "qwen2.5:3b"),
    ("qwen2.5:3b", ["--model", "qwen2.5:14b"], "qwen2.5:14b"),
])
def test_cli_uses_local_model_without_openai_credentials(monkeypatch, tmp_path, environment_model, arguments, expected):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "must-not-be-used-for-ollama")
    if environment_model:
        monkeypatch.setenv("OLLAMA_MODEL", environment_model)
    else:
        monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    requests = _server(monkeypatch, [
        _text({"objectives": ["Check availability."], "assumptions": ["Use real data only."]}),
        _call("finish_research", rationale="No evidence needed for this test."),
        _text({"title": "Empty report", "claims": [], "limitations": ["No evidence collected."]}),
    ])
    assert main(["Compare IBM prices.", "--symbols", "IBM", "--start-date", "2025-01-06",
                 "--end-date", "2025-01-07", "--output", str(tmp_path), *arguments]) == 0
    assert all(request["model"] == expected for request in requests)
    assert len(requests) == 3  # Plan, finish decision, and report; review is disabled by default.
    saved = ResearchState.model_validate_json(next(tmp_path.glob("*/state.json")).read_text())
    assert saved.draft_report is None and saved.critique is None
    assert len(list(tmp_path.glob("*/report.md"))) == 1


@pytest.mark.parametrize("mode_arguments", [[], ["--mode", "ollama"]])
@pytest.mark.parametrize("review", [False, True])
def test_real_prices_and_macro_flow_from_cli_through_model_tool_state_and_report(monkeypatch, tmp_path, mode_arguments, review):
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("FRED_API_KEY", "test-only-fred-key")
    messages = [
        _text({"objectives": ["Compare actual prices and real yields."], "assumptions": ["Endpoint comparisons cannot establish causation."]}),
        _call("get_market_prices", symbols=["ibm"], start_date="2025-01-06", end_date="2025-01-07",
              rationale="Retrieve the scoped price evidence."),
        _call("get_macro_series", series_id="dfii10", rationale="Inspect real yields over the same date range."),
        _call("finish_research", rationale="Prices and real yields have been retrieved."),
        _text({
            "title": "IBM prices and real yields",
            "claims": [
                {"statement": "The close increased by 10%.", "kind": "observation", "evidence_ids": ["E001"]},
                {"statement": "Real yields fell by 0.2 percentage points.", "kind": "observation", "evidence_ids": ["E002"]},
            ],
            "limitations": ["Endpoint comparisons cannot establish the cause of a market move."],
        }),
    ]
    if review:
        messages.append(_text({"issues": []}))
    requests = _server(monkeypatch, messages)
    fetched = []

    class Ticker:
        def __init__(self, symbol):
            assert symbol == "IBM"

        def history(self, **kwargs):
            fetched.append(kwargs)
            return pd.DataFrame({"Close": [100, 110]}, index=pd.to_datetime(["2025-01-06", "2025-01-07"]))

        def get_history_metadata(self):
            return {"symbol": "IBM", "currency": "USD", "exchangeTimezoneName": "America/New_York"}

    monkeypatch.setattr(prices.yf, "Ticker", Ticker)
    macro_calls = []

    def fred_response(request, timeout):
        from urllib.parse import parse_qs, urlparse

        url = urlparse(request.full_url)
        macro_calls.append(parse_qs(url.query))
        payload = {"seriess": [{
            "id": "DFII10", "title": "10-Year Real Yield", "units": "Percent", "frequency": "Daily",
            "seasonal_adjustment": "Not Seasonally Adjusted", "last_updated": "2025-01-08",
        }]} if url.path.endswith("/series") else {
            "count": 2, "offset": 0, "realtime_start": "2025-01-08", "realtime_end": "2025-01-08",
            "observations": [{"date": "2025-01-06", "value": "2.2"}, {"date": "2025-01-07", "value": "2.0"}],
        }
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(macro, "urlopen", fred_response)
    assert main([
        "Compare IBM closing prices.", *mode_arguments,
        "--symbols", "IBM", "--start-date", "2025-01-06", "--end-date", "2025-01-07",
        "--output", str(tmp_path), *(["--review"] if review else []),
    ]) == 0
    saved = ResearchState.model_validate_json(next(tmp_path.glob("*/state.json")).read_text())
    report = next(tmp_path.glob("*/report.md")).read_text()
    assert len(fetched) == 1
    assert saved.price_request.symbols == ["IBM"]
    assert saved.evidence[0].is_fixture is False
    assert saved.evidence[0].data["price_change_pct"] == 10
    assert len(saved.evidence[0].data["prices"]) == 2
    assert "REAL DATA MODE: Yahoo Finance" in report and "FRED" in report and "SYNTHETIC FIXTURE" not in report
    assert "2025-01-06 to 2025-01-07" in requests[0]["messages"][0]["content"]
    assert {tool["function"]["name"] for tool in requests[1]["tools"]} == {"get_market_prices", "get_macro_series", "analyze_prices", "finish_research"}
    assert len(saved.evidence) == 2 and all(not item.is_fixture for item in saved.evidence)
    assert not list(tmp_path.glob("*/quant.*"))
    diagnostics = json.loads(next(tmp_path.glob("*/run.json")).read_text())
    assert all(step["stage"] != "quant" for step in diagnostics["steps"])
    assert saved.evidence[1].data["change"] == -0.2
    assert len(saved.evidence[1].data["values"]) == 2
    assert macro_calls[1]["observation_start"] == ["2025-01-06"]
    assert macro_calls[1]["observation_end"] == ["2025-01-07"]
    assert macro_calls[1]["series_id"] == ["DFII10"]
    assert "test-only-fred-key" not in saved.model_dump_json() and "test-only-fred-key" not in report
    context = json.loads(requests[3]["messages"][1]["content"])
    assert "prices" not in context["evidence"][0]["data"]
    assert "prices" not in context["tool_calls"][0]["result"]["observations"][0]["data"]
    assert context["evidence"][0]["data"]["price_change_pct"] == 10
    assert "values" not in context["evidence"][1]["data"]
    assert "values" not in context["tool_calls"][1]["result"]["observations"][0]["data"]
    assert context["evidence"][1]["data"]["change"] == -0.2
    assert len(requests) == (6 if review else 5)
    if review:
        review_context = json.loads(requests[-1]["messages"][1]["content"])
        assert review_context["draft_report"] == saved.draft_report.model_dump(mode="json")
        assert "prices" not in review_context["evidence"][0]["data"]
        assert "values" not in review_context["tool_calls"][1]["result"]["observations"][0]["data"]
        assert "tools" not in requests[-1]
        assert saved.critique.issues == []
    else:
        assert saved.draft_report is None and saved.critique is None


@pytest.mark.parametrize("review", [False, True])
def test_model_selected_quant_tool_supplies_citable_statistics_before_reporting(monkeypatch, tmp_path, review):
    messages = [
        _text({"objectives": ["Calculate return volatility and correlation."], "assumptions": []}),
        _call("get_market_prices", symbols=["AAPL", "MSFT"], start_date="2025-01-06", end_date="2025-01-09",
              rationale="Fetch daily histories for the comparison."),
        _call("analyze_prices", evidence_ids=["E001", "E002"], window=3,
              rationale="Calculate statistics from the collected prices."),
        _call("finish_research", rationale="Computed statistics answer the question."),
        _text({
            "title": "Price-return comparison",
            "claims": [{"statement": "The observed returns have correlation 1.",
                        "kind": "observation", "evidence_ids": ["E003"]}],
            "limitations": ["Three observed returns do not establish causation or predict future returns."],
        }),
    ]
    if review:
        messages.append(_text({"issues": []}))
    requests = _server(monkeypatch, messages)

    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, **kwargs):
            values = [100, 110, 99, 99] if self.symbol == "AAPL" else [100, 120, 96, 96]
            return pd.DataFrame({"Close": values}, index=pd.date_range("2025-01-06", periods=4))

        def get_history_metadata(self):
            return {"currency": "USD", "exchangeTimezoneName": "America/New_York"}

    monkeypatch.setattr(prices.yf, "Ticker", Ticker)
    assert main([
        "Compare AAPL and MSFT return volatility and correlation.", "--symbols", "AAPL", "MSFT",
        "--start-date", "2025-01-06", "--end-date", "2025-01-09", "--output", str(tmp_path),
        *(["--review"] if review else []),
    ]) == 0
    state = ResearchState.model_validate_json(next(tmp_path.glob("*/state.json")).read_text())
    assert [call.action.tool for call in state.tool_calls] == ["get_market_prices", "analyze_prices"]
    assert state.evidence[-1].id == "E003" and state.evidence[-1].source_type == "quant"
    assert state.evidence[-1].is_fixture is False
    assert state.evidence[-1].data["pairs"][0]["correlation"] == pytest.approx(1)
    assert len(state.evidence[-1].data["assets"][0]["returns"]) == 3
    assert len(state.evidence[-1].data["pairs"][0]["rolling_correlations"]) == 1
    assert state.report.claims[0].evidence_ids == ["E003"]
    assert "[E003]" in next(tmp_path.glob("*/report.md")).read_text()
    for request in requests[3:]:  # Next decision, writer, and optional reviewer all see computed summaries.
        context = json.loads(request["messages"][1]["content"])
        summaries = [context["evidence"][-1], context["tool_calls"][-1]["result"]["observations"][0]]
        for item in summaries:
            assert item["data"]["input_evidence_ids"] == ["E001", "E002"]
            assert item["data"]["assets"][0]["annualized_volatility_pct"] > 0
            assert "returns" not in item["data"]["assets"][0]
            assert "rolling_correlations" not in item["data"]["pairs"][0]
            assert item["data"]["pairs"][0]["latest_rolling_correlation"]["correlation"] == pytest.approx(1)
    diagnostics = json.loads(next(tmp_path.glob("*/run.json")).read_text())
    assert diagnostics["tool_calls"] == 2
    assert sum(step["stage"] == "execute" for step in diagnostics["steps"]) == 2
