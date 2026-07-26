"""Offline tests for the Layer 25 ETL Extract parsers (no LLM, no OpenRouter).

Extract is format-parsing only: it keeps source-vocabulary field names and RAW string values
(money "$2.50", dates "01/10/2024", UOM "EA", notes whitespace-padded) — no coercion/mapping
(that is Layer 26). Structurally-broken units are flagged via .error and parsing keeps going.
"""

import csv
import io
import json
from pathlib import Path

from semantic_layer.extract import carrier_856, erp_csv, extract_all, json_sources

SOURCE_ROOT = Path(__file__).parent.parent / "source_systems"


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return path


# --- ERP CSV -------------------------------------------------------------------------------------

def test_erp_csv_keeps_raw_source_fields(tmp_path):
    path = _write(tmp_path, "po.csv",
                  "PO_NUMBER,UNIT_PRICE,ORDER_DT\nPO-001,$2.50,01/10/2024\n")
    (rec,) = list(erp_csv.parse(path))
    assert rec.error is None
    assert rec.source_row_ref == "record 0"
    assert rec.fields == {"PO_NUMBER": "PO-001", "UNIT_PRICE": "$2.50", "ORDER_DT": "01/10/2024"}


def test_erp_csv_quoted_freetext_round_trips_byte_exact(tmp_path):
    note = 'refused, damaged; "BOL" exception'  # commas + embedded quotes = the injection surface
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows([["PO_NUMBER", "NOTES"], ["PO-001", note]])
    path = _write(tmp_path, "po.csv", buf.getvalue())
    (rec,) = list(erp_csv.parse(path))
    assert rec.fields["NOTES"] == note  # exact bytes preserved through RFC-4180 quoting


def test_erp_csv_column_count_mismatch_is_flagged_and_parsing_continues(tmp_path):
    path = _write(tmp_path, "po.csv",
                  "PO_NUMBER,RETAILER\nPO-001,walmart,EXTRA\nPO-002,target\n")
    bad, good = list(erp_csv.parse(path))
    assert bad.error == "column count mismatch"
    assert bad.fields["_raw"] == "PO-001,walmart,EXTRA"
    assert good.error is None and good.fields == {"PO_NUMBER": "PO-002", "RETAILER": "target"}


# --- carrier 856 ---------------------------------------------------------------------------------

def test_carrier_856_assembles_segments_into_one_record(tmp_path):
    path = _write(tmp_path, "asn.txt",
                  "ASN*ASN-001*PO-001\nITEM*SKU-001*120*EA\nSHIP*2024-01-12*XPO Logistics\n")
    (rec,) = list(carrier_856.parse(path))
    assert rec.error is None
    assert rec.fields == {"asn_id": "ASN-001", "ref_po": "PO-001", "sku": "SKU-001",
                          "ship_qty": "120", "uom": "EA", "ship_dt": "2024-01-12",
                          "carrier": "XPO Logistics"}


def test_carrier_856_split_shipment_yields_two_records_sharing_ref_po(tmp_path):
    path = _write(tmp_path, "asn.txt",
                  "ASN*ASN-003A*PO-003\nITEM*SKU-003*360*EA\nSHIP*2024-03-03*Schneider\n"
                  "ASN*ASN-003B*PO-003\nITEM*SKU-003*360*EA\nSHIP*2024-03-05*Schneider\n")
    a, b = list(carrier_856.parse(path))
    assert a.fields["asn_id"] == "ASN-003A" and b.fields["asn_id"] == "ASN-003B"
    assert a.fields["ref_po"] == b.fields["ref_po"] == "PO-003"


def test_carrier_856_orphan_and_short_segments_are_flagged(tmp_path):
    path = _write(tmp_path, "asn.txt",
                  "ITEM*SKU-001*120*EA\n"          # orphan: no preceding ASN
                  "ASN*ASN-001*PO-001\nITEM*SKU-001\n")  # short ITEM segment
    orphan, record = list(carrier_856.parse(path))
    assert orphan.error == "orphan segment (no preceding ASN)"
    assert record.fields["asn_id"] == "ASN-001"
    assert record.error and record.error.startswith("malformed ITEM segment")


# --- JSON sources --------------------------------------------------------------------------------

def test_wms_json_flattens_nested_keys_and_preserves_whitespace(tmp_path):
    path = _write(tmp_path, "wms.json", json.dumps(
        [{"receipt": {"id": "RCP-001", "qtyReceived": 108, "uom": "EA", "notes": "  short  "}}]))
    (rec,) = list(json_sources.parse_wms_json(path))
    assert rec.error is None
    assert rec.fields["receipt.id"] == "RCP-001"
    assert rec.fields["receipt.qtyReceived"] == "108"  # stringified, not coerced to int here
    assert rec.fields["receipt.notes"] == "  short  "  # whitespace preserved (trim is Layer 26)


def test_portal_json_yields_one_record_per_claim(tmp_path):
    path = _write(tmp_path, "claims.json", json.dumps(
        {"lot_date": "2024-09-15",
         "claims": [{"claimId": "CLM-001", "amount": "$30.00"}, {"claimId": "CLM-002", "amount": "115.00"}]}))
    recs = list(json_sources.parse_portal_json(path))
    assert [r.fields["claimId"] for r in recs] == ["CLM-001", "CLM-002"]
    assert recs[0].fields["amount"] == "$30.00"  # raw money string, uncoerced


def test_json_missing_wrapper_and_invalid_json_are_flagged(tmp_path):
    missing = _write(tmp_path, "wms.json", json.dumps([{"receipt": {"id": "ok"}}, {"nope": 1}]))
    good, bad = list(json_sources.parse_wms_json(missing))
    assert good.error is None
    assert bad.error == "missing 'receipt' wrapper"

    broken = _write(tmp_path, "bad.json", "{not valid json")
    (rec,) = list(json_sources.parse_tpm_json(broken))
    assert rec.error and rec.error.startswith("invalid JSON")
    assert rec.fields["_raw"] == "{not valid json"


def test_portal_missing_claims_array_is_flagged(tmp_path):
    path = _write(tmp_path, "claims.json", json.dumps({"lot_date": "2024-09-15"}))
    (rec,) = list(json_sources.parse_portal_json(path))
    assert rec.error == "missing 'claims' array"


# --- orchestrator against the committed source_systems/ ------------------------------------------

def test_extract_all_tags_every_record_with_manifest_target_and_lineage():
    records = extract_all(SOURCE_ROOT)
    assert len(records) == 44  # 8 PO + 8 inv + 9 ASN + 8 receiving + 1 TA + 10 claims

    manifest = json.loads((SOURCE_ROOT / "manifest.json").read_text())
    file_to_target = {s["file"]: s["target"] for s in manifest["sources"]}
    for rec in records:
        assert rec.error is None
        assert rec.source_file in file_to_target
        assert rec.target == file_to_target[rec.source_file]
        assert rec.source_row_ref  # lineage seed present

    counts: dict[str, int] = {}
    for rec in records:
        counts[rec.target] = counts.get(rec.target, 0) + 1
    assert counts == {"purchase_orders": 8, "invoices": 8, "asns": 9,
                      "receiving_records": 8, "trade_agreements": 1, "deduction_claims": 10}
