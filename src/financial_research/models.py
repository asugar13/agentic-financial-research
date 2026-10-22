"""Validated boundaries: model decisions, tool results, evidence, and reports."""

from datetime import date, datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, field_validator, model_validator

Text = Annotated[str, Field(min_length=1)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ResearchPlan(Model):
    objectives: list[Text] = Field(min_length=1, max_length=5)
    assumptions: list[Text]


class ReasonedRequest(Model):
    rationale: Text = Field(description="One short decision explanation, not private reasoning.")


class PriceRequest(ReasonedRequest):
    symbols: list[Text] = Field(min_length=1, max_length=5)


class HistoricalPriceRequest(PriceRequest):
    """Standalone real-data request; dates are inclusive."""

    start_date: date
    end_date: date

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, symbols: list[str]) -> list[str]:
        return list(dict.fromkeys(symbol.upper() for symbol in symbols))

    @model_validator(mode="after")
    def dates_are_ordered(self) -> "HistoricalPriceRequest":
        if self.start_date >= self.end_date:
            raise ValueError("start_date must be earlier than end_date.")
        return self


class MacroRequest(ReasonedRequest):
    series_id: Text


class NewsRequest(ReasonedRequest):
    query: Text


class PriceAction(PriceRequest):
    tool: Literal["get_market_prices"] = "get_market_prices"
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def dates_are_paired(self) -> "PriceAction":
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("Provide both start_date and end_date, or neither for fixture data.")
        if self.start_date is not None and self.start_date >= self.end_date:
            raise ValueError("start_date must be earlier than end_date.")
        return self


class MacroAction(MacroRequest):
    tool: Literal["get_macro_series"] = "get_macro_series"


class NewsAction(NewsRequest):
    tool: Literal["search_news"] = "search_news"


class FinishAction(ReasonedRequest):
    tool: Literal["finish_research"] = "finish_research"


Action = Annotated[
    PriceAction | MacroAction | NewsAction | FinishAction,
    Field(discriminator="tool"),
]
ACTION_ADAPTER = TypeAdapter(Action)


class Observation(Model):
    source_type: Literal["prices", "macro", "news"]
    source_name: Text
    source_uri: Text
    period_start: date
    period_end: date
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    content: Text
    data: dict[str, JsonValue] = Field(default_factory=dict)
    is_fixture: bool = True


class Evidence(Observation):
    id: Text


class ToolResult(Model):
    observations: list[Observation] = Field(default_factory=list)
    error: Text | None = None

    @model_validator(mode="after")
    def error_has_no_evidence(self) -> "ToolResult":
        if self.error and self.observations:
            raise ValueError("A failed tool call cannot also supply observations.")
        return self


class ToolCall(Model):
    action: Action
    result: ToolResult


class Claim(Model):
    statement: Text
    kind: Literal["observation", "inference"]
    evidence_ids: list[Text] = Field(min_length=1)


class ResearchReport(Model):
    title: Text
    claims: list[Claim]
    limitations: list[Text] = Field(min_length=1)
