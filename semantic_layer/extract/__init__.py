"""ETL Extract (Layer 25): manifest-driven dispatch to the per-format source parsers.

`extract_all(source_root)` reads `manifest.json`, runs each declared source through the parser for
its `format`, and tags every emitted record with the manifest's `source_file` + `target` (the
lineage seed). The returned list of RawRecords is the public API Layer 26 (Transform) consumes.
"""

import json
from pathlib import Path

from semantic_layer.extract import carrier_856, erp_csv, json_sources
from semantic_layer.extract.records import RawRecord

PARSERS = {
    "erp_csv": erp_csv.parse,
    "carrier_856": carrier_856.parse,
    "wms_json": json_sources.parse_wms_json,
    "tpm_json": json_sources.parse_tpm_json,
    "portal_json": json_sources.parse_portal_json,
}


def extract_all(source_root: Path) -> list[RawRecord]:
    manifest = json.loads((source_root / "manifest.json").read_text())
    records: list[RawRecord] = []
    for source in manifest["sources"]:
        parse = PARSERS[source["format"]]
        for record in parse(source_root / source["file"]):
            record.source_file = source["file"]
            record.target = source["target"]
            records.append(record)
    return records


__all__ = ["RawRecord", "PARSERS", "extract_all"]
