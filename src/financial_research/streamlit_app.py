"""Local research workspace. Start with: streamlit run src/financial_research/streamlit_app.py"""

import json
import os
from datetime import date
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from financial_research.models import HistoricalPriceRequest
from financial_research.ollama_model import OllamaModel
from financial_research.research_model import DEMO_QUESTION, DemoModel
from financial_research.runner import run_saved_research
from financial_research.state import ResearchState


def show_prices(state: ResearchState) -> None:
    histories = [item for item in state.evidence if item.source_type == "prices" and item.data.get("prices")]
    if not histories:
        st.info("The offline demo contains only synthetic endpoint summaries." if state.price_request is None else
                "No daily price history was collected for this run.")
        return
    selected = st.selectbox("Price history", range(len(histories)),
                            format_func=lambda index: f"{histories[index].data['symbol']} · {histories[index].id}")
    item = histories[selected]
    prices = pd.DataFrame(item.data["prices"])
    prices["date"] = pd.to_datetime(prices["date"])
    st.line_chart(prices, x="date", y="close", x_label="Date",
                  y_label=f"Close ({item.data.get('currency') or 'unit unknown'})", height=320)
    st.caption(f"{item.period_start} to {item.period_end} · {item.source_name} · {len(prices)} observations. "
               "Observed closing prices; missing sessions are not filled. Returns exclude dividends.")


def show_analysis(state: ResearchState) -> None:
    analyses = [item for item in state.evidence if item.source_type == "quant"]
    if not analyses:
        st.info("The agent did not run quantitative analysis for this question. "
                "Ask about volatility or return correlation to request it.")
    for item in analyses:
        data = item.data
        st.subheader(f"Price analysis · {item.id}")
        st.caption(f"Calculated from {', '.join(data['input_evidence_ids'])}. "
                   f"Annualization: {data['periods_per_year']} sessions; rolling window: {data['window']} returns.")
        st.dataframe(pd.DataFrame([{
            "Symbol": asset["symbol"], "From": asset["start_date"], "To": asset["end_date"],
            "Close change (%)": asset["close_price_change_pct"],
            "Annualized volatility (%)": asset["annualized_volatility_pct"],
        } for asset in data["assets"]]), hide_index=True)
        for pair in data["pairs"]:
            label = " / ".join(pair["symbols"])
            correlation = pair["correlation"]
            st.metric(f"{label} · return correlation", "Unavailable" if correlation is None else f"{correlation:.4f}")
            st.caption(f"{pair['matched_returns']} matching returns. {pair['note']}")
            if pair["rolling_correlations"]:
                rolling = pd.DataFrame(pair["rolling_correlations"])
                rolling["end_date"] = pd.to_datetime(rolling["end_date"])
                st.line_chart(rolling, x="end_date", y="correlation", x_label="Window end date",
                              y_label=f"{data['window']}-return correlation", height=240)
        with st.expander(f"Method and limitations · {item.id}"):
            for note in data["limitations"]:
                st.write(f"- {note}")


