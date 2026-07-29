// Bulk accept: the one action in the UI that writes more than one decision.

import { bulkConfirmMessage, bulkOutcomeSummary, pickedOnPage } from "./lib.js";
import { fetchJSON } from "./api.js";
import { showBanner } from "./banner.js";
import { loadDashboard } from "./dashboard.js";
import { $ } from "./dom.js";
import { loadQueue } from "./queue.js";
import { setActionNote } from "./queue-view.js";
import { state } from "./state.js";

export async function bulkAccept() {
  const claimIds = pickedOnPage(state.pageIds, state.picked);
  if (!claimIds.length) return;
  // Names the count, because this is the one action in the UI that writes more than one decision.
  if (!window.confirm(bulkConfirmMessage(claimIds.length))) return;
  $("bulk-accept").disabled = true;
  setActionNote("Saving…");
  try {
    const saved = await fetchJSON(
      `/api/batches/${encodeURIComponent(state.batchId)}/dispositions`,
      { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ claim_ids: claimIds }) });
    // Set after the reload: loadQueue clears the selection and the note, so a summary written before
    // it would be wiped by its own refresh.
    await Promise.all([loadDashboard(), loadQueue()]);
    setActionNote(bulkOutcomeSummary(saved.results));
  } catch (err) {
    console.error("bulkAccept", err);
    setActionNote("");
    // "Nothing was saved" is a claim about the server, and it is true: the writer runs every claim in
    // one transaction, so a failure rolls all of them back.
    showBanner("Couldn't record the bulk decision — nothing was saved.");
  } finally {
    $("bulk-accept").disabled = false;
  }
}
