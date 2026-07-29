// `node --test tests/js/` — Node's built-in runner, no dependencies, no package.json.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  ageLabel, buildHash, bulkOutcomeSummary, confidenceBand, DEFAULT_STATE, discrepancyPhrase, dollars,
  dollarsCompact, keyAction, lotSubtitle, nextClaimId, parseHash, priorityLegend, queueFooter,
  reviewChecks, sentenceCase, sortIndicator, timelineGaps, todoSplit, verdictLabel,
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

// --- Layer 37b: the URL is the description of what you're looking at --------------------------------

describe("parseHash", () => {
  it("reads a full worklist state out of a hash", () => {
    const s = parseHash("#filter=disputable&sort=amount&dir=asc&q=walmart&page=3&size=50" +
      "&retailer=kroger&reason=shortage&from=2024-09-01&to=2024-09-15&claim=CLM-002");
    assert.deepEqual(s, {
      filter: "disputable", sort: "amount", direction: "asc", q: "walmart", page: 3, size: 50,
      claim: "CLM-002", retailer: "kroger", reason: "shortage",
      date_from: "2024-09-01", date_to: "2024-09-15",
    });
  });

  it("falls back to the defaults for an empty or absent hash", () => {
    assert.deepEqual(parseHash(""), DEFAULT_STATE);
    assert.deepEqual(parseHash("#"), DEFAULT_STATE);
    assert.deepEqual(parseHash(null), DEFAULT_STATE);
    assert.deepEqual(parseHash(undefined), DEFAULT_STATE);
  });

  it("sanitizes every unrecognised value instead of passing it to the API", () => {
    // The API rejects these with 422 (Layer 37a). A stale bookmark is the client's own mess, so it
    // lands on the default rather than erroring the whole page — e.g. `needs_me`, a filter key that
    // Layer 35 renamed out of existence.
    const s = parseHash("#filter=needs_me&sort=nope&dir=sideways&size=7&page=0");
    assert.equal(s.filter, DEFAULT_STATE.filter);
    assert.equal(s.sort, DEFAULT_STATE.sort);
    assert.equal(s.direction, null);
    assert.equal(s.size, DEFAULT_STATE.size);
    assert.equal(s.page, 1);
  });

  it("rejects a page number that isn't a positive integer", () => {
    for (const page of ["0", "-4", "abc", "25abc", "2.5e9999", ""]) {
      assert.equal(parseHash(`#page=${page}`).page, 1, page);
    }
    assert.equal(parseHash("#page=12").page, 12);
  });

  it("rejects a date that only looks like one", () => {
    // Shape alone is not enough: claim_date is stored as a string, so the server would compare
    // "2024-13-45" lexicographically and quietly return the wrong rows rather than complain.
    assert.equal(parseHash("#from=2024-13-45").date_from, null);
    assert.equal(parseHash("#from=2024-02-30").date_from, null);
    assert.equal(parseHash("#from=yesterday").date_from, null);
    assert.equal(parseHash("#from=2024-9-1").date_from, null);
    assert.equal(parseHash("#from=2024-09-01").date_from, "2024-09-01");
    assert.equal(parseHash("#from=2024-02-29").date_from, "2024-02-29");  // a real leap day
  });

  it("passes retailer and reason through unvalidated", () => {
    // Deliberate: an unknown retailer returns no rows, which is the honest answer to "show me claims
    // from a retailer that isn't in this lot" — not something to silently rewrite.
    assert.equal(parseHash("#retailer=nobody").retailer, "nobody");
  });
});

describe("buildHash", () => {
  it("omits everything at its default so a shared link carries only what changed", () => {
    assert.equal(buildHash(DEFAULT_STATE), "");
    assert.equal(buildHash({ ...DEFAULT_STATE, filter: "all" }), "#filter=all");
    assert.equal(buildHash({ ...DEFAULT_STATE, page: 1, size: 25 }), "");
  });

  it("round-trips every state parseHash can produce", () => {
    for (const state of [
      DEFAULT_STATE,
      { ...DEFAULT_STATE, filter: "decided", sort: "age", direction: "asc", page: 4 },
      { ...DEFAULT_STATE, q: "walmart", size: 100, claim: "CLM-SYN-0009" },
      { ...DEFAULT_STATE, retailer: "kroger", reason: "promo_billback",
        date_from: "2024-09-01", date_to: "2024-09-15" },
    ]) {
      assert.deepEqual(parseHash(buildHash(state)), state, JSON.stringify(state));
    }
  });

  it("keeps a null direction out of the URL", () => {
    // null means "server's choice of default for this column"; writing it as a value would freeze
    // the client's guess at that default into every link it ever produced.
    assert.equal(buildHash({ ...DEFAULT_STATE, sort: "amount" }), "#sort=amount");
    assert.equal(buildHash({ ...DEFAULT_STATE, sort: "amount", direction: "desc" }),
      "#sort=amount&dir=desc");
  });
});

