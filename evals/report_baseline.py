"""Evaluate the report writer over fixed evidence; no graph or data-provider calls."""

import argparse
import hashlib
import json
import os
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from uuid import uuid4

from pydantic import Field, model_validator

from financial_research.models import Evidence, HistoricalPriceRequest, Model, ResearchReport, Text, ToolCall
from financial_research.ollama_model import OllamaModel
from financial_research.reporting import render_report, validate_citations
from financial_research.state import ResearchState

CASES_PATH = Path(__file__).with_name("report_cases.json")


class Case(Model):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    question: Text
    evidence_ids: list[Text]
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: Text = "model_finished: Evidence gathering is complete."
    criteria: list[Text] = Field(min_length=1)


class Suite(Model):
    price_request: HistoricalPriceRequest
    evidence: list[Evidence]
    cases: list[Case] = Field(min_length=1)

    @model_validator(mode="after")
    def ids_are_valid(self) -> "Suite":
        known = {item.id for item in self.evidence}
        if len(known) != len(self.evidence) or len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Evidence IDs and case IDs must be unique.")
        if any(set(case.evidence_ids) - known for case in self.cases):
            raise ValueError("A case references unknown evidence.")
        return self


def evaluate_case(suite: Suite, case: Case, write_report: Callable[[ResearchState], ResearchReport]) -> dict:
    state = ResearchState(
        question=case.question, price_request=suite.price_request,
        evidence=[item for item in suite.evidence if item.id in case.evidence_ids],
        tool_calls=case.tool_calls, stop_reason=case.stop_reason,
        max_tool_calls=len(case.tool_calls) if case.stop_reason == "tool_budget_exhausted" else 4,
    ).model_copy(deep=True)
    result = {
        "case_id": case.id, "input_state": state.model_dump(mode="json"),
        "report": None, "automatic_checks": {}, "error": None,
        "manual_checks": [{"criterion": criterion, "passed": None, "notes": ""} for criterion in case.criteria],
    }
    started = time.perf_counter()
    try:
        report = ResearchReport.model_validate(write_report(state))
        result["report"] = report.model_dump(mode="json")
        try:
            validate_citations(report, state)
        except ValueError:
            citations_exist = False
        else:
            citations_exist = True
        result["automatic_checks"] = {
            "report_schema": True,
            "citation_ids_exist": citations_exist,
            "no_claims_without_evidence": not report.claims or bool(state.evidence),
        }
    except (ValueError, RuntimeError, OSError) as exc:
        result["error"] = str(exc)
    result["seconds"] = round(time.perf_counter() - started, 3)
    return result


def status(result: dict) -> str:
    """Pending semantic review cannot be counted as a quality pass."""
    if result["error"]:
        return "ERROR"
    automatic = list(result["automatic_checks"].values())
    manual = [check["passed"] for check in result["manual_checks"]]
    if any(value is not None and type(value) is not bool for value in automatic + manual):
        raise ValueError("Grades must be JSON true, false, or null.")
    if False in automatic + manual:
        return "FAIL"
    if not automatic or not manual or None in automatic + manual:
        return "PENDING"
    return "PASS"


def summary(results: list[dict]) -> str:
    lines = ["| Case | Repeat | Seconds | Result |", "| --- | --- | --- | --- |"]
    counts = Counter()
    for result in results:
        grade = status(result)
        counts[grade] += 1
        lines.append(f"| {result['case_id']} | {result['repeat']} | {result['seconds']:.3f} | {grade} |")
    lines.extend(["", " / ".join(f"{counts[name]} {name.lower()}" for name in ("PASS", "FAIL", "PENDING", "ERROR"))])
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Generate reports with local Ollama; semantic grades start pending")
    run.add_argument("--model", default=os.environ.get("OLLAMA_MODEL") or "qwen2.5:7b")
    run.add_argument("--case", action="append", help="Case ID to run; repeat the flag to select several")
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--output", type=Path, default=Path("runs/evals"))
    score = commands.add_parser("summarize", help="Summarize results after manually grading their checklist")
    score.add_argument("results", type=Path)
    args = parser.parse_args(argv)
    if args.command == "summarize":
        print(summary(json.loads(args.results.read_text(encoding="utf-8"))["results"]), end="")
        return 0
    if args.repeats < 1:
        parser.error("--repeats must be positive")

    raw = CASES_PATH.read_bytes()
    suite = Suite.model_validate_json(raw)
    if unknown := set(args.case or []) - {case.id for case in suite.cases}:
        parser.error(f"Unknown case IDs: {', '.join(sorted(unknown))}")
    cases = [case for case in suite.cases if not args.case or case.id in args.case]
    output = args.output / uuid4().hex
    output.mkdir(parents=True)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=CASES_PATH.parent.parent, capture_output=True, text=True, check=False,
    )
    record = {
        "model": args.model, "git_revision": revision.stdout.strip(),
        "cases_sha256": hashlib.sha256(raw).hexdigest(), "repeats": args.repeats, "results": [],
    }
    model = OllamaModel(args.model)
    print(f"Report-only evaluation | local model: {args.model} | {output}", flush=True)
    for case in cases:
        for repeat in range(1, args.repeats + 1):
            result = evaluate_case(suite, case, model.write_report)
            result["repeat"] = repeat
            record["results"].append(result)
            # Preserve completed results even if a later model request fails or is interrupted.
            (output / "results.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            if result["report"] is not None and result["automatic_checks"]["citation_ids_exist"]:
                state = ResearchState.model_validate(result["input_state"])
                state.report = ResearchReport.model_validate(result["report"])
                (output / f"{case.id}-{repeat}.md").write_text(render_report(state), encoding="utf-8")
            print(f"{case.id} / {repeat}: {status(result)} ({result['seconds']:.3f}s)", flush=True)
            if result["error"]:
                print(f"  {result['error']}", flush=True)
    (output / "summary.md").write_text(summary(record["results"]), encoding="utf-8")
    print(f"Results: {output / 'results.json'}", flush=True)
    print("Semantic criteria require manual grading in results.json.", flush=True)
    return int(any(status(result) in {"ERROR", "FAIL"} for result in record["results"]))


if __name__ == "__main__":
    raise SystemExit(main())
