import json
import os
import re
from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from agents.base import AgentResult, ToolCallRecord
from agents.investigator import run_investigator
from agents.reviewer import run_reviewer
from orchestrator.output import (
    write_dispute_packet_md,
    write_reasoning_trace_json,
    write_verdict_json,
)

MCP_SERVER_SCRIPT = Path(__file__).resolve().parent.parent / "mcp_server" / "server.py"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

Verdict = Literal["VALID", "INVALID", "ESCALATE"]
ReviewerVerdict = Literal["CONFIRM", "OVERTURN", "ESCALATE"]


class PipelineError(RuntimeError):
    """Raised when the Investigator or Reviewer cannot produce a valid, schema-conformant output."""


class PoSummary(BaseModel):
    ordered_qty_each: float
    shipped_qty_each: float
    received_qty_each: float
    invoiced_qty_each: float


class TimelineEvent(BaseModel):
    event: str
    date: str
    valid: bool


class CaseFile(BaseModel):
    claim_id: str
    po_summary: PoSummary
    timeline: list[TimelineEvent]
    proposed_verdict: Verdict
    confidence: float
    uom_conversions_applied: list[str] = []
    prior_claims: list[str] = []
    trade_agreement_found: bool = False
    discrepancy_qty: float = 0
    discrepancy_amount_cents: int = 0
    reasoning: str = ""


class ReviewFindings(BaseModel):
    uom_check: Literal["PASS", "FAIL", "N/A"] = "N/A"
    split_shipment_check: Literal["PASS", "FAIL", "N/A"] = "N/A"
    timeline_check: Literal["PASS", "FAIL", "N/A"] = "N/A"
    trade_agreement_check: Literal["PASS", "FAIL", "N/A"] = "N/A"
    duplicate_check: Literal["PASS", "FAIL", "N/A"] = "N/A"
    substitution_check: Literal["PASS", "FAIL", "N/A"] = "N/A"


class ReviewerOutput(BaseModel):
    claim_id: str
    investigator_verdict: str
    review_findings: ReviewFindings
    final_verdict: ReviewerVerdict
    confidence: float
    dispute_grounds: list[str] = []
    reasoning: str = ""


@dataclass
class PipelineResult:
    claim_id: str
    case_file: CaseFile
    reviewer_output: ReviewerOutput
    investigator_verdict: str
    reviewer_verdict: str
    final_verdict: str
    confidence: float
    output_dir: Path


REQUIRED_TOOL_CALLS: dict[str, Callable[[list[ToolCallRecord]], bool]] = {
    "s02": lambda trace: any(r.name == "normalize_uom" and not r.is_error for r in trace),
    "s03": lambda trace: any(
        r.name == "get_asns_for_po" and not r.is_error and len(json.loads(r.result)) >= 2
        for r in trace
    ),
    "s06": lambda trace: any(r.name == "get_trade_agreement" and not r.is_error for r in trace),
    "s07": lambda trace: any(r.name == "list_claims_for_po" and not r.is_error for r in trace),
    "s08": lambda trace: any(r.name == "list_claims_for_po" and not r.is_error for r in trace),
}


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)


def _find_balanced_json_object(text: str) -> str | None:
    """Return the first brace-balanced {...} substring, ignoring any trailing text after it.

    Tracks nesting depth rather than assuming the last '}' in the text closes the object —
    trailing prose after the JSON (e.g. "... } — let me know if you have questions.") can
    contain stray braces that would otherwise get swept into the result.
    """
    depth = 0
    start = None
    for i, char in enumerate(text):
        if char == "{":
            if depth == 0:
                start = i
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _extract_json(text: str) -> str:
    stripped = text.strip()

    fence_match = _JSON_FENCE_RE.search(stripped)
    if fence_match:
        return fence_match.group(1).strip()

    # Some models reason in prose before the JSON object with no code fence at all.
    balanced = _find_balanced_json_object(stripped)
    if balanced is not None:
        return balanced

    return stripped


def _required_tool_call_check(scenario: str) -> Callable[[list[ToolCallRecord]], bool] | None:
    return REQUIRED_TOOL_CALLS.get(scenario[:3])


