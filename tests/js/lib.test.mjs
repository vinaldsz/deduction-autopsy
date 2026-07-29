// `node --test tests/js/` — Node's built-in runner, no dependencies, no package.json.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  confidenceBand, discrepancyPhrase, dollars, dollarsCompact, lotSubtitle, sentenceCase, todoSplit,
  verdictLabel,
} from "../../ui/static/lib.js";

/** The /api/dashboard shape, as ui/queries.py::dashboard_metrics returns it. */
const METRICS = {
  lot_total: 50, todo_count: 12, not_investigated_count: 9, awaiting_my_call_count: 3,
  decided_count: 38, open_amount_cents: 368140, oldest_open_days: 44,
  priority_breakdown: { HIGH: 16, MEDIUM: 6, LOW: 27 },
  batch: { batch_id: "LOT-2024-09-15", status: "complete" },
};

describe("dollars", () => {
  it("groups thousands", () => {
    // The bug this replaces: `"$" + (cents / 100).toFixed(2)` rendered "$123456789.00".
    assert.equal(dollars(123456789), "$1,234,567.89");
    assert.equal(dollars(1234567), "$12,345.67");
  });

  it("formats small and zero amounts", () => {
    assert.equal(dollars(3000), "$30.00");
    assert.equal(dollars(5), "$0.05");
    assert.equal(dollars(0), "$0.00");
  });

  it("formats negatives", () => {
    assert.equal(dollars(-3000), "-$30.00");
  });

  it("renders an em dash rather than $NaN for missing values", () => {
    // COALESCE(SUM(...), 0) guards this server-side, but an absent JSON field must not read as money.
    assert.equal(dollars(null), "—");
    assert.equal(dollars(undefined), "—");
    assert.equal(dollars("nope"), "—");
  });
});

describe("dollarsCompact", () => {
  it("abbreviates thousands and millions", () => {
    assert.equal(dollarsCompact(1250000), "$12.5k");
    assert.equal(dollarsCompact(368140), "$3.7k");
    assert.equal(dollarsCompact(500000000), "$5.0M");
  });

  it("leaves sub-thousand amounts exact", () => {
    assert.equal(dollarsCompact(99900), "$999.00");
    assert.equal(dollarsCompact(0), "$0.00");
  });

  it("keeps the sign on negatives", () => {
    assert.equal(dollarsCompact(-1250000), "-$12.5k");
  });

  it("renders an em dash for missing values", () => {
    assert.equal(dollarsCompact(null), "—");
  });
});

describe("verdictLabel", () => {
  it("reads INVALID as money we recover and VALID as money we concede", () => {
    // THE regression this layer exists to prevent. The old UI painted VALID green (--ok) and
    // INVALID red (--bad), i.e. the opposite of the financial outcome in both word and colour. If a
    // future palette tweak inverts these two tones, nothing else in the suite would notice.
    assert.equal(verdictLabel("INVALID").tone, "pos");
    assert.equal(verdictLabel("INVALID").label, "Disputable");
    assert.equal(verdictLabel("VALID").tone, "neg");
    assert.equal(verdictLabel("VALID").label, "Conceded");
  });

  it("routes ESCALATE to the analyst without calling it an error", () => {
    const v = verdictLabel("ESCALATE");
    assert.equal(v.tone, "warn");
    assert.equal(v.label, "Your call");
  });

  it("gives every verdict a glyph, so the money direction survives greyscale", () => {
    for (const verdict of ["VALID", "INVALID", "ESCALATE", "unresolved", null]) {
      assert.ok(verdictLabel(verdict).glyph, `no glyph for ${verdict}`);
      assert.ok(verdictLabel(verdict).blurb, `no blurb for ${verdict}`);
    }
    // Distinct, or the glyph carries no information.
    const glyphs = ["VALID", "INVALID", "ESCALATE", "unresolved"].map((v) => verdictLabel(v).glyph);
    assert.equal(new Set(glyphs).size, 4);
  });

  it("treats an un-investigated claim as neutral, not as a verdict", () => {
    for (const absent of ["unresolved", null, undefined, ""]) {
      const v = verdictLabel(absent);
      assert.equal(v.tone, "neutral");
      assert.equal(v.label, "Not investigated");
      assert.equal(v.verdict, "unresolved");
    }
  });

  it("never guesses a money direction for a verdict it doesn't know", () => {
    // A wrong direction is worse than an admitted unknown: "Conceded" on something we can't read
    // would tell the analyst to write off money for no reason.
    const v = verdictLabel("PARTIALLY_VALID");
    assert.equal(v.tone, "neutral");
    assert.equal(v.label, "PARTIALLY_VALID");
    // Object.prototype keys are not verdicts. A truthiness lookup would return the inherited
    // property here and spread into a chip with no label, tone or glyph.
    assert.equal(verdictLabel("constructor").tone, "neutral");
    assert.equal(verdictLabel("toString").glyph, "·");
  });
});

