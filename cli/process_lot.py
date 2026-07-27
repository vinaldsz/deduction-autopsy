"""Process a daily lot end-to-end (Layer 32): run the Investigator + Reviewer over every
unresolved claim in a lot, so an analyst opens the worklist to fully-resolved, evidenced cases.

This is the intended post-ingestion step — run it right after the ETL loads a new lot (e.g. from
the same scheduled job). It's kept out of the ETL module on purpose: the ETL stays pure and
testable, while this makes the real (paid) OpenRouter calls. Defaults to the active lot (latest
load_date) and all its unresolved claims.

    python -m cli.process_lot                 # active lot, all unresolved
    python -m cli.process_lot --batch LOT-...  # a specific lot
    python -m cli.process_lot --cap 5          # limit (smoke test)
"""

import argparse
import asyncio
import os
import sys
from typing import Any, Awaitable, Callable

from dotenv import load_dotenv
from rich.console import Console

from agents.base import AgentRunnerError
from cli._common import configure_logging, ensure_api_key
from orchestrator.config import SETTINGS
from orchestrator.output import make_run_id
from orchestrator.pipeline import OPENROUTER_BASE_URL, PipelineError, run_pipeline
from ui import queries


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process a daily lot end-to-end (investigate + review).")
    parser.add_argument("--batch", default=None, help="Lot/batch id (defaults to the active lot).")
    parser.add_argument("--cap", type=int, default=None, help="Limit number of claims (default: all unresolved).")
    parser.add_argument("--run-id", default=None, help="Shared run id (defaults to a UTC timestamp).")
    return parser.parse_args(argv)


async def main(
    argv: list[str] | None = None,
    *,
    openai_client: Any | None = None,
    console: Console | None = None,
    run_pipeline_fn: Callable[..., Awaitable[Any]] = run_pipeline,
) -> int:
    args = parse_args([] if argv is None else argv)
    configure_logging()
    console = console or Console()

    batch = args.batch or (queries.active_batch() or {}).get("batch_id")
    if not batch:
        console.print("[bold red]No lot found.[/] Load a lot first (ETL), then process it.")
        return 1

    claim_ids = queries.unresolved_claim_ids(batch, args.cap)
    if not claim_ids:
        console.print(f"Lot [bold]{batch}[/] has no unresolved claims — nothing to process.")
        return 0

    run_id = args.run_id or make_run_id()
    if openai_client is None and not ensure_api_key(console):
        return 1
    if openai_client is None:
        from openai import AsyncOpenAI

        openai_client = AsyncOpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=os.environ["OPENROUTER_API_KEY"],
            timeout=SETTINGS.openrouter_timeout_seconds,
        )

    console.print(f"Processing lot [bold]{batch}[/] — {len(claim_ids)} unresolved claim(s)…")
    tally = {"VALID": 0, "INVALID": 0, "ESCALATE": 0}
    errors = 0
    for claim_id in claim_ids:
        try:
            result = await run_pipeline_fn(claim_id=claim_id, openai_client=openai_client, run_id=run_id)
        except (PipelineError, AgentRunnerError) as exc:
            errors += 1
            console.print(f"  [red]ERROR[/] {claim_id}: {exc}")
            continue
        tally[result.final_verdict] = tally.get(result.final_verdict, 0) + 1
        console.print(f"  {claim_id}: [bold]{result.final_verdict}[/]")

    console.print(
        f"\nDone: {sum(tally.values())} resolved "
        f"(VALID {tally['VALID']} · INVALID {tally['INVALID']} · ESCALATE {tally['ESCALATE']})"
        + (f" · [red]{errors} error(s)[/]" if errors else "")
    )
    return 1 if errors else 0


if __name__ == "__main__":
    load_dotenv()
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
