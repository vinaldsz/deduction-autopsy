"""Layer 32: cli/process_lot.py — the post-ingestion "process the whole lot" step."""

from types import SimpleNamespace

from rich.console import Console

from cli import process_lot
from ui import queries


def _result(claim_id, final):
    return SimpleNamespace(claim_id=claim_id, final_verdict=final)


async def test_processes_active_lot_and_summarizes(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(queries, "active_batch", lambda: {"batch_id": "LOT-X"})
    monkeypatch.setattr(queries, "unresolved_claim_ids", lambda b, cap: ["CLM-1", "CLM-2", "CLM-3"])
    verdicts = {"CLM-1": "VALID", "CLM-2": "INVALID", "CLM-3": "ESCALATE"}
    console = Console(record=True, no_color=True, width=160)

    async def run_pipeline_fn(*, claim_id, openai_client=None, run_id=None):
        return _result(claim_id, verdicts[claim_id])

    code = await process_lot.main([], openai_client=object(), console=console, run_pipeline_fn=run_pipeline_fn)
    out = console.export_text()
    assert code == 0
    assert "3 resolved" in out and "VALID 1" in out and "INVALID 1" in out and "ESCALATE 1" in out


async def test_no_lot_returns_error(monkeypatch):
    monkeypatch.setattr(queries, "active_batch", lambda: None)
    console = Console(record=True, no_color=True, width=160)
    code = await process_lot.main([], openai_client=object(), console=console,
                                  run_pipeline_fn=None)
    assert code == 1


async def test_nothing_unresolved_is_noop(monkeypatch):
    monkeypatch.setattr(queries, "active_batch", lambda: {"batch_id": "LOT-X"})
    monkeypatch.setattr(queries, "unresolved_claim_ids", lambda b, cap: [])
    console = Console(record=True, no_color=True, width=160)
    code = await process_lot.main([], openai_client=object(), console=console, run_pipeline_fn=None)
    assert code == 0
    assert "nothing to process" in console.export_text()