describe("sortIndicator", () => {
  it("marks only the sorted column, in the direction the server applied", () => {
    assert.equal(sortIndicator("amount", "amount", "desc"), "▼");
    assert.equal(sortIndicator("amount", "amount", "asc"), "▲");
    assert.equal(sortIndicator("age", "amount", "desc"), "");
  });

  it("shows nothing until the server has answered", () => {
    // appliedSort is null before the first response. An arrow drawn from the *request* would point
    // at a sort the table may not be in — `direction` goes out null most of the time.
    assert.equal(sortIndicator("amount", null, null), "");
    assert.equal(sortIndicator(null, null, null), "");
  });
});

describe("priorityLegend", () => {
  it("states the bands from the server's own thresholds", () => {
    const legend = priorityLegend({ high_cents: 15000, med_cents: 5000, age_days: 45 });
    assert.match(legend, /HIGH \$150\.00\+ at risk, or older than 45 days/);
    assert.match(legend, /MEDIUM \$50\.00\+/);
    assert.match(legend, /LOW everything else/);
  });

  it("says nothing rather than inventing thresholds when there are none", () => {
    assert.equal(priorityLegend(null), "");
    assert.equal(priorityLegend(undefined), "");
  });
});

describe("ageLabel", () => {
  it("renders days compactly and admits a missing value", () => {
    assert.equal(ageLabel(239), "239d");
    assert.equal(ageLabel(0), "0d");
    assert.equal(ageLabel(null), "—");
    assert.equal(ageLabel(undefined), "—");
  });
});

describe("queueFooter", () => {
  it("totals the filtered set and says that it is filtered", () => {
    assert.deepEqual(queueFooter(13, 35400, true), { label: "13 claims, filtered", amount: "$354.00" });
    assert.deepEqual(queueFooter(50, 371140, false), { label: "50 claims", amount: "$3,711.40" });
  });

  it("agrees with itself on one claim", () => {
    assert.equal(queueFooter(1, 3000, false).label, "1 claim");
    assert.equal(queueFooter(0, 0, true).label, "0 claims, filtered");
  });
});

// --- Layer 38: working the volume ------------------------------------------------------------------

describe("keyAction", () => {
  const press = (key, extra = {}) => keyAction({ key, ...extra });

  it("maps the queue keymap", () => {
    assert.equal(press("j"), "next");
    assert.equal(press("k"), "prev");
    assert.equal(press("a"), "accept");
    assert.equal(press("o"), "override");
    assert.equal(press("x"), "toggle-select");
    assert.equal(press("/"), "search");
  });

  it("claims nothing it hasn't been given", () => {
    assert.equal(press("s"), null, "`s` (send to human) has no target — Layer 32 removed the control");
    assert.equal(press("z"), null);
    assert.equal(press("Enter"), null, "row activation is the row's own handler");
    assert.equal(press("A"), null, "shift+a is not accept");
  });

  it("is silent while the caret is in a field — the load-bearing case", () => {
    // Every binding is a bare letter, so without this typing "jab" into the search box would walk the
    // queue and record a decision on whatever it landed on.
    for (const key of ["j", "k", "a", "o", "x", "/"]) {
      assert.equal(press(key, { inField: true }), null, `${key} must be inert in a field`);
    }
  });

  it("gives Escape a way out of a field, and does nothing with it outside one", () => {
    assert.equal(press("Escape", { inField: true }), "blur");
    assert.equal(press("Escape"), null);
  });

  it("leaves modified keys to the browser", () => {
    // Cmd+A is select-all, Ctrl+A is start-of-line. Stealing either is worse than no shortcut.
    assert.equal(press("a", { metaKey: true }), null);
    assert.equal(press("a", { ctrlKey: true }), null);
    assert.equal(press("j", { altKey: true }), null);
    assert.equal(press("x", { shiftKey: true }), null);
  });

  it("survives being called with nothing", () => {
    assert.equal(keyAction(), null);
    assert.equal(keyAction({}), null);
  });
});

