// Analyst workspace over the Layer 30/32 API. DOM + fetch only — pure logic lives in lib.js where
// `node --test` can reach it (tests/js/). No framework, no build step.

import {
  confidenceBand, discrepancyPhrase, dollars, dollarsCompact, lotSubtitle, sentenceCase, todoSplit,
  verdictLabel,
} from "./lib.js";

const LIMIT = 25;
const state = {
  batchId: null, offset: 0, total: 0, filter: "todo", sort: "priority", q: "",
  selected: null, selectedClaim: null,
};

const $ = (id) => document.getElementById(id);
const banner = $("banner");
const safeClass = (v) => (v === "N/A" ? "NA" : v);

/** Build an element. `text` goes in via textContent — never innerHTML, for any value that came from
    the DB or a model. Declared here rather than beside the document builders because the whole file
    now uses it. */
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

// --- errors ---------------------------------------------------------------------------------------

/** A human sentence, never a raw exception string, and a way out. */
function showBanner(msg, retry) {
  $("banner-msg").textContent = msg;
  const retryBtn = $("banner-retry");
  retryBtn.classList.toggle("hidden", !retry);
  retryBtn.onclick = retry ? () => { hideBanner(); retry(); } : null;
  banner.classList.remove("hidden");
}

function hideBanner() { banner.classList.add("hidden"); }

/** fetch + JSON that fails loudly. Previously `resp.ok` was checked in one place and ignored in
    three, which is how a failed request became "the previous claim's data, silently". */
async function fetchJSON(url, opts) {
  const resp = await fetch(url, opts);
  if (!resp.ok) throw new Error(`${resp.status} ${url}`);
  return resp.json();
}

// --- dashboard (KPI strip) -----------------------------------------------------------------------

async function loadDashboard() {
  try {
    const d = await fetchJSON("/api/dashboard");
    $("sub").textContent = lotSubtitle(d);
    // The two cards partition the lot, so each number is exactly the rows its tab returns.
    $("m-todo").textContent = d.todo_count;
    $("m-todo-split").textContent = todoSplit(d);
    $("m-decided").textContent = d.decided_count;
    $("m-decided-of").textContent = `of ${d.lot_total} in the lot`;
    $("m-open").textContent = dollarsCompact(d.open_amount_cents);
    const p = d.priority_breakdown;
    $("m-priority").textContent = `${p.HIGH}/${p.MEDIUM}/${p.LOW}`;
    $("m-oldest").textContent = d.oldest_open_days ? `${d.oldest_open_days}d` : "—";
    state.batchId = d.batch ? d.batch.batch_id : null;
  } catch (err) {
    // Log the real error: the catch also covers render bugs, and without this a
    // TypeError in here is indistinguishable from the network being down.
    console.error("loadDashboard", err);
    showBanner("Couldn't load the dashboard metrics.", loadDashboard);
  }
}

// --- worklist queue ------------------------------------------------------------------------------

/** The status cell: effective verdict, plus what it superseded. Shared with setRowStatus so a live
    run and a page load render the same thing. */
function statusCell(claim) {
  const v = verdictLabel(claim.status);
  const td = el("td", `status t-${v.tone}`);
  // Glyph, not the old coloured dot: a dot carries nothing in greyscale or to a colour-blind reader,
  // and it cost the same width as the mark that actually states the money direction.
  td.appendChild(el("span", "v-glyph", v.glyph));
  td.appendChild(el("span", null, v.label));
  // The raw machine verdict is NOT repeated here — the label is the scannable read, and at 420px the
  // extra word pushed the column past the pane and clipped itself. It lives on the review pane's
  // chip, which is where a single claim gets cross-referenced against the CLI and the API.
  // `status` is the effective verdict, so an override shows the analyst's answer as the claim's
  // answer. The superseded agent verdict stays visible beside it — dropping it would erase the
  // audit trail that keeping the two spines separate exists to preserve.
  if (claim.disposition === "override" && claim.agent_status !== claim.status) {
    td.appendChild(el("span", "status-sup", `was ${claim.agent_status}`));
  }
  if (claim.disposition) td.appendChild(el("span", "disp-badge", claim.disposition));
  return td;
}

