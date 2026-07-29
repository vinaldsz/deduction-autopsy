// `node --test tests/js/` — Node's built-in runner, no dependencies, no package.json.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  ageLabel, buildHash, bulkOutcomeSummary, confidenceBand, DEFAULT_STATE, discrepancyPhrase, dollars,
  dollarsCompact, keyAction, lotSubtitle, nextClaimId, parseHash, priorityLegend, queueFooter,
  bulkConfirmMessage, currentRun, decisionSummary, dispositionLabel, isFieldNode, isFiltered,
  overrideGuard, pageStatus, parseFrame, pickedOnPage, queryParams, reviewChecks, runHistoryLine,
  safeClass, selectAllState, sentenceCase, sortIndicator, splitFrames, statusParts, timelineGaps,
  todoSplit, usageLine, verdictLabel,
  batchSummaryLine, runConfirmMessage, runProgressLine, usageTokens,
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

// --- extracted from app.js by the modularization refactor --------------------------------------------
//
// None of the logic below had any test before it moved out of app.js. Where a test pins a rule that
// used to be implemented twice, or wrong, the comment says so.

describe("isFiltered", () => {
  it("calls the default tab filtered, because it is", () => {
    // `filter` defaults to "todo", not "all" — the landing view really is a narrowed one.
    assert.equal(isFiltered({ filter: "todo" }), true);
    assert.equal(isFiltered({ filter: "all" }), false);
  });

  it("notices a narrowing filter even on the all tab", () => {
    for (const key of ["q", "retailer", "reason", "date_from", "date_to"]) {
      assert.equal(isFiltered({ filter: "all", [key]: "x" }), true, key);
    }
  });

  it("ignores an empty narrowing value", () => {
    assert.equal(isFiltered({ filter: "all", q: "", retailer: null, date_from: undefined }), false);
  });
});

describe("safeClass", () => {
  it("makes N/A a usable class name", () => {
    assert.equal(safeClass("N/A"), "NA");
    assert.equal(safeClass("PASS"), "PASS");
    assert.equal(safeClass("FAIL"), "FAIL");
  });
});

describe("queryParams", () => {
  const base = { page: 1, size: 25, filter: "todo", sort: "priority", direction: null,
                 q: "", retailer: null, reason: null, date_from: null, date_to: null };
  const parse = (state, overrides) => Object.fromEntries(queryParams(state, overrides));

  it("translates page/size into the offset the server speaks", () => {
    // The one expression in the client that is only ever wrong by one.
    assert.equal(parse({ ...base, page: 1 }).offset, "0");
    assert.equal(parse({ ...base, page: 2 }).offset, "25");
    assert.equal(parse({ ...base, page: 3, size: 100 }).offset, "200");
  });

  it("omits direction rather than guessing one", () => {
    // The server owns each column's useful first click; sending a default here would override it.
    assert.equal("direction" in parse(base), false);
    assert.equal(parse({ ...base, direction: "asc" }).direction, "asc");
  });

  it("omits a narrowing filter that is empty instead of sending a blank", () => {
    assert.deepEqual(parse(base), { offset: "0", limit: "25", status_filter: "todo", sort: "priority" });
    const full = parse({ ...base, q: "walmart", retailer: "kroger", reason: "shortage",
                         date_from: "2024-01-01", date_to: "2024-06-30" });
    assert.equal(full.q, "walmart");
    assert.equal(full.retailer, "kroger");
    assert.equal(full.date_to, "2024-06-30");
  });

  it("lets an override win, which is how a single claim is looked up", () => {
    // fetchClaimRow's mode: widen to every filter and one big page to find a claim off the current one.
    const one = parse(base, { filter: "all", page: 1, size: 100, q: "CLM-042", retailer: null });
    assert.equal(one.status_filter, "all");
    assert.equal(one.limit, "100");
    assert.equal(one.q, "CLM-042");
  });
});

