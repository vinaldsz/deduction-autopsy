/** The live single-claim EventSource, in a holder rather than a module `let`.
 *
 *  Two modules legitimately need to end the stream — opening another claim (workspace) and starting a
 *  new run (investigate) — and an imported ESM binding cannot be reassigned by the importer, so a bare
 *  `let source` cannot be shared. The holder is also the safer shape: `closeStream()` is the only way
 *  to end one, so "close it and forget it" cannot be half-done.
 *
 *  Ending the previous claim's stream matters: without it one claim's tool_call events append into
 *  another claim's trace drawer. */
export const stream = { current: null };

export function closeStream() {
  if (stream.current) { stream.current.close(); stream.current = null; }
}
