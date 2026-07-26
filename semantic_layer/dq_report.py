"""ETL Data-Quality report (Layer 26): per-source read/loaded/rejected counts + reject reasons.

A human/audit summary of a Transform run. Layer 27 will also fold these counts into the `load_audit`
table; here it stays a plain in-memory structure with a renderer for logs/CLI.
"""

from collections import Counter
from dataclasses import dataclass, field

from semantic_layer.extract.records import RawRecord
from semantic_layer.transform import TransformResult


@dataclass
class SourceStats:
    rows_read: int = 0
    rows_loaded: int = 0
    rows_rejected: int = 0
    reasons: Counter = field(default_factory=Counter)


@dataclass
class DQReport:
    per_source: dict[str, SourceStats]

    @property
    def total_read(self) -> int:
        return sum(s.rows_read for s in self.per_source.values())

    @property
    def total_loaded(self) -> int:
        return sum(s.rows_loaded for s in self.per_source.values())

    @property
    def total_rejected(self) -> int:
        return sum(s.rows_rejected for s in self.per_source.values())


def build_dq_report(records: list[RawRecord], result: TransformResult) -> DQReport:
    per_source: dict[str, SourceStats] = {}

    def stats(source_file: str) -> SourceStats:
        return per_source.setdefault(source_file, SourceStats())

    for rec in records:
        stats(rec.source_file).rows_read += 1
    for clean in result.clean:
        stats(clean.source_file).rows_loaded += 1
    for reject in result.rejects:
        source = stats(reject.source_file)
        source.rows_rejected += 1
        source.reasons[reject.reason] += 1
    return DQReport(per_source)


def render_dq_report(report: DQReport) -> str:
    lines = [
        "Data Quality Report",
        f"  TOTAL: read={report.total_read} loaded={report.total_loaded} "
        f"rejected={report.total_rejected}",
    ]
    for source_file, s in sorted(report.per_source.items()):
        lines.append(
            f"  {source_file}: read={s.rows_read} loaded={s.rows_loaded} rejected={s.rows_rejected}"
        )
        for reason, count in s.reasons.most_common():
            lines.append(f"      - {reason}: {count}")
    return "\n".join(lines)
