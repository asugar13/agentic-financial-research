"""Run the learning workflow and preserve the report plus inspectable state."""

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

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
    parser = argparse.ArgumentParser(description="Inspect a bounded financial research loop using synthetic data.")
    parser.add_argument("question", nargs="?", default=DEMO_QUESTION)
    parser.add_argument("--mode", choices=["demo", "ollama", "llm"], default="demo")
    parser.add_argument("--model", help="Model ID; otherwise OLLAMA_MODEL (default qwen2.5:7b) or OPENAI_MODEL")
    parser.add_argument("--max-tool-calls", type=_budget, default=4)
    parser.add_argument("--output", type=Path, default=Path("runs"), help="Parent directory for a new run folder")
    args = parser.parse_args(argv)
    if not args.question.strip():
        parser.error("question must not be empty")

    print("SYNTHETIC FIXTURES | " + ("scripted demo, no LLM" if args.mode == "demo" else "LLM-selected tools"))
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
            args.question, model, max_tool_calls=args.max_tool_calls,
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
