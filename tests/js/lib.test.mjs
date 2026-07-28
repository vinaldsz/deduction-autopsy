// `node --test tests/js/` — Node's built-in runner, no dependencies, no package.json.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { dollars, dollarsCompact } from "../../ui/static/lib.js";

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
