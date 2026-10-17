"""The complete research snapshot passed between sequential graph nodes."""

from pydantic import Field

from financial_research.models import (
    Action,
    Evidence,
    Model,
    ResearchPlan,
    ResearchReport,
    Text,
    ToolCall,
)


class ResearchState(Model):
    question: Text
    max_tool_calls: int = Field(default=4, ge=0, le=10)
    plan: ResearchPlan | None = None
    action: Action | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    decisions: list[Action] = Field(default_factory=list)
    stop_reason: str | None = None
    report: ResearchReport | None = None
    events: list[str] = Field(default_factory=list)
