import pytest

from mcp_server.tools.document_tools import (
    get_asns_for_po,
    get_deduction_claim,
    get_invoice,
    get_po,
    get_receiving_record,
    get_trade_agreement,
    list_claims_for_po,
)


def test_s01_documents(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")

    po = get_po("PO-001")
    assert po.retailer == "walmart"
    assert po.sku == "SKU-001"

    invoice = get_invoice("PO-001")
    assert invoice.po_id == "PO-001"

    receiving = get_receiving_record("PO-001")
    assert receiving.po_id == "PO-001"

    asns = get_asns_for_po("PO-001")
    assert len(asns) == 1
    assert asns[0].po_id == "PO-001"


def test_s03_split_shipment_asns(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s03_split_shipment")

    asns = get_asns_for_po("PO-003")
    assert len(asns) == 2
    assert [asn.asn_id for asn in asns] == ["ASN-003A", "ASN-003B"]
    assert sum(asn.shipped_qty for asn in asns) == 720


def test_s06_trade_agreement_match(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s06_promo_billback")

    agreement = get_trade_agreement("safeway", "SKU-006", "PROMO-SPRING-2024")
    assert agreement is not None
    assert agreement.agreement_id == "TA-006"


def test_s06_trade_agreement_wrong_promo_returns_none(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s06_promo_billback")

    agreement = get_trade_agreement("safeway", "SKU-006", "PROMO-SUMMER-2024")
    assert agreement is None


def test_s07_list_claims_for_po(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s07_duplicate_claim")

    claim_ids = list_claims_for_po("PO-007")
    assert set(claim_ids) == {"CLM-007a", "CLM-007b"}


def test_s07_get_deduction_claim_resolves_correct_file(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s07_duplicate_claim")

    prior = get_deduction_claim("CLM-007a")
    assert "RESOLVED" in prior.retailer_notes

    current = get_deduction_claim("CLM-007b")
    assert "RESOLVED" not in current.retailer_notes


def test_po_id_mismatch_raises(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")

    with pytest.raises(ValueError):
        get_po("PO-999")
