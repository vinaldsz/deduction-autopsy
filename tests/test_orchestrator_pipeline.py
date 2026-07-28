import json
import os

import pytest
from fastmcp import Client

from agents.base import ToolCallRecord
from mcp_server.db import connect
from mcp_server.server import mcp
from orchestrator.config import SETTINGS
from orchestrator.pipeline import (
    CaseFile,
    PipelineError,
    _extract_json,
    _resolve_final_verdict,
    run_pipeline,
    strip_reasoning,
)
from tests.agent_stubs import StubAsyncOpenAI, floor_tool_calls, make_completion

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


def _corrections_sent(stub, needle: str) -> list[str]:
    """The distinct correction messages the pipeline injected into a retry, found by content rather
    than by request index (the index shifts whenever a scripted turn is added).

    Deduplicated because one correction is carried in the user message for the whole attempt, so it
    reappears in every model turn of that attempt — the interesting count is how many corrections
    were issued, not how many requests echoed one.
    """
    seen = {
        message["content"]
        for request in stub.requests
        for message in request["messages"]
        if message["role"] == "user" and needle in message["content"]
    }
    return sorted(seen)


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
    stub = StubAsyncOpenAI(
        [
            floor_tool_calls("CLM-001"),
            make_completion(content=VALID_CASE_FILE_JSON),
            make_completion(content=confirm_json("CLM-001")),
        ]
    )

    async with Client(mcp) as mcp_client:
        result = await run_pipeline(
            claim_id="CLM-001",
            openai_client=stub,
            mcp_client=mcp_client,
            output_dir=tmp_path,
        )

    assert result.investigator_verdict == "VALID"
    assert result.reviewer_verdict == "CONFIRM"
    assert result.final_verdict == "VALID"

    run_dir = result.run_dir
    assert run_dir == tmp_path / "CLM-001" / result.run_id
    # `latest` symlink resolves to the run just written.
    assert (tmp_path / "CLM-001" / "latest").resolve() == run_dir.resolve()
    assert (run_dir / "verdict.json").exists()
    assert (run_dir / "reasoning_trace.json").exists()
    assert not (run_dir / "dispute_packet.md").exists()

    verdict = json.loads((run_dir / "verdict.json").read_text())
    assert verdict == {
        "claim_id": "CLM-001",
        "investigator_verdict": "VALID",
        "reviewer_verdict": "CONFIRM",
        "final_verdict": "VALID",
        "confidence": 0.97,
        "timestamp": verdict["timestamp"],
        "usage": {
            "investigator": {"prompt_tokens": 0, "completion_tokens": 0},
            "reviewer": {"prompt_tokens": 0, "completion_tokens": 0},
        },
    }


async def test_reruns_are_archived_side_by_side_and_latest_repoints(monkeypatch, tmp_path):
    """Layer 17: two runs with distinct run_ids both persist (no clobber) and `latest` follows
    the newest run."""
    async def _run(run_id: str):
        stub = StubAsyncOpenAI(
            [
                floor_tool_calls("CLM-001"),
                make_completion(content=VALID_CASE_FILE_JSON),
                make_completion(content=confirm_json("CLM-001")),
            ]
        )
        async with Client(mcp) as mcp_client:
            return await run_pipeline(
                claim_id="CLM-001",
                openai_client=stub,
                mcp_client=mcp_client,
                output_dir=tmp_path,
                run_id=run_id,
            )

    first = await _run("20240101T000000Z")
    second = await _run("20240102T000000Z")

    # Both runs' artifacts coexist — the earlier audit trail is not overwritten.
    assert (first.run_dir / "verdict.json").exists()
    assert (second.run_dir / "verdict.json").exists()
    assert first.run_dir != second.run_dir
    # `latest` points at the most recent run.
    assert (tmp_path / "CLM-001" / "latest").resolve() == second.run_dir.resolve()


