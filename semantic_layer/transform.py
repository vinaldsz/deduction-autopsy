"""ETL Transform (Layer 26): raw source records -> validated canonical entities + quarantine.

Consumes the Layer-25 `RawRecord`s (source vocabulary, raw strings) and produces validated
`mcp_server.models` instances. It reverses the Layer-24 divergences (money -> int cents, dates ->
ISO, UOM synonyms -> canonical, whitespace-trim), maps aliased source fields to canonical columns,
Pydantic-validates, dedups/merge-conflict-checks by PK, and checks referential integrity. Anything
non-conforming becomes an in-memory `RejectRecord` (reason attached) — nothing is dropped silently.

Fidelity constraint (the Layer-27 oracle asserts DB == frozen scenarios/*.json): coercions are
exactly reversible and free text is **trim-only, never case-folded**, so carrier names, signatories,
and notes survive byte-exact. No DB writes, no batch_id, no lineage/reject_rows persistence — that is
the Layer-27 Load's job.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ValidationError

from mcp_server.models import (
    ASN,
    DeductionClaim,
    Invoice,
    PurchaseOrder,
    ReceivingRecord,
    TradeAgreement,
)
from semantic_layer.extract.records import RawRecord

_UOM_SYNONYMS = {
    "EA": "EACH", "CS": "CASE", "PLT": "PALLET",
    "EACH": "EACH", "CASE": "CASE", "PALLET": "PALLET",
}


# --- coercers (raise ValueError on non-conforming input) -----------------------------------------

def trim(value: str) -> str:
    return value.strip()


def to_int(value: str) -> int:
    return int(value.strip())


def to_cents(value: str) -> int:
    text = value.strip().lstrip("$").strip()
    try:
        return int((Decimal(text) * 100).to_integral_value())
    except InvalidOperation:
        raise ValueError(f"not a money value: {value!r}")


def to_iso_date(value: str) -> str:
    text = value.strip()
    fmt = "%m/%d/%Y" if "/" in text else "%Y-%m-%d"
    try:
        return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
    except ValueError:
        raise ValueError(f"not a date: {value!r}")


def to_uom(value: str) -> str:
    try:
        return _UOM_SYNONYMS[value.strip().upper()]
    except KeyError:
        raise ValueError(f"unknown UOM: {value!r}")


# --- target metadata: source field + coercer per canonical column --------------------------------

MAPPING: dict[str, dict[str, tuple[str, object]]] = {
    "purchase_orders": {
        "po_id": ("PO_NUMBER", trim), "retailer": ("RETAILER", trim), "sku": ("ITEM", trim),
        "ordered_qty": ("QTY", to_int), "ordered_uom": ("UOM", to_uom),
        "unit_price": ("UNIT_PRICE", to_cents), "order_date": ("ORDER_DT", to_iso_date),
    },
    "invoices": {
        "invoice_id": ("INVOICE_NO", trim), "po_id": ("PO_NUMBER", trim), "sku": ("ITEM", trim),
        "invoiced_qty": ("QTY", to_int), "invoiced_uom": ("UOM", to_uom),
        "amount": ("AMOUNT", to_cents), "invoice_date": ("INVOICE_DT", to_iso_date),
    },
    "asns": {
        "asn_id": ("asn_id", trim), "po_id": ("ref_po", trim), "sku": ("sku", trim),
        "shipped_qty": ("ship_qty", to_int), "shipped_uom": ("uom", to_uom),
        "ship_date": ("ship_dt", to_iso_date), "carrier": ("carrier", trim),
    },
    "receiving_records": {
        "receipt_id": ("receipt.id", trim), "po_id": ("receipt.po", trim), "sku": ("receipt.item", trim),
        "received_qty": ("receipt.qtyReceived", to_int), "received_uom": ("receipt.uom", to_uom),
        "receipt_date": ("receipt.date", to_iso_date), "lot_id": ("receipt.lot", trim),
        "notes": ("receipt.notes", trim),
    },
    "trade_agreements": {
        "agreement_id": ("agreement.id", trim), "retailer": ("agreement.retailer", trim),
        "sku": ("agreement.sku", trim), "promo_code": ("agreement.promoCode", trim),
        "discount_terms": ("agreement.terms", trim), "valid_from": ("agreement.validFrom", to_iso_date),
        "valid_to": ("agreement.validTo", to_iso_date), "signed_by": ("agreement.signedBy", trim),
    },
    "deduction_claims": {
        "claim_id": ("claimId", trim), "po_id": ("po", trim), "retailer": ("retailer", trim),
        "claimed_reason": ("reason", trim), "claimed_amount": ("amount", to_cents),
        "claim_date": ("claimDate", to_iso_date), "retailer_notes": ("notes", trim),
    },
}

MODELS: dict[str, type[BaseModel]] = {
    "purchase_orders": PurchaseOrder, "invoices": Invoice, "asns": ASN,
    "receiving_records": ReceivingRecord, "trade_agreements": TradeAgreement,
    "deduction_claims": DeductionClaim,
}

TARGET_PK = {
    "purchase_orders": "po_id", "invoices": "invoice_id", "asns": "asn_id",
    "receiving_records": "receipt_id", "trade_agreements": "agreement_id", "deduction_claims": "claim_id",
}

CHILD_TARGETS = {"asns", "invoices", "receiving_records", "deduction_claims"}  # carry a po_id FK


# --- result types --------------------------------------------------------------------------------

@dataclass
class CleanRecord:
    target: str
    model: BaseModel
    source_file: str
    source_row_ref: str
    raw: dict[str, str]  # original source fields, kept for a later reject (conflict/RI) + lineage

    @property
    def pk(self) -> str:
        return getattr(self.model, TARGET_PK[self.target])


@dataclass
class RejectRecord:
    target: str
    source_file: str
    source_row_ref: str
    raw: dict[str, str]
    reason: str


@dataclass
class TransformResult:
    clean: list[CleanRecord]
    rejects: list[RejectRecord]


# --- transform -----------------------------------------------------------------------------------

def _validation_reason(exc: ValidationError) -> str:
    err = exc.errors()[0]
    loc = ".".join(str(part) for part in err["loc"]) or "record"
    return f"invalid {loc}: {err['msg']}"


def _transform_one(rec: RawRecord) -> CleanRecord | RejectRecord:
    def reject(reason: str) -> RejectRecord:
        return RejectRecord(rec.target, rec.source_file, rec.source_row_ref, rec.fields, reason)

    if rec.error is not None:  # Extract already flagged this unit as structurally broken
        return reject(rec.error)
    mapping = MAPPING.get(rec.target)
    if mapping is None:
        return reject(f"unknown target: {rec.target}")

    canonical: dict[str, object] = {}
    for column, (source_field, coerce) in mapping.items():
        if source_field not in rec.fields:
            return reject(f"missing field {source_field}")
        try:
            canonical[column] = coerce(rec.fields[source_field])
        except ValueError as exc:
            return reject(f"bad {column}: {exc}")
    try:
        model = MODELS[rec.target].model_validate(canonical)
    except ValidationError as exc:
        return reject(_validation_reason(exc))
    return CleanRecord(rec.target, model, rec.source_file, rec.source_row_ref, rec.fields)


def _dedup(candidates: list[CleanRecord]) -> tuple[list[CleanRecord], list[RejectRecord]]:
    groups: dict[tuple[str, str], list[CleanRecord]] = defaultdict(list)
    for candidate in candidates:
        groups[(candidate.target, candidate.pk)].append(candidate)

    kept: list[CleanRecord] = []
    rejects: list[RejectRecord] = []
    for (_, pk), group in groups.items():
        if all(member.model == group[0].model for member in group):
            kept.append(group[0])  # identical duplicates collapse to one
        else:
            for member in group:  # same PK, disagreeing data -> hard reject the whole group
                rejects.append(RejectRecord(member.target, member.source_file, member.source_row_ref,
                                            member.raw, f"merge conflict on {pk}"))
    return kept, rejects


def _check_referential_integrity(
    records: list[CleanRecord],
) -> tuple[list[CleanRecord], list[RejectRecord]]:
    po_ids = {r.pk for r in records if r.target == "purchase_orders"}
    clean: list[CleanRecord] = []
    rejects: list[RejectRecord] = []
    for record in records:
        if record.target in CHILD_TARGETS and record.model.po_id not in po_ids:
            rejects.append(RejectRecord(record.target, record.source_file, record.source_row_ref,
                                        record.raw, f"orphan: no PO {record.model.po_id}"))
        else:
            clean.append(record)
    return clean, rejects


def transform(records: list[RawRecord]) -> TransformResult:
    candidates: list[CleanRecord] = []
    rejects: list[RejectRecord] = []
    for rec in records:
        outcome = _transform_one(rec)
        (candidates if isinstance(outcome, CleanRecord) else rejects).append(outcome)

    deduped, conflict_rejects = _dedup(candidates)
    clean, orphan_rejects = _check_referential_integrity(deduped)
    return TransformResult(clean=clean, rejects=rejects + conflict_rejects + orphan_rejects)
