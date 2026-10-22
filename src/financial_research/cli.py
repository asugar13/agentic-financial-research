"""Run the learning workflow and preserve the report plus inspectable state."""

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from financial_research.models import HistoricalPriceRequest
from financial_research.reporting import render_report
from financial_research.research_model import DEMO_QUESTION, DemoModel, ResearchModel
from financial_research.workflow import run_research


def _budget(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer from 0 to 10") from exc
    if not 0 <= number <= 10:
        raise argparse.ArgumentTypeError("must be an integer from 0 to 10")
    return number


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run bounded financial research with fixture or real price and macro data.")
    parser.add_argument("question", nargs="?")
    parser.add_argument("--mode", choices=["demo", "ollama", "llm"], default="demo")
    parser.add_argument("--model", help="Model ID; otherwise OLLAMA_MODEL (default qwen2.5:7b) or OPENAI_MODEL")
    parser.add_argument("--max-tool-calls", type=_budget, default=4)
    parser.add_argument("--data-source", choices=["fixtures", "yahoo"], default="fixtures")
    parser.add_argument("--symbols", nargs="+", help="Allowed provider symbols for real-price research")
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--output", type=Path, default=Path("runs"), help="Parent directory for a new run folder")
    args = parser.parse_args(argv)
    if args.question is not None and not args.question.strip():
        parser.error("question must not be empty")

    price_request = None
    if args.data_source == "yahoo":
        if args.mode == "demo":
            parser.error("real prices require --mode ollama or --mode llm")
        if not args.question or not args.symbols or not args.start_date or not args.end_date:
            parser.error("real prices require a question, --symbols, --start-date, and --end-date")
        try:
            price_request = HistoricalPriceRequest(
                symbols=args.symbols, start_date=args.start_date, end_date=args.end_date,
                rationale="The user selected these symbols and dates as the research scope.",
            )
        except ValidationError:
            parser.error("Provide 1–5 nonempty symbols and start-date earlier than end-date.")
    elif args.symbols or args.start_date or args.end_date:
        parser.error("--symbols and date arguments require --data-source yahoo")
    question = args.question or DEMO_QUESTION
    label = "REAL DATA" if price_request is not None else "SYNTHETIC FIXTURES"
    print(label + " | " + ("scripted demo, no LLM" if args.mode == "demo" else "LLM-selected tools"))
    model: ResearchModel = DemoModel()
    if args.mode == "ollama":
        from financial_research.ollama_model import OllamaModel

        model = OllamaModel(args.model or os.environ.get("OLLAMA_MODEL") or "qwen2.5:7b")
        print(f"Local Ollama model: {model.model}", flush=True)
    elif args.mode == "llm":
        model_name = args.model or os.environ.get("OPENAI_MODEL")
        if not os.environ.get("OPENAI_API_KEY") or not model_name:
            parser.error("llm mode requires OPENAI_API_KEY and --model (or OPENAI_MODEL)")
        try:
            from financial_research.openai_model import OpenAIModel
        except ImportError:
            parser.error("install the optional provider with: python -m pip install -e '.[llm]'")
        model = OpenAIModel(model_name)

    try:
        state = run_research(
            question, model, max_tool_calls=args.max_tool_calls, price_request=price_request,
            on_event=lambda message: print(message, flush=True),
        )
        markdown = render_report(state)
        output = args.output / uuid4().hex
        output.mkdir(parents=True)
        (output / "state.json").write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
        (output / "report.md").write_text(markdown, encoding="utf-8")
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Research failed: {exc}", file=sys.stderr)
        return 1
    print(f"\nReport: {(output / 'report.md').resolve()}")
    print(f"State:  {(output / 'state.json').resolve()}")
    return 0
