// Pure helpers for the analyst workspace. No DOM, no fetch, no module state.
//
// This file exists so the parts of the UI that are real logic can be tested (tests/js/lib.test.mjs
// under `node --test`, zero dependencies). Anything that touches document/window belongs in app.js,
// which stays untested by design — keeping that boundary is the whole point, so resist adding a
// DOM helper here just because it's convenient.

const USD = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

/** Cents -> "$1,234.56". Grouped: an analyst reading a ledger column scans by thousands separator. */
export function dollars(cents) {
  if (cents == null || Number.isNaN(Number(cents))) return "—";
  return USD.format(Number(cents) / 100);
}

/** Cents -> "$12.5k" for KPI cards, where full cents are noise rather than information. */
export function dollarsCompact(cents) {
  if (cents == null || Number.isNaN(Number(cents))) return "—";
  const value = Number(cents) / 100;
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(1)}k`;
  return USD.format(value);
}

/** "12 not investigated · 3 awaiting your call" — the two halves of the to-do queue, spelled out
    under the one number that matters. Omits a half that is zero rather than printing "0 …". */
export function todoSplit(metrics) {
  const parts = [];
  if (metrics.not_investigated_count) parts.push(`${metrics.not_investigated_count} not investigated`);
  if (metrics.awaiting_my_call_count) parts.push(`${metrics.awaiting_my_call_count} awaiting your call`);
  return parts.join(" · ");
}

// --- verdict semantics ----------------------------------------------------------------------------
//
// The machine verdicts are written from the retailer's side of the deduction, so read literally they
// invert the money: "VALID" means the retailer's claim holds and we concede the amount, "INVALID"
// means it doesn't hold and the amount is recoverable. The old palette compounded that by painting
// VALID green and INVALID red — telling the analyst the opposite of the financial outcome in both
// word and colour.
//
// Colour therefore no longer keys off the verdict name. It keys off `tone`, which is defined here and
// nowhere else; CSS knows only pos/neg/warn/neutral. `glyph` is not decoration — it is what carries
// the money direction in greyscale and to a colour-blind reader.
const VERDICTS = {
  INVALID: {
    label: "Disputable", tone: "pos", glyph: "+",
    blurb: "The documents don't support the retailer's deduction — disputable, and the amount is recoverable.",
  },
  VALID: {
    label: "Conceded", tone: "neg", glyph: "−",
    blurb: "The documents support the retailer's deduction — the amount stays deducted.",
  },
  ESCALATE: {
    label: "Your call", tone: "warn", glyph: "?",
    blurb: "The agents couldn't resolve this one — decide it from the source documents.",
  },
};

const NOT_INVESTIGATED = {
  label: "Not investigated", tone: "neutral", glyph: "·",
  blurb: "No agent run yet — the source documents are still available to read.",
};

/** A verdict -> what it means for the money: { verdict, label, tone, glyph, blurb }.
 *
 *  An unrecognised value degrades to `neutral` and keeps its own text rather than being bucketed:
 *  guessing a money direction for a verdict we don't know is worse than admitting we don't know it.
 *  Callers render the raw `verdict` alongside `label` only when `tone !== "neutral"` — for the
 *  neutral cases the label already *is* the raw word. */
export function verdictLabel(verdict) {
  // hasOwn, not a truthiness test: `VERDICTS["constructor"]` inherits from Object.prototype and is
  // truthy, which would spread into a chip with no label, tone or glyph at all.
  if (verdict != null && Object.hasOwn(VERDICTS, verdict)) return { verdict, ...VERDICTS[verdict] };
  if (verdict == null || verdict === "" || verdict === "unresolved") {
    return { verdict: "unresolved", ...NOT_INVESTIGATED };
  }
  return { verdict, ...NOT_INVESTIGATED, label: String(verdict) };
}

/** The Reviewer's stated confidence as { pct, band }, or null when there is none to show.
 *
 *  Clamped: the value is a model self-report, and a stray 1.3 would render a meter fill wider than
 *  its own track. Deliberately carries no `tone` — in this file a tone is a *money* direction, and
 *  painting high confidence green would overload the token that now means "recoverable". */
export function confidenceBand(confidence) {
  if (confidence == null || Number.isNaN(Number(confidence))) return null;
  const c = Math.min(1, Math.max(0, Number(confidence)));
  const band = c >= 0.9 ? "High" : c >= 0.7 ? "Moderate" : "Low";
  return { pct: Math.round(c * 100), band };
}

/** The reconciliation discrepancy, stated as a direction and in whose favour it runs.
 *
 *  Scoped to the quantity arithmetic on purpose: a real shortage does not imply the claim is VALID
 *  (s04 has a genuine shortage and an INVALID verdict, on the timeline), so this says the numbers
 *  favour the retailer's *claim* — not that the retailer wins. */
export function discrepancyPhrase(qty, cents) {
  if (qty == null || Number.isNaN(Number(qty))) return { text: "—", tone: "neutral" };
  const q = Number(qty);
  const money = dollars(Math.abs(Number(cents) || 0));
  if (q > 0) return { text: `${q} EACH short · ${money} — favours the retailer's claim`, tone: "neg" };
  if (q < 0) return { text: `${Math.abs(q)} EACH over-shipped · ${money} — favours us`, tone: "pos" };
  // Money against a zero quantity is an inconsistency in the agent's own arithmetic. Show it rather
  // than let the reconciling-exactly sentence paper over it.
  const suffix = Number(cents) ? ` · ${money}` : "";
  return { text: `No quantity discrepancy — the documents reconcile exactly${suffix}`, tone: "neutral" };
}

