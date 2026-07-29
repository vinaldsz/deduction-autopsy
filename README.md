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

This project was built layer by layer. The table below is the layer index;
[`PROGRESS.md`](PROGRESS.md) is the narrative log — what each layer decided, what broke, and
what was corrected mid-build. All layers are complete (31 was built last, after 32, because it
was gated on sign-off — it is the only layer that edits the agent prompts):

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
| 26 | ETL Transform + Data Quality (`semantic_layer/transform.py`, `dq_report.py`) | ✅ Done |
| 27 | ETL Load — merge-upsert + lineage + batch gate; fidelity oracle (`semantic_layer/load.py`, `etl.py`) | ✅ Done |
| 28 | DB-backed `FixtureLoader` + document tools (scenario-less) | ✅ Done |
| 29 | Scenario-less pipeline + CLI + resolution persistence | ✅ Done |
| 30a | Synthetic daily lot (~50 claims) — `CLM-SYN` volume for the worklist | ✅ Done |
| 30b | Dashboard + daily-lot worklist UI (`ui/queries.py`, removes `/api/scenarios`) | ✅ Done |
| 31 | Universal completeness check + ESCALATE on missing source data (`orchestrator/completeness.py`) | ✅ Done |
| 32 | Analyst review workspace — evidence-first UI + human decisions (`claim_dispositions`, `cli/process_lot.py`) | ✅ Done |
| 33 | JS test harness + render hygiene (`tests/js/`, `ui/static/lib.js`) | ✅ Done |
| 34 | Decision integrity — accept recorded as a snapshot, not a pointer (`decided_verdict`) | ✅ Done |
| 35 | KPIs that add up — to-do/decided partition, `v_batch_summary` dropped | ✅ Done |
| 36 | Verdict semantics that match the money — tone keyed to money direction, not verdict name | ✅ Done |
| 37a | Query surface for a workable grid — total sort order, filters, filtered totals, 422 on bad input | ✅ Done |
| — | Layer-end verification tooling (`scripts/check.sh`, `/layer-done`, `tests/test_invariants.py`) | ✅ Done |

## Setup

```bash
uv venv
uv pip install -e ".[dev]"
```