function renderRow(claim) {
  const tr = document.createElement("tr");
  tr.dataset.claimId = claim.claim_id;
  tr.tabIndex = 0;
  tr.appendChild(el("td", null, claim.claim_id));
  tr.appendChild(el("td", null, claim.retailer));
  tr.appendChild(el("td", null, sentenceCase(claim.claimed_reason)));
  tr.appendChild(el("td", "num", dollars(claim.claimed_amount)));
  const priority = el("td");
  priority.appendChild(el("span", `pill p-${claim.priority}`, claim.priority));
  tr.appendChild(priority);
  tr.appendChild(statusCell(claim));
  const open = () => selectClaim(claim);
  tr.addEventListener("click", open);
  tr.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
  return tr;
}

/** One message region under the table: no lot at all, or a filter that matched nothing. Without it
    both states rendered as a blank table and "0 of ", which reads as a broken page. */
function setQueueMessage(msg) {
  const node = $("queue-msg");
  node.textContent = msg || "";
  node.classList.toggle("hidden", !msg);
}

async function loadQueue() {
  if (!state.batchId) {
    $("rows").replaceChildren();
    $("page-info").textContent = "";
    setQueueMessage("No lot loaded. Run the ETL (python -m semantic_layer.etl), then reload.");
    return;
  }
  const params = new URLSearchParams({
    offset: state.offset, limit: LIMIT, status_filter: state.filter, sort: state.sort,
  });
  if (state.q) params.set("q", state.q);
  try {
    const data = await fetchJSON(`/api/batches/${encodeURIComponent(state.batchId)}?${params}`);
    state.total = data.total;
    const rows = $("rows");
    rows.replaceChildren();
    for (const claim of data.claims) rows.appendChild(renderRow(claim));
    if (state.selected) markSelectedRow(state.selected);
    // Re-point the open claim at the freshly-loaded row. Without this `state.selectedClaim` keeps
    // whatever it was selected with, so the decision line, the stale badge and the re-investigate
    // confirmation all reason about data that may be several writes out of date.
    const reselected = data.claims.find((c) => c.claim_id === state.selected);
    if (reselected) {
      state.selectedClaim = reselected;
      renderDecision(reselected);
      syncOverrideButton();
    }
    setQueueMessage(data.total ? "" : "No claims match this filter.");
    const shown = data.claims.length ? `${state.offset + 1}–${state.offset + data.claims.length}` : "0";
    $("page-info").textContent = `${shown} of ${data.total}`;
    $("prev").disabled = state.offset === 0;
    $("next").disabled = state.offset + LIMIT >= data.total;
  } catch (err) {
    console.error("loadQueue", err);
    showBanner("Couldn't load the worklist.", loadQueue);
  }
}

function markSelectedRow(claimId) {
  document.querySelectorAll("tr.selected").forEach((tr) => tr.classList.remove("selected"));
  const tr = document.querySelector(`tr[data-claim-id="${claimId}"]`);
  if (tr) tr.classList.add("selected");
}

function setRowStatus(claimId, verdict) {
  const tr = document.querySelector(`tr[data-claim-id="${claimId}"]`);
  if (!tr) return;
  tr.querySelector(".status").replaceWith(statusCell({ status: verdict }));
}

// --- filters / sort / search ---------------------------------------------------------------------

function setFilter(filter) {
  state.filter = filter;
  state.offset = 0;
  document.querySelectorAll("#tabs .tab").forEach((t) => t.classList.toggle("active", t.dataset.filter === filter));
  document.querySelectorAll("#kpis .card[data-filter]").forEach((c) => c.classList.toggle("active", c.dataset.filter === filter));
  loadQueue();
}

// --- claim review workspace ----------------------------------------------------------------------

const EVIDENCE_BLOCKS = ["w-recon-block", "w-timeline-block", "w-checks-block"];
let source = null;

// --- source documents (primary evidence, from the DB — injection-safe DOM building) --------------

function docCard(title, id, body) {
  const card = el("div", "doc");
  const h = el("div", "doc-h");
  h.appendChild(el("span", null, title));
  if (id) h.appendChild(el("span", "doc-id", id));
  card.appendChild(h);
  card.appendChild(body);
  return card;
}

