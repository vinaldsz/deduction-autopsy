Verify a layer is actually finished before it gets committed. Work the steps in order and
stop where a step says to stop.

Two different failure modes are in scope, and they need different treatment: **new bugs**
(mechanical — steps 1 and 5 catch these) and **scope or design drift** (judgment — steps 2,
3 and 4). Do not let a green step 1 stand in for the rest.

If the user named a layer (`/layer-done 35`), use it. Otherwise infer it from the diff and
`PROGRESS.md`'s `## Current layer`, and say which layer you concluded it is.

---

## 1. Mechanical gates

Run `scripts/check.sh`. It runs pytest, pyright, `node --check`, and the frontend unit
tests, reporting every failure rather than stopping at the first.

**If anything fails: report the failures and stop.** Do not review scope, smoke-test, or
draft a commit against broken code. Offer to fix.

## 2. Scope diff

Read the whole diff (`git diff` plus `git diff --cached`, and `git status` for untracked
files). For every changed hunk, name which part of the layer's goal it serves.

CLAUDE.md's rule is *every changed line should trace directly to the user's request*. Call
out, explicitly:

- Changes to files the layer had no reason to touch.
- Adjacent "improvements" — reformatting, renamed locals, tidied comments, refactors of
  code that wasn't broken.
- New abstractions, config knobs, or error handling for cases that cannot occur.
- Pre-existing dead code the diff deletes. Mention it; it should be a separate decision.

Report anything that doesn't trace, and ask whether to revert it. Do not silently keep it.

## 3. Non-negotiables

Check the diff against CLAUDE.md's "Non-negotiable design decisions" and "Safeguards — do
not remove these". These are the ones a layer realistically breaks:

- **Two agents.** `agents/investigator.py` and `agents/reviewer.py` stay separate, with
  separate system prompts.
- **Agents reach data only through injected tool callables.** Asserted by
  [tests/test_invariants.py](tests/test_invariants.py) — note the scope: `ui/queries.py`
  and the orchestrator read the DB directly *by design*, only `agents/` is constrained.
- **The 8 ground-truth verdicts in `docs/SPEC.md` are fixed.** A failing scenario is fixed
  by changing prompts or tool logic, never by editing fixture data. If the diff touches
  `scenarios/*/*.json`, `source_systems/`, or the expected verdicts, **say so plainly and
  require explicit sign-off before continuing** — CLAUDE.md demands it.
- **ETL fidelity oracle.** `test_db_row_equals_frozen_scenario` in
  [tests/test_etl.py:81](tests/test_etl.py#L81) — runs inside step 1, but confirm it ran
  rather than assuming, if the diff touched `semantic_layer/` or `mcp_server/db.py`.
- **Stripped-reasoning handoff and XML-delimited CaseFile.** Guarded by
  `test_reviewer_receives_case_file_without_reasoning` and
  `test_strip_reasoning_drops_only_the_reasoning_field` in
  [tests/test_orchestrator_pipeline.py:360-391](tests/test_orchestrator_pipeline.py#L360-L391),
  and by [tests/test_prompt_injection.py](tests/test_prompt_injection.py). If the diff
  *deletes or weakens* one of those tests, that is a finding, not a cleanup.

## 4. Smoke-test the real thing

The gates in step 1 cannot catch this project's most common class of bug, and the record is
unambiguous — three of the four bugs written up in `PROGRESS.md` were found by running the
app, not by tests or review:

- `cli/run_all.py` parsed no argv, so `--help` started a real paid run.
- `_extract_json` only stripped a fence at the start of the response; the live model put it
  in the middle.
- Layer 34's schema shim never reached the real DB because `ui/server.py` doesn't call
  `init_db` — invisible to the suite, because
  [tests/conftest.py](tests/conftest.py) builds its own DB in a temp dir.

That last one is the pattern to watch: **the suite never touches
`data/deductions.db`, so nothing in step 1 says the real store still works.**

Pick the smoke test from what the layer touched:

- **UI, `ui/queries.py`, or `mcp_server/db.py`** → boot `uvicorn ui.server:app` against the
  real `data/deductions.db`, load the worklist and one claim detail, and watch the server
  log for a 500 or a `no such column`. The `/run` skill covers launching it.
- **A CLI entry point** → run `--help` *and* one real invocation. Check `--help` exits
  without doing work.
- **Schema or migration** → confirm the migration path on a **copy** of the real DB, and
  confirm existing `claim_resolutions` / `claim_dispositions` rows survive. Never
  `rm data/deductions.db` — it holds real analyst decisions and real LLM spend.
- **ETL** → `python -m semantic_layer.etl` and check the DQ report and quarantine counts.

Keep it read-only against the real store unless the layer is specifically about writes. If
a layer touched none of these (pure test or docs change), say you're skipping this step and
why.

## 5. Integration suite — conditional, costs money

Warranted when the diff touches `agents/`, `orchestrator/pipeline.py`, `mcp_server/tools/`,
or any system prompt, because those are what the 8 ground-truth verdicts actually exercise:

```
pytest tests/test_pipeline_scenarios.py -m integration -v
```

This hits the real OpenRouter API and costs money. **State that it's warranted and ask
before running it. Never run it unprompted.** If the layer doesn't touch those paths, say
it isn't warranted and move on.

## 6. Draft the PROGRESS.md update

Do not write it yet — draft it and show the user.

The file's convention: `## Current layer` sits at line 3 and gets demoted to
`## Previous layer` when a new one lands. So: rename the existing `## Current layer`
heading, and insert the new block above it.

Match the existing entries' style — prose paragraphs with bold lead-ins, naming the specific
files and the reasoning, not a bullet list of changes. Include, if any came up: what the
smoke test found, anything corrected mid-build (the existing entries are candid about plans
that turned out wrong — keep that), and the gate results.

Also flag, without fixing: `## Layer status` and `## Tests passing` near
[PROGRESS.md:2065](PROGRESS.md#L2065) are stale — the table stops at Layer 13 and the count
says 146 tests. Pre-existing, so it's a separate decision, not this layer's cleanup.

## 7. Draft the commit message

CLAUDE.md's format:

```
Layer N: <what was built>

- bullet summarizing key decisions or non-obvious choices
```

Show it and let the user approve. Don't commit unless they ask. If the branch is `main`,
mention that CLAUDE.md suggests `layer/N-<slug>`.

---

Finish with a short verdict: **ready to commit**, or **not ready** with the specific
blockers listed.
