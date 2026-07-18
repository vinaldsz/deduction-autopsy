# Deduction Autopsy — Implementation Plan

## Context
Building a greenfield two-agent CPG deduction reconciliation system from an empty directory.
The system automates the investigation of retailer deduction claims (65-80% are invalid) by
running an Investigator agent that gathers evidence and proposes a verdict, then a Reviewer
agent that independently re-checks raw evidence before anything is finalized. This is a
segregation-of-duties control, not architectural complexity for its own sake.

---

## Folder Structure

```
deduction-autopsy/
├── pyproject.toml                    # deps: anthropic, fastmcp, rich, pytest, pydantic
├── .env.example
├── data/
│   └── sku_uom_conversions.json      # {"SKU-002": {"CASE_to_EACH": 24}, ...}
├── scenarios/
│   ├── s01_clean_shortage/           # po, asn, invoice, receiving_record, deduction_claim
│   ├── s02_casepack_mismatch/        # po in CASE, asn in EACH → normalized match
│   ├── s03_split_shipment/           # asn_1.json + asn_2.json (split across 2 files)
│   ├── s04_sequence_violation/       # invoice_date < ship_date
│   ├── s05_sku_substitution/         # asn sku differs from po sku; notes has approval
│   ├── s06_promo_billback/           # wrong promo_code, trade_agreement.json present but mismatched
│   └── s07_duplicate_claim/          # prior_claim.json (resolved) + deduction_claim.json
├── mcp_server/
│   ├── server.py                     # FastMCP app; reads SCENARIO_ID env var at startup
│   ├── fixtures.py                   # FixtureLoader: loads scenario JSON by filename glob
│   ├── models.py                     # Pydantic models for all 6 domain objects
│   └── tools/
│       ├── document_tools.py         # get_po, get_asns_for_po, get_invoice, etc.
│       └── uom_tools.py              # normalize_uom with BFS over conversion graph
├── agents/
│   ├── base.py                       # AgentRunner: tool loop + trace collection
│   ├── investigator.py               # INVESTIGATOR_SYSTEM_PROMPT + run_investigator()
│   └── reviewer.py                   # REVIEWER_SYSTEM_PROMPT + run_reviewer()
├── orchestrator/
│   ├── pipeline.py                   # run_pipeline(): MCP subprocess → Investigator → Reviewer
│   └── output.py                     # writes verdict.json, dispute_packet.md, reasoning_trace.json
├── cli/
│   ├── run_claim.py                  # python -m cli.run_claim --claim-id CLM-002
│   └── run_all.py                    # iterates all 7 scenarios, prints rich pass/fail table
└── tests/
    ├── conftest.py
    ├── test_fixtures.py              # validates every fixture file against Pydantic models
    ├── test_uom_tools.py             # parametrized normalize_uom unit tests
    ├── test_document_tools.py        # each get_* tool returns correct Pydantic model
    └── test_pipeline_scenarios.py    # integration: each scenario → expected verdict
```

---

## Build Order

1. `mcp_server/models.py` + `data/sku_uom_conversions.json`
2. All 7 scenario fixture JSON files + `tests/test_fixtures.py` passing
3. `mcp_server/fixtures.py` + `mcp_server/tools/` + `tests/test_uom_tools.py` + `tests/test_document_tools.py` passing
4. `mcp_server/server.py` (wire FastMCP)
5. `agents/base.py` (shared tool loop)
6. `agents/investigator.py` + `agents/reviewer.py` (system prompts)
7. `orchestrator/pipeline.py` + `orchestrator/output.py`
8. `cli/run_claim.py` + `cli/run_all.py`
9. Integration tests + README

**Rule:** Do not start layer N+1 until layer N has passing tests.

---

## Key Implementation Details

### `mcp_server/fixtures.py` — FixtureLoader
- Active scenario selected via `SCENARIO_ID` env var at server startup
- `get_asns_for_po`: globs `asn*.json` in scenario dir — handles split shipment (s03 has `asn_1.json`, `asn_2.json`)
- `list_claims_for_po`: globs `*claim*.json` and returns all `claim_id` fields
- `get_trade_agreement(retailer, sku, promo_code)`: loads `trade_agreement.json`, returns `None` if promo_code doesn't match

### `mcp_server/tools/uom_tools.py` — normalize_uom
- Load `sku_uom_conversions.json` once at import
- BFS over conversion graph for multi-hop (PALLET→CASE→EACH)
- Raises `ValueError` loudly on unknown UOM path — agents must handle this explicitly