Requires Python 3.11+. Copy `.env.example` to `.env` and set `OPENROUTER_API_KEY` (get one
at https://openrouter.ai/keys) — this is required to run the CLI or the integration tests;
unit tests don't need it.

## Running the CLI

First build the relational store the tools read (idempotent; writes `data/deductions.db`):

```bash
python -m semantic_layer.etl
```

Investigate a single claim end-to-end (the agent navigates the entity graph from the claim id —
no scenario):

```bash
python -m cli.run_claim --claim-id CLM-002
```

Add `--explain` to watch the two-agent split live — each agent's tool calls as they happen,
the stripped CaseFile handed from the Investigator to the Reviewer, and the Reviewer's six
per-check findings (with which ones triggered a re-fetch):

```bash
python -m cli.run_claim --claim-id CLM-002 --explain
```

Run all 8 scenarios and print a pass/fail table against ground truth:

```bash
python -m cli.run_all
```

Process a whole daily lot — run both agents over every unresolved claim in it, so the UI's
triage queue opens to fully-evidenced cases. This is the intended post-ingestion step (run it
right after the ETL loads a lot); it lives outside `semantic_layer/` on purpose, so the ETL stays
pure and testable while the paid OpenRouter calls stay here:

```bash
python -m cli.process_lot                    # active lot, all unresolved claims
python -m cli.process_lot --batch LOT-2024-09-15
python -m cli.process_lot --cap 5            # limit, for a smoke test
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

Build the DB first (`python -m semantic_layer.etl`), then open http://127.0.0.1:8000/ for the
**analyst workspace** — a two-pane surface shaped around the analyst's loop (**triage → read
evidence → decide**):

- **KPI strip** — two clickable cards, **To do** and **Decided**, which partition the lot: their
  counts always add up to it, and each equals the rows you get by clicking it. The To-do card spells
  out its two halves (not investigated / awaiting your call). `$ open`, the priority mix and the
  oldest open claim sit beside them as read-only figures, visually distinct because they aren't
  filters.
- **Left: triage queue** — the lot's claims with search, status filter tabs
  (to-do / not-investigated / awaiting-my-call / disputable / decided), sortable priority/amount,
  keyboard navigation, and disposition badges.
- **Right: review pane** — the verdict header with the Investigator→Reviewer provenance chain and
  a confidence meter; the **retailer's claim** (reason + notes); a **source documents** panel
  (PO / ASNs / invoice / receiving / trade agreement / prior claims), which is read straight from
  the DB and so is available whether or not the claim has been investigated; then, once
  investigated, the agent reconciliation, six check chips, dispute grounds, and a dispute-packet
  download. The raw tool-call trace and token usage are developer telemetry and sit in a collapsed
  audit drawer.
- **Decision bar** — accept / override / send-to-human, persisted to `claim_dispositions` (a
  separate table from `claim_resolutions`, so re-investigating a claim never clobbers the human
  decision).

**Process lot (investigate + review all)** runs the pipeline over every unresolved claim in the lot,
so the analyst opens to fully-evidenced cases. Dependency-free static client (`ui/static/`, no
build step).

API (data comes from the relational store — there is no "scenario"):

- `GET /api/dashboard` → `{lot_total, todo_count, not_investigated_count, awaiting_my_call_count,
  decided_count, open_amount_cents, oldest_open_days, priority_breakdown, batch}` — all lot-scoped,
  and counted with the same predicates the filter tabs use so the arithmetic closes.
- `GET /api/batches/{batch_id}?offset=&limit=&status_filter=&sort=&q=` → a page of the lot's claims
  (each with `priority`, `status`, and any disposition); 404 for an unknown batch.
- `POST /api/batches/{batch_id}/investigate?cap=` (SSE) → run over the lot's unresolved claims
  (the whole lot by default; `cap` limits it): per-claim `tool_call` + `claim_done`, then a
  `batch_done` summary.
- `GET /api/claims/{claim_id}/documents` → the claim's source-document graph from the DB.
- `GET /api/claims/{claim_id}/casefile` → the full CaseFile + ReviewerOutput from the latest run;
  404 if the claim hasn't been investigated.
- `GET /api/claims/{claim_id}/dispute-packet` → the Markdown packet for an `INVALID` claim's latest
  run (download).
- `POST /api/claims/{claim_id}/disposition` → record the analyst's decision
  `{disposition: accept|override|escalate, override_verdict?, note?}`.
- `GET /api/claims/{claim_id}/stream` (SSE) / `POST /api/claims/{claim_id}/investigate` → single-claim
  drill-in / run; 404 for an unknown claim, 502 on an upstream agent failure.

```bash
curl -s http://127.0.0.1:8000/api/dashboard
curl -s http://127.0.0.1:8000/api/claims/CLM-003/documents
curl -sN -X POST "http://127.0.0.1:8000/api/batches/LOT-2024-09-15/investigate?cap=2"
```

## Running tests

Every gate at once — this is what CI runs and what to run before committing a layer:

```bash
scripts/check.sh          # pytest + pyright + frontend syntax + frontend unit tests
scripts/check.sh pytest   # or one gate at a time: pytest | types | js
```

It reports every failing gate rather than stopping at the first, and prefers `./.venv/bin`
when present. `.github/workflows/tests.yml` calls this same script, so the gate definitions
can't drift between local runs and CI. The individual commands, for reference:

```bash
# Unit tests — no API key needed, runs by default
pytest tests/ -v

# Integration tests — hits the real OpenRouter API, costs money and time, requires
# OPENROUTER_API_KEY. Excluded from the default `pytest tests/` run (see pyproject.toml);
# opt in explicitly:
pytest tests/test_pipeline_scenarios.py -m integration -v

# Static type check (same gate CI runs; config in pyproject.toml [tool.pyright])
pyright

# Frontend: Node's built-in test runner over the pure helpers in ui/static/lib.js. No
# package.json, no node_modules, no build step. The glob matters — `node --test tests/js/`
# module-resolves the bare directory and fails. Note the syntax check reads the file on
# stdin: plain `node --check <file>` silently passes on these two (they use `export`) —
# see check_js_syntax in scripts/check.sh.
node --input-type=module --check < ui/static/app.js
node --input-type=module --check < ui/static/lib.js
node --test "tests/js/**/*.test.mjs"
```

`ui/static/app.js` is deliberately DOM + fetch only; anything that is real logic (money
formatting, verdict labels, the keymap, URL-hash state) lives in `ui/static/lib.js` so it can
be tested. CI runs all three gates — `pytest`, `pyright`, and the `js` job.

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

- **Wiring the ETL's quarantine/DQ signals into escalation** — Layer 31 escalates on documents that
  are *absent* from the store, but a row the ETL **quarantined** is invisible to it: `reject_rows`
  records only batch/source/raw_row/reason, dropping `source_row_ref` and `target`, and the DQ report
  aggregates per source file. So nothing can tie a quarantined row back to a `claim_id`. Doing it
  properly means adding `target` + `entity_pk` columns and backfilling them in the loader — an ETL
  schema change, which is why it was scoped out of Layer 31 rather than bolted on.
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
