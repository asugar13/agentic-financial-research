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

    def scan(tickers, *, days, cache, settings):
        assert isinstance(cache, web.ScanCache)
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


def test_prices_endpoint_shares_watchlist_validation(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(web, "price_history", lambda tickers, *, days, cache: calls.append((tickers, days)) or
                        web.WatchlistPrices(start_date="2026-10-28", end_date="2026-11-27", series=[]))
    with TestClient(web.create_app(tmp_path)) as client:
        assert client.post("/api/prices", json={"tickers": ["aapl"], "days": 90}).status_code == 200
        assert calls == [(["AAPL"], 90)]
        assert client.post("/api/prices", json={"tickers": ["NVDA; rm"]}).status_code == 422


def test_scan_forwards_validated_search_settings(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(web, "scan_watchlist", lambda tickers, *, days, cache, settings: seen.append(settings) or
                        web.WatchlistScan(start_date="2026-10-28", end_date="2026-11-27", benchmark="SPY", companies=[]))
    with TestClient(web.create_app(tmp_path)) as client:
        body = {"tickers": ["NVDA"], "settings": {"focus": "AI chips", "include_domains": ["https://Reuters.com/"],
                                                  "num_results": 25}}
        assert client.post("/api/scan", json=body).status_code == 200
        assert seen[0].include_domains == ["reuters.com"] and seen[0].num_results == 25
        for settings in ({"num_results": 15}, {"exclude_domains": ["not a domain"]}, {"focus": "x" * 201}):
            assert client.post("/api/scan", json={"tickers": ["NVDA"], "settings": settings}).status_code == 422
        # Prices ignore search settings, so sending them is a client error.
        assert client.post("/api/prices", json=body).status_code == 422


def test_explain_validates_events_and_maps_failures(tmp_path, monkeypatch):
    from datetime import date, timedelta

    calls = []

    def explain(ticker, reported, headline, *, settings, cache):
        calls.append((ticker, reported, headline, settings.focus))
        if ticker == "NOKEY":
            raise web.MissingExaKey("Set EXA_API_KEY on the server before researching events.")
        if ticker == "DOWN":
            raise web.ExplainError("Exa could not research this event right now; try again shortly.")
        raise AssertionError("Only failure paths are exercised here.")

    monkeypatch.setattr(web, "explain_event", explain)
    today = date.today()
    event = {"reported_date": (today - timedelta(days=3)).isoformat(), "headline": "The CEO returned."}
    with TestClient(web.create_app(tmp_path)) as client:
        assert client.post("/api/explain", json={**event, "ticker": "NOKEY"}).status_code == 503
        failed = client.post("/api/explain", json={**event, "ticker": "down", "settings": {"focus": "governance"}})
        assert failed.status_code == 502 and calls[-1] == ("DOWN", today - timedelta(days=3), "The CEO returned.", "governance")
        for invalid in ({**event, "ticker": "NVDA; rm"}, {**event, "ticker": "NVDA", "headline": ""},
                        {**event, "ticker": "NVDA", "headline": "x" * 501},
                        {**event, "ticker": "NVDA", "reported_date": (today + timedelta(days=1)).isoformat()}):
            assert client.post("/api/explain", json=invalid).status_code == 422


def test_discover_validates_queries_and_maps_failures(tmp_path, monkeypatch):
    def search(query, *, cache):
        if query == "no key":
            raise web.MissingExaKey("Set EXA_API_KEY on the server before searching companies.")
        raise web.DiscoverError("Exa could not search companies right now; try again shortly.")

    monkeypatch.setattr(web, "discover_companies", search)
    with TestClient(web.create_app(tmp_path)) as client:
        assert client.post("/api/discover", json={"query": "no key"}).status_code == 503
        assert client.post("/api/discover", json={"query": "chip makers"}).status_code == 502
        for invalid in ({"query": ""}, {"query": "x"}, {"query": "x" * 201}, {}):
            assert client.post("/api/discover", json=invalid).status_code == 422
