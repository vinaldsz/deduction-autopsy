import pytest
from fastmcp import Client
from rich.console import Console

from cli.run_claim import main, parse_args
from mcp_server.server import mcp
from tests.agent_stubs import StubAsyncOpenAI, make_completion
from tests.test_orchestrator_pipeline import VALID_CASE_FILE_JSON, confirm_json


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
        ]
    )
    assert args.output_dir == "custom_outputs"
    assert args.max_attempts == 5


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
