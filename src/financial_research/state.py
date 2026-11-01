"""The complete research snapshot passed between sequential graph nodes."""

import json

from pydantic import Field

from financial_research.models import (
    Action,
    Evidence,
    HistoricalPriceRequest,
    Model,
    ReportCritique,
    ResearchPlan,
    ResearchReport,
    Text,
    ToolCall,
)


class ResearchState(Model):
    question: Text
    price_request: HistoricalPriceRequest | None = None
    max_tool_calls: int = Field(default=4, ge=0, le=10)
    plan: ResearchPlan | None = None
    action: Action | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    decisions: list[Action] = Field(default_factory=list)
    stop_reason: str | None = None
    draft_report: ResearchReport | None = None
    critique: ReportCritique | None = None
    report: ResearchReport | None = None
    events: list[str] = Field(default_factory=list)

    def model_context(self) -> str:
        """Send summaries to the model; preserve full source and calculation series in saved state."""
        context = self.model_dump(mode="json", include={
            "question", "price_request", "max_tool_calls", "plan", "evidence", "tool_calls", "stop_reason",
        })
        if self.draft_report is not None:
            context["draft_report"] = self.draft_report.model_dump(mode="json")
        if self.critique is not None:
            context["critique"] = self.critique.model_dump(mode="json")
        observations = list(context["evidence"])
        for call in context["tool_calls"]:
            observations.extend(call["result"]["observations"])
        for item in observations:
            item["data"].pop("prices", None)
            item["data"].pop("values", None)
            if item["source_type"] == "quant":
                for asset in item["data"]["assets"]:
                    asset.pop("returns", None)
                for pair in item["data"]["pairs"]:
                    pair.pop("rolling_correlations", None)
        return json.dumps(context)
