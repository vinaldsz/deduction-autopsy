// Analyst workspace over the Layer 30/32 API. No framework, no build step.

const LIMIT = 25;
const state = { batchId: null, offset: 0, total: 0, filter: "needs_me", sort: "priority", q: "", selected: null };

const $ = (id) => document.getElementById(id);
const banner = $("banner");
const dollars = (cents) => "$" + (cents / 100).toFixed(2);
const safeClass = (v) => (v === "N/A" ? "NA" : v);

function showBanner(msg) { banner.textContent = msg; banner.classList.remove("hidden"); }

// --- dashboard (KPI strip) -----------------------------------------------------------------------

async function loadDashboard() {
  const d = await (await fetch("/api/dashboard")).json();
  $("m-unresolved").textContent = d.unresolved_count;
  $("m-resolved").textContent = d.resolved_this_month;
  $("m-risk").textContent = dollars(d.dollars_at_risk_cents);
  const p = d.priority_breakdown;
  $("m-priority").textContent = `${p.HIGH}/${p.MEDIUM}/${p.LOW}`;
  $("m-batch").textContent = d.batch ? `${d.batch.batch_id} (${d.batch.status})` : "—";
  $("m-escalate").textContent = d.needs_human_review ?? "—";
  $("m-needsme").textContent = d.needs_me_count ?? "—";
  state.batchId = d.batch ? d.batch.batch_id : null;
}

// --- worklist queue ------------------------------------------------------------------------------

function renderRow(claim) {
  const tr = document.createElement("tr");
  tr.dataset.claimId = claim.claim_id;
  tr.tabIndex = 0;
  const disp = claim.disposition ? `<span class="disp-badge">${claim.disposition}</span>` : "";
  // `status` is the effective verdict, so an override shows the analyst's answer as the claim's
  // answer. The superseded agent verdict stays visible beside it — dropping it would erase the
  // audit trail that keeping the two spines separate exists to preserve.
  const superseded =
    claim.disposition === "override" && claim.agent_status !== claim.status
      ? `<span class="status-sup">was ${claim.agent_status}</span>`
      : "";
  tr.innerHTML =
    `<td>${claim.claim_id}</td><td>${claim.retailer}</td><td>${claim.claimed_reason}</td>` +
    `<td class="num">${dollars(claim.claimed_amount)}</td>` +
    `<td><span class="pill p-${claim.priority}">${claim.priority}</span></td>` +
    `<td class="status"><span class="dot d-${claim.status}"></span>${claim.status}${superseded}${disp}</td>`;
  const open = () => selectClaim(claim);
  tr.addEventListener("click", open);
  tr.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
  return tr;
}

async function loadQueue() {
  if (!state.batchId) return;
  const params = new URLSearchParams({
    offset: state.offset, limit: LIMIT, status_filter: state.filter, sort: state.sort,
  });
  if (state.q) params.set("q", state.q);
  const data = await (await fetch(`/api/batches/${encodeURIComponent(state.batchId)}?${params}`)).json();
  state.total = data.total;
  const rows = $("rows");
  rows.innerHTML = "";
  for (const claim of data.claims) rows.appendChild(renderRow(claim));
  if (state.selected) markSelectedRow(state.selected);
  const shown = data.claims.length ? `${state.offset + 1}–${state.offset + data.claims.length}` : "0";
  $("page-info").textContent = `${shown} of ${data.total}`;
  $("prev").disabled = state.offset === 0;
  $("next").disabled = state.offset + LIMIT >= data.total;
  $("m-needsme").textContent = state.filter === "needs_me" ? data.total : $("m-needsme").textContent;
}

function markSelectedRow(claimId) {
  document.querySelectorAll("tr.selected").forEach((tr) => tr.classList.remove("selected"));
  const tr = document.querySelector(`tr[data-claim-id="${claimId}"]`);
  if (tr) tr.classList.add("selected");
}

function setRowStatus(claimId, verdict) {
  const tr = document.querySelector(`tr[data-claim-id="${claimId}"]`);
  if (!tr) return;
  const cell = tr.querySelector(".status");
  cell.innerHTML = `<span class="dot d-${verdict}"></span>${verdict}`;
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

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;   // textContent — never innerHTML for DB text
  return e;
}

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
  const box = $("w-reason"); box.innerHTML = "";
  box.appendChild(el("div", "lead",
    `${claim.retailer} claims ${claim.claimed_reason} for ${dollars(claim.claimed_amount)} · claimed ${claim.claim_date}`));
  if (notes) box.appendChild(el("div", "notes", `“${notes}”`));
}

