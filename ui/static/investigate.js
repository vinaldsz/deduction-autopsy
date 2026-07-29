// Running the agents — one claim over SSE, or the whole lot.

import {
  batchSummaryLine, fromDone, runConfirmMessage, runProgressLine, usageTokens,
} from "./lib.js";
import { fetchJSON, streamSSE } from "./api.js";
import { hideBanner, showBanner } from "./banner.js";
import { loadDashboard } from "./dashboard.js";
import { $ } from "./dom.js";
import { appendTrace, renderEvidence } from "./evidence.js";
import { loadQueue } from "./queue.js";
import { setRowStatus } from "./queue-view.js";
import { renderRunFailures, setRunning, setRunStatus } from "./run-bar.js";
import { state } from "./state.js";
import { closeStream, stream } from "./stream.js";

export function investigateClaim(claimId) {
  closeStream();
  $("trace").replaceChildren();
  $("w-investigate").disabled = true;
  // The audit drawer is deliberately NOT force-opened here. It was opened on every run, which put a
  // scrolling wall of tool calls between the analyst and the decision bar on work that is usually
  // routine; auditing is a thing you choose to do, and a past run's trace now loads on demand.

  stream.current = new EventSource(`/api/claims/${encodeURIComponent(claimId)}/stream`);
  stream.current.addEventListener("tool_call", (e) => appendTrace(JSON.parse(e.data)));
  stream.current.addEventListener("done", (e) => {
    const payload = JSON.parse(e.data);
    renderEvidence(claimId, fromDone(payload));
    setRowStatus(claimId, payload.final_verdict);
    $("w-investigate").textContent = "Re-investigate";
    $("w-investigate").disabled = false;
    closeStream();
    // loadQueue as well as loadDashboard: re-investigating is the one action that can make an
    // existing decision stale, so without a queue refresh the stale badge never appears after the
    // very thing that causes it.
    loadDashboard();
    loadQueue();
  });
  stream.current.addEventListener("error", (e) => {
    if (e.data) {
      // The raw exception goes to the console, not into the sentence: `str(exc)` from a
      // PipelineError is a developer's string, and the analyst needs to know what state the claim
      // is in. Nothing was recorded, so the claim is exactly as it was.
      console.error("investigation failed", JSON.parse(e.data).error);
      showBanner(`${claimId} could not be investigated — nothing was recorded, so the claim is `
        + `unchanged. The details are in the browser console.`);
      closeStream(); $("w-investigate").disabled = false;
    }
  });
  stream.current.onerror = () => {
    if (stream.current && stream.current.readyState === EventSource.CLOSED) {
      showBanner("Connection to the investigation stream failed.");
      $("w-investigate").disabled = false;
    }
  };
}

// The in-flight lot run, or null. A holder rather than loose `let`s for the same reason stream.js is
// one: `cancelBatch` and `runBatch` both need to write it, and the progress line has to be rebuilt
// from one place or the counter and the claim name drift apart. Not in `state`, which describes the
// view and is mirrored into the URL — a half-finished run is neither.
let live = null;

function progress() {
  if (!live) return;
  setRunStatus(runProgressLine({
    total: live.total, completed: live.completed, failed: live.failures.length,
    current: live.current,
  }));
}

/** Stop the lot. Aborting the fetch closes the stream, which is the server's signal to stop at the
    next claim boundary — the claim already running finishes and is persisted, because it has been
    paid for either way and killing it would leave a run dir with no verdict.json.

    The `live` guard is the whole re-entrancy story: the abort rejects the pending read within a
    tick, so `finally` has cleared `live` and hidden the button before a second click could land. */
export function cancelBatch() {
  if (!live) return;
  live.abort.abort();
}

export async function runBatch() {
  if (!state.batchId || live) return;
  hideBanner();

  const lot = encodeURIComponent(state.batchId);
  let estimate;
  try {
    estimate = await fetchJSON(`/api/batches/${lot}/run-estimate`);
  } catch (e) {
    console.error("run-estimate failed", e);
    showBanner("Could not work out how much this lot run would cost, so it was not started.");
    return;
  }
  const message = runConfirmMessage(estimate);
  // null means there is nothing unresolved left: say so rather than confirming an empty run.
  if (!message) { setRunStatus("Nothing to run — every claim in this lot has been investigated."); return; }
  if (!confirm(message)) return;
  // Cleared here, not before the confirm: declining the dialog must leave the previous run's
  // failure report on screen, since nothing has replaced it.
  renderRunFailures([]);

  // Tokens are counted here rather than read off batch_done, because a cancelled run never gets one
  // and it still spent every token it spent.
  live = { total: estimate.claims, completed: 0, tokens: 0, current: null, failures: [],
           abort: new AbortController() };
  setRunning(true);
  progress();

  try {
    await streamSSE(`/api/batches/${lot}/investigate`,
      { method: "POST", signal: live.abort.signal },
      (event, data) => {
        if (event === "batch_start") { live.total = data.total; progress(); }
        // `claim_start`, not `tool_call`: a claim that fails before its first tool call never emits
        // one, and the line would go on naming the previous claim as the counter moved past it.
        else if (event === "claim_start") { live.current = data.claim_id; progress(); }
        else if (event === "claim_done") {
          live.completed += 1;
          live.tokens += usageTokens(data.usage);
          setRowStatus(data.claim_id, data.final_verdict);
          progress();
        } else if (event === "claim_error") {
          // The lot continues. Nothing was written for this claim, so its queue row is untouched
          // and this list is the only record that it was attempted at all.
          console.error(`investigation failed for ${data.claim_id}`, data.error);
          live.failures.push({ claim_id: data.claim_id, error: data.error });
          renderRunFailures(live.failures);
          progress();
        } else if (event === "batch_done") {
          live.current = null;
          setRunStatus(batchSummaryLine({
            ending: data.stopped_reason || "done",
            investigated: data.investigated, failed: data.failed,
            escalated: data.ESCALATE, tokens: live.tokens,
          }));
        } else if (event === "error") {
          console.error("lot run failed", data.error);
          showBanner("The lot run stopped on an error before it could finish. Claims already "
            + "investigated were saved; the details are in the browser console.");
        }
      });
  } catch (e) {
    if (e.name === "AbortError") {
      // Our own cancel, not a failure. The summary is built from what the client counted, because
      // the stream ended before batch_done — and the spend is real either way.
      setRunStatus(batchSummaryLine({
        ending: "cancelled", investigated: live.completed, failed: live.failures.length,
        tokens: live.tokens,
      }));
    } else {
      console.error("lot run stream failed", e);
      showBanner("Lost the connection to the lot run. Claims already investigated were saved — "
        + "run the lot again to pick up the rest.");
      setRunStatus("");
    }
  } finally {
    live = null;
    setRunning(false);
    await loadDashboard();
    await loadQueue();
  }
}