describe("nextClaimId", () => {
  const ids = ["CLM-1", "CLM-2", "CLM-3"];

  it("walks forwards and backwards", () => {
    assert.equal(nextClaimId(ids, "CLM-1", 1), "CLM-2");
    assert.equal(nextClaimId(ids, "CLM-2", -1), "CLM-1");
    assert.equal(nextClaimId(ids, "CLM-2"), "CLM-3", "delta defaults to forwards");
  });

  it("does not wrap", () => {
    // Jumping silently back to row 1 after deciding the last claim is indistinguishable from a
    // reload bug, and it would re-open a claim the analyst has already worked.
    assert.equal(nextClaimId(ids, "CLM-3", 1), null);
    assert.equal(nextClaimId(ids, "CLM-1", -1), null);
  });

  it("starts from the top when the current claim has left the page", () => {
    // The normal outcome of deciding a claim: its row drops out of the filter. Forwards still lands
    // on work; backwards has nothing to reason from and says so.
    assert.equal(nextClaimId(ids, "CLM-GONE", 1), "CLM-1");
    assert.equal(nextClaimId(ids, "CLM-GONE", -1), null);
    assert.equal(nextClaimId(ids, null, 1), "CLM-1");
  });

  it("has nothing to offer on an empty page", () => {
    assert.equal(nextClaimId([], "CLM-1", 1), null);
    assert.equal(nextClaimId(null, "CLM-1", 1), null);
  });
});

describe("bulkOutcomeSummary", () => {
  it("counts the outcomes and leads with what was recorded", () => {
    const summary = bulkOutcomeSummary({
      "CLM-1": "recorded", "CLM-2": "recorded", "CLM-3": "not_investigated",
      "CLM-4": "already_decided",
    });
    assert.equal(summary, "2 accepted · 1 not investigated · 1 already decided");
  });

  it("omits the buckets that are empty", () => {
    assert.equal(bulkOutcomeSummary({ "CLM-1": "recorded" }), "1 accepted");
    assert.equal(bulkOutcomeSummary({}), "");
    assert.equal(bulkOutcomeSummary(null), "");
  });

  it("explains an escalated claim rather than just naming the code", () => {
    assert.match(bulkOutcomeSummary({ "CLM-1": "unresolved_verdict" }), /still escalated/);
  });

  it("still counts an outcome it doesn't recognise", () => {
    // The counts have to add up to the selection. A claim dropped from the summary because the server
    // grew a new outcome is a lie about what happened to it; an ugly word is not.
    const summary = bulkOutcomeSummary({ "CLM-1": "recorded", "CLM-2": "spontaneously_combusted" });
    assert.equal(summary, "1 accepted · 1 spontaneously_combusted");
  });
});

// --- Layer 39: explainability -----------------------------------------------------------------------

/** ReviewFindings as orchestrator/pipeline.py declares it — all seven fields, in field order. */
const FINDINGS = {
  uom_check: "PASS", split_shipment_check: "PASS", timeline_check: "FAIL",
  trade_agreement_check: "N/A", duplicate_check: "PASS", substitution_check: "N/A",
  data_completeness_check: "PASS",
};

describe("reviewChecks", () => {
  it("keeps the order the ReviewFindings model declared", () => {
    // Field order in pipeline.py is load-bearing *because* these render in it — data_completeness_check
    // is declared last with a comment saying so. Iterating the payload keeps the two in step with no
    // second copy of the order here.
    assert.deepEqual(
      reviewChecks(FINDINGS).map((c) => c.key),
      Object.keys(FINDINGS),
    );
  });

  it("labels uom_check 'Unit of measure', not 'Uom'", () => {
    // The reason this is a table and not sentenceCase(key): the very first check is the one the
    // generic path mangles.
    assert.equal(reviewChecks(FINDINGS)[0].label, "Unit of measure");
  });

  it("says what every check the model declares actually tested", () => {
    for (const check of reviewChecks(FINDINGS)) {
      assert.ok(check.description.length > 20, `${check.key} needs a description`);
    }
  });

  it("carries the status through untouched, because the word is what survives greyscale", () => {
    const byKey = Object.fromEntries(reviewChecks(FINDINGS).map((c) => [c.key, c.status]));
    assert.equal(byKey.timeline_check, "FAIL");
    assert.equal(byKey.trade_agreement_check, "N/A");
  });

  it("gives no check a tone — PASS is not a money direction", () => {
    // CLM-002 is the case: timeline_check FAIL is what makes the claim disputable, so a green PASS
    // would invert Layer 36 exactly where Layer 36 matters.
    for (const check of reviewChecks(FINDINGS)) {
      assert.equal(check.tone, undefined, check.key);
    }
  });

  it("says data completeness is not a dispute ground", () => {
    // agents/reviewer.py:67-74 — the one check that works differently, and whose FAIL forces ESCALATE.
    const completeness = reviewChecks(FINDINGS).find((c) => c.key === "data_completeness_check");
    assert.match(completeness.description, /never a dispute ground/);
    assert.match(completeness.description, /ESCALATE/);
  });

  it("keeps a check it doesn't recognise rather than dropping the row", () => {
    // An eighth check added upstream must still show its status: a dropped row could be hiding a FAIL.
    const [row] = reviewChecks({ carrier_liability_check: "FAIL" });
    assert.equal(row.status, "FAIL");
    assert.equal(row.label, "Carrier liability");
    assert.equal(row.description, "");
  });

  it("ignores what it inherits from Object.prototype", () => {
    // CHECKS["constructor"] is a truthy function; a truthiness lookup would spread it into a row.
    const [row] = reviewChecks({ constructor: "PASS" });
    assert.equal(row.description, "");
    assert.equal(row.label, "Constructor");
  });

  it("survives being called with nothing", () => {
    assert.deepEqual(reviewChecks(null), []);
    assert.deepEqual(reviewChecks({}), []);
  });
});

