"""Real boto3 request validation with stubbed responses; no AWS requests or credentials."""

import json

import pytest

boto3 = pytest.importorskip("boto3")
from botocore.stub import Stubber
from botocore.exceptions import NoCredentialsError

from financial_research.bedrock_model import BedrockModel, DEFAULT_MODEL, ModelError
from financial_research.cli import main
from financial_research.models import HistoricalPriceRequest, ResearchPlan, ResearchReport
from financial_research.runner import run_saved_research
from financial_research.state import ResearchState


def text_response(value, stop="end_turn"):
    return {"output": {"message": {"role": "assistant", "content": [{"text": json.dumps(value)}]}},
            "stopReason": stop, "usage": {"inputTokens": 100, "outputTokens": 20, "totalTokens": 120},
            "metrics": {"latencyMs": 1}}


def tool_response(name, **arguments):
    return {"output": {"message": {"role": "assistant", "content": [{"toolUse": {
        "toolUseId": "call_test", "name": name, "input": arguments,
    }}]}}, "stopReason": "tool_use", "usage": {"inputTokens": 100, "outputTokens": 20, "totalTokens": 120},
            "metrics": {"latencyMs": 1}}


def model_with_outputs(outputs):
    client = boto3.client("bedrock-runtime", region_name="eu-north-1",
                          aws_access_key_id="test-only", aws_secret_access_key="test-only")
    requests = []
    client.meta.events.register("before-parameter-build.bedrock-runtime.Converse",
                                lambda params, **kwargs: requests.append(params.copy()))
    stub = Stubber(client)
    for output in outputs:
        stub.add_response("converse", output)
    stub.activate()
    return BedrockModel(client=client, region="eu-north-1"), requests, stub


def test_native_calls_and_review_run_through_graph_and_save_usage(tmp_path):
    report = {"title": "Fixture findings", "claims": [{
        "statement": "Synthetic silver prices rose 20%.", "kind": "observation", "evidence_ids": ["E001"],
    }], "limitations": ["Synthetic evidence only."]}
    model, requests, stub = model_with_outputs([
        text_response({"objectives": ["Check fixture prices."], "assumptions": ["Synthetic evidence."]}),
        tool_response("get_market_prices", symbols=["XAGUSD"], rationale="Inspect fixture prices."),
        tool_response("finish_research", rationale="Enough evidence for this example."),
        text_response(report), text_response({"issues": ["Make the title more explicit about synthetic evidence."]}),
        text_response({**report, "title": "Synthetic fixture findings"}),
    ])
    state = run_saved_research("Inspect the fixture prices.", model, output=tmp_path / "run", mode="bedrock", review=True)
    assert state.report.title == "Synthetic fixture findings"
    assert state.draft_report.title == "Fixture findings" and state.critique.issues
    assert state.report.claims[0].evidence_ids == ["E001"]
    assert len(state.tool_calls) == 1
    assert all(request["inferenceConfig"]["maxTokens"] == 4096 for request in requests)
    assert all(request["modelId"] == DEFAULT_MODEL for request in requests)
    assert requests[1]["toolConfig"]["toolChoice"] == {"any": {}}
    assert {tool["toolSpec"]["name"] for tool in requests[1]["toolConfig"]["tools"]} == {
        "get_market_prices", "get_macro_series", "search_news", "finish_research",
    }
    assert json.loads(requests[2]["messages"][0]["content"][0]["text"])["evidence"][0]["id"] == "E001"
    assert "draft_report" in json.loads(requests[4]["messages"][0]["content"][0]["text"])
    assert "critique" in json.loads(requests[5]["messages"][0]["content"][0]["text"])
    saved = json.loads((tmp_path / "run/run.json").read_text())
    assert saved["model_usage"] == {"requests": 6, "input_tokens": 600, "output_tokens": 120, "responses_without_usage": 0}
    assert saved["model_region"] == "eu-north-1" and saved["mode"] == "bedrock"
    stub.assert_no_pending_responses()


def test_real_scope_offers_price_macro_and_quant_tools():
    model, requests, _ = model_with_outputs([
        tool_response("analyze_prices", evidence_ids=["E001", "E002"], window=20, rationale="Calculate correlation."),
    ])
    scope = HistoricalPriceRequest(symbols=["AAPL", "MSFT"], start_date="2025-01-01", end_date="2025-12-31", rationale="User scope.")
    action = model.choose_action(ResearchState(question="Compare returns.", price_request=scope))
    assert action.evidence_ids == ["E001", "E002"] and action.window == 20
    assert {tool["toolSpec"]["name"] for tool in requests[0]["toolConfig"]["tools"]} == {
        "get_market_prices", "get_macro_series", "analyze_prices", "finish_research",
    }
    assert "REAL Yahoo Finance" in requests[0]["system"][0]["text"]


def test_unsupported_schema_bounds_are_still_enforced_by_pydantic():
    model, requests, _ = model_with_outputs([
        text_response({"objectives": [], "assumptions": []}),
        tool_response("analyze_prices", evidence_ids=["E001"], window=2, rationale="Invalid trailing window."),
    ])
    with pytest.raises(ValueError):
        model.make_plan(ResearchState(question="Inspect prices."))
    schema = json.loads(requests[0]["outputConfig"]["textFormat"]["structure"]["jsonSchema"]["schema"])
    assert "maxItems" not in schema["properties"]["objectives"]
    assert ResearchPlan.model_json_schema()["properties"]["objectives"]["maxItems"] == 5
    with pytest.raises(ValueError):
        model.choose_action(ResearchState(question="Calculate volatility.", price_request=HistoricalPriceRequest(
            symbols=["AAPL"], start_date="2025-01-01", end_date="2025-12-31", rationale="User scope.",
        )))


