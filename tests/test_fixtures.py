import json
from pathlib import Path

import pytest

from mcp_server.models import (
    ASN,
    DeductionClaim,
    Invoice,
    PurchaseOrder,
    ReceivingRecord,
    TradeAgreement,
)
from orchestrator.ground_truth import GROUND_TRUTH

REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = REPO_ROOT / "scenarios"
UOM_TABLE_PATH = REPO_ROOT / "data" / "sku_uom_conversions.json"

SCENARIO_DIRS = sorted(p for p in SCENARIOS_DIR.iterdir() if p.is_dir())

MODEL_BY_STEM = {
    "po": PurchaseOrder,
    "invoice": Invoice,
    "receiving_record": ReceivingRecord,
    "trade_agreement": TradeAgreement,
    "deduction_claim": DeductionClaim,
    "prior_claim": DeductionClaim,
}

# Derived from orchestrator.ground_truth.GROUND_TRUTH — the single source of truth for
# scenario -> claim_id, shared with cli/run_all.py so the two can't drift out of sync.
EXPECTED_SCENARIOS = {case["scenario"] for case in GROUND_TRUTH}
EXPECTED_CLAIM_ID = {case["scenario"]: case["claim_id"] for case in GROUND_TRUTH}


def _model_for(path: Path):
    stem = path.stem
    if stem.startswith("asn"):
        return ASN
    return MODEL_BY_STEM[stem]


def _load(path: Path):
    return json.loads(path.read_text())


def test_all_seven_scenarios_present():
    found = {p.name for p in SCENARIO_DIRS}
    assert found == EXPECTED_SCENARIOS


@pytest.mark.parametrize("scenario_dir", SCENARIO_DIRS, ids=lambda p: p.name)
def test_every_fixture_file_validates_against_its_model(scenario_dir):
    fixture_files = sorted(scenario_dir.glob("*.json"))
    assert fixture_files, f"{scenario_dir.name} has no fixture files"
    for fixture_file in fixture_files:
        model = _model_for(fixture_file)
        model.model_validate(_load(fixture_file))


@pytest.mark.parametrize("scenario_dir", SCENARIO_DIRS, ids=lambda p: p.name)
def test_po_id_consistent_across_sibling_documents(scenario_dir):
    po = _load(scenario_dir / "po.json")
    po_id = po["po_id"]
    for fixture_file in scenario_dir.glob("*.json"):
        if fixture_file.name in ("po.json", "trade_agreement.json"):
            continue
        data = _load(fixture_file)
        assert data["po_id"] == po_id, f"{fixture_file} has mismatched po_id"


@pytest.mark.parametrize("scenario_dir", SCENARIO_DIRS, ids=lambda p: p.name)
def test_retailer_consistent_between_po_and_claim(scenario_dir):
    po = _load(scenario_dir / "po.json")
    claim = _load(scenario_dir / "deduction_claim.json")
    assert po["retailer"] == claim["retailer"]


@pytest.mark.parametrize("scenario_dir", SCENARIO_DIRS, ids=lambda p: p.name)
def test_deduction_claim_id_matches_ground_truth(scenario_dir):
    claim = _load(scenario_dir / "deduction_claim.json")
    assert claim["claim_id"] == EXPECTED_CLAIM_ID[scenario_dir.name]


def test_s03_has_two_split_asn_files_not_a_single_asn():
    scenario_dir = SCENARIOS_DIR / "s03_split_shipment"
    assert not (scenario_dir / "asn.json").exists()
    assert (scenario_dir / "asn_1.json").exists()
    assert (scenario_dir / "asn_2.json").exists()


@pytest.mark.parametrize(
    "scenario_dir",
    [p for p in SCENARIO_DIRS if p.name != "s03_split_shipment"],
    ids=lambda p: p.name,
)
def test_non_split_scenarios_have_single_asn_file(scenario_dir):
    assert (scenario_dir / "asn.json").exists()
    assert not list(scenario_dir.glob("asn_*.json"))


def test_only_s06_has_trade_agreement():
    for scenario_dir in SCENARIO_DIRS:
        has_trade_agreement = (scenario_dir / "trade_agreement.json").exists()
        assert has_trade_agreement == (scenario_dir.name == "s06_promo_billback")


def test_only_s07_has_prior_claim():
    for scenario_dir in SCENARIO_DIRS:
        has_prior_claim = (scenario_dir / "prior_claim.json").exists()
        assert has_prior_claim == (scenario_dir.name == "s07_duplicate_claim")


def test_s06_trade_agreement_promo_code_does_not_match_claimed_promo():
    scenario_dir = SCENARIOS_DIR / "s06_promo_billback"
    trade_agreement = _load(scenario_dir / "trade_agreement.json")
    claim = _load(scenario_dir / "deduction_claim.json")
    assert trade_agreement["promo_code"] not in claim["retailer_notes"]
    assert "PROMO-SUMMER-2024" in claim["retailer_notes"]


def test_s07_prior_claim_is_marked_resolved():
    scenario_dir = SCENARIOS_DIR / "s07_duplicate_claim"
    prior_claim = _load(scenario_dir / "prior_claim.json")
    assert "RESOLVED" in prior_claim["retailer_notes"]
    assert prior_claim["claim_id"] != _load(scenario_dir / "deduction_claim.json")["claim_id"]


def test_s05_asn_and_receiving_sku_differs_from_po_sku():
    scenario_dir = SCENARIOS_DIR / "s05_sku_substitution"
    po = _load(scenario_dir / "po.json")
    asn = _load(scenario_dir / "asn.json")
    receiving_record = _load(scenario_dir / "receiving_record.json")
    assert asn["sku"] != po["sku"]
    assert receiving_record["sku"] == asn["sku"]


def test_s04_invoice_date_precedes_ship_date():
    scenario_dir = SCENARIOS_DIR / "s04_sequence_violation"
    asn = _load(scenario_dir / "asn.json")
    invoice = _load(scenario_dir / "invoice.json")
    assert invoice["invoice_date"] < asn["ship_date"]


def test_s02_naive_qty_diff_looks_like_shortage_but_normalized_qty_matches():
    scenario_dir = SCENARIOS_DIR / "s02_casepack_mismatch"
    po = _load(scenario_dir / "po.json")
    asn = _load(scenario_dir / "asn.json")
    conversions = _load(UOM_TABLE_PATH)
    factor = conversions[po["sku"]]["CASE_to_EACH"]
    assert po["ordered_qty"] * factor == asn["shipped_qty"]


def test_s03_split_asn_quantities_sum_to_full_po_quantity():
    scenario_dir = SCENARIOS_DIR / "s03_split_shipment"
    po = _load(scenario_dir / "po.json")
    asn_1 = _load(scenario_dir / "asn_1.json")
    asn_2 = _load(scenario_dir / "asn_2.json")
    assert asn_1["shipped_qty"] + asn_2["shipped_qty"] == po["ordered_qty"]


def test_every_referenced_sku_has_a_uom_conversion_entry():
    conversions = _load(UOM_TABLE_PATH)
    for scenario_dir in SCENARIO_DIRS:
        for fixture_file in scenario_dir.glob("*.json"):
            data = _load(fixture_file)
            sku = data.get("sku")
            if sku is not None:
                assert sku in conversions, f"{sku} in {fixture_file} missing from UOM table"
