# Deduction Autopsy

A two-agent reconciliation system that investigates CPG retailer deduction claims and
determines whether they are **valid**, **invalid** (disputable), or **ambiguous**
(escalate to a human).

An **Investigator** agent gathers all source documents for a claim via MCP tools,
normalizes unit-of-measure differences, and proposes a verdict. A **Reviewer** agent —
a separate agent, on a separate model, that never sees the Investigator's narrative
reasoning — independently spot-checks the highest-risk steps and either **CONFIRM**s,
**OVERTURN**s, or **ESCALATE**s. The two-agent split is a segregation-of-duties control:
the agent that builds a case should not be the one grading it.

Full domain spec: [`docs/SPEC.md`](docs/SPEC.md). Full implementation plan:
[`docs/PLAN.md`](docs/PLAN.md).

## Status

This project is being built layer by layer (see [`PROGRESS.md`](PROGRESS.md) for the
authoritative, up-to-date state). As of now:

| Layer | What | Status |
|---|---|---|
| 1 | `mcp_server/models.py` + UOM conversion table | ✅ Done |
| 2 | 7 scenario fixtures + fixture validation tests | ✅ Done |
| 3 | `mcp_server/fixtures.py` + `mcp_server/tools/` | ⬜ Not started |
| 4 | `mcp_server/server.py` (FastMCP) | ⬜ Not started |
| 5 | `agents/base.py` | ⬜ Not started |
| 6 | `agents/investigator.py` + `agents/reviewer.py` | ⬜ Not started |
| 7 | `orchestrator/pipeline.py` + `orchestrator/output.py` | ⬜ Not started |
| 8 | `cli/run_claim.py` + `cli/run_all.py` | ⬜ Not started |
| 9 | Integration tests | ⬜ Not started |

The MCP server, agents, orchestrator, and CLI don't exist yet — only the data models and
scenario fixtures. The sections below describe what's runnable today.

## Setup

```bash
uv venv
uv pip install -e ".[dev]"
```

Requires Python 3.11+.

## Running tests

```bash
pytest tests/test_fixtures.py -v
```

This validates all 7 scenario fixture files against their Pydantic models and checks
cross-document consistency (`po_id`/`retailer` alignment, expected file layout per
scenario, and each scenario's specific numeric trap).

## The seven scenarios

Ground truth for the full pipeline (Investigator verdict → Reviewer's final verdict).
These expected verdicts are fixed — see [`docs/SPEC.md`](docs/SPEC.md) for full detail.

| # | Scenario | Investigator | Final | The trap |
|---|---|---|---|---|
| 1 | `s01_clean_shortage` | VALID | CONFIRM | All docs genuinely agree on a 12-unit shortage |
| 2 | `s02_casepack_mismatch` | INVALID | CONFIRM | PO in CASE, ASN in EACH — naive diff looks like a shortage; UOM-normalized quantities match |
| 3 | `s03_split_shipment` | INVALID | CONFIRM | Shipment split across two ASNs; retailer only counted the first |
| 4 | `s04_sequence_violation` | INVALID | CONFIRM | Invoice date precedes ship date — an impossible timeline |
| 5 | `s05_sku_substitution` | INVALID | CONFIRM | ASN SKU differs from PO SKU, but receiving notes show explicit pre-approval |
| 6 | `s06_promo_billback` | INVALID | CONFIRM | Trade agreement exists, but for a different promo code than the claim cites |
| 7 | `s07_duplicate_claim` | INVALID | CONFIRM | Claim duplicates a prior claim already resolved via credit memo |

## Non-negotiable design decisions

- **Two agents, always.** Never collapsed into one agent.
- **MCP server is the only data access path.** Agents never see fixture files directly —
  every document access is a traceable tool call.
- **The seven scenarios are ground truth.** Fixture data doesn't change to make a test
  pass; agent prompts and tool logic do.

## Explicit non-goals

- Real EDI X12 parsing (fixtures resemble real documents, not valid EDI)
- Third-party integrations (NetSuite, Shopify, Amazon, etc.)
- A frontend/UI — CLI output and markdown evidence packets only
- Parallel/concurrent orchestration
- SKU-to-product-name mapping — SKUs stay opaque codes everywhere
- Heterogeneous mock data sources (relational DB, CSV/Excel, etc. behind `FixtureLoader`,
  to recreate the messiness of real multi-system data landscapes) — planned as a
  post-launch enhancement once the in-scope build is complete, not part of the current plan
- Production concerns: auth, multi-tenancy, persistence beyond local files

See [`CLAUDE.md`](CLAUDE.md) for the full set of build and safeguard rules this project
follows.
