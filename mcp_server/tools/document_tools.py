from mcp_server.fixtures import FixtureLoader
from mcp_server.models import (
    ASN,
    DeductionClaim,
    Invoice,
    PurchaseOrder,
    ReceivingRecord,
    TradeAgreement,
)


def _validated_loader(po_id: str) -> tuple[FixtureLoader, PurchaseOrder]:
    """Return a FixtureLoader for the active scenario, after confirming its PO matches po_id."""
    loader = FixtureLoader()
    po = loader.get_po()
    if po.po_id != po_id:
        raise ValueError(f"po_id {po_id!r} not found; active scenario has {po.po_id!r}")
    return loader, po


def get_po(po_id: str) -> PurchaseOrder:
    """Look up the purchase order for a given PO ID."""
    _, po = _validated_loader(po_id)
    return po


def get_asns_for_po(po_id: str) -> list[ASN]:
    """Return all ASNs (shipment notices) for a PO, including split shipments across multiple ASN files."""
    loader, _ = _validated_loader(po_id)
    return loader.get_asns()


def get_invoice(po_id: str) -> Invoice:
    """Look up the invoice for a given PO ID."""
    loader, _ = _validated_loader(po_id)
    return loader.get_invoice()


def get_receiving_record(po_id: str) -> ReceivingRecord:
    """Look up the warehouse receiving record for a given PO ID."""
    loader, _ = _validated_loader(po_id)
    return loader.get_receiving_record()


def get_trade_agreement(retailer: str, sku: str, promo_code: str) -> TradeAgreement | None:
    """Look up a trade agreement by retailer/SKU/promo code; returns None if no agreement matches."""
    agreement = FixtureLoader().get_trade_agreement()
    if agreement is None:
        return None
    if (
        agreement.retailer != retailer
        or agreement.sku != sku
        or agreement.promo_code != promo_code
    ):
        return None
    return agreement


def get_deduction_claim(claim_id: str) -> DeductionClaim:
    """Look up a deduction claim by claim ID."""
    for claim in FixtureLoader().get_claims():
        if claim.claim_id == claim_id:
            return claim
    raise ValueError(f"claim_id {claim_id!r} not found in active scenario")


def list_claims_for_po(po_id: str) -> list[str]:
    """List all deduction claim IDs filed against a PO, to detect duplicate/prior claims."""
    return [claim.claim_id for claim in FixtureLoader().get_claims() if claim.po_id == po_id]
