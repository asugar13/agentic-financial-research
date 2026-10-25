"""Optional provider adapter: native function calls and structured outputs."""

import json
from typing import Any, TypeVar

from openai import OpenAI, OpenAIError, pydantic_function_tool
from openai.types.responses import ParsedResponse
from pydantic import BaseModel

from financial_research.models import (
    ACTION_ADAPTER,
    Action,
    ReportCritique,
    ResearchPlan,
    ResearchReport,
)
from financial_research.research_model import REVIEW_TASK, REVISION_TASK, ModelError, instructions, tool_definitions
from financial_research.state import ResearchState

T = TypeVar("T", bound=BaseModel)


class OpenAIModel:
    def __init__(self, model: str, client: OpenAI | None = None) -> None:
        self.model = model
        self.client = client if client is not None else OpenAI(timeout=30.0, max_retries=1)

    def _request(self, state: ResearchState, task: str, **options: Any) -> ParsedResponse[Any]:
        # Deliberately send bounded research state, not an ever-growing conversation.
        context = state.model_context()
        try:
            response = self.client.responses.parse(
                model=self.model,
                instructions=f"{instructions(state)}\nTask: {task}",
                input=context,
                store=False,
                max_output_tokens=4096,
                **options,
            )
        except OpenAIError as exc:
            raise ModelError(
                f"OpenAI request failed ({type(exc).__name__}); check credentials, network, and model access."
            ) from exc
        if response.status != "completed":
            raise ModelError(f"Model response did not complete: {response.status}.")
        return response

    def _structured(self, state: ResearchState, task: str, schema: type[T]) -> T:
        response = self._request(state, task, text_format=schema)
        if not isinstance(response.output_parsed, schema):
            raise ModelError("Model returned no valid structured output (it may have refused).")
        return response.output_parsed

    def make_plan(self, state: ResearchState) -> ResearchPlan:
        return self._structured(
            state, "Create a small evidence-gathering plan. State source/date assumptions.", ResearchPlan,
        )

    def choose_action(self, state: ResearchState) -> Action:
        # The SDK helper emits Chat Completions' nested tool shape; Responses uses a flat shape.
        tools = [
            {"type": "function", **pydantic_function_tool(schema, name=name, description=description)["function"]}
            for name, schema, description in tool_definitions(state)
        ]
        response = self._request(
            state,
            "Assess remaining evidence gaps and call exactly one tool, or finish_research. "
            "Do not repeat an identical completed or failed call. "
            "Stop if the configured source scope cannot answer the question.",
            tools=tools, tool_choice="required", parallel_tool_calls=False,
        )
        calls = [item for item in response.output if item.type == "function_call"]
        if len(calls) != 1:
            raise ModelError("Expected exactly one typed function call.")
        arguments = json.loads(calls[0].arguments)
        if not isinstance(arguments, dict):
            raise ModelError("Function arguments must be an object.")
        return ACTION_ADAPTER.validate_python({**arguments, "tool": calls[0].name})

    def write_report(self, state: ResearchState) -> ResearchReport:
        return self._structured(
            state,
            "Write a short report. Every claim must cite collected evidence IDs. "
            "Label inferences. Include counterevidence when collected. "
            "Limitations are methodological/data-availability caveats, not a place for uncited facts. "
            "If no evidence was collected, return no claims and explain why. "
            "Acknowledge budget exhaustion, tool failures, and the configured source limitations.",
            ResearchReport,
        )

    def review_report(self, state: ResearchState) -> ReportCritique:
        return self._structured(state, REVIEW_TASK, ReportCritique)

    def revise_report(self, state: ResearchState) -> ResearchReport:
        return self._structured(state, REVISION_TASK, ResearchReport)
