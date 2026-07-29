// The filter/sort controls and the URL hash — the view's own description of itself.

import { buildHash, sentenceCase, sortIndicator } from "./lib.js";
import { $, el } from "./dom.js";
import { state } from "./state.js";

/** The column headers' ▲/▼, from the sort the server reports applying rather than the one requested
    — `direction` goes out null most of the time, meaning "your choice". */
export function renderSortIndicators() {
  document.querySelectorAll("th.sortable").forEach((th) => {
    const mark = sortIndicator(th.dataset.sort, state.appliedSort, state.appliedDirection);
    th.classList.toggle("sorted", Boolean(mark));
    let ind = th.querySelector(".sort-ind");
    if (!ind) { ind = el("span", "sort-ind"); th.appendChild(ind); }
    ind.textContent = mark;
  });
}

export function fillOptions(select, values, allLabel, selected) {
  select.replaceChildren(el("option", null, allLabel));
  select.firstChild.value = "";
  for (const value of values) {
    const option = el("option", null, sentenceCase(value));
    option.value = value;
    select.appendChild(option);
  }
  select.value = selected || "";
}

// The hash is the single description of "what am I looking at": filter, sort, search, page, size, the
// three narrowing filters, and the open claim. Every control writes to `state` then calls commit(),
// which writes the hash and reloads — so a link is always shareable and a refresh always lands where
// the analyst was.

/** Set while we are the ones writing the hash, so our own write doesn't come back through
    `hashchange` and re-parse the state we just built. Back/forward still work: those fire
    `hashchange` without us having written anything.

    Private, with `consumeOwnHashWrite` as the only way to read it. It used to be a module-level `let`
    shared with the hashchange listener, which cannot survive a module split — an imported binding
    can't be assigned by the importer — and the flag is better as a stated protocol anyway: exactly one
    caller may claim a write, and claiming it clears it. */
let writingHash = false;

export function syncHash() {
  const next = buildHash(state);
  if (next === (location.hash || "")) return;
  writingHash = true;
  location.hash = next;
}

/** Was the hashchange now firing our own write? Clears the flag, so it answers true exactly once. */
export function consumeOwnHashWrite() {
  if (!writingHash) return false;
  writingHash = false;
  return true;
}

/** Push the current state into the controls that display it. Needed because state can arrive from
    the URL (a deep link, or the back button) and not only from a click. */
export function renderControls() {
  document.querySelectorAll("#tabs .tab").forEach(
    (t) => t.classList.toggle("active", t.dataset.filter === state.filter));
  document.querySelectorAll("#kpis .card[data-filter]").forEach(
    (c) => c.classList.toggle("active", c.dataset.filter === state.filter));
  $("search").value = state.q;
  $("f-retailer").value = state.retailer || "";
  $("f-reason").value = state.reason || "";
  $("f-from").value = state.date_from || "";
  $("f-to").value = state.date_to || "";
  $("page-size").value = String(state.size);
  const narrowed = Boolean(state.retailer || state.reason || state.date_from || state.date_to);
  $("f-clear").classList.toggle("hidden", !narrowed);
}
