"""Offline tests for the Layer 26 ETL Transform + DQ report (no LLM, no OpenRouter).

Transform reverses the Layer-24 divergences ($->cents, dates->ISO, UOM fold, trim), maps source
vocabulary to canonical columns, Pydantic-validates, dedups/merge-conflict-checks, and checks
referential integrity; non-conforming rows become in-memory RejectRecords. Free text is trim-only
(fidelity-preserving), which the Layer-27 oracle depends on.
"""

from pathlib import Path

import pytest

from mcp_server.models import DeductionClaim, PurchaseOrder, ReceivingRecord
from semantic_layer.dq_report import build_dq_report, render_dq_report
from semantic_layer.extract import extract_all
from semantic_layer.extract.records import RawRecord
from semantic_layer.transform import (
    to_cents,
    to_int,
    to_iso_date,
    to_uom,
    transform,
)

SOURCE_ROOT = Path(__file__).parent.parent / "source_systems"
SCENARIOS = Path(__file__).parent.parent / "scenarios"


# --- coercers ------------------------------------------------------------------------------------

def test_to_cents_handles_dollar_and_bare_decimal():
    assert to_cents("$2.50") == 250
    assert to_cents("2.50") == 250
    assert to_cents("$300.00") == 30000
    with pytest.raises(ValueError):
        to_cents("not money")


def test_to_iso_date_normalizes_both_formats():
    assert to_iso_date("01/10/2024") == "2024-01-10"
    assert to_iso_date("2024-01-10") == "2024-01-10"
    with pytest.raises(ValueError):
        to_iso_date("13/40/2024")


def test_to_int_and_to_uom():
    assert to_int("120") == 120
    assert to_uom("EA") == "EACH" and to_uom("CS") == "CASE" and to_uom("PLT") == "PALLET"
    assert to_uom("EACH") == "EACH"  # canonical passes through
    with pytest.raises(ValueError):
        to_uom("BOX")


# --- happy path over the committed source_systems/ -----------------------------------------------

def test_transform_all_sources_clean():
    result = transform(extract_all(SOURCE_ROOT))
    assert result.rejects == []
    assert len(result.clean) == 254
    counts: dict[str, int] = {}
    for rec in result.clean:
        counts[rec.target] = counts.get(rec.target, 0) + 1
    assert counts == {"purchase_orders": 50, "invoices": 50, "asns": 51,
                      "receiving_records": 50, "trade_agreements": 1, "deduction_claims": 52}


def test_values_are_coerced_to_canonical_types():
    by_pk = {(c.target, c.pk): c.model for c in transform(extract_all(SOURCE_ROOT)).clean}
    po = by_pk[("purchase_orders", "PO-001")]
    assert po.unit_price == 250 and po.order_date == "2024-01-10" and po.ordered_uom == "EACH"
    claim = by_pk[("deduction_claims", "CLM-002")]
    assert claim.claimed_amount == 11500 and claim.claim_date == "2024-02-10"
    rcp = by_pk[("receiving_records", "RCP-001")]
    assert rcp.notes == rcp.notes.strip() and not rcp.notes.startswith(" ")  # whitespace trimmed


@pytest.mark.parametrize("scenario,filename,model,target,pk", [
    ("s01_clean_shortage", "po.json", PurchaseOrder, "purchase_orders", "PO-001"),
    ("s01_clean_shortage", "receiving_record.json", ReceivingRecord, "receiving_records", "RCP-001"),
    ("s06_promo_billback", "deduction_claim.json", DeductionClaim, "deduction_claims", "CLM-006"),
])
def test_transformed_entities_match_frozen_scenarios(scenario, filename, model, target, pk):
    """Transform-level preview of the Layer-27 fidelity oracle: DB == frozen JSON, field-for-field."""
    frozen = model.model_validate_json((SCENARIOS / scenario / filename).read_text())
    got = next(c.model for c in transform(extract_all(SOURCE_ROOT)).clean
               if c.target == target and c.pk == pk)
    assert got == frozen


