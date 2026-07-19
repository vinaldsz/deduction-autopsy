from typing import Literal

from pydantic import BaseModel

UOM = Literal["EACH", "CASE", "PALLET"]
ClaimReason = Literal["shortage", "promo_billback", "compliance", "wrong_item"]


class PurchaseOrder(BaseModel):
    po_id: str
    retailer: str
    sku: str
    ordered_qty: int
    ordered_uom: UOM
    unit_price: int
    order_date: str


class ASN(BaseModel):
    asn_id: str
    po_id: str
    sku: str
    shipped_qty: int
    shipped_uom: UOM
    ship_date: str
    carrier: str


class Invoice(BaseModel):
    invoice_id: str
    po_id: str
    sku: str
    invoiced_qty: int
    invoiced_uom: UOM
    invoice_date: str
    amount: int


class ReceivingRecord(BaseModel):
    receipt_id: str
    po_id: str
    sku: str
    received_qty: int
    received_uom: UOM
    receipt_date: str
    lot_id: str
    notes: str


class TradeAgreement(BaseModel):
    agreement_id: str
    retailer: str
    sku: str
    promo_code: str
    discount_terms: str
    valid_from: str
    valid_to: str
    signed_by: str


class DeductionClaim(BaseModel):
    claim_id: str
    po_id: str
    retailer: str
    claimed_reason: ClaimReason
    claimed_amount: int
    claim_date: str
    retailer_notes: str
