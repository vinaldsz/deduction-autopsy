import argparse
import asyncio
import os
import sys
from typing import Any, Awaitable, Callable

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from agents.base import AgentRunnerError
from cli._common import configure_logging, ensure_api_key
from orchestrator.config import SETTINGS
from orchestrator.ground_truth import GROUND_TRUTH
from orchestrator.pipeline import OPENROUTER_BASE_URL, PipelineError, run_pipeline


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run all 7 ground-truth scenarios end-to-end and print a pass/fail table."
    )
    return parser.parse_args(argv)


async def main(
    argv: list[str] | None = None,
    *,
    openai_client: Any | None = None,
    console: Console | None = None,
    run_pipeline_fn: Callable[..., Awaitable[Any]] = run_pipeline,
) -> int:
    # Takes no real options — this exists so `--help` and unrecognized arguments (e.g. a typo'd
    # flag) fail fast with argparse's usage message instead of silently running all 7 scenarios
    # for real against a live API key. Note: default here means "no args", NOT "use sys.argv" —
    # argparse.parse_args(None) would fall back to real sys.argv, which breaks callers (tests)
    # that omit argv entirely. Real __main__ invocation passes sys.argv[1:] explicitly below.
    parse_args([] if argv is None else argv)
    configure_logging()
    console = console or Console()

    if openai_client is None and not ensure_api_key(console):
        return 1

    if openai_client is None:
        from openai import AsyncOpenAI

        openai_client = AsyncOpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=os.environ["OPENROUTER_API_KEY"],
            timeout=SETTINGS.openrouter_timeout_seconds,
        )

    table = Table(title="Deduction Autopsy — all scenarios")
    for column in (
        "Scenario",
        "Claim ID",
        "Investigator (actual/expected)",
        "Reviewer (actual/expected)",
        "Result",
    ):
        table.add_column(column)

    passed = 0
    for case in GROUND_TRUTH:
        try:
            # mcp_client is intentionally left unset: each scenario needs its own subprocess
            # with a different SCENARIO_ID env var. Only openai_client is shared/reused.
            result = await run_pipeline_fn(
                claim_id=case["claim_id"],
                scenario=case["scenario"],
                openai_client=openai_client,
            )
        except (PipelineError, AgentRunnerError) as exc:
            table.add_row(case["scenario"], case["claim_id"], "ERROR", str(exc), "[bold red]FAIL[/]")
            continue

        inv_ok = result.investigator_verdict == case["expected_investigator"]
        rev_ok = result.reviewer_verdict == case["expected_reviewer"]
        ok = inv_ok and rev_ok
        passed += int(ok)
        table.add_row(
            case["scenario"],
            case["claim_id"],
            f"{result.investigator_verdict}/{case['expected_investigator']}",
            f"{result.reviewer_verdict}/{case['expected_reviewer']}",
            "[bold green]PASS[/]" if ok else "[bold red]FAIL[/]",
        )

    console.print(table)
    console.print(f"\n{passed}/{len(GROUND_TRUTH)} passed")
    return 0 if passed == len(GROUND_TRUTH) else 1


if __name__ == "__main__":
    load_dotenv()
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
