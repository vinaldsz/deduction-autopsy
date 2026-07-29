# Deduction Autopsy — Claude Code Briefing

## What this project is

A two-agent reconciliation system that investigates CPG retailer deduction claims and
determines whether they are valid, invalid (disputable), or ambiguous (escalate to human).

The core loop: Investigator agent gathers all source documents via MCP tools, normalizes
unit-of-measure, proposes a verdict → Reviewer agent independently spot-checks the
highest-risk steps and either CONFIRMs, OVERTURNs, or ESCALATEs.

Full domain context and architecture in `docs/SPEC.md`.
Full implementation plan in `docs/PLAN.md`.

---

## Non-negotiable design decisions

**Two agents, always.** The Investigator and Reviewer are separate agents with separate
system prompts. Never collapse them into one agent "for simplicity." The Reviewer exists
because the same agent that builds a case cannot reliably grade its own work — this is a
segregation-of-duties control, not architectural decoration.

**MCP server is the only data access path.** Agents must call MCP tools to get document
data. They must not have fixture files on their context directly. This is what makes the
tool-call trace meaningful as an audit trail. (As of the Layer 23–31 phase the MCP tools
read a relational SQLite DB instead of per-scenario JSON — the abstraction is unchanged;
agents still see only tools, never the backing store. See "Semantic/DB layer" below.)

**Eight scenarios are ground truth.** (Seven at first; `s08_reviewer_overturn` arrived in Layer
10.) The expected verdicts in `docs/SPEC.md` are fixed.
Do not change fixture data to make a failing scenario pass — change the agent prompts or
tool logic instead. Changing expected verdicts requires explicit user sign-off.

**UI is additive, not a replacement.** Approved 2026-07-19: a FastAPI + minimal HTML/JS
UI (Layer 19+ in `docs/PLAN.md`) is now in scope, reversing the earlier "no frontend"
decision. `cli/run_claim.py`/`cli/run_all.py` are kept, not deprecated — they remain the
scriptable/CI path (see Layer 15). The UI calls the same `orchestrator/pipeline.py`, binds
to `127.0.0.1` only, and carries no auth, matching the CLI's existing trust model.

**Semantic/DB layer backs the MCP (approved 2026-07-25).** Data flows source systems → ETL
(extract/transform/load, in `semantic_layer/`) → relational SQLite (`data/deductions.db`) → the
MCP tools → agents. This *strengthens*, not weakens, "MCP server is the only data access path":
agents still see only MCP tools, never the backing store — the store just changed from per-
scenario JSON to a DB. "Scenario" is retired from the runtime path: investigations are keyed by
`claim_id` alone and the agent navigates the entity graph (claim → PO → ASN/invoice/receiving/
prior claims) itself. The frozen `scenarios/*/*.json` remain in the repo as the ETL **fidelity
oracle** (a test asserts the DB equals them field-for-field), so the 8 ground-truth verdicts
cannot drift. ETL is DE-grade: heterogeneous sources, incremental merge-upsert, quarantine +
data-quality report, lineage + load audit. Full roadmap: Layers 23–31 in `docs/PLAN.md`.

---

## Explicit out of scope

- Real EDI X12 parsing — fixtures only need to resemble real documents, not be valid EDI
- Any third-party integrations (NetSuite, Shopify, Amazon, etc.)
- Parallel/concurrent orchestration — mention as future work in README, do not build
- SKU-to-product-name mapping — SKUs stay opaque codes (e.g. "SKU-001") everywhere, no
  product master/catalog; mention as future work in README (display-only, cosmetic for
  dispute packets), do not build
- ~~Heterogeneous mock data sources~~ **Reversed 2026-07-25 — now IN scope (Layers 23–31,
  `docs/PLAN.md`).** The original gate ("worth pursuing once the in-scope build, layers 1-9, is
  complete") was satisfied at the time of the reversal (the build was then complete through Layer
  22; it now runs through Layer 38 — see PROGRESS.md). A real ETL now ingests
  heterogeneous source systems (ERP CSV, carrier EDI-ish flat text, WMS/portal/TPM JSON) into a
  relational SQLite DB the MCP reads — see "Semantic/DB layer" under design decisions above.
  Data model + ETL contract live in `docs/SPEC.md`; the SKU→UOM conversion table stays a JSON
  reference file (global, outside the claim graph).