function kvTable(rows) {
  const t = el("table", "doc-t"), tb = el("tbody");
  for (const [k, v, num] of rows) {
    const tr = el("tr");
    tr.appendChild(el("th", null, k));
    tr.appendChild(el("td", num ? "num" : null, v == null ? "—" : String(v)));
    tb.appendChild(tr);
  }
  t.appendChild(tb);
  return t;
}

function rowTable(headers, items, mapFn) {
  const t = el("table", "doc-t"), thead = el("thead"), htr = el("tr");
  for (const h of headers) htr.appendChild(el("th", null, h));
  thead.appendChild(htr); t.appendChild(thead);
  const tb = el("tbody");
  for (const it of items) {
    const tr = el("tr");
    for (const [val, num, note] of mapFn(it)) {
      const cls = num ? "num" : (note ? "note-cell" : null);
      tr.appendChild(el("td", cls, val == null || val === "" ? "—" : String(val)));
    }
    tb.appendChild(tr);
  }
  t.appendChild(tb); return t;
}

function emptyNode(msg) {
  const e = el("div", "doc-empty", msg);
  e.style.padding = "8px 10px";
  return e;
}

function renderReason(claim, notes) {
  const box = $("w-reason"); box.replaceChildren();
  // Reason leads the sentence, so sentenceCase reads correctly without a lowercase round-trip.
  box.appendChild(el("div", "lead",
    `${sentenceCase(claim.claimed_reason)} claimed by ${claim.retailer} for ${dollars(claim.claimed_amount)} · filed ${claim.claim_date}`));
  if (notes) box.appendChild(el("div", "notes", `“${notes}”`));
}

function renderDocuments(docs) {
  const wrap = $("w-docs"); wrap.replaceChildren();
  const po = docs.purchase_order;
  if (po) {
    wrap.appendChild(docCard("Purchase order", po.po_id, kvTable([
      ["SKU", po.sku], ["Ordered", `${po.ordered_qty} ${po.ordered_uom}`],
      ["Unit price", dollars(po.unit_price), true], ["Order date", po.order_date],
    ])));
  }
  wrap.appendChild(docCard(`ASN / shipments (${docs.asns.length})`, null,
    docs.asns.length
      ? rowTable(["ASN", "Shipped", "Ship date", "Carrier"], docs.asns,
          (a) => [[a.asn_id], [`${a.shipped_qty} ${a.shipped_uom}`, true], [a.ship_date], [a.carrier]])
      : emptyNode("No ASNs on file")));
  wrap.appendChild(docCard(`Invoices (${docs.invoices.length})`, null,
    docs.invoices.length
      ? rowTable(["Invoice", "Invoiced", "Amount", "Date"], docs.invoices,
          (i) => [[i.invoice_id], [`${i.invoiced_qty} ${i.invoiced_uom}`, true], [dollars(i.amount), true], [i.invoice_date]])
      : emptyNode("No invoices on file")));
  wrap.appendChild(docCard(`Receiving (${docs.receiving_records.length})`, null,
    docs.receiving_records.length
      ? rowTable(["Receipt", "Received", "Date", "Notes"], docs.receiving_records,
          (r) => [[r.receipt_id], [`${r.received_qty} ${r.received_uom}`, true], [r.receipt_date], [r.notes, false, true]])
      : emptyNode("No receiving records on file")));
  if (docs.trade_agreements.length) {
    wrap.appendChild(docCard(`Trade agreements (${docs.trade_agreements.length})`, null,
      rowTable(["Agreement", "Promo", "Terms", "Valid", "Signed by"], docs.trade_agreements,
        (t) => [[t.agreement_id], [t.promo_code], [t.discount_terms], [`${t.valid_from} – ${t.valid_to}`], [t.signed_by]])));
  }
  if (docs.prior_claims.length) {
    wrap.appendChild(docCard(`Prior claims on this PO (${docs.prior_claims.length})`, null,
      rowTable(["Claim", "Reason", "Amount", "Date", "Verdict"], docs.prior_claims,
        (c) => [[c.claim_id], [sentenceCase(c.claimed_reason)], [dollars(c.claimed_amount), true],
                [c.claim_date], [verdictLabel(c.final_verdict).label]])));
  }
}