describe("pageStatus", () => {
  it("counts the rows it actually got, not a full page", () => {
    // The last page is short; deriving the upper bound from `size` would claim rows that aren't there.
    assert.equal(pageStatus(6, 25, 137, 12).label, "126–137 of 137");
    assert.equal(pageStatus(1, 25, 137, 25).label, "1–25 of 137");
  });

  it("says 0 rather than 1–0 on an empty page", () => {
    assert.equal(pageStatus(1, 25, 0, 0).label, "0 of 0");
  });

  it("spends the arrows at the ends", () => {
    assert.deepEqual(pageStatus(1, 25, 137, 25), { label: "1–25 of 137", prevDisabled: true, nextDisabled: false });
    assert.equal(pageStatus(6, 25, 137, 12).nextDisabled, true);
    assert.equal(pageStatus(2, 25, 137, 25).prevDisabled, false);
  });

  it("disables next when the page exactly fills the total", () => {
    assert.equal(pageStatus(2, 25, 50, 25).nextDisabled, true);
  });
});

describe("pickedOnPage / selectAllState", () => {
  const ids = ["CLM-1", "CLM-2", "CLM-3"];

  it("counts only the selection that is on this page", () => {
    // The counter and the bulk POST used to compute this separately; they must mean the same thing.
    assert.deepEqual(pickedOnPage(ids, new Set(["CLM-2", "CLM-99"])), ["CLM-2"]);
  });

  it("shows 3-of-25 as indeterminate, not as all", () => {
    const s = selectAllState(ids, new Set(["CLM-1"]));
    assert.equal(s.checked, false);
    assert.equal(s.indeterminate, true);
  });

  it("checks the box only when the whole page is selected", () => {
    const s = selectAllState(ids, new Set(ids));
    assert.equal(s.checked, true);
    assert.equal(s.indeterminate, false);
  });

  it("does not call an empty page fully selected", () => {
    const s = selectAllState([], new Set());
    assert.equal(s.checked, false);
    assert.equal(s.indeterminate, false);
  });

  it("counts the whole selection, which may exceed this page", () => {
    // `picked` is page-scoped by policy, but the label must report what it holds.
    const s = selectAllState(ids, new Set(["CLM-1", "CLM-2"]));
    assert.equal(s.countLabel, "2 selected");
    assert.equal(s.acceptLabel, "Accept 2 verdicts");
  });

  it("uses the singular for one verdict", () => {
    assert.equal(selectAllState(ids, new Set(["CLM-1"])).acceptLabel, "Accept 1 verdict");
  });
});

describe("bulkConfirmMessage", () => {
  it("names the count and warns that some will be skipped", () => {
    assert.match(bulkConfirmMessage(12), /12 claims\?/);
    assert.match(bulkConfirmMessage(1), /1 claim\?/);
    assert.match(bulkConfirmMessage(3), /skipped and listed/);
  });
});

describe("statusParts", () => {
  it("keeps the superseded agent verdict visible on an override", () => {
    // The audit trail: `status` is the analyst's answer, and dropping what the agents said would erase
    // the separation the two spines exist to keep.
    const p = statusParts({ status: "VALID", agent_status: "INVALID", disposition: "override" });
    assert.equal(p.label, "Conceded");
    assert.equal(p.superseded, "was INVALID");
    assert.equal(p.badge, "override");
  });

  it("says nothing was superseded when the analyst agreed", () => {
    const p = statusParts({ status: "INVALID", agent_status: "INVALID", disposition: "accept" });
    assert.equal(p.superseded, null);
    assert.equal(p.badge, "accept");
  });

  it("survives the partial claim a live run re-renders from", () => {
    // setRowStatus passes `{status}` alone — no disposition, no agent verdict to compare.
    const p = statusParts({ status: "INVALID" });
    assert.equal(p.superseded, null);
    assert.equal(p.badge, null);
    assert.equal(p.glyph, "+");
  });

  it("carries a tone and glyph for a claim with no verdict at all", () => {
    const p = statusParts({ status: "unresolved" });
    assert.equal(p.tone, "neutral");
    assert.equal(p.label, "Not investigated");
  });
});

