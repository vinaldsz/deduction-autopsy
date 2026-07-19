import argparse
import asyncio
import os
import sys
from typing import Any, Awaitable, Callable

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from agents.base import AgentRunnerError
from orchestrator.pipeline import OPENROUTER_BASE_URL, PipelineError, run_pipeline

# From docs/SPEC.md's ground-truth table. expected_reviewer is uniformly CONFIRM and is in the
# Reviewer's own vocabulary — it must be checked against PipelineResult.reviewer_verdict, not
# .final_verdict (which is business-vocabulary VALID/INVALID/ESCALATE and varies per scenario).
GROUND_TRUTH = [
    {"scenario": "s01_clean_shortage", "claim_id": "CLM-001", "expected_investigator": "VALID", "expected_reviewer": "CONFIRM"},
    {"scenario": "s02_casepack_mismatch", "claim_id": "CLM-002", "expected_investigator": "INVALID", "expected_reviewer": "CONFIRM"},
    {"scenario": "s03_split_shipment", "claim_id": "CLM-003", "expected_investigator": "INVALID", "expected_reviewer": "CONFIRM"},
    {"scenario": "s04_sequence_violation", "claim_id": "CLM-004", "expected_investigator": "INVALID", "expected_reviewer": "CONFIRM"},
    {"scenario": "s05_sku_substitution", "claim_id": "CLM-005", "expected_investigator": "INVALID", "expected_reviewer": "CONFIRM"},
    {"scenario": "s06_promo_billback", "claim_id": "CLM-006", "expected_investigator": "INVALID", "expected_reviewer": "CONFIRM"},
    {"scenario": "s07_duplicate_claim", "claim_id": "CLM-007b", "expected_investigator": "INVALID", "expected_reviewer": "CONFIRM"},
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run all 7 ground-truth scenarios end-to-end and print a pass/fail table."
    )
    return parser.parse_args(argv)


async def main(
    argv: list[str] | None = (),
    *,
    openai_client: Any | None = None,
    console: Console | None = None,
    run_pipeline_fn: Callable[..., Awaitable[Any]] = run_pipeline,
) -> int:
    # Takes no real options — this exists so `--help` and unrecognized arguments (e.g. a typo'd
    # flag) fail fast with argparse's usage message instead of silently running all 7 scenarios
    # for real against a live API key.
    parse_args(argv)
    console = console or Console()

    if openai_client is None and "OPENROUTER_API_KEY" not in os.environ:
        console.print(
            "[bold red]OPENROUTER_API_KEY is not set[/] — add it to .env or export it."
        )
        return 1

    if openai_client is None:
        from openai import AsyncOpenAI

        openai_client = AsyncOpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=os.environ["OPENROUTER_API_KEY"],
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
