"""WMS / portal / TPM JSON parsers — format parsing only.

Nested objects are flattened to dotted keys (`receipt.id`, `agreement.promoCode`) so every parser
yields a uniformly flat `fields` dict; scalar values are stringified but otherwise untouched
(whitespace in notes is preserved — trimming is Layer 26). WMS/TPM are arrays of single-key-wrapped
objects (`{"receipt": {...}}` / `{"agreement": {...}}`); portal is `{"lot_date", "claims": [...]}`,
one record per claim. Structurally-broken input is flagged (error) and parsing continues: an element
missing its wrapper / not an object, a missing `claims` array, or a file that is not valid JSON.
"""

import json
from collections.abc import Iterator
from pathlib import Path

from semantic_layer.extract.records import RawRecord


def _flatten(obj: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in obj.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, f"{dotted}."))
        else:
            out[dotted] = value if isinstance(value, str) else str(value)
    return out


def _load(path: Path) -> tuple[object, str, str | None]:
    text = path.read_text()
    try:
        return json.loads(text), text, None
    except json.JSONDecodeError as exc:
        return None, text, f"invalid JSON: {exc}"


def _parse_wrapped_array(path: Path, wrapper: str) -> Iterator[RawRecord]:
    data, text, error = _load(path)
    if error is not None:
        yield RawRecord(source_row_ref="record 0", fields={"_raw": text}, error=error)
        return
    if not isinstance(data, list):
        yield RawRecord(source_row_ref="record 0", fields={"_raw": text}, error="expected a JSON array")
        return
    for i, element in enumerate(data):
        ref = f"record {i}"
        if not isinstance(element, dict) or not isinstance(element.get(wrapper), dict):
            yield RawRecord(source_row_ref=ref, fields={"_raw": json.dumps(element)},
                            error=f"missing '{wrapper}' wrapper")
            continue
        yield RawRecord(source_row_ref=ref, fields=_flatten(element))


def parse_wms_json(path: Path) -> Iterator[RawRecord]:
    return _parse_wrapped_array(path, "receipt")


def parse_tpm_json(path: Path) -> Iterator[RawRecord]:
    return _parse_wrapped_array(path, "agreement")


def parse_portal_json(path: Path) -> Iterator[RawRecord]:
    data, text, error = _load(path)
    if error is not None:
        yield RawRecord(source_row_ref="record 0", fields={"_raw": text}, error=error)
        return
    claims = data.get("claims") if isinstance(data, dict) else None
    if not isinstance(claims, list):
        yield RawRecord(source_row_ref="record 0", fields={"_raw": text}, error="missing 'claims' array")
        return
    for i, claim in enumerate(claims):
        ref = f"record {i}"
        if not isinstance(claim, dict):
            yield RawRecord(source_row_ref=ref, fields={"_raw": json.dumps(claim)}, error="claim is not an object")
            continue
        yield RawRecord(source_row_ref=ref, fields=_flatten(claim))