describe("decisionSummary / dispositionLabel", () => {
  it("returns nothing when no decision has been recorded", () => {
    assert.equal(decisionSummary({ claim_id: "CLM-1" }), null);
    assert.equal(decisionSummary(null), null);
  });

  it("names what the agents said when the analyst disagreed", () => {
    const s = decisionSummary({ disposition: "override", decided_verdict: "VALID",
                               agent_status: "INVALID", decided_at: "2026-07-28T06:22:31.101" });
    assert.equal(s.text, "Your decision: override → VALID (agents said INVALID) on 2026-07-28 06:22");
  });

  it("shares its sentence with the just-saved confirmation", () => {
    // The two used to be written independently 60 lines apart.
    assert.equal(dispositionLabel("accept", "INVALID"), "accept → INVALID");
    assert.match(decisionSummary({ disposition: "accept", decided_verdict: "INVALID" }).text,
                 /accept → INVALID/);
  });

  it("degrades through the pre-snapshot columns rather than reading blank", () => {
    // Layer 34 added decided_verdict; older override rows only have override_verdict.
    assert.match(decisionSummary({ disposition: "override", override_verdict: "VALID",
                                   agent_status: "INVALID" }).text, /override → VALID/);
    assert.match(decisionSummary({ disposition: "accept", agent_status: "INVALID" }).text,
                 /accept → INVALID/);
  });

  it("reports staleness and the note without formatting them in", () => {
    const s = decisionSummary({ disposition: "accept", decided_verdict: "VALID",
                               decision_stale: 1, note: "carrier signed" });
    assert.equal(s.stale, true);
    assert.equal(s.note, "carrier signed");
    assert.equal(decisionSummary({ disposition: "accept", decided_verdict: "VALID" }).note, null);
  });

  it("omits the timestamp when there isn't one", () => {
    assert.equal(decisionSummary({ disposition: "accept", decided_verdict: "VALID" }).text,
                 "Your decision: accept → VALID");
  });
});

describe("overrideGuard", () => {
  it("refuses an override to the agents' own verdict — that is an accept", () => {
    // Recording agreement as a disagreement would be false, and the server 422s it too.
    const g = overrideGuard({ chosen: "INVALID", reason: "because", agentVerdict: "INVALID" });
    assert.equal(g.disabled, true);
    assert.match(g.title, /already said INVALID/);
  });

  it("needs both a verdict and a stated reason", () => {
    assert.equal(overrideGuard({ chosen: "", reason: "", agentVerdict: "INVALID" }).disabled, true);
    assert.equal(overrideGuard({ chosen: "VALID", reason: "", agentVerdict: "INVALID" }).disabled, true);
    assert.equal(overrideGuard({ chosen: "", reason: "why", agentVerdict: "INVALID" }).disabled, true);
  });

  it("treats whitespace as no reason at all", () => {
    assert.equal(overrideGuard({ chosen: "VALID", reason: "   ", agentVerdict: "INVALID" }).disabled, true);
  });

  it("names the missing half, so the analyst isn't left guessing", () => {
    assert.match(overrideGuard({ chosen: "", reason: "why" }).title, /Choose the verdict/);
    assert.match(overrideGuard({ chosen: "VALID", reason: "" }).title, /needs a stated reason/);
  });

  it("enables and says nothing once the override is legitimate", () => {
    assert.deepEqual(overrideGuard({ chosen: "VALID", reason: "the BOL shows otherwise",
                                     agentVerdict: "INVALID" }), { disabled: false, title: "" });
  });

  it("survives being called with nothing", () => {
    assert.equal(overrideGuard().disabled, true);
    assert.equal(overrideGuard({}).disabled, true);
  });
});

