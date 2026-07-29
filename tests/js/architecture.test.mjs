// `node --test tests/js/` — Node's built-in runner, no dependencies, no package.json.
//
// The frontend has no bundler and no framework, so nothing else checks that its modules fit together:
// a name used but not imported is a ReferenceError the browser only reports when the user happens to
// take that action, and `node --check` sees only syntax. These tests read the module graph itself.
//
// They also make the LAYERING a gate rather than an agreement. `ui/static/app.js` was a single
// 1230-line file; splitting it is only durable if "renderers never import actions" is enforced,
// because otherwise the monolith comes back one convenient import at a time.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const DIR = "ui/static";

/** Which layer each module sits in. A module may import from a LOWER layer, or from its own layer as
 *  long as the graph stays acyclic — it may never import upward.
 *
 *   0  leaves       import nothing but lib.js, so they can never sit in a cycle's temporal dead zone
 *   1  renderers    draw the page; they take data and callbacks, and never fetch or orchestrate
 *   2  actions      load data and drive the renderers
 *   3  entry        wiring and boot
 *
 *  A new module must be added here deliberately — an unlisted file fails the last test in this file,
 *  which is the point: choosing its layer is the design decision, not an afterthought. */
const LAYER = {
  "lib.js": 0, "dom.js": 0, "state.js": 0, "stream.js": 0, "api.js": 0,
  "banner.js": 1, "controls.js": 1, "queue-view.js": 1, "documents.js": 1, "evidence.js": 1,
  "decision-pane.js": 1,
  "dashboard.js": 2, "workspace.js": 2, "queue.js": 2, "selection.js": 2, "investigate.js": 2,
  "disposition.js": 2, "keyboard.js": 2,
  "app.js": 3,
};

const files = readdirSync(DIR).filter((f) => f.endsWith(".js")).sort();
const source = Object.fromEntries(files.map((f) => [f, readFileSync(join(DIR, f), "utf8")]));

/** Comments and plain strings removed, so a name merely *discussed* in a comment is not read as a
 *  reference. Template literals are left intact: they carry real `${...}` references.
 *
 *  Spread is normalised to whitespace. `...DEFAULT_STATE` otherwise looks like a property access to the
 *  `(?<![\w$.])` guard below, which is how `state.js` shipped using an import it never declared — the
 *  app failed to boot at all, and this test could not see it. */
function codeOnly(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^[ \t]*\/\/.*$/gm, "")
    .replace(/'(?:\\.|[^'\\])*'/g, "''")
    .replace(/"(?:\\.|[^"\\])*"/g, '""')
    .replace(/\.\.\./g, " ");
}

const IMPORT = /import\s*\{([\s\S]*?)\}\s*from\s*"\.\/([\w.-]+)";/g;
// A bare `import "./x.js";` — pulled in for a module-scope side effect. keyboard.js is loaded this way
// because it only registers a listener; it still counts as an edge for the layer and cycle rules.
const SIDE_EFFECT_IMPORT = /^import\s*"\.\/([\w.-]+)";/gm;
const EXPORT = /export\s+(?:async\s+function|function|const|let)\s+([\w$]+)/g;

const importsOf = (f) => [
  ...[...source[f].matchAll(IMPORT)].map(([, names, from]) => ({
    from, names: names.split(",").map((n) => n.trim()).filter(Boolean),
  })),
  ...[...source[f].matchAll(SIDE_EFFECT_IMPORT)].map(([, from]) => ({ from, names: [] })),
];
const exportsOf = (f) => new Set([...source[f].matchAll(EXPORT)].map((m) => m[1]));

/** Does `f` reference `name` as a value?
 *
 *  `(?<![\w$.])` rules out a property access (`p.label` is not the `label` module's export) and
 *  `(?!:)` rules out an object KEY — `decoder.decode(value, { stream: true })` is not a reference to
 *  stream.js, which is a false positive this test really produced. A ternary's `:` is preceded by a
 *  space, so it survives. */
