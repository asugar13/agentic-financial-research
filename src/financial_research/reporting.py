"""Citation integrity and deterministic Markdown rendering."""

from financial_research.models import ResearchReport
from financial_research.state import ResearchState


def validate_citations(report: ResearchReport, state: ResearchState) -> None:
    known = {item.id for item in state.evidence}
    cited = {key for claim in report.claims for key in claim.evidence_ids}
    if unknown := cited - known:
        raise ValueError(f"Report cites unknown evidence: {', '.join(sorted(unknown))}")


def render_report(state: ResearchState) -> str:
    if state.report is None:
        raise ValueError("Cannot render a run without a report.")
    report = state.report
    validate_citations(report, state)
    data_notice = (
        "> REAL DATA MODE: Yahoo Finance prices and FRED macro data when retrieved. News research is unavailable."
        if state.price_request is not None else
        "> SYNTHETIC FIXTURE DATA — an orchestration demonstration, not market research."
    )
    lines = [
        f"# {report.title}", "",
        data_notice,
        "", f"Question: {state.question}", "",
        f"Stop reason: {state.stop_reason}", "", "## Findings", "",
    ]
    for claim in report.claims:
        citations = " ".join(f"[{key}]" for key in claim.evidence_ids)
        lines.append(f"- **{claim.kind.capitalize()}:** {claim.statement} {citations}")
    if not report.claims:
        lines.append("No supported findings were produced.")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {note}" for note in report.limitations)
    lines.extend(["", "## Collected sources", ""])
    for item in state.evidence:
        lines.extend([
            f"- [{item.id}] {item.source_name} — `{item.source_uri}` "
            f"({item.period_start} to {item.period_end}; retrieved {item.retrieved_at.isoformat()})",
            f"  {item.content}",
        ])
    return "\n".join(lines) + "\n"
