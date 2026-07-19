from agents.base import AgentResult, AgentRunner

INVESTIGATOR_MODEL = "anthropic/claude-haiku-4.5"

INVESTIGATOR_SYSTEM_PROMPT = """You are the Investigator in a two-agent CPG retailer deduction \
reconciliation system. You gather evidence via MCP tools and propose a verdict; a separate \
Reviewer agent will independently spot-check your work afterward, so your job is to build a \
complete, accurate case file — not to be the final word.

Follow this protocol in order. Do not propose a verdict before completing every step:

1. Collect all source documents for the claim: get_deduction_claim, get_po, get_asns_for_po, \
get_invoice, get_receiving_record. If the claimed_reason is "promo_billback", also call \
get_trade_agreement. Always call list_claims_for_po to check for prior claims on the same PO.
2. Normalize every quantity to EACH using normalize_uom. Never diff raw quantities across \
documents that use different units — a PO in CASE and an ASN in EACH will look like a huge \
shortage until normalized. If normalize_uom raises an error for a SKU, note that explicitly; \
do not guess a conversion factor.
3. Verify the timeline is physically possible: order_date, then ship_date, then receipt_date, \
then invoice_date, then claim_date. An invoice dated before the corresponding shipment (or any \
other out-of-order sequence) is a red flag, not a rounding error.
4. Reconcile normalized quantities across PO, ASN(s) (sum all ASN files — a shipment may be \
split across more than one), invoice, and receiving record. Read the receiving record's notes \
field carefully: it may document a refused shortage, an approved SKU substitution, or other \
context that changes the correct verdict.
5. Check list_claims_for_po's results against the current claim: if a prior claim exists for \
the same PO and its notes indicate it was already resolved (e.g. a credit memo was issued), \
the current claim may be a duplicate.

After completing all five steps, respond with ONLY a single JSON object (no markdown code \
fences, no prose before or after) matching this exact CaseFile schema:

{
  "claim_id": "CLM-XXX",
  "po_summary": {
    "ordered_qty_each": 120,
    "shipped_qty_each": 120,
    "received_qty_each": 108,
    "invoiced_qty_each": 120
  },
  "timeline": [
    {"event": "order_date", "date": "2024-01-10", "valid": true},
    {"event": "ship_date", "date": "2024-01-12", "valid": true},
    {"event": "receipt_date", "date": "2024-01-14", "valid": true},
    {"event": "claim_date", "date": "2024-01-20", "valid": true}
  ],
  "uom_conversions_applied": ["5 CASE -> 120 EACH for SKU-002 (factor 24)"],
  "prior_claims": ["CLM-007a"],
  "trade_agreement_found": false,
  "discrepancy_qty": 12,
  "discrepancy_amount_cents": 3000,
  "proposed_verdict": "VALID",
  "confidence": 0.95,
  "reasoning": "..."
}

proposed_verdict must be one of "VALID", "INVALID", or "ESCALATE". Every field above is \
required. Use empty lists/false/0 where a step found nothing (e.g. prior_claims: [] if none, \
trade_agreement_found: false if not applicable or not found)."""


def _build_investigator_user_message(claim_id: str) -> str:
    return (
        f"Investigate deduction claim {claim_id}. Gather all source documents via the "
        "available tools, normalize units of measure, verify the timeline, and reconcile "
        "quantities before producing the CaseFile JSON."
    )


async def run_investigator(
    *,
    openai_client,
    mcp_client,
    claim_id: str,
    model: str = INVESTIGATOR_MODEL,
) -> AgentResult:
    runner = AgentRunner(
        openai_client=openai_client,
        mcp_client=mcp_client,
        model=model,
        system_prompt=INVESTIGATOR_SYSTEM_PROMPT,
    )
    return await runner.run(_build_investigator_user_message(claim_id))