function renderDocuments(docs) {
  const wrap = $("w-docs"); wrap.innerHTML = "";
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
        (c) => [[c.claim_id], [c.claimed_reason], [dollars(c.claimed_amount), true], [c.claim_date], [c.final_verdict || "unresolved"]])));
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
  $("w-meta").textContent = `${claim.retailer} · ${claim.claimed_reason} · ${dollars(claim.claimed_amount)} · claimed ${claim.claim_date}`;
  $("trace").innerHTML = "";
  $("usage").textContent = "";
  $("w-disp-status").textContent = "";
  $("w-disp-status").classList.remove("err");
  $("w-disp-current").textContent = describeDecision(claim);
  const investigated = claim.status !== "unresolved";
  $("w-investigate").textContent = investigated ? "Re-investigate" : "Investigate";
  $("w-investigate").disabled = false;
  setDecisionEnabled(investigated);

  // Source documents + reason are always available from the DB, regardless of agent runs.
  const docResp = await fetch(`/api/claims/${encodeURIComponent(claim.claim_id)}/documents`);
  if (docResp.ok) {
    const docs = await docResp.json();
    renderReason(claim, docs.claim.retailer_notes);
    renderDocuments(docs);
  }

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

function hideAgentEvidence() {
  EVIDENCE_BLOCKS.forEach((id) => $(id).classList.add("hidden"));
  $("w-uom").classList.add("hidden");
  $("w-context-block").classList.add("hidden");
  $("w-dispute-block").classList.add("hidden");
}

function showDisputeDownloadOnly(claimId) {
  $("w-dispute-block").classList.remove("hidden");
  $("w-grounds").innerHTML = "<li class=\"muted\">Grounds not stored for this older run — re-investigate to regenerate.</li>";
  $("w-download").onclick = () => window.open(`/api/claims/${encodeURIComponent(claimId)}/dispute-packet`, "_blank");
}

function renderVerdictHeader(ev) {
  const chip = $("w-final");
  chip.textContent = ev.final_verdict;
  chip.className = "verdict-chip " + ev.final_verdict;
  if (ev.confidence != null) {
    $("w-conf-wrap").classList.remove("hidden");
    $("w-conf-bar").style.width = Math.round(ev.confidence * 100) + "%";
    $("w-conf-txt").textContent = `${Math.round(ev.confidence * 100)}% confidence`;
  } else {
    $("w-conf-wrap").classList.add("hidden");
  }
  const prov = $("w-provenance");
  if (ev.investigator_verdict && ev.reviewer_verdict) {
    prov.innerHTML =
      `Investigator proposed <span class="v V-${ev.investigator_verdict}">${ev.investigator_verdict}</span> → ` +
      `Reviewer <span class="v r-${ev.reviewer_verdict}">${ev.reviewer_verdict}</span>`;
  } else { prov.textContent = ""; }
}

function renderRecon(po, discQty, discCents) {
  const rows = [
    ["Ordered", po.ordered_qty_each], ["Shipped", po.shipped_qty_each],
    ["Received", po.received_qty_each], ["Invoiced", po.invoiced_qty_each],
  ];
  let html = "<tbody>";
  for (const [label, v] of rows) html += `<tr><td>${label}</td><td class="num">${v}</td></tr>`;
  const bad = discQty ? " bad" : "";
  html += `<tr class="disc"><td>Discrepancy</td><td class="num${bad}">${discQty} EACH · ${dollars(discCents)}</td></tr>`;
  html += "</tbody>";
  $("w-recon").innerHTML = html;
}

