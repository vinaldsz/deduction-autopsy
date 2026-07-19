import json

import pytest
from fastmcp import Client

from mcp_server.server import mcp
from orchestrator.pipeline import PipelineError, _extract_json, _resolve_final_verdict, run_pipeline
from tests.agent_stubs import StubAsyncOpenAI, make_completion

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
        "uom_conversions_applied": [],
        "prior_claims": [],
        "trade_agreement_found": False,
        "discrepancy_qty": 12,
        "discrepancy_amount_cents": 3000,
        "proposed_verdict": "VALID",
        "confidence": 0.95,
        "reasoning": "The receiving notes confirm a refused 12-unit shortage.",
    }
)

INVALID_CASE_FILE_JSON = json.dumps(
    {
        "claim_id": "CLM-002",
        "po_summary": {
            "ordered_qty_each": 120,
            "shipped_qty_each": 120,
            "received_qty_each": 120,
            "invoiced_qty_each": 120,
        },
        "timeline": [{"event": "order_date", "date": "2024-02-01", "valid": True}],
        "uom_conversions_applied": ["5 CASE -> 120 EACH for SKU-002 (factor 24)"],
        "prior_claims": [],
        "trade_agreement_found": False,
        "discrepancy_qty": 0,
        "discrepancy_amount_cents": 0,
        "proposed_verdict": "INVALID",
        "confidence": 0.97,
        "reasoning": "Normalized quantities match once CASE is converted to EACH.",
    }
)


def confirm_json(claim_id: str) -> str:
    return json.dumps(
        {
            "claim_id": claim_id,
            "investigator_verdict": "INVALID",
            "review_findings": {
                "uom_check": "PASS",
                "split_shipment_check": "N/A",
                "timeline_check": "N/A",
                "trade_agreement_check": "N/A",
                "duplicate_check": "N/A",
                "substitution_check": "N/A",
            },
            "final_verdict": "CONFIRM",
            "confidence": 0.97,
            "dispute_grounds": ["Normalized quantities match: 5 CASE = 120 EACH"],
            "reasoning": "Recomputed normalize_uom myself and it matches.",
        }
    )


async def test_happy_path_valid_confirmed_no_dispute_packet(monkeypatch, tmp_path):
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")
    stub = StubAsyncOpenAI(
        [
            make_completion(content=VALID_CASE_FILE_JSON),
            make_completion(content=confirm_json("CLM-001")),
        ]
    )

    async with Client(mcp) as mcp_client:
        result = await run_pipeline(
            claim_id="CLM-001",
            scenario="s01_clean_shortage",
            openai_client=stub,
            mcp_client=mcp_client,
            output_dir=tmp_path,
        )

    assert result.investigator_verdict == "VALID"
    assert result.reviewer_verdict == "CONFIRM"
    assert result.final_verdict == "VALID"

    claim_dir = tmp_path / "CLM-001"
    assert (claim_dir / "verdict.json").exists()
    assert (claim_dir / "reasoning_trace.json").exists()
    assert not (claim_dir / "dispute_packet.md").exists()

    verdict = json.loads((claim_dir / "verdict.json").read_text())
    assert verdict == {
        "claim_id": "CLM-001",
        "investigator_verdict": "VALID",
        "reviewer_verdict": "CONFIRM",
        "final_verdict": "VALID",
        "confidence": 0.97,
        "timestamp": verdict["timestamp"],
    }


async def test_happy_path_invalid_confirmed_writes_dispute_packet(monkeypatch, tmp_path):
    monkeypatch.setenv("SCENARIO_ID", "s02_casepack_mismatch")
    stub = StubAsyncOpenAI(
        [
            make_completion(
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": "normalize_uom",
                        "args": {"qty": 5, "from_uom": "CASE", "to_uom": "EACH", "sku": "SKU-002"},
                    }
                ]
            ),
            make_completion(content=INVALID_CASE_FILE_JSON),
            make_completion(content=confirm_json("CLM-002")),
        ]
    )

    async with Client(mcp) as mcp_client:
        result = await run_pipeline(
            claim_id="CLM-002",
            scenario="s02_casepack_mismatch",
            openai_client=stub,
            mcp_client=mcp_client,
            output_dir=tmp_path,
        )

    assert result.final_verdict == "INVALID"
    claim_dir = tmp_path / "CLM-002"
    assert (claim_dir / "dispute_packet.md").exists()
    packet = (claim_dir / "dispute_packet.md").read_text()
    assert "Normalized quantities match: 5 CASE = 120 EACH" in packet
    assert "120" in packet


async def test_missing_required_field_triggers_correction_retry(monkeypatch, tmp_path):
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")
    incomplete = json.dumps({"claim_id": "CLM-001"})  # missing po_summary/timeline/etc.
    stub = StubAsyncOpenAI(
        [
            make_completion(content=incomplete),
            make_completion(content=VALID_CASE_FILE_JSON),
            make_completion(content=confirm_json("CLM-001")),
        ]
    )

    async with Client(mcp) as mcp_client:
        result = await run_pipeline(
            claim_id="CLM-001",
            scenario="s01_clean_shortage",
            openai_client=stub,
            mcp_client=mcp_client,
            output_dir=tmp_path,
        )

    assert result.investigator_verdict == "VALID"
    second_attempt_user_message = next(
        m for m in stub.requests[1]["messages"] if m["role"] == "user"
    )
    assert "could not be parsed" in second_attempt_user_message["content"]


