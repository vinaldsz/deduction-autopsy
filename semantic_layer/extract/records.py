"""The raw, lineage-tagged record every Extract parser emits.

Layer 25 (Extract) does *format parsing only*: it turns a source file into RawRecords that keep
source-vocabulary field names and raw string values (money still "$2.50", dates still
"01/10/2024", UOM still "EA", notes still whitespace-padded). Canonical mapping, coercion,
UOM-synonym folding, trimming, and Pydantic validation are all Layer 26 (Transform) — so nothing
here imports mcp_server.models.
"""

from dataclasses import dataclass, field


@dataclass
class RawRecord:
    source_file: str = ""            # manifest-relative path, e.g. "erp/purchase_orders.csv" (lineage)
    source_row_ref: str = ""         # ordinal within the file, e.g. "record 0" (lineage)
    target: str = ""                 # entity table from the manifest, e.g. "purchase_orders"
    fields: dict[str, str] = field(default_factory=dict)  # raw source field name -> raw string value
    error: str | None = None         # set when the unit is structurally malformed (fields -> {"_raw": ...})
