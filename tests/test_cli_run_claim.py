import json

import pytest
from fastmcp import Client
from rich.console import Console

from cli.run_claim import main, parse_args
from mcp_server.server import mcp
from tests.agent_stubs import StubAsyncOpenAI, make_completion
from tests.test_orchestrator_pipeline import (
    INVALID_CASE_FILE_JSON,
    VALID_CASE_FILE_JSON,
    confirm_json,
)


def test_parse_args_requires_claim_id_and_scenario():
    with pytest.raises(SystemExit):
        parse_args([])
    with pytest.raises(SystemExit):
        parse_args(["--claim-id", "CLM-001"])
    with pytest.raises(SystemExit):
        parse_args(["--scenario", "s01_clean_shortage"])


def test_parse_args_defaults():
    args = parse_args(["--claim-id", "CLM-001", "--scenario", "s01_clean_shortage"])
    assert args.output_dir == "outputs"
    assert args.max_attempts == 3
    assert args.explain is False


def test_parse_args_overrides():
    args = parse_args(
        [
            "--claim-id",
            "CLM-001",
            "--scenario",
            "s01_clean_shortage",
            "--output-dir",
            "custom_outputs",
            "--max-attempts",
            "5",
            "--explain",
        ]
    )
    assert args.output_dir == "custom_outputs"
    assert args.max_attempts == 5
    assert args.explain is True


async def test_happy_path_prints_verdict_and_returns_zero(monkeypatch, tmp_path):
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")
    stub = StubAsyncOpenAI(
        [
            make_completion(content=VALID_CASE_FILE_JSON),
            make_completion(content=confirm_json("CLM-001")),
        ]
    )
    console = Console(record=True, no_color=True, width=300)

    async with Client(mcp) as mcp_client:
        exit_code = await main(
            ["--claim-id", "CLM-001", "--scenario", "s01_clean_shortage", "--output-dir", str(tmp_path)],
            openai_client=stub,
            mcp_client=mcp_client,
            console=console,
        )

    assert exit_code == 0
    output = console.export_text()
    assert "VALID" in output
    assert "CONFIRM" in output
    assert str(tmp_path / "CLM-001") in output
    assert (tmp_path / "CLM-001" / "verdict.json").exists()


async def test_pipeline_error_prints_message_and_returns_one(monkeypatch, tmp_path):
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")
    stub = StubAsyncOpenAI(
        [
            make_completion(content="not json"),
            make_completion(content="still not json"),
        ]
    )
    console = Console(record=True, no_color=True, width=120)

    async with Client(mcp) as mcp_client:
        exit_code = await main(
            [
                "--claim-id",
                "CLM-001",
                "--scenario",
                "s01_clean_shortage",
                "--output-dir",
                str(tmp_path),
                "--max-attempts",
                "2",
            ],
            openai_client=stub,
            mcp_client=mcp_client,
            console=console,
        )

    assert exit_code == 1
    assert "Pipeline failed" in console.export_text()


