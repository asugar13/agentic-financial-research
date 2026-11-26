"""HTTP entry point for the React research desk. Run with one Uvicorn worker."""

import json
import logging
import os
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from financial_research.models import HistoricalPriceRequest, Model, ResearchReport, Text
from financial_research.ollama_model import OllamaModel
from financial_research.research_model import DEMO_QUESTION, DemoModel, ResearchModel
from financial_research.runner import run_saved_research

Mode = Literal["demo", "ollama", "bedrock"]


class Scope(HistoricalPriceRequest):
    rationale: Text = "The user selected these symbols and dates as the research scope."


class ResearchRunRequest(Model):
    question: str = Field(min_length=1, max_length=4000)
    mode: Mode
    scope: Scope | None = None
    max_tool_calls: int = Field(default=4, ge=0, le=10)
    review: bool = False


class Source(Model):
    id: str
    title: str
    publisher: str
    kind: str
    url: str
    excerpt: str
    period_start: str
    period_end: str

    @classmethod
    def from_evidence(cls, item: dict) -> "Source":
        data = item["data"]
        title = data.get("title") or data.get("symbol") or data.get("series_id") or item["source_name"]
        return cls(
            id=item["id"], title=title, publisher=item["source_name"], kind=item["source_type"],
            url=item["source_uri"], excerpt=item["content"],
            period_start=item["period_start"], period_end=item["period_end"],
        )


class ResearchRunResponse(Model):
    id: UUID
    status: Literal["running", "succeeded", "failed"]
    question: str
    mode: Mode
    saved_at: str | None = None
    events: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    report: ResearchReport | None = None
    error: str | None = None


def _model(mode: Mode) -> ResearchModel:
    if mode == "demo":
        return DemoModel()
    if mode == "ollama":
        return OllamaModel(os.getenv("OLLAMA_MODEL") or "qwen2.5:7b")
    from financial_research.bedrock_model import BedrockModel

    return BedrockModel()


def create_app(runs: Path = Path("runs"), assets: Path = Path("frontend/dist")) -> FastAPI:
    app = FastAPI(title="Financial Research")
    hosted = os.getenv("RESEARCH_HOSTED") == "1"
    modes = ["bedrock", "demo"] if hosted else ["ollama", "bedrock", "demo"]
    active: ResearchRunResponse | None = None

    @app.get("/api/config")
    def config():
        return {"modes": modes, "demo_question": DEMO_QUESTION}

    def execute(run_id: UUID, request: ResearchRunRequest) -> None:
        output = runs / "react" / run_id.hex
        try:
            run_saved_research(
                request.question, _model(request.mode), output=output, mode=request.mode,
                price_request=request.scope, max_tool_calls=request.max_tool_calls, review=request.review,
            )
        except Exception:
            logging.getLogger(__name__).exception("Research run %s failed", run_id)
            # Model initialization can fail before the runner creates its first snapshot.
            if not (output / "run.json").exists():
                output.mkdir(parents=True, exist_ok=True)
                failure = {"status": "failed", "mode": request.mode, "question": request.question}
                tmp = output / "run.json.tmp"
                tmp.write_text(json.dumps(failure), encoding="utf-8")
                tmp.replace(output / "run.json")

    async def run(run_id: UUID, request: ResearchRunRequest) -> None:
        nonlocal active
        try:
            await run_in_threadpool(execute, run_id, request)
        finally:
            active = None

    @app.post("/api/runs", status_code=202)
    async def start(request: ResearchRunRequest, background: BackgroundTasks):
        nonlocal active
        if request.mode not in modes:
            raise HTTPException(422, "This provider is unavailable on this server.")
        if request.mode == "demo":
            if request.question != DEMO_QUESTION or request.scope is not None:
                raise HTTPException(422, "Select the fixed demo question to use synthetic data.")
        elif request.scope is None:
            raise HTTPException(422, "Live research requires symbols and a date range.")

        # Only this async handler reserves a slot. There is no await between checking
        # and setting it, so two submissions cannot start overlapping model runs.
        if active is not None:
            raise HTTPException(409, "Another research run is in progress. Try again when it finishes.")
        active = ResearchRunResponse(id=uuid4(), question=request.question, mode=request.mode, status="running")
        background.add_task(run, active.id, request)
        return {"id": active.id}

    @app.get("/api/runs/{run_id}", response_model=ResearchRunResponse)
    def read_run(run_id: UUID) -> ResearchRunResponse:
        output = runs / "react" / run_id.hex
        starting = active
        try:
            diagnostics = json.loads((output / "run.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            if starting and starting.id == run_id:
                return starting
            # The CLI and Streamlit save sibling UUID folders. A completed brief
            # can be opened by link without replaying its model or data requests.
            output = runs / run_id.hex
            try:
                diagnostics = json.loads((output / "run.json").read_text(encoding="utf-8"))
            except FileNotFoundError:
                raise HTTPException(404, "This research run could not be found.") from None
            if diagnostics["status"] != "succeeded" or diagnostics["mode"] not in ("demo", "ollama", "bedrock"):
                raise HTTPException(404, "No completed brief is available at this address.")
        state_path = output / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        status = diagnostics["status"]
        error = None
        if status == "running" and (starting is None or starting.id != run_id):
            status, error = "failed", "The API restarted before this run finished. Its collected sources are below."
        elif status == "failed":
            steps = diagnostics.get("steps", [])
            stage = steps[-1]["stage"] if steps else "provider setup"
            error = f"Research failed during {stage}. Check the API terminal for the provider error."
        return ResearchRunResponse(
            id=run_id, status=status, mode=diagnostics["mode"], error=error, saved_at=diagnostics.get("finished_at"),
            question=state.get("question") or diagnostics.get("question", ""),
            events=state.get("events", []), report=state.get("report") if status == "succeeded" else None,
            sources=[Source.from_evidence(item) for item in state.get("evidence", [])],
        )

    # Vite proxies /api in development. After a build, one Python server serves both.
    if assets.is_dir():
        app.mount("/", StaticFiles(directory=assets, html=True), name="workspace")
    return app


app = create_app()