async def test_missing_required_tool_call_triggers_correction_retry(monkeypatch, tmp_path):
    monkeypatch.setenv("SCENARIO_ID", "s02_casepack_mismatch")
    stub = StubAsyncOpenAI(
        [
            make_completion(content=INVALID_CASE_FILE_JSON),  # valid JSON, but no normalize_uom call
            make_completion(
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": "normalize_uom",
                        "args": {"qty": 5, "from_uom": "CASE", "to_uom": "EACH", "sku": "SKU-002"},
                    }
                ]
            ),
            make_completion(content=INVALID_CASE_FILE_JSON),
            make_completion(content=confirm_json("CLM-002")),
        ]
    )

    async with Client(mcp) as mcp_client:
        result = await run_pipeline(
            claim_id="CLM-002",
            scenario="s02_casepack_mismatch",
            openai_client=stub,
            mcp_client=mcp_client,
            output_dir=tmp_path,
        )

    assert result.investigator_verdict == "INVALID"
    second_attempt_user_message = next(
        m for m in stub.requests[1]["messages"] if m["role"] == "user"
    )
    assert "did not make the tool call" in second_attempt_user_message["content"]


async def test_exceeding_max_attempts_raises_pipeline_error(monkeypatch, tmp_path):
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")
    stub = StubAsyncOpenAI(
        [
            make_completion(content="not json"),
            make_completion(content="still not json"),
        ]
    )

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


async def test_reviewer_receives_case_file_without_reasoning(monkeypatch, tmp_path):
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")
    stub = StubAsyncOpenAI(
        [
            make_completion(content=VALID_CASE_FILE_JSON),
            make_completion(content=confirm_json("CLM-001")),
        ]
    )

    captured = {}
    import orchestrator.pipeline as pipeline_module

    original_run_reviewer = pipeline_module.run_reviewer

    async def spy_run_reviewer(*, case_file, **kwargs):
        captured["case_file"] = case_file
        return await original_run_reviewer(case_file=case_file, **kwargs)

    monkeypatch.setattr(pipeline_module, "run_reviewer", spy_run_reviewer)

    async with Client(mcp) as mcp_client:
        await run_pipeline(
            claim_id="CLM-001",
            scenario="s01_clean_shortage",
            openai_client=stub,
            mcp_client=mcp_client,
            output_dir=tmp_path,
        )

    assert "reasoning" not in captured["case_file"]


@pytest.mark.parametrize(
    "investigator_verdict,reviewer_verdict,expected",
    [
        ("VALID", "CONFIRM", "VALID"),
        ("INVALID", "CONFIRM", "INVALID"),
        ("VALID", "OVERTURN", "INVALID"),
        ("INVALID", "OVERTURN", "VALID"),
        ("VALID", "ESCALATE", "ESCALATE"),
        ("INVALID", "ESCALATE", "ESCALATE"),
        ("ESCALATE", "OVERTURN", "ESCALATE"),
    ],
)
def test_resolve_final_verdict(investigator_verdict, reviewer_verdict, expected):
    assert _resolve_final_verdict(investigator_verdict, reviewer_verdict) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"a": 1}', '{"a": 1}'),
        ('```\n{"a": 1}\n```', '{"a": 1}'),
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        (
            'Let me analyze this...\n\nHere is my conclusion:\n\n```json\n{"a": 1}\n```',
            '{"a": 1}',
        ),
        ('Some reasoning first. {"a": 1} trailing note.', '{"a": 1}'),
        ('{"a": {"nested": 1}}', '{"a": {"nested": 1}}'),
        # Trailing prose containing a stray '}' must not extend the match past the real object.
        ('{"a": 1} — let me know if you have questions {2}', '{"a": 1}'),
    ],
)
def test_extract_json_handles_prose_before_json(raw, expected):
    assert _extract_json(raw) == expected


async def test_happy_path_survives_prose_before_fenced_json(monkeypatch, tmp_path):
    """Regression test: a live OpenRouter run against s02 showed the Investigator reasoning in
    prose before emitting the CaseFile inside a ```json fence — _extract_json must not require
    the fence to be the very first characters of the response."""
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")
    prose_wrapped_case_file = (
        "Let me walk through the five-step protocol.\n\n"
        "Step 1: documents collected. Step 5: no prior claims.\n\n"
        f"```json\n{VALID_CASE_FILE_JSON}\n```"
    )
    stub = StubAsyncOpenAI(
        [
            make_completion(content=prose_wrapped_case_file),
            make_completion(content=confirm_json("CLM-001")),
        ]
    )

    async with Client(mcp) as mcp_client:
        result = await run_pipeline(
            claim_id="CLM-001",
            scenario="s01_clean_shortage",
            openai_client=stub,
            mcp_client=mcp_client,
            output_dir=tmp_path,
        )

    assert result.investigator_verdict == "VALID"
    assert result.final_verdict == "VALID"