async def test_missing_api_key_returns_one_without_network(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    console = Console(record=True, no_color=True, width=120)

    exit_code = await main(
        ["--claim-id", "CLM-001", "--scenario", "s01_clean_shortage"],
        console=console,
    )

    assert exit_code == 1
    assert "OPENROUTER_API_KEY is not set" in console.export_text()


async def test_explain_prints_tool_calls_case_file_and_review_findings(monkeypatch, tmp_path):
    monkeypatch.setenv("SCENARIO_ID", "s02_casepack_mismatch")
    stub = StubAsyncOpenAI(
        [
            make_completion(
                tool_calls=[{"id": "call_1", "name": "get_po", "args": {"po_id": "PO-002"}}]
            ),
            make_completion(
                tool_calls=[
                    {
                        "id": "call_2",
                        "name": "normalize_uom",
                        "args": {"qty": 5, "from_uom": "CASE", "to_uom": "EACH", "sku": "SKU-002"},
                    }
                ]
            ),
            make_completion(content=INVALID_CASE_FILE_JSON),
            make_completion(
                tool_calls=[
                    {
                        "id": "call_3",
                        "name": "normalize_uom",
                        "args": {"qty": 5, "from_uom": "CASE", "to_uom": "EACH", "sku": "SKU-002"},
                    }
                ]
            ),
            make_completion(content=confirm_json("CLM-002")),
        ]
    )
    console = Console(record=True, no_color=True, width=300)

    async with Client(mcp) as mcp_client:
        exit_code = await main(
            [
                "--claim-id",
                "CLM-002",
                "--scenario",
                "s02_casepack_mismatch",
                "--output-dir",
                str(tmp_path),
                "--explain",
            ],
            openai_client=stub,
            mcp_client=mcp_client,
            console=console,
        )

    assert exit_code == 0
    output = console.export_text()

    assert "Investigator" in output
    assert "Reviewer" in output
    assert "get_po" in output
    assert output.count("normalize_uom") >= 2  # once for the Investigator, once for the Reviewer

    assert "CaseFile handed to Reviewer" in output
    assert "CLM-002" in output
    # reasoning is stripped before the CaseFile is handed to the Reviewer / printed here
    assert "Normalized quantities match once CASE is converted to EACH" not in output

    assert "uom_check" in output
    assert "re-fetched via normalize_uom" in output
    assert "split_shipment_check" in output
    assert "verified from already-fetched documents" in output

    assert "Reviewer overturned" not in output  # s02 never overturns


async def test_explain_prints_overturn_callout_pointing_to_dispute_grounds(monkeypatch, tmp_path):
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")
    overturn_json = json.dumps(
        {
            "claim_id": "CLM-001",
            "investigator_verdict": "VALID",
            "review_findings": {
                "uom_check": "N/A",
                "split_shipment_check": "N/A",
                "timeline_check": "N/A",
                "trade_agreement_check": "N/A",
                "duplicate_check": "FAIL",
                "substitution_check": "N/A",
            },
            "final_verdict": "OVERTURN",
            "confidence": 0.9,
            "dispute_grounds": ["Found an unresolved prior claim the Investigator missed."],
            "reasoning": "Re-checked list_claims_for_po myself.",
        }
    )
    stub = StubAsyncOpenAI(
        [
            make_completion(content=VALID_CASE_FILE_JSON),
            make_completion(content=overturn_json),
        ]
    )
    console = Console(record=True, no_color=True, width=300)

    async with Client(mcp) as mcp_client:
        exit_code = await main(
            [
                "--claim-id",
                "CLM-001",
                "--scenario",
                "s01_clean_shortage",
                "--output-dir",
                str(tmp_path),
                "--explain",
            ],
            openai_client=stub,
            mcp_client=mcp_client,
            console=console,
        )

    assert exit_code == 0
    output = console.export_text()
    assert "Reviewer overturned the Investigator" in output
    # The explanation for the overturn should come from the Reviewer's own live findings
    # (dispute_grounds), not a static per-scenario description.
    assert "Dispute grounds" in output
    assert "Found an unresolved prior claim the Investigator missed." in output


async def test_without_explain_output_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")
    stub = StubAsyncOpenAI(
        [
            make_completion(content=VALID_CASE_FILE_JSON),
            make_completion(content=confirm_json("CLM-001")),
        ]
    )
    console = Console(record=True, no_color=True, width=300)

    async with Client(mcp) as mcp_client:
        exit_code = await main(
            ["--claim-id", "CLM-001", "--scenario", "s01_clean_shortage", "--output-dir", str(tmp_path)],
            openai_client=stub,
            mcp_client=mcp_client,
            console=console,
        )

    assert exit_code == 0
    output = console.export_text()
    # The final summary table always prints "Investigator verdict"/"Reviewer verdict" rows —
    # only the --explain-specific sections should be absent here.
    assert "CaseFile handed to Reviewer" not in output
    assert "Reviewer checks" not in output
    assert "uom_check" not in output
    assert "get_po(" not in output
