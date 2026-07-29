// Opening a claim: the review pane's data layer.

import {
  currentRun, dollars, fromCasefile, queryParams, runHistoryLine, sentenceCase
} from "./lib.js";
import { fetchJSON } from "./api.js";
import { syncHash } from "./controls.js";
import { renderDecision, setDecisionEnabled, syncOverrideButton } from "./decision-pane.js";
import { renderDocuments, renderReason } from "./documents.js";
import { $, el } from "./dom.js";
import {
  appendTrace, hideAgentEvidence, renderEvidence, renderUsage, renderVerdictHeader,
  setTraceEmpty, showDisputeDownloadOnly
} from "./evidence.js";
import { markSelectedRow } from "./queue-view.js";
import { state } from "./state.js";
import { closeStream } from "./stream.js";

/** Reopen the claim named in the URL after a deep link, a refresh or a back button.
 *
 *  Usually it is on the page already — the hash carries the filter and page too. The targeted lookup
 *  is for the case where it isn't (a link shared with no page in it, or a claim since filtered out of
 *  the tab): a deep link that silently opens nothing is worse than one extra request.
 *
 *  The guard reads `renderedClaim`, NOT `selectedClaim`. Found in the running app: navigating from
 *  one claim's URL to another's fires `hashchange`, and `loadQueue` re-points `selectedClaim` at the
 *  new row before this runs — so a `selectedClaim` guard saw a match, returned early, and left the
 *  previous claim's evidence sitting under the new claim's highlighted row. Same failure mode as the
 *  Layer 33 stale-documents bug, reached by a different route. */
export async function restoreSelection() {
  if (!state.claim) return;
  if (state.renderedClaim === state.claim) return;
  const onPage = document.querySelector(`tr[data-claim-id="${CSS.escape(state.claim)}"]`);
  if (onPage) { onPage.click(); return; }
  const row = await fetchClaimRow(state.claim);
  if (row) selectClaim(row);
}

async function fetchClaimRow(claimId) {
  try {
    const params = queryParams(state, { filter: "all", page: 1, size: 100, q: claimId,
                                 retailer: null, reason: null, date_from: null, date_to: null });
    const data = await fetchJSON(
      `/api/batches/${encodeURIComponent(state.batchId)}?${params}`);
    return data.claims.find((c) => c.claim_id === claimId) || null;
  } catch (err) {
    console.error("fetchClaimRow", err);
    return null;
  }
}

export async function selectClaim(claim) {
  state.claim = claim.claim_id;
  // Set before the awaits below, not after: it records which claim the pane is now committed to
  // showing, and restoreSelection may run again while the documents are still in flight.
  state.renderedClaim = claim.claim_id;
  syncHash();
  markSelectedRow(claim.claim_id);
  closeStream();

  $("ws-empty").classList.add("hidden");
  $("ws-body").classList.remove("hidden");
  $("w-claim").textContent = claim.claim_id;
  $("w-meta").textContent = `${claim.retailer} · ${sentenceCase(claim.claimed_reason)} · ${dollars(claim.claimed_amount)} · claimed ${claim.claim_date}`;
  $("trace").replaceChildren();
  $("usage").textContent = "";
  // The trace belongs to the run being displayed, so a claim change re-arms the lazy load.
  $("trace-empty").classList.add("hidden");
  state.tracedClaim = null;
  // Reset here rather than in hideAgentEvidence: loadRuns is not awaited, so hiding this later would
  // race the response it is waiting for. See hideAgentEvidence.
  $("w-runs").textContent = "";
  $("w-runs").classList.add("hidden");
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
    // Said here rather than left to a 404: there is no run to have a trace, so asking for one would be
    // a request we already know the answer to, and an empty drawer explains nothing.
    setTraceEmpty("No agent run yet — investigate this claim to record a tool trace.");
    return;
  }
  loadRuns(claim);
  // Only matters when the analyst left the drawer open — see maybeLoadTrace.
  maybeLoadTrace();
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

/** Read-only run history + the token spend, both from /runs.
 *
 *  The history line only appears above one run: on today's lot 46 of 50 claims have exactly one, and
 *  "1 run" on 92% of claims is noise. It is what makes Layer 34's stale-decision badge actionable —
 *  that badge says the agents moved on without ever saying what they used to think.
 *
 *  This is also what fills `#usage` for a claim nobody just ran: fromCasefile hardcodes `usage: null`,
 *  so before this the token line existed only during a live run, while verdict.json had it all along. */
async function loadRuns(claim) {
  try {
    const data = await fetchJSON(`/api/claims/${encodeURIComponent(claim.claim_id)}/runs`);
    if (state.renderedClaim !== claim.claim_id) return;  // a slower response for a claim we left
    const history = runHistoryLine(data.runs, data.latest_run_id);
    const line = $("w-runs");
    line.textContent = history || "";
    line.classList.toggle("hidden", !history);

    const current = currentRun(data.runs, data.latest_run_id);
    if (current && current.usage) renderUsage(current.usage);
  } catch (err) {
    // A missing history is not worth a banner over: the evidence and the decision bar both work
    // without it, and the alternative is an error box on a claim that rendered perfectly well.
    console.error("loadRuns", err);
  }
}

/** Pull the trace in whenever the drawer is open and empty.
 *
 *  Called from BOTH the drawer's toggle and claim selection, because "open the drawer" and "the open
 *  drawer needs a different claim's trace" are two different triggers. Found by driving the app: an
 *  analyst who leaves the drawer open fires no toggle event on the next claim, so a toggle-only hook
 *  left the drawer open and permanently empty from the second claim onward. */
export function maybeLoadTrace() {
  if ($("w-audit").open && state.renderedClaim && !$("trace").children.length) {
    loadTrace(state.renderedClaim);
  }
}

/** The trace, fetched on demand rather than on every claim selection — auditing is occasional, and
    this is the largest artifact on disk. */
async function loadTrace(claimId) {
  if (state.tracedClaim === claimId) return;
  state.tracedClaim = claimId;
  try {
    const data = await fetchJSON(`/api/claims/${encodeURIComponent(claimId)}/trace`);
    if (state.renderedClaim !== claimId) return;
    $("trace").replaceChildren();
    (data.tool_calls || []).forEach(appendTrace);
    if (!(data.tool_calls || []).length) setTraceEmpty("This run recorded no tool calls.");
  } catch (err) {
    console.error("loadTrace", err);
    state.tracedClaim = null;  // let a retry happen on the next open
    setTraceEmpty("No tool trace stored for this run — re-investigate to record one.");
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
