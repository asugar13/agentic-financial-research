"""Retrieve Exa web excerpts and normalize them into citable research observations."""

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ValidationError
from requests.exceptions import RequestException

from financial_research.models import Observation, ToolResult, WebSearchRequest


def exa_api_key() -> str | None:
    """Return the Exa key; the environment takes precedence over the local .env file."""
    key = os.environ.get("EXA_API_KEY")
    if not key:
        try:
            from dotenv import dotenv_values
        except ImportError:
            return None
        key = dotenv_values(Path.cwd() / ".env", interpolate=False).get("EXA_API_KEY")
    return key.strip() if key and key.strip() else None



def _observation(item, request: WebSearchRequest, start: date | None, end: date | None) -> Observation | None:
    """Keep dated HTTP sources with usable excerpts; never invent missing publication dates."""
    try:
        published = datetime.fromisoformat(item.published_date.replace("Z", "+00:00"))
        published = published.replace(tzinfo=timezone.utc) if published.tzinfo is None else published.astimezone(timezone.utc)
        day = published.date()
        url = urlsplit(item.url)
        highlights = [text.strip() for text in (item.highlights or []) if text.strip()]
        if url.scheme not in {"http", "https"} or not url.hostname or not item.title or not highlights:
            return None
        if start is not None and not start <= day <= end:
            return None
        return Observation(
            source_type="news", source_name=f"Exa / {url.hostname}", source_uri=item.url,
            period_start=day, period_end=day, is_fixture=False,
            content=f"{item.title} ({day}). Retrieved excerpts: " + "\n".join(highlights),
            data={
                "title": item.title, "published_date": item.published_date, "highlights": highlights,
                "query": request.query, "objective": request.objective,
                "coverage_note": "Excerpts, not full articles. Publication dates are provider estimates; "
                                 "undated pages and pages without excerpts are excluded. Search is not exhaustive.",
            },
        )
    except (AttributeError, TypeError, ValueError):
        return None


def search_news(
    request: WebSearchRequest, *, start_date: date | None = None, end_date: date | None = None,
) -> ToolResult:
    """Search with the official SDK; the environment takes precedence over the local .env file."""
    if (start_date is None) != (end_date is None) or (start_date is not None and start_date >= end_date):
        return ToolResult(error="Provide both news dates with start-date earlier than end-date, or neither.")
    try:
        from exa_py import Exa
    except ImportError:
        return ToolResult(error="Install Exa support with: python -m pip install -e '.[news]'")

    api_key = exa_api_key()
    if not api_key:
        return ToolResult(error="Set EXA_API_KEY in the environment or the local .env file before searching.")

    # Only a user-selected bounded interval warrants hard publication-date filters.
    options = {}
    if start_date is not None:
        options = {
            "start_published_date": datetime.combine(start_date, time.min, timezone.utc).isoformat(),
            "end_published_date": datetime.combine(end_date + timedelta(days=1), time.min, timezone.utc).isoformat(),
        }
    try:
        result = Exa(api_key=api_key, timeout=20).search(
            request.query, objective=request.objective, contents={"highlights": True}, **options,
        )
        observations = []
        seen = set()
        for item in result.results:
            observation = _observation(item, request, start_date, end_date)
            if observation is not None and observation.source_uri not in seen:
                observations.append(observation)
                seen.add(observation.source_uri)
        if result.results and not observations:
            return ToolResult(error="Exa returned no usable dated excerpts within the requested interval.")
        return ToolResult(observations=observations)
    except RequestException:
        return ToolResult(error="Exa connection failed or timed out; try again later.")
    except (ValueError, TypeError, AttributeError):
        # SDK error messages can include response bodies. Keep them out of saved state and prompts.
        return ToolResult(error="Exa search failed; check the API key, credits, rate limits, and request parameters.")


# Optional CLI for testing Exa directly and printing observations; the UI and graph call search_news() instead.
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Search Exa directly and print citable source excerpts.")
    parser.add_argument("query")
    parser.add_argument("--objective", required=True)
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    args = parser.parse_args(argv)
    try:
        request = WebSearchRequest(query=args.query, objective=args.objective, rationale="Inspect a standalone Exa search.")
    except ValidationError:
        parser.error("Provide a nonempty query and objective of at most 4096 characters.")
    result = search_news(request, start_date=args.start_date, end_date=args.end_date)
    print(result.model_dump_json(indent=2))
    return 1 if result.error else 0


if __name__ == "__main__":
    sys.exit(main())
