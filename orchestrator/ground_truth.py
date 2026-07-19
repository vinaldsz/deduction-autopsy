"""The 7 scenarios' ground-truth verdicts from docs/SPEC.md.

Single source of truth for scenario -> claim_id -> expected verdict, imported by both
cli/run_all.py (live pass/fail table) and tests/test_fixtures.py (claim_id consistency check)
so the two never drift out of sync with each other.

expected_reviewer is in the Reviewer's own vocabulary (CONFIRM/OVERTURN/ESCALATE) — uniformly
CONFIRM here — and must be compared against PipelineResult.reviewer_verdict, not .final_verdict
(business vocabulary VALID/INVALID/ESCALATE, which varies per scenario).
"""

GROUND_TRUTH = [
    {"scenario": "s01_clean_shortage", "claim_id": "CLM-001", "expected_investigator": "VALID", "expected_reviewer": "CONFIRM"},
    {"scenario": "s02_casepack_mismatch", "claim_id": "CLM-002", "expected_investigator": "INVALID", "expected_reviewer": "CONFIRM"},
    {"scenario": "s03_split_shipment", "claim_id": "CLM-003", "expected_investigator": "INVALID", "expected_reviewer": "CONFIRM"},
    {"scenario": "s04_sequence_violation", "claim_id": "CLM-004", "expected_investigator": "INVALID", "expected_reviewer": "CONFIRM"},
    {"scenario": "s05_sku_substitution", "claim_id": "CLM-005", "expected_investigator": "INVALID", "expected_reviewer": "CONFIRM"},
    {"scenario": "s06_promo_billback", "claim_id": "CLM-006", "expected_investigator": "INVALID", "expected_reviewer": "CONFIRM"},
    {"scenario": "s07_duplicate_claim", "claim_id": "CLM-007b", "expected_investigator": "INVALID", "expected_reviewer": "CONFIRM"},
]
