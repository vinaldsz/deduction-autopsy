"""Offline tests for tools/generate_source_systems.py (no LLM, no OpenRouter).

Layer 24: proves the generator (1) reproduces the committed source_systems/ tree byte-for-byte
(drift guard — this is what lets us commit the generated output), (2) emits every pooled entity
exactly once, (3) assigns claims to the right daily lots, (4) produces parseable divergent files,
and (5) applies divergences that are exactly reversible (so the Layer 27 fidelity oracle can hold).
"""

import csv
import json

import pytest

from tools import generate_source_systems as gen

COMMITTED = gen.DEFAULT_OUT
TODAY_LOT = "2024-09-15"  # max active claim_date
PRIOR_LOTS = {"2024-06-08": "CLM-007a", "2024-08-10": "CLM-008a"}


@pytest.fixture(scope="module")
def pool():
    return gen.build_pool()  # canonical scenarios + the synthetic daily-lot volume


@pytest.fixture
def fresh(tmp_path):
    """Regenerate the whole tree into a temp dir."""
    gen.generate(tmp_path)
    return tmp_path


# --- reproducibility / drift guard ---------------------------------------------------------------

def test_regeneration_is_byte_identical_to_committed_tree(fresh):
    committed = sorted(p.relative_to(COMMITTED) for p in COMMITTED.rglob("*") if p.is_file())
    regenerated = sorted(p.relative_to(fresh) for p in fresh.rglob("*") if p.is_file())
    assert regenerated == committed, "committed source_systems/ file set drifted from generator"
    for rel in committed:
        assert (fresh / rel).read_bytes() == (COMMITTED / rel).read_bytes(), f"drift in {rel}"


# --- completeness: every pooled entity appears exactly once ---------------------------------------

def test_each_entity_appears_exactly_once(fresh, pool):
    with (fresh / "erp" / "purchase_orders.csv").open() as f:
        po_ids = [row["PO_NUMBER"] for row in csv.DictReader(f)]
    with (fresh / "erp" / "invoices.csv").open() as f:
        inv_ids = [row["INVOICE_NO"] for row in csv.DictReader(f)]
    asn_ids = [ln.split("*")[1] for ln in (fresh / "carrier" / "asn_856.txt").read_text().splitlines()
               if ln.startswith("ASN*")]
    receipt_ids = [r["receipt"]["id"] for r in json.loads((fresh / "wms" / "receiving.json").read_text())]
    ta_ids = [a["agreement"]["id"] for a in json.loads((fresh / "tpm" / "trade_agreements.json").read_text())]
    claim_ids = []
    for path in (fresh / "portal").glob("claims_*.json"):
        claim_ids += [c["claimId"] for c in json.loads(path.read_text())["claims"]]

    def once(got, want):
        assert len(got) == len(set(got)), f"duplicate PK emitted: {got}"
        assert set(got) == set(want)

    once(po_ids, pool.purchase_orders)
    once(inv_ids, pool.invoices)
    once(asn_ids, pool.asns)  # 9, incl. the ASN-003A/003B split pair
    once(receipt_ids, pool.receiving)
    once(ta_ids, pool.agreements)
    once(claim_ids, {**pool.active_claims, **pool.prior_claims})
    assert len(claim_ids) == 52  # 8 canonical + 42 synthetic active + 2 prior


def test_split_shipment_emits_two_asn_loops_for_one_po(fresh):
    text = (fresh / "carrier" / "asn_856.txt").read_text()
    po3 = [ln for ln in text.splitlines() if ln.startswith("ASN*") and ln.endswith("*PO-003")]
    assert sorted(po3) == ["ASN*ASN-003A*PO-003", "ASN*ASN-003B*PO-003"]


# --- lot assignment ------------------------------------------------------------------------------

def test_todays_lot_holds_exactly_the_active_claims(fresh, pool):
    lot = json.loads((fresh / "portal" / f"claims_{TODAY_LOT}.json").read_text())
    assert lot["lot_date"] == TODAY_LOT
    assert {c["claimId"] for c in lot["claims"]} == set(pool.active_claims)
    assert "CLM-007a" not in {c["claimId"] for c in lot["claims"]}


