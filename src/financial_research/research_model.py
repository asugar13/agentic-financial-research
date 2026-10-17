"""One model boundary, with a deliberately scripted offline implementation."""

from typing import Protocol

from financial_research.models import (
    Action,
    Claim,
    FinishAction,
    MacroAction,
    NewsAction,
    PriceAction,
    ResearchPlan,
    ResearchReport,
)
from financial_research.state import ResearchState

DEMO_QUESTION = "Why has silver outperformed gold recently?"


class ResearchModel(Protocol):
    def make_plan(self, state: ResearchState) -> ResearchPlan: ...

    def choose_action(self, state: ResearchState) -> Action: ...

    def write_report(self, state: ResearchState) -> ResearchReport: ...


class DemoModel:
    """A replay for learning graph mechanics; this class does not use an LLM."""

    def make_plan(self, state: ResearchState) -> ResearchPlan:
        if state.question.casefold().rstrip("? .") != DEMO_QUESTION.casefold().rstrip("? ."):
            raise ValueError(
                f"The scripted demo supports only: {DEMO_QUESTION} "
                "Use --mode llm for other questions (the data will still be fixtures)."
            )
        return ResearchPlan(
            objectives=[
                "Verify the silver/gold relative-return premise.",
                "Inspect the real-yield explanation.",
                "Look for industrial-demand commentary and counterevidence.",
            ],
            assumptions=[
                "Recently means the synthetic period 2025-01-02 to 2025-03-31.",
                "All prices, macro values, and news are fictional fixtures.",
            ],
        )

    def choose_action(self, state: ResearchState) -> Action:
        scripted: list[Action] = [
            PriceAction(
                symbols=["XAGUSD", "XAUUSD"],
                rationale="Check that silver actually outperformed gold before explaining it.",
            ),
            MacroAction(series_id="DFII10", rationale="Inspect the proposed real-yield driver."),
            NewsAction(
                query="silver gold industrial demand counterarguments",
                rationale="Find a competing explanation and evidence against the thesis.",
            ),
        ]
        if len(state.tool_calls) < len(scripted):
            return scripted[len(state.tool_calls)]
        return FinishAction(rationale="The scripted fixture investigation is complete.")

    def write_report(self, state: ResearchState) -> ResearchReport:
        claims = [
            Claim(statement=item.content, kind="observation", evidence_ids=[item.id])
            for item in state.evidence
        ]
        if {item.source_type for item in state.evidence} == {"prices", "macro", "news"}:
            claims.append(Claim(
                statement=(
                    "Real yields and industrial demand are candidate explanations in this "
                    "scenario, but the collected evidence does not establish the cause "
                    "of silver's relative outperformance."
                ),
                kind="inference",
                evidence_ids=[item.id for item in state.evidence],
            ))
        limitations = [
            "Synthetic fixtures cannot answer a question about actual market conditions.",
            "Two endpoints and fictional commentary cannot establish causation or statistical significance.",
        ]
        if state.stop_reason == "tool_budget_exhausted":
            limitations.append("The tool-call budget was reached; research may be incomplete.")
        limitations.extend(call.result.error for call in state.tool_calls if call.result.error)
        return ResearchReport(title="Silver versus gold: fixture demonstration", claims=claims, limitations=limitations)
