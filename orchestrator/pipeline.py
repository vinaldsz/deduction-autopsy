import json
import logging
import os
import re
from collections.abc import Callable, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from agents.base import AgentResult, TokenUsage, ToolCallRecord
from agents.investigator import run_investigator
from agents.reviewer import run_reviewer
from mcp_server.db import DEFAULT_DB_PATH
from orchestrator.completeness import Requirement, data_gaps, required_tool_calls, unmet
from orchestrator.config import SETTINGS
from orchestrator.output import (
    make_run_id,
    prepare_run_dir,
    write_case_file_json,
    write_dispute_packet_md,
    write_reasoning_trace_json,
    write_verdict_json,
)
from orchestrator.resolutions import write_claim_resolution

logger = logging.getLogger(__name__)

MCP_SERVER_SCRIPT = Path(__file__).resolve().parent.parent / "mcp_server" / "server.py"
OPENROUTER_BASE_URL = SETTINGS.openrouter_base_url


def _safe_for_log(text: Any, limit: int = 300) -> str:
    """Neutralize untrusted text (model output / fixture notes) before it enters a log message.

    Validation-error strings embed raw model output derived from fixture notes/retailer_notes
    — the same prompt-injection surface CLAUDE.md wraps in <case_file> delimiters. Collapsing
    newlines/control chars stops a crafted value from forging a second log line, and the cap
    bounds log volume.
    """
    collapsed = " ".join(str(text).split())
    return collapsed[:limit] + ("…" if len(collapsed) > limit else "")

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
    # Declared last so it renders last in the UI's check chips, which iterate this model's fields.
    # Defaulted like the rest, so a Reviewer that omits it still validates.
    data_completeness_check: Literal["PASS", "FAIL", "N/A"] = "N/A"


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
    run_id: str
    run_dir: Path
    usage: dict


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


def strip_reasoning(case_file: CaseFile) -> dict[str, Any]:
    """The CaseFile view the Reviewer actually receives (its own narrative reasoning removed)."""
    return {key: value for key, value in case_file.model_dump().items() if key != "reasoning"}


async def _run_investigator_until_valid(
    *,
    openai_client: Any,
    mcp_client: Any,
    claim_id: str,
    max_attempts: int,
    requirements: list[Requirement],
    on_tool_call: Callable[[ToolCallRecord], None] | None = None,
) -> tuple[AgentResult, CaseFile, TokenUsage, list[str]]:
    """Run the Investigator until it returns a schema-valid, complete CaseFile.

    Returns the trailing `list[str]` of requirements still unmet when attempts ran out — empty on
    success. A parse failure means no CaseFile exists at all and still raises; an *incomplete* one
    means we have a valid CaseFile and simply couldn't finish the investigation, which is a
    human-review outcome rather than a crash.
    """
    correction: str | None = None
    last_error = ""
    total_usage = TokenUsage()
    incomplete: tuple[AgentResult, CaseFile, list[str]] | None = None

    for attempt in range(1, max_attempts + 1):
        result = await run_investigator(
            openai_client=openai_client,
            mcp_client=mcp_client,
            claim_id=claim_id,
            extra_instructions=correction,
            on_tool_call=on_tool_call,
        )
        total_usage = total_usage + result.usage

        try:
            case_file = CaseFile.model_validate(json.loads(_extract_json(result.final_text)))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)
            incomplete = None  # this attempt produced no CaseFile at all
            logger.warning(
                "case_file_validation_failed claim_id=%s attempt=%d/%d error=%s",
                claim_id,
                attempt,
                max_attempts,
                _safe_for_log(last_error),
            )
            correction = (
                "Your previous response could not be parsed as a valid CaseFile: "
                f"{last_error}. Respond again with ONLY the complete CaseFile JSON object "
                "(no markdown fences, no prose), including every required field: claim_id, "
                "po_summary (all four sub-fields), timeline, proposed_verdict, confidence."
            )
            continue

        missing = unmet(requirements, result.trace)
        if missing:
            last_error = "incomplete investigation: " + "; ".join(missing)
            incomplete = (result, case_file, missing)
            logger.warning(
                "investigation_incomplete claim_id=%s attempt=%d/%d unmet=%s",
                claim_id,
                attempt,
                max_attempts,
                _safe_for_log("; ".join(missing)),
            )
            # Naming the specific unmet calls (with the right ids) recovers far more reliably than
            # the old generic "you did not make the tool call required" nudge did.
            correction = (
                "Your investigation is incomplete. You have not yet made these required tool "
                f"calls, with exactly these arguments: {'; '.join(missing)}. Make them now, then "
                "respond again with the complete CaseFile JSON."
            )
            continue

        return result, case_file, total_usage, []

    logger.warning(
        "investigator_exhausted claim_id=%s attempts=%d error=%s",
        claim_id,
        max_attempts,
        _safe_for_log(last_error),
    )
    if incomplete is not None:
        result, case_file, missing = incomplete
        return result, case_file, total_usage, missing
    raise PipelineError(
        f"Investigator failed to produce a valid CaseFile for {claim_id} after "
        f"{max_attempts} attempts: {last_error}"
    )


