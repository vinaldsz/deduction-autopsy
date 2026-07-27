// Dashboard + daily-lot worklist over the Layer-30 API. No framework, no build step.

const LIMIT = 25;
const state = { batchId: null, offset: 0, total: 0 };

const $ = (id) => document.getElementById(id);
const banner = $("banner");
const dollars = (cents) => "$" + (cents / 100).toFixed(2);

function showBanner(msg) { banner.textContent = msg; banner.classList.remove("hidden"); }

async function loadDashboard() {
  const d = await (await fetch("/api/dashboard")).json();
  $("m-unresolved").textContent = d.unresolved_count;
  $("m-resolved").textContent = d.resolved_this_month;
  $("m-risk").textContent = dollars(d.dollars_at_risk_cents);
  const p = d.priority_breakdown;
  $("m-priority").textContent = `${p.HIGH}/${p.MEDIUM}/${p.LOW}`;
  $("m-batch").textContent = d.batch ? `${d.batch.batch_id} (${d.batch.status})` : "—";
  state.batchId = d.batch ? d.batch.batch_id : null;
}

function renderRow(claim) {
  const tr = document.createElement("tr");
  tr.dataset.claimId = claim.claim_id;
  tr.innerHTML =
    `<td>${claim.claim_id}</td><td>${claim.retailer}</td><td>${claim.claimed_reason}</td>` +
    `<td class="num">${dollars(claim.claimed_amount)}</td>` +
    `<td><span class="pill p-${claim.priority}">${claim.priority}</span></td>` +
    `<td class="status st-${claim.status}">${claim.status}</td>`;
  tr.addEventListener("click", () => drillIn(claim.claim_id));
  return tr;
}

async function loadBatch() {
  if (!state.batchId) return;
  const url = `/api/batches/${encodeURIComponent(state.batchId)}?offset=${state.offset}&limit=${LIMIT}`;
  const data = await (await fetch(url)).json();
  state.total = data.total;
  const rows = $("rows");
  rows.innerHTML = "";
  for (const claim of data.claims) rows.appendChild(renderRow(claim));
  const shown = data.claims.length ? `${state.offset + 1}–${state.offset + data.claims.length}` : "0";
  $("page-info").textContent = `${shown} of ${data.total}`;
  $("prev").disabled = state.offset === 0;
  $("next").disabled = state.offset + LIMIT >= data.total;
}

function setRowStatus(claimId, verdict) {
  const tr = document.querySelector(`tr[data-claim-id="${claimId}"]`);
  if (!tr) return;
  const cell = tr.querySelector(".status");
  cell.textContent = verdict;
  cell.className = "status st-" + verdict;
}

// Consume an SSE stream delivered over fetch (used for the POST bulk-run).
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
  }
}

// --- single-claim drill-in (GET SSE via EventSource) ---------------------------------------------

let source = null;

function setVerdict(id, value) {
  const el = $(id);
  el.textContent = value;
  el.className = "v " + value;
}

function renderVerdict(data) {
  setVerdict("v-investigator", data.investigator_verdict);
  setVerdict("v-reviewer", data.reviewer_verdict);
  setVerdict("v-final", data.final_verdict);
  $("v-confidence").textContent = data.confidence;
  const list = $("dispute-list");
  list.innerHTML = "";
  if (data.final_verdict === "INVALID" && data.dispute_grounds && data.dispute_grounds.length) {
    for (const g of data.dispute_grounds) {
      const li = document.createElement("li"); li.textContent = g; list.appendChild(li);
    }
    $("dispute").classList.remove("hidden");
  } else {
    $("dispute").classList.add("hidden");
  }
  const u = data.usage;
  $("usage").textContent =
    `tokens — investigator: ${u.investigator.prompt_tokens} in / ${u.investigator.completion_tokens} out · ` +
    `reviewer: ${u.reviewer.prompt_tokens} in / ${u.reviewer.completion_tokens} out`;
  $("verdict-card").classList.remove("hidden");
}

function drillIn(claimId) {
  if (source) source.close();
  $("drill").classList.remove("hidden");
  $("drill-claim").textContent = claimId;
  $("trace").innerHTML = "";
  $("verdict-card").classList.add("hidden");
  $("drill").scrollIntoView({ behavior: "smooth" });

  source = new EventSource(`/api/claims/${encodeURIComponent(claimId)}/stream`);
  source.addEventListener("tool_call", (e) => {
    const d = JSON.parse(e.data);
    const li = document.createElement("li");
    if (d.is_error) li.classList.add("error");
    li.innerHTML = `<span class="agent-tag agent-${d.agent}">${d.agent}</span>` +
      `<span>${d.name}</span><span class="tool-args"> ${JSON.stringify(d.args)}</span>`;
    $("trace").appendChild(li);
  });
  source.addEventListener("done", (e) => {
    renderVerdict(JSON.parse(e.data));
    setRowStatus(claimId, JSON.parse(e.data).final_verdict);
    source.close(); source = null;
    loadDashboard();
  });
  source.addEventListener("error", (e) => {
    if (e.data) { showBanner(JSON.parse(e.data).error); source.close(); source = null; }
  });
  source.onerror = () => {
    if (source && source.readyState === EventSource.CLOSED) {
      showBanner("Connection to the investigation stream failed.");
    }
  };
}

$("run").addEventListener("click", runBatch);
$("prev").addEventListener("click", () => { state.offset = Math.max(0, state.offset - LIMIT); loadBatch(); });
$("next").addEventListener("click", () => { state.offset += LIMIT; loadBatch(); });

(async () => { await loadDashboard(); await loadBatch(); })();
