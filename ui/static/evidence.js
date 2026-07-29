// The review pane's agent-derived evidence: verdict header, reconciliation, timeline, reviewer checks,
// reasoning, dispute grounds, and the audit drawer's trace.

import {
  confidenceBand, discrepancyPhrase, reviewChecks, safeClass, sentenceCase, timelineGaps,
  usageLine, verdictLabel
} from "./lib.js";
import { $, el } from "./dom.js";

const EVIDENCE_BLOCKS = ["w-recon-block", "w-timeline-block", "w-checks-block"];

export function setTraceEmpty(message) {
  $("trace-empty").textContent = message;
  $("trace-empty").classList.remove("hidden");
}

export function renderUsage(u) {
  $("usage").textContent = usageLine(u);
}

export function hideAgentEvidence() {
  EVIDENCE_BLOCKS.forEach((id) => $(id).classList.add("hidden"));
  $("w-uom").classList.add("hidden");
  $("w-context-block").classList.add("hidden");
  $("w-dispute-block").classList.add("hidden");
  // Without this the previous claim's reasoning survives onto an un-investigated one — the Layer 33
  // stale-documents bug, in the block Layer 39 added.
  $("w-why-block").classList.add("hidden");
  // `w-runs` is deliberately NOT hidden here. It is written by loadRuns, which is dispatched without
  // being awaited, so hiding it after the awaited /casefile fetch was a race: on a claim whose latest
  // run has no case_file.json this function runs on the else branch and could clobber a history that
  // had legitimately arrived. Whether a 2-run claim showed its history was a coin flip — caught by
  // diffing two runs of the same code. Selection resets it instead, and loadRuns alone reveals it.
}

/** The agents' own prose. Each half hides on its own: a run may carry one reasoning and not the other,
    and an empty labelled box reads as a failed load rather than as an absent field. */
function renderWhy(investigator, reviewer) {
  const halves = [["w-why-inv", investigator], ["w-why-rev", reviewer]];
  let any = false;
  for (const [id, text] of halves) {
    const present = Boolean(text && String(text).trim());
    $(id).classList.toggle("hidden", !present);
    if (present) $(`${id}-body`).textContent = text;
    any = any || present;
  }
  $("w-why-block").classList.toggle("hidden", !any);
}

export function showDisputeDownloadOnly(claimId) {
  $("w-dispute-block").classList.remove("hidden");
  $("w-grounds").replaceChildren(
    el("li", "muted", "Grounds not stored for this older run — re-investigate to regenerate."));
  $("w-download").onclick = () => window.open(`/api/claims/${encodeURIComponent(claimId)}/dispute-packet`, "_blank");
}

export function renderVerdictHeader(ev) {
  const v = verdictLabel(ev.final_verdict);
  const chip = $("w-final");
  chip.className = "verdict-chip t-" + v.tone;
  chip.title = v.blurb;
  chip.replaceChildren(el("span", "v-glyph", v.glyph), el("span", null, v.label));
  if (v.tone !== "neutral") chip.appendChild(el("span", "v-code", v.verdict));

  const conf = confidenceBand(ev.confidence);
  if (conf) {
    $("w-conf-wrap").classList.remove("hidden");
    const bar = $("w-conf-bar");
    bar.style.width = conf.pct + "%";
    bar.className = "c-" + conf.band;
    $("w-conf-meter").setAttribute("aria-valuenow", String(conf.pct));
    $("w-conf-txt").textContent = `${conf.band} confidence (${conf.pct}%)`;
  } else {
    $("w-conf-wrap").classList.add("hidden");
  }

  const prov = $("w-provenance");
  if (ev.investigator_verdict && ev.reviewer_verdict) {
    // The provenance chain keeps the machine words: it records what each agent literally said, and
    // the Reviewer's CONFIRM/OVERTURN is an action, not a money direction — only ESCALATE is toned.
    const proposed = verdictLabel(ev.investigator_verdict);
    prov.replaceChildren(
      document.createTextNode("Investigator proposed "),
      el("span", `v t-${proposed.tone}`, ev.investigator_verdict),
      document.createTextNode(" → Reviewer "),
      el("span", ev.reviewer_verdict === "ESCALATE" ? "v t-warn" : "v", ev.reviewer_verdict),
    );
  } else { prov.textContent = ""; }
}

