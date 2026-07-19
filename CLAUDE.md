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
tool-call trace meaningful as an audit trail.

**Seven scenarios are ground truth.** The expected verdicts in `docs/SPEC.md` are fixed.
Do not change fixture data to make a failing scenario pass — change the agent prompts or
tool logic instead. Changing expected verdicts requires explicit user sign-off.

---

## Explicit out of scope
- Real EDI X12 parsing — fixtures only need to resemble real documents, not be valid EDI
- Any third-party integrations (NetSuite, Shopify, Amazon, etc.)
- Frontend or UI — CLI output and markdown evidence packets only
- Parallel/concurrent orchestration — mention as future work in README, do not build
- SKU-to-product-name mapping — SKUs stay opaque codes (e.g. "SKU-001") everywhere, no
  product master/catalog; mention as future work in README (display-only, cosmetic for
  dispute packets), do not build
- Heterogeneous mock data sources — fixtures stay plain JSON for all 7 scenarios; no
  relational DB, CSV/Excel, or other mixed backing stores behind `FixtureLoader`. The goal
  (recreating the messiness of real multi-system data landscapes, and testing that the
  MCP-tool abstraction hides the backing store from agents) is worth pursuing once the
  in-scope build (layers 1-9) is complete — mention as future work in README, do not build
  now
- Production concerns: auth, multi-tenancy, persistence beyond local files

---

## Tech stack
- Python 3.11+
- FastMCP (Python MCP SDK) for the MCP server
- OpenRouter (OpenAI-compatible chat completions API) via the `openai` Python SDK's
  `AsyncOpenAI`, pointed at `base_url="https://openrouter.ai/api/v1"` with
  `OPENROUTER_API_KEY` — NOT the Anthropic SDK directly, and NOT Anthropic's native
  Messages/tool_use format. Deliberate deviation from the original plan, approved 2026-07-18.
  - Investigator: Claude Haiku 4.5 (mechanical data-fetch + compare) — exact OpenRouter
    model slug to be confirmed against OpenRouter's model catalog at implementation time;
    do not hardcode a guessed slug.
  - Reviewer: Claude Sonnet 4.5 (subtle reasoning for trap detection) — same caveat.
- Fixtures as plain JSON, checked into repo
- `pytest` for unit tests; `rich` for CLI output
- Temperature 0 for both agents (deterministic, debuggable)

---

## Build order — do not skip ahead
1. `mcp_server/models.py` + `data/sku_uom_conversions.json`
2. All 7 scenario fixture JSON files (verified against Pydantic models)
3. `mcp_server/fixtures.py` + `mcp_server/tools/` + unit tests passing
4. `mcp_server/server.py` (wire FastMCP)
5. `agents/base.py` (shared tool loop)
6. `agents/investigator.py` + `agents/reviewer.py` (system prompts)
7. `orchestrator/pipeline.py` + `orchestrator/output.py`
8. `cli/run_claim.py` + `cli/run_all.py`
9. Integration tests + README

**Rule:** Do not start layer N+1 until layer N has passing tests. Check PROGRESS.md for
current state before starting any session.

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