### `agents/base.py` — AgentRunner
- Standard Anthropic tool-use loop: `create → process tool_use blocks → append results → repeat`
- Logs every tool call name + args to trace list
- Returns `AgentResult(final_text, trace)`
- **Investigator**: `claude-haiku-4-5` (mechanical data-fetch + compare)
- **Reviewer**: `claude-sonnet-4-5` (subtle reasoning needed for trap detection)
- Temperature 0 for both agents

### `orchestrator/pipeline.py` — handoff pattern
1. Launch FastMCP server as subprocess with `SCENARIO_ID` env var
2. Create MCP stdio client (both agents share same connection)
3. Run Investigator → validate CaseFile JSON against required fields schema
4. If required fields missing → send correction message, force another Investigator turn
5. Verify required tool calls appear in Investigator trace (see SPEC.md)
6. Strip `reasoning` field from CaseFile before passing to Reviewer (prevents anchoring)
7. Embed CaseFile in Reviewer's user message inside `<case_file>...</case_file>` tags (prompt injection guard)
8. Reviewer spot-checks only: re-runs `normalize_uom`, re-calls `get_asns_for_po`, `get_trade_agreement`, `list_claims_for_po`
9. Parse Reviewer's JSON output for final verdict
10. Write artifacts to `outputs/{claim_id}/`

### Output artifacts (`outputs/{claim_id}/`)
- `verdict.json` — `{claim_id, investigator_verdict, reviewer_verdict, final_verdict, confidence, timestamp}`
- `dispute_packet.md` — written only when `final_verdict == INVALID`
- `reasoning_trace.json` — full messages arrays from both agents

---

## Scenario Fixture Key Values

| Scenario | Claim ID | Investigator expected | Final expected | Key trap |
|---|---|---|---|---|
| s01_clean_shortage | CLM-001 | VALID | CONFIRM | Receiving notes confirm 12 refused |
| s02_casepack_mismatch | CLM-002 | INVALID | CONFIRM | PO=5 CASE, ASN=120 EACH; normalize_uom resolves match |
| s03_split_shipment | CLM-003 | INVALID | CONFIRM | Two ASN files total to full PO qty |
| s04_sequence_violation | CLM-004 | INVALID | CONFIRM | invoice_date (Apr 8) < ship_date (Apr 10) |
| s05_sku_substitution | CLM-005 | INVALID | CONFIRM | receiving_record.notes has explicit pre-approval text |
| s06_promo_billback | CLM-006 | INVALID | CONFIRM | trade_agreement promo_code = SPRING, claim = SUMMER |
| s07_duplicate_claim | CLM-007b | INVALID | CONFIRM | prior_claim CLM-007a notes = "RESOLVED - credit memo..." |

---

## System Prompt Design

**Investigator** — ordered protocol: (1) collect all docs, (2) normalize all UOMs to EACH,
(3) verify timeline order, (4) reconcile quantities, (5) produce structured CaseFile JSON
with `proposed_verdict: VALID | INVALID | ESCALATE` + confidence. Must not propose verdict
before completing all steps.

**Reviewer** — targeted spot-check (not a full re-investigation): re-run `normalize_uom`
to verify math, re-call `get_asns_for_po` to verify no ASN was missed, re-call
`get_trade_agreement` to verify promo match, re-call `list_claims_for_po` to verify
duplicate detection. CaseFile passed as `<case_file>` XML-delimited block. Output only
the structured JSON verdict object.

---

## Tests

- `test_fixtures.py` — every fixture file in every scenario parses against its Pydantic model
- `test_uom_tools.py` — parametrized: 5 CASE→120 EACH for SKU-002; multi-hop PALLET→EACH; unknown SKU raises ValueError
- `test_document_tools.py` — s03 returns 2 ASNs; s06 get_trade_agreement with wrong promo_code returns None; s07 list_claims_for_po returns both IDs
- `test_pipeline_scenarios.py` — marked `@pytest.mark.integration`; calls run_pipeline; asserts final_verdict matches expected; asserts required tool calls appear in trace

---

## Verification

```bash
# Unit tests (no API key needed)
pytest tests/test_fixtures.py tests/test_uom_tools.py tests/test_document_tools.py -v

# Integration tests (requires ANTHROPIC_API_KEY)
pytest tests/test_pipeline_scenarios.py -m integration -v

# Single claim end-to-end
python -m cli.run_claim --claim-id CLM-002 --scenario s02_casepack_mismatch

# Full pass/fail table
python -m cli.run_all
```