def show_result(run: dict) -> None:
    output = run["output"]
    complete = run["status"] == "succeeded"
    if run["error"]:
        st.error(run["error"])
    if not complete:
        st.info("This run did not publish a report. Any saved evidence, draft, and critique are available below.")
    try:
        state = ResearchState.model_validate_json((output / "state.json").read_text(encoding="utf-8"))
        diagnostics = json.loads((output / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        st.warning(f"Could not read the saved run: {exc}")
        return

    if state.price_request is None:
        st.warning("Offline demonstration · all prices, macro data, and news are synthetic.")
    else:
        scope = state.price_request
        st.caption(f"{', '.join(scope.symbols)} · {scope.start_date} to {scope.end_date} · Real data")
    st.write(state.question)
    columns = st.columns(3)
    columns[0].metric("Evidence collected", len(state.evidence))
    columns[1].metric("Tool calls", f"{len(state.tool_calls)} / {state.max_tool_calls}")
    columns[2].metric("Elapsed", f"{diagnostics.get('seconds', 0):.1f}s")
    if diagnostics.get("tool_failures"):
        st.warning("Some data requests failed. Check the progress log and report limitations for missing coverage.")

    findings, prices, analysis, sources = st.tabs(["Findings", "Prices", "Analysis", "Sources"])
    with findings:
        if state.report is not None and complete:
            st.subheader(state.report.title)
            for claim in state.report.claims:
                citations = " ".join(f"[{key}]" for key in claim.evidence_ids)
                st.markdown(f"**{claim.kind.capitalize()}:** {claim.statement} {citations}")
            if not state.report.claims:
                st.info("No supported findings were produced.")
            st.markdown("**Limitations**")
            for note in state.report.limitations:
                st.write(f"- {note}")
        else:
            st.info("No completed report is available.")
    with prices:
        show_prices(state)
    with analysis:
        show_analysis(state)
    with sources:
        if not state.evidence:
            st.info("No evidence was collected.")
        for item in state.evidence:
            with st.expander(f"{item.id} · {item.source_name}"):
                st.write(item.content)
                st.caption(f"{item.period_start} to {item.period_end} · Retrieved {item.retrieved_at.isoformat()}")
                st.code(item.source_uri, language=None)

    st.divider()
    for column, filename in zip(st.columns(3), ("report.md", "state.json", "run.json")):
        artifact = output / filename
        if artifact.exists() and (filename != "report.md" or complete):
            column.download_button(f"Download {filename}", artifact.read_bytes(),
                                   file_name=f"{output.name}-{filename}",
                                   mime="application/json" if filename.endswith("json") else "text/markdown",
                                   on_click="ignore")
    with st.expander("Run details"):
        st.caption(f"Saved locally in {output.resolve()}")
        st.json(diagnostics, expanded=False)
        if state.draft_report is not None:
            st.write("Draft report")
            st.json(state.draft_report.model_dump(mode="json"), expanded=False)
        if state.critique is not None:
            st.write("Reviewer corrections")
            st.json(state.critique.model_dump(mode="json"))


def main() -> None:
    st.set_page_config(page_title="Financial Research", page_icon="📈", layout="wide")
    st.caption("FINANCIAL RESEARCH")
    st.title("Ask a market question.")
    st.write("Explore prices and macro data, follow the research, and inspect the evidence behind each finding.")

    with st.sidebar:
        st.header("New research")
        mode = st.radio("Data and model", ["Local Ollama", "Offline demo"], key="mode")
        demo = mode == "Offline demo"
        year = date.today().year - 1
        with st.form("research", enter_to_submit=False):
            if demo:
                st.info(DEMO_QUESTION)
                st.caption("A fixed, synthetic example. No model, API key, or internet connection needed.")
                question, price_request = DEMO_QUESTION, None
            else:
                question = st.text_area("Research question", f"How did AAPL prices change during {year}?", key="question")
                symbols = st.text_input("Symbols", "AAPL", help="Up to five Yahoo symbols, separated by commas or spaces.", key="symbols")
                start = st.date_input("Start date", date(year, 1, 1), key="start_date")
                end = st.date_input("End date", date(year, 12, 31), key="end_date")
            with st.expander("Research settings"):
                model_name = st.text_input("Ollama model", os.environ.get("OLLAMA_MODEL") or "qwen2.5:7b", disabled=demo)
                budget = st.number_input("Maximum tool calls", min_value=0, max_value=10, value=4,
                                         help="Includes failed data requests. Does not limit model requests or tokens.")
                review = st.checkbox("Review the draft", value=False,
                                     help="Adds a critique and at most one revision. This takes extra model requests.")
            submitted = st.form_submit_button("Run research", type="primary", width="stretch")
        if not demo:
            st.caption("Ollama must be running locally. Yahoo supplies prices; FRED macro requests need FRED_API_KEY. "
                       "Live news is not connected.")

    if submitted:
        try:
            if not demo:
                if not question.strip() or not model_name.strip():
                    raise ValueError("Enter a research question and an Ollama model name.")
                price_request = HistoricalPriceRequest(
                    symbols=symbols.replace(",", " ").split(), start_date=start, end_date=end,
                    rationale="The user selected these symbols and dates as the research scope.",
                )
        except (ValidationError, ValueError):
            st.error("Enter a question, a model name, 1–5 symbols, and a start date earlier than the end date.")
        else:
            # Keep each browser session's latest attempt. Rerenders and downloads
            # read this result; only an explicit form submission starts research.
            run = {"output": Path("runs") / uuid4().hex, "events": [], "error": None, "status": "running"}
            st.session_state["research_run"] = run
            with st.status("Research in progress…", expanded=True) as progress:
                def on_event(message: str) -> None:
                    run["events"].append(message)
                    st.write(message)

                try:
                    run_saved_research(
                        question.strip(), DemoModel() if demo else OllamaModel(model_name.strip()),
                        output=run["output"], mode="demo" if demo else "ollama", price_request=price_request,
                        max_tool_calls=budget, review=review, on_event=on_event,
                    )
                except Exception as exc:
                    run["status"] = "failed"
                    run["error"] = f"{type(exc).__name__}: {exc}"
                    if notes := getattr(exc, "__notes__", []):
                        run["error"] += "\n" + "\n".join(notes)
                    progress.update(label="Research failed", state="error")
                else:
                    run["status"] = "succeeded"
                    progress.update(label="Research complete", state="complete", expanded=False)
    elif "research_run" in st.session_state:
        run = st.session_state["research_run"]
        # Streamlit can interrupt execution on a rerun. Don't label an unfinished
        # attempt complete just because it never reached the exception handler.
        if run["status"] == "running":
            run["status"] = "interrupted"
            run["error"] = "Research was interrupted. Inspect the saved run or submit a new question."
        complete = run["status"] == "succeeded"
        with st.status("Research complete" if complete else "Research did not finish",
                       state="complete" if complete else "error", expanded=False):
            for event in run["events"]:
                st.write(event)

    if "research_run" in st.session_state:
        show_result(st.session_state["research_run"])
    else:
        st.info("Set a question and date range in the sidebar, or choose Offline demo to explore without setup.")
        st.subheader("Questions to try")
        for column, title, example in zip(st.columns(3), ("Price changes", "Volatility & correlation", "Macro context"), (
            f"How did AAPL prices change during {year}?",
            f"Compare AAPL and MSFT volatility and return correlation during {year}. Use a 20-return rolling window.",
            f"How did AAPL prices and the DFII10 real yield change during {year}?",
        )):
            with column.container(border=True):
                st.markdown(f"**{title}**")
                st.write(example)
        st.caption("Reports are generated by a model. Citation checks verify evidence IDs, not the accuracy of every claim.")


if __name__ == "__main__":
    main()
