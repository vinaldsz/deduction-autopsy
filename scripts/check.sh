#!/usr/bin/env bash
#
# The project's verification gates, in one place. Run at every layer boundary before
# committing (see CLAUDE.md: "Commit only when the layer's tests pass").
#
#   scripts/check.sh          all gates
#   scripts/check.sh pytest   unit tests only
#   scripts/check.sh types    pyright only
#   scripts/check.sh js       frontend syntax + unit tests only
#
# .github/workflows/tests.yml calls this script rather than repeating the commands, so the
# gate definitions cannot drift between local runs and CI.
#
# NOT included: the integration suite. pyproject.toml's `addopts = "-m 'not integration'"`
# deselects it, and it hits the real OpenRouter API — it costs money, so it stays an
# explicit opt-in (`pytest tests/test_pipeline_scenarios.py -m integration`), never part of
# the reflex check. No DB build step either: tests/conftest.py's session fixture builds one
# into a temp dir.

set -uo pipefail

cd "$(dirname "$0")/.."

# Prefer ./.venv when it exists, else fall back to PATH. Locally .venv is there; CI's `test`
# job installs bare into the runner's Python, and its `types` job builds ./.venv so that
# pyproject.toml's [tool.pyright] venvPath resolves the same way it does here.
bin() {
  if [ -x ".venv/bin/$1" ]; then
    printf '%s' ".venv/bin/$1"
  else
    printf '%s' "$1"
  fi
}

FAILED=""

# Deliberately does not short-circuit: at a layer boundary you want every failure in one
# pass, not fix-rerun-fix. Hence no `set -e` above.
run_gate() {
  label=$1
  shift
  printf '\n\033[1m=== %s ===\033[0m\n' "$label"
  if ! "$@"; then
    FAILED="$FAILED
  - $label"
  fi
}

# `node --check <file>` is a NO-OP on both of these files and always exits 0: they use
# `export`, and when node 22's module-syntax detection retries a failed CommonJS parse as
# ESM it swallows the SyntaxError. Verified — appending `this is not valid ===` to lib.js
# still passed `node --check`, while `node --test` failed. Feeding the file on stdin with
# --input-type=module parses it as the ES module it actually is, and does fail.
# Errors are reported against `[stdin]`, so name the file ourselves.
# Globbed rather than a fixed list: the frontend is ~18 modules now, and a new one that nobody
# remembered to add here would be checked by nothing at all.
check_js_syntax() {
  syntax_status=0
  for f in ui/static/*.js; do
    if ! node --input-type=module --check <"$f"; then
      printf '  ^ syntax error in %s (reported above as [stdin])\n' "$f" >&2
      syntax_status=1
    fi
  done
  return $syntax_status
}

gate_pytest() {
  run_gate "pytest — unit tests" "$(bin pytest)" tests/ -q
}

gate_types() {
  run_gate "pyright — static types" "$(bin pyright)"
}

gate_js() {
  run_gate "node --check — frontend syntax" check_js_syntax
  # The glob stays quoted: `node --test tests/js/` module-resolves the bare directory and
  # fails, and bash without globstar won't expand `**` the way node's runner does.
  run_gate "node --test — frontend unit tests" node --test "tests/js/**/*.test.mjs"
}

# Validate the argument instead of ignoring it. Layer 8 shipped a CLI that silently swallowed
# unrecognized flags and started a real paid run instead of printing help (PROGRESS.md); an
# unknown gate name here should fail loudly, not quietly pass by running nothing.
case "${1:-all}" in
  all)
    gate_pytest
    gate_types
    gate_js
    ;;
  pytest) gate_pytest ;;
  types)  gate_types ;;
  js)     gate_js ;;
  -h | --help)
    cat <<'USAGE'
scripts/check.sh — the project's verification gates. Run before committing a layer.

  scripts/check.sh          all gates
  scripts/check.sh pytest   unit tests only
  scripts/check.sh types    pyright only
  scripts/check.sh js       frontend syntax + unit tests only

Excludes the integration suite (hits the real OpenRouter API, costs money):
  pytest tests/test_pipeline_scenarios.py -m integration -v
USAGE
    exit 0
    ;;
  *)
    printf 'check.sh: unknown gate %s (expected: pytest | types | js, or no argument for all)\n' "$1" >&2
    exit 2
    ;;
esac

if [ -n "$FAILED" ]; then
  printf '\n\033[1;31mFAILED:\033[0m%s\n' "$FAILED"
  exit 1
fi

printf '\n\033[1;32mAll gates passed.\033[0m\n'
