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

from orchestrator.ground_truth import GROUND_TRUTH
from orchestrator.pipeline import REQUIRED_TOOL_CALLS, run_pipeline

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="requires OPENROUTER_API_KEY to hit the real OpenRouter API",
    ),
]


def _investigator_tool_names(output_dir: Path, claim_id: str) -> set[str]:
    trace_path = output_dir / claim_id / "reasoning_trace.json"
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

    claim_dir = tmp_path / case["claim_id"]
    assert (claim_dir / "verdict.json").exists()
    assert (claim_dir / "reasoning_trace.json").exists()
    assert (claim_dir / "dispute_packet.md").exists() == (result.final_verdict == "INVALID")

    required_check = REQUIRED_TOOL_CALLS.get(case["scenario"][:3])
    if required_check is not None:
        required_tool = {
            "s02": "normalize_uom",
            "s03": "get_asns_for_po",
            "s06": "get_trade_agreement",
            "s07": "list_claims_for_po",
        }[case["scenario"][:3]]
        assert required_tool in _investigator_tool_names(tmp_path, case["claim_id"])