async def _run_investigator_until_valid(
    *,
    openai_client: Any,
    mcp_client: Any,
    claim_id: str,
    scenario: str,
    max_attempts: int,
) -> tuple[AgentResult, CaseFile]:
    correction: str | None = None
    last_error = ""
    check = _required_tool_call_check(scenario)

    for _ in range(max_attempts):
        result = await run_investigator(
            openai_client=openai_client,
            mcp_client=mcp_client,
            claim_id=claim_id,
            extra_instructions=correction,
        )

        try:
            case_file = CaseFile.model_validate(json.loads(_extract_json(result.final_text)))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)
            correction = (
                "Your previous response could not be parsed as a valid CaseFile: "
                f"{last_error}. Respond again with ONLY the complete CaseFile JSON object "
                "(no markdown fences, no prose), including every required field: claim_id, "
                "po_summary (all four sub-fields), timeline, proposed_verdict, confidence."
            )
            continue

        if check is not None and not check(result.trace):
            last_error = f"required tool call for scenario {scenario!r} is missing from the trace"
            correction = (
                "Your investigation is incomplete: you did not make the tool call required to "
                "detect this claim's discrepancy. Re-investigate using the full tool-call "
                "protocol from your instructions, then respond again with the complete "
                "CaseFile JSON."
            )
            continue

        return result, case_file

    raise PipelineError(
        f"Investigator failed to produce a valid CaseFile for {claim_id} after "
        f"{max_attempts} attempts: {last_error}"
    )


def _resolve_final_verdict(investigator_verdict: str, reviewer_verdict: str) -> str:
    if reviewer_verdict == "ESCALATE":
        return "ESCALATE"
    if reviewer_verdict == "CONFIRM":
        return investigator_verdict
    if reviewer_verdict == "OVERTURN":
        if investigator_verdict == "VALID":
            return "INVALID"
        if investigator_verdict == "INVALID":
            return "VALID"
        return "ESCALATE"
    raise PipelineError(f"unknown reviewer_verdict: {reviewer_verdict!r}")


async def run_pipeline(
    *,
    claim_id: str,
    scenario: str,
    openai_client: Any | None = None,
    mcp_client: Any | None = None,
    output_dir: str | Path = "outputs",
    max_investigator_attempts: int = 3,
) -> PipelineResult:
    output_dir = Path(output_dir)

    async with AsyncExitStack() as stack:
        if openai_client is None:
            from openai import AsyncOpenAI

            openai_client = AsyncOpenAI(
                base_url=OPENROUTER_BASE_URL,
                api_key=os.environ["OPENROUTER_API_KEY"],
            )

        if mcp_client is None:
            from fastmcp import Client
            from fastmcp.client.transports import PythonStdioTransport

            transport = PythonStdioTransport(
                script_path=MCP_SERVER_SCRIPT,
                env={**os.environ, "SCENARIO_ID": scenario},
            )
            mcp_client = await stack.enter_async_context(Client(transport))

        investigator_result, case_file = await _run_investigator_until_valid(
            openai_client=openai_client,
            mcp_client=mcp_client,
            claim_id=claim_id,
            scenario=scenario,
            max_attempts=max_investigator_attempts,
        )

        stripped_case_file = {
            key: value for key, value in case_file.model_dump().items() if key != "reasoning"
        }
        reviewer_result = await run_reviewer(
            openai_client=openai_client,
            mcp_client=mcp_client,
            case_file=stripped_case_file,
        )

        try:
            reviewer_output = ReviewerOutput.model_validate(
                json.loads(_extract_json(reviewer_result.final_text))
            )
        except (json.JSONDecodeError, ValidationError) as exc:
            raise PipelineError(
                f"Reviewer failed to produce a valid verdict for {claim_id}: {exc}"
            ) from exc

        final_verdict = _resolve_final_verdict(case_file.proposed_verdict, reviewer_output.final_verdict)
        timestamp = datetime.now(timezone.utc).isoformat()

        write_verdict_json(
            output_dir,
            claim_id=claim_id,
            investigator_verdict=case_file.proposed_verdict,
            reviewer_verdict=reviewer_output.final_verdict,
            final_verdict=final_verdict,
            confidence=reviewer_output.confidence,
            timestamp=timestamp,
        )
        write_reasoning_trace_json(
            output_dir,
            claim_id=claim_id,
            investigator_messages=investigator_result.messages,
            reviewer_messages=reviewer_result.messages,
        )
        if final_verdict == "INVALID":
            write_dispute_packet_md(
                output_dir,
                claim_id=claim_id,
                case_file=case_file,
                reviewer_output=reviewer_output,
            )

        return PipelineResult(
            claim_id=claim_id,
            case_file=case_file,
            reviewer_output=reviewer_output,
            investigator_verdict=case_file.proposed_verdict,
            reviewer_verdict=reviewer_output.final_verdict,
            final_verdict=final_verdict,
            confidence=reviewer_output.confidence,
            output_dir=output_dir,
        )