describe("runHistoryLine / currentRun", () => {
  const runs = [
    { run_id: "20260728T062104Z", timestamp: "2026-07-28T06:21:50Z", final_verdict: "INVALID" },
    { run_id: "20260727T012241Z", timestamp: "2026-07-27T01:23:17Z", final_verdict: "VALID" },
  ];

  it("says nothing at all for a single run", () => {
    // 46 of 50 claims on the real lot have exactly one; "1 run" would be noise on 92% of them.
    assert.equal(runHistoryLine([runs[0]], runs[0].run_id), null);
    assert.equal(runHistoryLine([], null), null);
    assert.equal(runHistoryLine(null, null), null);
  });

  it("names the count, marks the current run, and reads newest-first", () => {
    assert.equal(runHistoryLine(runs, "20260728T062104Z"),
      "2 runs · 07-28 Disputable (current) ← 07-27 Conceded");
  });

  it("uses MM-DD, because the line lives in a sticky header", () => {
    // Full ISO dates wrapped the header to two lines on the real six-run claim, costing 65px of
    // evidence pane permanently. Measured, not guessed.
    assert.match(runHistoryLine(runs, runs[0].run_id), /^2 runs · 07-28/);
  });

  it("falls back to the run id when a crashed run wrote no timestamp", () => {
    const crashed = [{ run_id: "20260729T000000Z", final_verdict: null }, runs[0]];
    assert.match(runHistoryLine(crashed, runs[0].run_id), /Not investigated/);
  });

  it("finds the run latest points at, not merely the first", () => {
    assert.equal(currentRun(runs, "20260727T012241Z").final_verdict, "VALID");
    assert.equal(currentRun(runs, "gone").run_id, runs[0].run_id, "falls back to newest");
    assert.equal(currentRun([], "x"), null);
  });
});

describe("usageLine", () => {
  it("reports both agents' spend", () => {
    assert.equal(
      usageLine({ investigator: { prompt_tokens: 12536, completion_tokens: 1653 },
                  reviewer: { prompt_tokens: 14515, completion_tokens: 1613 } }),
      "tokens — investigator: 12536 in / 1653 out · reviewer: 14515 in / 1613 out");
  });

  it("survives a run that recorded only one agent", () => {
    // The old inline version read four levels deep with no guard and threw inside a catch that
    // swallowed it — taking the whole run-history line down with it.
    assert.equal(usageLine({ investigator: { prompt_tokens: 1, completion_tokens: 2 } }),
                 "tokens — investigator: 1 in / 2 out");
  });

  it("says nothing when there is no usage at all", () => {
    assert.equal(usageLine(null), "");
    assert.equal(usageLine({}), "");
  });
});

describe("isFieldNode", () => {
  it("recognises the nodes that own a keystroke", () => {
    // The one untested input to the already-tested keymap: every shortcut is a bare letter, so getting
    // this wrong makes the search box unusable.
    for (const tagName of ["INPUT", "TEXTAREA", "SELECT"]) {
      assert.equal(isFieldNode({ tagName }), true, tagName);
    }
    assert.equal(isFieldNode({ tagName: "DIV", isContentEditable: true }), true);
  });

  it("leaves ordinary nodes to the shortcuts", () => {
    assert.equal(isFieldNode({ tagName: "BODY" }), false);
    assert.equal(isFieldNode({ tagName: "BUTTON" }), false);
    assert.equal(isFieldNode({ tagName: "TD" }), false);
  });

  it("survives no active element", () => {
    assert.equal(isFieldNode(null), false);
    assert.equal(isFieldNode(undefined), false);
  });
});

