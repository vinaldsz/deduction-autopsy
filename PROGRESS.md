# Progress

## Current layer
**Layer 10 — `scenarios/s08_reviewer_overturn/` (8th scenario) complete**

Built the 8th scenario per `docs/PLAN.md`'s Layer 10 section and `docs/SPEC.md`'s "Eighth
Scenario" plan: `scenarios/s08_reviewer_overturn/` (`po.json`/`asn.json`/`invoice.json`/
`receiving_record.json`/`deduction_claim.json` for a clean 12-unit shortage on PO-008, plus
`prior_claim.json` for `CLM-008a` showing the same shortage already credited via CM-014).
Added `SKU-008` to `data/sku_uom_conversions.json`, an `s08` entry to
`orchestrator/ground_truth.py` and `REQUIRED_TOOL_CALLS` in `orchestrator/pipeline.py`
(same `list_claims_for_po` check as s07), and fixture tests in `tests/test_fixtures.py`
(renamed the now-inaccurate `test_all_seven_scenarios_present` →
`test_all_scenarios_present`, widened `test_only_s07_has_prior_claim` →
`test_only_s07_and_s08_have_prior_claim` since s08 also carries a `prior_claim.json`, and
added two s08-specific assertions).

**Original design didn't survive first contact with a live run — documenting the full
pivot since it's the actual point of this layer.** The initial fixture design (approved via
a design discussion before implementation) tried to make the prior claim's resolution a
*numeric-inference* trap rather than a wording one: `prior_claim.retailer_notes` stated a
dollar credit ("Credit of $24.00 issued... per CM-014") without restating the unit count,
so connecting it to the current claim's 12-unit shortage would require computing
`$24.00 / $2.00 unit_price = 12 units` — deliberately avoiding a keyword-matchable phrase
like s07's explicit "RESOLVED", per user pushback that an earlier wording-subtlety draft
risked "making the Investigator artificially dumb" rather than testing a genuine reasoning
gap.

**First live run (`python -m cli.run_claim --claim-id CLM-008 ...`) immediately falsified
this design**: `investigator_verdict: INVALID`, `reviewer_verdict: CONFIRM` — the
Investigator caught the duplicate on its own, no OVERTURN. Reading the trace showed why:
`agents/investigator.py`'s step 5 (hardened during the Layer 9 follow-up) already reads
almost identically to the Reviewer's own duplicate-check instruction ("if a prior claim's
notes indicate it was already resolved, e.g. a credit memo was issued"), so any prior-claim
wording legible enough for the Reviewer to reliably catch is equally legible to the
Investigator — there is no fixture-wording lever left to pull for this specific check. Also
learned in the process: `claimed_amount` is a structured JSON field on every claim (not
prose), so it's always exactly visible to whichever agent fetches the prior claim regardless
of notes wording — dollar-figure matching can't be hidden through phrasing at all.

**Resolution, per the user's own suggestion**: stop trying to force the live Investigator
into a specific wrong answer. `orchestrator/ground_truth.py`'s `s08` entry now records the
real observed behavior honestly (`expected_investigator: INVALID`, `expected_reviewer:
CONFIRM` — same pattern as s07, independent fixture data, still legitimate additional
end-to-end coverage). Separately, added
`tests/test_pipeline_scenarios.py::test_reviewer_overturns_a_missed_duplicate`: feeds the
*live* Reviewer a hand-authored, fabricated CaseFile against s08's real fixtures
(`proposed_verdict: "VALID"`, `prior_claims: []`, as if a hypothetical Investigator had
reconciled quantities correctly but never surfaced `CLM-008a`) and asserts the Reviewer's
mandatory `list_claims_for_po` re-check still independently finds the prior claim and
returns `OVERTURN` regardless of what the case file claimed. This proves the
segregation-of-duties safety net actually works, without depending on the real Investigator
ever being wrong. `docs/SPEC.md`'s "Eighth Scenario" section rewritten to document this
full story so a future reader isn't confused by why `s08`'s ground truth doesn't show
`OVERTURN`.

