"""Check the real-price boundary using provider-shaped DataFrames, without network."""

import json

import pandas as pd
import pytest
from pydantic import ValidationError

from financial_research import prices
from financial_research.models import HistoricalPriceRequest


def _request(**changes):
    return HistoricalPriceRequest.model_validate({
        "symbols": ["AAPL"], "start_date": "2025-01-04", "end_date": "2025-01-08",
        "rationale": "Compare prices over an explicit date range.", **changes,
    })


def _history():
    # Unsorted, with rows outside the requested range and a different Adj Close.
    return pd.DataFrame({"Close": [120, 90, 105, 110, 100], "Adj Close": [60, 45, 52.5, 55, 50]},
                        index=pd.to_datetime(["2025-01-08", "2025-01-02", "2025-01-07", "2025-01-06", "2025-01-03"]))


def _server(monkeypatch, history=None, metadata=None, error=None):
    calls = []

    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, **kwargs):
            calls.append({"symbol": self.symbol, **kwargs})
            if error:
                raise error
            return history if history is not None else _history()

        def get_history_metadata(self):
            return metadata if metadata is not None else {
                "symbol": self.symbol, "currency": "USD", "exchangeTimezoneName": "America/New_York",
            }

    monkeypatch.setattr(prices.yf, "Ticker", Ticker)
    return calls


def test_multiple_symbols_dates_calculation_and_provenance(monkeypatch):
    calls = _server(monkeypatch)
    result = prices.get_market_prices(_request(symbols=[" aapl ", "SPY", "aapl"]))
    assert result.error is None
    assert [call["symbol"] for call in calls] == ["AAPL", "SPY"]
    assert all(call["start"] == "2025-01-04" and call["end"] == "2025-01-09" for call in calls)
    assert all(call["interval"] == "1d" and call["auto_adjust"] is False for call in calls)
    assert len(result.observations) == 2
    item = result.observations[0]
    assert item.is_fixture is False
    assert item.period_start.isoformat() == "2025-01-06"
    assert item.period_end.isoformat() == "2025-01-08"
    assert item.data["price_change_pct"] == pytest.approx((120 / 110 - 1) * 100)
    assert item.data["prices"] == [
        {"date": "2025-01-06", "close": 110},
        {"date": "2025-01-07", "close": 105},
        {"date": "2025-01-08", "close": 120},
    ]
    assert item.data["currency"] == "USD"
    assert item.data["time_zone"] == "America/New_York"
    assert item.data["price_type"] == "close"
    assert "not a total return" in item.content
    assert item.retrieved_at.tzinfo is not None
    assert item.source_uri == "https://finance.yahoo.com/quote/AAPL/history/"


def test_multi_year_history_is_not_capped_at_100_rows(monkeypatch):
    dates = pd.bdate_range("2020-01-01", "2024-12-31")
    _server(monkeypatch, pd.DataFrame({"Close": range(1, len(dates) + 1)}, index=dates))
    result = prices.get_market_prices(_request(start_date="2020-01-01", end_date="2024-12-31"))
    assert result.error is None
    assert len(result.observations[0].data["prices"]) == len(dates) > 1000


def test_price_decrease_keeps_its_sign(monkeypatch):
    _server(monkeypatch)
    result = prices.get_market_prices(_request(start_date="2025-01-06", end_date="2025-01-07"))
    assert result.observations[0].data["price_change_pct"] == pytest.approx((105 / 110 - 1) * 100)


def test_shorter_coverage_is_explicit_without_filling_prices(monkeypatch):
    _server(monkeypatch)
    result = prices.get_market_prices(_request(start_date="2020-01-01", end_date="2025-01-10"))
    item = result.observations[0]
    assert item.period_start.isoformat() == "2025-01-02"
    assert item.period_end.isoformat() == "2025-01-08"
    assert item.data["requested_start"] == "2020-01-01"
    assert "no missing prices are filled" in item.data["coverage_note"]
    assert len(item.data["prices"]) == 5


@pytest.mark.parametrize("currency", ["GBp", None])
def test_currency_units_are_preserved_and_missing_currency_is_not_assumed(monkeypatch, currency):
    _server(monkeypatch, metadata={"currency": currency})
    result = prices.get_market_prices(_request())
    assert result.observations[0].data["currency"] == currency


@pytest.mark.parametrize("start, end", [("2025-01-04", "2025-01-05"), ("2025-01-05", "2025-01-06")])
def test_fewer_than_two_prices_cannot_produce_a_return(monkeypatch, start, end):
    _server(monkeypatch)
    result = prices.get_market_prices(_request(start_date=start, end_date=end))
    assert result.observations == []
    assert "fewer than two" in result.error


@pytest.mark.parametrize("close", [0, -1, float("nan"), float("inf"), "bad-data"])
def test_invalid_prices_are_rejected(monkeypatch, close):
    history = _history().astype(object)
    history.loc[pd.Timestamp("2025-01-06"), "Close"] = close
    _server(monkeypatch, history)
    result = prices.get_market_prices(_request())
    assert result.observations == []
    assert "malformed" in result.error


@pytest.mark.parametrize("history, message", [
    (pd.DataFrame(), "no daily prices"),
    (_history().drop(columns="Close"), "no Close column"),
    (pd.concat([_history(), _history()]), "duplicate"),
])
def test_empty_or_malformed_history_returns_no_evidence(monkeypatch, history, message):
    _server(monkeypatch, history)
    result = prices.get_market_prices(_request())
    assert result.observations == []
    assert message in result.error


@pytest.mark.parametrize("error", [TimeoutError(), ConnectionError(), RuntimeError("private session URL")])
def test_provider_failures_are_structured_and_do_not_echo_transport_details(monkeypatch, error):
    _server(monkeypatch, error=error)
    result = prices.get_market_prices(_request())
    assert "connection or rate limit" in result.error
    assert result.observations == []
    assert "private session" not in result.error


def test_batch_failure_discards_partial_results(monkeypatch):
    _server(monkeypatch, metadata={"symbol": "AAPL"})
    result = prices.get_market_prices(_request(symbols=["AAPL", "UNKNOWN"]))
    assert result.observations == []
    assert "different symbol" in result.error


@pytest.mark.parametrize("changes", [
    {"symbols": []}, {"symbols": ["   "]}, {"symbols": ["A", "B", "C", "D", "E", "F"]},
    {"start_date": "2025-01-09"}, {"start_date": "2025-01-08"},
])
def test_invalid_requests_fail_validation(changes):
    with pytest.raises(ValidationError):
        _request(**changes)


def test_standalone_cli_outputs_real_data_without_api_key(monkeypatch, capsys):
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    _server(monkeypatch)
    assert prices.main(["--symbols", "AAPL", "--start-date", "2025-01-04", "--end-date", "2025-01-08"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["observations"][0]["is_fixture"] is False
    assert result["error"] is None


def test_standalone_cli_reports_failure_with_nonzero_exit(monkeypatch, capsys):
    _server(monkeypatch, pd.DataFrame())
    assert prices.main(["--symbols", "AAPL", "--start-date", "2025-01-04", "--end-date", "2025-01-08"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]
