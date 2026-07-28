"""Guard the one architectural invariant no other test asserts: `agents/` cannot reach data.

CLAUDE.md's "MCP server is the only data access path" is easy to read as a project-wide rule.
It isn't one, and asserting it that way would fail on correct code:

  - `ui/queries.py` imports `sqlite3` and connects to the store directly. By design — the
    dashboard is a read surface over the store, not an agent.
  - `orchestrator/{completeness,dispositions,resolutions}.py` read and write the DB directly.
    Also by design — the orchestrator is the harness that runs the investigation and records
    its outcome, not a party under review.

The actual control is segregation of duties on the *agents*: an agent's only route to a
document is a tool callable the pipeline injects, which is what makes the tool-call trace a
complete audit trail. If a module under `agents/` could open the DB or a fixture file, the
trace would stop accounting for everything the agent saw — silently, with every other test in
this suite still green. That is the failure this file exists to catch.

Scope note: this is a denylist of data-access routes, not an allowlist of permitted imports.
An allowlist would also catch novel routes (someone adding a dataframe library to read a
parquet), but it breaks on every legitimate new stdlib import and would be abandoned within a
few layers. The denylist encodes the control itself.
"""

import ast
from pathlib import Path

import pytest

AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"

# Import roots an agent module must never pull in, and why.
FORBIDDEN_IMPORT_ROOTS = {
    "sqlite3": "opens the relational store directly",
    "mcp_server": "reaches the MCP server's internals instead of calling injected tools",
    "semantic_layer": "reaches the ETL and its source files",
    "ui": "the dashboard's query layer is not an agent data path",
}

# Attribute calls that read a file without going through `open`.
FORBIDDEN_READ_ATTRS = {"read_text", "read_bytes"}


def _agent_modules() -> list[Path]:
    return sorted(p for p in AGENTS_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _imported_roots(tree: ast.Module) -> set[str]:
    """Top-level package name of every absolute import in the module."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is a relative import, which by definition stays inside agents/.
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_agents_dir_has_modules_to_check():
    """Fail loudly if the glob stops matching — otherwise every test below passes vacuously."""
    modules = _agent_modules()
    assert modules, f"no agent modules found under {AGENTS_DIR}"
    names = {p.name for p in modules}
    assert {"base.py", "investigator.py", "reviewer.py"} <= names, names


@pytest.mark.parametrize("module", _agent_modules(), ids=lambda p: p.name)
def test_agent_module_imports_no_data_access(module: Path):
    offenders = _imported_roots(_parse(module)) & FORBIDDEN_IMPORT_ROOTS.keys()
    assert not offenders, "\n".join(
        f"agents/{module.name} imports `{root}` — {FORBIDDEN_IMPORT_ROOTS[root]}. "
        "Agents receive data only through tool callables the pipeline injects; see this "
        "file's docstring."
        for root in sorted(offenders)
    )


@pytest.mark.parametrize("module", _agent_modules(), ids=lambda p: p.name)
def test_agent_module_reads_no_files(module: Path):
    for node in ast.walk(_parse(module)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if called == "open" or called in FORBIDDEN_READ_ATTRS:
            pytest.fail(
                f"agents/{module.name}:{node.lineno} calls `{called}(...)` — an agent must not "
                "read from the filesystem. Data reaches it only through injected tool "
                "callables; see this file's docstring."
            )
