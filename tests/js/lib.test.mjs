// `node --test tests/js/` — Node's built-in runner, no dependencies, no package.json.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { dollars, dollarsCompact, lotSubtitle, todoSplit } from "../../ui/static/lib.js";

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
