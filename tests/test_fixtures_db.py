"""Direct unit coverage of the DB-backed FixtureLoader (Layer 28), the seam behind the MCP tools.

Runs against the session DB built by conftest (DEDUCTIONS_DB). Previously FixtureLoader had no
direct tests — its behavior was only exercised through the document tools.
"""

from mcp_server.fixtures import FixtureLoader


def test_get_po_returns_model_or_none():
    loader = FixtureLoader()
    po = loader.get_po("PO-001")
    assert po is not None and po.retailer == "walmart" and po.sku == "SKU-001"
    assert loader.get_po("PO-999") is None


def test_get_invoice_and_receiving_by_po():
    loader = FixtureLoader()
    assert loader.get_invoice("PO-002").invoice_id == "INV-002"
    rec = loader.get_receiving_record("PO-001")
    assert rec.receipt_id == "RCP-001"
    assert rec.notes == rec.notes.strip()  # loaded trimmed (no source whitespace padding)


def test_get_asns_returns_split_pair_in_order():
    asns = FixtureLoader().get_asns("PO-003")
    assert [a.asn_id for a in asns] == ["ASN-003A", "ASN-003B"]
    assert sum(a.shipped_qty for a in asns) == 720
    assert FixtureLoader().get_asns("PO-001")[0].asn_id == "ASN-001"


def test_get_claim_and_claims_for_po_are_global():
    loader = FixtureLoader()
    assert loader.get_claim("CLM-007a").po_id == "PO-007"
    assert loader.get_claim("CLM-404") is None
    # both the prior and current claim for the PO are visible (global duplicate detection)
    claim_ids = {c.claim_id for c in loader.get_claims_for_po("PO-007")}
    assert claim_ids == {"CLM-007a", "CLM-007b"}


def test_get_trade_agreement_matches_only_on_full_key():
    loader = FixtureLoader()
    hit = loader.get_trade_agreement("safeway", "SKU-006", "PROMO-SPRING-2024")
    assert hit is not None and hit.agreement_id == "TA-006"
    assert loader.get_trade_agreement("safeway", "SKU-006", "PROMO-SUMMER-2024") is None
    assert loader.get_trade_agreement("walmart", "SKU-006", "PROMO-SPRING-2024") is None
