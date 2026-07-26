"""ERP CSV parser (POs, invoices) — format parsing only.

Uses the stdlib csv module (RFC-4180 quoting) so quoted free-text with commas/quotes round-trips
byte-exact. Values stay raw strings (money "$2.50" / "2.50", dates "MM/DD/YYYY" or ISO); Transform
(Layer 26) maps the aliased headers to canonical columns and coerces. A row whose column count
doesn't match the header is flagged (error) rather than dropped, and parsing continues.
"""

import csv
from collections.abc import Iterator
from pathlib import Path

from semantic_layer.extract.records import RawRecord


def parse(path: Path) -> Iterator[RawRecord]:
    with path.open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:
            return
        for i, row in enumerate(reader):
            if len(row) != len(header):
                yield RawRecord(
                    source_row_ref=f"record {i}",
                    fields={"_raw": ",".join(row)},
                    error="column count mismatch",
                )
                continue
            yield RawRecord(source_row_ref=f"record {i}", fields=dict(zip(header, row)))