async def test_run_id_defaults_to_a_generated_timestamp(monkeypatch, tmp_path):
    """Omitting run_id auto-generates one so the default path is non-overwriting too."""
    stub = StubAsyncOpenAI(
        [
            floor_tool_calls("CLM-001"),
            make_completion(content=VALID_CASE_FILE_JSON),
            make_completion(content=confirm_json("CLM-001")),
        ]
    )

    async with Client(mcp) as mcp_client:
        result = await run_pipeline(
            claim_id="CLM-001",
            openai_client=stub,
            mcp_client=mcp_client,
            output_dir=tmp_path,
        )

    assert result.run_id
    assert result.run_dir == tmp_path / "CLM-001" / result.run_id
    assert result.run_dir.is_dir()


async def test_constructs_openai_client_with_configured_timeout(monkeypatch, tmp_path):
    """Regression guard for Layer 13: when no openai_client is injected, run_pipeline must
    construct AsyncOpenAI with the SETTINGS-derived timeout, not the client library's
    unbounded default."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    stub = StubAsyncOpenAI(
        [
            floor_tool_calls("CLM-001"),
            make_completion(content=VALID_CASE_FILE_JSON),
            make_completion(content=confirm_json("CLM-001")),
        ]
    )
    captured_kwargs = []

    class _CapturingAsyncOpenAI:
        def __init__(self, **kwargs):
            captured_kwargs.append(kwargs)
            self.chat = stub.chat

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _CapturingAsyncOpenAI)

    async with Client(mcp) as mcp_client:
        await run_pipeline(
            claim_id="CLM-001",
            mcp_client=mcp_client,
            output_dir=tmp_path,
        )

    assert captured_kwargs[0]["timeout"] == SETTINGS.openrouter_timeout_seconds


async def test_happy_path_invalid_confirmed_writes_dispute_packet(monkeypatch, tmp_path):
    stub = StubAsyncOpenAI(
        [
            floor_tool_calls("CLM-002"),
            make_completion(content=INVALID_CASE_FILE_JSON),
            make_completion(content=confirm_json("CLM-002")),
        ]
    )

    async with Client(mcp) as mcp_client:
        result = await run_pipeline(
            claim_id="CLM-002",
            openai_client=stub,
            mcp_client=mcp_client,
            output_dir=tmp_path,
        )

    assert result.final_verdict == "INVALID"
    assert (result.run_dir / "dispute_packet.md").exists()
    packet = (result.run_dir / "dispute_packet.md").read_text()
    assert "Normalized quantities match: 5 CASE = 120 EACH" in packet
    assert "120" in packet


async def test_missing_required_field_triggers_correction_retry(monkeypatch, tmp_path):
    incomplete = json.dumps({"claim_id": "CLM-001"})  # missing po_summary/timeline/etc.
    stub = StubAsyncOpenAI(
        [
            make_completion(content=incomplete),
            floor_tool_calls("CLM-001"),
            make_completion(content=VALID_CASE_FILE_JSON),
            make_completion(content=confirm_json("CLM-001")),
        ]
    )

    async with Client(mcp) as mcp_client:
        result = await run_pipeline(
            claim_id="CLM-001",
            openai_client=stub,
            mcp_client=mcp_client,
            output_dir=tmp_path,
        )

    assert result.investigator_verdict == "VALID"
    assert len(_corrections_sent(stub, "could not be parsed")) == 1


async def test_verdict_json_sums_usage_across_investigator_retries(monkeypatch, tmp_path):
    incomplete = json.dumps({"claim_id": "CLM-001"})  # missing po_summary/timeline/etc.
    stub = StubAsyncOpenAI(
        [
            make_completion(content=incomplete, usage=(40, 5)),
            floor_tool_calls("CLM-001"),
            make_completion(content=VALID_CASE_FILE_JSON, usage=(60, 15)),
            make_completion(content=confirm_json("CLM-001"), usage=(25, 8)),
        ]
    )

    async with Client(mcp) as mcp_client:
        result = await run_pipeline(
            claim_id="CLM-001",
            openai_client=stub,
            mcp_client=mcp_client,
            output_dir=tmp_path,
        )

    expected_usage = {
        "investigator": {"prompt_tokens": 100, "completion_tokens": 20},
        "reviewer": {"prompt_tokens": 25, "completion_tokens": 8},
    }
    assert result.usage == expected_usage

    verdict = json.loads((result.run_dir / "verdict.json").read_text())
    assert verdict["usage"] == expected_usage


async def test_missing_required_tool_call_triggers_correction_retry(monkeypatch, tmp_path):
    """Attempt 1 collects every document but never normalizes UOM, so the *conditional* requirement
    is what fails and the correction must name it.

    Deliberately not "no tool calls at all" (how this read before Layer 31): with a universal floor
    that would fail the floor instead, and a generic assertion would still have passed — a green test
    that had stopped covering the UOM rule.
    """
    stub = StubAsyncOpenAI(
        [
            floor_tool_calls("CLM-002", omit="normalize_uom"),
            make_completion(content=INVALID_CASE_FILE_JSON),
            floor_tool_calls("CLM-002"),
            make_completion(content=INVALID_CASE_FILE_JSON),
            make_completion(content=confirm_json("CLM-002")),
        ]
    )

    async with Client(mcp) as mcp_client:
        result = await run_pipeline(
            claim_id="CLM-002",
            openai_client=stub,
            mcp_client=mcp_client,
            output_dir=tmp_path,
        )

    assert result.investigator_verdict == "INVALID"
    corrections = _corrections_sent(stub, "investigation is incomplete")
    assert len(corrections) == 1
    assert "normalize_uom" in corrections[0]


async def test_exceeding_max_attempts_raises_pipeline_error(monkeypatch, tmp_path):
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
                openai_client=stub,
                mcp_client=mcp_client,
                output_dir=tmp_path,
                max_investigator_attempts=2,
            )


async def test_reviewer_receives_case_file_without_reasoning(monkeypatch, tmp_path):
    stub = StubAsyncOpenAI(
        [
            floor_tool_calls("CLM-001"),
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
            openai_client=stub,
            mcp_client=mcp_client,
            output_dir=tmp_path,
        )

    assert "reasoning" not in captured["case_file"]


def test_strip_reasoning_drops_only_the_reasoning_field():
    case_file = CaseFile.model_validate(json.loads(VALID_CASE_FILE_JSON))
    stripped = strip_reasoning(case_file)

    assert "reasoning" not in stripped
    assert stripped["claim_id"] == "CLM-001"
    assert stripped["proposed_verdict"] == "VALID"
    assert set(stripped.keys()) == set(case_file.model_dump().keys()) - {"reasoning"}


async def test_tool_call_hooks_fire_only_for_their_own_agent(monkeypatch, tmp_path):
    stub = StubAsyncOpenAI(
        [
            floor_tool_calls("CLM-001"),
            make_completion(content=VALID_CASE_FILE_JSON),
            make_completion(
                tool_calls=[{"id": "call_2", "name": "get_po", "args": {"po_id": "PO-001"}}]
            ),
            make_completion(content=confirm_json("CLM-001")),
        ]
    )

    investigator_calls = []
    reviewer_calls = []

    async with Client(mcp) as mcp_client:
        await run_pipeline(
            claim_id="CLM-001",
            openai_client=stub,
            mcp_client=mcp_client,
            output_dir=tmp_path,
            on_investigator_tool_call=investigator_calls.append,
            on_reviewer_tool_call=reviewer_calls.append,
        )

    assert investigator_calls, "expected the Investigator to make at least one tool call"
    assert reviewer_calls, "expected the Reviewer to make at least one tool call"
    assert all(isinstance(record, ToolCallRecord) for record in investigator_calls + reviewer_calls)


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
    "investigator_verdict,reviewer_verdict",
    [
        ("VALID", "CONFIRM"),
        ("INVALID", "CONFIRM"),
        ("VALID", "OVERTURN"),
        ("INVALID", "OVERTURN"),
        ("INVALID", "ESCALATE"),
    ],
)
def test_a_data_gap_forces_escalate_whatever_the_agents_said(
    investigator_verdict, reviewer_verdict
):
    """Layer 31: a claim whose source documents are provably absent cannot be decided, so the
    orchestrator widens any agreed verdict to ESCALATE."""
    assert (
        _resolve_final_verdict(investigator_verdict, reviewer_verdict, ["no invoice for PO-T"])
        == "ESCALATE"
    )


async def test_pipeline_escalates_end_to_end_when_a_document_is_absent(monkeypatch, tmp_path):
    """The wiring, not just the pure resolver: against a store missing CLM-001's invoice, both agents
    agree on VALID/CONFIRM and the run still lands on ESCALATE.

    The corpus is complete, so this branch is unreachable without doctoring a copy of the DB.
    """
    from semantic_layer.etl import build_db

    db_path = tmp_path / "gap.db"
    build_db(db_path=db_path)
    with connect(db_path) as conn:
        conn.execute("DELETE FROM invoices WHERE po_id = 'PO-001'")
    monkeypatch.setenv("DEDUCTIONS_DB", str(db_path))

    stub = StubAsyncOpenAI(
        [
            floor_tool_calls("CLM-001"),
            make_completion(content=VALID_CASE_FILE_JSON),
            make_completion(content=confirm_json("CLM-001")),
        ]
    )
    async with Client(mcp) as mcp_client:
        result = await run_pipeline(
            claim_id="CLM-001",
            openai_client=stub,
            mcp_client=mcp_client,
            output_dir=tmp_path / "out",
        )

    assert result.investigator_verdict == "VALID"
    assert result.reviewer_verdict == "CONFIRM"
    assert result.final_verdict == "ESCALATE"
    # ESCALATE is not INVALID, so no dispute packet is produced.
    assert not (result.run_dir / "dispute_packet.md").exists()


def test_gap_override_only_widens_and_an_empty_gap_list_is_inert():
    """The override must never narrow a verdict to VALID/INVALID, and no-gaps must behave exactly
    as before it existed."""
    assert _resolve_final_verdict("VALID", "CONFIRM", []) == "VALID"
    assert _resolve_final_verdict("VALID", "CONFIRM", ()) == "VALID"
    assert _resolve_final_verdict("INVALID", "OVERTURN", []) == "VALID"


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
    prose_wrapped_case_file = (
        "Let me walk through the five-step protocol.\n\n"
        "Step 1: documents collected. Step 5: no prior claims.\n\n"
        f"```json\n{VALID_CASE_FILE_JSON}\n```"
    )
    stub = StubAsyncOpenAI(
        [
            floor_tool_calls("CLM-001"),
            make_completion(content=prose_wrapped_case_file),
            make_completion(content=confirm_json("CLM-001")),
        ]
    )

    async with Client(mcp) as mcp_client:
        result = await run_pipeline(
            claim_id="CLM-001",
            openai_client=stub,
            mcp_client=mcp_client,
            output_dir=tmp_path,
        )

    assert result.investigator_verdict == "VALID"
    assert result.final_verdict == "VALID"


async def test_run_writes_claim_resolution_and_reruns_upsert(monkeypatch, tmp_path):
    """Layer 29: the pipeline persists its verdict to claim_resolutions, and a re-run upserts
    (updates the same row) rather than duplicating."""
    from mcp_server.db import connect

    def _run_once():
        stub = StubAsyncOpenAI(
            [
                floor_tool_calls("CLM-001"),
                make_completion(content=VALID_CASE_FILE_JSON),
                make_completion(content=confirm_json("CLM-001")),
            ]
        )

        async def _go():
            async with Client(mcp) as mcp_client:
                return await run_pipeline(
                    claim_id="CLM-001", openai_client=stub, mcp_client=mcp_client, output_dir=tmp_path
                )

        return _go()

    result = await _run_once()

    db_path = os.environ["DEDUCTIONS_DB"]  # conftest points this at the session DB
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT investigator_verdict, final_verdict, confidence, run_id FROM claim_resolutions "
            "WHERE claim_id = ?", ("CLM-001",)
        ).fetchone()
    assert row == ("VALID", "VALID", result.confidence, result.run_id)

    await _run_once()  # re-run
    with connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM claim_resolutions WHERE claim_id = ?", ("CLM-001",)
        ).fetchone()[0]
    assert count == 1  # upsert, not a duplicate