function fromCasefile(json, rowStatus) {
  const cf = json.case_file, ro = json.reviewer_output;
  return {
    investigator_verdict: cf.proposed_verdict, reviewer_verdict: ro.final_verdict,
    final_verdict: rowStatus, confidence: ro.confidence, dispute_grounds: ro.dispute_grounds,
    usage: null, po_summary: cf.po_summary, timeline: cf.timeline,
    uom_conversions_applied: cf.uom_conversions_applied, prior_claims: cf.prior_claims,
    trade_agreement_found: cf.trade_agreement_found, discrepancy_qty: cf.discrepancy_qty,
    discrepancy_amount_cents: cf.discrepancy_amount_cents, review_findings: ro.review_findings,
  };
}

function fromDone(p) {
  return { ...p.case_file, investigator_verdict: p.investigator_verdict,
    reviewer_verdict: p.reviewer_verdict, final_verdict: p.final_verdict,
    confidence: p.confidence, dispute_grounds: p.dispute_grounds, usage: p.usage };
}

async function selectClaim(claim) {
  state.selected = claim.claim_id;
  markSelectedRow(claim.claim_id);
  if (source) { source.close(); source = null; }

  $("ws-empty").classList.add("hidden");
  $("ws-body").classList.remove("hidden");
  $("w-claim").textContent = claim.claim_id;
  $("w-meta").textContent = `${claim.retailer} · ${sentenceCase(claim.claimed_reason)} · ${dollars(claim.claimed_amount)} · claimed ${claim.claim_date}`;
  $("trace").replaceChildren();
  $("usage").textContent = "";
  $("w-disp-status").textContent = "";
  $("w-disp-status").classList.remove("err");
  // Clear the override inputs: a reason typed for one claim must not carry over and attach itself
  // to the next one — it would both enable the Override button and be submitted as that claim's
  // justification.
  $("w-note").value = "";
  $("w-override-verdict").value = "";
  state.selectedClaim = claim;
  renderDecision(claim);
  syncOverrideButton();
  const investigated = claim.status !== "unresolved";
  $("w-investigate").textContent = investigated ? "Re-investigate" : "Investigate";
  $("w-investigate").disabled = false;
  setDecisionEnabled(investigated);

  // Source documents + reason are always available from the DB, regardless of agent runs. Cleared
  // before the fetch: leaving the previous claim's documents under a new claim's header is the worst
  // outcome available in a reconciliation tool, and that is exactly what an unhandled failure did.
  $("w-reason").replaceChildren();
  $("w-docs").replaceChildren();
  await loadDocuments(claim);

  // Agent-derived evidence (reconciliation, checks, dispute grounds) exists only once investigated.
  if (claim.status === "unresolved") {
    renderVerdictHeader({ final_verdict: "unresolved" });
    hideAgentEvidence();
    return;
  }
  const resp = await fetch(`/api/claims/${encodeURIComponent(claim.claim_id)}/casefile`);
  if (resp.ok) {
    renderEvidence(claim.claim_id, fromCasefile(await resp.json(), claim.status));
  } else {
    // Resolved in a pre-Layer-32 run: no case_file.json. Still show verdict + a downloadable
    // dispute packet if one was written for an INVALID claim.
    renderVerdictHeader({ final_verdict: claim.status });
    hideAgentEvidence();
    if (claim.status === "INVALID") showDisputeDownloadOnly(claim.claim_id);
  }
}

async function loadDocuments(claim) {
  try {
    const docs = await fetchJSON(`/api/claims/${encodeURIComponent(claim.claim_id)}/documents`);
    renderReason(claim, docs.claim.retailer_notes);
    renderDocuments(docs);
  } catch (err) {
    console.error("loadDocuments", err);
    const box = el("div", "doc-empty", "Couldn't load source documents for this claim. ");
    const retry = el("button", "ghost sm", "Retry");
    retry.onclick = () => loadDocuments(claim);
    box.appendChild(retry);
    $("w-docs").replaceChildren(box);
  }
}

