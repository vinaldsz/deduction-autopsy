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
  "decision-pane.js": 1, "run-bar.js": 1,
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
 *  app failed to boot at all, and this test could not see it.
 *
 *  ONE LEFT-TO-RIGHT SCAN, not four independent regex passes. The passes could not see each other, so
 *  each quote style was matched against text the others had already rearranged: `disposition.js` has an
 *  apostrophe inside a *template literal* (which this function deliberately keeps), the single-quote
 *  pass read it as an opening quote, ran on to the next `'` several lines later, and the double-quote
 *  pass then desynced on the wreckage. Eleven lines of real code vanished — including a call to an
 *  import that had never been declared, which is precisely the defect the next test exists to catch.
 *  A scanner cannot desync that way: whichever delimiter opens first consumes to its own close.
 *
 *  Two behaviours are kept deliberately. Only a WHOLE-LINE `//` is a comment (a trailing one still
 *  counts as a reference, so an import kept alive only by a trailing comment stays "used" — changing
 *  that is a different test's business). And an unterminated quote is treated as an ordinary character
 *  rather than swallowing the rest of the file, so the worst case is a false positive instead of a
 *  silent blind spot. Regex literals are not parsed; none in `ui/static/` contains a quote character. */
function closingQuote(s, start, quote) {
  for (let i = start + 1; i < s.length; i++) {
    if (s[i] === "\\") { i++; continue; }
    if (s[i] === quote) return i;
    if (quote !== "`" && s[i] === "\n") return -1;  // a plain string never spans a line
  }
  return -1;
}

function codeOnly(text) {
  const s = String(text);
  let out = "";
  let i = 0;
  let blank = true;  // only spaces/tabs since the last newline of the INPUT
  while (i < s.length) {
    const c = s[i];
    if (c === "\n") { out += c; blank = true; i++; continue; }
    if (c === "/" && s[i + 1] === "*") {
      const end = s.indexOf("*/", i + 2);
      i = end === -1 ? s.length : end + 2;
      continue;
    }
    if (c === "/" && s[i + 1] === "/" && blank) {
      const nl = s.indexOf("\n", i);
      i = nl === -1 ? s.length : nl;  // leave the newline for the branch above
      continue;
    }
    if (c === "'" || c === '"' || c === "`") {
      const end = closingQuote(s, i, c);
      if (end !== -1) {
        // A plain string is blanked; a template literal is copied through, `${...}` and all.
        out += c === "`" ? s.slice(i, end + 1) : c + c;
        i = end + 1;
        blank = false;
        continue;
      }
    }
    out += c;
    if (c !== " " && c !== "\t") blank = false;
    i++;
  }
  return out.replace(/\.\.\./g, " ");
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

/** Names the file binds for itself: a local `const`/`let`/`var`, or a plain function parameter.
 *
 *  A binding of its own is not a reference to somebody else's export. `lib.js` has three functions
 *  taking a parameter called `state` — `queryParams(state, overrides)`, `buildHash(state)`,
 *  `isFiltered(state)` — and reading those as uses of `state.js` would demand an import that the layer
 *  rule forbids and that would put a browser-global module inside the one file guaranteed pure. This
 *  was invisible until codeOnly stopped desyncing; it is a false positive, not a finding.
 *
 *  Deliberately narrow: it cannot hide the failure this test exists for, because a module that merely
 *  *calls* an import (`syncHash()`, `dispositionLabel(...)`) binds nothing by that name. */
const localsOf = (f) => {
  const code = codeOnly(source[f]);
  const names = new Set();
  for (const m of code.matchAll(/(?:const|let|var)\s+([\w$]+)/g)) names.add(m[1]);
  for (const m of code.matchAll(/\(([^()]*)\)\s*(?:=>|\{)/g)) {
    for (const part of m[1].split(",")) {
      const n = part.trim().replace(/\s*=[\s\S]*$/, "");
      if (/^[\w$]+$/.test(n)) names.add(n);
    }
  }
  return names;
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
      const local = localsOf(f);
      for (const [name, home] of owner) {
        if (home === f || own.has(name) || imported.has(name) || local.has(name)) continue;
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
    //
    // Matched on identifier boundaries, not as a substring. `includes("document")` also matches the
    // word "documents", which lib.js says in a template literal the analyst reads — this check was
    // passing only because the old codeOnly happened to have deleted that line while desyncing.
    const code = codeOnly(source["lib.js"]);
    const forbidden = {
      document: /(?<![\w$.])document(?![\w$])/,
      window: /(?<![\w$.])window(?![\w$])/,
      "fetch()": /(?<![\w$.])fetch\s*\(/,
      localStorage: /(?<![\w$.])localStorage(?![\w$])/,
      EventSource: /(?<![\w$.])EventSource(?![\w$])/,
    };
    for (const [name, pattern] of Object.entries(forbidden)) {
      assert.ok(!pattern.test(code), `lib.js must not reference ${name}`);
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
