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
