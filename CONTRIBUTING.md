# Contributing

This project is built **one layer at a time**, and the discipline is the point: a layer is a
scoped change with passing gates and one commit. If you are picking it up mid-build, start with
[`PROGRESS.md`](PROGRESS.md)'s `## Current layer`, then the layer's entry in
[`docs/PLAN.md`](docs/PLAN.md).

[`CLAUDE.md`](CLAUDE.md) is the authoritative rulebook (it is also the briefing given to Claude
Code). This file is the human-readable short version.

---

## Set up

```bash
uv venv
uv pip install -e ".[dev]"
cp .env.example .env          # then set OPENROUTER_API_KEY
python -m semantic_layer.etl  # build data/deductions.db
```

Python 3.11+ and Node 22+ (Node only for the frontend gates — there is no `package.json` and no
`node_modules`). An API key is needed to *run* the pipeline; unit tests don't need one.

---

## The loop

```bash
scripts/check.sh              # all four gates, before every commit
scripts/check.sh pytest       # or one at a time: pytest | types | js
```

`scripts/check.sh` is the **single definition** of every gate, and CI calls the same script — so
local and CI gates cannot drift. It deliberately does not short-circuit: at a layer boundary you
want every failure in one pass, not fix-rerun-fix.

| Gate | What it runs |
|---|---|
| `pytest` | `pytest tests/ -q` — mocks OpenRouter, runs the real MCP server in-process |
| `types` | `pyright` over the shipped code (`tests/` excluded on purpose) |
| `js` | Per-file ES-module syntax check, then `node --test "tests/js/**/*.test.mjs"` |

Not in the gates, because it costs real money:

```bash
pytest tests/test_pipeline_scenarios.py -m integration -v
```

**A green `scripts/check.sh` is not the end of a UI change.** `ui/static/`'s DOM+fetch modules
and the stylesheet are unreachable by every gate. That is measured, not theoretical: five layers
found bugs only by driving the running app, three in one layer. Budget for a live pass.

There is also a `/layer-done` command (`.claude/commands/layer-done.md`) that runs the gates and
then the checks a script can't do: scope diff against the layer's stated goal, the non-negotiables
below, and a smoke test against the **real** `data/deductions.db`. That last one matters because
`tests/conftest.py` builds its own DB in a temp dir, so a green suite says nothing about the real
store — which is exactly how one `init_db` bug got through.

---

## The rules that don't bend

Change any of these only with explicit sign-off, and say so in the commit.

1. **Two agents, always.** The Investigator and Reviewer stay separate agents with separate
   prompts. Collapsing them "for simplicity" removes the control the project exists to
   demonstrate.
2. **MCP tools are the only data path for agents.** No fixture paths, no SQL, no pre-loaded
   context. This is what makes the tool-call trace an audit trail. (The orchestrator and the UI
   read the DB directly — that constraint is about *agents*.)
3. **The 8 scenarios are ground truth.** A failing scenario means the prompt or the tool logic is
   wrong. **Do not edit fixture data to make a test pass.** Changing an expected verdict needs
   explicit user sign-off.
4. **Code decides what a verdict is *allowed* to be; agents decide what it *is*.** The
   orchestrator may only widen a verdict to `ESCALATE`, never narrow it to `VALID`/`INVALID`.
5. **Pure frontend logic goes in `ui/static/lib.js`.** That is the only frontend file a test can
   reach. DOM+fetch modules stay free of logic worth testing.
6. **Frontend modules may import only from a lower layer or their own**
   (`dom/state/stream/api → renderers → actions → app.js`).
   `tests/js/architecture.test.mjs` enforces it; a new module must declare its layer there.

---

## Where things go

| Change | Files |
|---|---|
| Business entity or field | `mcp_server/models.py` → `SCHEMA_SQL` in `mcp_server/db.py` → `semantic_layer/transform.py` |
| New source system | `semantic_layer/extract/<parser>.py` + `source_systems/manifest.json` |
| New MCP tool | `mcp_server/tools/` + register in `mcp_server/server.py` + **tell both prompts it exists** |
| Agent behaviour | `agents/investigator.py` / `agents/reviewer.py` (prompt text only — no logic) |
| Verdict rules, safeguards | `orchestrator/pipeline.py`, `orchestrator/completeness.py` |
| Worklist SQL, KPIs | `ui/queries.py` (all SQL lives here; whitelist new filters/sorts) |
| HTTP route | `ui/server.py` (thin: routing/SSE/CSV only) + `docs/API.md` |
| Frontend logic | `ui/static/lib.js` + a test in `tests/js/` |
| Frontend rendering | the matching renderer/action module in `ui/static/` |

There is **no migration framework**. Schema changes are idempotent `CREATE … IF NOT EXISTS` DDL
plus a hand-written ALTER-and-backfill — see `mcp_server/db.py::_add_snapshot_columns` for the
pattern, including how it degrades rather than asserting a fact that never happened. Existing
stores upgrade by re-running `python -m semantic_layer.etl`.

Deeper reasoning for all of the above: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Style

The codebase's convention, which reviews hold to:

- **Simplest thing that solves the stated problem.** No speculative abstraction, no
  configurability nobody asked for, no error handling for impossible states.
- **Surgical diffs.** Every changed line should trace to the request. Don't improve adjacent code,
  don't reformat, don't delete pre-existing dead code — mention it instead.
- **Comments explain *why*, and especially why-not.** This codebase's comments carry the rejected
  alternative (`# Binding None is not an option — SQLite raises "datatype mismatch"`). That is the
  house style; a comment restating the code is noise, a comment recording the trap is what keeps
  the next person from reintroducing it.
- **Tests pin decisions, not implementations.** When a behaviour is deliberate (`offset`/`limit`
  ignored rather than rejected on the CSV route), a test exists so it stays a decision.

---

## Commits

One commit per layer, only when the gates pass. Never commit broken code.

```
Layer N: <what was built>

- bullet summarizing key decisions or non-obvious choices
```

Branch per layer is recommended (`layer/37b-grid`); `main` receives a merge when the layer is
complete and green. Never commit `.env`, `outputs/`, or `data/deductions.db` (all gitignored).

At the end of a layer, update:

- **`PROGRESS.md`** — the narrative entry: what was decided, what broke, what was corrected.
- **`README.md`'s [Status table](README.md#status)** — the one layer index; `PROGRESS.md` points
  here rather than keeping a second copy.
- **`docs/PLAN.md`** — if the plan changed, and say why it changed.
- **`docs/SPEC.md`** — if a contract changed (schema, JSON shape, route).
- **`docs/API.md`** — if a route or MCP tool changed.

Push at the end of each session.
