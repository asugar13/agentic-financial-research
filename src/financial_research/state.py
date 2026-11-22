"""The complete research snapshot passed between sequential graph nodes."""

import json

from pydantic import Field

from financial_research.models import (
    Action,
    Evidence,
    HistoricalPriceRequest,
    Model,
    Observation,
    ReportCritique,
    ResearchPlan,
    ResearchReport,
    Text,
    ToolCall,
)

NEWS_EXCERPT_LIMIT = 1000


def _observation_context(observation: Observation) -> dict:
    """Return a compact copy for the LLM; keep the observation in state unchanged."""
    item = observation.model_dump(mode="json")
    data = item["data"]
    data.pop("prices", None)
    data.pop("values", None)

    if observation.source_type == "news" and not observation.is_fixture:
        # The action already carries the query and objective; content carries the excerpt.
        for field in ("highlights", "query", "objective"):
            data.pop(field, None)
        if len(item["content"]) > NEWS_EXCERPT_LIMIT:
            item["content"] = item["content"][:NEWS_EXCERPT_LIMIT] + " [excerpt shortened]"

    if observation.source_type == "quant":
        for asset in data["assets"]:
            asset.pop("returns", None)
        for pair in data["pairs"]:
            pair.pop("rolling_correlations", None)
    return item


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
        """Build the LLM input separately from the full saved state."""
        context = self.model_dump(mode="json", include={
            "question", "price_request", "max_tool_calls", "plan", "stop_reason",
        })
        context["evidence"] = [_observation_context(item) for item in self.evidence]

        # Match URL and content so different excerpts from the same page remain distinct.
        news_ids = {
            (item.source_uri, item.content): item.id
            for item in self.evidence if item.source_type == "news" and not item.is_fixture
        }
        context["tool_calls"] = []
        for call in self.tool_calls:
            observations = []
            for item in call.result.observations:
                evidence_id = None
                if item.source_type == "news" and not item.is_fixture:
                    evidence_id = news_ids.get((item.source_uri, item.content))
                if evidence_id is not None:
                    # The excerpt is already in evidence; tool history only needs its ID.
                    observations.append({"evidence_id": evidence_id})
                else:
                    observations.append(_observation_context(item))

            call_context = call.model_dump(mode="json")
            call_context["result"]["observations"] = observations
            context["tool_calls"].append(call_context)

        if self.draft_report is not None:
            context["draft_report"] = self.draft_report.model_dump(mode="json")
        if self.critique is not None:
            context["critique"] = self.critique.model_dump(mode="json")
        return json.dumps(context)
