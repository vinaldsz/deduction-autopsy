// Recording the analyst's decision, and advancing to the next claim.

import { dispositionLabel, nextClaimId } from "./lib.js";
import { loadDashboard } from "./dashboard.js";
import { renderDecision, syncOverrideButton } from "./decision-pane.js";
import { $ } from "./dom.js";
import { loadQueue } from "./queue.js";
import { setActionNote } from "./queue-view.js";
import { state } from "./state.js";
import { restoreSelection } from "./workspace.js";

export async function postDisposition(disposition) {
  if (!state.claim) return;
  // Resolved BEFORE the write, from the page as it stands: deciding a claim usually removes it from
  // the current filter — that is the point of working a queue — so after the reload there is no row
  // left to ask "what came after this one".
  const advanceTo = nextClaimId(state.pageIds, state.claim, 1);
  const decided = state.claim;
  const body = { disposition, note: $("w-note").value.trim() || null };
  if (disposition === "override") body.override_verdict = $("w-override-verdict").value;
  const resp = await fetch(`/api/claims/${encodeURIComponent(state.claim)}/disposition`, {
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
  const label = dispositionLabel(disposition, saved.decided_verdict);
  status.classList.remove("err");
  // Still set, and not redundant with the queue-bar note below: on the last row of a page there is no
  // next claim, the pane stays on this one, and this is the confirmation the analyst is looking at.
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
  // Only on success — the early return above leaves the pane where it is, with the failure visible
  // next to the button that produced it. The note goes to the queue bar rather than the pane, because
  // the pane is about to belong to a different claim.
  setActionNote(`${decided}: ${label}`);
  if (advanceTo) {
    state.claim = advanceTo;
    // restoreSelection already knows how to open a claim whether or not its row is on this page.
    await restoreSelection();
  }
}
