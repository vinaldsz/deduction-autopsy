# Deduction Autopsy — Specification

## Data Models

All monetary amounts in USD cents (integer). Dates as ISO-8601 strings. Quantities as
integers unless UOM conversion produces a float.

```
PurchaseOrder
  po_id          str       e.g. "PO-001"
  retailer       str       e.g. "walmart"
  sku            str       e.g. "SKU-001"
  ordered_qty    int
  ordered_uom    str       "EACH" | "CASE" | "PALLET"
  unit_price     int       cents per EACH
  order_date     str       ISO-8601

ASN  (0 or more per PO)
  asn_id         str
  po_id          str
  sku            str
  shipped_qty    int
  shipped_uom    str
  ship_date      str
  carrier        str

Invoice
  invoice_id     str
  po_id          str
  sku            str
  invoiced_qty   int
  invoiced_uom   str
  invoice_date   str
  amount         int       cents

ReceivingRecord
  receipt_id     str
  po_id          str
  sku            str
  received_qty   int
  received_uom   str
  receipt_date   str
  lot_id         str
  notes          str       free text; may contain substitution approval language

TradeAgreement  (optional; only present for promo-related claims)
  agreement_id   str
  retailer       str
  sku            str
  promo_code     str
  discount_terms str
  valid_from     str
  valid_to       str
  signed_by      str

DeductionClaim  (the input to the whole system)
  claim_id       str
  po_id          str
  retailer       str
  claimed_reason str       "shortage" | "promo_billback" | "compliance" | "wrong_item"
  claimed_amount int       cents
  claim_date     str
  retailer_notes str
```

---

## UOM Conversion Table (`data/sku_uom_conversions.json`)

```json
{
  "SKU-001": { "CASE_to_EACH": 12 },
  "SKU-002": { "CASE_to_EACH": 24, "PALLET_to_CASE": 40 },
  "SKU-003": { "CASE_to_EACH": 6 },
  "SKU-004": { "CASE_to_EACH": 12 },
  "SKU-005": { "CASE_to_EACH": 10 },
  "SKU-005-ALT": { "CASE_to_EACH": 10 },
  "SKU-006": { "CASE_to_EACH": 24 }
}
```

---

## MCP Tools

| Tool | Signature | Returns |
|---|---|---|
| `get_deduction_claim` | `(claim_id: str)` | `DeductionClaim` |
| `get_po` | `(po_id: str)` | `PurchaseOrder` |
| `get_asns_for_po` | `(po_id: str)` | `list[ASN]` |
| `get_invoice` | `(po_id: str)` | `Invoice` |
| `get_receiving_record` | `(po_id: str)` | `ReceivingRecord` |
| `get_trade_agreement` | `(retailer: str, sku: str, promo_code: str)` | `TradeAgreement \| None` |
| `normalize_uom` | `(qty: float, from_uom: str, to_uom: str, sku: str)` | `float` |
| `list_claims_for_po` | `(po_id: str)` | `list[str]` (claim_ids) |

`normalize_uom` raises `ValueError` with a descriptive message if the conversion path is
unknown for the given SKU. Agents must handle this explicitly — it is load-bearing for
scenario 2 detection.

---

## CaseFile Schema (Investigator output, validated by orchestrator)

```json
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
    {"event": "ship_date",  "date": "2024-01-12", "valid": true},
    {"event": "receipt_date","date": "2024-01-14","valid": true},
    {"event": "claim_date", "date": "2024-01-20", "valid": true}
  ],
  "uom_conversions_applied": ["5 CASE → 120 EACH for SKU-002 (factor 24)"],
  "prior_claims": ["CLM-007a"],
  "trade_agreement_found": false,
  "discrepancy_qty": 12,
  "discrepancy_amount_cents": 3000,
  "proposed_verdict": "VALID",
  "confidence": 0.95,
  "reasoning": "..."
}
```

Required fields: `claim_id`, `po_summary` (all 4 sub-fields), `timeline`, `proposed_verdict`,
`confidence`. Missing any of these causes the orchestrator to send a correction message and
force another Investigator turn.

---

## Reviewer Output Schema

```json
{
  "claim_id": "CLM-XXX",
  "investigator_verdict": "INVALID",
  "review_findings": {
    "uom_check": "PASS",
    "split_shipment_check": "PASS",
    "timeline_check": "N/A",
    "trade_agreement_check": "N/A",
    "duplicate_check": "N/A",
    "substitution_check": "N/A"
  },
  "final_verdict": "CONFIRM",
  "confidence": 0.97,
  "dispute_grounds": ["Normalized quantities match: 5 CASE = 120 EACH"],
  "reasoning": "..."
}
```

---

## Seven Scenarios — Ground Truth

**These expected verdicts are fixed. Do not change fixture data to make a failing test
pass — fix the agent prompts or tool logic instead.**

| # | Scenario dir | Claim ID | Investigator expected | Final expected | The trap |
|---|---|---|---|---|---|
| 1 | `s01_clean_shortage` | CLM-001 | VALID | CONFIRM | All docs genuinely agree on 12-unit shortage; receiving notes confirm refusal |
| 2 | `s02_casepack_mismatch` | CLM-002 | INVALID | CONFIRM | PO=5 CASE, ASN=120 EACH; naive diff looks like 115-unit shortage; normalize_uom resolves to match |
| 3 | `s03_split_shipment` | CLM-003 | INVALID | CONFIRM | Two ASN files (60+60); retailer only counted the first; aggregate = full PO |
| 4 | `s04_sequence_violation` | CLM-004 | INVALID | CONFIRM | invoice_date (Apr 8) precedes ship_date (Apr 10); timeline is impossible |
| 5 | `s05_sku_substitution` | CLM-005 | INVALID | CONFIRM | ASN sku=SKU-005-ALT vs PO sku=SKU-005; receiving_record.notes contains explicit pre-approval text |
| 6 | `s06_promo_billback` | CLM-006 | INVALID | CONFIRM | trade_agreement exists but for PROMO-SPRING-2024; claim cites PROMO-SUMMER-2024; get_trade_agreement returns None |
| 7 | `s07_duplicate_claim` | CLM-007b | INVALID | CONFIRM | prior_claim CLM-007a notes = "RESOLVED - credit memo CM-007 issued 2024-06-10" |

---

## Scenario Fixture File Layout

```
scenarios/s0N_name/
  deduction_claim.json      always present
  po.json                   always present
  asn.json                  s01, s02, s04, s05, s06 (single ASN)
  asn_1.json + asn_2.json   s03 only (split shipment)
  invoice.json              always present
  receiving_record.json     always present
  trade_agreement.json      s06 only (mismatched promo code)
  prior_claim.json          s07 only (CLM-007a, resolved)
```

---

## Output Artifacts (`outputs/{claim_id}/`)

| File | When written | Contents |
|---|---|---|
| `verdict.json` | Always | `{claim_id, investigator_verdict, reviewer_verdict, final_verdict, confidence, timestamp}` |
| `dispute_packet.md` | When final_verdict == INVALID | Normalized qty table, timeline, dispute grounds bullets |
| `reasoning_trace.json` | Always | Full messages arrays from both agents including all tool inputs/outputs |

---

## Required Tool Calls Per Scenario (trace verification)

The orchestrator verifies these appear in the Investigator's tool-call trace:

| Scenario | Required tool call |
|---|---|
| s02 | `normalize_uom` |
| s03 | `get_asns_for_po` returning list of length ≥ 2 |
| s06 | `get_trade_agreement` |
| s07 | `list_claims_for_po` |

If a required tool call is absent from the trace, the orchestrator sends a correction
message before accepting the CaseFile.
