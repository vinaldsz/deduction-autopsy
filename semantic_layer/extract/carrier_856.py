"""Carrier 856-ish flat-text parser (ASNs) — a small state machine.

Segments are `TAG*e1*e2[*e3]`:
    ASN*<asn_id>*<ref_po>          starts a record
    ITEM*<sku>*<ship_qty>*<uom>    fills it
    SHIP*<ship_dt>*<carrier>       fills it
The next `ASN*` (or EOF) flushes the current record, so a split shipment (two `ASN*` loops sharing
one `ref_po`) naturally yields two records. Field names use the SPEC carrier vocabulary; values stay
raw (UOM still EA/CS/PLT). Structurally-broken segments are flagged (error) and parsing continues:
a segment with the wrong element count, an ITEM/SHIP before any ASN (orphan), or an unknown tag.
"""

from collections.abc import Iterator
from pathlib import Path

from semantic_layer.extract.records import RawRecord

_ELEMENTS = {"ASN": ("asn_id", "ref_po"), "ITEM": ("sku", "ship_qty", "uom"), "SHIP": ("ship_dt", "carrier")}


def parse(path: Path) -> Iterator[RawRecord]:
    current: dict[str, str] | None = None
    current_error: str | None = None
    counter = 0

    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        tag, *elems = line.split("*")

        if tag == "ASN":
            if current is not None:
                yield RawRecord(source_row_ref=f"record {counter}", fields=current, error=current_error)
                counter += 1
                current, current_error = None, None
            if len(elems) != len(_ELEMENTS["ASN"]):
                yield RawRecord(source_row_ref=f"record {counter}", fields={"_raw": line},
                                error="malformed ASN segment")
                counter += 1
            else:
                current = dict(zip(_ELEMENTS["ASN"], elems))
                current_error = None
        elif tag in ("ITEM", "SHIP"):
            if current is None:
                yield RawRecord(source_row_ref=f"record {counter}", fields={"_raw": line},
                                error="orphan segment (no preceding ASN)")
                counter += 1
            elif len(elems) != len(_ELEMENTS[tag]):
                current_error = f"malformed {tag} segment: {line}"
            else:
                current.update(zip(_ELEMENTS[tag], elems))
        else:
            if current is None:
                yield RawRecord(source_row_ref=f"record {counter}", fields={"_raw": line},
                                error="unknown segment")
                counter += 1
            else:
                current_error = f"unknown segment: {line}"

    if current is not None:
        yield RawRecord(source_row_ref=f"record {counter}", fields=current, error=current_error)
