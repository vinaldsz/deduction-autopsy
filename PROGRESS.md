# Progress

## Current layer
**Layer 0 — Foundation files complete**

CLAUDE.md, docs/SPEC.md, and PROGRESS.md written. No code yet.

## Next session
Start **Layer 1**: `mcp_server/models.py` + `data/sku_uom_conversions.json`

Then **Layer 2**: All 7 scenario fixture JSON files.
Run `pytest tests/test_fixtures.py` — all fixtures must validate against Pydantic models
before any tool code is written.

## Layer status

| Layer | What | Status |
|---|---|---|
| 0 | CLAUDE.md, SPEC.md, PROGRESS.md | ✅ Done |
| 1 | `mcp_server/models.py` + UOM conversion table | ⬜ Not started |
| 2 | All 7 scenario fixture JSON files + fixture validation tests | ⬜ Not started |
| 3 | `mcp_server/fixtures.py` + `mcp_server/tools/` + unit tests passing | ⬜ Not started |
| 4 | `mcp_server/server.py` (FastMCP wiring) | ⬜ Not started |
| 5 | `agents/base.py` (shared tool loop) | ⬜ Not started |
| 6 | `agents/investigator.py` + `agents/reviewer.py` | ⬜ Not started |
| 7 | `orchestrator/pipeline.py` + `orchestrator/output.py` | ⬜ Not started |
| 8 | `cli/run_claim.py` + `cli/run_all.py` | ⬜ Not started |
| 9 | Integration tests + README | ⬜ Not started |

## Tests passing
None yet.

## Known issues / decisions pending
None.

---
*Update this file at the end of every session before stopping.*
