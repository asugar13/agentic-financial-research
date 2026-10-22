"""The complete research snapshot passed between sequential graph nodes."""

import json

from pydantic import Field

from financial_research.models import (
    Action,
    Evidence,
    HistoricalPriceRequest,
    Model,
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
    report: ResearchReport | None = None
    events: list[str] = Field(default_factory=list)

    def model_context(self) -> str:
        """Send summaries to the model; preserve full daily series in saved state."""
        context = self.model_dump(mode="json", include={
            "question", "price_request", "max_tool_calls", "plan", "evidence", "tool_calls", "stop_reason",
        })
        observations = list(context["evidence"])
        for call in context["tool_calls"]:
            observations.extend(call["result"]["observations"])
        for item in observations:
            item["data"].pop("prices", None)
        return json.dumps(context)
