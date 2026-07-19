import json
from collections import deque
from pathlib import Path

CONVERSIONS_PATH = Path(__file__).parent.parent.parent / "data" / "sku_uom_conversions.json"
_CONVERSIONS = json.loads(CONVERSIONS_PATH.read_text())


def normalize_uom(qty: float, from_uom: str, to_uom: str, sku: str) -> float:
    """Convert a quantity between units of measure (EACH/CASE/PALLET) for a given SKU; raises if no conversion path exists."""
    if from_uom == to_uom:
        return qty
    if sku not in _CONVERSIONS:
        raise ValueError(f"No UOM conversions defined for SKU {sku!r}")

    graph: dict[str, list[tuple[str, float]]] = {}
    for key, factor in _CONVERSIONS[sku].items():
        big, small = key.split("_to_")
        graph.setdefault(big, []).append((small, factor))
        graph.setdefault(small, []).append((big, 1 / factor))

    queue = deque([(from_uom, 1.0)])
    seen = {from_uom}
    while queue:
        node, mult = queue.popleft()
        if node == to_uom:
            return qty * mult
        for neighbor, factor in graph.get(node, []):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((neighbor, mult * factor))

    raise ValueError(f"No conversion path from {from_uom} to {to_uom} for SKU {sku!r}")