describe("splitFrames", () => {
  it("returns complete frames and keeps the incomplete tail", () => {
    // A chunk boundary lands mid-frame constantly; returning the tail as a frame truncates JSON.
    const { frames, rest } = splitFrames("event: a\ndata: 1\n\nevent: b\ndata: 2\n\nevent: c\ndata:");
    assert.deepEqual(frames, ["event: a\ndata: 1", "event: b\ndata: 2"]);
    assert.equal(rest, "event: c\ndata:");
  });

  it("has no frames yet when nothing is terminated", () => {
    assert.deepEqual(splitFrames("event: a\ndata: {\"partial\""), { frames: [], rest: 'event: a\ndata: {"partial"' });
  });

  it("leaves an empty tail once the buffer ends on a boundary", () => {
    assert.deepEqual(splitFrames("event: a\ndata: 1\n\n"), { frames: ["event: a\ndata: 1"], rest: "" });
  });

  it("survives an empty buffer", () => {
    assert.deepEqual(splitFrames(""), { frames: [], rest: "" });
    assert.deepEqual(splitFrames(null), { frames: [], rest: "" });
  });
});

describe("parseFrame", () => {
  it("reads the event name and its payload", () => {
    assert.deepEqual(parseFrame('event: claim_done\ndata: {"claim_id": "CLM-002"}'),
                     { event: "claim_done", data: '{"claim_id": "CLM-002"}' });
  });

  it("joins multiple data lines instead of keeping only the last", () => {
    // THE bug this extraction found: the inline version assigned rather than accumulated, so any
    // payload containing a newline was silently truncated to its final line.
    assert.equal(parseFrame("event: x\ndata: {\ndata:   \"a\": 1\ndata: }").data, '{\n  "a": 1\n}');
  });

  it("is not defeated by CRLF framing", () => {
    assert.deepEqual(parseFrame("event: done\r\ndata: {}\r"), { event: "done", data: "{}" });
  });

  it("strips exactly one space after the colon, per the wire format", () => {
    assert.equal(parseFrame("event: x\ndata:  leading").data, " leading");
    assert.equal(parseFrame("event: x\ndata:no space").data, "no space");
  });

  it("reports a frame with no data, rather than inventing some", () => {
    assert.deepEqual(parseFrame("event: batch_done"), { event: "batch_done", data: null });
  });

  it("reports a nameless frame honestly and lets the caller decide", () => {
    assert.deepEqual(parseFrame("data: 1"), { event: null, data: "1" });
    assert.deepEqual(parseFrame(""), { event: null, data: null });
  });

  it("ignores a comment or an unknown field", () => {
    assert.deepEqual(parseFrame(": keep-alive\nid: 7\nevent: x\ndata: 1"), { event: "x", data: "1" });
  });
});

// --- Layer 40: running the lot ---------------------------------------------------------------------

describe("usageTokens", () => {
  it("adds up both agents", () => {
    assert.equal(usageTokens({
      investigator: { prompt_tokens: 1000, completion_tokens: 200 },
      reviewer: { prompt_tokens: 3000, completion_tokens: 400 },
    }), 4600);
  });

  it("survives a run that recorded usage for only one agent", () => {
    // The exact shape that broke usageLine before it was guarded: real, on disk, and it used to
    // throw four levels deep inside a catch that swallowed it.
    assert.equal(usageTokens({ investigator: { prompt_tokens: 40, completion_tokens: 10 } }), 50);
    assert.equal(usageTokens({ reviewer: null, investigator: { prompt_tokens: 7 } }), 7);
  });

  it("returns 0 rather than throwing on nothing at all", () => {
    for (const bad of [null, undefined, {}, "nope", 5, { investigator: "x" }]) {
      assert.equal(usageTokens(bad), 0);
    }
  });

  it("ignores a non-numeric token count instead of coercing it", () => {
    assert.equal(usageTokens({ investigator: { prompt_tokens: "1000", completion_tokens: 5 } }), 5);
  });
});

