const app = document.querySelector("#app");
let snapshot = null;
let lastCall = null;

function el(tag, attrs, kids) {
  const node = document.createElement(tag);
  const props = attrs || {};
  Object.keys(props).forEach((key) => {
    const value = props[key];
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value == null ? "" : String(value);
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (value != null) node.setAttribute(key, String(value));
  });
  (kids || []).forEach((kid) => {
    if (kid == null) return;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  });
  return node;
}

function effectWord(effect) {
  if (effect === "require_approval") return "Hold";
  if (effect === "allow") return "Allow";
  if (effect === "deny") return "Deny";
  if (effect === "release") return "Release";
  if (effect === "error") return "Error";
  return effect || "";
}

async function loadSnapshot() {
  const response = await fetch("/api/snapshot", { cache: "no-store" });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "The policy could not be read.");
  snapshot = body;
  return body;
}

function renderShell(data) {
  app.replaceChildren(el("div", { class: "wrap" }, [
    el("header", { class: "mast" }, [
      el("div", { class: "mark" }, [
        mark(),
        el("div", {}, [
          el("h1", { text: data.product.title }),
          el("p", { text: "An org chart for what an agent may spend, send, and open." }),
        ]),
      ]),
      el("div", {}, [
        el("p", { class: "org-name" }, [
          data.org.name,
          el("span", { text: data.org.unit || "Local policy" }),
        ]),
      ]),
      el("div", { class: "meta" }, [
        el("div", { text: "deny by default" }),
        el("div", { text: "policy " + data.policy_hash }),
        el("div", { text: "manifest " + data.manifest_hash }),
      ]),
    ]),
    el("div", { class: "layout" }, [
      el("section", { class: "panel", id: "org" }, [
        el("h2", { text: "Org chart" }),
        el("div", { id: "tree" }),
      ]),
      el("aside", { class: "panel try", id: "try" }, [
        el("h2", { text: "Try a call" }),
        el("div", { id: "try-body" }),
      ]),
    ]),
    el("section", { class: "tape", id: "tape" }),
    el("p", { class: "foot" }, [
      "Loopback only. No telemetry. Builtin finance tools do not use the network. ",
      "Protected paths stay denied: ",
      el("code", { text: data.protected.slice(0, 4).join(", ") }),
      ".",
    ]),
  ]));
  renderTree(data);
  renderTry(data);
  renderTape(data);
}

function mark() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", "36");
  svg.setAttribute("height", "36");
  svg.setAttribute("viewBox", "0 0 32 32");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = "<rect width='32' height='32' rx='3' fill='#1c1915'/><path d='M7 16h11a5 5 0 1 0-4.2-7.7' fill='none' stroke='#f3ecdc' stroke-width='2'/><circle cx='23' cy='16' r='2.2' fill='#f3ecdc'/>";
  return svg;
}

function renderTree(data) {
  const root = document.querySelector("#tree");
  const list = el("ul", { class: "tree" });
  data.tree.forEach((role) => list.append(roleBranch(role)));
  root.replaceChildren(list);
}

function roleBranch(role) {
  const item = el("li", { class: "branch" }, [roleCard(role)]);
  const nested = el("ul", { class: "tree" });
  role.agents.forEach((agent) => nested.append(el("li", { class: "branch" }, [agentCard(agent)])));
  role.children.forEach((child) => nested.append(roleBranch(child)));
  if (nested.childNodes.length) item.append(nested);
  return item;
}

function roleCard(role) {
  const facts = el("ul", { class: "facts" });
  if (role.spend) facts.append(el("li", {}, [el("b", { text: "Spend" }), role.spend.label]));
  const mail = []
    .concat(role.recipients.domains.map((domain) => "*@" + domain))
    .concat(role.recipients.addresses);
  if (mail.length) facts.append(el("li", {}, [el("b", { text: "Mail" }), mail.join("  ")]));
  ["files", "urls", "tables"].forEach((kind) => {
    const allow = role.scopes[kind].allow;
    if (allow.length) facts.append(el("li", {}, [el("b", { text: kind }), allow.join(", ")]));
  });
  role.approvals.forEach((rule) => {
    facts.append(el("li", {}, [el("b", { text: "Hold" }), rule.label]));
  });
  return el("article", { class: "card role" }, [
    el("span", { class: "kind", text: role.inherits ? "inherits " + role.inherits : "role" }),
    el("h3", { text: role.name }),
    role.description ? el("p", { class: "desc", text: role.description }) : null,
    chips(role.tools),
    facts,
  ]);
}

