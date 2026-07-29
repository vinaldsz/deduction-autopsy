// Talking to the server. Nothing here knows about the DOM.

import { parseFrame, splitFrames } from "./lib.js";

/** fetch + JSON that fails loudly. Previously `resp.ok` was checked in one place and ignored in
    three, which is how a failed request became "the previous claim's data, silently". */
export async function fetchJSON(url, opts) {
  const resp = await fetch(url, opts);
  if (!resp.ok) throw new Error(`${resp.status} ${url}`);
  return resp.json();
}

/** Read an SSE response body, calling `onEvent(event, data)` per frame. Used for the batch run,
    which streams over POST; the single-claim drill-in uses EventSource instead (see stream.js). */
export async function streamSSE(url, opts, onEvent) {
  const resp = await fetch(url, opts);
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const { frames, rest } = splitFrames(buf);
    buf = rest;
    for (const block of frames) {
      const { event, data } = parseFrame(block);
      // A nameless frame is dropped: this server always labels its events, and inventing a default
      // would route an unlabelled payload to a handler that never asked for it.
      if (event) onEvent(event, data ? JSON.parse(data) : null);
    }
  }
}
