import pytest
from pydantic import ValidationError

from financial_research.models import (
    ACTION_ADAPTER,
    Claim,
    FinishAction,
    MacroAction,
    NewsAction,
    PriceAction,
    ResearchReport,
)
from financial_research.reporting import render_report
from financial_research.research_model import DEMO_QUESTION, DemoModel
from financial_research.workflow import run_research


def test_demo_collects_evidence_and_finishes_by_model_decision():
    state = run_research(DEMO_QUESTION, DemoModel())
    assert [call.action.tool for call in state.tool_calls] == [
        "get_market_prices", "get_macro_series", "search_news",
    ]
    assert [item.id for item in state.evidence] == ["E001", "E002", "E003", "E004", "E005"]
    assert state.stop_reason.startswith("model_finished:")
    assert state.report.claims[-1].kind == "inference"
    assert "leaving relative outperformance unexplained" in render_report(state)


@pytest.mark.parametrize("budget, expected_calls", [(0, 0), (1, 1), (2, 2), (3, 3), (4, 3), (10, 3)])
def test_call_budget_is_enforced_before_execution(budget, expected_calls):
    state = run_research(DEMO_QUESTION, DemoModel(), max_tool_calls=budget)
    assert len(state.tool_calls) == expected_calls
    if budget <= 3:
        assert state.stop_reason == "tool_budget_exhausted"
        assert any("incomplete" in note for note in state.report.limitations)
    if budget == 0:
        assert state.evidence == []
        assert state.report.claims == []
        assert "No supported findings" in render_report(state)


def test_repeated_actions_consume_budget_without_duplicating_evidence():
    class RepeatingModel(DemoModel):
        def choose_action(self, state):
            return PriceAction(symbols=["XAGUSD"], rationale="Request prices again.")

    state = run_research(DEMO_QUESTION, RepeatingModel(), max_tool_calls=10)
    assert len(state.tool_calls) == 10
    assert len(state.evidence) == 1
    assert state.stop_reason == "tool_budget_exhausted"


def test_model_can_change_tool_order_after_a_failed_call():
    class AdaptiveModel(DemoModel):
        def choose_action(self, state):
            if not state.tool_calls:
                return MacroAction(series_id="UNAVAILABLE", rationale="Try another macro series.")
            if state.tool_calls[-1].result.error:
                return NewsAction(query="gold", rationale="Macro failed; investigate commentary instead.")
            return FinishAction(rationale="No more useful fixture research is available.")

    state = run_research(DEMO_QUESTION, AdaptiveModel())
    assert [call.action.tool for call in state.tool_calls] == ["get_macro_series", "search_news"]
    assert state.tool_calls[0].result.error
    assert all(item.source_type == "news" for item in state.evidence)
    assert state.stop_reason.startswith("model_finished:")


def test_unknown_citations_are_rejected():
    class InvalidWriter(DemoModel):
        def write_report(self, state):
            return ResearchReport(
                title="Invalid report",
                claims=[Claim(statement="A fabricated claim.", kind="observation", evidence_ids=["E999"])],
                limitations=["Fixtures only."],
            )

    with pytest.raises(ValueError, match="unknown evidence: E999"):
        run_research(DEMO_QUESTION, InvalidWriter())


def test_a_claim_cannot_omit_citations():
    with pytest.raises(ValidationError):
        Claim(statement="A claim.", kind="observation", evidence_ids=[])


@pytest.mark.parametrize("action", [
    {"tool": "run_python", "rationale": "Execute arbitrary code."},
    {"tool": "get_market_prices", "symbols": [], "rationale": "Empty request."},
    {"tool": "get_macro_series", "series_id": "DFII10", "rationale": "   "},
    {"tool": "search_news", "query": "gold", "rationale": "Read news.", "unexpected": True},
])
def test_invalid_tool_requests_are_rejected(action):
    with pytest.raises(ValidationError):
        ACTION_ADAPTER.validate_python(action)


def test_separate_runs_do_not_share_evidence():
    first = run_research(DEMO_QUESTION, DemoModel())
    second = run_research(DEMO_QUESTION, DemoModel(), max_tool_calls=0)
    assert len(first.evidence) == 5
    assert second.evidence == []
