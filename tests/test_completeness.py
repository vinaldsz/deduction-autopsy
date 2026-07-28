"""Layer 31: per-claim investigation requirements + source-data gap detection.

Two halves, matching orchestrator/completeness.py's split:

- against the **real session DB** (all 52 claims), proving the requirements generalize and that no
  claim in the corpus has a gap — the latter is the insurance that wiring gap-forced ESCALATE cannot
  change any live verdict;
- against **controlled temp DBs**, since the real corpus is complete and therefore cannot exercise a
  single gap branch.
"""

import json
import sqlite3

import pytest

from agents.base import ToolCallRecord
from mcp_server.db import connect, init_db
from orchestrator.completeness import Requirement, data_gaps, required_tool_calls, unmet
from orchestrator.ground_truth import GROUND_TRUTH

FLOOR_TOOLS = {
    "get_deduction_claim",
    "get_po",
    "get_asns_for_po",
    "get_invoice",
    "get_receiving_record",
    "list_claims_for_po",
}


def _all_claim_ids() -> list[str]:
    from mcp_server.db import DEFAULT_DB_PATH
    import os

    path = os.environ.get("DEDUCTIONS_DB", str(DEFAULT_DB_PATH))
    with sqlite3.connect(path) as conn:
        return [r[0] for r in conn.execute("SELECT claim_id FROM deduction_claims ORDER BY claim_id")]


# --- against the real corpus ----------------------------------------------------------------------


@pytest.mark.parametrize("case", GROUND_TRUTH, ids=lambda c: c["scenario"])
def test_no_ground_truth_claim_has_a_data_gap(case):
    """The load-bearing guarantee: gap-forced ESCALATE is a no-op on all 8 ground-truth claims, so
    it cannot move a verdict the live suite asserts."""
    assert data_gaps(case["claim_id"]) == []


def test_no_claim_in_the_whole_corpus_has_a_data_gap():
    assert {cid: data_gaps(cid) for cid in _all_claim_ids() if data_gaps(cid)} == {}


def test_every_claim_requires_the_full_floor():
    """The point of the layer: coverage went from 5 hardcoded claims to all of them."""
    claim_ids = _all_claim_ids()
    assert len(claim_ids) > 8  # synthetics present, i.e. this is the real lot not a stub DB
    for claim_id in claim_ids:
        tools = {r.tool for r in required_tool_calls(claim_id) if not r.conditional}
        assert tools == FLOOR_TOOLS, claim_id


@pytest.mark.parametrize(
    "claim_id,tool",
    [
        ("CLM-002", "normalize_uom"),  # was REQUIRED_TOOL_CALLS["CLM-002"]
        ("CLM-006", "get_trade_agreement"),  # was REQUIRED_TOOL_CALLS["CLM-006"]
    ],
)
def test_derived_conditionals_reproduce_the_old_hardcoded_rules(claim_id, tool):
    assert tool in {r.tool for r in required_tool_calls(claim_id) if r.conditional}


def test_split_shipment_requires_both_asns():
    """Was REQUIRED_TOOL_CALLS["CLM-003"] (>=2 ASNs). PO-003 is the only 2-ASN PO in the store, so
    this is the one place a name-only floor entry would have silently weakened the gate."""
    asn_requirement = next(
        r for r in required_tool_calls("CLM-003") if r.tool == "get_asns_for_po"
    )
    assert asn_requirement.min_results == 2
    assert asn_requirement.args == {"po_id": "PO-003"}


@pytest.mark.parametrize("claim_id", ["CLM-001", "CLM-003"])
def test_uniform_uom_claim_does_not_require_normalize_uom(claim_id):
    assert "normalize_uom" not in {r.tool for r in required_tool_calls(claim_id)}


def test_non_promo_claim_does_not_require_a_trade_agreement():
    assert "get_trade_agreement" not in {r.tool for r in required_tool_calls("CLM-001")}


def test_unknown_claim_requires_nothing_and_is_reported_as_a_gap():
    assert required_tool_calls("CLM-NOPE") == []
    assert data_gaps("CLM-NOPE") == ["claim CLM-NOPE is not in the store"]