def test_prior_claims_each_get_their_own_earlier_lot(fresh):
    for lot_date, claim_id in PRIOR_LOTS.items():
        lot = json.loads((fresh / "portal" / f"claims_{lot_date}.json").read_text())
        assert lot["lot_date"] == lot_date
        assert [c["claimId"] for c in lot["claims"]] == [claim_id]


def test_manifest_lists_every_emitted_file_with_targets(fresh):
    manifest = json.loads((fresh / "manifest.json").read_text())
    listed = {s["file"] for s in manifest["sources"]}
    on_disk = {str(p.relative_to(fresh)) for p in fresh.rglob("*")
               if p.is_file() and p.name != "manifest.json"}
    assert listed == on_disk
    lot_dates = {s["lot_date"] for s in manifest["sources"] if s["target"] == "deduction_claims"}
    assert lot_dates == {TODAY_LOT, *PRIOR_LOTS}


# --- parse-ability -------------------------------------------------------------------------------

def test_sources_parse(fresh):
    for csv_name in ("purchase_orders.csv", "invoices.csv"):
        with (fresh / "erp" / csv_name).open() as f:
            assert list(csv.DictReader(f))  # non-empty, well-formed
    for json_rel in ("wms/receiving.json", "tpm/trade_agreements.json"):
        assert json.loads((fresh / json_rel).read_text())
    loops = [ln for ln in (fresh / "carrier" / "asn_856.txt").read_text().splitlines()
             if ln.startswith("ASN*")]
    assert len(loops) == 51  # 9 canonical (incl. split pair) + 42 synthetic


# --- divergences are exactly reversible ----------------------------------------------------------

def test_money_divergence_reverses_to_cents(fresh, pool):
    with (fresh / "erp" / "purchase_orders.csv").open() as f:
        by_id = {row["PO_NUMBER"]: row for row in csv.DictReader(f)}
    for po_id, po in pool.purchase_orders.items():
        raw = by_id[po_id]["UNIT_PRICE"].lstrip("$")
        dollars, cents = raw.split(".")
        assert int(dollars) * 100 + int(cents) == po.unit_price


def test_mmddyyyy_date_reverses_to_iso(fresh, pool):
    with (fresh / "erp" / "purchase_orders.csv").open() as f:
        by_id = {row["PO_NUMBER"]: row for row in csv.DictReader(f)}
    seen_mmddyyyy = False
    for po_id, po in pool.purchase_orders.items():
        raw = by_id[po_id]["ORDER_DT"]
        if "/" in raw:
            seen_mmddyyyy = True
            month, day, year = raw.split("/")
            assert f"{year}-{month}-{day}" == po.order_date
        else:
            assert raw == po.order_date
    assert seen_mmddyyyy, "expected at least one MM/DD/YYYY date"


def test_uom_synonyms_fold_back_to_canonical(fresh, pool):
    records = json.loads((fresh / "wms" / "receiving.json").read_text())
    inverse = {v: k for k, v in gen.UOM_SYNONYM.items()}
    by_id = {r["receipt"]["id"]: r["receipt"] for r in records}
    for receipt_id, rcp in pool.receiving.items():
        assert inverse[by_id[receipt_id]["uom"]] == rcp.received_uom


def test_notes_round_trip_byte_exact(fresh, pool):
    """The prompt-injection surface must survive: WMS notes trim back, portal notes are verbatim."""
    records = json.loads((fresh / "wms" / "receiving.json").read_text())
    by_id = {r["receipt"]["id"]: r["receipt"] for r in records}
    for receipt_id, rcp in pool.receiving.items():
        assert by_id[receipt_id]["notes"].strip() == rcp.notes  # padded in source, trimmed back
    lot = json.loads((fresh / "portal" / f"claims_{TODAY_LOT}.json").read_text())
    portal_notes = {c["claimId"]: c["notes"] for c in lot["claims"]}
    for claim_id, claim in pool.active_claims.items():
        assert portal_notes[claim_id] == claim.retailer_notes  # verbatim
