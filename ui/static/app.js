// Analyst workspace: wiring and boot.
//
// Every listener is registered here, before the boot sequence's first await, so the page is interactive
// from the moment it parses. Pure logic lives in lib.js (tested by `node --test`); the DOM and fetch
// work is split across the modules below, layered so nothing imports upward:
//
//     dom / state / stream / api  ->  renderers  ->  actions  ->  this file
//
// tests/js/architecture.test.mjs enforces that, so it stays true.

import { parseHash } from "./lib.js";
import { hideBanner } from "./banner.js";
import { renderControls } from "./controls.js";
import { loadDashboard } from "./dashboard.js";
import { syncOverrideButton } from "./decision-pane.js";
import { postDisposition } from "./disposition.js";
import { $ } from "./dom.js";
import { investigateClaim, runBatch } from "./investigate.js";
import { commit, loadFilterOptions, loadQueue, setFilter } from "./queue.js";
import { clearPicked, renderQueueBar } from "./queue-view.js";
import { bulkAccept } from "./selection.js";
import { state } from "./state.js";
// Imported for its side effect and nothing else: keyboard.js registers the document-level keydown
// listener at module scope, so if nobody imports it the whole keyboard path silently does not exist.
import "./keyboard.js";
import { maybeLoadTrace, restoreSelection } from "./workspace.js";

// --- wiring --------------------------------------------------------------------------------------

$("run").addEventListener("click", runBatch);

$("banner-dismiss").addEventListener("click", hideBanner);

$("prev").addEventListener("click", () => { state.page = Math.max(1, state.page - 1); commit(); });

$("next").addEventListener("click", () => { state.page += 1; commit(); });

$("page-size").addEventListener("change", (e) => {
  // Back to page 1: page 4 of 25-per-page does not exist at 100 per page, and landing on an empty
  // table after changing a row count reads as a broken filter.
  state.size = Number(e.target.value);
  state.page = 1;
  commit();
});

$("w-investigate").addEventListener("click", () => {
  if (!state.claim) return;
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
  investigateClaim(state.claim);
});

$("w-audit").addEventListener("toggle", maybeLoadTrace);

$("w-override-verdict").addEventListener("change", syncOverrideButton);

$("w-note").addEventListener("input", syncOverrideButton);

document.querySelectorAll("#tabs .tab").forEach((t) =>
  t.addEventListener("click", () => setFilter(t.dataset.filter)));

document.querySelectorAll("#kpis .card[data-filter]").forEach((c) =>
  c.addEventListener("click", () => setFilter(c.dataset.filter)));

// Sorting lives on the column headers only. No KPI card sorts any more — one that looked like the
// filter cards but reordered the table instead taught that a card's behaviour is unguessable.
document.querySelectorAll("th.sortable").forEach((th) =>
  th.addEventListener("click", () => {
    // Clicking the column you are already sorted by flips the direction the SERVER applied. Clicking
    // a different one hands the choice back with `direction: null`, because the useful first click
    // per column (desc for money and age, asc for ids and names) is defined in ui/queries.py and
    // mirroring that table here would be a second copy free to drift.
    state.direction = th.dataset.sort === state.appliedSort
      ? (state.appliedDirection === "asc" ? "desc" : "asc")
      : null;
    state.sort = th.dataset.sort;
    state.page = 1;
    commit();
  }));

document.querySelectorAll(".decision [data-disp]").forEach((b) =>
  b.addEventListener("click", () => postDisposition(b.dataset.disp)));

// Retailer / reason / date narrowing. `|| null` rather than "": an empty <select> or a cleared date
// input means "no filter", and buildHash omits nulls so a cleared control leaves the URL too.
for (const [id, key] of [["f-retailer", "retailer"], ["f-reason", "reason"],
                         ["f-from", "date_from"], ["f-to", "date_to"]]) {
  $(id).addEventListener("change", (e) => {
    state[key] = e.target.value || null;
    state.page = 1;
    commit();
  });
}

$("bulk-accept").addEventListener("click", bulkAccept);

$("bulk-clear").addEventListener("click", clearPicked);

// This page only. There is no "select all N filtered" control anywhere, on purpose.
$("pick-all").addEventListener("change", (e) => {
  for (const claimId of state.pageIds) {
    if (e.target.checked) state.picked.add(claimId); else state.picked.delete(claimId);
  }
  document.querySelectorAll("#rows .pick input").forEach((box) => { box.checked = e.target.checked; });
  state.actionNote = "";
  renderQueueBar();
});

$("f-clear").addEventListener("click", () => {
  Object.assign(state, { retailer: null, reason: null, date_from: null, date_to: null, page: 1 });
  commit();
});

let searchTimer = null;
$("search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.q = e.target.value.trim(); state.page = 1; commit(); }, 250);
});

(async () => {
  // The URL first: it is the description of what to show, and loadQueue needs the batch id, so the
  // dashboard and the lot's filter options have to land in between.
  Object.assign(state, parseHash(location.hash));
  await loadDashboard();
  await loadFilterOptions();
  renderControls();
  await loadQueue();
  await restoreSelection();
})();
