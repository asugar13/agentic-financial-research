"""Exercise FRED-shaped responses without live network requests or credentials."""

import io
import json
from datetime import date, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import ValidationError

from financial_research import macro
from financial_research.models import HistoricalMacroRequest


def _request(**changes):
    return HistoricalMacroRequest.model_validate({
        "series_id": " dfii10 ", "start_date": "2025-01-04", "end_date": "2025-01-08",
        "rationale": "Inspect real yields over the selected interval.", **changes,
    })


def _metadata(**changes):
    return {"seriess": [{
        "id": "DFII10", "title": "10-Year Real Treasury Yield", "units": "Percent",
        "frequency": "Daily", "seasonal_adjustment": "Not Seasonally Adjusted",
        "last_updated": "2025-01-09 15:15:00-06", **changes,
    }]}


def _values(rows=None, **changes):
    rows = rows if rows is not None else [
        {"date": "2025-01-08", "value": "2.0"}, {"date": "2025-01-02", "value": "2.3"},
        {"date": "2025-01-07", "value": "."}, {"date": "2025-01-06", "value": "2.2"},
    ]
    return {
        "observations": rows, "count": len(rows), "offset": 0,
        "realtime_start": "2025-01-09", "realtime_end": "2025-01-09", **changes,
    }


def _server(monkeypatch, metadata=None, values=None):
    calls = []
    monkeypatch.setenv("FRED_API_KEY", "test-secret&value")

    def respond(request, timeout):
        url = urlparse(request.full_url)
        calls.append((url.path, parse_qs(url.query)))
        assert timeout == 20
        payload = (
            metadata if metadata is not None else _metadata()
        ) if url.path.endswith("/series") else (values if values is not None else _values())
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(macro, "urlopen", respond)
    return calls


def test_dates_missing_values_units_and_provenance(monkeypatch):
    calls = _server(monkeypatch)
    result = macro.get_macro_series(_request())
    assert result.error is None
    assert len(calls) == 2
    assert calls[1][1]["observation_start"] == ["2025-01-04"]
    assert calls[1][1]["observation_end"] == ["2025-01-08"]
    assert calls[1][1]["units"] == ["lin"]
    assert all(query["series_id"] == ["DFII10"] for _, query in calls)
    assert all(query["api_key"] == ["test-secret&value"] for _, query in calls)
    item = result.observations[0]
    assert item.is_fixture is False
    assert item.data["series_id"] == "DFII10"
    assert item.period_start == date(2025, 1, 6) and item.period_end == date(2025, 1, 8)
    assert item.data["change"] == -0.2 and item.data["change_unit"] == "percentage points"
    assert item.data["observation_count"] == 2 and item.data["missing_count"] == 1
    assert item.data["values"] == [
        {"date": "2025-01-06", "value": 2.2}, {"date": "2025-01-07", "value": None},
        {"date": "2025-01-08", "value": 2.0},
    ]
    assert "No missing observations are filled" in item.data["coverage_note"]
    assert item.data["realtime_start"] == "2025-01-09"
    assert "not an as-of backtest dataset" in item.content
    assert item.source_uri == "https://fred.stlouisfed.org/series/DFII10"
    assert item.retrieved_at.tzinfo is not None
    assert "test-secret" not in result.model_dump_json() and "api_key" not in result.model_dump_json()


@pytest.mark.parametrize("series_id, unit, frequency", [
    ("UNRATE", "Percent", "Monthly"), ("CPIAUCSL", "Index 1982-1984=100", "Monthly"),
])
def test_other_series_keep_their_native_units_and_frequency(monkeypatch, series_id, unit, frequency):
    _server(monkeypatch, metadata=_metadata(id=series_id, units=unit, frequency=frequency), values=_values([
        {"date": "2025-01-01", "value": "100"}, {"date": "2025-02-01", "value": "101"},
    ]))
    result = macro.get_macro_series(_request(series_id=series_id, start_date="2025-01-01", end_date="2025-02-01"))
    assert result.error is None
    data = result.observations[0].data
    assert data["unit"] == unit and data["frequency"] == frequency
    assert data["change"] == 1
    assert data["change_unit"] == ("percentage points" if unit == "Percent" else unit)
    assert data["coverage_note"] is None


def test_negative_and_zero_rates_are_valid(monkeypatch):
    _server(monkeypatch, values=_values([
        {"date": "2025-01-06", "value": "-1.0"}, {"date": "2025-01-08", "value": "0"},
    ]))
    result = macro.get_macro_series(_request())
    assert result.error is None and result.observations[0].data["change"] == 1


