// Running the agents — one claim over SSE, or the whole lot.

import { fromDone } from "./lib.js";
import { streamSSE } from "./api.js";
import { hideBanner, showBanner } from "./banner.js";
import { loadDashboard } from "./dashboard.js";
import { $ } from "./dom.js";
import { appendTrace, renderEvidence } from "./evidence.js";
import { loadQueue } from "./queue.js";
import { setRowStatus } from "./queue-view.js";
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
      showBanner(`The investigation failed: ${JSON.parse(e.data).error}`);
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

export async function runBatch() {
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