function agentCard(agent) {
  const spend = agent.spend;
  const usage = agent.usage;
  const meters = el("div");
  if (spend && spend.per_day_cents) {
    meters.append(meter("Today", usage.today_cents, spend.per_day_cents, usage.today_label, spend.per_day_label));
  }
  if (spend && spend.total_cents) {
    meters.append(meter("Total", usage.total_cents, spend.total_cents, usage.total_label, spend.total_label));
  }
  return el("article", { class: "card agent", "data-agent": agent.name }, [
    el("span", { class: "kind", text: "agent · " + agent.role }),
    el("h3", { text: agent.name }),
    agent.description ? el("p", { class: "desc", text: agent.description }) : null,
    spend ? el("p", { class: "desc", text: spend.label }) : el("p", { class: "desc", text: "No spend ceiling. Money movement is denied." }),
    meters,
  ]);
}

function meter(label, used, cap, usedLabel, capLabel) {
  const pct = cap > 0 ? Math.min(100, Math.round((used / cap) * 100)) : 0;
  const fill = el("span");
  fill.style.width = pct + "%";
  return el("div", { class: "meter" }, [
    el("div", { class: "row" }, [
      el("span", { text: label }),
      el("span", { text: usedLabel + " / " + capLabel }),
    ]),
    el("div", { class: "track" }, [fill]),
  ]);
}

function chips(tools) {
  return el("div", { class: "chips" }, tools.map((tool) => el("span", { class: "chip", text: tool })));
}

function renderTry(data) {
  const body = document.querySelector("#try-body");
  const preset = el("select", { id: "preset", onchange: onPreset }, [
    el("option", { value: "", text: "Custom call" }),
    ...data.presets.map((item) => el("option", { value: item.id, text: item.label })),
  ]);
  const agent = el("select", { id: "agent" }, data.agent_details.map((item) => el("option", { value: item.name, text: item.name })));
  const tool = el("select", { id: "tool" }, data.tools.map((item) => el("option", { value: item.name, text: item.name })));
  const args = el("textarea", { id: "args", spellcheck: "false" }, ["{}"]);
  const blurb = el("p", { class: "blurb", id: "blurb", text: "Pick a scripted call, or edit the JSON and check it against the live policy." });
  body.replaceChildren(
    el("label", { class: "field" }, [el("span", { text: "Preset" }), preset]),
    blurb,
    el("label", { class: "field" }, [el("span", { text: "Agent" }), agent]),
    el("label", { class: "field" }, [el("span", { text: "Tool" }), tool]),
    el("label", { class: "field" }, [el("span", { text: "Arguments" }), args]),
    el("div", { class: "actions" }, [
      el("button", { id: "check", type: "button", onclick: onCheck, text: "Check" }),
      el("button", { id: "approve", class: "secondary", type: "button", onclick: onApprove, hidden: "hidden", text: "Approve and retry" }),
    ]),
    el("div", { class: "result", id: "result" }, [
      el("p", { class: "blurb", text: "No call yet." }),
    ]),
  );
  const requested = new URLSearchParams(location.search).get("preset");
  const initial = data.presets.find((item) => item.id === requested) || data.presets[0];
  if (initial) {
    preset.value = initial.id;
    applyPreset(initial);
    if (new URLSearchParams(location.search).get("run") === "1") onCheck();
  }
}

function onPreset(event) {
  const id = event.target.value;
  const found = (snapshot.presets || []).find((item) => item.id === id);
  const blurb = document.querySelector("#blurb");
  if (!found) {
    blurb.textContent = "Edit the JSON and check it against the live policy.";
    return;
  }
  applyPreset(found);
}

