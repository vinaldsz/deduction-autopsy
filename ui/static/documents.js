// The source-document cards: the analyst's primary evidence, straight from the DB.
//
// Every value goes in through `el`'s textContent — these are DB strings, and `receiving_records.notes`
// is a documented prompt-injection surface.

import { dollars, sentenceCase, verdictLabel } from "./lib.js";
import { $, el } from "./dom.js";

function docCard(title, id, body) {
  const card = el("div", "doc");
  const h = el("div", "doc-h");
  h.appendChild(el("span", null, title));
  if (id) h.appendChild(el("span", "doc-id", id));
  card.appendChild(h);
  card.appendChild(body);
  return card;
}

function kvTable(rows) {
  const t = el("table", "doc-t"), tb = el("tbody");
  for (const [k, v, num] of rows) {
    const tr = el("tr");
    tr.appendChild(el("th", null, k));
    tr.appendChild(el("td", num ? "num" : null, v == null ? "—" : String(v)));
    tb.appendChild(tr);
  }
  t.appendChild(tb);
  return t;
}

function rowTable(headers, items, mapFn) {
  const t = el("table", "doc-t"), thead = el("thead"), htr = el("tr");
  for (const h of headers) htr.appendChild(el("th", null, h));
  thead.appendChild(htr); t.appendChild(thead);
  const tb = el("tbody");
  for (const it of items) {
    const tr = el("tr");
    for (const [val, num, note] of mapFn(it)) {
      const cls = num ? "num" : (note ? "note-cell" : null);
      tr.appendChild(el("td", cls, val == null || val === "" ? "—" : String(val)));
    }
    tb.appendChild(tr);
  }
  t.appendChild(tb); return t;
}

function emptyNode(msg) {
  const e = el("div", "doc-empty", msg);
  e.style.padding = "8px 10px";
  return e;
}

export function renderReason(claim, notes) {
  const box = $("w-reason"); box.replaceChildren();
  // Reason leads the sentence, so sentenceCase reads correctly without a lowercase round-trip.
  box.appendChild(el("div", "lead",
    `${sentenceCase(claim.claimed_reason)} claimed by ${claim.retailer} for ${dollars(claim.claimed_amount)} · filed ${claim.claim_date}`));
  if (notes) box.appendChild(el("div", "notes", `“${notes}”`));
}

export function renderDocuments(docs) {
  const wrap = $("w-docs"); wrap.replaceChildren();
  const po = docs.purchase_order;
  if (po) {
    wrap.appendChild(docCard("Purchase order", po.po_id, kvTable([
      ["SKU", po.sku], ["Ordered", `${po.ordered_qty} ${po.ordered_uom}`],
      ["Unit price", dollars(po.unit_price), true], ["Order date", po.order_date],
    ])));
  }
  wrap.appendChild(docCard(`ASN / shipments (${docs.asns.length})`, null,
    docs.asns.length
      ? rowTable(["ASN", "Shipped", "Ship date", "Carrier"], docs.asns,
          (a) => [[a.asn_id], [`${a.shipped_qty} ${a.shipped_uom}`, true], [a.ship_date], [a.carrier]])
      : emptyNode("No ASNs on file")));
  wrap.appendChild(docCard(`Invoices (${docs.invoices.length})`, null,
    docs.invoices.length
      ? rowTable(["Invoice", "Invoiced", "Amount", "Date"], docs.invoices,
          (i) => [[i.invoice_id], [`${i.invoiced_qty} ${i.invoiced_uom}`, true], [dollars(i.amount), true], [i.invoice_date]])
      : emptyNode("No invoices on file")));
  wrap.appendChild(docCard(`Receiving (${docs.receiving_records.length})`, null,
    docs.receiving_records.length
      ? rowTable(["Receipt", "Received", "Date", "Notes"], docs.receiving_records,
          (r) => [[r.receipt_id], [`${r.received_qty} ${r.received_uom}`, true], [r.receipt_date], [r.notes, false, true]])
      : emptyNode("No receiving records on file")));
  if (docs.trade_agreements.length) {
    wrap.appendChild(docCard(`Trade agreements (${docs.trade_agreements.length})`, null,
      rowTable(["Agreement", "Promo", "Terms", "Valid", "Signed by"], docs.trade_agreements,
        (t) => [[t.agreement_id], [t.promo_code], [t.discount_terms], [`${t.valid_from} – ${t.valid_to}`], [t.signed_by]])));
  }
  if (docs.prior_claims.length) {
    wrap.appendChild(docCard(`Prior claims on this PO (${docs.prior_claims.length})`, null,
      rowTable(["Claim", "Reason", "Amount", "Date", "Verdict"], docs.prior_claims,
        (c) => [[c.claim_id], [sentenceCase(c.claimed_reason)], [dollars(c.claimed_amount), true],
                [c.claim_date], [verdictLabel(c.final_verdict).label]])));
  }
}