function hideAgentEvidence() {
  EVIDENCE_BLOCKS.forEach((id) => $(id).classList.add("hidden"));
  $("w-uom").classList.add("hidden");
  $("w-context-block").classList.add("hidden");
  $("w-dispute-block").classList.add("hidden");
}

function showDisputeDownloadOnly(claimId) {
  $("w-dispute-block").classList.remove("hidden");
  $("w-grounds").replaceChildren(
    el("li", "muted", "Grounds not stored for this older run — re-investigate to regenerate."));
  $("w-download").onclick = () => window.open(`/api/claims/${encodeURIComponent(claimId)}/dispute-packet`, "_blank");
}

function renderVerdictHeader(ev) {
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

function renderEvidence(claimId, ev) {
  renderVerdictHeader(ev);

  EVIDENCE_BLOCKS.forEach((id) => $(id).classList.remove("hidden"));
  renderRecon(ev.po_summary, ev.discrepancy_qty, ev.discrepancy_amount_cents);

  const uom = ev.uom_conversions_applied || [];
  if (uom.length) { $("w-uom").classList.remove("hidden"); $("w-uom-body").textContent = uom.join("; "); }
  else $("w-uom").classList.add("hidden");

  const tl = $("w-timeline"); tl.replaceChildren();
  for (const e of ev.timeline || []) {
    const div = el("div", "tl-event" + (e.valid ? "" : " invalid"));
    div.appendChild(el("span", "e", sentenceCase(e.event)));
    div.appendChild(el("span", "d", e.date));
    tl.appendChild(div);
  }

  const checks = $("w-checks"); checks.replaceChildren();
  for (const [k, v] of Object.entries(ev.review_findings || {})) {
    const label = k.replace(/_check$/, "").replace(/_/g, " ");
    checks.appendChild(el("span", "check " + safeClass(v), `${label}: ${v}`));
  }

  const chips = [];
  if (ev.trade_agreement_found) chips.push("Trade agreement found");
  if ((ev.prior_claims || []).length) chips.push("Prior claims: " + ev.prior_claims.join(", "));
  if (chips.length) {
    $("w-context-block").classList.remove("hidden");
    $("w-context").replaceChildren(...chips.map((c) => el("span", "chip", c)));
  } else $("w-context-block").classList.add("hidden");

  const grounds = ev.dispute_grounds || [];
  if (ev.final_verdict === "INVALID" && grounds.length) {
    $("w-dispute-block").classList.remove("hidden");
    $("w-grounds").replaceChildren(...grounds.map((g) => el("li", null, g)));
    $("w-download").onclick = () => window.open(`/api/claims/${encodeURIComponent(claimId)}/dispute-packet`, "_blank");
  } else $("w-dispute-block").classList.add("hidden");

  if (ev.usage) {
    const u = ev.usage;
    $("usage").textContent =
      `tokens — investigator: ${u.investigator.prompt_tokens} in / ${u.investigator.completion_tokens} out · ` +
      `reviewer: ${u.reviewer.prompt_tokens} in / ${u.reviewer.completion_tokens} out`;
  }
}

// --- investigate (single claim, live SSE) --------------------------------------------------------

function appendTrace(d) {
  const li = el("li", d.is_error ? "error" : null);
  li.appendChild(el("span", `agent-tag agent-${d.agent}`, d.agent));
  li.appendChild(el("span", null, d.name));
  li.appendChild(el("span", "tool-args", " " + JSON.stringify(d.args)));
  $("trace").appendChild(li);
}

function investigateClaim(claimId) {
  if (source) source.close();
  $("trace").replaceChildren();
  $("w-investigate").disabled = true;
  document.querySelector("details.audit").open = true;

  source = new EventSource(`/api/claims/${encodeURIComponent(claimId)}/stream`);
  source.addEventListener("tool_call", (e) => appendTrace(JSON.parse(e.data)));
  source.addEventListener("done", (e) => {
    const payload = JSON.parse(e.data);
    renderEvidence(claimId, fromDone(payload));
    setRowStatus(claimId, payload.final_verdict);
    $("w-investigate").textContent = "Re-investigate";
    $("w-investigate").disabled = false;
    source.close(); source = null;
    // loadQueue as well as loadDashboard: re-investigating is the one action that can make an
    // existing decision stale, so without a queue refresh the stale badge never appears after the
    // very thing that causes it.
    loadDashboard();
    loadQueue();
  });
  source.addEventListener("error", (e) => {
    if (e.data) {
      showBanner(`The investigation failed: ${JSON.parse(e.data).error}`);
      source.close(); source = null; $("w-investigate").disabled = false;
    }
  });
  source.onerror = () => {
    if (source && source.readyState === EventSource.CLOSED) {
      showBanner("Connection to the investigation stream failed.");
      $("w-investigate").disabled = false;
    }
  };
}

// --- batch bulk-run (SSE over POST) --------------------------------------------------------------

async function streamSSE(url, opts, onEvent) {
  const resp = await fetch(url, opts);
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i); buf = buf.slice(i + 2);
      let event = null, data = null;
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data = line.slice(5).trim();
      }
      if (event) onEvent(event, data ? JSON.parse(data) : null);
    }
  }
}