# --- quarantine paths ----------------------------------------------------------------------------

def _po_fields(**overrides):
    fields = {"PO_NUMBER": "PO-900", "RETAILER": "walmart", "ITEM": "SKU-1", "QTY": "10",
              "UOM": "EACH", "UNIT_PRICE": "$1.00", "ORDER_DT": "2024-01-01"}
    fields.update(overrides)
    return fields


def _raw(target, fields, error=None, ref="record 0", source="x"):
    return RawRecord(source_file=source, source_row_ref=ref, target=target, fields=fields, error=error)


def test_extract_flagged_record_is_rejected_with_its_reason():
    result = transform([_raw("purchase_orders", {"_raw": "garbage"}, error="column count mismatch")])
    assert [r.reason for r in result.rejects] == ["column count mismatch"]
    assert result.clean == []


@pytest.mark.parametrize("overrides,needle", [
    ({"UNIT_PRICE": "abc"}, "bad unit_price"),
    ({"ORDER_DT": "31/31/2024"}, "bad order_date"),
    ({"UOM": "BOX"}, "bad ordered_uom"),
])
def test_bad_values_are_rejected(overrides, needle):
    result = transform([_raw("purchase_orders", _po_fields(**overrides))])
    assert result.clean == []
    assert needle in result.rejects[0].reason


def test_missing_source_field_is_rejected():
    fields = _po_fields()
    del fields["UNIT_PRICE"]
    result = transform([_raw("purchase_orders", fields)])
    assert result.rejects[0].reason == "missing field UNIT_PRICE"


def test_orphan_child_is_rejected_but_siblings_survive():
    po = _raw("purchase_orders", _po_fields(PO_NUMBER="PO-900"))
    good_asn = {"asn_id": "ASN-1", "ref_po": "PO-900", "sku": "SKU-1", "ship_qty": "10",
                "uom": "EA", "ship_dt": "2024-01-02", "carrier": "XPO"}
    orphan_asn = {**good_asn, "asn_id": "ASN-2", "ref_po": "PO-404"}
    result = transform([po, _raw("asns", good_asn), _raw("asns", orphan_asn)])
    assert {c.pk for c in result.clean} == {"PO-900", "ASN-1"}
    assert result.rejects[0].reason == "orphan: no PO PO-404"


def test_merge_conflict_rejects_group_but_identical_dupes_collapse():
    a = _raw("purchase_orders", _po_fields(PO_NUMBER="PO-900", RETAILER="walmart"))
    b = _raw("purchase_orders", _po_fields(PO_NUMBER="PO-900", RETAILER="target"))  # same PK, differs
    conflict = transform([a, b])
    assert conflict.clean == []
    assert all("merge conflict on PO-900" in r.reason for r in conflict.rejects)

    dup = _raw("purchase_orders", _po_fields(PO_NUMBER="PO-900"))
    dup2 = _raw("purchase_orders", _po_fields(PO_NUMBER="PO-900"))
    deduped = transform([dup, dup2])
    assert len(deduped.clean) == 1 and deduped.rejects == []


# --- DQ report -----------------------------------------------------------------------------------

def test_dq_report_reconciles_and_renders():
    records = extract_all(SOURCE_ROOT)
    result = transform(records)
    report = build_dq_report(records, result)
    assert report.total_read == 254 and report.total_loaded == 254 and report.total_rejected == 0
    for stats in report.per_source.values():
        assert stats.rows_read == stats.rows_loaded + stats.rows_rejected
    assert "Data Quality Report" in render_dq_report(report)


def test_dq_report_captures_reject_reasons():
    records = [_raw("purchase_orders", _po_fields(UOM="BOX"), source="erp/purchase_orders.csv")]
    report = build_dq_report(records, transform(records))
    stats = report.per_source["erp/purchase_orders.csv"]
    assert stats.rows_rejected == 1 and stats.rows_loaded == 0
    assert any("bad ordered_uom" in reason for reason in stats.reasons)