def test_long_history_is_not_silently_capped_at_100_observations(monkeypatch):
    rows = [{"date": (date(2020, 1, 1) + timedelta(days=i)).isoformat(), "value": str(i)} for i in range(1000)]
    _server(monkeypatch, values=_values(rows))
    result = macro.get_macro_series(_request(start_date="2020-01-01", end_date="2024-12-31"))
    assert result.error is None and result.observations[0].data["observation_count"] == 1000


@pytest.mark.parametrize("rows", [[], [{"date": "2025-01-06", "value": "2.2"}], [
    {"date": "2025-01-06", "value": "."}, {"date": "2025-01-08", "value": "2.0"},
]])
def test_insufficient_numeric_observations_return_no_evidence(monkeypatch, rows):
    _server(monkeypatch, values=_values(rows))
    result = macro.get_macro_series(_request())
    assert result.observations == [] and "fewer than two" in result.error


@pytest.mark.parametrize("value", ["NaN", "Infinity", "not-a-number"])
def test_malformed_numeric_values_are_rejected(monkeypatch, value):
    _server(monkeypatch, values=_values([{"date": "2025-01-06", "value": value}]))
    result = macro.get_macro_series(_request())
    assert result.observations == [] and "malformed" in result.error


@pytest.mark.parametrize("metadata, values, message", [
    (_metadata(id="OTHER"), _values(), "different series"),
    ({"seriess": []}, _values(), "malformed"),
    (_metadata(), _values(count=100), "incomplete history"),
    (_metadata(), _values(offset=1), "incomplete history"),
    (_metadata(), _values([{"date": "2025-01-06", "value": "2"}] * 2), "duplicate"),
    ({"error_code": 400, "error_message": "private request with test-secret&value"}, _values(), "rejected"),
])
def test_provider_errors_and_incomplete_or_duplicate_data_are_explicit(monkeypatch, metadata, values, message):
    _server(monkeypatch, metadata, values)
    result = macro.get_macro_series(_request())
    assert result.observations == [] and message in result.error
    assert "test-secret" not in result.model_dump_json()


@pytest.mark.parametrize("error", [
    URLError("private request URL"), TimeoutError(),
    HTTPError("https://example.invalid/?api_key=secret", 400, "Bad Request", {}, None),
    HTTPError("https://example.invalid/?api_key=secret", 429, "Rate Limit", {}, None),
    HTTPError("https://example.invalid/?api_key=secret", 503, "Unavailable", {}, None),
])
def test_transport_errors_do_not_echo_credentials_or_urls(monkeypatch, error):
    _server(monkeypatch)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(macro, "urlopen", fail)
    result = macro.get_macro_series(_request())
    assert result.observations == [] and result.error
    assert "secret" not in result.error and "private" not in result.error


def test_invalid_json_is_a_tool_failure(monkeypatch):
    _server(monkeypatch)
    monkeypatch.setattr(macro, "urlopen", lambda *args, **kwargs: io.BytesIO(b"<html>Unavailable</html>"))
    assert macro.get_macro_series(_request()).error == "FRED returned invalid JSON."


def test_missing_key_does_not_make_a_network_request(monkeypatch):
    calls = _server(monkeypatch)
    monkeypatch.delenv("FRED_API_KEY")
    assert "FRED_API_KEY" in macro.get_macro_series(_request()).error
    assert calls == []


@pytest.mark.parametrize("changes", [{"series_id": " "}, {"start_date": "bad-date"}, {"end_date": "2025-01-04"}])
def test_invalid_requests_are_rejected(changes):
    with pytest.raises(ValidationError):
        _request(**changes)


def test_standalone_cli_outputs_real_observations(monkeypatch, capsys):
    _server(monkeypatch)
    assert macro.main(["--series-id", "DFII10", "--start-date", "2025-01-04", "--end-date", "2025-01-08"]) == 0
    assert json.loads(capsys.readouterr().out)["observations"][0]["is_fixture"] is False


def test_standalone_cli_failure_has_nonzero_exit(monkeypatch, capsys):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    assert macro.main(["--series-id", "DFII10", "--start-date", "2025-01-04", "--end-date", "2025-01-08"]) == 1
    assert "FRED_API_KEY" in json.loads(capsys.readouterr().out)["error"]
