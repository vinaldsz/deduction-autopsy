# Glossary

Two vocabularies meet in this repo: the CPG deductions trade, and the terms this system invents
on top of it. If a verdict or a table name doesn't make sense, the reason is usually here.

## The business

| Term | Meaning |
|---|---|
| **CPG** | Consumer packaged goods — the supplier side of this transaction. You make the product; the retailer sells it |
| **Deduction** (also **chargeback**) | Money a retailer withholds from an invoice payment, asserting the supplier owes it — a shortage, a promo discount, a compliance penalty. The retailer takes the money first and explains afterwards, which is why disputing them is a job |
| **Deduction analyst** | The person whose day is a queue of these claims: read the documents, decide whether the retailer is right, dispute the ones that aren't. The [analyst workspace](../README.md#the-analyst-workspace) is built for this seat |
| **Claim** | One deduction, with a stated reason and amount, against one purchase order. A retailer can file more than one claim per PO — which is how duplicates happen |
| **PO** — purchase order | What the retailer ordered: SKU, quantity, unit of measure, price, date. The spine of the entity graph; every other document hangs off it |
| **ASN** — advance ship notice (**EDI 856**) | What the supplier says it shipped, sent before the truck arrives. `856` is the EDI transaction-set number. One shipment can be **split** across two ASNs, and the total across all of them is what counts |
| **Invoice** | What the supplier billed. Its date must fall after the shipment it bills — an invoice dated *before* the ship date is a physical impossibility, not a typo |
| **Receiving record** | What the retailer's warehouse says it actually took in, plus free-text `notes` from the dock. Those notes routinely decide the verdict: a refused shortage, an approved substitution, a carrier-signed exception |
| **BOL** — bill of lading | The carrier's shipping document. Referenced in receiving notes ("carrier signed BOL exception") as evidence about who is responsible for a shortfall |
| **UOM** — unit of measure | `EACH`, `CASE` or `PALLET`. Documents disagree on it constantly, which is the single richest source of false shortages |
| **Case pack** | How many eaches are in a case, per SKU. 5 `CASE` and 120 `EACH` are the same shipment when the case pack is 24; compared raw they look like a 115-unit shortage |
| **Shortage** | The retailer says it received less than it was billed for. Sometimes true, often a UOM or split-shipment artefact |
| **Promo billback** | The retailer deducts a promotional discount it says was agreed. Valid only if a **trade agreement** covers that retailer, SKU *and* promo code |
| **Trade agreement** | The signed promo terms, held in a **TPM** (trade promotion management) system. Standalone — not linked to a PO — and matched on all three of retailer + SKU + promo code |
| **Compliance deduction** | A penalty for violating the retailer's routing/labelling/timing rules, rather than for goods |
| **Wrong item / SKU substitution** | A different SKU shipped than ordered. A dispute ground *unless* the receiving notes show it was pre-approved |
| **Credit memo** | A credit already issued against a claim. A prior claim whose notes mention one is the tell for a **duplicate claim** — the retailer billing twice for the same event |
| **Daily lot** | The batch of claims a retailer portal drops in one day (`LOT-2024-09-15`). The unit of work: ingest a lot, run it, work the queue |
| **Dispute packet** | The document you send back to argue a claim is invalid: normalized quantities, the timeline, and the grounds. Generated only for `INVALID` verdicts |

## The system

| Term | Meaning |
|---|---|
| **Investigator** | First agent (Haiku 4.5). Gathers every document via MCP tools, normalizes UOM, reconciles, proposes a verdict as a **CaseFile** |
| **Reviewer** | Second agent (Sonnet 4.5), separate prompt and model. Independently re-checks the highest-risk steps against the raw documents and returns `CONFIRM` / `OVERTURN` / `ESCALATE`. It never sees the Investigator's narrative reasoning |
| **CaseFile** | The Investigator's structured output: normalized quantities, timeline, conversions applied, prior claims, proposed verdict, confidence, reasoning. Schema-validated by the orchestrator before the Reviewer sees it — with `reasoning` stripped |
| **Verdict** | `VALID` (the retailer is right, pay it), `INVALID` (disputable, fight it), `ESCALATE` (a human must look). Three verdicts are recorded per run: the Investigator's, the Reviewer's, and the resolved **final** one |
| **Blocker** | A reason a claim cannot be honestly decided — a document absent from the store, or an investigation step never completed. Established by code, not by an agent, and it forces `ESCALATE` |
| **Resolution** vs **disposition** | `claim_resolutions` is what the *agents* concluded (never rewritten by a human — it is the audit trail). `claim_dispositions` is what the *analyst* decided. Two tables on purpose |
| **Effective verdict** | The analyst's decision when they have made one, otherwise the agents'. Derived at read time, which is what keeps every KPI and filter honest |
| **Stale decision** | A claim decided against one run that the agents have since re-run to a different answer. Badged, and deliberately *not* allowed to change the effective verdict — the machine changing its mind does not un-decide what a person signed |
| **Run** / `run_id` | One investigation of one claim. Artifacts land in `outputs/<claim_id>/<run_id>/`; runs are archived side by side, never overwritten, with `latest` a symlink |
| **Trace** | The tool-call record of a run. Because agents can only reach data through MCP tools, the trace *is* the audit trail — and the orchestrator verifies required calls actually appear in it |
| **MCP** — Model Context Protocol | The tool protocol the agents speak. Here: a FastMCP server over stdio, exposing 8 tools and no other data path |
| **Semantic layer** | `semantic_layer/` — the ETL that turns heterogeneous source files into the relational store. Extract → Transform → Load |
| **Quarantine** | `reject_rows`. A source row that fails coercion, validation or referential integrity is set aside with a reason rather than crashing the load |
| **DQ report** | The per-source data-quality summary printed by every ETL run: rows read, loaded, rejected, and why |
| **Lineage** | One row per loaded business row, pointing back to its exact source file and row. Makes provenance bidirectional: DB row → source, and source → DB row |
| **Fidelity oracle** | The frozen `scenarios/*/*.json` fixtures, no longer read at runtime, now used by a test that asserts the DB equals them field-for-field. It is what stops a transform rule from quietly moving ground truth |
| **Layer** | One unit of build in this project — planned in `docs/PLAN.md`, logged in `PROGRESS.md`, one commit, gates green before the next one starts |
