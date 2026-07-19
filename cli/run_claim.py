import argparse
import asyncio
from typing import Any

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from agents.base import AgentRunnerError
from cli._common import ensure_api_key
from orchestrator.pipeline import PipelineError, PipelineResult, run_pipeline


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one deduction claim end-to-end.")
    parser.add_argument("--claim-id", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--max-attempts", type=int, default=3)
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

    try:
        result = await run_pipeline(
            claim_id=args.claim_id,
            scenario=args.scenario,
            openai_client=openai_client,
            mcp_client=mcp_client,
            output_dir=args.output_dir,
            max_investigator_attempts=args.max_attempts,
        )
    except (PipelineError, AgentRunnerError) as exc:
        console.print(f"[bold red]Pipeline failed:[/] {exc}")
        return 1

    _print_result(console, result)
    return 0


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
