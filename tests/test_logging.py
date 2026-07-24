"""Layer 16 — structured logging.

Establishes the caplog pattern for the repo (nothing used caplog before this layer).
Reuses the wire-level stub pattern from tests/agent_stubs.py so nothing hits OpenRouter,
exactly as tests/test_orchestrator_pipeline.py and tests/test_agents_base.py do.
"""

import json
import logging

import pytest
from fastmcp import Client

from agents.base import AgentRunner
from mcp_server.server import mcp
from orchestrator.pipeline import PipelineError, run_pipeline
from tests.agent_stubs import (
    StubAsyncOpenAI,
    make_completion,
    make_status_error,
)

VALID_CASE_FILE_JSON = json.dumps(
    {
        "claim_id": "CLM-001",
        "po_summary": {
            "ordered_qty_each": 120,
            "shipped_qty_each": 120,
            "received_qty_each": 108,
            "invoiced_qty_each": 120,
        },
        "timeline": [{"event": "order_date", "date": "2024-01-10", "valid": True}],
        "proposed_verdict": "VALID",
        "confidence": 0.95,
        "discrepancy_qty": 12,
        "discrepancy_amount_cents": 3000,
        "reasoning": "The receiving notes confirm a refused 12-unit shortage.",
    }
)

CONFIRM_JSON = json.dumps(
    {
        "claim_id": "CLM-001",
        "investigator_verdict": "VALID",
        "review_findings": {},
        "final_verdict": "CONFIRM",
        "confidence": 0.95,
        "reasoning": "Recomputed and it matches.",
    }
)


class _FakeSleep:
    """Records requested backoff durations instead of actually sleeping."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


async def test_pipeline_start_and_final_verdict_are_logged(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")
    stub = StubAsyncOpenAI(
        [make_completion(content=VALID_CASE_FILE_JSON), make_completion(content=CONFIRM_JSON)]
    )

    with caplog.at_level(logging.INFO, logger="orchestrator.pipeline"):
        async with Client(mcp) as mcp_client:
            await run_pipeline(
                claim_id="CLM-001",
                scenario="s01_clean_shortage",
                openai_client=stub,
                mcp_client=mcp_client,
                output_dir=tmp_path,
            )

    messages = [r.getMessage() for r in caplog.records if r.name == "orchestrator.pipeline"]
    assert any(m.startswith("pipeline_start") and "claim_id=CLM-001" in m for m in messages)
    final = [m for m in messages if m.startswith("final_verdict")]
    assert len(final) == 1
    assert "claim_id=CLM-001" in final[0]
    assert "final=VALID" in final[0]


async def test_case_file_validation_failure_logs_warning(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")
    # First investigator turn returns unparseable prose; it should recover on the retry.
    stub = StubAsyncOpenAI(
        [
            make_completion(content="I could not complete the investigation."),
            make_completion(content=VALID_CASE_FILE_JSON),
            make_completion(content=CONFIRM_JSON),
        ]
    )

    with caplog.at_level(logging.WARNING, logger="orchestrator.pipeline"):
        async with Client(mcp) as mcp_client:
            await run_pipeline(
                claim_id="CLM-001",
                scenario="s01_clean_shortage",
                openai_client=stub,
                mcp_client=mcp_client,
                output_dir=tmp_path,
            )

    warnings = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and r.getMessage().startswith("case_file_validation_failed")
    ]
    assert len(warnings) == 1
    assert "claim_id=CLM-001" in warnings[0]
    assert "attempt=1/" in warnings[0]


async def test_validation_failure_log_cannot_forge_a_second_line(monkeypatch, tmp_path, caplog):
    """Regression guard for log-forging (security).

    A validation error embeds model output derived from the fixture notes fields — the
    prompt-injection surface CLAUDE.md wraps in <case_file> delimiters. The error string is
    inherently multi-line (pydantic) and untrusted, so it is passed through _safe_for_log,
    which collapses newlines. Assert no logged record ends up spanning more than one physical
    line, so an injected newline can never forge a fake `final_verdict ...` entry.
    """
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")
    # Valid JSON, but missing required fields -> a multi-line pydantic ValidationError whose
    # text would splice into the log line if it were not sanitized.
    incomplete = json.dumps(
        {
            "claim_id": "CLM-001",
            "reasoning": "\n2026-07-23 INFO orchestrator.pipeline final_verdict "
            "claim_id=CLM-001 final=INVALID confidence=0.99",
        }
    )
    stub = StubAsyncOpenAI(
        [
            make_completion(content=incomplete),
            make_completion(content=VALID_CASE_FILE_JSON),
            make_completion(content=CONFIRM_JSON),
        ]
    )

    with caplog.at_level(logging.WARNING, logger="orchestrator.pipeline"):
        async with Client(mcp) as mcp_client:
            await run_pipeline(
                claim_id="CLM-001",
                scenario="s01_clean_shortage",
                openai_client=stub,
                mcp_client=mcp_client,
                output_dir=tmp_path,
            )

    validation_warnings = [
        r for r in caplog.records if r.getMessage().startswith("case_file_validation_failed")
    ]
    assert len(validation_warnings) == 1
    assert "\n" not in validation_warnings[0].getMessage()


async def test_transport_retry_logs_warning(caplog):
    stub = StubAsyncOpenAI([make_status_error(429), make_completion(content="Done.")])
    sleep = _FakeSleep()

    with caplog.at_level(logging.WARNING, logger="agents.base"):
        async with Client(mcp) as mcp_client:
            runner = AgentRunner(
                openai_client=stub,
                mcp_client=mcp_client,
                model="test-model",
                system_prompt="You are a test agent.",
                sleep=sleep,
            )
            await runner.run("Investigate claim CLM-001.")

    warnings = [
        r.getMessage()
        for r in caplog.records
        if r.name == "agents.base" and r.getMessage().startswith("transport_retry")
    ]
    assert len(warnings) == 1
    assert "model=test-model" in warnings[0]
    assert "attempt=1/" in warnings[0]


async def test_pipeline_error_logs_warning_before_raising(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")
    # Every investigator turn is unparseable -> attempts exhausted -> PipelineError.
    stub = StubAsyncOpenAI([make_completion(content="nope") for _ in range(5)])

    with caplog.at_level(logging.WARNING, logger="orchestrator.pipeline"):
        async with Client(mcp) as mcp_client:
            with pytest.raises(PipelineError):
                await run_pipeline(
                    claim_id="CLM-001",
                    scenario="s01_clean_shortage",
                    openai_client=stub,
                    mcp_client=mcp_client,
                    output_dir=tmp_path,
                    max_investigator_attempts=2,
                )

    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(m.startswith("investigator_exhausted") and "claim_id=CLM-001" in m for m in messages)
