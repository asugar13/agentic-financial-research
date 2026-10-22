"""Local Ollama adapter: schema-constrained JSON and native tool calls, no SDK."""

import json
from typing import Any, TypeVar
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field, JsonValue

from financial_research.models import ACTION_ADAPTER, Action, ResearchPlan, ResearchReport
from financial_research.research_model import ModelError, instructions, tool_definitions
from financial_research.state import ResearchState

T = TypeVar("T", bound=BaseModel)
URL = "http://127.0.0.1:11434/api/chat"


class _Function(BaseModel):
    name: str
    arguments: dict[str, JsonValue]


class _ToolCall(BaseModel):
    function: _Function


class _Message(BaseModel):
    content: str = ""
    tool_calls: list[_ToolCall] = Field(default_factory=list)


class _Response(BaseModel):
    message: _Message
    done: bool
    done_reason: str


class OllamaModel:
    def __init__(self, model: str = "qwen2.5:7b") -> None:
        self.model = model

    def _chat(self, state: ResearchState, task: str, **options: Any) -> _Message:
        context = state.model_context()
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": f"{instructions(state)}\nTask: {task}"},
                {"role": "user", "content": context},
            ],
            "stream": False,
            "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 2048},
            **options,
        }
        request = Request(URL, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=120) as response:
                result = _Response.model_validate_json(response.read())
        except HTTPError as exc:
            raise ModelError(
                f"Ollama returned HTTP {exc.code}; check that {self.model!r} appears in 'ollama list'."
            ) from exc
        except TimeoutError as exc:
            raise ModelError("Ollama request timed out; try a smaller installed model.") from exc
        except URLError as exc:
            raise ModelError(f"Cannot reach Ollama at {URL}; start Ollama or run 'ollama serve'.") from exc
        if not result.done or result.done_reason != "stop":
            raise ModelError(f"Ollama response did not complete normally: {result.done_reason}.")
        return result.message

    def _structured(self, state: ResearchState, task: str, schema: type[T]) -> T:
        shape = schema.model_json_schema()
        message = self._chat(
            state, f"{task}\nReturn only JSON matching this schema: {json.dumps(shape)}", format=shape,
        )
        return schema.model_validate_json(message.content)

    def make_plan(self, state: ResearchState) -> ResearchPlan:
        return self._structured(
            state, "Create a small evidence-gathering plan. State source/date assumptions.", ResearchPlan,
        )

    def choose_action(self, state: ResearchState) -> Action:
        tools = [
            {"type": "function", "function": {
                "name": name, "description": description, "parameters": schema.model_json_schema(),
            }}
            for name, schema, description in tool_definitions(state)
        ]
        message = self._chat(
            state,
            "Assess remaining evidence gaps. Call exactly ONE tool, including finish_research to stop. "
            "Do not return ordinary text or call multiple tools. "
            "Do not repeat an identical completed or failed call. "
            "Stop if the configured source scope cannot answer the question.",
            tools=tools,
        )
        if len(message.tool_calls) != 1:
            raise ModelError("Expected exactly one Ollama tool call.")
        call = message.tool_calls[0].function
        return ACTION_ADAPTER.validate_python({**call.arguments, "tool": call.name})

    def write_report(self, state: ResearchState) -> ResearchReport:
        return self._structured(
            state,
            "Write a short report. Every claim must cite collected evidence IDs. "
            "Label inferences. Include counterevidence when collected. "
            "Limitations are methodological/data-availability caveats, not uncited facts. "
            "If no evidence was collected, return no claims and explain why. "
            "Acknowledge budget exhaustion, tool failures, and the configured source limitations.",
            ResearchReport,
        )
