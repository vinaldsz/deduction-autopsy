from mcp_server.fixtures import FixtureLoader
from mcp_server.models import (
    ASN,
    DeductionClaim,
    Invoice,
    PurchaseOrder,
    ReceivingRecord,
    TradeAgreement,
)


def get_po(po_id: str) -> PurchaseOrder:
    """Look up the purchase order for a given PO ID."""
    po = FixtureLoader().get_po(po_id)
    if po is None:
        raise ValueError(f"po_id {po_id!r} not found")
    return po


def get_asns_for_po(po_id: str) -> list[ASN]:
    """Return all ASNs (shipment notices) for a PO, including split shipments across multiple ASN files."""
    return FixtureLoader().get_asns(po_id)


def get_invoice(po_id: str) -> Invoice:
    """Look up the invoice for a given PO ID."""
    invoice = FixtureLoader().get_invoice(po_id)
    if invoice is None:
        raise ValueError(f"invoice for po_id {po_id!r} not found")
    return invoice


def get_receiving_record(po_id: str) -> ReceivingRecord:
    """Look up the warehouse receiving record for a given PO ID."""
    record = FixtureLoader().get_receiving_record(po_id)
    if record is None:
        raise ValueError(f"receiving record for po_id {po_id!r} not found")
    return record


def get_trade_agreement(retailer: str, sku: str, promo_code: str) -> TradeAgreement | None:
    """Look up a trade agreement by retailer/SKU/promo code; returns None if no agreement matches."""
    return FixtureLoader().get_trade_agreement(retailer, sku, promo_code)


def get_deduction_claim(claim_id: str) -> DeductionClaim:
    """Look up a deduction claim by claim ID."""
    claim = FixtureLoader().get_claim(claim_id)
    if claim is None:
        raise ValueError(f"claim_id {claim_id!r} not found")
    return claim


def list_claims_for_po(po_id: str) -> list[str]:
    """List all deduction claim IDs filed against a PO, to detect duplicate/prior claims."""
    return [claim.claim_id for claim in FixtureLoader().get_claims_for_po(po_id)]
