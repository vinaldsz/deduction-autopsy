// The keyboard path. The keymap itself is `keyAction` in lib.js, where it is tested; this is the
// wiring plus the two things it needs from the page.

import { isFieldNode, keyAction, nextClaimId } from "./lib.js";
import { $ } from "./dom.js";
import { setPicked } from "./queue-view.js";
import { state } from "./state.js";

// The keymap itself is `lib.js::keyAction`, which is pure and therefore tested. Everything here is the
// DOM half: what "inField" means, and what each action does. `docs/PLAN.md`'s `s` (send to human) has
// no binding — Layer 32 removed that control because the analyst is the human it would have sent to.

/** Is the caret somewhere that owns the keystroke? The rule itself is in lib.js, where it is tested;
    this is only the `document.activeElement` lookup it needs. */
function inField() {
  return isFieldNode(document.activeElement);
}

function moveSelection(delta) {
  const target = nextClaimId(state.pageIds, state.claim, delta);
  if (!target) return;
  const tr = document.querySelector(`tr[data-claim-id="${CSS.escape(target)}"]`);
  if (tr) tr.click();
}

document.addEventListener("keydown", (e) => {
  const action = keyAction({
    key: e.key, ctrlKey: e.ctrlKey, metaKey: e.metaKey, altKey: e.altKey, shiftKey: e.shiftKey,
    inField: inField(),
  });
  if (!action) return;
  e.preventDefault();
  switch (action) {
    case "next": moveSelection(1); break;
    case "prev": moveSelection(-1); break;
    case "accept": {
      // Via the button, not postDisposition directly, so the keyboard cannot reach a decision the
      // mouse is forbidden from making — `disabled` covers the un-investigated claim.
      const accept = document.querySelector('.decision [data-disp="accept"]');
      if (accept && !accept.disabled && !$("w-decision-actions").classList.contains("hidden")) {
        accept.click();
      }
      break;
    }
    // Focus, never submit: an override needs a verdict *and* a stated reason, so there is deliberately
    // no keystroke that records one.
    case "override": if (state.claim) $("w-override-verdict").focus(); break;
    case "toggle-select": {
      if (!state.claim) break;
      const box = document.querySelector(
        `tr[data-claim-id="${CSS.escape(state.claim)}"] .pick input`);
      if (box) { box.checked = !box.checked; setPicked(state.claim, box.checked); }
      break;
    }
    case "search": $("search").focus(); break;
    case "blur":
      if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
      break;
  }
});
