# Progress

## Current layer
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
Start **Layer 6**: `agents/investigator.py` + `agents/reviewer.py`. Each defines its system
prompt (`INVESTIGATOR_SYSTEM_PROMPT`/`REVIEWER_SYSTEM_PROMPT` per `docs/PLAN.md`'s System
Prompt Design section) and a `run_investigator()`/`run_reviewer()` entry point that constructs
an `AgentRunner` (from Layer 5) with the right model/system prompt and calls `.run(...)`.
Needs the exact OpenRouter model slugs for Claude Haiku 4.5 (Investigator) and Claude Sonnet
4.5 (Reviewer) confirmed against OpenRouter's actual model catalog first — `CLAUDE.md`
deliberately left these unresolved rather than hardcoding a guess. Also needs a `CaseFile`
Pydantic model (doesn't exist yet — currently only a JSON shape in `docs/SPEC.md`) if Layer 6
wants schema-validated parsing of the Investigator's output before Layer 7 does its own
required-fields check.

## Layer status

| Layer | What | Status |
|---|---|---|
| 0 | CLAUDE.md, SPEC.md, PROGRESS.md | ✅ Done |
| 1 | `mcp_server/models.py` + UOM conversion table | ✅ Done |
| 2 | All 7 scenario fixture JSON files + fixture validation tests | ✅ Done |
| 3 | `mcp_server/fixtures.py` + `mcp_server/tools/` + unit tests passing | ✅ Done |
| 4 | `mcp_server/server.py` (FastMCP wiring) | ✅ Done |
| 5 | `agents/base.py` (shared tool loop) | ✅ Done |
| 6 | `agents/investigator.py` + `agents/reviewer.py` | ⬜ Not started |
| 7 | `orchestrator/pipeline.py` + `orchestrator/output.py` | ⬜ Not started |
| 8 | `cli/run_claim.py` + `cli/run_all.py` | ⬜ Not started |
| 9 | Integration tests + README | ⬜ Not started |

## Tests passing
`pytest tests/` — 70 passed, 0 failed:
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

## Known issues / decisions pending
- Cross-document referential integrity (po_id/sku consistency) is now enforced by
  `tests/test_fixtures.py`, not at the model layer — this is intentional; models stay
  single-document, fixture-level tests catch cross-file drift.
- **Resolved**: OpenRouter-vs-Anthropic-SDK decision — user chose OpenRouter. `CLAUDE.md`,
  `pyproject.toml`, and `.env.example` updated accordingly; see Layer 5 notes above.
- `pytest-asyncio` added as a dev dependency (not anticipated in `docs/PLAN.md`'s original
  dependency list) since `fastmcp.Client`'s test API is async-only; `asyncio_mode = "auto"`
  set in `pyproject.toml` so async test functions need no per-test decorator.
- `fastmcp.Client.call_tool(...).data` returns dynamically-generated dataclasses, not the
  server's original Pydantic model instances — see Layer 5 notes above. Worth remembering if
  Layer 6/7 ever need to inspect tool results directly rather than through `AgentRunner`'s
  serializer.
- Exact OpenRouter model slugs for Claude Haiku 4.5 / Sonnet 4.5 still need confirming against
  OpenRouter's live model catalog before Layer 6 can hardcode them.

---
*Update this file at the end of every session before stopping.*