def _resolve_final_verdict(
    investigator_verdict: str, reviewer_verdict: str, blockers: Sequence[str] = ()
) -> str:
    """Resolve the final verdict. Any blocker forces ESCALATE.

    A blocker is a reason this claim could not be decided on the evidence: either source documents
    that are provably absent from the store, or an investigation the Investigator never completed
    despite its retries. Both have the same correct disposition — a human looks at it.

    The override is deliberately one-directional: it may only *widen* to ESCALATE, never narrow to
    VALID/INVALID. Deciding a claim stays with the agents; refusing to let a claim be decided on
    evidence that isn't there is an orchestrator control, alongside the schema validation and trace
    verification in CLAUDE.md's safeguards. Blockers are established by code
    (orchestrator/completeness.py), never from anything a model reported about itself.
    """
    if blockers:
        return "ESCALATE"
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
    openai_client: Any | None = None,
    mcp_client: Any | None = None,
    output_dir: str | Path = "outputs",
    run_id: str | None = None,
    max_investigator_attempts: int = SETTINGS.max_investigator_attempts,
    on_investigator_tool_call: Callable[[ToolCallRecord], None] | None = None,
    on_reviewer_tool_call: Callable[[ToolCallRecord], None] | None = None,
) -> PipelineResult:
    output_dir = Path(output_dir)
    run_id = run_id or make_run_id()
    logger.info("pipeline_start claim_id=%s run_id=%s", claim_id, run_id)

    # Established before the agents run: whether the source documents this claim needs are actually
    # in the store. No amount of re-investigating fixes an absent document, so this deterministically
    # forces ESCALATE below, and is handed to the Reviewer so its verdict can account for it.
    gaps = data_gaps(claim_id)
    if gaps:
        logger.warning(
            "data_gaps claim_id=%s gaps=%s", claim_id, _safe_for_log("; ".join(gaps))
        )

    async with AsyncExitStack() as stack:
        if openai_client is None:
            from openai import AsyncOpenAI

            openai_client = AsyncOpenAI(
                base_url=OPENROUTER_BASE_URL,
                api_key=os.environ["OPENROUTER_API_KEY"],
                timeout=SETTINGS.openrouter_timeout_seconds,
            )

        if mcp_client is None:
            from fastmcp import Client
            from fastmcp.client.transports import PythonStdioTransport

            resolved_db = os.environ.get("DEDUCTIONS_DB", str(DEFAULT_DB_PATH))
            transport = PythonStdioTransport(
                script_path=MCP_SERVER_SCRIPT,
                env={**os.environ, "DEDUCTIONS_DB": resolved_db},
            )
            mcp_client = await stack.enter_async_context(Client(transport))

        (
            investigator_result,
            case_file,
            investigator_usage,
            unmet_requirements,
        ) = await _run_investigator_until_valid(
            openai_client=openai_client,
            mcp_client=mcp_client,
            claim_id=claim_id,
            max_attempts=max_investigator_attempts,
            # A claim with a missing document is escalating regardless, so don't spend retries
            # demanding the agent prove diligence on a document that isn't there.
            requirements=[] if gaps else required_tool_calls(claim_id),
            on_tool_call=on_investigator_tool_call,
        )

        # Phrased for the Reviewer, which receives these verbatim in <orchestrator_findings> so its
        # own verdict can account for them — rather than being silently overridden afterwards and
        # leaving an artifact that reads CONFIRM / all-checks-PASS / final ESCALATE.
        blockers = [
            *(f"source document missing from the system of record: {gap}" for gap in gaps),
            *(
                f"required investigation step never completed: {name}"
                for name in unmet_requirements
            ),
        ]

        stripped_case_file = strip_reasoning(case_file)
        reviewer_result = await run_reviewer(
            openai_client=openai_client,
            mcp_client=mcp_client,
            case_file=stripped_case_file,
            blockers=blockers,
            on_tool_call=on_reviewer_tool_call,
        )

        try:
            reviewer_output = ReviewerOutput.model_validate(
                json.loads(_extract_json(reviewer_result.final_text))
            )
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning(
                "reviewer_invalid_verdict claim_id=%s error=%s",
                claim_id,
                _safe_for_log(exc),
            )
            raise PipelineError(
                f"Reviewer failed to produce a valid verdict for {claim_id}: {exc}"
            ) from exc

        final_verdict = _resolve_final_verdict(
            case_file.proposed_verdict, reviewer_output.final_verdict, blockers
        )
        logger.info(
            "final_verdict claim_id=%s investigator=%s reviewer=%s final=%s confidence=%s",
            claim_id,
            case_file.proposed_verdict,
            reviewer_output.final_verdict,
            final_verdict,
            reviewer_output.confidence,
        )
        timestamp = datetime.now(timezone.utc).isoformat()
        usage = {
            "investigator": {
                "prompt_tokens": investigator_usage.prompt_tokens,
                "completion_tokens": investigator_usage.completion_tokens,
            },
            "reviewer": {
                "prompt_tokens": reviewer_result.usage.prompt_tokens,
                "completion_tokens": reviewer_result.usage.completion_tokens,
            },
        }

        run_dir = prepare_run_dir(output_dir, claim_id, run_id)
        write_verdict_json(
            run_dir,
            claim_id=claim_id,
            investigator_verdict=case_file.proposed_verdict,
            reviewer_verdict=reviewer_output.final_verdict,
            final_verdict=final_verdict,
            confidence=reviewer_output.confidence,
            timestamp=timestamp,
            usage=usage,
        )
        write_reasoning_trace_json(
            run_dir,
            claim_id=claim_id,
            investigator_messages=investigator_result.messages,
            reviewer_messages=reviewer_result.messages,
        )
        write_case_file_json(
            run_dir,
            claim_id=claim_id,
            case_file=case_file,
            reviewer_output=reviewer_output,
        )
        if final_verdict == "INVALID":
            write_dispute_packet_md(
                run_dir,
                claim_id=claim_id,
                case_file=case_file,
                reviewer_output=reviewer_output,
            )

        write_claim_resolution(
            claim_id=claim_id,
            investigator_verdict=case_file.proposed_verdict,
            final_verdict=final_verdict,
            confidence=reviewer_output.confidence,
            resolved_at=timestamp,
            run_id=run_id,
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
            run_id=run_id,
            run_dir=run_dir,
            usage=usage,
        )
