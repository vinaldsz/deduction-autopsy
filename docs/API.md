# API reference

Two interfaces, for two different consumers:

- **[MCP tools](#mcp-tools)** — what the *agents* see. The only path to document data.
- **[HTTP API](#http-api)** — what the *browser* sees. FastAPI, `127.0.0.1` only, no auth.

Contract history and the reasoning behind each shape live in [`docs/SPEC.md`](SPEC.md); the
design context is in [`docs/ARCHITECTURE.md`](ARCHITECTURE.md).

---

## MCP tools

Served by `mcp_server/server.py` (FastMCP over stdio), launched as a subprocess by the
orchestrator with `DEDUCTIONS_DB` in its environment. Agents get these tool schemas advertised
as OpenAI-format functions and nothing else — no file paths, no SQL, no scenario parameter.

**Start from `get_deduction_claim`.** Every other document tool takes the `po_id` *from that
response*, never the `claim_id`. Both agent prompts are hardened against this slip because it is
the single most common failure mode.

### Document tools

| Tool | Signature | Returns | Not-found behaviour |
|---|---|---|---|
| `get_deduction_claim` | `(claim_id: str)` | `DeductionClaim` — includes `po_id`, `claimed_reason`, `claimed_amount` (cents), `retailer_notes` | `ERROR: …` string |
| `get_po` | `(po_id: str)` | `PurchaseOrder` — `ordered_qty` + `ordered_uom`, `unit_price` (cents) | `ERROR: …` string |
| `get_asns_for_po` | `(po_id: str)` | `list[ASN]` — 0..N; a split shipment is two rows and the **sum** is what matters | `[]` |
| `get_invoice` | `(po_id: str)` | `Invoice` — `invoiced_qty` + `invoiced_uom`, `amount` (cents) | `ERROR: …` string |
| `get_receiving_record` | `(po_id: str)` | `ReceivingRecord` — plus free-text `notes` that frequently decides the verdict | `ERROR: …` string |
| `get_trade_agreement` | `(retailer: str, sku: str, promo_code: str)` | `TradeAgreement` or `None` — matched on all three, so an agreement for a *different* promo code correctly does not match | `None` |
| `list_claims_for_po` | `(po_id: str)` | `list[str]` of claim ids on the same PO — how duplicate claims are found | `[]` |

The asymmetry is deliberate and is documented in `orchestrator/completeness.py`: only the
single-record getters can error. Because the list tools signal "nothing found" with `[]`, a
missing ASN is invisible in the tool-call trace — which is why data-gap detection reads the
store directly instead of the trace.

### `normalize_uom`

```
normalize_uom(qty: float, from_uom: str, to_uom: str, sku: str) -> float
```

Converts between `EACH` / `CASE` / `PALLET` using the per-SKU factors in
`data/sku_uom_conversions.json`. Raises for an unknown SKU or unit rather than guessing a factor.

Both prompts require every quantity to be normalized to `EACH` before any comparison. Diffing
raw quantities across documents in different units is exactly the trap in scenario 2: a PO in
`CASE` against an ASN in `EACH` looks like a catastrophic shortage until normalized, at which
point the quantities match.

### Field conventions

| Convention | Detail |
|---|---|
| Money | **Integer cents**, everywhere — `unit_price`, `claimed_amount`, `amount`. `discrepancy_amount_cents` is `discrepancy_qty × unit_price`, with no further conversion |
| Quantities | Integers, in the document's own UOM. Convert before comparing |
| Dates | ISO `YYYY-MM-DD`. Valid order: `order_date → ship_date → receipt_date → invoice_date → claim_date` |
| SKUs | Opaque codes (`SKU-001`). There is no product master |
| Free text | `receiving_records.notes` and `deduction_claims.retailer_notes` are untrusted input — see the prompt-injection safeguard in [ARCHITECTURE §5](ARCHITECTURE.md#5-the-two-agent-handoff) |

---

## HTTP API

```bash
uvicorn ui.server:app --host 127.0.0.1 --port 8000
```

Build the store first (`python -m semantic_layer.etl`). `GET /` serves the analyst workspace from
`ui/static/`. Everything below is under `/api`.

**Conventions.** JSON in, JSON out. Errors are `{"error": "<message>"}` with a status code:

| Code | Means |
|---|---|
| `404` | Unknown `claim_id` / `batch_id`, or an artifact that was never produced (no run, no dispute packet) |
| `409` | The decision conflicts with recorded state |
| `422` | A query or body this route will not honour — an unknown `sort`, `status_filter`, `direction`, a malformed date, an out-of-range page, an empty selection |
| `502` | The upstream agent call failed |

`422` rather than a silent fallback is a deliberate rule across the whole surface: a page — or
worse, a *file* — of plausible but wrongly-filtered rows, with nothing on screen saying so, is
worse than an error.

### Dashboard

#### `GET /api/dashboard`

Lot-scoped KPIs for the active batch.

```json
{
  "batch": {"batch_id": "LOT-2024-09-15", "status": "complete"},
  "lot_total": 50,
  "todo_count": 29,
  "not_investigated_count": 22,
  "awaiting_my_call_count": 7,
  "decided_count": 21,
  "open_amount_cents": 348000,
  "oldest_open_days": 21,
  "priority_breakdown": {"HIGH": 9, "MEDIUM": 8, "LOW": 12},
  "priority_thresholds": {"high_cents": 15000, "med_cents": 5000, "age_days": 45}
}
```

Every count is computed with the same SQL predicate as the filter tab its card links to, so
`todo_count + decided_count == lot_total` always holds, and each card's number equals the rows
you get by clicking it. `not_investigated_count + awaiting_my_call_count == todo_count`. The
money, priority mix and aging figures are over the **open** (to-do) claims, and age is measured
against the lot's `load_date` — which the browser never receives, so it is derived server-side
rather than left to the client to get subtly wrong. With no active lot every figure is `0` and
`batch` is `null`.

### Worklist

#### `GET /api/batches/{batch_id}`

One page of the lot's claims, for the triage queue.

| Param | Default | Values |
|---|---|---|
| `offset` | `0` | ≥ 0 |
| `limit` | `25` | 1–200 |
| `status_filter` | `all` | `all`, `todo`, `not_investigated`, `awaiting_my_call`, `decided`, `disputable` |
| `sort` | `claim_id` | `claim_id`, `po_id`, `retailer`, `amount`, `age`, `priority` |
| `direction` | per-column default | `asc`, `desc` |
| `q` | — | free-text search |
| `retailer`, `reason` | — | exact match (see `/filter-options`) |
| `date_from`, `date_to` | — | ISO dates on `claim_date` |

```json
{
  "batch_id": "LOT-2024-09-15", "total": 29, "total_amount_cents": 348000,
  "offset": 0, "limit": 25, "sort": "amount", "direction": "desc",
  "claims": [{
    "claim_id": "CLM-SYN-0003", "po_id": "PO-SYN-0003", "retailer": "kroger",
    "claimed_reason": "shortage", "claimed_amount": 18000, "claim_date": "2024-09-15",
    "priority": "HIGH", "priority_reason": "…", "age_days": 0,
    "status": "INVALID", "agent_status": "INVALID",
    "disposition": null, "override_verdict": null, "decided_verdict": null,
    "note": null, "decided_at": null, "decision_stale": false
  }]
}
```

`total` / `total_amount_cents` are over the **filtered set**, not the page, so the footer can say
what a filter is actually worth. `status` is the *effective* verdict (what the claim's answer is
now) while `agent_status` keeps the agents' original, so an override can show what it superseded;
both are `"unresolved"` when there is nothing yet. Sort order is total — every `ORDER BY` carries
a `claim_id` tiebreaker, so paging cannot repeat or drop a row when values tie. `404` unknown
batch, `422` bad query.

#### `GET /api/batches/{batch_id}/filter-options`

The retailers and reasons actually present in this lot, for the filter dropdowns.

#### `GET /api/batches/{batch_id}/export.csv`

The filtered worklist as a CSV download. Same filter/sort params as the queue route — but **no
`offset`/`limit`**: this is every matching claim, not the page on screen, which is the only
reason it is server-side at all. Those two params are *ignored* if sent rather than rejected,
because the client's own query-string builder always emits them, and over-delivering
correctly-filtered rows is never wrong.

One row per claim with the full record including the analyst's note and `decided_at`. Money is a
plain `claimed_amount_usd` decimal so it sums in Excel; verdicts are the **raw machine tokens**
(`INVALID`, not the UI's "Disputable" — the screen owns the words, the file owns the tokens);
`decision_stale` is `yes`/`no` because Excel translates its own booleans. CRLF line endings, UTF-8
BOM. `404` unknown batch, `422` bad query.

### Running the agents

#### `POST /api/batches/{batch_id}/investigate?cap=` — SSE

Runs the pipeline over the lot's unresolved claims (the whole lot by default; `cap` limits it).
Frames:

```
batch_start  {total}
claim_start  {claim_id}
tool_call    {agent, name, args, is_error}     ← repeated
claim_done   {claim_id, investigator_verdict, reviewer_verdict, final_verdict, confidence}
claim_error  {claim_id, error}                 ← instead of claim_done
batch_done   {investigated, VALID, INVALID, ESCALATE, failed, stopped_reason}
```

**One claim failing does not end the lot** — it is reported and the run continues, matching
`cli/process_lot.py`. After **3 consecutive** failures the run stops with
`stopped_reason: "consecutive_failures"`, so a dead API key costs 3 round-trips rather than 50.
Closing the stream (the UI's Cancel) stops at the **next claim boundary**: the claim already
running finishes and is persisted, because it is paid for either way and killing it would leave a
run directory with no `verdict.json`.

#### `GET /api/batches/{batch_id}/run-estimate`

```json
{"claims": 29, "median_tokens_per_claim": 18420, "runs_measured": 12}
```

What a lot run is about to cost, **measured** from the `usage` block in each archived run's
`verdict.json`. With no runs on disk the median is `null` and the UI says there is nothing to
estimate from — for the same reason there is no ETA: a number nobody measured is worse than an
admitted unknown when the analyst is about to spend against it. `claims` comes from the same
query the run itself uses.

#### `POST /api/claims/{claim_id}/investigate` · `GET /api/claims/{claim_id}/stream` (SSE)

Single-claim run / drill-in, same frames minus the batch envelope. `404` unknown claim, `502` on
an upstream agent failure.

### Claim evidence

#### `GET /api/claims/{claim_id}/documents`

The claim's source-document graph straight from the store: PO, ASNs, invoice, receiving record,
trade agreement, prior claims. Available **whether or not the claim has been investigated** —
evidence does not depend on an agent run. `404` unknown claim.

#### `GET /api/claims/{claim_id}/casefile`

The full `CaseFile` + `ReviewerOutput` from the latest run, including both agents' reasoning and
the check findings. `404` if the claim has never been investigated.

#### `GET /api/claims/{claim_id}/runs`

`{claim_id, latest_run_id, runs: [...]}` — every archived run, **newest first by `verdict.json`'s
timestamp**. Run ids are caller-supplied strings, so directory order is not recency (the repo's own
`run-A`/`run-B` sort after every timestamp-shaped id while being the oldest runs on disk); a run
with no readable timestamp sorts last, where a crashed run belongs. Each entry carries the verdict
trio, confidence, token usage and `has_case_file` / `has_dispute_packet`. A claim with no runs is
`200` with an empty list, not a `404` — "never investigated" is a true answer, and the client asks
this for every claim it opens.

Read-only history: there is deliberately no way to *open* an older run, because rendering one
run's evidence beside another run's verdict is worse than not showing it. That is
[Layer 42](PLAN.md#42-the-run-picker--open-an-older-run-honestly).

#### `GET /api/claims/{claim_id}/trace`

The latest run's tool-call trace, compacted from `reasoning_trace.json` into the same
`{agent, name, args, is_error}` shape the live SSE emits, so the audit drawer works on a claim
nobody just ran. Compacted rather than raw because the artifact embeds both system prompts
verbatim. `404` if no trace was recorded.

#### `GET /api/claims/{claim_id}/dispute-packet`

The Markdown dispute packet for the latest run, as a download. `404` unless that run's final
verdict was `INVALID` — packets are only written for disputable claims.

### Human decisions

#### `POST /api/claims/{claim_id}/disposition`

```json
{"disposition": "accept" | "override" | "escalate", "override_verdict": "VALID|INVALID|ESCALATE", "note": "…"}
```

Recorded in `claim_dispositions`, a separate table from `claim_resolutions`, so re-investigating a
claim never clobbers the human's call. `accept` stores a **snapshot** of the verdict approved
(plus the `run_id` it came from), not a pointer to it — see
[ARCHITECTURE §7](ARCHITECTURE.md#7-presentation-plane). The response reports the
`decided_verdict` actually stored, derived the same way the writer derives it, so it cannot
contradict the row it just wrote.

The rules, and why each one is a status code rather than a silent success:

| Condition | Result |
|---|---|
| Unknown claim | `404` |
| `accept` on a claim that was never investigated | `409` — the claim exists, there is just no verdict to accept |
| `override` with no `override_verdict` | `422` |
| `override` with no non-empty `note` | `422` — a human overruling an audited verdict is the one decision that most needs a stated reason |
| `override_verdict` equal to the agents' verdict | `422` — that is an `accept`, and recording it as an override misdescribes the audit trail |
| `escalate` | Always allowed; parking a claim for someone else is not deciding it, and it does not count as `decided` |

#### `POST /api/batches/{batch_id}/dispositions`

```json
{"claim_ids": ["CLM-SYN-0003", "CLM-SYN-0005"]}
```

→ `{"batch_id": …, "recorded": 1, "decided_at": "…", "results": {"CLM-SYN-0003": "recorded", "CLM-SYN-0005": "already_decided"}}`

**Accept only.** The body has no `disposition` field at all, and `extra="forbid"` rejects one —
bulk *override* is the "approved something they never saw" failure this design exists to remove.
There is no bulk override: approving many claims at once is only defensible when you are agreeing
with a verdict that already exists.

One transaction, one timestamp. Per-claim outcomes are `recorded`, `unknown_claim`,
`not_investigated`, `unresolved_verdict`, `already_decided` — an ineligible claim in a selection
is a result to report, not a request-level error, so this is `200` bar a bad batch (`404`) or an
empty selection (`422`).

---

## Quick smoke test

```bash
python -m semantic_layer.etl                     # build the store
uvicorn ui.server:app --host 127.0.0.1 --port 8000 &

curl -s http://127.0.0.1:8000/api/dashboard
curl -s "http://127.0.0.1:8000/api/batches/LOT-2024-09-15?status_filter=todo&sort=amount"
curl -s http://127.0.0.1:8000/api/claims/CLM-003/documents
curl -s "http://127.0.0.1:8000/api/batches/LOT-2024-09-15/export.csv?status_filter=disputable"
curl -sN -X POST "http://127.0.0.1:8000/api/batches/LOT-2024-09-15/investigate?cap=2"   # spends money
```