async function runBatch() {
  if (!state.batchId) return;
  hideBanner();
  $("run").disabled = true;
  $("run-status").textContent = "Running…";
  try {
    await streamSSE(`/api/batches/${encodeURIComponent(state.batchId)}/investigate`,
      { method: "POST" },
      (event, data) => {
        if (event === "tool_call") $("run-status").textContent = `Investigating ${data.claim_id}…`;
        else if (event === "claim_done") setRowStatus(data.claim_id, data.final_verdict);
        else if (event === "batch_done") $("run-status").textContent =
          `Done: ${data.investigated} investigated · ${data.ESCALATE} escalated`;
        else if (event === "error") showBanner(`The lot run failed: ${data.error}`);
      });
  } catch (e) {
    showBanner("Bulk investigation stream failed.");
  } finally {
    $("run").disabled = false;
    await loadDashboard();
    await loadQueue();
  }
}

// --- disposition (human decision) ----------------------------------------------------------------

/** The recorded decision, with the two things the schema always stored but the UI never showed:
    when it was made, and what the analyst wrote. */
function renderDecision(claim) {
  const box = $("w-disp-current");
  box.replaceChildren();
  if (!claim.disposition) {
    box.textContent = "No analyst decision recorded yet.";
    return;
  }
  const verdict = claim.decided_verdict || claim.override_verdict || claim.agent_status;
  const when = claim.decided_at ? ` on ${claim.decided_at.slice(0, 16).replace("T", " ")}` : "";
  const what = claim.disposition === "override"
    ? `override → ${verdict} (agents said ${claim.agent_status})`
    : `${claim.disposition} → ${verdict}`;
  box.appendChild(document.createTextNode(`Your decision: ${what}${when}`));
  if (claim.decision_stale) {
    box.appendChild(el("span", "stale-badge", "re-investigated since you decided"));
  }
  if (claim.note) box.appendChild(el("div", "disp-note", `“${claim.note}”`));
}

// A verdict can only be accepted or overridden once one exists. Leaving these live on an
// un-investigated claim invited "accept" on nothing at all.
function setDecisionEnabled(enabled) {
  $("w-decision-actions").classList.toggle("hidden", !enabled);
  $("w-decision-blocked").classList.toggle("hidden", enabled);
}

/** Override stays disabled until there is both a verdict and a reason. The server enforces this too
    (422) — this is so the analyst sees why the button won't fire, not the only line of defense. */
function syncOverrideButton() {
  const btn = document.querySelector('.decision [data-disp="override"]');
  const chosen = $("w-override-verdict").value;
  const reason = $("w-note").value.trim();
  const agentVerdict = state.selectedClaim ? state.selectedClaim.agent_status : null;
  btn.disabled = !chosen || !reason || chosen === agentVerdict;
  btn.title = !chosen ? "Choose the verdict you're overriding to"
    : chosen === agentVerdict ? `The agents already said ${chosen} — accept it instead`
    : !reason ? "An override needs a stated reason"
    : "";
}

