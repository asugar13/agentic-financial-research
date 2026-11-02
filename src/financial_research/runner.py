"""Run research and save artifacts for both the CLI and the Streamlit interface."""

import time
from collections.abc import Callable
from pathlib import Path

from financial_research.diagnostics import RunRecorder
from financial_research.models import HistoricalPriceRequest
from financial_research.reporting import render_report
from financial_research.research_model import ResearchModel
from financial_research.state import ResearchState
from financial_research.workflow import run_research


def run_saved_research(
    question: str,
    model: ResearchModel,
    *,
    output: Path,
    mode: str,
    max_tool_calls: int = 4,
    price_request: HistoricalPriceRequest | None = None,
    review: bool = False,
    on_event: Callable[[str], None] | None = None,
) -> ResearchState:
    recorder = None
    stage = "graph"
    started = time.perf_counter()
    try:
        # Save the inputs before any node can fail. The workflow constructs its
        # own initial state; this copy is only the recorder's starting snapshot.
        initial = ResearchState(question=question, price_request=price_request, max_tool_calls=max_tool_calls)
        recorder = RunRecorder(output, initial, mode=mode, model=getattr(model, "model", "scripted"), review=review)
        state = run_research(
            question, model, max_tool_calls=max_tool_calls, price_request=price_request,
            review=review, on_event=on_event, on_step=recorder.step,
        )
        stage, started = "artifacts", time.perf_counter()
        (output / "report.md").write_text(render_report(state), encoding="utf-8")
        recorder.step("artifacts", state, time.perf_counter() - started, None)
        recorder.finish()
        return state
    except (Exception, KeyboardInterrupt) as exc:
        if recorder:
            try:
                # Graph nodes record their own failures. File-writing failures
                # happen outside the graph, so record that stage here as well.
                if stage != "graph":
                    recorder.step(stage, state, time.perf_counter() - started, f"{type(exc).__name__}: {exc}")
                recorder.finish(f"{type(exc).__name__}: {exc}")
            except OSError as save_error:
                exc.add_note(f"Could not finish saving diagnostics: {save_error}")
        raise
