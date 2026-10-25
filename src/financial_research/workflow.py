"""The graph owns execution and limits; the model owns research choices."""

from collections.abc import Callable
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from financial_research.models import Evidence, FinishAction, HistoricalPriceRequest, ReportCritique, ResearchReport, ToolCall
from financial_research.reporting import validate_citations
from financial_research.research_model import ResearchModel
from financial_research.state import ResearchState
from financial_research.tools import execute_tool


def write_reviewed_report(state: ResearchState, model: ResearchModel) -> ResearchReport:
    """Save the draft and critique on state, then return a final report after at most one revision."""
    state.draft_report = model.write_report(state)
    state.critique = model.review_report(state)
    try:
        validate_citations(state.draft_report, state)
    except ValueError as exc:
        if state.evidence:
            correction = f"{exc}. Use only collected evidence IDs: {', '.join(item.id for item in state.evidence)}."
        else:
            correction = f"{exc}. No evidence was collected: return claims=[] and explain retrieval failures in limitations."
        state.critique = ReportCritique(issues=[correction, *state.critique.issues[:4]])
    if state.critique.issues:
        return model.revise_report(state)
    return state.draft_report


def build_graph(
    model: ResearchModel, on_event: Callable[[str], None] | None = None,
    *, review: bool = False,
) -> CompiledStateGraph:
    def log(state: ResearchState, message: str) -> list[str]:
        if on_event:
            on_event(message)
        return [*state.events, message]

    def plan(state: ResearchState) -> dict[str, Any]:
        research_plan = model.make_plan(state)
        return {
            "plan": research_plan,
            "events": log(state, f"plan: {'; '.join(research_plan.objectives)}"),
        }

    def decide(state: ResearchState) -> dict[str, Any]:
        # Failed calls and duplicate requests also consume budget.
        if len(state.tool_calls) >= state.max_tool_calls:
            return {
                "stop_reason": "tool_budget_exhausted",
                "events": log(state, "decide: tool-call budget exhausted; produce a bounded report."),
            }
        action = model.choose_action(state)
        return {
            "action": action,
            "decisions": [*state.decisions, action],
            "stop_reason": f"model_finished: {action.rationale}" if isinstance(action, FinishAction) else None,
            "events": log(state, f"decide: {action.tool} — {action.rationale}"),
        }

    def execute(state: ResearchState) -> dict[str, Any]:
        if state.action is None or isinstance(state.action, FinishAction):
            raise ValueError("execute requires a data-tool action.")
        result = execute_tool(state.action, state.price_request)
        evidence = list(state.evidence)
        seen = {(item.source_uri, item.content) for item in evidence}
        for observation in result.observations:
            identity = (observation.source_uri, observation.content)
            if identity not in seen:
                evidence.append(Evidence(id=f"E{len(evidence) + 1:03d}", **observation.model_dump()))
                seen.add(identity)
        added = len(evidence) - len(state.evidence)
        message = f"execute: {state.action.tool} — {result.error or f'{added} new evidence item(s)'}"
        return {
            "evidence": evidence,
            "tool_calls": [*state.tool_calls, ToolCall(action=state.action, result=result)],
            "events": log(state, message),
        }

    def report(state: ResearchState) -> dict[str, Any]:
        report_state = state.model_copy(deep=True)
        if review:
            final_report = write_reviewed_report(report_state, model)
            assert report_state.critique is not None
            review_result = "revised once" if report_state.critique.issues else "no revision requested"
        else:
            final_report = model.write_report(report_state)
            review_result = "review disabled"
        validate_citations(final_report, state)
        return {
            "draft_report": report_state.draft_report,
            "critique": report_state.critique,
            "report": final_report,
            "events": log(state, f"report: {review_result}; validated citations for {len(final_report.claims)} claim(s)."),
        }

    def route(state: ResearchState) -> str:
        return "report" if state.stop_reason else "execute"

    builder = StateGraph(ResearchState)
    builder.add_node("plan", plan)
    builder.add_node("decide", decide)
    builder.add_node("execute", execute)
    builder.add_node("report", report)
    builder.add_edge(START, "plan")
    builder.add_edge("plan", "decide")
    builder.add_conditional_edges("decide", route, {"report": "report", "execute": "execute"})
    builder.add_edge("execute", "decide")
    builder.add_edge("report", END)
    return builder.compile()


def run_research(
    question: str,
    model: ResearchModel,
    *,
    max_tool_calls: int = 4,
    price_request: HistoricalPriceRequest | None = None,
    review: bool = False,
    on_event: Callable[[str], None] | None = None,
) -> ResearchState:
    initial = ResearchState(question=question, max_tool_calls=max_tool_calls, price_request=price_request)
    graph = build_graph(model, on_event, review=review)
    # At most 1 plan + (N executions + N+1 decisions) + 1 report.
    result = graph.invoke(initial, config={"recursion_limit": 2 * max_tool_calls + 6})
    return ResearchState.model_validate(result)
