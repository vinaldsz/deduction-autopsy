"""ETL Load (Layer 27): persist a TransformResult into the relational store.

Writes the validated entities from Transform into the Layer-23 SQLite schema via transactional
merge-upsert by PK (idempotent — re-running yields the same DB), records provenance in
`lineage`/`load_audit`, quarantines rejects into `reject_rows`, creates the daily-lot `batches`,
and seeds the prior-lot claim resolutions (007a/008a). Reading the store back is Layer 28's job.
"""

import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime

from semantic_layer.dq_report import DQReport
from semantic_layer.transform import TARGET_PK, TransformResult

# Parents before children (FK-safe); trade_agreements is standalone.
_LOAD_ORDER = [
    "purchase_orders", "trade_agreements", "asns", "invoices", "receiving_records", "deduction_claims",
]

# Prior claims resolved in earlier lots (credit memos issued per their fixture notes), pre-seeded so
# duplicate detection (list_claims_for_po) is authentic and the dashboard has history. Not
# pipeline-produced; INSERT OR IGNORE never clobbers a real Layer-29 resolution.
SEED_RESOLUTIONS = {
    "CLM-007a": {"investigator_verdict": "VALID", "final_verdict": "VALID", "confidence": 1.0,
                 "resolved_at": "2024-06-10", "run_id": "seed-earlier-lot"},
    "CLM-008a": {"investigator_verdict": "VALID", "final_verdict": "VALID", "confidence": 1.0,
                 "resolved_at": "2024-08-20", "run_id": "seed-earlier-lot"},
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _upsert(conn: sqlite3.Connection, table: str, pk: str, row: dict) -> None:
    cols = list(row)
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != pk)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT({pk}) DO UPDATE SET {updates}",
        [row[c] for c in cols],
    )


def load(conn: sqlite3.Connection, result: TransformResult, report: DQReport, manifest: dict) -> None:
    now = _now()
    lot_dates = sorted({s["lot_date"] for s in manifest["sources"] if s.get("lot_date")})
    current_batch = f"LOT-{lot_dates[-1]}"

    def batch_of(source_file: str) -> str:
        for source in manifest["sources"]:
            if source["file"] == source_file and source.get("lot_date"):
                return f"LOT-{source['lot_date']}"
        return current_batch  # shared reference sources load with today's lot

    batch_ids = {f"LOT-{d}" for d in lot_dates}
    reject_batches = {batch_of(r.source_file) for r in result.rejects}

    # 1. batches (gate: a batch with any quarantined row is complete_with_exceptions)
    for batch_id in sorted(batch_ids):
        status = "complete_with_exceptions" if batch_id in reject_batches else "complete"
        _upsert(conn, "batches", "batch_id", {
            "batch_id": batch_id, "load_date": batch_id.removeprefix("LOT-"),
            "status": status, "created_at": now,
        })

    # idempotency: the append-only metadata tables are refreshed per batch, not accumulated
    placeholders = ", ".join("?" for _ in batch_ids)
    for table in ("lineage", "load_audit", "reject_rows"):
        conn.execute(f"DELETE FROM {table} WHERE batch_id IN ({placeholders})", list(batch_ids))

    # 2. business entities (parents first) + 3. lineage
    by_target: dict[str, list] = defaultdict(list)
    for clean in result.clean:
        by_target[clean.target].append(clean)
    for target in _LOAD_ORDER:
        for clean in by_target.get(target, []):
            row = clean.model.model_dump()
            if target == "deduction_claims":
                row["batch_id"] = batch_of(clean.source_file)
            _upsert(conn, target, TARGET_PK[target], row)
            conn.execute(
                "INSERT INTO lineage (batch_id, entity_table, entity_pk, source_file, "
                "source_row_ref, loaded_at) VALUES (?, ?, ?, ?, ?, ?)",
                (batch_of(clean.source_file), target, clean.pk, clean.source_file,
                 clean.source_row_ref, now),
            )

    # 4. load_audit (per source file)
    for source_file, stats in report.per_source.items():
        conn.execute(
            "INSERT INTO load_audit (batch_id, source, rows_read, rows_loaded, rows_rejected, "
            "loaded_at) VALUES (?, ?, ?, ?, ?, ?)",
            (batch_of(source_file), source_file, stats.rows_read, stats.rows_loaded,
             stats.rows_rejected, now),
        )

    # 5. reject_rows (quarantine / dead-letter)
    for reject in result.rejects:
        conn.execute(
            "INSERT INTO reject_rows (batch_id, source, raw_row, reason, rejected_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (batch_of(reject.source_file), reject.source_file, json.dumps(reject.raw),
             reject.reason, now),
        )

    # 6. seed prior-lot resolutions (idempotent, non-clobbering); only when the claim was loaded
    for claim_id, seed in SEED_RESOLUTIONS.items():
        if conn.execute("SELECT 1 FROM deduction_claims WHERE claim_id = ?", (claim_id,)).fetchone():
            conn.execute(
                "INSERT OR IGNORE INTO claim_resolutions (claim_id, investigator_verdict, "
                "final_verdict, confidence, resolved_at, run_id) VALUES (?, ?, ?, ?, ?, ?)",
                (claim_id, seed["investigator_verdict"], seed["final_verdict"], seed["confidence"],
                 seed["resolved_at"], seed["run_id"]),
            )
