// The worklist table and its selection UI. Renders rows and reflects the selection; never loads them.

import { ageLabel, dollars, selectAllState, sentenceCase, statusParts } from "./lib.js";
import { $, el } from "./dom.js";
import { state } from "./state.js";

/** The status cell: effective verdict, plus what it superseded. Shared with setRowStatus so a live
    run and a page load render the same thing. */
function statusCell(claim) {
  const p = statusParts(claim);
  const td = el("td", `status t-${p.tone}`);
  // Glyph, not the old coloured dot: a dot carries nothing in greyscale or to a colour-blind reader,
  // and it cost the same width as the mark that actually states the money direction.
  td.appendChild(el("span", "v-glyph", p.glyph));
  td.appendChild(el("span", null, p.label));
  // The raw machine verdict is NOT repeated here — the label is the scannable read, and at 420px the
  // extra word pushed the column past the pane and clipped itself. It lives on the review pane's
  // chip, which is where a single claim gets cross-referenced against the CLI and the API.
  if (p.superseded) td.appendChild(el("span", "status-sup", p.superseded));
  if (p.badge) td.appendChild(el("span", "disp-badge", p.badge));
  return td;
}

/** A queue row. `onOpen` is injected rather than imported: this module renders, and importing the
    action that opens a claim is the single edge that would make the module graph cyclic. */
export function renderRow(claim, onOpen) {
  const tr = document.createElement("tr");
  tr.dataset.claimId = claim.claim_id;
  tr.tabIndex = 0;
  const pick = el("td", "pick");
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = state.picked.has(claim.claim_id);
  box.setAttribute("aria-label", `Select ${claim.claim_id}`);
  // Stop the click reaching the row: checking a box is not opening the claim, and having it do both
  // means every bulk selection also loads an evidence pane nobody asked for.
  box.addEventListener("click", (e) => e.stopPropagation());
  box.addEventListener("change", () => setPicked(claim.claim_id, box.checked));
  pick.appendChild(box);
  tr.appendChild(pick);
  tr.appendChild(el("td", null, claim.claim_id));
  // po_id was returned and searchable from Layer 30b and never rendered, so a search that matched on
  // it showed rows with no visible reason for matching.
  tr.appendChild(el("td", null, claim.po_id));
  tr.appendChild(el("td", null, claim.retailer));
  tr.appendChild(el("td", null, sentenceCase(claim.claimed_reason)));
  tr.appendChild(el("td", "num", dollars(claim.claimed_amount)));
  tr.appendChild(el("td", "num", ageLabel(claim.age_days)));
  const priority = el("td");
  const pill = el("span", `pill p-${claim.priority}`, claim.priority);
  // Why this claim is in this band, for the screen reader and the hover. The visible statement of
  // the rule is the legend above the table — a title attribute alone would be keyboard-inaccessible.
  if (claim.priority_reason) {
    pill.title = claim.priority_reason;
    pill.setAttribute("aria-label", `${claim.priority} priority — ${claim.priority_reason}`);
  }
  priority.appendChild(pill);
  tr.appendChild(priority);
  tr.appendChild(statusCell(claim));
  const open = () => onOpen(claim);
  tr.addEventListener("click", open);
  tr.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
  return tr;
}

/** One message region under the table: no lot at all, or a filter that matched nothing. Without it
    both states rendered as a blank table and "0 of ", which reads as a broken page. */
export function setQueueMessage(msg) {
  const node = $("queue-msg");
  node.textContent = msg || "";
  node.classList.toggle("hidden", !msg);
}

// The selection is page-scoped and deliberately short-lived: it is cleared by every queue load, so it
// cannot survive a filter, sort, page or search change. A selection that outlives the rows it was made
// on is how an analyst accepts claims they can no longer see.

export function setPicked(claimId, on) {
  if (on) state.picked.add(claimId); else state.picked.delete(claimId);
  // Any change to the selection retires the last recorded-action line: left in place it would read as
  // a report on the rows now checked, which it says nothing about.
  state.actionNote = "";
  renderQueueBar();
}

export function clearPicked() {
  state.picked.clear();
  document.querySelectorAll("#rows .pick input").forEach((box) => { box.checked = false; });
  renderQueueBar();
}

/** The bar above the table carries two independent things: the live selection's controls, and a line
    saying what was last recorded — one claim or fifty.
 *
 *  The note lives out here rather than in the review pane because it has to survive the pane moving
 *  on: save-and-next re-renders the pane for the *next* claim, and a confirmation that dies with the
 *  claim it describes leaves a saved decision looking like nothing happened. */
export function renderQueueBar() {
  const sel = selectAllState(state.pageIds, state.picked);
  $("queue-bar").classList.toggle("hidden", sel.count === 0 && !state.actionNote);
  $("bulk-controls").classList.toggle("hidden", sel.count === 0);
  $("queue-note").textContent = state.actionNote;
  $("bulk-count").textContent = sel.countLabel;
  $("bulk-accept").textContent = sel.acceptLabel;
  // The select-all box reflects the page rather than driving it: with 3 of 25 checked it must not
  // read as "all selected".
  const all = $("pick-all");
  all.checked = sel.checked;
  all.indeterminate = sel.indeterminate;
}

export function setActionNote(text) {
  state.actionNote = text;
  renderQueueBar();
}

export function markSelectedRow(claimId) {
  document.querySelectorAll("tr.selected").forEach((tr) => tr.classList.remove("selected"));
  const tr = document.querySelector(`tr[data-claim-id="${claimId}"]`);
  if (tr) tr.classList.add("selected");
}

export function setRowStatus(claimId, verdict) {
  const tr = document.querySelector(`tr[data-claim-id="${claimId}"]`);
  if (!tr) return;
  tr.querySelector(".status").replaceWith(statusCell({ status: verdict }));
}
