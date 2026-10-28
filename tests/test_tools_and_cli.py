import json

import pytest

from financial_research.cli import main
from financial_research.models import NewsRequest, PriceRequest
from financial_research.state import ResearchState
from financial_research.tools import get_market_prices, search_news


def test_prices_calculate_returns_and_respect_requested_symbols():
    result = get_market_prices(PriceRequest(symbols=["XAGUSD", "XAGUSD"], rationale="Check silver."))
    assert result.error is None
    assert len(result.observations) == 1
    assert result.observations[0].data["return_pct"] == pytest.approx(20.0)
    assert result.observations[0].source_uri == "fixture://prices/XAGUSD"


def test_unavailable_prices_are_explicit_failures_without_evidence():
    result = get_market_prices(PriceRequest(symbols=["UNKNOWN"], rationale="Check availability."))
    assert "unavailable" in result.error
    assert result.observations == []


def test_news_query_with_no_matches_returns_no_evidence():
    result = search_news(NewsRequest(query="unrelated topic", rationale="Check coverage."))
    assert result.error is None
    assert result.observations == []


@pytest.mark.parametrize("review", [False, True])
def test_cli_saves_report_and_roundtrippable_state(tmp_path, capsys, review):
    assert main(["--mode", "demo", "--output", str(tmp_path), *(["--review"] if review else [])]) == 0
    output = next(tmp_path.iterdir())
    saved = ResearchState.model_validate_json((output / "state.json").read_text())
    assert len(saved.tool_calls) == 3
    diagnostics = json.loads((output / "run.json").read_text())
    assert diagnostics["status"] == "succeeded"
    assert diagnostics["tool_calls"] == 3 and diagnostics["tool_failures"] == 0
    assert diagnostics["steps"][0]["stage"] == "plan"
    assert all(step["seconds"] >= 0 for step in diagnostics["steps"])
    if review:
        assert saved.draft_report == saved.report
        assert saved.critique.issues == []
    else:
        assert saved.draft_report is None and saved.critique is None
    assert "SYNTHETIC FIXTURE DATA" in (output / "report.md").read_text()
    assert "finish_research" in capsys.readouterr().out
    # Reusing the output parent creates another run rather than overwriting it.
    assert main(["--mode", "demo", "--output", str(tmp_path), "--max-tool-calls", "0"]) == 0
    assert len(list(tmp_path.iterdir())) == 2


@pytest.mark.parametrize("arguments", [["--max-tool-calls", "-1"], ["--max-tool-calls", "11"], ["   "]])
def test_cli_rejects_invalid_inputs(arguments):
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2


def test_demo_rejection_preserves_failure_diagnostics(tmp_path, capsys):
    assert main(["Why did oil fall?", "--mode", "demo", "--output", str(tmp_path)]) == 1
    output = next(tmp_path.iterdir())
    diagnostics = json.loads((output / "run.json").read_text())
    assert diagnostics["status"] == "failed"
    assert diagnostics["steps"][-1]["stage"] == "plan"
    assert diagnostics["steps"][-1]["status"] == "failed"
    assert ResearchState.model_validate_json((output / "state.json").read_text()).question == "Why did oil fall?"
    assert not (output / "report.md").exists()
    assert "scripted demo supports only" in capsys.readouterr().err


def test_failed_report_keeps_collected_evidence_and_records_failed_stage(monkeypatch, tmp_path):
    from financial_research.research_model import DemoModel

    def unavailable(self, state):
        raise RuntimeError("Report model unavailable")

    monkeypatch.setattr(DemoModel, "write_report", unavailable)
    assert main(["--mode", "demo", "--output", str(tmp_path)]) == 1
    output = next(tmp_path.iterdir())
    saved = ResearchState.model_validate_json((output / "state.json").read_text())
    diagnostics = json.loads((output / "run.json").read_text())
    assert len(saved.evidence) == 5 and len(saved.tool_calls) == 3
    assert saved.report is None and not (output / "report.md").exists()
    assert diagnostics["steps"][-1]["stage"] == "report"
    assert diagnostics["steps"][-1]["status"] == "failed"
    assert "Report model unavailable" in diagnostics["error"]


def test_review_failure_preserves_the_draft_without_publishing_it(monkeypatch, tmp_path):
    from financial_research.research_model import DemoModel

    def unavailable(self, state):
        raise RuntimeError("Reviewer unavailable")

    monkeypatch.setattr(DemoModel, "review_report", unavailable)
    assert main(["--mode", "demo", "--review", "--output", str(tmp_path)]) == 1
    output = next(tmp_path.iterdir())
    state = ResearchState.model_validate_json((output / "state.json").read_text())
    assert state.draft_report is not None and state.report is None
    assert not (output / "report.md").exists()


def test_llm_mode_requires_credentials(monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit) as error:
        main(["Compare IBM prices.", "--mode", "llm", "--model", "test-model",
              "--symbols", "IBM", "--start-date", "2025-01-06", "--end-date", "2025-01-07"])
    assert error.value.code == 2
    assert "llm mode requires OPENAI_API_KEY" in capsys.readouterr().err


@pytest.mark.parametrize("arguments", [
    [],
    ["--mode", "ollama"],
    ["A question."],
    ["A question.", "--symbols", "IBM"],
    ["--symbols", "IBM"],
    ["A question.", "--symbols", "IBM",
     "--start-date", "2025-02-01", "--end-date", "2025-01-01"],
    ["--mode", "demo", "--symbols", "IBM"],
    ["--mode", "demo", "--start-date", "2025-01-06"],
    ["--mode", "demo", "--end-date", "2025-01-07"],
])
def test_cli_rejects_ambiguous_or_invalid_real_data_configuration(arguments):
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2


def test_cli_without_arguments_explains_required_scope_and_offline_demo(capsys):
    with pytest.raises(SystemExit) as error:
        main([])
    assert error.value.code == 2
    message = capsys.readouterr().err
    assert "question, --symbols, --start-date, and --end-date" in message
    assert "--mode demo" in message
