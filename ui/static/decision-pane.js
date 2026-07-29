// The decision bar's display: what was recorded, and whether Override may fire.
//
// A leaf on purpose. Three separate modules drive it and it calls none of them, which is what keeps the
// decision bar out of the module graph's cycles.

import { decisionSummary, overrideGuard } from "./lib.js";
import { $, el } from "./dom.js";
import { state } from "./state.js";

/** The recorded decision, with the two things the schema always stored but the UI never showed:
    when it was made, and what the analyst wrote. */
export function renderDecision(claim) {
  const box = $("w-disp-current");
  box.replaceChildren();
  const summary = decisionSummary(claim);
  if (!summary) {
    box.textContent = "No analyst decision recorded yet.";
    return;
  }
  box.appendChild(document.createTextNode(summary.text));
  if (summary.stale) {
    box.appendChild(el("span", "stale-badge", "re-investigated since you decided"));
  }
  if (summary.note) box.appendChild(el("div", "disp-note", `“${summary.note}”`));
}

// A verdict can only be accepted or overridden once one exists. Leaving these live on an
// un-investigated claim invited "accept" on nothing at all.
export function setDecisionEnabled(enabled) {
  $("w-decision-actions").classList.toggle("hidden", !enabled);
  $("w-decision-blocked").classList.toggle("hidden", enabled);
}

/** Override stays disabled until there is both a verdict and a reason. The server enforces this too
    (422) — this is so the analyst sees why the button won't fire, not the only line of defense. */
export function syncOverrideButton() {
  const btn = document.querySelector('.decision [data-disp="override"]');
  const guard = overrideGuard({
    chosen: $("w-override-verdict").value,
    reason: $("w-note").value,
    agentVerdict: state.selectedClaim ? state.selectedClaim.agent_status : null,
  });
  btn.disabled = guard.disabled;
  btn.title = guard.title;
}
