"""End-to-end integration tests: real OpenRouter + real MCP server subprocess.

Unlike every other test in this suite, these hit the network and cost money — they are
excluded by default (see pyproject.toml's `addopts = "-m 'not integration'"`) and must be
run explicitly with `-m integration`. They also skip cleanly when OPENROUTER_API_KEY isn't
set, rather than failing.
"""

import json
import os
from pathlib import Path

import pytest
from fastmcp import Client
from pydantic import ValidationError

from agents.reviewer import run_reviewer
from mcp_server.server import mcp
from orchestrator.ground_truth import GROUND_TRUTH
from orchestrator.pipeline import (
    OPENROUTER_BASE_URL,
    REQUIRED_TOOL_CALLS,
    ReviewerOutput,
    _extract_json,
    run_pipeline,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="requires OPENROUTER_API_KEY to hit the real OpenRouter API",
    ),
]


def _investigator_tool_names(run_dir: Path) -> set[str]:
    trace_path = run_dir / "reasoning_trace.json"
    trace = json.loads(trace_path.read_text())
    names = set()
    for message in trace["investigator"]:
        for tool_call in message.get("tool_calls") or []:
            names.add(tool_call["function"]["name"])
    return names


@pytest.mark.parametrize("case", GROUND_TRUTH, ids=lambda c: c["scenario"])
async def test_scenario_matches_ground_truth(case, tmp_path):
    result = await run_pipeline(
        claim_id=case["claim_id"],
        scenario=case["scenario"],
        output_dir=tmp_path,
    )

    assert result.investigator_verdict == case["expected_investigator"]
    assert result.reviewer_verdict == case["expected_reviewer"]

    run_dir = result.run_dir
    assert (tmp_path / case["claim_id"] / "latest").resolve() == run_dir.resolve()
    assert (run_dir / "verdict.json").exists()
    assert (run_dir / "reasoning_trace.json").exists()
    assert (run_dir / "dispute_packet.md").exists() == (result.final_verdict == "INVALID")

    required_check = REQUIRED_TOOL_CALLS.get(case["scenario"][:3])
    if required_check is not None:
        required_tool = {
            "s02": "normalize_uom",
            "s03": "get_asns_for_po",
            "s06": "get_trade_agreement",
            "s07": "list_claims_for_po",
            "s08": "list_claims_for_po",
        }[case["scenario"][:3]]
        assert required_tool in _investigator_tool_names(result.run_dir)


async def test_reviewer_overturns_a_missed_duplicate(monkeypatch):
    """Proves the Reviewer's spot-check would independently catch and overturn a duplicate
    claim even if the Investigator missed it — without depending on the real Investigator
    actually making that mistake live (it doesn't: s08_reviewer_overturn's own
    GROUND_TRUTH entry is INVALID/CONFIRM, because the current Investigator prompt already
    catches this duplicate correctly on its own). Feeds the Reviewer a fabricated CaseFile —
    as if a hypothetical Investigator had reconciled quantities correctly but never noticed
    CLM-008a — against s08's real fixtures, and confirms the live Reviewer's mandatory
    list_claims_for_po re-check surfaces the prior claim regardless of what the case file says.
    """
    monkeypatch.setenv("SCENARIO_ID", "s08_reviewer_overturn")

    from openai import AsyncOpenAI

    openai_client = AsyncOpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=os.environ["OPENROUTER_API_KEY"],
    )

    fabricated_case_file = {
        "claim_id": "CLM-008",
        "po_summary": {
            "ordered_qty_each": 150,
            "shipped_qty_each": 150,
            "received_qty_each": 138,
            "invoiced_qty_each": 150,
        },
        "timeline": [
            {"event": "order_date", "date": "2024-08-01", "valid": True},
            {"event": "ship_date", "date": "2024-08-02", "valid": True},
            {"event": "receipt_date", "date": "2024-08-04", "valid": True},
            {"event": "invoice_date", "date": "2024-08-02", "valid": True},
            {"event": "claim_date", "date": "2024-09-15", "valid": True},
        ],
        "uom_conversions_applied": [],
        "prior_claims": [],  # the fabricated miss: never surfaced CLM-008a
        "trade_agreement_found": False,
        "discrepancy_qty": 12,
        "discrepancy_amount_cents": 2400,
        "proposed_verdict": "VALID",
        "confidence": 0.95,
    }

    async with Client(mcp) as mcp_client:
        reviewer_result = await run_reviewer(
            openai_client=openai_client,
            mcp_client=mcp_client,
            case_file=fabricated_case_file,
        )

    try:
        reviewer_output = ReviewerOutput.model_validate(
            json.loads(_extract_json(reviewer_result.final_text))
        )
    except (json.JSONDecodeError, ValidationError) as exc:
        pytest.fail(f"Reviewer failed to produce a valid verdict: {exc}")

    assert reviewer_output.final_verdict == "OVERTURN"
    assert reviewer_output.review_findings.duplicate_check == "FAIL"
    assert reviewer_output.dispute_grounds
