"""Test local-provider decisions and failures without a running model server."""

import io
import json
from urllib.error import HTTPError, URLError

import pytest

from financial_research import ollama_model
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
    ])
    state = run_research("Investigate fixture silver returns.", OllamaModel())
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
        _text({"objectives": ["Check availability."], "assumptions": ["Fixtures only."]}),
        _call("finish_research", rationale="No evidence needed for this test."),
        _text({"title": "Empty report", "claims": [], "limitations": ["No evidence collected."]}),
    ])
    assert main(["--mode", "ollama", "--output", str(tmp_path), *arguments]) == 0
    assert all(request["model"] == expected for request in requests)
    assert len(list(tmp_path.glob("*/report.md"))) == 1
