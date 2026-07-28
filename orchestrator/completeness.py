"""Per-claim investigation requirements and source-data gap detection (Layer 31).

Replaces the claim-keyed `REQUIRED_TOOL_CALLS` answer key that covered 5 of 52 claims. Both
functions here are **computed from the store**, so they cover every claim uniformly and encode no
knowledge of which discrepancy a given claim happens to contain.

Two distinct questions, deliberately separated:

- `required_tool_calls` — *did the agent do the work?* A diligence check. Unmet requirements mean
  the Investigator skipped a step, which is recoverable: the pipeline sends a correction and retries.
- `data_gaps` — *is the data even there?* An absence check. Gaps are not the agent's fault and no
  amount of retrying fixes them, so they deterministically force ESCALATE.

Why gaps come from the DB and not from the tool-call trace: only `get_po`, `get_invoice`,
`get_receiving_record` and `get_deduction_claim` can raise. `get_asns_for_po` and
`list_claims_for_po` return `[]` and `get_trade_agreement` returns `None`, so a trace-derived check
structurally cannot see a missing ASN. Worse, with a complete corpus an `is_error` record almost
always means the agent passed a bad id (the claim_id-vs-po_id slip both prompts are hardened
against) — escalating on that would turn a recoverable typo into a wrong verdict. `is_error` is
therefore treated as evidence a requirement is *unsatisfied*, never as evidence data is missing.

Reading the DB from the orchestrator is already established (`orchestrator/resolutions.py`);
CLAUDE.md's "MCP server is the only data access path" constrains *agents*, which still see only
tools. This overlaps slightly with `ui/queries.py::claim_documents`, which assembles the same graph
for display; kept separate rather than imported because `ui` depends on `orchestrator`, not the
reverse.
"""

import json
import os
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp_server.db import DEFAULT_DB_PATH, connect


@dataclass(frozen=True)
class Requirement:
    """One tool call the Investigator must have made for a claim to count as investigated.

    `args` pins the call to the authoritative identifier from the store, so a call made with the
    wrong id does not satisfy the requirement. `min_results` guards the list-returning tools, which
    signal "nothing found" with `[]` rather than an error.
    """

    tool: str
    reason: str
    args: dict[str, str] = field(default_factory=dict)
    min_results: int = 0
    conditional: bool = False


def _db_path(db_path: str | Path | None) -> str:
    return str(db_path or os.environ.get("DEDUCTIONS_DB", str(DEFAULT_DB_PATH)))


def data_gaps(claim_id: str, db_path: str | Path | None = None) -> list[str]:
    """Source documents that are genuinely absent for `claim_id`, as human-readable strings.

    A missing trade agreement or absent prior claim is NOT a gap — both are legitimate findings the
    agents are meant to reason about (a promo claim with no matching agreement is exactly how a
    billback dispute looks). Only the documents every claim must have are checked.

    Returns `[]` for a fully-documented claim, which is every claim in the current corpus.
    """
    path = _db_path(db_path)
    with closing(connect(path)) as conn:
        row = conn.execute(
            "SELECT po_id FROM deduction_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        if row is None:
            return [f"claim {claim_id} is not in the store"]
        po_id = row[0]

        def count(table: str) -> int:
            return conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE po_id = ?", (po_id,)
            ).fetchone()[0]

        gaps = []
        if count("purchase_orders") == 0:
            gaps.append(f"no purchase order for {po_id}")
        if count("asns") == 0:
            gaps.append(f"no shipment notice (ASN) for {po_id}")
        if count("invoices") == 0:
            gaps.append(f"no invoice for {po_id}")
        if count("receiving_records") == 0:
            gaps.append(f"no receiving record for {po_id}")
    return gaps


