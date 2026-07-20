"""The 8 scenarios' ground-truth verdicts from docs/SPEC.md.

Single source of truth for scenario -> claim_id -> expected verdict, imported by both
cli/run_all.py (live pass/fail table) and tests/test_fixtures.py (claim_id consistency check)
so the two never drift out of sync with each other.

expected_reviewer is in the Reviewer's own vocabulary (CONFIRM/OVERTURN/ESCALATE) — uniformly
CONFIRM here — and must be compared against PipelineResult.reviewer_verdict, not .final_verdict
(business vocabulary VALID/INVALID/ESCALATE, which varies per scenario).

s08's expected verdicts are INVALID/CONFIRM, not VALID/OVERTURN: live-testing found the current
Investigator prompt (hardened during the Layer 9 follow-up) already catches this duplicate
correctly on its own — see tests/test_pipeline_scenarios.py's
test_reviewer_overturns_a_missed_duplicate for the dedicated test that proves the Reviewer's
spot-check *would* independently catch and overturn a hypothetical Investigator miss, using a
fabricated CaseFile rather than depending on the real Investigator actually getting it wrong.
"""

GROUND_TRUTH = [
    {
        "scenario": "s01_clean_shortage",
        "claim_id": "CLM-001",
        "expected_investigator": "VALID",
        "expected_reviewer": "CONFIRM",
        "trap": "All docs genuinely agree on a 12-unit shortage; receiving notes confirm refusal.",
    },
    {
        "scenario": "s02_casepack_mismatch",
        "claim_id": "CLM-002",
        "expected_investigator": "INVALID",
        "expected_reviewer": "CONFIRM",
        "trap": "PO=5 CASE, ASN=120 EACH; a naive diff looks like a 115-unit shortage, but "
        "normalize_uom resolves them to an exact match.",
    },
    {
        "scenario": "s03_split_shipment",
        "claim_id": "CLM-003",
        "expected_investigator": "INVALID",
        "expected_reviewer": "CONFIRM",
        "trap": "Two ASN files (60+60); the retailer only counted the first one, but the "
        "aggregate across both equals the full PO quantity.",
    },
    {
        "scenario": "s04_sequence_violation",
        "claim_id": "CLM-004",
        "expected_investigator": "INVALID",
        "expected_reviewer": "CONFIRM",
        "trap": "invoice_date (Apr 8) precedes ship_date (Apr 10) — the timeline is physically "
        "impossible, independent of how cleanly the quantities otherwise reconcile.",
    },
    {
        "scenario": "s05_sku_substitution",
        "claim_id": "CLM-005",
        "expected_investigator": "INVALID",
        "expected_reviewer": "CONFIRM",
        "trap": "ASN sku=SKU-005-ALT vs PO sku=SKU-005; receiving_record.notes contains explicit "
        "buyer pre-approval language for the substitution.",
    },
    {
        "scenario": "s06_promo_billback",
        "claim_id": "CLM-006",
        "expected_investigator": "INVALID",
        "expected_reviewer": "CONFIRM",
        "trap": "A trade agreement exists, but for PROMO-SPRING-2024; the claim cites "
        "PROMO-SUMMER-2024, so get_trade_agreement returns None for the claimed code.",
    },
    {
        "scenario": "s07_duplicate_claim",
        "claim_id": "CLM-007b",
        "expected_investigator": "INVALID",
        "expected_reviewer": "CONFIRM",
        "trap": "prior_claim CLM-007a's notes read 'RESOLVED - credit memo CM-007 issued "
        "2024-06-10' — this claim re-deducts the same, already-credited shortage.",
    },
    {
        "scenario": "s08_reviewer_overturn",
        "claim_id": "CLM-008",
        "expected_investigator": "INVALID",
        "expected_reviewer": "CONFIRM",
        "trap": "prior_claim CLM-008a shows the same PO/quantity already credited via CM-014, "
        "expressed as a dollar figure rather than an explicit 'RESOLVED' — a duplicate claim "
        "the segregation-of-duties check is designed to catch (see docs/SPEC.md's 'Eighth "
        "Scenario' section for why the live Investigator already catches this one itself).",
    },
]
