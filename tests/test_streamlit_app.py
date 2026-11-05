"""Exercise the UI and real graph without contacting a model or data provider."""

import json
from datetime import date
from pathlib import Path

import pytest

from financial_research import prices, runner
from financial_research.models import Claim, FinishAction, PriceAction, QuantAction, ResearchPlan, ResearchReport
from financial_research.ollama_model import OllamaModel
from financial_research.research_model import DemoModel
from financial_research.state import ResearchState

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
APP = Path(__file__).parents[1] / "src/financial_research/streamlit_app.py"


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return AppTest.from_file(str(APP), default_timeout=30).run()


def submit(app):
    app.button(key="FormSubmitter:research-Run research").click().run()
    assert not app.exception


def test_demo_saves_downloads_and_reruns_do_not_start_more_research(app, tmp_path):
    assert app.radio(key="mode").value == "Local Ollama"
    assert app.checkbox[0].value is False
    assert not (tmp_path / "runs").exists()
    app.radio(key="mode").set_value("Offline demo").run()
    submit(app)
    output = next((tmp_path / "runs").iterdir())
    state = ResearchState.model_validate_json((output / "state.json").read_text())
    assert state.report and len(state.evidence) == 5
    assert any("synthetic" in item.value for item in app.warning)
    assert len(app.get("download_button")) == 3
    assert app.status[0].state == "complete"
    assert not app.get("vega_lite_chart")  # Endpoint-only fixtures aren't a daily chart.
    app.run()
    assert not app.exception
    assert len(list((tmp_path / "runs").iterdir())) == 1
    assert app.session_state["research_run"]["output"].resolve() == output
    # A separate browser session never picks up another session's result.
    other = AppTest.from_file(str(APP), default_timeout=30).run()
    assert "research_run" not in other.session_state
    assert not other.get("download_button")
    submit(app)
    assert len(list((tmp_path / "runs").iterdir())) == 2


@pytest.mark.parametrize("invalid", ["question", "symbols", "dates", "too_many_symbols", "model"])
def test_invalid_inputs_do_not_create_a_run(app, tmp_path, invalid):
    if invalid == "question":
        app.text_area(key="question").set_value("   ")
    elif invalid == "symbols":
        app.text_input(key="symbols").set_value(", ,")
    elif invalid == "too_many_symbols":
        app.text_input(key="symbols").set_value("A B C D E F")
    elif invalid == "model":
        next(item for item in app.text_input if item.label == "Ollama model").set_value(" ")
    else:
        app.date_input(key="start_date").set_value(date(2025, 2, 1))
        app.date_input(key="end_date").set_value(date(2025, 1, 1))
    submit(app)
    assert app.error and not (tmp_path / "runs").exists()


@pytest.mark.parametrize("quant", [False, True])
def test_real_mode_uses_graph_evidence_and_only_shows_requested_analysis(app, monkeypatch, tmp_path, quant):
    plans = []

    def plan(self, state):
        plans.append(state)
        return ResearchPlan(objectives=["Compare prices."], assumptions=[])

    def choose(self, state):
        if not state.tool_calls:
            return PriceAction(symbols=state.price_request.symbols, rationale="Fetch requested prices.")
        if quant and len(state.tool_calls) == 1:
            return QuantAction(evidence_ids=[item.id for item in state.evidence], window=3, rationale="Compare returns.")
        return FinishAction(rationale="Enough evidence.")

    def report(self, state):
        return ResearchReport(title="Collected price findings", claims=[Claim(
            statement=state.evidence[-1].content, kind="observation", evidence_ids=[state.evidence[-1].id],
        )], limitations=["Historical analysis only."])

    monkeypatch.setattr(OllamaModel, "make_plan", plan)
    monkeypatch.setattr(OllamaModel, "choose_action", choose)
    monkeypatch.setattr(OllamaModel, "write_report", report)
    monkeypatch.setattr(prices, "_fetch", lambda request, symbol: prices._DailyResponse(
        prices={date(2025, 1, day): close for day, close in enumerate([100., 110., 99., 99.], 1)},
        currency="USD", time_zone="America/New_York",
    ))
    app.text_input(key="symbols").set_value("aapl, msft")
    app.date_input(key="start_date").set_value(date(2025, 1, 1))
    app.date_input(key="end_date").set_value(date(2025, 1, 4))
    submit(app)
    assert not app.error and len(plans) == 1
    assert plans[0].price_request.symbols == ["AAPL", "MSFT"]
    assert plans[0].max_tool_calls == 4
    assert ("[E003]" if quant else "[E002]") in " ".join(item.value for item in app.markdown)
    assert len(app.get("vega_lite_chart")) == (2 if quant else 1)
    if quant:
        assert app.dataframe[0].value.iloc[0]["Close change (%)"] == pytest.approx(-1)
        assert app.metric[-1].value == "1.0000"
    else:
        assert any("did not run quantitative analysis" in item.value for item in app.info)
    app.selectbox[0].select(1).run()
    assert not app.exception and len(plans) == 1
    assert len(list((tmp_path / "runs").iterdir())) == 1


def test_review_failure_replaces_previous_result_and_preserves_partial_state(app, monkeypatch, tmp_path):
    app.radio(key="mode").set_value("Offline demo").run()
    submit(app)
    previous = app.session_state["research_run"]["output"]

    def unavailable(self, state):
        raise RuntimeError("Reviewer unavailable")

    monkeypatch.setattr(DemoModel, "review_report", unavailable)
    app.checkbox[0].check()
    submit(app)
    output = app.session_state["research_run"]["output"]
    assert output != previous and (previous / "report.md").exists()
    assert "Reviewer unavailable" in app.error[0].value
    assert app.status[0].state == "error"
    assert not (output / "report.md").exists()
    assert len(app.get("download_button")) == 2
    state = ResearchState.model_validate_json((output / "state.json").read_text())
    assert state.draft_report and state.evidence and state.report is None
    diagnostics = json.loads((output / "run.json").read_text())
    assert diagnostics["status"] == "failed"
    assert diagnostics["steps"][-1]["stage"] == "report"
    app.run()
    assert "Reviewer unavailable" in app.error[0].value
    assert len(list((tmp_path / "runs").iterdir())) == 2


def test_artifact_failure_is_recorded_outside_the_graph(app, monkeypatch):
    def fail(state):
        raise OSError("Cannot write report")

    monkeypatch.setattr(runner, "render_report", fail)
    app.radio(key="mode").set_value("Offline demo").run()
    submit(app)
    output = app.session_state["research_run"]["output"]
    diagnostics = json.loads((output / "run.json").read_text())
    assert diagnostics["status"] == "failed"
    assert diagnostics["steps"][-1]["stage"] == "artifacts"
    assert diagnostics["steps"][-1]["status"] == "failed"
    assert "Cannot write report" in app.error[0].value
    assert not (output / "report.md").exists()


def test_interrupted_run_is_not_displayed_as_a_success(app, monkeypatch):
    from streamlit.runtime.scriptrunner import StopException

    def interrupted(self, state):
        raise StopException()

    monkeypatch.setattr(DemoModel, "write_report", interrupted)
    app.radio(key="mode").set_value("Offline demo").run()
    submit(app)
    assert app.session_state["research_run"]["status"] == "running"
    app.run()
    assert not app.exception
    assert app.session_state["research_run"]["status"] == "interrupted"
    assert "interrupted" in app.error[0].value
    assert app.status[0].state == "error"
    assert len(app.get("download_button")) == 2
