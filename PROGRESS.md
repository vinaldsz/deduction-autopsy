# Progress

## Current layer
**Layer 3 — FixtureLoader + MCP tool functions + unit tests complete**

Built `mcp_server/fixtures.py` (`FixtureLoader`): resolves the active scenario directory
from the `SCENARIO_ID` env var via prefix glob (`scenarios/{SCENARIO_ID}*`), so either a
short code (`s01`) or full directory name (`s01_clean_shortage`) works; raises `ValueError`
if that doesn't resolve to exactly one directory. Loads `po.json`/`invoice.json`/
`receiving_record.json` directly, globs `asn*.json` sorted (handles both single `asn.json`
and split `asn_1.json`+`asn_2.json`), globs `*claim*.json` sorted (handles both single
`deduction_claim.json` and s07's `prior_claim.json`+`deduction_claim.json`), and returns
`None` from `get_trade_agreement()` when the file is absent. No caching — re-reads from
disk each call, since fixtures are tiny and this keeps tests free of cross-test state.

Built `mcp_server/tools/document_tools.py` and `mcp_server/tools/uom_tools.py` matching the
exact signatures in `docs/SPEC.md`'s MCP Tools table:
- `get_po`/`get_invoice`/`get_receiving_record`/`get_asns_for_po` all validate the requested
  `po_id` against the scenario's actual PO and raise `ValueError` on mismatch (a design
  choice confirmed with the user — catches agent mistakes early rather than silently
  ignoring the argument)
- `get_trade_agreement(retailer, sku, promo_code)` returns `None` on any field mismatch,
  which is what makes s06 resolve correctly (claim cites `PROMO-SUMMER-2024`, fixture has
  `PROMO-SPRING-2024`)
- `get_deduction_claim(claim_id)` searches all `*claim*.json` files for a matching
  `claim_id`, which is what resolves `CLM-007a` (prior, resolved) vs `CLM-007b` (current,
  duplicate) to the correct file in s07
- `list_claims_for_po(po_id)` returns all claim_ids for that po_id — both s07 claims
- `normalize_uom` builds an undirected weighted graph per-SKU from
  `sku_uom_conversions.json` and BFS's from `from_uom` to `to_uom`, accumulating the
  multiplier — handles SKU-002's multi-hop PALLET→CASE→EACH (40×24=960) as well as direct
  and reverse-direction conversions; raises `ValueError` for both an unknown SKU and a
  known SKU with no path to the requested UOM
- Each tool function constructs a fresh `FixtureLoader()` per call (reads `SCENARIO_ID`
  from env each time, no module-level singleton) — this is what lets
  `tests/test_document_tools.py` flip scenarios per-test via `monkeypatch.setenv`

Wrote `tests/test_uom_tools.py` (6 tests) and `tests/test_document_tools.py` (7 tests), all
passing alongside the existing `test_fixtures.py` suite (58 total, 0 failed).

---

## Previous layer
**Layer 2 — All 7 scenario fixture JSON files + fixture validation tests complete**

