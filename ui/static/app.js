// Thin client over the Layer 19/20 API. No framework, no build step.
// Uses the SSE /stream endpoint so tool calls render live as each agent makes them.

const scenarioSelect = document.getElementById("scenario");
const claimInput = document.getElementById("claim-id");
const runButton = document.getElementById("run");
const trace = document.getElementById("trace");
const traceEmpty = document.getElementById("trace-empty");
const verdictSection = document.getElementById("verdict");
const disputeBlock = document.getElementById("dispute");
const disputeList = document.getElementById("dispute-list");
const usageEl = document.getElementById("usage");
const banner = document.getElementById("banner");

let source = null;

// Populate the scenario dropdown from /api/scenarios; each option carries its fixed claim id.
async function loadScenarios() {
  const resp = await fetch("/api/scenarios");
  const { scenarios } = await resp.json();
  for (const s of scenarios) {
    const opt = document.createElement("option");
    opt.value = s.scenario;
    opt.textContent = s.scenario;
    opt.dataset.claimId = s.claim_id;
    scenarioSelect.appendChild(opt);
  }
  syncClaimId();
}

function syncClaimId() {
  const opt = scenarioSelect.selectedOptions[0];
  claimInput.value = opt ? opt.dataset.claimId : "";
}

function showBanner(message) {
  banner.textContent = message;
  banner.classList.remove("hidden");
}

function appendToolCall(data) {
  traceEmpty.classList.add("hidden");
  const li = document.createElement("li");
  if (data.is_error) li.classList.add("error");
  const agent = document.createElement("span");
  agent.className = "agent-tag agent-" + data.agent;
  agent.textContent = data.agent;
  const name = document.createElement("span");
  name.className = "tool-name";
  name.textContent = data.name;
  const args = document.createElement("span");
  args.className = "tool-args";
  args.textContent = " " + JSON.stringify(data.args);
  li.append(agent, name, args);
  trace.appendChild(li);
}

function setVerdict(id, value) {
  const el = document.getElementById(id);
  el.textContent = value;
  el.className = "v " + value; // color via .v.VALID / .v.INVALID / ...
}

function renderDone(data) {
  setVerdict("v-investigator", data.investigator_verdict);
  setVerdict("v-reviewer", data.reviewer_verdict);
  setVerdict("v-final", data.final_verdict);
  document.getElementById("v-confidence").textContent = data.confidence;

  disputeList.innerHTML = "";
  if (data.final_verdict === "INVALID" && data.dispute_grounds && data.dispute_grounds.length) {
    for (const ground of data.dispute_grounds) {
      const li = document.createElement("li");
      li.textContent = ground;
      disputeList.appendChild(li);
    }
    disputeBlock.classList.remove("hidden");
  } else {
    disputeBlock.classList.add("hidden");
  }

  const u = data.usage;
  usageEl.textContent =
    `tokens — investigator: ${u.investigator.prompt_tokens} in / ${u.investigator.completion_tokens} out · ` +
    `reviewer: ${u.reviewer.prompt_tokens} in / ${u.reviewer.completion_tokens} out`;

  verdictSection.classList.remove("hidden");
}

function finish() {
  if (source) {
    source.close();
    source = null;
  }
  runButton.disabled = false;
  runButton.textContent = "Run investigation";
}

function run() {
  const scenario = scenarioSelect.value;
  const claimId = claimInput.value;
  if (!scenario || !claimId) return;

  // Reset previous run.
  trace.innerHTML = "";
  traceEmpty.classList.remove("hidden");
  verdictSection.classList.add("hidden");
  banner.classList.add("hidden");
  runButton.disabled = true;
  runButton.textContent = "Running…";

  const url = `/api/claims/${encodeURIComponent(claimId)}/stream?scenario=${encodeURIComponent(scenario)}`;
  source = new EventSource(url);

  source.addEventListener("tool_call", (e) => appendToolCall(JSON.parse(e.data)));
  source.addEventListener("done", (e) => {
    renderDone(JSON.parse(e.data));
    finish();
  });
  // In-band SSE error event (pipeline failure after the stream opened).
  source.addEventListener("error", (e) => {
    if (e.data) {
      showBanner(JSON.parse(e.data).error);
      finish();
    }
    // No e.data => native EventSource connection error; onerror handles it.
  });
  // Connection-level failure (e.g. 404 before the stream opened, dropped connection).
  source.onerror = () => {
    if (source && source.readyState === EventSource.CLOSED) {
      showBanner("Connection to the investigation stream failed.");
      finish();
    }
  };
}

scenarioSelect.addEventListener("change", syncClaimId);
runButton.addEventListener("click", run);
loadScenarios();