# --- gap branches, against controlled temp DBs ---------------------------------------------------


def _seed(path, *, asn=True, invoice=True, receiving=True, po=True):
    """A one-claim store, optionally with documents withheld."""
    init_db(path)
    with connect(path) as conn:
        conn.execute("INSERT INTO purchase_orders (po_id, ordered_uom) VALUES ('PO-T', 'EACH')")
        conn.execute(
            "INSERT INTO deduction_claims (claim_id, po_id, claimed_reason) "
            "VALUES ('CLM-T', 'PO-T', 'shortage')"
        )
        if asn:
            conn.execute("INSERT INTO asns (asn_id, po_id, shipped_uom) VALUES ('A-1','PO-T','EACH')")
        if invoice:
            conn.execute(
                "INSERT INTO invoices (invoice_id, po_id, invoiced_uom) VALUES ('I-1','PO-T','EACH')"
            )
        if receiving:
            conn.execute(
                "INSERT INTO receiving_records (receipt_id, po_id, received_uom) "
                "VALUES ('R-1','PO-T','EACH')"
            )
    if not po:
        # FK-off raw handle: connect() enforces foreign keys, so an orphan claim cannot be created
        # through it. The ETL's RI check quarantines orphans, making this state unreachable in
        # practice — covered anyway because the branch exists.
        with sqlite3.connect(path) as conn:
            conn.execute("DELETE FROM purchase_orders WHERE po_id = 'PO-T'")
    return path


@pytest.mark.parametrize(
    "withheld,expected",
    [
        ({"asn": False}, "no shipment notice (ASN) for PO-T"),
        ({"invoice": False}, "no invoice for PO-T"),
        ({"receiving": False}, "no receiving record for PO-T"),
        ({"po": False}, "no purchase order for PO-T"),
    ],
)
def test_each_withheld_document_is_reported_as_a_gap(tmp_path, withheld, expected):
    path = _seed(tmp_path / "gap.db", **withheld)
    assert data_gaps("CLM-T", db_path=path) == [expected]


def test_complete_claim_has_no_gaps(tmp_path):
    assert data_gaps("CLM-T", db_path=_seed(tmp_path / "ok.db")) == []


def test_absent_trade_agreement_is_not_a_gap(tmp_path):
    """A promo claim with no matching agreement is a legitimate dispute finding, not missing data —
    it is exactly how CLM-006 reads. Escalating on it would destroy the s06 verdict."""
    path = tmp_path / "promo.db"
    _seed(path)
    with connect(path) as conn:
        conn.execute("UPDATE deduction_claims SET claimed_reason = 'promo_billback'")
    assert data_gaps("CLM-T", db_path=path) == []
    assert "get_trade_agreement" in {r.tool for r in required_tool_calls("CLM-T", db_path=path)}


def test_mixed_uom_in_the_store_requires_normalization(tmp_path):
    path = tmp_path / "uom.db"
    _seed(path)
    with connect(path) as conn:
        conn.execute("UPDATE purchase_orders SET ordered_uom = 'CASE'")
    requirement = next(
        r for r in required_tool_calls("CLM-T", db_path=path) if r.tool == "normalize_uom"
    )
    assert "CASE" in requirement.reason and "EACH" in requirement.reason


# --- unmet() -------------------------------------------------------------------------------------


def _record(name, args=None, result="{}", is_error=False):
    return ToolCallRecord(name=name, args=args or {}, result=result, is_error=is_error)


def _floor_trace(po_id="PO-T", claim_id="CLM-T", asns=1, prior_claims=1):
    return [
        _record("get_deduction_claim", {"claim_id": claim_id}),
        _record("get_po", {"po_id": po_id}),
        _record("get_asns_for_po", {"po_id": po_id}, json.dumps([{}] * asns)),
        _record("get_invoice", {"po_id": po_id}),
        _record("get_receiving_record", {"po_id": po_id}),
        _record("list_claims_for_po", {"po_id": po_id}, json.dumps(["CLM-T"] * prior_claims)),
    ]


