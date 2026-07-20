import argparse
import asyncio
from typing import Any

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from agents.base import AgentRunnerError, ToolCallRecord
from cli._common import ensure_api_key
from orchestrator.ground_truth import GROUND_TRUTH
from orchestrator.pipeline import PipelineError, PipelineResult, run_pipeline, strip_reasoning

# Which MCP tool call, if any, independently re-verifies each of ReviewFindings' six checks —
# used only to annotate --explain output; timeline/substitution have no dedicated re-fetch tool
# because the Reviewer's own prompt re-derives them from documents it already fetched.
CHECK_RE_FETCH_TOOL: dict[str, str | None] = {
    "uom_check": "normalize_uom",
    "split_shipment_check": "get_asns_for_po",
    "timeline_check": None,
    "trade_agreement_check": "get_trade_agreement",
    "duplicate_check": "list_claims_for_po",
    "substitution_check": None,
}

_FINDING_STYLE = {"PASS": "green", "FAIL": "red", "N/A": "dim"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one deduction claim end-to-end.")
    parser.add_argument("--claim-id", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Render each agent's tool calls, the stripped CaseFile handoff, and the "
        "Reviewer's per-check findings live as the pipeline runs.",
    )
    return parser.parse_args(argv)


async def main(
    argv: list[str] | None = None,
    *,
    openai_client: Any | None = None,
    mcp_client: Any | None = None,
    console: Console | None = None,
) -> int:
    args = parse_args(argv)
    console = console or Console()

    if openai_client is None and mcp_client is None and not ensure_api_key(console):
        return 1

    on_investigator_tool_call = None
    on_reviewer_tool_call = None
    reviewer_trace: list[ToolCallRecord] = []

    if args.explain:
        console.print("[bold]Investigator[/]")

        def on_investigator_tool_call(record: ToolCallRecord) -> None:
            _print_tool_call(console, "investigator", record)

        reviewer_header_printed = False

        def on_reviewer_tool_call(record: ToolCallRecord) -> None:
            nonlocal reviewer_header_printed
            if not reviewer_header_printed:
                console.print("\n[bold]Reviewer[/]")
                reviewer_header_printed = True
            reviewer_trace.append(record)
            _print_tool_call(console, "reviewer", record)

    try:
        result = await run_pipeline(
            claim_id=args.claim_id,
            scenario=args.scenario,
            openai_client=openai_client,
            mcp_client=mcp_client,
            output_dir=args.output_dir,
            max_investigator_attempts=args.max_attempts,
            on_investigator_tool_call=on_investigator_tool_call,
            on_reviewer_tool_call=on_reviewer_tool_call,
        )
    except (PipelineError, AgentRunnerError) as exc:
        console.print(f"[bold red]Pipeline failed:[/] {exc}")
        return 1

    if args.explain:
        _print_explain_summary(console, args.scenario, result, reviewer_trace)

    _print_result(console, result)
    return 0


def _print_tool_call(console: Console, label: str, record: ToolCallRecord) -> None:
    args_str = ", ".join(f"{key}={value!r}" for key, value in record.args.items())
    result_summary = record.result if len(record.result) <= 160 else f"{record.result[:157]}..."
    style = "red" if record.is_error else "dim"
    console.print(f"  [{style}]{label}[/] [cyan]{record.name}[/]({args_str}) -> {result_summary}")


def _print_explain_summary(
    console: Console,
    scenario: str,
    result: PipelineResult,
    reviewer_trace: list[ToolCallRecord],
) -> None:
    console.print("\n[bold]CaseFile handed to Reviewer[/] [dim](reasoning stripped)[/]")
    console.print_json(data=strip_reasoning(result.case_file))

    console.print("\n[bold]Reviewer checks[/]")
    re_fetched_tools = {record.name for record in reviewer_trace if not record.is_error}
    for check_name, value in result.reviewer_output.review_findings.model_dump().items():
        tool = CHECK_RE_FETCH_TOOL.get(check_name)
        if tool is None:
            provenance = "verified from already-fetched documents"
        elif tool in re_fetched_tools:
            provenance = f"re-fetched via {tool}"
        else:
            provenance = f"not re-fetched (would use {tool})"
        console.print(f"  {check_name}: [{_FINDING_STYLE[value]}]{value}[/] ({provenance})")

    if result.final_verdict != result.investigator_verdict:
        trap = next((case["trap"] for case in GROUND_TRUTH if case["scenario"] == scenario), None)
        console.print("\n[bold yellow]Reviewer overturned the Investigator[/]")
        if trap:
            console.print(f"  Scenario trap: {trap}")


def _print_result(console: Console, result: PipelineResult) -> None:
    table = Table(title=f"Claim {result.claim_id}", show_header=False)
    table.add_row("Investigator verdict", result.investigator_verdict)
    table.add_row("Reviewer verdict", result.reviewer_verdict)
    table.add_row("Final verdict", result.final_verdict)
    table.add_row("Confidence", f"{result.confidence:.2f}")
    table.add_row("Output dir", str(result.output_dir / result.claim_id))
    console.print(table)

    if result.reviewer_output.dispute_grounds:
        console.print("[bold]Dispute grounds:[/]")
        for ground in result.reviewer_output.dispute_grounds:
            console.print(f"  - {ground}")


if __name__ == "__main__":
    load_dotenv()
    raise SystemExit(asyncio.run(main()))
