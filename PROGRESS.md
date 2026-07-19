# Progress

## Current layer
**Layer 1 — `mcp_server/models.py` + UOM conversion table complete**

Added `pyproject.toml` (deps: anthropic, fastmcp, pydantic>=2, rich; pytest as dev dep),
`data/sku_uom_conversions.json` (verbatim from SPEC.md), and `mcp_server/models.py` with
Pydantic v2 models for all 6 domain objects (PurchaseOrder, ASN, Invoice, ReceivingRecord,
TradeAgreement, DeductionClaim). UOM fields and `claimed_reason` use `Literal` types for
fail-fast validation. Dates kept as plain `str` (no datetime parsing) and money as `int`
cents, per spec. Verified: all 6 models import and instantiate correctly, invalid UOM
literal is rejected with a `ValidationError`, and the JSON conversion table parses. `.venv`
created via `uv venv` + `uv pip install -e .`.

## Next session
Start **Layer 2**: All 7 scenario fixture JSON files + `tests/test_fixtures.py`.
Run `pytest tests/test_fixtures.py` — all fixtures must validate against Pydantic models
before any tool code is written.

## Layer status

| Layer | What | Status |
|---|---|---|
| 0 | CLAUDE.md, SPEC.md, PROGRESS.md | ✅ Done |
| 1 | `mcp_server/models.py` + UOM conversion table | ✅ Done |
| 2 | All 7 scenario fixture JSON files + fixture validation tests | ⬜ Not started |
| 3 | `mcp_server/fixtures.py` + `mcp_server/tools/` + unit tests passing | ⬜ Not started |
| 4 | `mcp_server/server.py` (FastMCP wiring) | ⬜ Not started |
| 5 | `agents/base.py` (shared tool loop) | ⬜ Not started |
| 6 | `agents/investigator.py` + `agents/reviewer.py` | ⬜ Not started |
| 7 | `orchestrator/pipeline.py` + `orchestrator/output.py` | ⬜ Not started |
| 8 | `cli/run_claim.py` + `cli/run_all.py` | ⬜ Not started |
| 9 | Integration tests + README | ⬜ Not started |

## Tests passing
No pytest suite yet (Layer 2 introduces `tests/test_fixtures.py`). Layer 1 verified via
manual import/instantiation script (see above) — all 6 models pass, Literal validation
rejects bad UOM, JSON conversion table is valid.

## Known issues / decisions pending
- No cross-document referential integrity (po_id/sku consistency across sibling fixture
  files) is enforced at the model layer — worth a lightweight cross-reference check when
  writing `tests/test_fixtures.py` in Layer 2, per plan notes in
  `linked-tickling-kernighan.md`.
- Tech stack note: user is considering OpenRouter as an alternative to calling the
  Anthropic SDK directly for agents. Confirmed this does not affect Layer 1-4 (models,
  fixtures, tools, MCP server); revisit when building `agents/base.py` (Layer 5).

---
*Update this file at the end of every session before stopping.*
