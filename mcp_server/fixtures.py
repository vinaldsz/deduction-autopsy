import os
from pathlib import Path

from mcp_server.models import (
    ASN,
    DeductionClaim,
    Invoice,
    PurchaseOrder,
    ReceivingRecord,
    TradeAgreement,
)

SCENARIOS_ROOT = Path(__file__).parent.parent / "scenarios"


class FixtureLoader:
    def __init__(self, scenario_id: str | None = None):
        self.scenario_id = scenario_id or os.environ["SCENARIO_ID"]
        self.dir = self._resolve_dir()

    def _resolve_dir(self) -> Path:
        matches = sorted(SCENARIOS_ROOT.glob(f"{self.scenario_id}*"))
        if len(matches) != 1:
            raise ValueError(
                f"SCENARIO_ID {self.scenario_id!r} matched {len(matches)} directories "
                f"under {SCENARIOS_ROOT}, expected exactly 1"
            )
        return matches[0]

    def _load(self, filename, model):
        return model.model_validate_json((self.dir / filename).read_text())

    def get_po(self) -> PurchaseOrder:
        return self._load("po.json", PurchaseOrder)

    def get_invoice(self) -> Invoice:
        return self._load("invoice.json", Invoice)

    def get_receiving_record(self) -> ReceivingRecord:
        return self._load("receiving_record.json", ReceivingRecord)

    def get_asns(self) -> list[ASN]:
        return [
            self._load(path.name, ASN)
            for path in sorted(self.dir.glob("asn*.json"))
        ]

    def get_trade_agreement(self) -> TradeAgreement | None:
        path = self.dir / "trade_agreement.json"
        if not path.exists():
            return None
        return self._load(path.name, TradeAgreement)

    def get_claims(self) -> list[DeductionClaim]:
        return [
            self._load(path.name, DeductionClaim)
            for path in sorted(self.dir.glob("*claim*.json"))
        ]
