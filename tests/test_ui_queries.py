"""Layer 30b: the dashboard read-queries (ui/queries.py) against a controlled temp DB.

Isolated from the shared session DB (which pipeline tests mutate) so counts are deterministic.
"""

from datetime import UTC, datetime

import pytest

from mcp_server.db import connect, init_db
from ui import queries


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "dash.db"
    init_db(path)
    now = datetime.now(UTC).isoformat()
    with connect(path) as conn:
        conn.execute("INSERT INTO batches (batch_id, load_date, status) VALUES ('LOT-2024-06-08','2024-06-08','complete')")
        conn.execute("INSERT INTO batches (batch_id, load_date, status) VALUES ('LOT-2024-09-15','2024-09-15','complete')")
        conn.execute("INSERT INTO purchase_orders (po_id) VALUES ('PO-T')")
        rows = [
            ("CLM-A", 20000, "2024-09-15"),  # HIGH (amount)
            ("CLM-B", 8000, "2024-09-15"),   # MEDIUM
            ("CLM-C", 1000, "2024-09-15"),   # LOW
            ("CLM-D", 1000, "2024-01-01"),   # HIGH (aged > 45d vs lot date)
            ("CLM-E", 20000, "2024-09-15"),  # resolved -> excluded from unresolved
        ]
        for cid, amt, cdate in rows:
            conn.execute(
                "INSERT INTO deduction_claims (claim_id, po_id, batch_id, retailer, claimed_reason, "
                "claimed_amount, claim_date) VALUES (?, 'PO-T', 'LOT-2024-09-15', 'walmart', "
                "'shortage', ?, ?)", (cid, amt, cdate))
        conn.execute(
            "INSERT INTO claim_resolutions (claim_id, final_verdict, resolved_at) "
            "VALUES ('CLM-E', 'INVALID', ?)", (now,))
    monkeypatch.setenv("DEDUCTIONS_DB", str(path))
    return path


def test_priority_thresholds():
    assert queries.priority(20000, "2024-09-15", "2024-09-15") == "HIGH"
    assert queries.priority(8000, "2024-09-15", "2024-09-15") == "MEDIUM"
    assert queries.priority(1000, "2024-09-15", "2024-09-15") == "LOW"
    assert queries.priority(1000, "2024-01-01", "2024-09-15") == "HIGH"  # aging


def test_active_batch_is_latest_load_date(db):
    assert queries.active_batch()["batch_id"] == "LOT-2024-09-15"


def test_batch_claims_paginates_with_priority_and_status(db):
    page = queries.batch_claims("LOT-2024-09-15", offset=0, limit=2)
    assert page["total"] == 5
    assert [c["claim_id"] for c in page["claims"]] == ["CLM-A", "CLM-B"]
    assert page["claims"][0]["priority"] == "HIGH"
    resolved = queries.batch_claims("LOT-2024-09-15", offset=4, limit=2)["claims"]
    assert resolved[0]["claim_id"] == "CLM-E" and resolved[0]["status"] == "INVALID"


def test_unresolved_claim_ids_caps_and_excludes_resolved(db):
    assert queries.unresolved_claim_ids("LOT-2024-09-15", cap=3) == ["CLM-A", "CLM-B", "CLM-C"]
    assert "CLM-E" not in queries.unresolved_claim_ids("LOT-2024-09-15", cap=99)


def test_dashboard_metrics(db):
    m = queries.dashboard_metrics()
    assert m["batch"] == {"batch_id": "LOT-2024-09-15", "status": "complete"}
    assert m["unresolved_count"] == 4
    assert m["dollars_at_risk_cents"] == 30000  # A+B+C+D, E resolved
    assert m["priority_breakdown"] == {"HIGH": 2, "MEDIUM": 1, "LOW": 1}
    assert m["resolved_this_month"] == 1