function applyPreset(preset) {
  document.querySelector("#agent").value = preset.agent;
  const tool = document.querySelector("#tool");
  if (![...tool.options].some((option) => option.value === preset.tool)) {
    tool.append(el("option", { value: preset.tool, text: preset.tool }));
  }
  tool.value = preset.tool;
  document.querySelector("#args").value = JSON.stringify(preset.arguments, null, 2);
  document.querySelector("#blurb").textContent = preset.blurb;
}

async function onCheck() {
  const button = document.querySelector("#check");
  button.disabled = true;
  try {
    const payload = readForm();
    lastCall = payload;
    const response = await fetch("/api/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "Check failed.");
    renderResult(body);
    await refreshLive();
  } catch (error) {
    renderResult({ effect: "error", rule: "client", reason: error.message, tool_result: null });
  } finally {
    button.disabled = false;
  }
}

async function onApprove() {
  if (!lastCall) return;
  const button = document.querySelector("#approve");
  button.disabled = true;
  try {
    const approved = await fetch("/api/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...lastCall, by: "board-operator" }),
    });
    const approval = await approved.json();
    if (!approved.ok) throw new Error(approval.error || "Approval failed.");
    await onCheck();
  } catch (error) {
    renderResult({ effect: "error", rule: "client", reason: error.message, tool_result: null });
  } finally {
    button.disabled = false;
  }
}

function readForm() {
  let args;
  try {
    args = JSON.parse(document.querySelector("#args").value || "{}");
  } catch (error) {
    throw new Error("Arguments are not valid JSON.");
  }
  if (!args || typeof args !== "object" || Array.isArray(args)) {
    throw new Error("Arguments must be a JSON object.");
  }
  return {
    agent: document.querySelector("#agent").value,
    tool: document.querySelector("#tool").value,
    arguments: args,
  };
}

function renderResult(body) {
  const approve = document.querySelector("#approve");
  approve.hidden = body.effect !== "require_approval";
  const preview = body.tool_result == null ? null : el("pre", { class: "preview", text: JSON.stringify(body.tool_result, null, 2) });
  const nodes = [
    el("div", { class: "stamp " + body.effect, text: effectWord(body.effect) }),
    el("p", { class: "reason", text: body.reason || "" }),
    body.rule ? el("p", { class: "rule", text: body.rule }) : null,
    preview,
  ].filter((node) => node != null);
  document.querySelector("#result").replaceChildren(...nodes);
}

function renderTape(data) {
  const section = document.querySelector("#tape");
  const rows = (data.audit || []).slice().reverse();
  const table = el("table", {}, [
    el("thead", {}, [
      el("tr", {}, ["When", "Effect", "Agent", "Tool", "Reason"].map((label) => el("th", { text: label }))),
    ]),
    el("tbody", {}, rows.map((event) => el("tr", {}, [
      el("td", { class: "when", text: (event.ts || "").replace("T", " ").replace("Z", " UTC") }),
      el("td", {}, [el("span", { class: "effect " + (event.effect || ""), text: effectWord(event.effect) })]),
      el("td", { text: event.agent || "" }),
      el("td", { class: "tool", text: event.tool || "" }),
      el("td", { text: event.reason || "" }),
    ]))),
  ]);
  section.replaceChildren(
    el("div", { class: "tape-head" }, [
      el("h2", { text: "Audit tape" }),
      el("p", { text: data.audit_count + " lines · live" }),
    ]),
    rows.length ? table : el("p", { class: "empty", text: "The tape is empty. Run python -m agentleash demo, or check a call." }),
  );
}

async function refreshLive() {
  const data = await loadSnapshot();
  renderTree(data);
  renderTape(data);
}

async function boot() {
  try {
    const data = await loadSnapshot();
    renderShell(data);
    window.setInterval(() => {
      refreshLive().catch((error) => showError(error.message));
    }, 2000);
  } catch (error) {
    showError(error.message);
  }
}

function showError(message) {
  const banner = el("p", { class: "banner", text: message });
  if (!document.querySelector(".wrap")) {
    app.replaceChildren(banner);
    return;
  }
  const existing = document.querySelector(".banner");
  if (existing) existing.remove();
  document.querySelector(".wrap").prepend(banner);
}

boot();
