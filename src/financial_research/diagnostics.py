"""Persist run metadata and the last graph snapshot, including failed runs."""

import json
import hashlib
from importlib.metadata import PackageNotFoundError, version
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from financial_research.state import ResearchState


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text + "\n", encoding="utf-8")
    temporary.replace(path)


class RunRecorder:
    def __init__(self, output: Path, state: ResearchState, *, mode: str, model: str, review: bool):
        self.output = output
        output.mkdir(parents=True)
        self.started = time.perf_counter()
        try:
            revision = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent,
                capture_output=True, text=True, timeout=2, check=False,
            ).stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            revision = None
        source = hashlib.sha256()
        for path in sorted(Path(__file__).parent.glob("*.py")):
            source.update(path.name.encode() + b"\0" + path.read_bytes())
        dependencies = {}
        for name in ("langgraph", "pydantic", "yfinance", "openai"):
            try:
                dependencies[name] = version(name)
            except PackageNotFoundError:
                pass
        self.data: dict[str, Any] = {
            "run_id": output.name, "status": "running", "started_at": _now(),
            "mode": mode, "model": model, "review": review, "git_revision": revision,
            "python": platform.python_version(), "steps": [], "error": None,
            "source_sha256": source.hexdigest(), "dependencies": dependencies,
        }
        self.snapshot(state)

    def snapshot(self, state: ResearchState) -> None:
        _write(self.output / "state.json", state.model_dump_json(indent=2))
        self.data.update(tool_calls=len(state.tool_calls), evidence_items=len(state.evidence),
                         tool_failures=sum(bool(call.result.error) for call in state.tool_calls),
                         stop_reason=state.stop_reason)
        self.save()

    def save(self) -> None:
        _write(self.output / "run.json", json.dumps(self.data, indent=2))

    def step(self, name: str, state: ResearchState, seconds: float, error: str | None) -> None:
        self.data["steps"].append({"stage": name, "seconds": round(seconds, 6),
                                   "status": "failed" if error else "succeeded", "error": error})
        self.snapshot(state)

    def finish(self, error: str | None = None) -> None:
        self.data.update(status="failed" if error else "succeeded", error=error,
                         finished_at=_now(), seconds=round(time.perf_counter() - self.started, 6))
        self.save()