describe("timelineGaps", () => {
  const timeline = [
    { event: "order_date", date: "2024-02-01", valid: true },
    { event: "ship_date", date: "2024-02-03", valid: true },
    { event: "receipt_date", date: "2024-05-02", valid: true },
  ];

  it("states the interval since the previous event", () => {
    // The interval is the finding: a claim filed 90 days after receipt is why a deduction is late.
    assert.deepEqual(timelineGaps(timeline).map((e) => e.gap), [null, "+2 days", "+89 days"]);
  });

  it("leaves the first event without a gap, having nothing to measure from", () => {
    const [first] = timelineGaps(timeline);
    assert.equal(first.gap, null);
    assert.equal(first.gapDays, null);
  });

  it("does not reorder the events", () => {
    // CLM-002, real: the Investigator emits invoice_date BEFORE the receipt it follows, flags both
    // valid, and the Reviewer independently records timeline_check FAIL. Sorting this would delete the
    // visible evidence for that FAIL.
    const outOfOrder = [
      { event: "receipt_date", date: "2024-02-05", valid: true },
      { event: "invoice_date", date: "2024-02-04", valid: true },
    ];
    assert.deepEqual(timelineGaps(outOfOrder).map((e) => e.event), ["receipt_date", "invoice_date"]);
  });

  it("names a backwards interval as out of order", () => {
    const outOfOrder = [
      { event: "receipt_date", date: "2024-02-05", valid: true },
      { event: "invoice_date", date: "2024-02-04", valid: true },
    ];
    const [, invoice] = timelineGaps(outOfOrder);
    assert.equal(invoice.gapDays, -1);
    assert.equal(invoice.gap, "1 day earlier · out of order");
  });

  it("says 'same day' rather than '+0 days'", () => {
    const sameDay = [{ date: "2024-02-01" }, { date: "2024-02-01" }];
    assert.equal(timelineGaps(sameDay)[1].gap, "same day");
  });

  it("uses the singular for one day", () => {
    assert.equal(timelineGaps([{ date: "2024-02-01" }, { date: "2024-02-02" }])[1].gap, "+1 day");
  });

  it("offers no gap for a date it cannot read", () => {
    // TimelineEvent.date is an unvalidated str, and "NaN days" on screen is worse than no label.
    const bad = [{ date: "2024-02-01" }, { date: "not a date" }, { date: "2024-13-45" }];
    assert.deepEqual(timelineGaps(bad).map((e) => e.gap), [null, null, null]);
  });

  it("voids the gap after an unreadable date instead of measuring from two events back", () => {
    const withHole = [{ date: "2024-02-01" }, { date: "" }, { date: "2024-02-10" }];
    const gaps = timelineGaps(withHole).map((e) => e.gap);
    assert.deepEqual(gaps, [null, null, null], "the third gap would read '+9 days' since the first");
  });

  it("passes the event's own fields straight through", () => {
    const [first] = timelineGaps([{ event: "order_date", date: "2024-02-01", valid: false }]);
    assert.equal(first.event, "order_date");
    assert.equal(first.date, "2024-02-01");
    assert.equal(first.valid, false);
  });

  it("survives being called with nothing", () => {
    assert.deepEqual(timelineGaps(null), []);
    assert.deepEqual(timelineGaps([]), []);
  });
});