Built out `scenarios/s01_clean_shortage/` through `scenarios/s07_duplicate_claim/` per the
layout in `docs/SPEC.md` (po, asn, invoice, receiving_record, deduction_claim always;
`asn_1.json`+`asn_2.json` split for s03; `trade_agreement.json` only in s06;
`prior_claim.json` only in s07). Each scenario's numbers were built to encode its specific
trap:
- s01: clean 12-unit shortage, all docs agree (uses the exact po_summary example numbers
  from `docs/SPEC.md`'s CaseFile schema — 120/120/108/120, $30.00 discrepancy)
- s02: PO=5 CASE vs ASN/invoice/receipt=120 EACH for SKU-002 (factor 24) — naive diff reads
  as a 115-unit shortage, `normalize_uom` shows an exact match
- s03: PO=720 EACH, split into two 360-unit ASNs; receiving totals the full PO but retailer
  claim only accounts for the first ASN
- s04: invoice_date (2024-04-08) precedes ship_date (2024-04-10) — physically impossible
  sequence
- s05: ASN/receiving sku=SKU-005-ALT vs PO/invoice sku=SKU-005; receiving_record.notes
  contains explicit buyer pre-approval language
- s06: trade_agreement.json exists for promo_code=PROMO-SPRING-2024; claim's retailer_notes
  cites PROMO-SUMMER-2024 — `get_trade_agreement` will return `None` for the claimed code
- s07: prior_claim.json (CLM-007a) notes say "RESOLVED - credit memo CM-007 issued
  2024-06-10"; deduction_claim.json (CLM-007b) re-claims the same 12-unit shortage on the
  same PO a month later

Wrote `tests/test_fixtures.py` (45 tests, all passing): every fixture file validates
against its Pydantic model; po_id is consistent across sibling documents in a scenario
(trade_agreement.json excluded — it has no po_id field per spec); retailer matches between
po.json and deduction_claim.json; claim_id matches the ground-truth table in SPEC.md; file
layout matches expectations (single asn.json vs split asn_1/asn_2, trade_agreement only in
s06, prior_claim only in s07); plus scenario-specific numeric/trap assertions (s02 case-pack
match, s03 split sum, s04 date ordering, s05 sku divergence, s06 promo mismatch, s07
resolved-duplicate) and a cross-check that every sku referenced anywhere in fixtures has an
entry in `data/sku_uom_conversions.json`. Installed `pytest` into `.venv` via
`uv pip install pytest` (already declared as a dev dependency in `pyproject.toml`, just
hadn't been installed yet).

## Next session
Start **Layer 4**: `mcp_server/server.py` — wire up FastMCP, reading `SCENARIO_ID` from env
at startup and exposing the 8 functions in `mcp_server/tools/` (`document_tools.py`,
`uom_tools.py`) as MCP tools with the exact signatures from `docs/SPEC.md`'s MCP Tools
table. The tool functions themselves already read `SCENARIO_ID` fresh from env per call, so
server.py mainly needs to register them with FastMCP and launch as a stdio server (per
`docs/PLAN.md`'s orchestrator handoff pattern: "Launch FastMCP server as subprocess with
`SCENARIO_ID` env var").

## Layer status

| Layer | What | Status |
|---|---|---|
| 0 | CLAUDE.md, SPEC.md, PROGRESS.md | ✅ Done |
| 1 | `mcp_server/models.py` + UOM conversion table | ✅ Done |
| 2 | All 7 scenario fixture JSON files + fixture validation tests | ✅ Done |
| 3 | `mcp_server/fixtures.py` + `mcp_server/tools/` + unit tests passing | ✅ Done |
| 4 | `mcp_server/server.py` (FastMCP wiring) | ⬜ Not started |
| 5 | `agents/base.py` (shared tool loop) | ⬜ Not started |
| 6 | `agents/investigator.py` + `agents/reviewer.py` | ⬜ Not started |
| 7 | `orchestrator/pipeline.py` + `orchestrator/output.py` | ⬜ Not started |
| 8 | `cli/run_claim.py` + `cli/run_all.py` | ⬜ Not started |
| 9 | Integration tests + README | ⬜ Not started |

## Tests passing
`pytest tests/` — 58 passed, 0 failed:
- `test_fixtures.py` (45): Pydantic model validation for every fixture file, po_id/retailer
  cross-document consistency, file-layout expectations per scenario, ground-truth claim_id
  matches, and each scenario's specific numeric trap.
- `test_uom_tools.py` (6): direct/reverse/multi-hop/same-unit conversions, unknown-SKU and
  undefined-path `ValueError`s.
- `test_document_tools.py` (7): s01 basic wiring, s03 split-ASN aggregation, s06
  trade-agreement promo match/mismatch, s07 claim_id resolution across two claim files,
  po_id mismatch raises.

## Known issues / decisions pending
- Cross-document referential integrity (po_id/sku consistency) is now enforced by
  `tests/test_fixtures.py`, not at the model layer — this is intentional; models stay
  single-document, fixture-level tests catch cross-file drift.
- Tech stack note: user is considering OpenRouter as an alternative to calling the
  Anthropic SDK directly for agents. Confirmed this does not affect Layer 1-4 (models,
  fixtures, tools, MCP server); revisit when building `agents/base.py` (Layer 5).

---
*Update this file at the end of every session before stopping.*
