import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def make_run_id() -> str:
    """Default run id: a UTC timestamp that is filesystem-safe (no colons) and lexically sortable."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def prepare_run_dir(output_dir: Path, claim_id: str, run_id: str) -> Path:
    """Create outputs/<claim_id>/<run_id>/ and (re)point outputs/<claim_id>/latest at it.

    Artifacts are archived per run instead of overwriting outputs/<claim_id>/, so
    reasoning_trace.json (a documented audit artifact) survives reruns. `latest` is a
    *relative* symlink to the newest run_id, giving the common "show me the last run" case a
    stable path without duplicating files.
    """
    claim_dir = Path(output_dir) / claim_id
    run_dir = claim_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    latest = claim_dir / "latest"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    os.symlink(run_id, latest, target_is_directory=True)

    return run_dir


def write_verdict_json(
    run_dir: Path,
    *,
    claim_id: str,
    investigator_verdict: str,
    reviewer_verdict: str,
    final_verdict: str,
    confidence: float,
    timestamp: str,
    usage: dict,
) -> Path:
    path = Path(run_dir) / "verdict.json"
    path.write_text(
        json.dumps(
            {
                "claim_id": claim_id,
                "investigator_verdict": investigator_verdict,
                "reviewer_verdict": reviewer_verdict,
                "final_verdict": final_verdict,
                "confidence": confidence,
                "timestamp": timestamp,
                "usage": usage,
            },
            indent=2,
        )
    )
    return path


def write_reasoning_trace_json(
    run_dir: Path,
    *,
    claim_id: str,
    investigator_messages: list[dict],
    reviewer_messages: list[dict],
) -> Path:
    path = Path(run_dir) / "reasoning_trace.json"
    path.write_text(
        json.dumps(
            {
                "claim_id": claim_id,
                "investigator": investigator_messages,
                "reviewer": reviewer_messages,
            },
            indent=2,
        )
    )
    return path


def write_dispute_packet_md(
    run_dir: Path,
    *,
    claim_id: str,
    case_file: Any,
    reviewer_output: Any,
) -> Path:
    po_summary = case_file.po_summary
    lines = [
        f"# Dispute Packet — {claim_id}",
        "",
        "## Normalized Quantities (EACH)",
        "",
        "| | Ordered | Shipped | Received | Invoiced |",
        "|---|---|---|---|---|",
        (
            f"| Qty | {po_summary.ordered_qty_each} | {po_summary.shipped_qty_each} | "
            f"{po_summary.received_qty_each} | {po_summary.invoiced_qty_each} |"
        ),
        "",
        "## Timeline",
        "",
        "| Event | Date | Valid |",
        "|---|---|---|",
    ]
    for event in case_file.timeline:
        lines.append(f"| {event.event} | {event.date} | {event.valid} |")

    lines += ["", "## Dispute Grounds", ""]
    if reviewer_output.dispute_grounds:
        lines += [f"- {ground}" for ground in reviewer_output.dispute_grounds]
    else:
        lines.append("- (none provided)")

    path = Path(run_dir) / "dispute_packet.md"
    path.write_text("\n".join(lines) + "\n")
    return path
