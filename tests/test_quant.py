import json
import math
from datetime import date, timedelta

import pytest

from financial_research.models import Evidence
from financial_research.quant import analyze_prices, main, render_quant
from financial_research.state import ResearchState


def history(symbol="A", prices=(100, 110, 99, 99), days=None, **metadata):
    days = days or [date(2025, 1, 1) + timedelta(days=i) for i in range(len(prices))]
    return Evidence(
        id=f"E-{symbol}", source_type="prices", source_name="Synthetic test history",
        source_uri=f"fixture://{symbol}", period_start=days[0], period_end=days[-1],
        content="Synthetic closing prices for calculation tests.",
        data={"symbol": symbol, "price_type": "close", "auto_adjust": False,
              "currency": "USD", "time_zone": "America/New_York",
              "prices": [{"date": day.isoformat(), "close": close} for day, close in zip(days, prices)], **metadata},
    )


def test_returns_and_sample_volatility_have_known_numeric_answers():
    result = analyze_prices([history()], window=3)
    asset = result["assets"][0]
    assert [item["return"] for item in asset["returns"]] == pytest.approx([0.1, -0.1, 0])
    assert asset["close_price_change_pct"] == pytest.approx(-1)
    assert asset["annualized_volatility_pct"] == pytest.approx(10 * math.sqrt(252))
    assert asset["is_fixture"] is True
    assert result["pairs"] == []


@pytest.mark.parametrize("prices, expected", [((100, 120, 96, 96), 1), ((100, 90, 99, 99), -1)])
def test_known_positive_and_negative_return_correlations(prices, expected):
    result = analyze_prices([history(), history("B", prices)], window=3)
    pair = result["pairs"][0]
    assert pair["matched_returns"] == 3
    assert pair["correlation"] == pytest.approx(expected)
    assert pair["rolling_correlations"][0]["correlation"] == pytest.approx(expected)


def test_missing_observations_do_not_match_different_return_intervals():
    days = [date(2025, 1, 1) + timedelta(days=i) for i in range(5)]
    a = history(prices=(100, 110, 99, 105, 109), days=days)
    b = history("B", (100, 120, 96, 100), days=[days[0], days[1], days[3], days[4]])
    pair = analyze_prices([a, b], window=3)["pairs"][0]
    assert pair["matched_returns"] == 2
    assert pair["correlation"] is None and pair["rolling_correlations"] == []


def test_future_prices_do_not_change_earlier_rolling_windows():
    first = analyze_prices([history(), history("B", (100, 120, 96, 96))], window=3)
    later = analyze_prices([history(prices=(100, 110, 99, 99, 1000)),
                            history("B", (100, 120, 96, 96, 1))], window=3)
    assert later["pairs"][0]["rolling_correlations"][:1] == first["pairs"][0]["rolling_correlations"]
    assert len(later["pairs"][0]["rolling_correlations"]) == 2


def test_constant_returns_and_short_histories_report_unavailable_values():
    result = analyze_prices([history(), history("B", (100, 100, 100, 100))], window=3)
    assert result["assets"][1]["annualized_volatility_pct"] == 0
    assert result["pairs"][0]["correlation"] is None
    assert result["pairs"][0]["rolling_correlations"][0]["correlation"] is None
    short = analyze_prices([history(prices=(100, 110))])
    assert short["assets"][0]["annualized_volatility_pct"] is None
    assert "unavailable" in render_quant(short)


@pytest.mark.parametrize("zone", [None, "Europe/London"])
def test_different_or_unknown_time_zones_are_not_silently_correlated(zone):
    pair = analyze_prices([history(), history("B", time_zone=zone)], window=3)["pairs"][0]
    assert pair["correlation"] is None and pair["rolling_correlations"] == []
    assert "time zones" in pair["note"]


@pytest.mark.parametrize("prices", [(100, 0), (100, -1), (100, float("nan")), (100, float("inf")), (100,)])
def test_invalid_prices_fail_explicitly(prices):
    with pytest.raises(ValueError):
        analyze_prices([history(prices=prices)])


def test_duplicate_dates_and_ambiguous_histories_are_rejected():
    item = history()
    item.data["prices"][1]["date"] = item.data["prices"][0]["date"]
    with pytest.raises(ValueError, match="unique dates"):
        analyze_prices([item])
    with pytest.raises(ValueError, match="multiple histories"):
        analyze_prices([history(), history()])


@pytest.mark.parametrize("changes", [{"window": 2}, {"periods_per_year": 0}])
def test_invalid_configuration_is_rejected(changes):
    with pytest.raises(ValueError):
        analyze_prices([history()], **changes)


def test_replaying_saved_state_is_deterministic_and_preserves_provenance(tmp_path):
    state = ResearchState(question="Compare test prices.", evidence=[history(), history("B", (100, 120, 96, 96))])
    path = tmp_path / "state.json"
    path.write_text(state.model_dump_json())
    output = tmp_path / "quant"
    args = [str(path), "--window", "3", "--output", str(output)]
    assert main(args) == 0 and main(args) == 0
    results = list(output.glob("*/quant.json"))
    assert len(results) == 2 and results[0].read_bytes() == results[1].read_bytes()
    analysis = json.loads(results[0].read_text())
    assert analysis["assets"][0]["evidence_id"] == "E-A"
    assert len(analysis["input_sha256"]) == 64
    assert "fixture://A" in results[0].with_suffix(".md").read_text()
    assert path.read_text() == state.model_dump_json()


def test_endpoint_only_state_fails_without_fake_quant_results(tmp_path, capsys):
    path = tmp_path / "state.json"
    path.write_text(ResearchState(question="No full prices.").model_dump_json())
    output = tmp_path / "quant"
    assert main([str(path), "--output", str(output)]) == 1
    assert not output.exists()
    assert "No full price histories" in capsys.readouterr().err
