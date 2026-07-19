import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from mcp_server.server import mcp

EXPECTED_TOOL_NAMES = {
    "get_po",
    "get_asns_for_po",
    "get_invoice",
    "get_receiving_record",
    "get_trade_agreement",
    "normalize_uom",
    "get_deduction_claim",
    "list_claims_for_po",
}


async def test_lists_all_eight_tools():
    async with Client(mcp) as client:
        tools = await client.list_tools()
    assert {tool.name for tool in tools} == EXPECTED_TOOL_NAMES


async def test_get_po_via_mcp(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")

    async with Client(mcp) as client:
        result = await client.call_tool("get_po", {"po_id": "PO-001"})

    assert result.data.retailer == "walmart"
    assert result.data.sku == "SKU-001"


async def test_normalize_uom_via_mcp(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s02_casepack_mismatch")

    async with Client(mcp) as client:
        result = await client.call_tool(
            "normalize_uom",
            {"qty": 5, "from_uom": "CASE", "to_uom": "EACH", "sku": "SKU-002"},
        )

    assert result.data == 120


async def test_get_trade_agreement_returns_none_via_mcp(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s06_promo_billback")

    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_trade_agreement",
            {"retailer": "safeway", "sku": "SKU-006", "promo_code": "PROMO-SUMMER-2024"},
        )

    assert result.data is None


async def test_unknown_po_id_raises_via_mcp(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")

    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_po", {"po_id": "PO-999"})
