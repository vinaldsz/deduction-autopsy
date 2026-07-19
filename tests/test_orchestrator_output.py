import json

from orchestrator.output import (
    write_dispute_packet_md,
    write_reasoning_trace_json,
    write_verdict_json,
)
from orchestrator.pipeline import CaseFile, ReviewerOutput

SAMPLE_CASE_FILE = CaseFile.model_validate(
    {
        "claim_id": "CLM-002",
        "po_summary": {
            "ordered_qty_each": 120,
            "shipped_qty_each": 120,
            "received_qty_each": 120,
            "invoiced_qty_each": 120,
        },
        "timeline": [{"event": "order_date", "date": "2024-02-01", "valid": True}],
        "proposed_verdict": "INVALID",
        "confidence": 0.97,
    }
)

SAMPLE_REVIEWER_OUTPUT = ReviewerOutput.model_validate(
    {
        "claim_id": "CLM-002",
        "investigator_verdict": "INVALID",
        "review_findings": {"uom_check": "PASS"},
        "final_verdict": "CONFIRM",
        "confidence": 0.97,
        "dispute_grounds": ["Normalized quantities match: 5 CASE = 120 EACH"],
    }
)


def test_write_verdict_json(tmp_path):
    path = write_verdict_json(
        tmp_path,
        claim_id="CLM-002",
        investigator_verdict="INVALID",
        reviewer_verdict="CONFIRM",
        final_verdict="INVALID",
        confidence=0.97,
        timestamp="2024-02-05T00:00:00+00:00",
    )

    assert path == tmp_path / "CLM-002" / "verdict.json"
    assert json.loads(path.read_text()) == {
        "claim_id": "CLM-002",
        "investigator_verdict": "INVALID",
        "reviewer_verdict": "CONFIRM",
        "final_verdict": "INVALID",
        "confidence": 0.97,
        "timestamp": "2024-02-05T00:00:00+00:00",
    }


def test_write_reasoning_trace_json_round_trips_messages(tmp_path):
    investigator_messages = [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
    reviewer_messages = [{"role": "system", "content": "..."}]

    path = write_reasoning_trace_json(
        tmp_path,
        claim_id="CLM-002",
        investigator_messages=investigator_messages,
        reviewer_messages=reviewer_messages,
    )

    written = json.loads(path.read_text())
    assert written["claim_id"] == "CLM-002"
    assert written["investigator"] == investigator_messages
    assert written["reviewer"] == reviewer_messages


def test_write_dispute_packet_md_includes_quantities_and_grounds(tmp_path):
    path = write_dispute_packet_md(
        tmp_path,
        claim_id="CLM-002",
        case_file=SAMPLE_CASE_FILE,
        reviewer_output=SAMPLE_REVIEWER_OUTPUT,
    )

    content = path.read_text()
    assert "CLM-002" in content
    assert "120" in content
    assert "order_date" in content
    assert "Normalized quantities match: 5 CASE = 120 EACH" in content


def test_write_dispute_packet_md_handles_no_dispute_grounds(tmp_path):
    reviewer_output = SAMPLE_REVIEWER_OUTPUT.model_copy(update={"dispute_grounds": []})

    path = write_dispute_packet_md(
        tmp_path,
        claim_id="CLM-002",
        case_file=SAMPLE_CASE_FILE,
        reviewer_output=reviewer_output,
    )

    assert "(none provided)" in path.read_text()


def test_claim_directory_is_created(tmp_path):
    output_dir = tmp_path / "nested" / "outputs"
    write_verdict_json(
        output_dir,
        claim_id="CLM-002",
        investigator_verdict="INVALID",
        reviewer_verdict="CONFIRM",
        final_verdict="INVALID",
        confidence=0.97,
        timestamp="2024-02-05T00:00:00+00:00",
    )

    assert (output_dir / "CLM-002" / "verdict.json").exists()