/** "promo_billback" -> "Promo billback", "order_date" -> "Order date". Sentence case, not title
    case: the rest of the UI reads "Source documents" / "Purchase order", and "Promo Billback" would
    be the one label that doesn't match its own page. */
export function sentenceCase(s) {
  if (!s) return "";
  const words = String(s).replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// --- the worklist grid, and the URL that describes it ----------------------------------------------
//
// These whitelists mirror ui/queries.py's. The two are deliberately enforced in opposite directions:
// the server *rejects* an unrecognised value with 422 (a plausible page of the wrong rows is worse
// than an error), while the client *sanitizes* its own persisted URL down to a default. A stale
// bookmark is the client's own mess, and erroring the whole page over one is not a fix.

export const FILTERS = [
  "todo", "not_investigated", "awaiting_my_call", "disputable", "decided", "all",
];
export const SORTS = ["claim_id", "po_id", "retailer", "amount", "age", "priority"];
export const DIRECTIONS = ["asc", "desc"];
export const PAGE_SIZES = [25, 50, 100];

/** `direction: null` means "let the server pick". Each column has a useful first click (money and
    age descending, ids and names ascending) and that table lives in ui/queries.py — mirroring it
    here would be a second copy free to drift. */
export const DEFAULT_STATE = {
  filter: "todo", sort: "priority", direction: null, q: "", page: 1, size: 25,
  claim: null, retailer: null, reason: null, date_from: null, date_to: null,
};

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const DIGITS = /^\d+$/;

/** A non-negative integer, or null. Strict on purpose: `Number.parseInt` stops at the first
    non-digit, so "2.5e9999" parses as 2 and "25abc" as 25 — leniency in the one function whose job
    is to distrust the URL. */
function wholeNumber(value) {
  return value != null && DIGITS.test(value) ? Number(value) : null;
}

/** An ISO date, or null. Shape alone isn't enough — "2024-13-45" matches the pattern and is not a
    date, and `claim_date` is stored as a string, so the server would compare it lexicographically
    rather than complain. */
function isoDate(value) {
  if (!value || !ISO_DATE.test(value)) return null;
  const parsed = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toISOString().slice(0, 10) === value ? value : null;
}

/** A location.hash -> the worklist state it describes, with every unrecognised value replaced by its
    default. Total by construction: it always returns a complete, usable state. */
export function parseHash(hash) {
  const p = new URLSearchParams(String(hash || "").replace(/^#/, ""));
  const oneOf = (key, allowed, fallback) =>
    (allowed.includes(p.get(key)) ? p.get(key) : fallback);
  const page = wholeNumber(p.get("page"));
  const size = wholeNumber(p.get("size"));
  return {
    filter: oneOf("filter", FILTERS, DEFAULT_STATE.filter),
    sort: oneOf("sort", SORTS, DEFAULT_STATE.sort),
    direction: oneOf("dir", DIRECTIONS, null),
    q: p.get("q") || "",
    page: page && page > 0 ? page : DEFAULT_STATE.page,
    size: PAGE_SIZES.includes(size) ? size : DEFAULT_STATE.size,
    // Free text, deliberately not validated against the lot: an unknown retailer returns no rows,
    // which is the honest answer to "show me claims from a retailer that isn't in this lot".
    claim: p.get("claim") || null,
    retailer: p.get("retailer") || null,
    reason: p.get("reason") || null,
    date_from: isoDate(p.get("from")),
    date_to: isoDate(p.get("to")),
  };
}

/** The inverse: state -> hash, omitting anything at its default so a shared link carries only what
    the analyst actually changed. */
export function buildHash(state) {
  const p = new URLSearchParams();
  const put = (key, value, dflt) => {
    if (value != null && value !== "" && value !== dflt) p.set(key, String(value));
  };
  put("filter", state.filter, DEFAULT_STATE.filter);
  put("sort", state.sort, DEFAULT_STATE.sort);
  put("dir", state.direction, null);
  put("q", state.q, "");
  put("page", state.page, DEFAULT_STATE.page);
  put("size", state.size, DEFAULT_STATE.size);
  put("retailer", state.retailer, null);
  put("reason", state.reason, null);
  put("from", state.date_from, null);
  put("to", state.date_to, null);
  put("claim", state.claim, null);
  const query = p.toString();
  return query ? `#${query}` : "";
}

/** The ▲/▼ on a column header, read from the sort the SERVER reports applying — not from what the
 *  client asked for. `direction` is often null on the way out (meaning "your choice"), so rendering
 *  the request would leave the arrow guessing, and a rejected or defaulted parameter would leave it
 *  pointing at a sort the table isn't in. */
export function sortIndicator(column, appliedSort, appliedDirection) {
  if (!column || column !== appliedSort) return "";
  return appliedDirection === "asc" ? "▲" : "▼";
}

/** The banding rules, in the UI, generated from the server's own constants — so a threshold change
    in ui/queries.py can't leave the page confidently explaining a rule that no longer applies. */
export function priorityLegend(thresholds) {
  if (!thresholds) return "";
  return `HIGH ${dollars(thresholds.high_cents)}+ at risk, or older than ` +
    `${thresholds.age_days} days · MEDIUM ${dollars(thresholds.med_cents)}+ · LOW everything else`;
}

/** Days -> "239d". An age column is scanned down, not read across; the word belongs in the header. */
export function ageLabel(days) {
  if (days == null || Number.isNaN(Number(days))) return "—";
  return `${Number(days)}d`;
}

/** The table footer: how many claims match, and what they add up to.
 *
 *  Says "filtered" whenever a narrowing is active, because the number is over the filtered set and
 *  not the lot — a total that quietly means something different depending on the tab is the same
 *  class of defect as a KPI that doesn't equal its own rows. */
export function queueFooter(total, cents, filtered) {
  const noun = total === 1 ? "claim" : "claims";
  return { label: `${total} ${noun}${filtered ? ", filtered" : ""}`, amount: dollars(cents) };
}

// --- working the volume: keyboard, advance, bulk ---------------------------------------------------

/** A keystroke -> the action it means, or null for "not ours".
 *
 *  Pure so the keymap is testable at all: `app.js` only supplies `{key, modifiers, inField}` from the
 *  real event. Two rules carry the weight —
 *
 *   1. While the caret is in a field NOTHING is a shortcut except Escape. Every binding here is a
 *      bare letter, so without this typing "jab" into the search box would walk the queue and record
 *      a decision.
 *   2. A held modifier means the key belongs to the browser or the OS. Cmd+A is select-all, not
 *      accept, and stealing it would be worse than having no shortcut at all.
 *
 *  There is deliberately no key that *submits* an override: an override needs a verdict and a stated
 *  reason, so `o` only moves focus to the picker. `docs/PLAN.md`'s `s` (send to human) has no binding
 *  — Layer 32 removed that control because the analyst is the human it would have sent to. */
export function keyAction({ key, ctrlKey, metaKey, altKey, shiftKey, inField } = {}) {
  if (key === "Escape") return inField ? "blur" : null;
  if (inField) return null;
  if (ctrlKey || metaKey || altKey || shiftKey) return null;
  switch (key) {
    case "j": return "next";
    case "k": return "prev";
    case "a": return "accept";
    case "o": return "override";
    case "x": return "toggle-select";
    case "/": return "search";
    default: return null;
  }
}

/** The id `delta` places along `ids` from `current`, or null.
 *
 *  Does not wrap, on purpose: after deciding the last claim on a page, jumping silently back to row 1
 *  is indistinguishable from a reload bug. An unknown `current` (its row just left the filter, which
 *  is the normal outcome of deciding it) starts from the top for a forward move, so the analyst lands
 *  on work rather than on nothing. */
export function nextClaimId(ids, current, delta = 1) {
  const list = ids || [];
  const at = list.indexOf(current);
  if (at === -1) return delta > 0 ? (list[0] ?? null) : null;
  return list[at + delta] ?? null;
}

// What each bulk outcome means to the analyst. Lives here rather than in app.js for the same reason
// VERDICTS does: it is the vocabulary on screen, and a second copy would drift from the writer's.
// Keys mirror orchestrator/dispositions.py's outcome constants.
const OUTCOME_PHRASES = {
  recorded: "accepted",
  unknown_claim: "not in this lot",
  not_investigated: "not investigated",
  unresolved_verdict: "still escalated — decide these yourself",
  already_decided: "already decided",
};

/** {claim_id: outcome} -> "12 accepted · 2 not investigated · 1 already decided".
 *
 *  Empty buckets are omitted, `recorded` leads (it is the answer to "did my click work"), and an
 *  outcome this file doesn't recognise is still counted, under its raw key. Dropping it would make
 *  the sentence disagree with the number of claims the analyst selected — a summary that quietly
 *  loses a claim is worse than one that shows an ugly word. */
export function bulkOutcomeSummary(results) {
  const counts = new Map();
  for (const outcome of Object.values(results || {})) {
    counts.set(outcome, (counts.get(outcome) || 0) + 1);
  }
  if (!counts.size) return "";
  const order = [...counts.keys()].sort((a, b) => (a === "recorded" ? -1 : b === "recorded" ? 1 : 0));
  return order
    .map((key) => `${counts.get(key)} ${OUTCOME_PHRASES[key] ?? key}`)
    .join(" · ");
}

// --- Layer 39: explainability ----------------------------------------------------------------------
//
// What each reviewer check actually tested. Derived from agents/reviewer.py's spot-check instructions
// (lines 36-74) rather than invented here, so the screen describes the review that was really run; the
// prompt is the source of truth and this is a reading of it. Keys and their ORDER mirror
// orchestrator/pipeline.py's ReviewFindings, whose field order is load-bearing precisely because these
// render in it.
//
// Deliberately no `tone`. In this file a tone is a *money* direction, and PASS is not money: on
// CLM-002 `timeline_check: FAIL` is exactly what makes the claim disputable, so painting PASS green
// would invert Layer 36 on the very case Layer 36 was built for. The literal word PASS/FAIL/N/A stays
// in the row, which is what survives greyscale.
const CHECKS = {
  uom_check: {
    label: "Unit of measure",
    description: "Recomputed every conversion the Investigator applied, rather than trusting a stated factor.",
  },
  split_shipment_check: {
    label: "Split shipment",
    description: "Re-fetched the PO's ASNs to confirm none was missed — one delivery can span several.",
  },
  timeline_check: {
    label: "Timeline sequence",
    description: "Re-derived order → ship → receipt → invoice → claim from the raw dates. An out-of-order pair is physically impossible, and is grounds to dispute on its own however cleanly the quantities reconcile.",
  },
  trade_agreement_check: {
    label: "Trade agreement",
    description: "Re-looked-up the claim's promo code to confirm the agreement truly matches it.",
  },
  duplicate_check: {
    label: "Duplicate claim",
    description: "Re-listed the other claims on this PO to see whether this deduction was already taken and settled.",
  },
  substitution_check: {
    label: "Substitution",
    description: "Whether the SKU shipped differs from the SKU ordered, and whether the receiving notes show it was pre-approved.",
  },
  data_completeness_check: {
    label: "Data completeness",
    description: "The odd one out: never a dispute ground, and not the Reviewer's own finding. It FAILs only when the orchestrator reports source data genuinely missing or an investigation left incomplete — and that forces ESCALATE.",
  },
};

/** ReviewFindings -> one row per check: [{ key, status, label, description }].
 *
 *  Iterates the findings the server sent, so the order on screen is the model's field order rather
 *  than one retyped here. An unrecognised key keeps its row with a generated label and no description:
 *  an eighth check added upstream must still show its status, and silently dropping it would hide a
 *  FAIL. */
export function reviewChecks(findings) {
  return Object.entries(findings || {}).map(([key, status]) => {
    // hasOwn for the reason verdictLabel documents: CHECKS["constructor"] inherits a truthy function
    // from Object.prototype and would spread a row with no label at all.
    const known = Object.hasOwn(CHECKS, key) ? CHECKS[key] : null;
    return {
      key,
      status: status == null || status === "" ? "N/A" : String(status),
      label: known ? known.label : sentenceCase(key.replace(/_check$/, "")),
      description: known ? known.description : "",
    };
  });
}

/** CaseFile.timeline -> the same events, in the same order, each annotated with the interval since the
 *  previous one: [{ event, date, valid, gapDays, gap }].
 *
 *  The interval is the finding. Five bare dates say nothing, while "+90 days" between receipt and
 *  claim is the whole reason a deduction is late, and a *negative* interval is a sequence violation
 *  standing in plain sight.
 *
 *  Never sorts, on purpose. The Investigator emits this list itself and can emit it out of order —
 *  CLM-002 lists invoice_date 2024-02-04 after receipt_date 2024-02-05, both flagged valid, while the
 *  Reviewer independently recorded timeline_check: FAIL. Quietly reordering that would delete the
 *  visible evidence for the Reviewer's own finding. Reconciling the two is not this function's
 *  business either: showing the disagreement is the deliverable, resolving it would be verdict logic. */
export function timelineGaps(events) {
  const list = events || [];
  let previous = null;
  return list.map((e) => {
    // `TimelineEvent.date` is an unvalidated string, so a bad value must yield no gap rather than
    // "NaN days". isoDate is the same guard parseHash uses on a URL date.
    const date = isoDate(e && e.date);
    const at = date ? Date.parse(`${date}T00:00:00Z`) : null;
    // An unreadable date voids the NEXT event's gap too: an interval that skipped over it would be
    // labelled "since the previous event" while measuring from two events back.
    const gapDays = at != null && previous != null ? Math.round((at - previous) / 86400000) : null;
    previous = at;
    return { event: e && e.event, date: e && e.date, valid: e && e.valid, gapDays, gap: gapLabel(gapDays) };
  });
}

function gapLabel(days) {
  if (days == null) return null;
  if (days === 0) return "same day";
  const unit = Math.abs(days) === 1 ? "day" : "days";
  return days > 0 ? `+${days} ${unit}` : `${Math.abs(days)} ${unit} earlier · out of order`;
}

/** The header subtitle: the state of today's lot, not a description of the architecture. */
export function lotSubtitle(metrics) {
  if (!metrics || !metrics.batch) return "No lot loaded — run the ETL to ingest today's deductions.";
  const parts = [
    metrics.batch.batch_id,
    `${metrics.lot_total} claims`,
    metrics.todo_count ? `${metrics.todo_count} to do` : "all decided",
  ];
  if (metrics.open_amount_cents) parts.push(`${dollarsCompact(metrics.open_amount_cents)} open`);
  if (metrics.oldest_open_days) parts.push(`oldest ${metrics.oldest_open_days}d`);
  if (metrics.batch.status && metrics.batch.status !== "complete") parts.push(metrics.batch.status);
  return parts.join(" · ");
}
