"""ETL orchestration (Layer 27): extract -> transform -> load into the relational store.

`build_db()` runs the whole pipeline end-to-end and is the single entry point the tests (fidelity
oracle) and the CLI use. Runnable as `python -m semantic_layer.etl` to (re)build `data/deductions.db`
from `source_systems/` and print a data-quality summary.
"""

import json
from pathlib import Path

from mcp_server.db import connect, init_db
from orchestrator.config import SETTINGS
from semantic_layer.dq_report import DQReport, build_dq_report, render_dq_report
from semantic_layer.extract import extract_all
from semantic_layer.load import load
from semantic_layer.transform import transform

DEFAULT_SOURCE_ROOT = Path(__file__).parent.parent / "source_systems"


def build_db(source_root: Path | str = DEFAULT_SOURCE_ROOT, db_path: Path | str | None = None) -> DQReport:
    source_root = Path(source_root)
    db_path = db_path or SETTINGS.deductions_db

    init_db(db_path)
    records = extract_all(source_root)
    result = transform(records)
    report = build_dq_report(records, result)
    manifest = json.loads((source_root / "manifest.json").read_text())

    conn = connect(db_path)
    try:
        with conn:  # one transaction: commits on success, rolls back on error
            load(conn, result, report, manifest)
    finally:
        conn.close()
    return report


def main() -> None:
    report = build_db()
    print(render_dq_report(report))
    print(f"\nBuilt {SETTINGS.deductions_db}: "
          f"{report.total_loaded} rows loaded, {report.total_rejected} rejected.")


if __name__ == "__main__":
    main()
