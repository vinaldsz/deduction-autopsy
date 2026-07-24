import json
import re

from orchestrator.output import (
    make_run_id,
    prepare_run_dir,
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


def test_make_run_id_is_filesystem_safe_and_sortable():
    run_id = make_run_id()
    assert re.fullmatch(r"\d{8}T\d{6}Z", run_id)
    assert "/" not in run_id and ":" not in run_id


def test_prepare_run_dir_creates_nested_dir_and_relative_latest_symlink(tmp_path):
    run_dir = prepare_run_dir(tmp_path, "CLM-002", "20240205T000000Z")

    assert run_dir == tmp_path / "CLM-002" / "20240205T000000Z"
    assert run_dir.is_dir()

    latest = tmp_path / "CLM-002" / "latest"
    assert latest.is_symlink()
    # Relative target (not absolute) so the tree stays portable if moved.
    import os

    assert os.readlink(latest) == "20240205T000000Z"
    assert latest.resolve() == run_dir.resolve()


def test_prepare_run_dir_second_run_does_not_clobber_and_repoints_latest(tmp_path):
    first = prepare_run_dir(tmp_path, "CLM-002", "20240205T000000Z")
    (first / "marker.txt").write_text("first")

    second = prepare_run_dir(tmp_path, "CLM-002", "20240206T111111Z")

    # First run's artifacts survive the second run.
    assert (first / "marker.txt").read_text() == "first"
    # latest now points at the newest run.
    assert (tmp_path / "CLM-002" / "latest").resolve() == second.resolve()


def test_write_verdict_json(tmp_path):
    usage = {
        "investigator": {"prompt_tokens": 10, "completion_tokens": 2},
        "reviewer": {"prompt_tokens": 8, "completion_tokens": 3},
    }
    path = write_verdict_json(
        tmp_path,
        claim_id="CLM-002",
        investigator_verdict="INVALID",
        reviewer_verdict="CONFIRM",
        final_verdict="INVALID",
        confidence=0.97,
        timestamp="2024-02-05T00:00:00+00:00",
        usage=usage,
    )

    assert path == tmp_path / "verdict.json"
    assert json.loads(path.read_text()) == {
        "claim_id": "CLM-002",
        "investigator_verdict": "INVALID",
        "reviewer_verdict": "CONFIRM",
        "final_verdict": "INVALID",
        "confidence": 0.97,
        "timestamp": "2024-02-05T00:00:00+00:00",
        "usage": usage,
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


def test_writers_land_inside_the_prepared_run_dir(tmp_path):
    run_dir = prepare_run_dir(tmp_path, "CLM-002", "20240205T000000Z")
    write_verdict_json(
        run_dir,
        claim_id="CLM-002",
        investigator_verdict="INVALID",
        reviewer_verdict="CONFIRM",
        final_verdict="INVALID",
        confidence=0.97,
        timestamp="2024-02-05T00:00:00+00:00",
        usage={"investigator": {"prompt_tokens": 0, "completion_tokens": 0}, "reviewer": {"prompt_tokens": 0, "completion_tokens": 0}},
    )

    assert (tmp_path / "CLM-002" / "20240205T000000Z" / "verdict.json").exists()
