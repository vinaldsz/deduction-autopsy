// `node --test tests/js/` — Node's built-in runner, no dependencies, no package.json.
//
// `stream.js` touches no DOM, so unlike the rest of ui/static it can simply be imported and tested.
// Worth doing: it replaced a module-level `let source` that two separate modules reassigned, and the
// thing it protects is easy to get wrong quietly — if the previous claim's stream is not ended, one
// claim's tool_call events append into another claim's trace drawer.
import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";

import { closeStream, stream } from "../../ui/static/stream.js";

/** Stands in for an EventSource: the holder only ever calls close(). */
const fakeSource = () => ({ closed: 0, close() { this.closed += 1; } });

describe("stream", () => {
  beforeEach(() => { stream.current = null; });

  it("starts with nothing open", () => {
    assert.equal(stream.current, null);
  });

  it("closes the live stream and forgets it, so the two cannot come apart", () => {
    // The bug this shape prevents: `source.close()` without `source = null` left a closed stream in
    // the variable, and every later `if (source)` guard read as "still running".
    const s = fakeSource();
    stream.current = s;
    closeStream();
    assert.equal(s.closed, 1);
    assert.equal(stream.current, null);
  });

  it("is safe to call when nothing is open", () => {
    // Called on every claim selection, most of which have no stream running.
    assert.doesNotThrow(closeStream);
    assert.equal(stream.current, null);
  });

  it("does not close the same stream twice", () => {
    const s = fakeSource();
    stream.current = s;
    closeStream();
    closeStream();
    assert.equal(s.closed, 1);
  });

  it("closes the stream that is current, not the one that used to be", () => {
    // Starting a new run replaces the holder's contents; the old one was already closed by then, and
    // closing the wrong one would kill the run that just started.
    const first = fakeSource();
    stream.current = first;
    closeStream();
    const second = fakeSource();
    stream.current = second;
    closeStream();
    assert.equal(first.closed, 1);
    assert.equal(second.closed, 1);
  });
});