function renderEvidence(claimId, ev) {
  renderVerdictHeader(ev);

  EVIDENCE_BLOCKS.forEach((id) => $(id).classList.remove("hidden"));
  renderRecon(ev.po_summary, ev.discrepancy_qty, ev.discrepancy_amount_cents);

  const uom = ev.uom_conversions_applied || [];
  if (uom.length) { $("w-uom").classList.remove("hidden"); $("w-uom-body").textContent = uom.join("; "); }
  else $("w-uom").classList.add("hidden");

  const tl = $("w-timeline"); tl.innerHTML = "";
  for (const e of ev.timeline || []) {
    const div = document.createElement("div");
    div.className = "tl-event" + (e.valid ? "" : " invalid");
    div.innerHTML = `<span class="e">${e.event}</span><span class="d">${e.date}</span>`;
    tl.appendChild(div);
  }

  const checks = $("w-checks"); checks.innerHTML = "";
  for (const [k, v] of Object.entries(ev.review_findings || {})) {
    const label = k.replace(/_check$/, "").replace(/_/g, " ");
    const span = document.createElement("span");
    span.className = "check " + safeClass(v);
    span.textContent = `${label}: ${v}`;
    checks.appendChild(span);
  }

  const chips = [];
  if (ev.trade_agreement_found) chips.push("Trade agreement found");
  if ((ev.prior_claims || []).length) chips.push("Prior claims: " + ev.prior_claims.join(", "));
  if (chips.length) {
    $("w-context-block").classList.remove("hidden");
    $("w-context").innerHTML = chips.map((c) => `<span class="chip">${c}</span>`).join("");
  } else $("w-context-block").classList.add("hidden");

  const grounds = ev.dispute_grounds || [];
  if (ev.final_verdict === "INVALID" && grounds.length) {
    $("w-dispute-block").classList.remove("hidden");
    $("w-grounds").innerHTML = grounds.map((g) => `<li>${g}</li>`).join("");
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
  const li = document.createElement("li");
  if (d.is_error) li.classList.add("error");
  li.innerHTML = `<span class="agent-tag agent-${d.agent}">${d.agent}</span>` +
    `<span>${d.name}</span><span class="tool-args"> ${JSON.stringify(d.args)}</span>`;
  $("trace").appendChild(li);
}

function investigateClaim(claimId) {
  if (source) source.close();
  $("trace").innerHTML = "";
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
    loadDashboard();
  });
  source.addEventListener("error", (e) => {
    if (e.data) { showBanner(JSON.parse(e.data).error); source.close(); source = null; $("w-investigate").disabled = false; }
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
  banner.classList.add("hidden");
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
        else if (event === "error") showBanner(data.error);
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

function describeDecision(claim) {
  if (!claim.disposition) return "No analyst decision recorded yet.";
  if (claim.disposition === "override") {
    return `Your decision: override → ${claim.override_verdict} (agents said ${claim.agent_status})`;
  }
  return `Your decision: ${claim.disposition}`;
}

// A verdict can only be accepted or overridden once one exists. Leaving these live on an
// un-investigated claim invited "accept" on nothing at all.
function setDecisionEnabled(enabled) {
  $("w-decision-actions").classList.toggle("hidden", !enabled);
  $("w-decision-blocked").classList.toggle("hidden", enabled);
}

async function postDisposition(disposition) {
  if (!state.selected) return;
  const body = { disposition, note: $("w-note").value || null };
  if (disposition === "override") body.override_verdict = $("w-override-verdict").value;
  const resp = await fetch(`/api/claims/${encodeURIComponent(state.selected)}/disposition`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const status = $("w-disp-status");
  if (!resp.ok) {
    status.textContent = "Failed to save decision.";
    status.classList.add("err");
    return;
  }
  const label = disposition === "override" ? `override → ${body.override_verdict}` : disposition;
  status.classList.remove("err");
  status.textContent = `Saved — decision recorded: ${label}.`;
  $("w-disp-current").textContent = `Your decision: ${label}`;
  $("w-note").value = "";
  // Re-fetch both, in this order: a decision changes the claim's effective verdict, so the KPIs and
  // the row's status/filter membership are now stale. Previously only the dashboard was reloaded
  // (and no KPI read dispositions anyway), so a saved decision left the screen looking unchanged.
  await Promise.all([loadDashboard(), loadQueue()]);
}

// --- wiring --------------------------------------------------------------------------------------

$("run").addEventListener("click", runBatch);
$("prev").addEventListener("click", () => { state.offset = Math.max(0, state.offset - LIMIT); loadQueue(); });
$("next").addEventListener("click", () => { state.offset += LIMIT; loadQueue(); });
$("w-investigate").addEventListener("click", () => { if (state.selected) investigateClaim(state.selected); });

document.querySelectorAll("#tabs .tab").forEach((t) =>
  t.addEventListener("click", () => setFilter(t.dataset.filter)));
document.querySelectorAll("#kpis .card[data-filter]").forEach((c) =>
  c.addEventListener("click", () => setFilter(c.dataset.filter)));
document.querySelectorAll("#kpis .card[data-sort], th.sortable").forEach((el) =>
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