@pytest.mark.parametrize("response", [
    text_response({"message": "I would finish."}),
    tool_response("run_python", rationale="Unknown tool."),
    tool_response("get_market_prices", symbols=[], rationale="Invalid symbols."),
])
def test_invalid_actions_fail_before_tool_execution(response):
    model, _, _ = model_with_outputs([response])
    with pytest.raises((ModelError, ValueError)):
        model.choose_action(ResearchState(question="Inspect prices."))


def test_multiple_tool_calls_are_rejected():
    response = tool_response("finish_research", rationale="Done.")
    response["output"]["message"]["content"].append({"toolUse": {
        "toolUseId": "call_other", "name": "search_news", "input": {"query": "prices", "rationale": "Extra call."},
    }})
    model, _, _ = model_with_outputs([response])
    with pytest.raises(ModelError, match="exactly one"):
        model.choose_action(ResearchState(question="Inspect prices."))


@pytest.mark.parametrize("stop", ["max_tokens", "guardrail_intervened", "content_filtered", "malformed_tool_use"])
def test_incomplete_or_filtered_outputs_fail_and_retain_usage(stop):
    model, _, _ = model_with_outputs([text_response({}, stop)])
    with pytest.raises(ModelError, match="did not complete normally"):
        model.make_plan(ResearchState(question="Inspect prices."))
    assert model.usage["output_tokens"] == 20


def test_aws_failures_preserve_diagnostics_and_do_not_expose_provider_message(tmp_path):
    model, _, stub = model_with_outputs([])
    stub.add_client_error("converse", service_error_code="AccessDeniedException", service_message="do-not-expose-provider-detail")
    with pytest.raises(ModelError, match="AccessDeniedException") as failure:
        run_saved_research("Inspect prices.", model, output=tmp_path / "failure", mode="bedrock")
    saved = json.loads((tmp_path / "failure/run.json").read_text())
    assert saved["status"] == "failed" and saved["model_usage"]["requests"] == 1
    assert "do-not-expose-provider-detail" not in str(failure.value) + json.dumps(saved)
    assert not (tmp_path / "failure/report.md").exists()


def test_credential_errors_happen_inside_the_recorded_run(monkeypatch, tmp_path):
    from financial_research import bedrock_model

    def no_credentials():
        raise NoCredentialsError()

    monkeypatch.setattr(bedrock_model.boto3, "Session", no_credentials)
    with pytest.raises(ModelError, match="AWS_PROFILE"):
        run_saved_research("Inspect prices.", BedrockModel(), output=tmp_path / "failure", mode="bedrock")
    saved = json.loads((tmp_path / "failure/run.json").read_text())
    assert saved["status"] == "failed" and saved["model_usage"]["requests"] == 1
    assert (tmp_path / "failure/state.json").exists()


def test_reused_model_usage_is_recorded_per_run(tmp_path):
    outputs = [text_response({"objectives": ["Inspect availability."], "assumptions": []}),
               text_response({"title": "No findings", "claims": [], "limitations": ["No evidence gathered."]})]
    model, _, _ = model_with_outputs(outputs * 2)
    for name in ("first", "second"):
        run_saved_research("Inspect prices.", model, output=tmp_path / name, mode="bedrock", max_tool_calls=0)
        saved = json.loads((tmp_path / name / "run.json").read_text())
        assert saved["model_usage"]["requests"] == 2
        assert saved["model_usage"]["input_tokens"] == 200


@pytest.mark.parametrize("explicit, environment, expected", [
    (None, None, DEFAULT_MODEL), (None, "configured-model", "configured-model"),
    ("explicit-model", "configured-model", "explicit-model"),
])
def test_cli_provider_and_model_selection(monkeypatch, tmp_path, explicit, environment, expected):
    from financial_research import bedrock_model

    if environment:
        monkeypatch.setenv("BEDROCK_MODEL", environment)
    else:
        monkeypatch.delenv("BEDROCK_MODEL", raising=False)
    model, _, _ = model_with_outputs([
        text_response({"objectives": ["Check availability."], "assumptions": []}),
        tool_response("finish_research", rationale="No evidence available for this test."),
        text_response({"title": "No findings", "claims": [], "limitations": ["No evidence collected."]}),
    ])
    original = bedrock_model.BedrockModel
    selected = []

    def construct(name):
        selected.append(original(name, client=model.client).model)
        return model

    monkeypatch.setattr(bedrock_model, "BedrockModel", construct)
    assert main(["Inspect AAPL.", "--mode", "bedrock", "--symbols", "AAPL", "--start-date", "2025-01-01",
                 "--end-date", "2025-12-31", "--output", str(tmp_path), *(["--model", explicit] if explicit else [])]) == 0
    assert selected == [expected]
    diagnostics = json.loads(next(tmp_path.glob("*/run.json")).read_text())
    assert diagnostics["mode"] == "bedrock" and diagnostics["model_usage"]["requests"] == 3
