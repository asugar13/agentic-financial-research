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


def test_cli_saves_report_and_roundtrippable_state(tmp_path, capsys):
    assert main(["--output", str(tmp_path)]) == 0
    output = next(tmp_path.iterdir())
    saved = ResearchState.model_validate_json((output / "state.json").read_text())
    assert len(saved.tool_calls) == 3
    assert "SYNTHETIC FIXTURE DATA" in (output / "report.md").read_text()
    assert "finish_research" in capsys.readouterr().out
    # Reusing the output parent creates another run rather than overwriting it.
    assert main(["--output", str(tmp_path), "--max-tool-calls", "0"]) == 0
    assert len(list(tmp_path.iterdir())) == 2


@pytest.mark.parametrize("arguments", [["--max-tool-calls", "-1"], ["--max-tool-calls", "11"], ["   "]])
def test_cli_rejects_invalid_inputs(arguments):
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2


def test_demo_rejects_other_questions_without_creating_artifacts(tmp_path, capsys):
    assert main(["Why did oil fall?", "--output", str(tmp_path)]) == 1
    assert list(tmp_path.iterdir()) == []
    assert "scripted demo supports only" in capsys.readouterr().err


def test_llm_mode_requires_credentials(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit) as error:
        main(["--mode", "llm", "--model", "test-model"])
    assert error.value.code == 2
