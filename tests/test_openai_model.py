"""Exercise real SDK serialization/parsing against an in-memory HTTP transport."""

import json

import pytest

openai = pytest.importorskip("openai")
httpx = pytest.importorskip("httpx")

from financial_research.models import HistoricalPriceRequest, ResearchPlan, ResearchReport
from financial_research.openai_model import ModelError, OpenAIModel
from financial_research.state import ResearchState
from financial_research.workflow import run_research


def structured_output(data):
    return [{
        "id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
        "content": [{"type": "output_text", "text": json.dumps(data), "annotations": []}],
    }]


def tool_output(name, **arguments):
    return [{
        "id": "fc_test", "type": "function_call", "call_id": "call_test",
        "name": name, "arguments": json.dumps(arguments), "status": "completed",
    }]


def model_with_outputs(outputs):
    requests = []
    remaining = iter(outputs)

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "resp_test", "object": "response", "created_at": 0,
            "status": "completed", "model": "test-model", "output": next(remaining),
            "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
        })

    client = openai.OpenAI(
        api_key="test-placeholder", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    return OpenAIModel("test-model", client=client), requests


def test_native_tool_calls_drive_the_graph_without_real_network_requests():
    model, requests = model_with_outputs([
        structured_output({"objectives": ["Inspect commentary and prices."], "assumptions": ["Fixtures only."]}),
        tool_output("search_news", query="silver", rationale="Start with possible explanations."),
        tool_output("get_market_prices", symbols=["XAGUSD", "XAUUSD"], rationale="Now verify the premise."),
        tool_output("finish_research", rationale="Enough for a limited fixture report."),
        structured_output({
            "title": "Limited fixture report",
            "claims": [{"statement": "Fictional commentary mentions solar demand.", "kind": "observation", "evidence_ids": ["E001"]}],
            "limitations": ["Synthetic data; no causal conclusion."],
        }),
    ])
    state = run_research("Investigate silver and gold in the fixture period.", model)
    assert [call.action.tool for call in state.tool_calls] == ["search_news", "get_market_prices"]
    assert len(state.evidence) == 4
    assert state.report.title == "Limited fixture report"
    assert all(request["store"] is False for request in requests)
    assert requests[1]["parallel_tool_calls"] is False
    assert requests[1]["tool_choice"] == "required"
    assert {tool["name"] for tool in requests[1]["tools"]} == {
        "get_market_prices", "get_macro_series", "search_news", "finish_research",
    }
    # The next decision receives normalized results in state, not hidden provider history.
    next_context = json.loads(requests[2]["input"])
    assert next_context["evidence"][0]["id"] == "E001"
    assert next_context["tool_calls"][0]["action"]["tool"] == "search_news"


def test_provider_rejects_a_response_without_a_function_call():
    model, _ = model_with_outputs([structured_output({"message": "No tool call."})])
    with pytest.raises(ModelError, match="exactly one"):
        model.choose_action(ResearchState(question="Test question."))


def test_provider_rejects_unknown_tool_names():
    model, _ = model_with_outputs([tool_output("run_python", rationale="Unexpected tool.")])
    with pytest.raises(ValueError, match="union_tag_invalid"):
        model.choose_action(ResearchState(question="Test question."))


def test_provider_reports_refusal_without_fabricating_a_plan():
    model, _ = model_with_outputs([[{
        "id": "msg_refusal", "type": "message", "role": "assistant", "status": "completed",
        "content": [{"type": "refusal", "refusal": "Unable to provide output."}],
    }]])
    with pytest.raises(ModelError, match="no valid structured output"):
        model.make_plan(ResearchState(question="Test question."))


def test_real_data_scope_uses_the_price_and_macro_catalog_and_instructions():
    model, requests = model_with_outputs([
        tool_output("get_macro_series", series_id="DFII10", rationale="Inspect real yields in the configured interval."),
    ])
    scope = HistoricalPriceRequest(
        symbols=["IBM"], start_date="2025-01-06", end_date="2025-01-07", rationale="User-selected scope.",
    )
    action = model.choose_action(ResearchState(question="Compare IBM prices.", price_request=scope))
    assert action.series_id == "DFII10"
    assert {tool["name"] for tool in requests[0]["tools"]} == {"get_market_prices", "get_macro_series", "finish_research"}
    assert "REAL Yahoo Finance" in requests[0]["instructions"]
    assert "FRED" in requests[0]["instructions"]
    assert json.loads(requests[0]["input"])["price_request"]["start_date"] == "2025-01-06"
