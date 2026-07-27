"""DB-backed document loader — the single data-access seam behind the MCP tools.

As of Layer 28 the MCP tools read the relational store (`data/deductions.db`) built by the ETL,
not per-scenario JSON. `FixtureLoader` keeps its name (it is the prompt-injection monkeypatch seam
in tests) but is now global and keyed by po_id/claim_id/etc. — it navigates the whole entity graph,
so there is no active "scenario". The DB path is read from `DEDUCTIONS_DB` at call time (falling
back to the default) so tests can point it at a temp DB.
"""

import os
from contextlib import closing
from pathlib import Path

from mcp_server.db import DEFAULT_DB_PATH, connect
from mcp_server.models import (
    ASN,
    DeductionClaim,
    Invoice,
    PurchaseOrder,
    ReceivingRecord,
    TradeAgreement,
)


class FixtureLoader:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = str(db_path or os.environ.get("DEDUCTIONS_DB", str(DEFAULT_DB_PATH)))

    def _one(self, conn, table, model, where_col, value):
        cols = list(model.model_fields)  # model has no batch_id, so it's excluded from the SELECT
        row = conn.execute(
            f"SELECT {', '.join(cols)} FROM {table} WHERE {where_col} = ?", (value,)
        ).fetchone()
        return model.model_validate(dict(zip(cols, row))) if row is not None else None

    def _many(self, conn, table, model, where_col, value, order_by):
        cols = list(model.model_fields)
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {table} WHERE {where_col} = ? ORDER BY {order_by}",
            (value,),
        ).fetchall()
        return [model.model_validate(dict(zip(cols, row))) for row in rows]

    def get_po(self, po_id: str) -> PurchaseOrder | None:
        with closing(connect(self.db_path)) as conn:
            return self._one(conn, "purchase_orders", PurchaseOrder, "po_id", po_id)

    def get_invoice(self, po_id: str) -> Invoice | None:
        with closing(connect(self.db_path)) as conn:
            return self._one(conn, "invoices", Invoice, "po_id", po_id)

    def get_receiving_record(self, po_id: str) -> ReceivingRecord | None:
        with closing(connect(self.db_path)) as conn:
            return self._one(conn, "receiving_records", ReceivingRecord, "po_id", po_id)

    def get_asns(self, po_id: str) -> list[ASN]:
        with closing(connect(self.db_path)) as conn:
            return self._many(conn, "asns", ASN, "po_id", po_id, order_by="asn_id")

    def get_claim(self, claim_id: str) -> DeductionClaim | None:
        with closing(connect(self.db_path)) as conn:
            return self._one(conn, "deduction_claims", DeductionClaim, "claim_id", claim_id)

    def get_claims_for_po(self, po_id: str) -> list[DeductionClaim]:
        with closing(connect(self.db_path)) as conn:
            return self._many(conn, "deduction_claims", DeductionClaim, "po_id", po_id,
                              order_by="claim_id")

    def get_trade_agreement(
        self, retailer: str, sku: str, promo_code: str
    ) -> TradeAgreement | None:
        cols = list(TradeAgreement.model_fields)
        with closing(connect(self.db_path)) as conn:
            row = conn.execute(
                f"SELECT {', '.join(cols)} FROM trade_agreements "
                "WHERE retailer = ? AND sku = ? AND promo_code = ?",
                (retailer, sku, promo_code),
            ).fetchone()
        return TradeAgreement.model_validate(dict(zip(cols, row))) if row is not None else None
