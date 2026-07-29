// The KPI strip, and the only writer of `state.batchId` — which is why it runs first at boot.

import { dollarsCompact, lotSubtitle, priorityLegend, todoSplit } from "./lib.js";
import { fetchJSON } from "./api.js";
import { showBanner } from "./banner.js";
import { $ } from "./dom.js";
import { state } from "./state.js";

export async function loadDashboard() {
  try {
    const d = await fetchJSON("/api/dashboard");
    $("sub").textContent = lotSubtitle(d);
    // The two cards partition the lot, so each number is exactly the rows its tab returns.
    $("m-todo").textContent = d.todo_count;
    $("m-todo-split").textContent = todoSplit(d);
    $("m-decided").textContent = d.decided_count;
    $("m-decided-of").textContent = `of ${d.lot_total} in the lot`;
    $("m-open").textContent = dollarsCompact(d.open_amount_cents);
    const p = d.priority_breakdown;
    $("m-priority").textContent = `${p.HIGH}/${p.MEDIUM}/${p.LOW}`;
    $("m-oldest").textContent = d.oldest_open_days ? `${d.oldest_open_days}d` : "—";
    // Generated from the server's own thresholds, so the stated rule and the applied rule are the
    // same rule. A priority pill that never says what put it there is a number on trust.
    $("priority-legend").textContent = priorityLegend(d.priority_thresholds);
    state.batchId = d.batch ? d.batch.batch_id : null;
  } catch (err) {
    // Log the real error: the catch also covers render bugs, and without this a
    // TypeError in here is indistinguishable from the network being down.
    console.error("loadDashboard", err);
    showBanner("Couldn't load the dashboard metrics.", loadDashboard);
  }
}