function renderRecon(po, discQty, discCents) {
  const rows = [
    ["Ordered", po.ordered_qty_each], ["Shipped", po.shipped_qty_each],
    ["Received", po.received_qty_each], ["Invoiced", po.invoiced_qty_each],
  ];
  const body = el("tbody");
  for (const [label, v] of rows) {
    const tr = el("tr");
    tr.appendChild(el("td", null, label));
    tr.appendChild(el("td", "num", v));
    body.appendChild(tr);
  }
  // Not "0 EACH · $0.00" in plain text and anything else in red: a zero discrepancy IS the grounds
  // for disputing, and a real shortage is a finding rather than an error.
  const phrase = discrepancyPhrase(discQty, discCents);
  const disc = el("tr", "disc");
  disc.appendChild(el("td", null, "Discrepancy"));
  disc.appendChild(el("td", `num t-${phrase.tone}`, phrase.text));
  body.appendChild(disc);
  $("w-recon").replaceChildren(body);
}

export function renderEvidence(claimId, ev) {
  renderVerdictHeader(ev);

  EVIDENCE_BLOCKS.forEach((id) => $(id).classList.remove("hidden"));
  renderRecon(ev.po_summary, ev.discrepancy_qty, ev.discrepancy_amount_cents);

  const uom = ev.uom_conversions_applied || [];
  if (uom.length) { $("w-uom").classList.remove("hidden"); $("w-uom-body").textContent = uom.join("; "); }
  else $("w-uom").classList.add("hidden");

  // The interval between events goes BETWEEN the chips, in the agent's own order — see timelineGaps
  // for why this list is never sorted.
  const tl = $("w-timeline"); tl.replaceChildren();
  for (const e of timelineGaps(ev.timeline)) {
    if (e.gap) tl.appendChild(el("span", "tl-gap" + (e.gapDays < 0 ? " back" : ""), e.gap));
    const div = el("div", "tl-event" + (e.valid ? "" : " invalid"));
    div.appendChild(el("span", "e", sentenceCase(e.event)));
    div.appendChild(el("span", "d", e.date));
    tl.appendChild(div);
  }

  const checks = $("w-checks"); checks.replaceChildren();
  for (const c of reviewChecks(ev.review_findings)) {
    const row = el("div", "check-row");
    row.appendChild(el("span", "check " + safeClass(c.status), c.status));
    row.appendChild(el("span", "label", c.label));
    row.appendChild(el("span", "desc", c.description));
    checks.appendChild(row);
  }

  const chips = [];
  if (ev.trade_agreement_found) chips.push("Trade agreement found");
  if ((ev.prior_claims || []).length) chips.push("Prior claims: " + ev.prior_claims.join(", "));
  if (chips.length) {
    $("w-context-block").classList.remove("hidden");
    $("w-context").replaceChildren(...chips.map((c) => el("span", "chip", c)));
  } else $("w-context-block").classList.add("hidden");

  // Deliberately NOT in EVIDENCE_BLOCKS: that list is unhidden unconditionally, and both reasoning
  // fields default to "" (a model that omits them still validates), which would leave a bare
  // "Why this verdict" heading over nothing. Each agent's half hides independently.
  renderWhy(ev.investigator_reasoning, ev.reviewer_reasoning);

  const grounds = ev.dispute_grounds || [];
  if (ev.final_verdict === "INVALID" && grounds.length) {
    $("w-dispute-block").classList.remove("hidden");
    $("w-grounds").replaceChildren(...grounds.map((g) => el("li", null, g)));
    $("w-download").onclick = () => window.open(`/api/claims/${encodeURIComponent(claimId)}/dispute-packet`, "_blank");
  } else $("w-dispute-block").classList.add("hidden");

  if (ev.usage) renderUsage(ev.usage);
}

/** One row in the audit drawer's tool trace. Shared by the live SSE run and the stored trace a past
    run loads on demand, so both render identically. */
export function appendTrace(d) {
  const li = el("li", d.is_error ? "error" : null);
  li.appendChild(el("span", `agent-tag agent-${d.agent}`, d.agent));
  li.appendChild(el("span", null, d.name));
  li.appendChild(el("span", "tool-args", " " + JSON.stringify(d.args)));
  $("trace").appendChild(li);
  $("trace-empty").classList.add("hidden");
}