**Live verification**: `test_reviewer_overturns_a_missed_duplicate` passed on first live run.
Full `pytest tests/test_pipeline_scenarios.py -m integration -v` (9 tests: 8 scenarios + the
new dedicated test): **8/9 passed** first run, one failure —
`test_scenario_matches_ground_truth[s04_sequence_violation]` (`reviewer_verdict: OVERTURN`
instead of expected `CONFIRM`). s08 itself and the new dedicated test both passed clean.
Re-ran s04 alone: passed. Re-ran the full suite again: s04 failed again (2 of 3 live
attempts total). Confirmed via trace this is unrelated to s08/Layer 10 — a real, previously
undiscovered bug in `agents/reviewer.py`: the Layer 9 follow-up's s01 fix added "a shortage
that every document consistently confirms is exactly what a legitimate deduction claim looks
like" / "don't re-litigate liability" language to stop the Reviewer manufacturing
out-of-scope disputes — and the model is now citing that exact language to excuse a genuine,
independent timeline-sequence violation in s04 instead of disputing it. Per explicit user
decision, **not fixed this session** — logged below as the top-priority known issue for the
next session, matching the project's discipline of reporting live results honestly rather
than silently patching around them mid-layer.

`pytest tests/` — 125 passed, 0 failed, 9 deselected (unit suite; one more deselected test
than Layer 9 since the new dedicated integration test was added). Live:
`pytest tests/test_pipeline_scenarios.py -m integration -v` — 8/9 passed on the representative
run above (s04 is the known-flaky exception, see "Known issues").

Updated `README.md`'s layer-status table (Layer 10 done), CLI section ("all 8 scenarios"),
and "The seven scenarios" → "The eight scenarios" with an s08 row explaining the pivot.

---

## Previous layer
**Layer 9 — Integration tests + README complete (build order finished)**

