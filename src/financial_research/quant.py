"""Deterministic price analysis, reproducible offline from a saved research state."""

import argparse
import hashlib
import json
import math
import statistics
import sys
from collections.abc import Sequence
from datetime import date
from itertools import combinations, pairwise
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from pydantic import Field, TypeAdapter

from financial_research.models import Evidence, Model, Observation, QuantRequest, ToolResult
from financial_research.state import ResearchState


class _Price(Model):
    date: date
    close: Annotated[float, Field(gt=0, allow_inf_nan=False)]


_PRICES = TypeAdapter(list[_Price])


def _finite(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("Price history produced a non-finite statistic.")
    return value


def _correlation(left: list[float], right: list[float]) -> float | None:
    # Two points give a misleading perfect correlation; near-constant returns
    # have undefined correlation at the precision used here.
    if len(left) < 3 or any(max(values) - min(values) < 1e-12 for values in (left, right)):
        return None
    return _finite(statistics.correlation(left, right))


def analyze_prices(evidence: Sequence[Evidence], *, window: int = 20, periods_per_year: int = 252) -> dict[str, Any]:
    if window < 3 or periods_per_year < 1:
        raise ValueError("window must be at least 3 and periods-per-year must be positive.")
    sources = [item for item in evidence if item.source_type == "prices" and "prices" in item.data]
    assets, histories = [], {}
    for item in sources:
        symbol = item.data.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("Price evidence must identify its symbol.")
        if item.data.get("price_type") != "close" or item.data.get("auto_adjust") is not False:
            raise ValueError(f"{symbol}: expected unadjusted provider Close history.")
        points = sorted(_PRICES.validate_python(item.data["prices"]), key=lambda point: point.date)
        if len(points) < 2 or len({point.date for point in points}) != len(points):
            raise ValueError(f"{symbol}: need at least two prices with unique dates.")
        if points[0].date != item.period_start or points[-1].date != item.period_end:
            raise ValueError(f"{symbol}: price dates do not match evidence coverage.")
        if symbol in histories:
            raise ValueError(f"{symbol}: multiple histories are ambiguous; select one history per symbol.")
        returns = {(a.date.isoformat(), b.date.isoformat()): _finite(b.close / a.close - 1)
                   for a, b in pairwise(points)}
        values = list(returns.values())
        histories[symbol] = (returns, item)
        assets.append({
            "symbol": symbol, "evidence_id": item.id, "source_uri": item.source_uri,
            "retrieved_at": item.retrieved_at.isoformat(), "is_fixture": item.is_fixture,
            "currency": item.data.get("currency"), "time_zone": item.data.get("time_zone"),
            "start_date": points[0].date.isoformat(), "end_date": points[-1].date.isoformat(),
            "observations": len(points), "return_count": len(values),
            "close_price_change_pct": _finite((points[-1].close / points[0].close - 1) * 100),
            "annualized_volatility_pct": _finite(statistics.stdev(values) * math.sqrt(periods_per_year) * 100)
                if len(values) >= 2 else None,
            "returns": [{"start_date": start, "end_date": end, "return": value}
                        for (start, end), value in returns.items()],
        })
    pairs = []
    for left, right in combinations(histories, 2):
        left_returns, left_source = histories[left]
        right_returns, right_source = histories[right]
        # Match the complete return interval, not just its end date. A missing
        # observation must not turn a two-day return into a one-day comparison.
        intervals = sorted(left_returns.keys() & right_returns.keys())
        compatible = bool(left_source.data.get("time_zone")) and (
            left_source.data.get("time_zone") == right_source.data.get("time_zone"))
        x, y = [left_returns[key] for key in intervals], [right_returns[key] for key in intervals]
        rolling = [{"start_date": intervals[end - window][0], "end_date": intervals[end - 1][1],
                    "correlation": _correlation(x[end - window:end], y[end - window:end])}
                   for end in range(window, len(intervals) + 1)] if compatible else []
        pairs.append({
            "symbols": [left, right], "evidence_ids": [left_source.id, right_source.id],
            "matched_returns": len(intervals),
            "start_date": intervals[0][0] if intervals else None,
            "end_date": intervals[-1][1] if intervals else None,
            "correlation": _correlation(x, y) if compatible else None,
            "rolling_correlations": rolling,
            "note": "Uses matching observed return intervals; no prices are filled."
                if compatible else "Correlation unavailable: exchange time zones differ or are unknown.",
        })
    encoded = json.dumps([item.model_dump(mode="json") for item in sources], sort_keys=True).encode()
    return {
        "method_version": 1, "input_sha256": hashlib.sha256(encoded).hexdigest(),
        "window": window, "periods_per_year": periods_per_year, "assets": assets, "pairs": pairs,
        "limitations": [
            "Close-price returns exclude dividends and are not total returns or strategy performance.",
            "Returns use consecutive observed closes. Missing sessions are not filled; gaps can create multi-session returns.",
            f"Volatility is sample standard deviation of observed returns times sqrt({periods_per_year}); this annualization assumes equally spaced trading sessions and is approximate when sessions are missing.",
            "Pairwise correlations match both interval dates and require equal known exchange time zones; closing times may still differ.",
            "Rolling windows use trailing matched returns only. Correlation is descriptive and does not establish causation or predict returns.",
            "Null volatility means fewer than two returns. Null correlation means fewer than three matched returns, near-constant returns, or incompatible time zones. Empty rolling output means insufficient coverage or incompatible time zones.",
            "Provider history may be revised. Replaying saved evidence reproduces this calculation, not a point-in-time backtest.",
        ],
    }


def analyze_price_evidence(request: QuantRequest, evidence: Sequence[Evidence]) -> ToolResult:
    """Compute from stored histories, then return citable evidence to the research loop."""
    if len(set(request.evidence_ids)) != len(request.evidence_ids):
        return ToolResult(error="Select each price evidence ID only once.")
    by_id = {item.id: item for item in evidence}
    unknown = set(request.evidence_ids) - by_id.keys()
    if unknown:
        return ToolResult(error=f"Unknown price evidence IDs: {', '.join(sorted(unknown))}. Fetch prices first.")
    selected = [by_id[key] for key in request.evidence_ids]
    if any(item.source_type != "prices" or "prices" not in item.data for item in selected):
        return ToolResult(error="Analysis requires full daily price histories; endpoint summaries and macro/news evidence cannot be used.")
    try:
        analysis = analyze_prices(selected, window=request.window)
    except (ValueError, OverflowError) as exc:
        return ToolResult(error=f"Price analysis failed: {exc}")
    analysis["input_evidence_ids"] = request.evidence_ids
    for pair in analysis["pairs"]:
        rolling = pair["rolling_correlations"]
        pair["first_rolling_correlation"] = rolling[0] if rolling else None
        pair["latest_rolling_correlation"] = rolling[-1] if rolling else None
    return ToolResult(observations=[Observation(
        source_type="quant", source_name="Python price analysis",
        source_uri=(f"quant://prices/{analysis['input_sha256']}"
                    f"?method_version={analysis['method_version']}&window={request.window}"
                    f"&periods_per_year={analysis['periods_per_year']}"),
        period_start=min(item.period_start for item in selected),
        period_end=max(item.period_end for item in selected),
        content=(f"Calculated close-price changes, annualized volatility, and return correlations "
                 f"from {', '.join(request.evidence_ids)} using a {request.window}-return trailing window. "
                 "See data for numerical results, actual coverage, and limitations; null means unavailable."),
        data=analysis, is_fixture=any(item.is_fixture for item in selected),
    )])


def render_quant(analysis: dict[str, Any]) -> str:
    def number(value):
        return "unavailable" if value is None else f"{value:.4f}"

    lines = ["# Quantitative price analysis", "", f"Input SHA-256: `{analysis['input_sha256']}`", "",
             "| Symbol | Observed dates | Close change (%) | Annualized volatility (%) | Evidence |",
             "| --- | --- | --- | --- | --- |"]
    for asset in analysis["assets"]:
        lines.append(f"| {asset['symbol']} | {asset['start_date']} to {asset['end_date']} | "
                     f"{number(asset['close_price_change_pct'])} | {number(asset['annualized_volatility_pct'])} | {asset['evidence_id']} |")
    lines.extend(["", f"## Correlations ({analysis['window']}-return rolling window)", ""])
    for pair in analysis["pairs"]:
        latest = pair["rolling_correlations"][-1] if pair["rolling_correlations"] else None
        lines.append(f"- {' / '.join(pair['symbols'])}: {pair['matched_returns']} matching returns; "
                     f"full-sample correlation {number(pair['correlation'])}; latest rolling correlation "
                     f"{number(latest['correlation'] if latest else None)}"
                     f"{(' ending ' + latest['end_date']) if latest else ''}. {pair['note']}")
    if not analysis["pairs"]:
        lines.append("At least two price histories are needed for pairwise correlation.")
    lines.extend(["", "## Sources", ""])
    for asset in analysis["assets"]:
        lines.append(f"- {asset['evidence_id']}: {asset['source_uri']} (retrieved {asset['retrieved_at']}; synthetic: {asset['is_fixture']}).")
    lines.extend(["", "## Method and limitations", "", *[f"- {note}" for note in analysis["limitations"]]])
    return "\n".join(lines) + "\n"


def save_quant(analysis: dict[str, Any], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "quant.json").write_text(json.dumps(analysis, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (output / "quant.md").write_text(render_quant(analysis), encoding="utf-8")


# Secondary utility: replay saved prices with different settings, without fetching
# data or calling an LLM. Normal research selects this calculation through a tool.
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path, help="Saved state.json containing full daily price history")
    parser.add_argument("--window", type=int, default=20)
    parser.add_argument("--periods-per-year", type=int, default=252)
    parser.add_argument("--output", type=Path, default=Path("runs/quant"))
    args = parser.parse_args(argv)
    try:
        state = ResearchState.model_validate_json(args.state.read_text(encoding="utf-8"))
        analysis = analyze_prices(state.evidence, window=args.window, periods_per_year=args.periods_per_year)
        if not analysis["assets"]:
            raise ValueError("No full price histories in this state; endpoint-only fixtures and macro series cannot be used.")
        output = args.output / uuid4().hex
        save_quant(analysis, output)
    except (ValueError, OSError, OverflowError) as exc:
        print(f"Quant analysis failed: {exc}", file=sys.stderr)
        return 1
    print(f"Quant report: {(output / 'quant.md').resolve()}")
    print(f"Quant data: {(output / 'quant.json').resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
