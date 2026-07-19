import pytest

from mcp_server.tools.uom_tools import normalize_uom


@pytest.mark.parametrize(
    "qty,from_uom,to_uom,sku,expected",
    [
        (5, "CASE", "EACH", "SKU-002", 120),
        (1, "PALLET", "EACH", "SKU-002", 960),
        (12, "EACH", "CASE", "SKU-001", 1),
        (120, "EACH", "EACH", "SKU-001", 120),
    ],
)
def test_normalize_uom(qty, from_uom, to_uom, sku, expected):
    assert normalize_uom(qty, from_uom, to_uom, sku) == expected


def test_unknown_sku_raises():
    with pytest.raises(ValueError):
        normalize_uom(1, "CASE", "EACH", "SKU-999")


def test_undefined_path_raises():
    with pytest.raises(ValueError):
        normalize_uom(1, "EACH", "PALLET", "SKU-001")
