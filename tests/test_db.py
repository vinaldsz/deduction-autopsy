"""Offline schema tests for mcp_server/db.py (no LLM, no OpenRouter).

Layer 23: proves the DDL creates a clean, empty DB matching the finalized schema, is
idempotent, and enforces foreign keys. This is the gate for Layers 24+.
"""

import sqlite3

import pytest

from mcp_server import db

EXPECTED_TABLES = {
    "purchase_orders",
    "asns",
    "invoices",
    "receiving_records",
    "trade_agreements",
    "deduction_claims",
    "batches",
    "claim_resolutions",
    "reject_rows",
    "load_audit",
    "lineage",
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {r[0] for r in rows}


def _views(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'view'"
    ).fetchall()
    return {r[0] for r in rows}


EXPECTED_INDEXES = {
    "idx_asns_po_id",
    "idx_invoices_po_id",
    "idx_receiving_records_po_id",
    "idx_deduction_claims_po_id",
    "idx_deduction_claims_batch_id",
    "idx_trade_agreements_lookup",
    "idx_reject_rows_batch_id",
    "idx_load_audit_batch_id",
    "idx_lineage_batch_id",
    "idx_lineage_entity",
}


def _named_indexes(conn: sqlite3.Connection) -> set[str]:
    # Exclude SQLite's auto-generated PK indexes (sqlite_autoindex_*).
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def test_init_db_creates_all_tables_and_view(tmp_path):
    db_path = tmp_path / "deductions.db"
    db.init_db(db_path)

    conn = db.connect(db_path)
    try:
        assert _tables(conn) == EXPECTED_TABLES
        assert len(EXPECTED_TABLES) == 11
        assert _views(conn) == {"v_batch_summary"}
        assert _named_indexes(conn) == EXPECTED_INDEXES
    finally:
        conn.close()


def test_init_db_creates_a_clean_empty_db(tmp_path):
    db_path = tmp_path / "deductions.db"
    db.init_db(db_path)

    conn = db.connect(db_path)
    try:
        for table in EXPECTED_TABLES:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count == 0, f"{table} should be empty in a fresh DB"
    finally:
        conn.close()


def test_init_db_is_idempotent(tmp_path):
    db_path = tmp_path / "deductions.db"
    db.init_db(db_path)
    db.init_db(db_path)  # second run must not error

    conn = db.connect(db_path)
    try:
        assert _tables(conn) == EXPECTED_TABLES
        assert _views(conn) == {"v_batch_summary"}
    finally:
        conn.close()


def test_connect_enables_foreign_keys(tmp_path):
    db_path = tmp_path / "deductions.db"
    db.init_db(db_path)

    conn = db.connect(db_path)
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_check_constraint_rejects_bad_claimed_reason(tmp_path):
    db_path = tmp_path / "deductions.db"
    db.init_db(db_path)

    conn = db.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO purchase_orders (po_id) VALUES ('PO-001')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO deduction_claims (claim_id, po_id, claimed_reason) "
                "VALUES ('CLM-001', 'PO-001', 'not_a_reason')"
            )
    finally:
        conn.close()


def test_foreign_key_rejects_orphan_child(tmp_path):
    db_path = tmp_path / "deductions.db"
    db.init_db(db_path)

    conn = db.connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO asns (asn_id, po_id) VALUES ('ASN-001', 'PO-MISSING')"
            )
    finally:
        conn.close()