describe("confidenceBand", () => {
  it("bands the reviewer's self-report instead of implying false precision", () => {
    assert.deepEqual(confidenceBand(0.97), { pct: 97, band: "High" });
    assert.deepEqual(confidenceBand(0.82), { pct: 82, band: "Moderate" });
    assert.deepEqual(confidenceBand(0.4), { pct: 40, band: "Low" });
  });

  it("puts the boundaries in the higher band", () => {
    assert.equal(confidenceBand(0.9).band, "High");
    assert.equal(confidenceBand(0.899).band, "Moderate");
    assert.equal(confidenceBand(0.7).band, "Moderate");
    assert.equal(confidenceBand(0.699).band, "Low");
  });

  it("clamps a model self-report that leaves [0, 1]", () => {
    // 1.3 would otherwise render a meter fill 130% as wide as its own track.
    assert.deepEqual(confidenceBand(1.3), { pct: 100, band: "High" });
    assert.deepEqual(confidenceBand(-0.2), { pct: 0, band: "Low" });
  });

  it("rounds rather than truncates", () => {
    assert.equal(confidenceBand(0.949).pct, 95);
    assert.equal(confidenceBand(0.005).pct, 1);
  });

  it("returns null when there is no confidence to show", () => {
    // The caller hides the whole meter on null — a 0% bar would read as "no confidence at all".
    assert.equal(confidenceBand(null), null);
    assert.equal(confidenceBand(undefined), null);
    assert.equal(confidenceBand("nope"), null);
  });
});

describe("discrepancyPhrase", () => {
  it("states which way a shortage runs and in whose favour", () => {
    // "favours the retailer's claim", not "the retailer wins": s04 has a real shortage and an
    // INVALID verdict, so the quantity arithmetic does not decide the verdict on its own.
    assert.deepEqual(discrepancyPhrase(12, 3000), {
      text: "12 EACH short · $30.00 — favours the retailer's claim", tone: "neg",
    });
  });

  it("calls a zero discrepancy what it is — the grounds for disputing", () => {
    // The old row rendered "0 EACH · $0.00", which reads as missing data rather than as the finding.
    assert.deepEqual(discrepancyPhrase(0, 0), {
      text: "No quantity discrepancy — the documents reconcile exactly", tone: "neutral",
    });
  });

  it("shows money booked against a zero quantity instead of papering over it", () => {
    const d = discrepancyPhrase(0, 3000);
    assert.equal(d.text, "No quantity discrepancy — the documents reconcile exactly · $30.00");
    assert.equal(d.tone, "neutral");
  });

  it("reads an over-shipment as running in our favour", () => {
    assert.deepEqual(discrepancyPhrase(-8, -2000), {
      text: "8 EACH over-shipped · $20.00 — favours us", tone: "pos",
    });
  });

  it("renders an em dash rather than NaN when the field is absent", () => {
    assert.deepEqual(discrepancyPhrase(null, null), { text: "—", tone: "neutral" });
    assert.equal(discrepancyPhrase("nope", 0).text, "—");
    // A present quantity with a missing amount is still a statable finding.
    assert.equal(discrepancyPhrase(12, null).text,
      "12 EACH short · $0.00 — favours the retailer's claim");
  });
});

describe("sentenceCase", () => {
  it("humanises every claimed_reason the models allow", () => {
    // mcp_server/models.py::ClaimReason — all four, rendered raw in the queue until now.
    assert.equal(sentenceCase("shortage"), "Shortage");
    assert.equal(sentenceCase("promo_billback"), "Promo billback");
    assert.equal(sentenceCase("compliance"), "Compliance");
    assert.equal(sentenceCase("wrong_item"), "Wrong item");
  });

  it("humanises the timeline event names", () => {
    assert.equal(sentenceCase("order_date"), "Order date");
    assert.equal(sentenceCase("claim_date"), "Claim date");
  });

  it("leaves already-readable text alone and survives empty input", () => {
    assert.equal(sentenceCase("Shortage"), "Shortage");
    assert.equal(sentenceCase(""), "");
    assert.equal(sentenceCase(null), "");
    assert.equal(sentenceCase(undefined), "");
  });
});

describe("todoSplit", () => {
  it("spells out the two halves of the queue", () => {
    assert.equal(todoSplit(METRICS), "9 not investigated · 3 awaiting your call");
  });

  it("omits a half that is empty rather than printing a zero", () => {
    assert.equal(todoSplit({ ...METRICS, awaiting_my_call_count: 0 }), "9 not investigated");
    assert.equal(todoSplit({ ...METRICS, not_investigated_count: 0 }), "3 awaiting your call");
    assert.equal(todoSplit({ ...METRICS, not_investigated_count: 0, awaiting_my_call_count: 0 }), "");
  });
});

describe("lotSubtitle", () => {
  it("states today's lot rather than describing the architecture", () => {
    assert.equal(lotSubtitle(METRICS),
      "LOT-2024-09-15 · 50 claims · 12 to do · $3.7k open · oldest 44d");
  });

  it("says so when the queue is clear", () => {
    assert.equal(lotSubtitle({ ...METRICS, todo_count: 0, open_amount_cents: 0, oldest_open_days: 0 }),
      "LOT-2024-09-15 · 50 claims · all decided");
  });

  it("surfaces a batch status that isn't clean", () => {
    const m = { ...METRICS, batch: { batch_id: "LOT-1", status: "complete_with_exceptions" } };
    assert.match(lotSubtitle(m), /complete_with_exceptions$/);
  });

  it("tells a first-time user what to do when there is no lot", () => {
    // The empty store: dashboard_metrics returns batch: null, and a blank header reads as broken.
    assert.match(lotSubtitle({ ...METRICS, batch: null }), /run the ETL/i);
    assert.match(lotSubtitle(null), /run the ETL/i);
  });
});
