from fastmcp import FastMCP

from mcp_server.tools.document_tools import (
    get_asns_for_po,
    get_deduction_claim,
    get_invoice,
    get_po,
    get_receiving_record,
    get_trade_agreement,
    list_claims_for_po,
)
from mcp_server.tools.uom_tools import normalize_uom

mcp = FastMCP("deduction-autopsy")

for fn in (
    get_po,
    get_asns_for_po,
    get_invoice,
    get_receiving_record,
    get_trade_agreement,
    normalize_uom,
    get_deduction_claim,
    list_claims_for_po,
):
    mcp.tool(fn)

if __name__ == "__main__":
    mcp.run()