- Production concerns: auth, multi-tenancy, persistence beyond local files
- API-facing deployment (auth, per-user/per-IP rate limiting, per-user cost caps on OpenRouter
  usage) — the Layer 19+ UI (see below) does not change this: it's still a local, single-user,
  no-auth surface bound to `127.0.0.1`, same trust model as the CLI. Auth/rate-limiting/cost
  caps only become a real concern if this is ever exposed beyond localhost to multiple users;
  mention as future work in README, do not build now

---

## Tech stack

- Python 3.11+
- FastMCP (Python MCP SDK) for the MCP server
- OpenRouter (OpenAI-compatible chat completions API) via the `openai` Python SDK's
  `AsyncOpenAI`, pointed at `base_url="https://openrouter.ai/api/v1"` with
  `OPENROUTER_API_KEY` — NOT the Anthropic SDK directly, and NOT Anthropic's native
  Messages/tool_use format. Deliberate deviation from the original plan, approved 2026-07-18.
  - Investigator: Claude Haiku 4.5 (mechanical data-fetch + compare) — confirmed OpenRouter
    slug `anthropic/claude-haiku-4.5` (Layer 6, checked against OpenRouter's live catalog).
  - Reviewer: Claude Sonnet 4.5 (subtle reasoning for trap detection) — confirmed OpenRouter
    slug `anthropic/claude-sonnet-4.5` (Layer 6, same check).
- Fixtures as plain JSON, checked into repo (also the ETL fidelity oracle as of Layer 23+)
- SQLite (stdlib `sqlite3`) as the relational backing store the MCP reads — approved
  2026-07-25, Layers 23–31; heterogeneous source files under `source_systems/`, ETL in
  `semantic_layer/`, DB at `data/deductions.db` (gitignored), path via `DEDUCTIONS_DB`
- `pytest` for unit tests; `rich` for CLI output
- Temperature 0 for both agents (deterministic, debuggable)
- FastAPI + `uvicorn` for the Layer 19+ UI; plain HTML/JS served as static files (no
  frontend build step, no framework) — approved 2026-07-19, see "UI is additive" above

---

## Build order — do not skip ahead

This section is the **historical** order the build was done in, kept because "do not skip ahead"
still applies to whatever layer is current. For where the project actually stands, read
PROGRESS.md's `## Current layer`; for the index, the table in README.md.

1. `mcp_server/models.py` + `data/sku_uom_conversions.json`
2. The scenario fixture JSON files (verified against Pydantic models) — 7 at this point; s08
   arrived in Layer 10, so there are 8 on disk today
3. `mcp_server/fixtures.py` + `mcp_server/tools/` + unit tests passing
4. `mcp_server/server.py` (wire FastMCP)
5. `agents/base.py` (shared tool loop)
6. `agents/investigator.py` + `agents/reviewer.py` (system prompts)
7. `orchestrator/pipeline.py` + `orchestrator/output.py`
8. `cli/run_claim.py` + `cli/run_all.py`
9. Integration tests + README

Layers 10–22 (see `docs/PLAN.md` / PROGRESS.md): Reviewer-overturn scenario, config, retry,
usage tracking, CLI batch, hooks, prompt-injection test, and the Web UI phase (19–22).

**Semantic/DB layer phase — Layers 23–31 (approved 2026-07-25, full detail in `docs/PLAN.md`):**

23. Data model & source-mapping design (USER-LED) → `docs/SPEC.md` schema + DDL in `mcp_server/db.py`
24. Heterogeneous source-system fixtures (`source_systems/`) + generator
25. ETL Extract — per-source parsers (`semantic_layer/extract/`) + lineage seed
26. ETL Transform + Data Quality — coerce/RI/dedup, quarantine + DQ report
27. ETL Load — incremental merge-upsert + lineage + batch gate; **fidelity oracle** test
28. DB-backed `FixtureLoader` + document tools (scenario-less, keyed by po_id/claim_id)
29. Scenario-less pipeline + CLI + `claim_resolutions` persistence
30. Dashboard + daily-lot worklist UI (replaces the scenario dropdown)
31. Universal completeness check + ESCALATE on missing source data — **built** (signed off; built
    out of order, after Layer 32, because it is the only layer that edits the agent prompts)

**Analyst-workspace phase — Layer 32, then the UX remediation in Layers 33–41** (approved
2026-07-28; the 33–41 block acts on a 40-finding UI/UX review of the Layer 30/32 dashboard taken
from a deductions analyst's seat). Full detail in `docs/PLAN.md`:

32. Analyst review workspace — evidence-first UI + human decisions (`claim_dispositions`)
33. JS test harness (`ui/static/lib.js` + `node --test`) + render hygiene
34. Decision integrity — `accept` recorded as a snapshot, not a pointer (**the only schema gate**)
35. KPIs that add up — the to-do/decided partition
36. Verdict semantics that match the money — tone keyed to money direction, not verdict name
37. A grid you can work — split 37a (query surface) / 37b (the grid itself)
38. Working the volume — bulk accept, keyboard path, pinned decision bar, save-and-next
39. Explainability — reasoning, runs, checks, timeline
40. Run transparency — a batch stream that survives one claim failing
41. Export, print, light mode, density

**Rule:** Do not start layer N+1 until layer N has passing tests. Check PROGRESS.md for
current state before starting any session.

**Two standing rules the UX phase added:** no agent/prompt/verdict-logic changes and no fixture
edits anywhere in Layers 33–41 — the 8 ground-truth verdicts stay untouched; and pure frontend
logic goes in `ui/static/lib.js` (tested by `node --test`) while the DOM+fetch modules stay free of it.
Those modules and the stylesheet are unreachable by every gate, which is why each of these layers ends
by driving the running app: five of them found bugs that way, three in Layer 38 alone.

**Frontend module layering (added by the post-Layer-39 refactor).** `app.js` was one 1230-line file;
it is now ~18 modules in `ui/static/`, in four layers, and **a module may import only from a lower
layer or its own**:

    dom / state / stream / api   ->   renderers   ->   actions   ->   app.js (wiring + boot)

`tests/js/architecture.test.mjs` enforces it — acyclic graph, no upward imports, every used name
imported, every module reachable from `app.js`, and `lib.js` free of DOM. That test is the reason the
split is durable: without it, "renderers never import actions" is a comment, and the monolith returns
one convenient import at a time. Adding a module means declaring its layer in that test.

---

## Clean Code Principles

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:

- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

## Safeguards — do not remove these

**CaseFile schema validation:** The orchestrator parses and validates the Investigator's
CaseFile JSON against required fields before passing it to the Reviewer. If fields are
missing, it sends a correction message and forces another turn. The Reviewer never sees
an incomplete CaseFile.

**Tool-call trace verification:** After the Investigator runs, the orchestrator checks
that scenario-required tools appear in the actual tool-call trace (not just the text).
For scenarios involving UOM differences, `normalize_uom` must appear in the trace.
For scenario 7, `list_claims_for_po` must appear.

**Stripped reasoning handoff:** The orchestrator passes the Investigator's CaseFile to
the Reviewer with the `reasoning` field removed. The Reviewer sees the numbers and
checklist results, not the Investigator's narrative argument. This prevents anchoring.

**XML-delimited CaseFile:** The CaseFile is embedded in the Reviewer's user message
inside `<case_file>...</case_file>` tags, treated as data not instructions. This guards
against prompt injection via fixture `notes` fields.

---

## Git practices

**One commit per layer.** Commit only when the layer's tests pass. Never commit broken code.

**Commit message format:**

```
Layer N: <what was built>

- bullet summarizing key decisions or non-obvious choices
```

**Branch per layer** (optional but recommended):

```
main          ← only receives merges when a layer is complete and tests pass
layer/1-models
layer/2-fixtures
...
```

**.gitignore must include:**

```
.env
__pycache__/
*.pyc
outputs/
.pytest_cache/
```

**Never commit:**

- `.env` (contains OPENROUTER_API_KEY)
- `outputs/` (generated artifacts, not source)

**Push cadence:** push to remote at the end of each session after committing.

---

## Session workflow

- Check `PROGRESS.md` before starting — it tells you what layer we're on and what tests pass
- Use plan mode (`/plan`) for anything that touches more than one file
- One session = one layer from the build order
- End each session by running the relevant tests; update `PROGRESS.md` with results

---

## Context management

- Run `/compact` when messages reach ~500k tokens (roughly 50% of the 967k window)
- Auto-compaction is enabled in `.claude/settings.json` as a backstop — but compact manually before that to preserve useful context in the summary
- Use `/clear` when switching to a completely unrelated task (not just a new layer)