def test_complete_trace_leaves_nothing_unmet(tmp_path):
    reqs = required_tool_calls("CLM-T", db_path=_seed(tmp_path / "u.db"))
    assert unmet(reqs, _floor_trace()) == []


def test_absent_call_is_unmet(tmp_path):
    reqs = required_tool_calls("CLM-T", db_path=_seed(tmp_path / "u.db"))
    trace = [r for r in _floor_trace() if r.name != "get_invoice"]
    assert unmet(reqs, trace) == ["get_invoice for PO-T"]


def test_errored_call_does_not_satisfy_a_requirement(tmp_path):
    reqs = required_tool_calls("CLM-T", db_path=_seed(tmp_path / "u.db"))
    trace = _floor_trace()
    trace[3] = _record("get_invoice", {"po_id": "PO-T"}, "ERROR: not found", is_error=True)
    assert unmet(reqs, trace) == ["get_invoice for PO-T"]


def test_call_against_the_wrong_po_does_not_satisfy_a_requirement(tmp_path):
    """The claim_id-for-po_id slip both prompts guard against: the call happened, but not for the
    PO under investigation, so it proves nothing."""
    reqs = required_tool_calls("CLM-T", db_path=_seed(tmp_path / "u.db"))
    trace = _floor_trace(po_id="CLM-T")
    assert sorted(unmet(reqs, trace)) == sorted(
        [
            "get_po for PO-T",
            "get_asns_for_po for PO-T (expected 1 ASN(s))",
            "get_invoice for PO-T",
            "get_receiving_record for PO-T",
            "list_claims_for_po for PO-T",
        ]
    )


def test_under_counted_asns_are_unmet(tmp_path):
    """The s03 guard: get_asns_for_po returns [] instead of raising, so an empty/short result must
    fail the requirement rather than pass as a non-error call."""
    path = tmp_path / "split.db"
    _seed(path)
    with connect(path) as conn:
        conn.execute("INSERT INTO asns (asn_id, po_id, shipped_uom) VALUES ('A-2','PO-T','EACH')")
    reqs = required_tool_calls("CLM-T", db_path=path)
    assert unmet(reqs, _floor_trace(asns=1)) == ["get_asns_for_po for PO-T (expected 2 ASN(s))"]
    assert unmet(reqs, _floor_trace(asns=2)) == []


def test_empty_prior_claim_list_is_unmet(tmp_path):
    """list_claims_for_po always returns >= 1 for a real PO (the claim itself), so [] means the
    agent queried something else."""
    reqs = required_tool_calls("CLM-T", db_path=_seed(tmp_path / "u.db"))
    assert unmet(reqs, _floor_trace(prior_claims=0)) == ["list_claims_for_po for PO-T"]


def test_floor_short_circuits_conditionals(tmp_path):
    """An unmet floor is reported alone: conditionals derived from documents the agent never
    fetched are noise, and 'finish collecting first' is the actionable correction."""
    path = tmp_path / "sc.db"
    _seed(path)
    with connect(path) as conn:
        conn.execute("UPDATE purchase_orders SET ordered_uom = 'CASE'")
    reqs = required_tool_calls("CLM-T", db_path=path)
    trace = [r for r in _floor_trace() if r.name != "get_po"]
    assert unmet(reqs, trace) == ["get_po for PO-T"]
    # floor satisfied -> the conditional surfaces
    assert unmet(reqs, _floor_trace()) == ["normalize_uom (documents mix CASE/EACH)"]


def test_conditional_is_satisfied_by_a_matching_call(tmp_path):
    path = tmp_path / "cond.db"
    _seed(path)
    with connect(path) as conn:
        conn.execute("UPDATE purchase_orders SET ordered_uom = 'CASE'")
    reqs = required_tool_calls("CLM-T", db_path=path)
    assert unmet(reqs, [*_floor_trace(), _record("normalize_uom", result="24")]) == []


def test_requirement_with_no_args_ignores_call_arguments():
    requirement = Requirement("normalize_uom", "normalize_uom", conditional=True)
    assert unmet([requirement], [_record("normalize_uom", {"anything": "goes"})]) == []
