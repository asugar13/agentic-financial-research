"""Check watchlist event scans without paid Exa or live Yahoo requests."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

exa_py = pytest.importorskip("exa_py")

from financial_research import radar
from financial_research.prices import _DailyResponse, _PriceError

START, END = date(2026, 10, 28), date(2026, 11, 27)


def grounding(index, confidence="high", url="https://investor.example.com/q3"):
    return [SimpleNamespace(field=f"events[{index}].{field}", confidence=confidence,
                            citations=[SimpleNamespace(title="Q3 results", url=url)])
            for field in ("event_date", "headline")]


def output(rows, entries=()):
    return SimpleNamespace(content={"events": rows}, grounding=list(entries))


def row(day="2026-11-17", category="earnings", headline="Revenue rose 20%.", why="Beat expectations.", scheduled=""):
    return {"reported_date": day, "scheduled_date": scheduled, "category": category, "headline": headline,
            "why_it_matters": why}


def test_event_schema_stays_within_exa_structured_output_limits():
    def count(schema, depth=0):
        properties = schema.get("properties", {})
        nested = [count(child.get("items", child), depth + 1) for child in properties.values()]
        return len(properties) + sum(total for total, _ in nested), max([depth, *(level for _, level in nested)])

    total, depth = count(radar.EVENT_SCHEMA)
    assert total <= 10 and depth <= 2


def test_rows_keep_citations_and_weakest_confidence_and_flag_scheduled_future_events():
    entries = [*grounding(0), *grounding(0, "medium", "https://news.example.com/q3"), *grounding(2)]
    rows = [
        row(),
        row("2026-10-01", headline="Old news."),
        row("2026-11-07", "product", "Launch next month.", scheduled="2026-12-05"),
        row("2026-12-02", headline="A report dated in the future."),
        row("2026-11-10", "legal", "Settled last week.", scheduled="2026-11-03"),
    ]
    events = radar.parse_events(output(rows, entries), start=START, end=END)
    assert [event.reported_date for event in events] == [date(2026, 11, 17), date(2026, 11, 10), date(2026, 11, 7)]
    earnings, settled, launch = events
    assert launch.upcoming and launch.scheduled_date == date(2026, 12, 5)
    # A "scheduled" date on or before the report adds nothing and is dropped.
    assert not settled.upcoming and settled.scheduled_date is None
    assert earnings.confidence == "medium"
    assert [source.url for source in earnings.sources] == ["https://investor.example.com/q3", "https://news.example.com/q3"]


def test_malformed_rows_are_skipped_and_unknown_categories_become_other():
    rows = [row(category="rumour"), row(day="not a date"), "not a row", row(headline="")]
    events = radar.parse_events(output(rows), start=START, end=END)
    assert len(events) == 1 and events[0].category == "other" and events[0].sources == []


def test_reaction_spans_the_close_before_to_the_first_close_after_the_event():
    stock = {date(2026, 11, 16): 100.0, date(2026, 11, 17): 99.0, date(2026, 11, 18): 96.0}
    spy = {date(2026, 11, 16): 500.0, date(2026, 11, 18): 495.0}
    reaction = radar.price_reaction(date(2026, 11, 17), stock, spy)
    assert (reaction.from_date, reaction.to_date) == (date(2026, 11, 16), date(2026, 11, 18))
    assert (reaction.stock_pct, reaction.benchmark_pct, reaction.excess_pct) == (-4.0, -1.0, -3.0)
    # A weekend event uses Friday's close and Monday's close.
    assert radar.price_reaction(date(2026, 11, 21), {date(2026, 11, 20): 10.0, date(2026, 11, 23): 11.0}, None).stock_pct == 10.0
    # The latest session has no later close yet, so the event day's own close is used.
    assert radar.price_reaction(date(2026, 11, 18), stock, None).to_date == date(2026, 11, 18)
    assert radar.price_reaction(date(2026, 11, 16), stock, None) is None


@pytest.fixture
def services(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "test-only-exa-key")
    searches = []

    class Client:
        def __init__(self, *, api_key, timeout):
            assert api_key == "test-only-exa-key"

        def search(self, query, **options):
            searches.append((query, options))
            if "Failing" in query:
                raise ValueError("HTTP 402 response body with account details")
            return SimpleNamespace(output=output([row()], grounding(0)))

    def fetch(request, symbol):
        if symbol == "NOPE":
            raise _PriceError("No prices.")
        days = [request.start_date + timedelta(days=offset) for offset in range((request.end_date - request.start_date).days + 1)]
        return _DailyResponse(prices={day: 100.0 + index for index, day in enumerate(days)},
                              name={"ACME": "Acme Corp", "FAIL": "Failing Inc"}.get(symbol))

    monkeypatch.setattr(exa_py, "Exa", Client)
    monkeypatch.setattr(radar, "_fetch", fetch)
    return searches


def test_scan_searches_by_company_name_with_dates_and_schema(services):
    scan = radar.scan_watchlist(["ACME", "NOPE"], days=30, today=END)
    acme, nope = scan.companies
    assert acme.name == "Acme Corp" and acme.events[0].reaction is not None
    # Without Yahoo data the scan still searches by ticker; events simply have no price reaction.
    assert nope.name is None and nope.events[0].reaction is None
    query, options = next(search for search in services if search[0].startswith("Acme Corp (ACME)"))
    assert options["output_schema"] == radar.EVENT_SCHEMA and options["type"] == "auto" and options["category"] == "news"
    assert options["start_published_date"].startswith("2026-10-28") and options["end_published_date"].startswith("2026-11-28")
    assert any(search[0].startswith("NOPE stock") for search in services)


def test_one_failed_company_does_not_discard_the_others_or_leak_the_provider_error(services):
    acme, failing = radar.scan_watchlist(["ACME", "FAIL"], today=END).companies
    assert acme.events and failing.events == []
    assert failing.error and "402" not in failing.error


def test_scan_requires_an_exa_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    with pytest.raises(radar.MissingExaKey):
        radar.scan_watchlist(["ACME"])


def test_price_history_needs_no_exa_key_and_reports_missing_tickers(services, monkeypatch):
    monkeypatch.delenv("EXA_API_KEY")
    acme, nope = radar.price_history(["ACME", "NOPE"], days=30, today=END).series
    assert acme.name == "Acme Corp" and len(acme.prices) == 31
    # The fake history rises by 1 a day from 100, so 30 days later it closes at 130.
    assert acme.change_pct == 30.0 and acme.error is None
    assert nope.prices == [] and nope.error
    assert services == []


@pytest.fixture
def clock(monkeypatch):
    now = {"value": datetime(2026, 11, 27, 8, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(radar, "_now", lambda: now["value"])
    return now


def test_a_fresh_cached_scan_is_shared_without_another_exa_call(services, clock, tmp_path):
    cache = radar.ScanCache(tmp_path)
    first = radar.scan_watchlist(["ACME"], today=END, cache=cache)
    searches = len(services)
    clock["value"] += timedelta(hours=11)
    second = radar.scan_watchlist(["ACME"], today=END, cache=cache)
    assert len(services) == searches
    assert second.companies == first.companies and not second.companies[0].stale


def test_a_failed_live_search_falls_back_to_the_last_good_scan(services, clock, tmp_path, monkeypatch):
    cache = radar.ScanCache(tmp_path)
    radar.scan_watchlist(["ACME"], today=END, cache=cache)
    clock["value"] += timedelta(hours=13)

    def unavailable(*args):
        raise ValueError("Exa is down")

    monkeypatch.setattr(radar, "_search", unavailable)
    company = radar.scan_watchlist(["ACME"], today=END, cache=cache).companies[0]
    assert company.stale and company.error is None and company.events


def test_failed_scans_and_unreadable_files_are_never_served(services, clock, tmp_path):
    cache = radar.ScanCache(tmp_path)
    assert radar.scan_watchlist(["FAIL"], today=END, cache=cache).companies[0].error
    assert cache.read("events", "FAIL", 30, radar.CompanyScan) is None
    broken = tmp_path / f"v{radar.CACHE_VERSION}" / "events" / "ACME-30d.json"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("{not json")
    rescanned = radar.scan_watchlist(["ACME"], today=END, cache=cache).companies[0]
    assert rescanned.events and not rescanned.stale


def test_prices_are_reused_briefly_and_fall_back_when_yahoo_fails(services, clock, tmp_path, monkeypatch):
    cache = radar.ScanCache(tmp_path)
    live = radar.price_history(["ACME"], today=END, cache=cache).series[0]

    def unavailable(request, symbol):
        raise _PriceError("Yahoo is down")

    monkeypatch.setattr(radar, "_fetch", unavailable)
    clock["value"] += timedelta(minutes=29)
    reused = radar.price_history(["ACME"], today=END, cache=cache).series[0]
    assert reused == live and not reused.stale
    clock["value"] += timedelta(minutes=2)
    fallback = radar.price_history(["ACME"], today=END, cache=cache).series[0]
    assert fallback.stale and fallback.prices == live.prices


def test_unsafe_tickers_never_become_cache_paths(tmp_path):
    cache = radar.ScanCache(tmp_path)
    cache.write("events", "../escape", 30, radar.CompanyScan(ticker="X"))
    assert cache.read("events", "../escape", 30, radar.CompanyScan) is None
    assert not any(tmp_path.parent.glob("escape*"))


def test_search_settings_reach_exa_and_defaults_leave_the_request_unchanged(services):
    radar.scan_watchlist(["ACME"], today=END)
    _, default = services[-1]
    assert default["num_results"] == 10 and "include_domains" not in default and "exclude_domains" not in default
    settings = radar.SearchSettings(focus="AI chips", include_domains=["reuters.com"], exclude_domains=["fool.com"],
                                    num_results=25)
    scan = radar.scan_watchlist(["ACME"], today=END, settings=settings)
    query, options = services[-1]
    assert query.endswith("Focus on: AI chips")
    assert options["include_domains"] == ["reuters.com"] and options["exclude_domains"] == ["fool.com"]
    assert options["num_results"] == 25 and scan.settings == settings


def test_each_settings_combination_has_its_own_cache_copy(services, clock, tmp_path):
    cache = radar.ScanCache(tmp_path)
    radar.scan_watchlist(["ACME"], today=END, cache=cache)
    searches = len(services)
    focused = radar.SearchSettings(focus="AI chips")
    radar.scan_watchlist(["ACME"], today=END, cache=cache, settings=focused)
    assert len(services) == searches + 1
    radar.scan_watchlist(["ACME"], today=END, cache=cache, settings=focused)
    radar.scan_watchlist(["ACME"], today=END, cache=cache)
    assert len(services) == searches + 1
    files = sorted(path.name for path in (tmp_path / f"v{radar.CACHE_VERSION}" / "events").iterdir())
    assert files == ["ACME-30d-" + focused.cache_variant() + ".json", "ACME-30d.json"]
