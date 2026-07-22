import json
from pathlib import Path
from typing import Any


def _claim_dir(output_dir: Path, claim_id: str) -> Path:
    claim_dir = Path(output_dir) / claim_id
    claim_dir.mkdir(parents=True, exist_ok=True)
    return claim_dir


def write_verdict_json(
    output_dir: Path,
    *,
    claim_id: str,
    investigator_verdict: str,
    reviewer_verdict: str,
    final_verdict: str,
    confidence: float,
    timestamp: str,
    usage: dict,
) -> Path:
    path = _claim_dir(output_dir, claim_id) / "verdict.json"
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
    output_dir: Path,
    *,
    claim_id: str,
    investigator_messages: list[dict],
    reviewer_messages: list[dict],
) -> Path:
    path = _claim_dir(output_dir, claim_id) / "reasoning_trace.json"
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
    output_dir: Path,
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

    path = _claim_dir(output_dir, claim_id) / "dispute_packet.md"
    path.write_text("\n".join(lines) + "\n")
    return path
