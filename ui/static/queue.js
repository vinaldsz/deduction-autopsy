// Loading the worklist, and the URL state that decides which rows it holds.

import { isFiltered, pageStatus, parseHash, queryParams, queueFooter } from "./lib.js";
import { fetchJSON } from "./api.js";
import { showBanner } from "./banner.js";
import {
  consumeOwnHashWrite, fillOptions, renderControls, renderSortIndicators, syncHash,
} from "./controls.js";
import { renderDecision, syncOverrideButton } from "./decision-pane.js";
import { $ } from "./dom.js";
import { markSelectedRow, renderQueueBar, renderRow, setQueueMessage } from "./queue-view.js";
import { state } from "./state.js";
import { restoreSelection, selectClaim } from "./workspace.js";

export async function loadQueue() {
  // Every reload starts from an empty selection and no stale action note. The rows are about to be
  // replaced, and a checked id that survives them is a decision waiting to be made on a row the
  // analyst can no longer see. (bulkAccept sets its summary *after* awaiting this, deliberately.)
  state.picked.clear();
  state.actionNote = "";
  if (!state.batchId) {
    $("rows").replaceChildren();
    $("page-info").textContent = "";
    $("queue-foot").classList.add("hidden");
    state.pageIds = [];
    renderQueueBar();
    setQueueMessage("No lot loaded. Run the ETL (python -m semantic_layer.etl), then reload.");
    return;
  }
  try {
    const data = await fetchJSON(
      `/api/batches/${encodeURIComponent(state.batchId)}?${queryParams(state)}`);
    state.appliedSort = data.sort;
    state.appliedDirection = data.direction;
    renderSortIndicators();
    const rows = $("rows");
    rows.replaceChildren();
    for (const claim of data.claims) rows.appendChild(renderRow(claim, selectClaim));
    // The page's row order, which is what j/k and save-and-next walk. Captured here because after a
    // decision the row is usually gone from the filter, and "the next claim" has to be answered from
    // the list as it stood when the analyst was looking at it.
    state.pageIds = data.claims.map((c) => c.claim_id);
    renderQueueBar();
    if (state.claim) markSelectedRow(state.claim);
    // Re-point the open claim at the freshly-loaded row. Without this `state.selectedClaim` keeps
    // whatever it was selected with, so the decision line, the stale badge and the re-investigate
    // confirmation all reason about data that may be several writes out of date.
    const reselected = data.claims.find((c) => c.claim_id === state.claim);
    if (reselected) {
      state.selectedClaim = reselected;
      renderDecision(reselected);
      syncOverrideButton();
    }
    setQueueMessage(data.total ? "" : "No claims match this filter.");
    const foot = queueFooter(data.total, data.total_amount_cents, isFiltered(state));
    $("queue-foot").classList.toggle("hidden", !data.total);
    $("foot-label").textContent = foot.label;
    $("foot-amount").textContent = foot.amount;
    const pager = pageStatus(state.page, state.size, data.total, data.claims.length);
    $("page-info").textContent = pager.label;
    $("prev").disabled = pager.prevDisabled;
    $("next").disabled = pager.nextDisabled;
  } catch (err) {
    console.error("loadQueue", err);
    showBanner("Couldn't load the worklist.", loadQueue);
  }
}

/** The retailers and reasons actually in this lot. Fetched once — the lot doesn't change under the
    analyst, and re-fetching it on every keystroke-debounced query would be noise. */
export async function loadFilterOptions() {
  if (!state.batchId) return;
  try {
    const opts = await fetchJSON(
      `/api/batches/${encodeURIComponent(state.batchId)}/filter-options`);
    fillOptions($("f-retailer"), opts.retailers, "All retailers", state.retailer);
    fillOptions($("f-reason"), opts.reasons, "All reasons", state.reason);
  } catch (err) {
    // Not banner-worthy: the dropdowns degrade to "All", and every other way of narrowing the
    // queue still works.
    console.error("loadFilterOptions", err);
  }
}

export function commit() {
  renderControls();
  syncHash();
  loadQueue();
}

export function setFilter(filter) {
  state.filter = filter;
  state.page = 1;
  commit();
}

/** Back/forward, or a hand-edited URL. parseHash sanitizes, so a stale bookmark naming a filter that
    no longer exists lands on the default instead of erroring — the API rejects unknown values with
    422, but that is about the API not lying, not about punishing an old link. */
async function applyHash() {
  Object.assign(state, parseHash(location.hash));
  renderControls();
  await loadQueue();
  await restoreSelection();
}

window.addEventListener("hashchange", () => {
  if (consumeOwnHashWrite()) return;
  applyHash();
});
