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
    po = FixtureLoader().get_po()
    if po.po_id != po_id:
        raise ValueError(f"po_id {po_id!r} not found; active scenario has {po.po_id!r}")
    return po


def get_asns_for_po(po_id: str) -> list[ASN]:
    get_po(po_id)
    return FixtureLoader().get_asns()


def get_invoice(po_id: str) -> Invoice:
    get_po(po_id)
    return FixtureLoader().get_invoice()


def get_receiving_record(po_id: str) -> ReceivingRecord:
    get_po(po_id)
    return FixtureLoader().get_receiving_record()


def get_trade_agreement(retailer: str, sku: str, promo_code: str) -> TradeAgreement | None:
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
    for claim in FixtureLoader().get_claims():
        if claim.claim_id == claim_id:
            return claim
    raise ValueError(f"claim_id {claim_id!r} not found in active scenario")


def list_claims_for_po(po_id: str) -> list[str]:
    return [claim.claim_id for claim in FixtureLoader().get_claims() if claim.po_id == po_id]
