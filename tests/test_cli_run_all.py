from types import SimpleNamespace

import pytest
from rich.console import Console

from cli.run_all import GROUND_TRUTH, main, parse_args
from orchestrator.config import SETTINGS
from orchestrator.pipeline import PipelineError


def test_parse_args_rejects_unrecognized_flags():
    """Regression test: run_all.py must not silently run for real on a bad flag (e.g. --help
    or a typo) — it should fail fast via argparse instead of falling through to main()'s body."""
    with pytest.raises(SystemExit):
        parse_args(["--bogus"])
    with pytest.raises(SystemExit):
        parse_args(["--help"])
    parse_args([])  # no args is the only supported invocation — must not raise


def _fake_result(claim_id, investigator_verdict, reviewer_verdict, final_verdict=None):
    return SimpleNamespace(
        claim_id=claim_id,
        investigator_verdict=investigator_verdict,
        reviewer_verdict=reviewer_verdict,
        final_verdict=final_verdict if final_verdict is not None else investigator_verdict,
    )


def _all_pass_fn(*, claim_id, scenario, openai_client=None):
    case = next(c for c in GROUND_TRUTH if c["claim_id"] == claim_id)
    return _fake_result(claim_id, case["expected_investigator"], case["expected_reviewer"])


async def test_all_scenarios_pass_returns_zero_and_full_summary(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    console = Console(record=True, no_color=True, width=160)

    async def run_pipeline_fn(*, claim_id, scenario, openai_client=None):
        return _all_pass_fn(claim_id=claim_id, scenario=scenario, openai_client=openai_client)

    exit_code = await main(openai_client=object(), console=console, run_pipeline_fn=run_pipeline_fn)

    assert exit_code == 0
    assert f"{len(GROUND_TRUTH)}/{len(GROUND_TRUTH)} passed" in console.export_text()


async def test_investigator_mismatch_fails_that_row_only(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    console = Console(record=True, no_color=True, width=160)

    async def run_pipeline_fn(*, claim_id, scenario, openai_client=None):
        case = next(c for c in GROUND_TRUTH if c["claim_id"] == claim_id)
        if claim_id == "CLM-002":
            return _fake_result(claim_id, "VALID", case["expected_reviewer"])
        return _fake_result(claim_id, case["expected_investigator"], case["expected_reviewer"])

    exit_code = await main(openai_client=object(), console=console, run_pipeline_fn=run_pipeline_fn)

    assert exit_code == 1
    assert f"{len(GROUND_TRUTH) - 1}/{len(GROUND_TRUTH)} passed" in console.export_text()


async def test_pipeline_error_recorded_and_loop_continues(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    console = Console(record=True, no_color=True, width=160)
    seen_claim_ids = []

    async def run_pipeline_fn(*, claim_id, scenario, openai_client=None):
        seen_claim_ids.append(claim_id)
        if claim_id == "CLM-003":
            raise PipelineError("boom")
        case = next(c for c in GROUND_TRUTH if c["claim_id"] == claim_id)
        return _fake_result(claim_id, case["expected_investigator"], case["expected_reviewer"])

    exit_code = await main(openai_client=object(), console=console, run_pipeline_fn=run_pipeline_fn)

    assert exit_code == 1
    assert seen_claim_ids == [c["claim_id"] for c in GROUND_TRUTH]
    output = console.export_text()
    assert f"{len(GROUND_TRUTH) - 1}/{len(GROUND_TRUTH)} passed" in output
    assert "boom" in output


async def test_constructs_openai_client_with_configured_timeout(monkeypatch):
    """Regression guard for Layer 13: when no openai_client is injected, main() must construct
    AsyncOpenAI with the SETTINGS-derived timeout, not the client library's unbounded default."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    console = Console(record=True, no_color=True, width=160)
    captured_kwargs = []

    class _CapturingAsyncOpenAI:
        def __init__(self, **kwargs):
            captured_kwargs.append(kwargs)

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _CapturingAsyncOpenAI)

    async def run_pipeline_fn(*, claim_id, scenario, openai_client=None):
        case = next(c for c in GROUND_TRUTH if c["claim_id"] == claim_id)
        return _fake_result(claim_id, case["expected_investigator"], case["expected_reviewer"])

    await main(console=console, run_pipeline_fn=run_pipeline_fn)

    assert captured_kwargs[0]["timeout"] == SETTINGS.openrouter_timeout_seconds


async def test_missing_api_key_returns_one_without_calling_run_pipeline(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    console = Console(record=True, no_color=True, width=160)
    called = False

    async def run_pipeline_fn(*, claim_id, scenario, openai_client=None):
        nonlocal called
        called = True

    exit_code = await main(console=console, run_pipeline_fn=run_pipeline_fn)

    assert exit_code == 1
    assert called is False
    assert "OPENROUTER_API_KEY is not set" in console.export_text()


async def test_ground_truth_check_uses_reviewer_verdict_not_final_verdict(monkeypatch):
    """Regression test for the SPEC.md vocabulary gotcha (see PROGRESS.md's Layer 8 note):
    'Final expected' is the Reviewer's own verdict (CONFIRM), not final_verdict."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    console = Console(record=True, no_color=True, width=160)

    async def run_pipeline_fn(*, claim_id, scenario, openai_client=None):
        case = next(c for c in GROUND_TRUTH if c["claim_id"] == claim_id)
        # final_verdict deliberately set to something that would NOT match "CONFIRM" if the
        # comparison were (incorrectly) made against it instead of reviewer_verdict.
        return _fake_result(
            claim_id,
            case["expected_investigator"],
            case["expected_reviewer"],
            final_verdict="INVALID",
        )

    exit_code = await main(openai_client=object(), console=console, run_pipeline_fn=run_pipeline_fn)

    assert exit_code == 0
    assert f"{len(GROUND_TRUTH)}/{len(GROUND_TRUTH)} passed" in console.export_text()
