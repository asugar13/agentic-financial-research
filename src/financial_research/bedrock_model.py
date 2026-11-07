"""Amazon Bedrock adapter using the AWS credential chain and Converse API."""

import json
import os
from typing import Any, TypeVar

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel

from financial_research.models import ACTION_ADAPTER, Action, ReportCritique, ResearchPlan, ResearchReport
from financial_research.research_model import REVIEW_TASK, REVISION_TASK, ModelError, instructions, tool_definitions
from financial_research.state import ResearchState

T = TypeVar("T", bound=BaseModel)
DEFAULT_MODEL = "qwen.qwen3-32b-v1:0"
DEFAULT_REGION = "eu-north-1"


def _schema_for_bedrock(value: Any) -> Any:
    """Remove unsupported grammar constraints; Pydantic still enforces them locally."""
    if isinstance(value, list):
        return [_schema_for_bedrock(item) for item in value]
    if not isinstance(value, dict):
        return value
    # Bedrock supports only a subset of JSON Schema. Simplify the outgoing
    # schema without changing the Pydantic model; _structured() validates the
    # returned JSON against that original model and all its constraints.
    unsupported = {"minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum",
                   "exclusiveMaximum", "multipleOf", "minItems", "maxItems", "default"}
    result = {}
    for key, item in value.items():
        if key in {"properties", "$defs", "definitions"}:
            result[key] = {name: _schema_for_bedrock(shape) for name, shape in item.items()}
        elif key not in unsupported:
            result[key] = _schema_for_bedrock(item)
    if result.get("type") == "object":
        result["additionalProperties"] = False
        result["required"] = list(result.get("properties", {}))
    return result


class BedrockModel:
    def __init__(self, model: str | None = None, *, region: str | None = None, client: Any = None) -> None:
        self.model = model or os.environ.get("BEDROCK_MODEL") or DEFAULT_MODEL
        self.region = region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or DEFAULT_REGION
        self.client = client
        self.usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "responses_without_usage": 0}

    def _request(self, state: ResearchState, task: str, **options: Any) -> dict:
        self.usage["requests"] += 1
        try:
            # Delay client creation until the graph starts, so credential failures
            # retain the same initial state and diagnostics as other run failures.
            if self.client is None:
                self.client = boto3.Session().client(
                    "bedrock-runtime", region_name=self.region,
                    config=Config(connect_timeout=10, read_timeout=120,
                                  retries={"mode": "adaptive", "total_max_attempts": 2}),
                )
            response = self.client.converse(
                modelId=self.model,
                system=[{"text": f"{instructions(state)}\nTask: {task}\n/no_think"}],
                messages=[{"role": "user", "content": [{"text": state.model_context()}]}],
                # Cap generated output per request, separately from the input context.
                # Start with 4,096 tokens for the short JSON plans/reports we request.
                # A max_tokens stop is rejected below because the JSON may be incomplete.
                inferenceConfig={"maxTokens": 4096, "temperature": 0},
                **options,
            )
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            raise ModelError(
                f"Bedrock request failed ({code}); check model access, Region, and AWS credentials."
            ) from exc
        except BotoCoreError as exc:
            raise ModelError(
                f"Bedrock connection failed ({type(exc).__name__}); check AWS_PROFILE and renew aws login if expired."
            ) from exc
        usage = response.get("usage", {})
        if "inputTokens" in usage and "outputTokens" in usage:
            self.usage["input_tokens"] += usage["inputTokens"]
            self.usage["output_tokens"] += usage["outputTokens"]
        else:
            self.usage["responses_without_usage"] += 1
        if response.get("stopReason") not in {"end_turn", "tool_use"}:
            raise ModelError(f"Bedrock response did not complete normally: {response.get('stopReason')}.")
        return response

    def _structured(self, state: ResearchState, task: str, schema: type[T]) -> T:
        shape = _schema_for_bedrock(schema.model_json_schema())
        response = self._request(state, task, outputConfig={"textFormat": {
            "type": "json_schema", "structure": {"jsonSchema": {
                "name": schema.__name__, "schema": json.dumps(shape),
            }},
        }})
        blocks = response.get("output", {}).get("message", {}).get("content", [])
        if response["stopReason"] != "end_turn" or any("toolUse" in block for block in blocks):
            raise ModelError("Expected a completed Bedrock JSON response, not a tool call.")
        return schema.model_validate_json("".join(block.get("text", "") for block in blocks))

    def make_plan(self, state: ResearchState) -> ResearchPlan:
        return self._structured(state, "Create a small evidence-gathering plan. State source/date assumptions.", ResearchPlan)

    def choose_action(self, state: ResearchState) -> Action:
        tools = [{"toolSpec": {
            "name": name, "description": description,
            "inputSchema": {"json": schema.model_json_schema()},
        }} for name, schema, description in tool_definitions(state)]
        response = self._request(
            state,
            "Assess remaining evidence gaps. Call exactly ONE tool, including finish_research to stop. "
            "Do not return ordinary text or call multiple tools. "
            "Do not repeat an identical completed or failed call. "
            "Stop if the configured source scope cannot answer the question.",
            toolConfig={"tools": tools, "toolChoice": {"any": {}}},
        )
        calls = [block["toolUse"] for block in response.get("output", {}).get("message", {}).get("content", [])
                 if "toolUse" in block]
        if response["stopReason"] != "tool_use" or len(calls) != 1:
            raise ModelError("Expected exactly one Bedrock tool call.")
        call = calls[0]
        if not isinstance(call.get("input"), dict):
            raise ModelError("Bedrock tool arguments must be a JSON object.")
        return ACTION_ADAPTER.validate_python({**call["input"], "tool": call["name"]})

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

    def review_report(self, state: ResearchState) -> ReportCritique:
        return self._structured(state, REVIEW_TASK, ReportCritique)

    def revise_report(self, state: ResearchState) -> ResearchReport:
        return self._structured(state, REVISION_TASK, ResearchReport)
