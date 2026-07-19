# Progress

## Current layer
**Layer 7 — `orchestrator/pipeline.py` + `orchestrator/output.py` complete**

Built `orchestrator/pipeline.py`'s `run_pipeline(*, claim_id, scenario, openai_client=None,
mcp_client=None, output_dir="outputs", max_investigator_attempts=3)`: the single entry point
that wires `run_investigator` → `run_reviewer` and writes all three output artifacts,
implementing every safeguard named in `CLAUDE.md`. `openai_client`/`mcp_client` are optional
dependency injections (mirroring `AgentRunner`'s Layer 5/6 convention) — when omitted,
`run_pipeline` constructs a real `AsyncOpenAI` (OpenRouter) and launches `mcp_server/server.py`
as a real stdio subprocess via `fastmcp.client.transports.PythonStdioTransport` with
`SCENARIO_ID` set in its environment (merged with the parent env, not replacing it), sharing
one MCP connection across both agents per `docs/PLAN.md`'s handoff pattern. Tests always
inject a `StubAsyncOpenAI` + real in-process `fastmcp.Client(mcp)`, so no test spawns a real
subprocess or hits OpenRouter.

Added `CaseFile`/`PoSummary`/`TimelineEvent` and `ReviewFindings`/`ReviewerOutput` Pydantic
models in `pipeline.py` (colocated with the orchestrator logic rather than
`mcp_server/models.py`, since these are agent I/O contracts, not domain objects). Only the
fields `docs/SPEC.md` lists as "Required fields" (`claim_id`, `po_summary`'s four sub-fields,
`timeline`, `proposed_verdict`, `confidence`) have no default, so a Pydantic `ValidationError`
maps 1:1 onto the "CaseFile schema validation" safeguard.

Implemented the three `CLAUDE.md` safeguards precisely:
- **CaseFile schema validation + correction retry**: `_run_investigator_until_valid` calls
  `run_investigator`, parses/validates the response, and on failure re-invokes with a specific
  `extra_instructions` correction describing exactly what was missing — up to
  `max_investigator_attempts` (default 3) before raising `PipelineError`.
- **Tool-call trace verification**: `REQUIRED_TOOL_CALLS` maps only the 4 scenarios
  `docs/SPEC.md`'s table actually specifies (s02→`normalize_uom`, s03→`get_asns_for_po` with
  `len >= 2`, s06→`get_trade_agreement`, s07→`list_claims_for_po`) to a trace predicate,
  matched via `scenario[:3]`; a failing check triggers the same correction-retry loop as schema
  validation. Deliberately did not invent checks for s01/s04/s05 — not in the spec's table.
- **Stripped reasoning handoff**: the orchestrator strips `reasoning` from
  `case_file.model_dump()` itself before calling `run_reviewer`, independent of
  `agents/reviewer.py`'s own internal strip (belt-and-suspenders, confirmed by a test that
  spies on the `run_reviewer` call and asserts `"reasoning" not in case_file`).

**Verdict semantics** (confirmed with the user before implementing, since `docs/SPEC.md`'s
`dispute_packet.md` trigger of `final_verdict == INVALID` can't literally apply to the
Reviewer's own `CONFIRM/OVERTURN/ESCALATE` vocabulary): `verdict.json` carries three distinct
fields — `investigator_verdict` (`case_file.proposed_verdict`, verbatim), `reviewer_verdict`
(`reviewer_output.final_verdict`, verbatim), and `final_verdict` (the business outcome in
`VALID/INVALID/ESCALATE`, derived by `_resolve_final_verdict`: `CONFIRM` keeps
`investigator_verdict`; `OVERTURN` flips `VALID`↔`INVALID` (falls back to `ESCALATE` if the
investigator itself said `ESCALATE`); `ESCALATE` always yields `ESCALATE`). `confidence` in
verdict.json is the Reviewer's confidence, since the Reviewer has final say.

**Two small additive changes to already-completed layers** (confirmed with the user,
non-breaking — all 80 prior tests pass unchanged):
- `agents/base.py`: `AgentResult` gained a `messages: list[dict]` field (the full transcript
  `AgentRunner.run()` already built internally) so `reasoning_trace.json` can contain the
  literal message arrays `docs/SPEC.md` asks for. While adding this, discovered and fixed a
  latent aliasing bug: `AgentRunner.run()` was passing the same mutable `messages` list object
  by reference to every `create()` call, so `StubAsyncOpenAI`'s recorded `stub.requests[i]`
  all pointed at the same list — appending the final assistant turn on return retroactively
  changed what earlier "recorded" requests looked like. Fixed by passing `messages=list(messages)`
  (a shallow copy) into each `create()` call; caught by `test_agents_base.py`'s existing
  `test_single_tool_call_round_trip` failing after the `messages` field was added, not by a
  new test.
- `agents/investigator.py`: `run_investigator()` gained an optional
  `extra_instructions: str | None = None`, appended to the user message, so the
  correction-retry safeguard can name the specific problem instead of a generic re-run.

Wrote `tests/test_orchestrator_pipeline.py` (13 tests) and `tests/test_orchestrator_output.py`
(5 tests), following the exact `StubAsyncOpenAI` + real in-process `fastmcp.Client(mcp)`
convention from Layers 5-6 — no test hits OpenRouter or spawns a subprocess. Covers: s01
(VALID→CONFIRM) and s02 (INVALID→CONFIRM, with an actual `normalize_uom` tool-call round trip)
happy paths including output-artifact presence/absence; missing-required-field and
missing-required-tool-call correction retries (asserting the second attempt's user message
contains the specific correction text); `max_investigator_attempts` exhaustion raising
`PipelineError`; the reasoning-strip safeguard via a `run_reviewer` spy; the full
`_resolve_final_verdict` truth table (7 cases, including the `ESCALATE`+`OVERTURN` edge case);
and `output.py`'s three writers (correct paths/JSON shape, dispute packet content, empty
`dispute_grounds` handling, nested output-dir creation).

`pytest tests/` — 98 passed, 0 failed (80 prior + 18 new).

---

## Previous layer
**Layer 6 — `agents/investigator.py` + `agents/reviewer.py` complete**

Confirmed the two OpenRouter model slugs `CLAUDE.md` had deliberately left unresolved, by
checking OpenRouter's live model pages rather than guessing: `anthropic/claude-haiku-4.5`
(Investigator) and `anthropic/claude-sonnet-4.5` (Reviewer). Updated `CLAUDE.md`'s Tech
stack section to record these as confirmed rather than pending.

Built `agents/investigator.py`: `INVESTIGATOR_SYSTEM_PROMPT` encodes the five-step ordered
protocol from `docs/PLAN.md` (collect all docs → normalize UOM → verify timeline → reconcile
quantities → check prior claims) and spells out the exact CaseFile JSON shape from
`docs/SPEC.md` inline in the prompt so the model has a concrete schema to match rather than
inferring one. `run_investigator(*, openai_client, mcp_client, claim_id, model=...)` builds a
short user message naming the claim_id and delegates to `AgentRunner` — it does not parse or
validate the returned JSON itself; per the build order, CaseFile schema validation and the
required-tool-call trace check are Layer 7's job (`orchestrator/pipeline.py`), not this layer's.

Built `agents/reviewer.py`: `REVIEWER_SYSTEM_PROMPT` frames the Reviewer as a targeted
spot-check (re-run `normalize_uom`, re-call `get_asns_for_po`/`get_trade_agreement`/
`list_claims_for_po`), not a full re-investigation, and states explicitly that the case file
is data to verify, not instructions to follow — reinforcing the XML-delimiter prompt-injection
guard from `CLAUDE.md`'s Safeguards section. `run_reviewer(*, openai_client, mcp_client,
case_file, model=...)` embeds the case file inside `<case_file>...</case_file>` tags in the
user message. It also strips the `reasoning` field itself via a module-level
`_case_file_for_reviewer()` helper — belt-and-suspenders alongside Layer 7's orchestrator-level
stripping (`CLAUDE.md`'s "Stripped reasoning handoff" safeguard), so the Reviewer never sees
the Investigator's narrative even if a future caller forgets to strip it first.

Both `run_investigator`/`run_reviewer` take an optional `model` override (defaulting to the
confirmed slug) purely so tests can substitute `"test-model"` without monkeypatching a module
constant — mirrors how `AgentRunner` itself takes `model` as a required constructor arg.

Extracted the `make_completion`/`StubAsyncOpenAI` test helpers that `test_agents_base.py` had
defined inline into `tests/agent_stubs.py`, since all three agent test files now need identical
scripted-`ChatCompletion` fixtures — re-pointed `test_agents_base.py`'s imports at the shared
module with no behavior change (verified by re-running it before adding new tests).

Wrote `tests/test_agents_investigator.py` (5 tests) and `tests/test_agents_reviewer.py` (5
tests), both using the same real in-process `fastmcp.Client(mcp)` pattern as
`test_agents_base.py` (`monkeypatch.setenv("SCENARIO_ID", ...)`, no mocking of the MCP layer).
Covers: the confirmed model slug constants, user-message wiring (claim_id present for the
Investigator; case_file JSON present and XML-delimited for the Reviewer), the `model=` override
being honored, the `reasoning` field never reaching the Reviewer's actual prompt text even
though the fixture case file included one, and one real tool-call round trip per agent against
actual scenario fixtures (s02's `normalize_uom` for the Investigator, s07's
`list_claims_for_po` for the Reviewer).

`pytest tests/` — 80 passed, 0 failed (70 prior + 10 new).

---

## Previous layer
**Layer 5 — `agents/base.py` shared tool-use loop complete**

Resolved the OpenRouter-vs-Anthropic-SDK decision from the previous session: **the user chose
OpenRouter.** Updated `CLAUDE.md`'s "Tech stack" section (and its "Never commit" line) to
describe `AsyncOpenAI` pointed at `base_url="https://openrouter.ai/api/v1"` with
`OPENROUTER_API_KEY`, explicitly noting this is an approved deviation (2026-07-18) from the
original Anthropic-SDK plan. Swapped `anthropic` → `openai` in `pyproject.toml`. Added
`.env.example` documenting `OPENROUTER_API_KEY=` and the existing `SCENARIO_ID=` convention.
Exact OpenRouter model slugs for Claude Haiku 4.5 / Sonnet 4.5 are intentionally left
unconfirmed in the docs — to be resolved against OpenRouter's actual catalog at Layer 6.

Because OpenRouter speaks the OpenAI-compatible chat completions API rather than Anthropic's
native `tool_use`/`tool_result` content blocks, the loop shape is OpenAI's `tool_calls` on the
assistant message plus one `role:"tool"` reply message per `tool_call_id`, not Anthropic's
format — this is the main way `agents/base.py` differs from what `docs/PLAN.md` originally
sketched.

Built `agents/base.py`: `AgentRunner` (constructor-injected `openai_client` and `mcp_client`,
both duck-typed and owned by the caller — Layer 7's orchestrator will own the real subprocess
MCP connection and the real `AsyncOpenAI` client; `AgentRunner` itself is stateless and never
touches env vars or transport). `run(user_message)` is fully async (required since
`fastmcp.Client` is async-only): fetches tool schemas fresh via `mcp_client.list_tools()` every
call, loops `create → inspect tool_calls → execute → append tool-role messages → repeat` until
a text-only response, returning `AgentResult(final_text, trace)`. `max_iterations` (default 10)
bounds the loop; exceeding it raises `AgentRunnerError` rather than returning a truncated
result. `fastmcp.exceptions.ToolError` (what a tool's `ValueError` becomes crossing the MCP
protocol boundary) and malformed tool-call-argument JSON are both caught and fed back to the
model as ordinary `"ERROR: ..."` tool-result content (OpenAI's tool-message schema has no
`is_error` flag, unlike Anthropic's) — both are recorded in the trace with `is_error=True`
rather than crashing the run.

**Important discovery, not anticipated by the original plan**: `fastmcp.Client.call_tool(...)`'s
`.data` field is **not** the original Pydantic model instance — the client reconstructs return
values from the tool's JSON output schema into a dynamically-generated `dataclass`
(`fastmcp.utilities.json_schema_type.Root`), since the client only sees JSON Schema over the
wire, not the server's actual Python types. `_serialize_tool_result` in `agents/base.py`
recursively converts via `dataclasses.is_dataclass`/`dataclasses.asdict`, not
`isinstance(..., pydantic.BaseModel)` as originally planned — confirmed by direct
experimentation against `get_po` (single object), `get_asns_for_po` (list of dataclasses), and
`get_trade_agreement` (`None` case) before writing the serializer.

Added one-line docstrings to all 8 `mcp_server/tools/*.py` functions — FastMCP derives each
tool's LLM-visible `description` from its docstring, and all 8 were previously blank, which
would have left the agent with no information about what any tool does beyond its name and
parameter types.

Wrote `tests/test_agents_base.py` (7 tests): real in-process `fastmcp.Client(mcp)` for the MCP
side (same pattern as `test_server.py`, `monkeypatch.setenv("SCENARIO_ID", ...)`, no mocking),
and a small hand-written `StubAsyncOpenAI` (constructor-injected, no `unittest.mock.patch`
needed) returning scripted real `openai.types.chat.ChatCompletion` objects — using the actual
SDK types rather than duck-typed stand-ins to catch attribute-shape mistakes. Covers:
text-only response, single tool-call round trip, parallel tool calls in one turn, a real
`ToolError` from `normalize_uom` on an unknown SKU, malformed tool-call-argument JSON, a
runner that never stops calling tools (`AgentRunnerError` after exactly `max_iterations`
calls), and MCP→OpenAI tool-schema translation (asserts all 8 tools have non-empty
descriptions — a regression guard for the docstring fix).

`pytest tests/` — 70 passed, 0 failed (63 prior + 7 new).

---

## Previous layer
**Layer 4 — `mcp_server/server.py` FastMCP wiring complete**

Built `mcp_server/server.py`: constructs a `FastMCP("deduction-autopsy")` app and registers the
8 existing tool functions from `mcp_server/tools/` (`document_tools.py`, `uom_tools.py`) via a
loop calling `mcp.tool(fn)` on each imported function object — no re-definition or wrapping, so
the tools' exact signatures/docstrings stay the single source of truth. `server.py` itself has
no `SCENARIO_ID` handling: each tool function already builds a fresh `FixtureLoader()` per call
and reads the env var fresh, so nothing extra was needed at server-construction or startup time.
`if __name__ == "__main__": mcp.run()` runs over stdio (the default transport), matching
`docs/PLAN.md`'s "launch FastMCP server as subprocess with `SCENARIO_ID` env var" handoff
pattern for the Layer 7 orchestrator.

Confirmed installed dependency is `fastmcp==3.4.4` (the jlowin/fastmcp framework — `FastMCP`,
`Client`, `mcp.tool()`, `mcp.run()`), not the low-level MCP SDK submodule.

Wrote `tests/test_server.py` (5 tests) using `fastmcp.Client(mcp)` for in-process testing —
constructs the client directly from the `FastMCP` app object, no subprocess/stdio pipes needed,
while still exercising the real MCP protocol layer (tool registration, JSON-schema args,
result serialization). Added `pytest-asyncio` as a dev dependency (`asyncio_mode = "auto"` in
`pyproject.toml`) since `Client` methods are async-only. Tests cover: all 8 tools listed by
name, `get_po` round-trip via MCP (s01), `normalize_uom` via MCP (s02 CASE→EACH), `None` return
serializes correctly for a promo mismatch (s06 — `result.data is None`), and a `ValueError` from
the tool layer surfaces as `fastmcp.exceptions.ToolError` through `call_tool` rather than being
swallowed. Manually verified the server also runs correctly as a real stdio subprocess via
`PythonStdioTransport` (not just the in-process `Client` path used in the test suite).

`pytest tests/` — 63 passed, 0 failed (58 prior + 5 new).

---

## Previous layer
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
Start **Layer 8**: `cli/run_claim.py` + `cli/run_all.py`. `run_claim.py` should call
`orchestrator.pipeline.run_pipeline(claim_id=..., scenario=...)` with no injected
`openai_client`/`mcp_client` (letting it construct the real `AsyncOpenAI` + subprocess MCP
client) and print the resulting `PipelineResult` — needs `OPENROUTER_API_KEY` set in `.env`.
`run_all.py` iterates the 7 scenarios from `docs/SPEC.md`'s ground-truth table and prints a
`rich` pass/fail table comparing `final_verdict` against the "Final expected" column (all
`CONFIRM`... note: the ground-truth table's "Final expected" column is literally the
Reviewer's verdict vocabulary, not `run_pipeline`'s business-vocabulary `final_verdict` field —
Layer 8 should compare against `reviewer_verdict`, not `final_verdict`, or the pass/fail table
will be wrong for every scenario since `final_verdict` is `VALID`/`INVALID` depending on
scenario while the table says `CONFIRM` uniformly). This is a real, first-run manual
verification against OpenRouter — no scenario has been run end-to-end against a live model yet;
budget for prompt-tuning iteration once actual model behavior is observed (the CaseFile/Reviewer
JSON schemas and correction-retry loop are built to spec but untested against real model output).

## Layer status

| Layer | What | Status |
|---|---|---|
| 0 | CLAUDE.md, SPEC.md, PROGRESS.md | ✅ Done |
| 1 | `mcp_server/models.py` + UOM conversion table | ✅ Done |
| 2 | All 7 scenario fixture JSON files + fixture validation tests | ✅ Done |
| 3 | `mcp_server/fixtures.py` + `mcp_server/tools/` + unit tests passing | ✅ Done |
| 4 | `mcp_server/server.py` (FastMCP wiring) | ✅ Done |
| 5 | `agents/base.py` (shared tool loop) | ✅ Done |
| 6 | `agents/investigator.py` + `agents/reviewer.py` | ✅ Done |
| 7 | `orchestrator/pipeline.py` + `orchestrator/output.py` | ✅ Done |
| 8 | `cli/run_claim.py` + `cli/run_all.py` | ⬜ Not started |
| 9 | Integration tests + README | ⬜ Not started |

## Tests passing
`pytest tests/` — 98 passed, 0 failed:
- `test_orchestrator_pipeline.py` (13): s01/s02 happy paths (investigator/reviewer wiring,
  output-artifact presence/absence), missing-required-field and missing-required-tool-call
  correction retries, `max_investigator_attempts` exhaustion, reasoning-strip safeguard,
  full `_resolve_final_verdict` truth table.
- `test_orchestrator_output.py` (5): `verdict.json`/`reasoning_trace.json`/`dispute_packet.md`
  writers — paths, JSON shape, markdown content, empty-dispute-grounds handling, nested
  output-dir creation.
- `test_fixtures.py` (45): Pydantic model validation for every fixture file, po_id/retailer
  cross-document consistency, file-layout expectations per scenario, ground-truth claim_id
  matches, and each scenario's specific numeric trap.
- `test_uom_tools.py` (6): direct/reverse/multi-hop/same-unit conversions, unknown-SKU and
  undefined-path `ValueError`s.
- `test_document_tools.py` (7): s01 basic wiring, s03 split-ASN aggregation, s06
  trade-agreement promo match/mismatch, s07 claim_id resolution across two claim files,
  po_id mismatch raises.
- `test_server.py` (5): all 8 tools registered and listed by name, `get_po`/`normalize_uom`
  round-trip through the real MCP protocol (in-process `fastmcp.Client`), `None` return
  serializes correctly (s06 promo mismatch), `ValueError` surfaces as `ToolError` through
  `call_tool` rather than being swallowed.
- `test_agents_base.py` (7): text-only response, single/parallel tool-call round trips,
  real `ToolError` surfaced without crashing, malformed tool-call JSON handled, max-iterations
  safety bound raises `AgentRunnerError`, and MCP→OpenAI tool-schema translation with a
  non-empty-description regression guard.
- `test_agents_investigator.py` (5): confirmed model slug, claim_id present in the user
  message, default vs. overridden `model`, and a real `normalize_uom` tool-call round trip
  against s02's fixtures.
- `test_agents_reviewer.py` (5): confirmed model slug, `reasoning` field absent from the
  actual prompt text sent to the model, case file XML-delimited and valid JSON inside the
  tags, default vs. overridden `model`, and a real `list_claims_for_po` tool-call round trip
  against s07's fixtures.

## Known issues / decisions pending
- Cross-document referential integrity (po_id/sku consistency) is now enforced by
  `tests/test_fixtures.py`, not at the model layer — this is intentional; models stay
  single-document, fixture-level tests catch cross-file drift.
- **Resolved**: OpenRouter-vs-Anthropic-SDK decision — user chose OpenRouter. `CLAUDE.md`,
  `pyproject.toml`, and `.env.example` updated accordingly; see Layer 5 notes above.
- **Resolved**: exact OpenRouter model slugs — `anthropic/claude-haiku-4.5` (Investigator),
  `anthropic/claude-sonnet-4.5` (Reviewer). Confirmed against OpenRouter's live catalog in
  Layer 6, hardcoded as `INVESTIGATOR_MODEL`/`REVIEWER_MODEL` in the respective agent modules.
- `pytest-asyncio` added as a dev dependency (not anticipated in `docs/PLAN.md`'s original
  dependency list) since `fastmcp.Client`'s test API is async-only; `asyncio_mode = "auto"`
  set in `pyproject.toml` so async test functions need no per-test decorator.
- `fastmcp.Client.call_tool(...).data` returns dynamically-generated dataclasses, not the
  server's original Pydantic model instances — see Layer 5 notes above. Worth remembering if
  a future layer ever needs to inspect tool results directly rather than through
  `AgentRunner`'s serializer.
- **Resolved**: `CaseFile`/`ReviewerOutput` Pydantic models now live in
  `orchestrator/pipeline.py` (Layer 7) — see Layer 7 notes above for why they're colocated
  there rather than in `mcp_server/models.py`.
- **Found and fixed during Layer 7**: `agents/base.py`'s `AgentRunner.run()` passed the same
  mutable `messages` list object by reference into every `chat.completions.create()` call.
  Harmless against the real `AsyncOpenAI` client (which serializes immediately), but a real
  aliasing bug against `StubAsyncOpenAI` (which stores `**kwargs` by reference) — appending to
  `messages` after a later call retroactively changed what earlier `stub.requests[i]["messages"]`
  looked like. Fixed by passing `messages=list(messages)` (shallow copy) to `create()`. Worth
  keeping in mind if `agents/base.py` changes again: don't mutate `messages` after any call
  whose kwargs might still be referenced (tests or otherwise).
- Layer 8 gotcha to watch for: `docs/SPEC.md`'s ground-truth table's "Final expected" column is
  in the *Reviewer's* vocabulary (`CONFIRM`/`OVERTURN`/`ESCALATE`, uniformly `CONFIRM` across
  all 7 scenarios), not `run_pipeline`'s business-vocabulary `final_verdict` field
  (`VALID`/`INVALID`/`ESCALATE`, which varies per scenario). `cli/run_all.py`'s pass/fail table
  must compare against `PipelineResult.reviewer_verdict`, not `.final_verdict` — see Layer 7's
  "Next session" note above.

---
*Update this file at the end of every session before stopping.*
