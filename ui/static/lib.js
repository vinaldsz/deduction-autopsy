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