async function postDisposition(disposition) {
  if (!state.selected) return;
  const body = { disposition, note: $("w-note").value.trim() || null };
  if (disposition === "override") body.override_verdict = $("w-override-verdict").value;
  const resp = await fetch(`/api/claims/${encodeURIComponent(state.selected)}/disposition`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const status = $("w-disp-status");
  if (!resp.ok) {
    const detail = await resp.json().catch(() => null);
    status.textContent = detail?.error
      ? `Couldn't save: ${detail.error}`
      : "Couldn't save the decision. Try again.";
    status.classList.add("err");
    return;
  }
  const saved = await resp.json();
  const label = disposition === "override"
    ? `override → ${saved.decided_verdict}` : `${disposition} → ${saved.decided_verdict}`;
  status.classList.remove("err");
  status.textContent = `Saved — decision recorded: ${label}.`;
  $("w-note").value = "";
  $("w-override-verdict").value = "";
  syncOverrideButton();
  // Apply the save to the open claim immediately. loadQueue below re-syncs from the server, but only
  // when the claim is still in the current filter — deciding it often removes it (that is the point
  // of working a queue), and then nothing would refresh the decision line at all.
  if (state.selectedClaim) {
    Object.assign(state.selectedClaim, {
      disposition, decided_verdict: saved.decided_verdict,
      override_verdict: saved.override_verdict, note: body.note,
      decided_at: saved.decided_at, decision_stale: false,
      status: saved.decided_verdict || state.selectedClaim.status,
    });
    renderDecision(state.selectedClaim);
  }
  // Re-fetch both, in this order: a decision changes the claim's effective verdict, so the KPIs and
  // the row's status/filter membership are now stale. Previously only the dashboard was reloaded
  // (and no KPI read dispositions anyway), so a saved decision left the screen looking unchanged.
  await Promise.all([loadDashboard(), loadQueue()]);
}

// --- wiring --------------------------------------------------------------------------------------

$("run").addEventListener("click", runBatch);
$("banner-dismiss").addEventListener("click", hideBanner);
$("prev").addEventListener("click", () => { state.offset = Math.max(0, state.offset - LIMIT); loadQueue(); });
$("next").addEventListener("click", () => { state.offset += LIMIT; loadQueue(); });
$("w-investigate").addEventListener("click", () => {
  if (!state.selected) return;
  // Re-running the agents on a claim that already carries a human decision is legitimate (a second
  // opinion after a prompt change), but it must not be a silent one-click action: the analyst needs
  // to know a recorded sign-off is about to be contradicted.
  const decided = state.selectedClaim && state.selectedClaim.disposition;
  if (decided) {
    const verdict = state.selectedClaim.decided_verdict || state.selectedClaim.status;
    const ok = window.confirm(
      `You already recorded "${state.selectedClaim.disposition} → ${verdict}" on this claim.\n\n` +
      "Re-investigating will not change that decision, but if the agents reach a different " +
      "verdict the decision will be flagged as stale.\n\nRun the agents again?");
    if (!ok) return;
  }
  investigateClaim(state.selected);
});

$("w-override-verdict").addEventListener("change", syncOverrideButton);
$("w-note").addEventListener("input", syncOverrideButton);

document.querySelectorAll("#tabs .tab").forEach((t) =>
  t.addEventListener("click", () => setFilter(t.dataset.filter)));
document.querySelectorAll("#kpis .card[data-filter]").forEach((c) =>
  c.addEventListener("click", () => setFilter(c.dataset.filter)));
// Sorting lives on the column headers only. No KPI card sorts any more — one that looked like the
// filter cards but reordered the table instead taught that a card's behaviour is unguessable.
document.querySelectorAll("th.sortable").forEach((el) =>
  el.addEventListener("click", () => { state.sort = el.dataset.sort; state.offset = 0; loadQueue(); }));
document.querySelectorAll(".decision [data-disp]").forEach((b) =>
  b.addEventListener("click", () => postDisposition(b.dataset.disp)));

let searchTimer = null;
$("search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.q = e.target.value.trim(); state.offset = 0; loadQueue(); }, 250);
});

(async () => {
  await loadDashboard();
  setFilter(state.filter);  // sets active tab/card and loads the queue
})();
