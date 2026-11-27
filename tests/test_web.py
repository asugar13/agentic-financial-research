import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID, uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from financial_research import web
from financial_research.research_model import DEMO_QUESTION

DEMO = {"mode": "demo", "question": DEMO_QUESTION}


def test_demo_run_returns_report_with_sources_and_survives_restart(tmp_path):
    with TestClient(web.create_app(tmp_path)) as client:
        response = client.post("/api/runs", json=DEMO)
        assert response.status_code == 202
        path = "/api/runs/" + response.json()["id"]
        run = client.get(path).json()
        ids = {source["id"] for source in run["sources"]}
        assert run["status"] == "succeeded" and run["events"] and ids
        assert all(set(claim["evidence_ids"]) <= ids for claim in run["report"]["claims"])

    with TestClient(web.create_app(tmp_path)) as restarted:
        assert restarted.get(path).json() == run
    assert len(list((tmp_path / "react").iterdir())) == 1

    # A CLI run has the same artifacts in the parent folder.
    directory = tmp_path / "react" / UUID(run["id"]).hex
    directory.rename(tmp_path / directory.name)
    with TestClient(web.create_app(tmp_path)) as client:
        assert client.get(path).json() == run


def test_active_run_can_be_polled_and_blocks_overlapping_submissions(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    run_id = uuid4()
    monkeypatch.setattr(web, "uuid4", lambda: run_id)

    def delayed_model(mode):
        entered.set()
        assert release.wait(10)
        return web.DemoModel()

    monkeypatch.setattr(web, "_model", delayed_model)
    with TestClient(web.create_app(tmp_path)) as client, ThreadPoolExecutor() as executor:
        first = executor.submit(client.post, "/api/runs", json=DEMO)
        try:
            assert entered.wait(10)
            assert client.get(f"/api/runs/{run_id}").json()["status"] == "running"
            assert client.post("/api/runs", json=DEMO).status_code == 409
        finally:
            release.set()
        assert first.result().status_code == 202
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "succeeded"


def test_real_scope_and_hosted_provider_restrictions(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_HOSTED", "1")
    with TestClient(web.create_app(tmp_path)) as client:
        assert client.get("/api/config").json()["modes"] == ["bedrock", "demo"]
        for invalid in ({"mode": "ollama", "question": "Test?"}, {"mode": "bedrock", "question": "Test?"},
                        {"mode": "demo", "question": "Something else?"}):
            assert client.post("/api/runs", json=invalid).status_code == 422
        assert client.get(f"/api/runs/{uuid4()}").status_code == 404


def test_failed_provider_setup_and_interrupted_runs_remain_readable(tmp_path, monkeypatch):
    def unavailable(mode):
        raise RuntimeError("private provider diagnostics")

    monkeypatch.setattr(web, "_model", unavailable)
    with TestClient(web.create_app(tmp_path)) as client:
        run_id = client.post("/api/runs", json=DEMO).json()["id"]
        run = client.get(f"/api/runs/{run_id}").json()
        assert run["status"] == "failed" and run["report"] is None
        assert "provider setup" in run["error"] and "private" not in run["error"]
        metadata = tmp_path / "react" / UUID(run_id).hex / "run.json"
        metadata.write_text(json.dumps({"status": "running", "mode": "demo", "question": DEMO_QUESTION}))
        interrupted = client.get(f"/api/runs/{run_id}").json()
        assert interrupted["status"] == "failed" and "restarted" in interrupted["error"]


def test_scan_validates_tickers_and_reports_a_missing_exa_key(tmp_path, monkeypatch):
    calls = []

    def scan(tickers, *, days):
        calls.append((tickers, days))
        if "NOKEY" in tickers:
            raise web.MissingExaKey("Set EXA_API_KEY on the server before scanning.")
        return web.WatchlistScan(start_date="2026-10-28", end_date="2026-11-27", benchmark="SPY", companies=[])

    monkeypatch.setattr(web, "scan_watchlist", scan)
    with TestClient(web.create_app(tmp_path)) as client:
        assert client.post("/api/scan", json={"tickers": [" nvda", "NVDA", "brk-b"], "days": 7}).status_code == 200
        assert calls == [(["NVDA", "BRK-B"], 7)]
        for invalid in ({"tickers": []}, {"tickers": ["NVDA; rm"]}, {"tickers": ["A"] * 16}, {"tickers": ["NVDA"], "days": 91}):
            assert client.post("/api/scan", json=invalid).status_code == 422
        missing = client.post("/api/scan", json={"tickers": ["NOKEY"]})
        assert missing.status_code == 503 and "EXA_API_KEY" in missing.json()["detail"]
