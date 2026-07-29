// The two DOM primitives the whole UI is built from. Imports nothing, so it can never be caught in
// an import cycle's temporal dead zone.

export const $ = (id) => document.getElementById(id);

/** Build an element. `text` goes in via textContent — never innerHTML, for any value that came from
    the DB or a model. Declared here rather than beside the document builders because the whole file
    now uses it. */
export function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
