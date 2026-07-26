# Deduction Autopsy

[![Tests](https://github.com/vinaldsz/deduction-autopsy/actions/workflows/tests.yml/badge.svg)](https://github.com/vinaldsz/deduction-autopsy/actions/workflows/tests.yml)

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

This project was built layer by layer (see [`PROGRESS.md`](PROGRESS.md) for the
authoritative, up-to-date state). All layers are complete:

| Layer | What | Status |
|---|---|---|
| 1 | `mcp_server/models.py` + UOM conversion table | ✅ Done |
| 2 | 7 scenario fixtures + fixture validation tests | ✅ Done |
| 3 | `mcp_server/fixtures.py` + `mcp_server/tools/` | ✅ Done |
| 4 | `mcp_server/server.py` (FastMCP) | ✅ Done |
| 5 | `agents/base.py` | ✅ Done |
| 6 | `agents/investigator.py` + `agents/reviewer.py` | ✅ Done |
| 7 | `orchestrator/pipeline.py` + `orchestrator/output.py` | ✅ Done |
| 8 | `cli/run_claim.py` + `cli/run_all.py` | ✅ Done |
| 9 | Integration tests + README | ✅ Done |
| 10 | `scenarios/s08_reviewer_overturn/` (8th scenario) | ✅ Done |
| 11 | CLI demo mode (`--explain` flag) | ✅ Done |
| 12 | `orchestrator/config.py` (consolidated settings) | ✅ Done |
| 13 | Retry/backoff + timeout around OpenRouter calls | ✅ Done |
| 14 | Token/cost usage capture | ✅ Done |
| 15 | CI (`.github/workflows/tests.yml`) | ✅ Done |
| 16 | Structured logging | ✅ Done |
| 17 | Non-overwriting output runs (`--run-id` + `latest`) | ✅ Done |
| 18 | Prompt-injection regression test | ✅ Done |
| 19 | Web UI — FastAPI investigate endpoint | ✅ Done |
| 20 | Web UI — SSE streaming endpoint | ✅ Done |
| 21 | Web UI — static frontend (`ui/static/`) | ✅ Done |
| 22 | Web UI — UI tests (`tests/test_ui_server.py`) | ✅ Done |
| 23 | Data model & source-mapping design (schema + `mcp_server/db.py`) | ✅ Done |
| 24 | Heterogeneous source-system fixtures + generator (`source_systems/`, `tools/generate_source_systems.py`) | ✅ Done |
| 25 | ETL Extract — per-source parsers (`semantic_layer/extract/`) | ✅ Done |

## Setup

```bash
uv venv
uv pip install -e ".[dev]"
```

Requires Python 3.11+. Copy `.env.example` to `.env` and set `OPENROUTER_API_KEY` (get one
at https://openrouter.ai/keys) — this is required to run the CLI or the integration tests;
unit tests don't need it.

## Running the CLI

Investigate a single claim end-to-end:

```bash
python -m cli.run_claim --claim-id CLM-002 --scenario s02_casepack_mismatch
```

Add `--explain` to watch the two-agent split live — each agent's tool calls as they happen,
the stripped CaseFile handed from the Investigator to the Reviewer, and the Reviewer's six
per-check findings (with which ones triggered a re-fetch):

```bash
python -m cli.run_claim --claim-id CLM-002 --scenario s02_casepack_mismatch --explain
```

Run all 8 scenarios and print a pass/fail table against ground truth:

```bash
python -m cli.run_all
```

Each claim run writes its artifacts to `outputs/<claim_id>/<run_id>/` (the `run_id` defaults
to a UTC timestamp, or pass `--run-id`), so reruns are archived side by side instead of
overwriting. `outputs/<claim_id>/latest` is a symlink to the most recent run; `run_all` uses
one shared `run_id` across every claim in the batch.

- `verdict.json` — investigator/reviewer/final verdicts, confidence, timestamp (always written)
- `reasoning_trace.json` — full message history for both agents, including every tool call (always written)
- `dispute_packet.md` — normalized quantities, timeline, and dispute grounds (only when `final_verdict` is `INVALID`)

## Running the Web UI

The UI is an additive second entry point onto the same pipeline (the CLI is kept). It binds to
`127.0.0.1` only, with no auth or rate limiting — same trust model as the CLI.

```bash
uvicorn ui.server:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/ for the minimal browser UI: pick a scenario from the dropdown
(the claim ID auto-fills), hit **Run investigation**, and watch each agent's tool calls stream
in live, followed by a verdict card (and, on an `INVALID` final verdict, the Reviewer's dispute
grounds). It's a dependency-free static client (`ui/static/`, no build step) over the same SSE
endpoint below.

`GET /api/scenarios` lists the eight scenarios and their claim IDs (the dropdown's data source).

`POST /api/claims/{claim_id}/investigate?scenario=<id>` runs the pipeline and returns the
verdict JSON (same fields as `verdict.json`, plus `dispute_grounds`):

```bash
curl -X POST "http://127.0.0.1:8000/api/claims/CLM-002/investigate?scenario=s02_casepack_mismatch"
```

An unknown `scenario` returns 404; an upstream (OpenRouter/agent) failure returns 502
`{"error": ...}`.

`GET /api/claims/{claim_id}/stream?scenario=<id>` runs the same pipeline as a Server-Sent
Events stream — one `tool_call` event per tool call (tagged with the `agent` that made it) in
call order, then a final `done` event carrying the same body as `/investigate` (or a single
`error` event if the pipeline fails):

```bash
curl -N "http://127.0.0.1:8000/api/claims/CLM-002/stream?scenario=s02_casepack_mismatch"
```

## Running tests

```bash
# Unit tests — no API key needed, runs by default
pytest tests/ -v

# Integration tests — hits the real OpenRouter API, costs money and time, requires
# OPENROUTER_API_KEY. Excluded from the default `pytest tests/` run (see pyproject.toml);
# opt in explicitly:
pytest tests/test_pipeline_scenarios.py -m integration -v
```

Unit tests mock OpenRouter responses (`tests/agent_stubs.py`) but always exercise the real
MCP server in-process — no test hits OpenRouter or spawns a subprocess except the
integration suite. The integration suite runs `run_pipeline` directly (not through the CLI)
for all 8 scenarios and asserts both agents' verdicts match `orchestrator/ground_truth.py`,
plus a dedicated test (`test_reviewer_overturns_a_missed_duplicate`) proving the Reviewer's
spot-check independently catches and overturns a fabricated wrong CaseFile — see "The eight
scenarios" below for why that's a separate test rather than part of `s08`'s own ground truth.

## The eight scenarios

Ground truth for the full pipeline (Investigator verdict → Reviewer's final verdict).
Scenarios 1-7's expected verdicts are fixed — see [`docs/SPEC.md`](docs/SPEC.md) for full
detail.

| # | Scenario | Investigator | Final | The trap |
|---|---|---|---|---|
| 1 | `s01_clean_shortage` | VALID | CONFIRM | All docs genuinely agree on a 12-unit shortage |
| 2 | `s02_casepack_mismatch` | INVALID | CONFIRM | PO in CASE, ASN in EACH — naive diff looks like a shortage; UOM-normalized quantities match |
| 3 | `s03_split_shipment` | INVALID | CONFIRM | Shipment split across two ASNs; retailer only counted the first |
| 4 | `s04_sequence_violation` | INVALID | CONFIRM | Invoice date precedes ship date — an impossible timeline |
| 5 | `s05_sku_substitution` | INVALID | CONFIRM | ASN SKU differs from PO SKU, but receiving notes show explicit pre-approval |
| 6 | `s06_promo_billback` | INVALID | CONFIRM | Trade agreement exists, but for a different promo code than the claim cites |
| 7 | `s07_duplicate_claim` | INVALID | CONFIRM | Claim duplicates a prior claim already resolved via credit memo |
| 8 | `s08_reviewer_overturn` | INVALID | CONFIRM | Same shape as #7, independent fixture data. Originally designed to make the Investigator miss a subtly-worded prior credit and force a real Reviewer `OVERTURN` — live-tested during Layer 10 and found the Investigator already catches it (see `docs/SPEC.md`'s "Eighth Scenario" section for the full story). The `OVERTURN` case is instead proven directly: `test_reviewer_overturns_a_missed_duplicate` hands the live Reviewer a fabricated CaseFile against these same fixtures and confirms it independently catches and overturns the duplicate. |

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
- Production concerns: auth, multi-tenancy, persistence beyond local files

## Future work

These are deliberately out of scope for the current build (see [`CLAUDE.md`](CLAUDE.md)'s
"Explicit out of scope" section for the authoritative list):

- **Parallel/concurrent orchestration** — scenarios and claims currently run sequentially.
- **SKU-to-product-name mapping** — SKUs stay opaque codes (e.g. `SKU-001`) everywhere; a
  display-only product catalog for dispute packets would be cosmetic, not functional.
- **Incremental / CDC source extraction** — extraction reads whole source files each run;
  there is no "pull only what changed since last watermark." Modelling the sources as stateful
  stores with change-data-capture is real DE depth, but it changes nothing the agents or the
  reconciliation demo exercise, so it stays deferred. (Note: the *load* side already does
  incremental merge-upsert by PK — earlier lots then today's lot on top.)
- **Transient / streaming landing zone** — `source_systems/` is a mock landing zone whose files
  are git-tracked for reproducibility (see `docs/SPEC.md`). Making it a genuinely transient drop
  (gitignored, feeds "arrive" and are archived) or a streamed feed would be more faithful to real
  ingestion topology, but buys little for a local, single-user, secondary-DE build.
- **API-facing deployment concerns** — auth, per-user/per-IP rate limiting, and per-user
  cost caps on OpenRouter usage only become a real concern if this sits behind a
  frontend/web UI instead of a locally-run CLI.

See [`CLAUDE.md`](CLAUDE.md) for the full set of build and safeguard rules this project
follows.