const references = (f, name) => {
  const code = codeOnly(source[f]).replace(/import\s*\{[\s\S]*?\}\s*from\s*"[^"]+";/g, "");
  const pattern = name === "$"
    ? /(?<![\w$.])\$(?=\()/
    : new RegExp(`(?<![\\w$.])${name.replace(/\$/g, "\\$")}(?![\\w$])(?!:)`);
  return pattern.test(code);
};

describe("the module graph", () => {
  it("resolves every import to a real export", () => {
    for (const f of files) {
      for (const { from, names } of importsOf(f)) {
        assert.ok(source[from], `${f} imports from ${from}, which does not exist`);
        const available = exportsOf(from);
        for (const name of names) {
          assert.ok(available.has(name), `${f} imports ${name} from ${from}, which does not export it`);
        }
      }
    }
  });

  it("imports every name it uses from another module", () => {
    // The failure this prevents: a name is used, its import is dropped or never added, and the browser
    // throws a ReferenceError only when the user takes the one action that calls it. It really happened
    // during the split — `queue.js` called `syncHash()` with the import removed.
    const owner = new Map();
    for (const f of files) for (const name of exportsOf(f)) owner.set(name, f);

    for (const f of files) {
      const imported = new Set(importsOf(f).flatMap((i) => i.names));
      const own = exportsOf(f);
      for (const [name, home] of owner) {
        if (home === f || own.has(name) || imported.has(name)) continue;
        assert.ok(!references(f, name),
          `${f} uses ${name} (exported by ${home}) without importing it`);
      }
    }
  });

  it("imports nothing it does not use", () => {
    for (const f of files) {
      for (const { from, names } of importsOf(f)) {
        for (const name of names) {
          assert.ok(references(f, name), `${f} imports ${name} from ${from} and never uses it`);
        }
      }
    }
  });

  it("never imports upward through the layers", () => {
    // This is the rule that keeps the split from decaying. A renderer that imports an action becomes
    // an action, and the 1230-line file comes back one import at a time.
    for (const f of files) {
      for (const { from } of importsOf(f)) {
        assert.ok(LAYER[from] <= LAYER[f],
          `${f} (layer ${LAYER[f]}) imports ${from} (layer ${LAYER[from]}) — that is upward`);
      }
    }
  });

  it("has no import cycles", () => {
    // Cycles are not fatal in ESM for hoisted function declarations, but they ARE fatal for a `const`
    // touched while a cyclically-imported module is still evaluating — which is every shared binding
    // here (`state`, `$`, `stream`). Forbidding cycles outright is cheaper than auditing that rule.
    const graph = Object.fromEntries(files.map((f) => [f, importsOf(f).map((i) => i.from)]));
    const seen = new Map();
    const stack = [];
    const walk = (node) => {
      if (seen.get(node) === "done") return null;
      if (seen.get(node) === "open") return [...stack.slice(stack.indexOf(node)), node];
      seen.set(node, "open");
      stack.push(node);
      for (const next of graph[node] || []) {
        const cycle = walk(next);
        if (cycle) return cycle;
      }
      stack.pop();
      seen.set(node, "done");
      return null;
    };
    for (const f of files) {
      const cycle = walk(f);
      assert.equal(cycle, null, cycle && `import cycle: ${cycle.join(" -> ")}`);
    }
  });

  it("keeps lib.js pure — no DOM, no fetch", () => {
    // The boundary the whole test suite depends on: lib.js is the only frontend file `node --test` can
    // load, and it can only stay loadable if it never touches a browser global.
    const code = codeOnly(source["lib.js"]);
    for (const forbidden of ["document", "window", "fetch(", "localStorage", "EventSource"]) {
      assert.ok(!code.includes(forbidden), `lib.js must not reference ${forbidden}`);
    }
  });

  it("builds DOM only through el(), never innerHTML", () => {
    // Agent- and DB-supplied text reaches these modules; `receiving_records.notes` is a documented
    // prompt-injection surface. Layer 33 converted the last of it and this keeps it converted.
    for (const f of files) {
      const code = codeOnly(source[f]);
      for (const sink of ["innerHTML", "outerHTML", "insertAdjacentHTML"]) {
        assert.ok(!code.includes(sink), `${f} uses ${sink} — build nodes with el() instead`);
      }
    }
  });

  it("declares a layer for every module, and no module that is gone", () => {
    assert.deepEqual(files.slice().sort(), Object.keys(LAYER).sort());
  });
});

describe("the entry point", () => {
  it("reaches every module, so none is dead or silently unloaded", () => {
    // keyboard.js registers a document-level keydown listener and exports nothing anybody calls, so a
    // graph that never reaches it loses the whole keyboard path with no error at all — which is what
    // happened when the split was first generated.
    const graph = Object.fromEntries(files.map((f) => [f, importsOf(f).map((i) => i.from)]));
    const reached = new Set();
    const walk = (f) => {
      if (reached.has(f)) return;
      reached.add(f);
      (graph[f] || []).forEach(walk);
    };
    walk("app.js");
    const orphans = files.filter((f) => !reached.has(f));
    assert.deepEqual(orphans, [], `not reachable from app.js: ${orphans.join(", ")}`);
  });

  it("is the only module the page loads directly", () => {
    // Everything else arrives through the import graph, so index.html never has to be edited when the
    // split changes shape.
    const html = readFileSync(join(DIR, "index.html"), "utf8");
    const scripts = [...html.matchAll(/<script[^>]*src="\/([\w.-]+)"/g)].map((m) => m[1]);
    assert.deepEqual(scripts, ["app.js"]);
  });
});
