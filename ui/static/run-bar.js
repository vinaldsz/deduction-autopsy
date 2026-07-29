// The lot-run bar: what the run is doing, and what it left behind.
//
// Split out of investigate.js, which used to be one line of `$("run-status").textContent = ...`.
// A failure list is real rendering, and a run that can now end four different ways (finished,
// cancelled, circuit-broken, transport failure) needs somewhere that owns clearing the last one.

import { $, el } from "./dom.js";

/** The live line: progress while running, the summary once it has stopped. */
export function setRunStatus(text) {
  $("run-status").textContent = text || "";
}

/** Enable/disable the Run button and show/hide Cancel — always together, because "running" is one
    state and the two controls disagreeing is how a lot gets started twice. */
export function setRunning(running) {
  $("run").disabled = running;
  $("run-cancel").classList.toggle("hidden", !running);
}

/** The claims that failed, named. Nothing is written to claim_resolutions for these, so they carry
    no queue-row badge — the grid stays a faithful view of the store and this list is the record. */
export function renderRunFailures(failures) {
  const box = $("run-failures");
  box.replaceChildren();
  box.classList.toggle("hidden", !failures.length);
  if (!failures.length) return;
  box.appendChild(el("div", "run-failures-head",
    `${failures.length} ${failures.length === 1 ? "claim" : "claims"} could not be investigated:`));
  const list = el("ul");
  for (const { claim_id, error } of failures) {
    const item = el("li");
    item.appendChild(el("span", "run-failure-id", claim_id));
    // The raw exception is kept, but as detail after the claim id rather than as the whole message.
    item.appendChild(el("span", "muted", ` — ${error}`));
    list.appendChild(item);
  }
  box.appendChild(list);
}
