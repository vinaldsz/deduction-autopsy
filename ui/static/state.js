// The single view-state object, shared by every module.
//
// Never reassigned — only mutated — which is what makes a shared leaf safe: importers see the live
// binding. Everything in DEFAULT_STATE is mirrored in the URL hash and sanitized by parseHash; the rest
// is per-session view state that has no business in a shared link — including `appliedSort`, which is
// what the *server* reports sorting by, and is what the column indicator reads.

import { DEFAULT_STATE } from "./lib.js";

export const state = {
  ...DEFAULT_STATE,
  batchId: null, appliedSort: null, appliedDirection: null,
  // `selectedClaim` is the row DATA for state.claim, re-pointed by loadQueue on every reload.
  // `renderedClaim` is what the review pane is actually DISPLAYING. They are not the same question,
  // and conflating them put one claim's evidence under another claim's header — see restoreSelection.
  selectedClaim: null, renderedClaim: null,
  // The checked claims, and the page's row order (which is what j/k and save-and-next walk).
  // Neither is in the hash: a shared link that arrives with rows pre-checked is a trap, and the hash
  // describes a *view*. `picked` is cleared on every queue load — see loadQueue.
  picked: new Set(), pageIds: [], actionNote: "",
  // Which claim's stored trace has already been pulled into the audit drawer, so opening and closing
  // it doesn't re-fetch the largest artifact on disk.
  tracedClaim: null,
};