describe("runConfirmMessage", () => {
  const EST = { claims: 12, median_tokens_per_claim: 46213, runs_measured: 57 };

  it("names the claim count and the measured spend, per claim and in total", () => {
    const msg = runConfirmMessage(EST);
    assert.match(msg, /Investigate 12 claims\?/);
    assert.match(msg, /about 46k tokens per claim \(median of 57 past runs\)/);
    assert.match(msg, /roughly 555k tokens/);  // 12 × 46,213 = 554,556
  });

  it("says there is no measurement rather than inventing one", () => {
    // The same rule that keeps a fabricated ETA off this screen. A number nobody measured is worse
    // than an admitted unknown here, because the analyst spends against it.
    const msg = runConfirmMessage({ claims: 12, median_tokens_per_claim: null, runs_measured: 0 });
    assert.match(msg, /Investigate 12 claims\?/);
    assert.match(msg, /no past runs on disk to estimate the token spend from/);
    assert.ok(!/tokens per claim/.test(msg));
  });

  it("returns null when there is nothing to run, so the caller does not confirm an empty run", () => {
    assert.equal(runConfirmMessage({ claims: 0, median_tokens_per_claim: 1000, runs_measured: 3 }), null);
    assert.equal(runConfirmMessage({}), null);
    assert.equal(runConfirmMessage(), null);
  });

  it("counts in the singular where it should", () => {
    const msg = runConfirmMessage({ claims: 1, median_tokens_per_claim: 2000, runs_measured: 1 });
    assert.match(msg, /Investigate 1 claim\?/);
    assert.match(msg, /median of 1 past run\)/);
  });
});

describe("runProgressLine", () => {
  it("counts the claim in flight against the lot total", () => {
    assert.equal(runProgressLine({ total: 50, completed: 6, failed: 0, current: "CLM-007" }),
      "Investigating CLM-007 · 7 of 50");
  });

  it("counts failures towards the position, so the counter cannot fall behind the lot", () => {
    // A counter that skipped failures would read "48 of 50" at the end of a lot where two claims
    // failed — a progress bar that never arrives.
    assert.equal(runProgressLine({ total: 50, completed: 5, failed: 1, current: "CLM-007" }),
      "Investigating CLM-007 · 7 of 50 · 1 failed");
  });

  it("has something to say before the first claim reports", () => {
    assert.equal(runProgressLine({ total: 50 }), "Starting — 0 of 50");
    assert.equal(runProgressLine({}), "Starting…");
  });

  it("never counts past the total", () => {
    assert.equal(runProgressLine({ total: 2, completed: 2, failed: 3, current: "CLM-9" }),
      "Investigating CLM-9 · 2 of 2 · 3 failed");
  });
});

describe("batchSummaryLine", () => {
  it("reports a clean run", () => {
    assert.equal(
      batchSummaryLine({ ending: "done", investigated: 48, escalated: 6, tokens: 2_200_000 }),
      "Done: 48 investigated · 6 need your call · 2.2M tokens");
  });

  it("omits every zero rather than printing it", () => {
    assert.equal(batchSummaryLine({ investigated: 3 }), "Done: 3 investigated");
  });

  it("does not call a cancelled run done", () => {
    // The lot was not processed; saying "Done" would report work that was never attempted.
    // Naming the in-flight claim matters: cancel stops at the next claim BOUNDARY, so one claim is
    // still running and will be saved. "Cancelled: 4 investigated" alone reads as "nothing more
    // happened" to an analyst whose queue gains a fifth row half a minute later.
    assert.equal(
      batchSummaryLine({ ending: "cancelled", investigated: 4, tokens: 187_000 }),
      "Cancelled: 4 investigated · 187k tokens. Any claim already running will finish and be "
      + "saved; the rest of the lot was not run.");
  });

  it("says a circuit-broken run stopped, and what to do about it", () => {
    const line = batchSummaryLine({ ending: "consecutive_failures", investigated: 0, failed: 3 });
    assert.match(line, /^Stopped after 3 failures in a row: 0 investigated · 3 failed\./);
    assert.match(line, /Check the API key or the connection/);
  });

  it("still reports the spend of a run that failed, because it was still spent", () => {
    assert.match(batchSummaryLine({ ending: "cancelled", investigated: 2, failed: 1, tokens: 90_000 }),
      /2 investigated · 1 failed · 90k tokens/);
  });
});
