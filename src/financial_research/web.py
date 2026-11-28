"""HTTP entry point for the React UI. Run with one Uvicorn worker."""

import json
from datetime import date, timedelta
import logging
import os
import re
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import Field, field_validator, model_validator

from financial_research.ask import AskError, StockAnswer, ask_about_stock
from financial_research.discover import DiscoverError, Discovery, discover_companies
from financial_research.explain import EventExplanation, ExplainError, explain_event
from financial_research.models import HistoricalPriceRequest, Model, ResearchReport, Text
from financial_research.ollama_model import OllamaModel
from financial_research.radar import (
    MissingExaKey, ScanCache, SearchSettings, WatchlistPrices, WatchlistScan, price_history, scan_watchlist,
)
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


class WatchlistRequest(Model):
    tickers: list[str] = Field(min_length=1, max_length=15)
    days: int = Field(default=30, ge=1, le=90)

    @field_validator("tickers")
    @classmethod
    def valid_tickers(cls, tickers: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(ticker.strip().upper() for ticker in tickers if ticker.strip()))
        if not normalized or not all(re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,11}", ticker) for ticker in normalized):
            raise ValueError("Enter stock tickers such as NVDA, BRK-B, or SAP.DE.")
        return normalized


class ScanRequest(WatchlistRequest):
    settings: SearchSettings = SearchSettings()


class AskRequest(Model):
    ticker: str = Field(min_length=1, max_length=12)
    question: str = Field(min_length=3, max_length=300)
    start_date: date
    end_date: date
    settings: SearchSettings = SearchSettings()

    @field_validator("ticker")
    @classmethod
    def valid_ticker(cls, ticker: str) -> str:
        ticker = ticker.strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,11}", ticker):
            raise ValueError("Enter a stock ticker such as NVDA, BRK-B, or SAP.DE.")
        return ticker

    @model_validator(mode="after")
    def sensible_period(self) -> "AskRequest":
        if self.end_date > date.today():
            raise ValueError("The period can't end in the future.")
        if not timedelta(0) <= self.end_date - self.start_date <= timedelta(days=366):
            raise ValueError("Choose a start date before the end date, at most a year apart.")
        return self


class DiscoverRequest(Model):
    query: str = Field(min_length=2, max_length=200)


class ExplainRequest(Model):
    ticker: str = Field(min_length=1, max_length=12)
    reported_date: date
    headline: str = Field(min_length=1, max_length=500)
    settings: SearchSettings = SearchSettings()

    @field_validator("ticker")
    @classmethod
    def valid_ticker(cls, ticker: str) -> str:
        ticker = ticker.strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,11}", ticker):
            raise ValueError("Enter a stock ticker such as NVDA, BRK-B, or SAP.DE.")
        return ticker

    @field_validator("reported_date")
    @classmethod
    def recent_past(cls, reported: date) -> date:
        today = date.today()
        if not today - timedelta(days=400) <= reported <= today:
            raise ValueError("Choose an event from the past year.")
        return reported


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
    # Scans and prices live with the saved runs, so they survive restarts and deploys.
    cache = ScanCache(runs / "cache")

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

    @app.post("/api/prices", response_model=WatchlistPrices)
    def prices(request: WatchlistRequest) -> WatchlistPrices:
        return price_history(request.tickers, days=request.days, cache=cache)

    @app.post("/api/scan", response_model=WatchlistScan)
    def scan(request: ScanRequest) -> WatchlistScan:
        try:
            return scan_watchlist(request.tickers, days=request.days, cache=cache, settings=request.settings)
        except MissingExaKey as exc:
            raise HTTPException(503, str(exc)) from None

    @app.post("/api/ask", response_model=StockAnswer)
    def ask(request: AskRequest) -> StockAnswer:
        try:
            return ask_about_stock(request.ticker, request.question, request.start_date, request.end_date,
                                   settings=request.settings, cache=cache)
        except MissingExaKey as exc:
            raise HTTPException(503, str(exc)) from None
        except AskError as exc:
            raise HTTPException(502, str(exc)) from None

    @app.post("/api/discover", response_model=Discovery)
    def discover(request: DiscoverRequest) -> Discovery:
        try:
            return discover_companies(request.query, cache=cache)
        except MissingExaKey as exc:
            raise HTTPException(503, str(exc)) from None
        except DiscoverError as exc:
            raise HTTPException(502, str(exc)) from None

    @app.post("/api/explain", response_model=EventExplanation)
    def explain(request: ExplainRequest) -> EventExplanation:
        try:
            return explain_event(request.ticker, request.reported_date, request.headline,
                                 settings=request.settings, cache=cache)
        except MissingExaKey as exc:
            raise HTTPException(503, str(exc)) from None
        except ExplainError as exc:
            raise HTTPException(502, str(exc)) from None

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
            # The CLI saves sibling UUID folders. A completed brief
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