def required_tool_calls(claim_id: str, db_path: str | Path | None = None) -> list[Requirement]:
    """The tool calls a complete investigation of `claim_id` must contain.

    A universal floor (the documents every reconciliation needs), plus conditionals derived from what
    the store actually holds:

    - more than one distinct UOM across the PO/ASN/invoice/receiving quantities -> `normalize_uom`
      must have run, because diffing raw quantities across mixed units is meaningless;
    - a `promo_billback` claim -> `get_trade_agreement` must have been consulted.

    These reproduce the two per-claim rules the old `REQUIRED_TOOL_CALLS` hardcoded for CLM-002 and
    CLM-006, and extend them to every claim that shares those data shapes. Returns `[]` for an
    unknown claim — there is nothing to require, and `data_gaps` is what reports it.
    """
    path = _db_path(db_path)
    with closing(connect(path)) as conn:
        claim = conn.execute(
            "SELECT po_id, claimed_reason FROM deduction_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        if claim is None:
            return []
        po_id, claimed_reason = claim

        def scalars(sql: str) -> list[str]:
            return [r[0] for r in conn.execute(sql, (po_id,)).fetchall() if r[0] is not None]

        asn_uoms = scalars("SELECT shipped_uom FROM asns WHERE po_id = ?")
        uoms = set(asn_uoms)
        uoms.update(scalars("SELECT ordered_uom FROM purchase_orders WHERE po_id = ?"))
        uoms.update(scalars("SELECT invoiced_uom FROM invoices WHERE po_id = ?"))
        uoms.update(scalars("SELECT received_uom FROM receiving_records WHERE po_id = ?"))
        asn_count = len(asn_uoms)

    requirements = [
        Requirement(
            "get_deduction_claim",
            f"get_deduction_claim for {claim_id}",
            args={"claim_id": claim_id},
        ),
        Requirement("get_po", f"get_po for {po_id}", args={"po_id": po_id}),
        # min_results is the store's real ASN count, so a split shipment cannot be under-counted.
        # This is what preserves the old CLM-003 rule: the tool returns [] rather than raising, so
        # requiring merely a non-error call would let an Investigator read "0 shipped" off a wrong
        # PO and call a split shipment a total shortage.
        Requirement(
            "get_asns_for_po",
            f"get_asns_for_po for {po_id} (expected {asn_count} ASN(s))",
            args={"po_id": po_id},
            min_results=asn_count,
        ),
        Requirement("get_invoice", f"get_invoice for {po_id}", args={"po_id": po_id}),
        Requirement(
            "get_receiving_record", f"get_receiving_record for {po_id}", args={"po_id": po_id}
        ),
        # Always >= 1: the claim under investigation is itself a row against this PO.
        Requirement(
            "list_claims_for_po",
            f"list_claims_for_po for {po_id}",
            args={"po_id": po_id},
            min_results=1,
        ),
    ]

    if len(uoms) > 1:
        requirements.append(
            Requirement(
                "normalize_uom",
                f"normalize_uom (documents mix {'/'.join(sorted(uoms))})",
                conditional=True,
            )
        )
    if claimed_reason == "promo_billback":
        # No args pinned: promo_code is not a DeductionClaim field (it lives in retailer_notes free
        # text, which is the injection surface), and a legitimate lookup may return None.
        requirements.append(
            Requirement(
                "get_trade_agreement",
                "get_trade_agreement (claim is a promo_billback)",
                conditional=True,
            )
        )
    return requirements


def _satisfies(requirement: Requirement, record: Any) -> bool:
    """Whether one ToolCallRecord satisfies `requirement` (duck-typed to avoid importing agents)."""
    if record.name != requirement.tool or record.is_error:
        return False
    for key, value in requirement.args.items():
        if str((record.args or {}).get(key)) != value:
            return False
    if requirement.min_results:
        try:
            payload = json.loads(record.result)
        except (json.JSONDecodeError, TypeError):
            return False
        if not isinstance(payload, list) or len(payload) < requirement.min_results:
            return False
    return True


def unmet(requirements: list[Requirement], trace: list[Any]) -> list[str]:
    """The `reason` of every requirement no call in `trace` satisfies, floor before conditionals.

    Floor failures short-circuit: a conditional derived from documents the agent never fetched would
    be noise in the correction message, and telling the agent everything at once is less actionable
    than telling it to finish collecting documents first.
    """

    def missing(subset: list[Requirement]) -> list[str]:
        return [
            requirement.reason
            for requirement in subset
            if not any(_satisfies(requirement, record) for record in trace)
        ]

    floor = missing([r for r in requirements if not r.conditional])
    return floor or missing([r for r in requirements if r.conditional])