Added `tests/test_pipeline_scenarios.py`: one `@pytest.mark.integration` test, parametrized
over `orchestrator.ground_truth.GROUND_TRUTH` (reused directly, not re-hardcoded), calling
`run_pipeline` with no injected clients so it builds a real `AsyncOpenAI` + spawns the real
MCP server subprocess — the first tests in the suite to hit the network. Follows
`ground_truth.py`'s own docstring: compares `investigator_verdict`/`reviewer_verdict` against
`expected_investigator`/`expected_reviewer`, never `final_verdict` (same convention as
`cli/run_all.py`). For the 4 scenarios with a required-tool-call check
(`orchestrator/pipeline.py`'s `REQUIRED_TOOL_CALLS`), also reads the written
`reasoning_trace.json` and asserts the specific tool name appears in the Investigator's
`tool_calls` — making explicit what `run_pipeline` already enforces internally (it would raise
`PipelineError` otherwise). Also asserts `verdict.json`/`reasoning_trace.json` always exist and
`dispute_packet.md` exists iff `final_verdict == "INVALID"`.

Registered the `integration` marker in `pyproject.toml` and set
`addopts = "-m 'not integration'"` so plain `pytest`/`pytest tests/` never spends API credits
by accident — integration tests require explicit `-m integration`. Added `tests/conftest.py`
(`load_dotenv()`) since pytest doesn't source `.env` on its own the way the CLI scripts'
`__main__` blocks do — without it the new tests silently skipped (missing
`OPENROUTER_API_KEY` in `os.environ` despite it being in `.env`).

Fixed the stale `ANTHROPIC_API_KEY` reference in `docs/PLAN.md`'s verification snippet to
`OPENROUTER_API_KEY`. Rewrote `README.md`: layer-status table now shows all 9 layers done, new
"Running the CLI" section (`.env` setup, `run_claim`/`run_all` invocations, output artifacts
per claim), "Running tests" now distinguishes default unit tests from opt-in integration
tests, and a new "Future work" section transcribing `CLAUDE.md`'s "Explicit out of scope" list
(parallel orchestration, SKU-to-product-name mapping, heterogeneous mock data sources,
API-facing deployment concerns) so a reader doesn't have to open `CLAUDE.md` to find it.

**First-ever live run of all 7 scenarios against real OpenRouter — mixed results, logged
honestly rather than adjusted to pass.** `pytest tests/test_pipeline_scenarios.py -m
integration -v`: **4/7 passed** (s02, s03, s05, s07) on the full run. Re-ran the 3 failures
(s01, s04, s06) alone to capture full tracebacks (the first run's output got truncated by an
overly aggressive `tail` in the capture command, not a test problem):
- **s06** passed on the re-run (`OVERTURN` the first time, `CONFIRM` the second) — looks like
  genuine model-response variance run-to-run rather than a reproducible bug; not investigated
  further this session.
- **s01 failed both times**: Reviewer returns `OVERTURN` instead of `CONFIRM` for the
  "everything genuinely agrees" scenario. Reproducible across 2 runs — not a fluke.
- **s04 failed both times** with the same root cause: the Investigator calls `get_po`,
  `get_asns_for_po`, `get_invoice`, `get_receiving_record` using `po_id="CLM-004"` (the
  *claim* ID) instead of resolving the actual PO ID (`PO-004`) from `get_deduction_claim`'s
  response first. Every one of those calls fails with `ValueError: po_id 'CLM-004' not found`,
  so the Investigator ends up with almost no evidence and (reasonably, given what it actually
  gathered) proposes `ESCALATE` instead of `INVALID`. Root cause:
  `INVESTIGATOR_SYSTEM_PROMPT` (`agents/investigator.py`) lists
  "get_deduction_claim, get_po, get_asns_for_po, ..." as one flat sequence without explicitly
  telling the model that `get_po`/`get_asns_for_po`/`get_invoice`/`get_receiving_record` all
  take the PO ID returned by `get_deduction_claim`, not the claim ID itself — the model is left
  to infer that, and Haiku got it wrong specifically on s04.

**Not fixed this session** — Layer 9's scope (per the approved plan) was integration tests +
README, not agent-prompt changes; per `CLAUDE.md`, the fix belongs in
`agents/investigator.py`'s system prompt (make the claim_id→po_id handoff explicit), not in
fixture data. Flagged as the top item for a follow-up session — see "Known issues" below.

`pytest tests/` — 125 collected, 118 passed + 7 deselected by default (the 7 deselected are
exactly `test_pipeline_scenarios.py`'s parametrized integration cases — confirmed via
`--collect-only` that no other file's count changed this layer). Live integration run:
`pytest tests/test_pipeline_scenarios.py -m integration -v` — 4/7 passed (see above).

---

## Follow-up session — fixed the two live failures from Layer 9's first run

Both issues flagged in Layer 9's "Known issues" (s04 wrong-ID tool calls, s01 spurious
`OVERTURN`) turned out to be prompt gaps, not fixture problems — fixed per `CLAUDE.md`'s
"change the agent prompts or tool logic instead" rule, with every fix confirmed against a real
live run before moving to the next, not just unit tests. Two additional bugs surfaced live
during that verification that hadn't been in the original "Known issues" list at all.

**1. Investigator claim_id/po_id confusion (s04's root cause).** `INVESTIGATOR_SYSTEM_PROMPT`
(`agents/investigator.py`) now spells out that `get_po`/`get_asns_for_po`/`get_invoice`/
`get_receiving_record`/`list_claims_for_po` all take the `po_id` returned by
`get_deduction_claim`, never the `claim_id` — previously the model had to infer this and Haiku
got it wrong specifically on s04. Confirmed live: the Investigator's tool-call trace no longer
shows any `po_id="CLM-004"` errors.

**2. Same bug, independently, in the Reviewer — not previously noticed.** Re-running s04 live
after fix #1 still errored, but now inside the *Reviewer's* trace: `agents/reviewer.py`'s
`REVIEWER_SYSTEM_PROMPT` never told it how to get a `po_id` at all, and the stripped case file
it receives only has `claim_id` — no `po_id` field. The Reviewer had been silently guessing
`po_id` by pattern-matching the claim_id's format (`CLM-004` → `PO-004`), which only worked by
coincidence of this fixture set's naming convention, then self-correcting after 4 tool errors
per run. Fixed by telling the Reviewer explicitly to call `get_deduction_claim(claim_id)` itself
first to resolve the real `po_id`, same as the Investigator now does.

**3. Investigator found the s04 timeline violation but didn't act on it.** Even after fix #1,
s04 still resolved to `VALID` instead of `INVALID` — the model's own reasoning identified
"invoice dated before shipment ... physically impossible" but then proposed `VALID` anyway
("the shortage itself is well-documented ... despite this timeline concern"). The prompt's
step 3 said a sequence violation was "a red flag" but never said what verdict that implies,
leaving it to the model's judgment call, which went the wrong way. Fixed by stating explicitly
that any timeline sequence violation is grounds to propose `INVALID` and must not be overridden
by an otherwise-clean quantity match. Confirmed live: s04 now resolves `INVALID` → `CONFIRM`.

**4. Reviewer manufacturing an out-of-scope dispute (s01's root cause) — not a simple
"try harder to agree" fix.** The first attempted fix (telling the Reviewer that `CONFIRM` is a
valid, expected outcome, not a failure to find something) was not sufficient by itself — a
follow-up live re-run of all 7 scenarios still failed s01 with `OVERTURN`. Read that run's
actual reasoning trace rather than assuming the first fix worked: the Reviewer wasn't
manufacturing a disagreement out of nothing, it was doing real (but out-of-scope) analysis —
arguing that because the receiving record's carrier-signed BOL exception documents damage in
transit, "this is a carrier claim, not a valid supplier deduction," i.e. relitigating *whose
fault* the shortage was. That question isn't one of the six checks
(`uom`/`split_shipment`/`timeline`/`trade_agreement`/`duplicate`/`substitution`) the Reviewer's
own `review_findings` schema defines, and s01's designed trap is precisely that everything
genuinely agrees — the carrier's BOL acknowledgment is supporting evidence *for* the shortage
being real, not grounds to redirect liability. Fixed by scoping the Reviewer's prompt explicitly
to those six checks and telling it not to introduce dispute grounds outside them, specifically
calling out liability-apportionment arguments as out of scope. Confirmed live across two
separate full-suite runs after this fix (see below).

**5. Investigator cents/dollars calculation bug, found while diagnosing #4, not
independently sought out.** The same s01 trace showed the Investigator's CaseFile had
`discrepancy_amount_cents: 300000` where it should be `3000` (12 units × 250-cents-per-unit
`unit_price` = 3000 cents = $30, matching `docs/SPEC.md`'s own worked example for this exact
scenario) — the model read `unit_price: 250` as $250 and multiplied by 100 again converting to
cents, a 100x error. Nothing in `INVESTIGATOR_SYSTEM_PROMPT` said `unit_price`/amounts were
already in cents. This wasn't caught by any test because no test asserts on
`discrepancy_amount_cents`'s value and it doesn't fail the ground-truth verdict check directly
— but it was the reasoning fuel behind the Reviewer's fix #4 tangent, and is a real correctness
bug in its own right (would corrupt dispute-packet dollar amounts). Fixed by stating explicitly
in step 4 that `unit_price` and all amounts are already USD cents, with no additional
conversion.

**Verification discipline**: every fix in this session was confirmed by re-running the actual
live pipeline (`cli.run_claim` and/or the integration test) and reading the resulting
`reasoning_trace.json`, not just by re-running unit tests or assuming the prompt change worked.
Fix #4 in particular was caught only because a second live full-suite run was done after the
first (insufficient) attempt — a single passing live run should not be treated as proof a
prompt fix actually addressed the root cause, given documented run-to-run model variance.

`pytest tests/` — 118 passed, 0 failed, 7 deselected (unit suite unaffected by these prompt-only
changes). **Live**: `pytest tests/test_pipeline_scenarios.py -m integration -v` — 7/7 passed,
confirmed on a full run after all five fixes above (s06, previously flagged as flaky, also
passed clean this run — consistent with the "genuine model variance, not a bug" read from
Layer 9).

---

## Previous layer
**Layer 8 — `cli/run_claim.py` + `cli/run_all.py` complete**

Built `cli/run_claim.py`: `parse_args` (stdlib `argparse`) takes `--claim-id`/`--scenario`
(required) plus `--output-dir`/`--max-attempts` (default to `run_pipeline`'s own defaults, so
omitting them changes nothing). `main(argv=None, *, openai_client=None, mcp_client=None,
console=None)` mirrors `run_pipeline`'s own DI convention — real invocation via `__main__`
passes no clients, so `run_pipeline` constructs the real `AsyncOpenAI`/subprocess MCP client;
tests inject `StubAsyncOpenAI` + a real in-process `fastmcp.Client(mcp)`. Catches
`PipelineError`/`AgentRunnerError` and prints a clean error instead of a traceback, and checks
for `OPENROUTER_API_KEY` up front (before touching `run_pipeline`) so a missing key fails fast
with a clear message rather than a deep `KeyError`. Prints a `rich` table of the `PipelineResult`
(verdicts, confidence, output dir) plus dispute grounds when present.

Built `cli/run_all.py`: hardcoded `GROUND_TRUTH` list from `docs/SPEC.md`'s ground-truth table
(scenario, claim_id, expected_investigator, expected_reviewer). `main()` runs all 7 scenarios
**sequentially** (per `CLAUDE.md`'s out-of-scope note on parallel orchestration), sharing one
`AsyncOpenAI` client across calls but leaving `mcp_client` unset so each scenario gets its own
subprocess with the correct `SCENARIO_ID`. Accepts an injectable `run_pipeline_fn` purely for
testing the table/pass-fail/exit-code logic without needing 7 real or stubbed pipeline runs. A
`PipelineError`/`AgentRunnerError` on one scenario is recorded as an error row and does **not**
abort the remaining scenarios. Exit code `0` only if all 7 scenarios match ground truth.

**Correctly implemented the documented gotcha**: the pass/fail check compares
`result.investigator_verdict` against `expected_investigator` **and** `result.reviewer_verdict`
against `expected_reviewer` (`"CONFIRM"` for all 7) — never against `.final_verdict`. Added
`test_ground_truth_check_uses_reviewer_verdict_not_final_verdict` as an explicit regression test
(constructs a fake result with a `final_verdict` that would fail the check if compared against
by mistake, asserts it still passes).

**`run_all.py` initially had a real bug, caught during manual testing, not code review**: since
it took zero CLI flags, it never called `argparse` at all — running `python -m cli.run_all
--help` silently ignored the flag and executed a real 7-scenario run against the live
OPENROUTER_API_KEY instead of printing help. Caught this manually (see below) after it had
already launched a few real MCP subprocesses; killed it, no partial `outputs/` were written.
Fixed by giving `run_all.py` its own (empty) `parse_args`/`argv` handling too, so `--help` and
any unrecognized flag now fail fast via `argparse` instead of falling through to a real run.
Added `test_parse_args_rejects_unrecognized_flags` as a regression test.

Added `python-dotenv` as a new dependency; both scripts call `load_dotenv()` in their `__main__`
block so `.env`'s `OPENROUTER_API_KEY`/`SCENARIO_ID` are picked up automatically — previously
nothing in the codebase loaded `.env` at all.

**Live smoke test against real OpenRouter** (`python -m cli.run_claim --claim-id CLM-002
--scenario s02_casepack_mismatch`), first real end-to-end run of the whole system: initially
**failed** — `PipelineError: Investigator failed to produce a valid CaseFile ... after 3
attempts: Expecting value: line 1 column 1 (char 0)`. Diagnosed by calling `run_investigator`
directly against the real model: it reasons through the five-step protocol in prose, then emits
the CaseFile inside a ` ```json ` fence *after* that prose — despite the system prompt's "ONLY a
single JSON object, no prose before or after" instruction. `_extract_json` in
`orchestrator/pipeline.py` only stripped a fence when the response *started* with `` ``` ``, so
it fed the whole prose+JSON blob to `json.loads` and failed identically on all 3 retries (the
correction message didn't change the model's behavior). Fixed `_extract_json` to (1) find a
` ```json `/`` ``` `` fence anywhere in the text via regex, and (2) fall back to the outermost
`{...}` brace-matched substring if there's no fence at all — a `tool logic` fix per `CLAUDE.md`,
not a prompt or fixture change. Added `test_extract_json_handles_prose_before_json` (parametrized)
and `test_happy_path_survives_prose_before_fenced_json` (full `run_pipeline` regression test
using a stub that reproduces the exact prose-then-fence pattern). Re-ran the live smoke test
after the fix: CLM-002 now correctly resolves `INVALID` → `CONFIRM` (matches SPEC.md), with all
three output artifacts written correctly, confirming the CLI, `run_pipeline`, and the fix all
work end-to-end against a real model. Did not run the full `run_all.py` 7-scenario suite live
this session (cost/time tradeoff, deferred to a future session or explicit request).

Wrote `tests/test_cli_run_claim.py` (8 tests) and `tests/test_cli_run_all.py` (7 tests), plus 2
new tests in `tests/test_orchestrator_pipeline.py` for the `_extract_json` fix — all following
the established `StubAsyncOpenAI` + real in-process `fastmcp.Client(mcp)` convention (no
subprocess/network calls in the test suite).

`pytest tests/` — 116 passed, 0 failed (98 prior + 18 new).

---

## Previous layer
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
The Layer 1-10 build order is complete. Suggested next steps, in rough priority order:

1. **Fix the s04 Reviewer regression found during Layer 10** (see "Known issues" below):
   `agents/reviewer.py`'s prompt needs a carve-out clarifying that "don't re-litigate
   liability / a consistently-documented shortage is legitimate" (added for s01 in the Layer
   9 follow-up) is independent from — and must not excuse — a genuine timeline-sequence
   violation. Confirmed reproducible in 2 of 3 live full-suite runs this session. Per this
   project's own verification discipline, don't trust a single clean re-run as proof once
   fixed — re-run the live suite multiple times, same as the Layer 9 follow-up did for s01.
2. Nothing else is currently flagged. Revisit "Explicit out of scope" in `CLAUDE.md` (parallel
   orchestration, SKU-to-product-name mapping, heterogeneous mock data sources, API-facing
   deployment concerns) only if the user asks to expand scope beyond the original 9-layer build.
   Layers 11-22 in `docs/PLAN.md` (demo/production hardening + additive web UI) are approved
   and ready whenever prioritized.

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
| 8 | `cli/run_claim.py` + `cli/run_all.py` | ✅ Done |
| 9 | Integration tests + README | ✅ Done |
| 10 | `scenarios/s08_reviewer_overturn/` (8th scenario) | ✅ Done |

## Tests passing
`pytest tests/` — 125 passed, 0 failed, 9 deselected (the 9 deselected are
`test_pipeline_scenarios.py`'s integration tests — 8 parametrized scenarios + the dedicated
`test_reviewer_overturns_a_missed_duplicate`):
- `test_cli_run_claim.py` (8): argparse required-flag enforcement and `--output-dir`/
  `--max-attempts` defaults/overrides, happy path against s01 (exit 0, verdict fields printed,
  `verdict.json` written under a custom `--output-dir`), `PipelineError` path (exit 1, clean
  error message, no traceback), missing-`OPENROUTER_API_KEY` path (exit 1 without touching
  `run_pipeline` at all).
- `test_cli_run_all.py` (7): all-7-pass summary line and exit 0; one scenario's
  `investigator_verdict` mismatch fails only that row (exit 1, others still pass); a
  `PipelineError` on one scenario is recorded as an error row and the loop continues to the
  rest; missing-`OPENROUTER_API_KEY` returns 1 without ever calling `run_pipeline_fn`; explicit
  regression test that the ground-truth check compares `reviewer_verdict` (not `final_verdict`)
  against `"CONFIRM"`; `parse_args` regression test that `--help`/an unrecognized flag raises
  `SystemExit` rather than falling through to a real run (see Layer 8 notes above for why this
  matters).
- `test_orchestrator_pipeline.py` (15): s01/s02 happy paths (investigator/reviewer wiring,
  output-artifact presence/absence), missing-required-field and missing-required-tool-call
  correction retries, `max_investigator_attempts` exhaustion, reasoning-strip safeguard,
  full `_resolve_final_verdict` truth table, `_extract_json` parametrized cases (fenced,
  unfenced, prose-before-fence, prose-before-unfenced-brace) and a full-pipeline regression test
  reproducing the exact live prose-before-fence failure.
- `test_orchestrator_output.py` (5): `verdict.json`/`reasoning_trace.json`/`dispute_packet.md`
  writers — paths, JSON shape, markdown content, empty-dispute-grounds handling, nested
  output-dir creation.
- `test_fixtures.py` (52): Pydantic model validation for every fixture file, po_id/retailer
  cross-document consistency, file-layout expectations per scenario (now 8 — `prior_claim.json`
  allowed in both s07 and s08), ground-truth claim_id matches, and each scenario's specific
  numeric trap, including s08's clean-agreement-before-shortage and
  amount-implies-the-same-shortage checks.
- `test_pipeline_scenarios.py` (9, `-m integration` only): all 8 `GROUND_TRUTH` scenarios
  end-to-end against the real pipeline, plus `test_reviewer_overturns_a_missed_duplicate`
  (see Layer 10 notes above).
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
- **NOT resolved — top priority for next session, found during Layer 10 (unrelated to
  s08)**: `agents/reviewer.py`'s prompt has a regression affecting s04, one of the 7 frozen
  scenarios. Live-tested 3 times this session: failed 2/3 (`reviewer_verdict: OVERTURN`
  instead of expected `CONFIRM`), passed once. Root cause per the trace: the Layer 9
  follow-up's s01 fix added "don't re-litigate liability... a shortage that every document
  consistently confirms is exactly what a legitimate deduction claim looks like" to stop the
  Reviewer manufacturing out-of-scope liability disputes. The model is now citing that exact
  language to excuse s04's genuine, independent timeline-sequence violation (invoice dated
  before shipment) instead of disputing it — i.e., the general "don't re-litigate" guidance
  is bleeding into a check it was never meant to touch. Needs an explicit carve-out in the
  Reviewer's prompt clarifying that liability-apportionment and timeline-sequence violations
  are independent checks, and the former's "don't manufacture a dispute" guidance must not
  excuse a real finding on the latter. User explicitly chose to log this rather than fix it
  in the same session as s08, to keep Layer 10's diff scoped.
- **Resolved (follow-up session, see above)**: s04's Investigator was calling
  `get_po`/`get_asns_for_po`/`get_invoice`/`get_receiving_record` with `po_id="CLM-004"` (the
  claim ID) instead of the actual PO ID (`PO-004`). Fixed by making `INVESTIGATOR_SYSTEM_PROMPT`
  state explicitly that these tools take the PO ID from `get_deduction_claim`'s response, not
  the claim ID. The identical bug was then found in the Reviewer too (not previously noticed)
  and fixed the same way. Confirmed live, 7/7.
- **Resolved (follow-up session, see above)**: s04's Investigator also identified its own
  timeline violation finding but didn't act on it (proposed `VALID` despite noting the invoice
  predates the shipment). Fixed by stating explicitly that a sequence violation is grounds for
  `INVALID` regardless of otherwise-clean quantities.
- **Resolved (follow-up session, see above)**: s01's Reviewer returned `OVERTURN` instead of
  `CONFIRM` for the scenario where every document genuinely agrees — not a manufactured
  disagreement but real, out-of-scope analysis (a carrier-liability argument outside the six
  defined review checks). Fixed by scoping the Reviewer's prompt to exactly its six checks and
  naming liability apportionment as explicitly out of scope. A first attempted fix ("CONFIRM is
  a valid outcome, don't overturn just to justify the check") was insufficient by itself — this
  was only caught by reading the actual reasoning trace from a second live full-suite run, not
  by assuming the first fix worked.
- **Resolved (follow-up session, see above)**: found while diagnosing the s01 issue — the
  Investigator's `discrepancy_amount_cents` was 100x too large (300000 instead of 3000) because
  it treated `unit_price` (already cents-per-unit per `docs/SPEC.md`) as dollars and converted
  to cents again. Fixed by stating explicitly in the prompt that `unit_price`/amounts are
  already in cents. Not caught by any test (nothing asserts on this field's value), only by
  reading a live reasoning trace.
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
- **Resolved**: the "Final expected" vocabulary gotcha flagged at the end of Layer 7 —
  `cli/run_all.py`'s pass/fail check compares `reviewer_verdict` (not `final_verdict`) against
  `"CONFIRM"`, with an explicit regression test. See Layer 8 notes above.
- **Found and fixed during Layer 8 (manual smoke test, not code review)**: `cli/run_all.py` took
  no CLI flags at all and never called `argparse`, so `python -m cli.run_all --help` silently
  ignored the flag and executed a real 7-scenario run against the live `OPENROUTER_API_KEY`
  instead of printing help — caught mid-run and killed, no partial `outputs/` written. Fixed by
  giving it an (empty) `parse_args`/`argv` too, so `--help`/any unrecognized flag now fails fast.
  Worth remembering for any future zero-flag CLI entry point in this project: always parse
  `sys.argv` even with no real options, so unexpected input can't fall through into a real run.
- **Found and fixed during Layer 8's live smoke test**: `orchestrator/pipeline.py`'s
  `_extract_json` only stripped a ` ``` ` fence when the model's response *started* with it. The
  real Investigator model (via OpenRouter) reasons through the five-step protocol in prose first,
  then emits the CaseFile inside a ` ```json ` fence afterward — despite the system prompt's
  explicit "no prose before or after" instruction, and the correction-retry loop's message didn't
  change this behavior across 3 attempts. Fixed `_extract_json` to find a fence anywhere in the
  text (regex) and, failing that, fall back to the outermost `{...}` brace-matched substring.
  Confirmed live against s02 after the fix (`INVALID`→`CONFIRM`, matches SPEC.md). Only observed
  live on s02 so far — worth watching for the same pattern on the other 6 scenarios when Layer 9
  runs `run_all.py` live for the first time.

---
*Update this file at the end of every session before stopping.*
