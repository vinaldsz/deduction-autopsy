// The error banner: a human sentence, never a raw exception, and a way out.

import { $ } from "./dom.js";

/** A human sentence, never a raw exception string, and a way out. */
export function showBanner(msg, retry) {
  $("banner-msg").textContent = msg;
  const retryBtn = $("banner-retry");
  retryBtn.classList.toggle("hidden", !retry);
  retryBtn.onclick = retry ? () => { hideBanner(); retry(); } : null;
  $("banner").classList.remove("hidden");
}

export function hideBanner() { $("banner").classList.add("hidden"); }
