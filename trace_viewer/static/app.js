const state = {
  items: [],
  selectedId: null,
  selected: null,
  view: "conversation",
  filter: ""
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function pretty(value) {
  return escapeHtml(JSON.stringify(value ?? null, null, 2));
}

function formatDate(timestamp) {
  if (!timestamp) return "Unknown time";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  }).format(new Date(timestamp));
}

function formatNumber(value) {
  return value === undefined || value === null ? "-" : Number(value).toLocaleString();
}

function shortId(value, length = 16) {
  if (!value) return "unknown-session";
  return value.length > length ? value.slice(0, length) + "..." : value;
}

function pathFromUrl(url) {
  try {
    return new URL(url).pathname || "/";
  } catch {
    return url || "/";
  }
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("visible"), 2200);
}

function staticTraceUrl(id) {
  const path = id.split("/").map((part) => encodeURIComponent(part)).join("/");
  return "data/traces/" + path;
}

async function fetchJson(apiPath, staticPath) {
  const response = await fetch(apiPath, { cache: "no-store" });
  if (response.ok) return response.json();
  if (!staticPath) throw new Error("Request failed");
  const fallback = await fetch(staticPath, { cache: "no-store" });
  if (!fallback.ok) throw new Error("Could not read trace data");
  return fallback.json();
}

async function loadIndex(preferredId = state.selectedId) {
  setConnection("Loading", "loading");
  try {
    const payload = await fetchJson("api/traces", "data/index.json");
    state.items = payload.items || [];
    $("#reportLocation").textContent = payload.reports || "Watching reports";
    setConnection("Live", "live");
    renderSessions();
    const nextId = state.items.some((item) => item.id === preferredId)
      ? preferredId
      : state.items[0]?.id;
    if (nextId) await selectTrace(nextId);
    else showEmpty();
  } catch (error) {
    setConnection("Offline", "error");
    showToast(error.message);
    showEmpty("Viewer could not read the reports folder.");
  }
}

function setConnection(text, mode) {
  const el = $("#connectionState");
  el.innerHTML = '<span class="status-dot"></span>' + escapeHtml(text);
  el.className = "connection-state " + mode;
}

function visibleItems() {
  const query = state.filter.toLowerCase().trim();
  if (!query) return state.items;
  return state.items.filter((item) => [
    item.session, item.agentId, item.path, item.url, item.model, item.status
  ].join(" ").toLowerCase().includes(query));
}

function renderSessions() {
  const visible = visibleItems();
  const groups = new Map();
  for (const item of visible) {
    if (!groups.has(item.session)) groups.set(item.session, []);
    groups.get(item.session).push(item);
  }
  $("#sessionCount").textContent = String(groups.size);
  const list = $("#sessionList");
  if (!groups.size) {
    list.innerHTML = '<div class="list-empty">No matching sessions</div>';
    return;
  }
  list.innerHTML = [...groups.entries()].map(([session, items]) => {
    const latest = items[0];
    const active = state.selectedId && items.some((item) => item.id === state.selectedId);
    return '<button class="session-item ' + (active ? "selected" : "") + '" data-session="' + escapeHtml(session) + '">' +
      '<span class="session-icon">◌</span>' +
      '<span class="session-copy">' +
        '<strong>' + escapeHtml(shortId(session, 22)) + '</strong>' +
        '<small>' + items.length + ' trace' + (items.length === 1 ? "" : "s") + ' <span>·</span> ' + formatDate(latest.timestamp) + '</small>' +
      '</span>' +
      '<span class="session-chevron">›</span>' +
    '</button>';
  }).join("");
  $$(".session-item").forEach((button) => {
    button.addEventListener("click", () => {
      const first = state.items.find((item) => item.session === button.dataset.session);
      if (first) selectTrace(first.id);
    });
  });
}

async function selectTrace(id) {
  state.selectedId = id;
  renderSessions();
  $("#emptyState").classList.add("hidden");
  $("#traceView").classList.remove("hidden");
  $("#traceContent").innerHTML = '<div class="loading-panel"><div class="spinner"></div><span>Loading trace</span></div>';
  try {
    state.selected = await fetchJson("api/trace?id=" + encodeURIComponent(id), staticTraceUrl(id));
    renderTrace();
  } catch (error) {
    showToast(error.message);
  }
}

function showEmpty(message = "Choose a session from the left to inspect the request and response timeline.") {
  $("#traceView").classList.add("hidden");
  $("#emptyState").classList.remove("hidden");
  $("#emptyState p").textContent = message;
}

function currentMeta() {
  return state.selected?._meta || state.items.find((item) => item.id === state.selectedId) || {};
}

function renderTrace() {
  const meta = currentMeta();
  const request = state.selected.forwarded_request || {};
  $("#traceMethod").textContent = request.method || meta.method || "?";
  $("#traceMethod").className = "method-badge method-" + String(request.method || "").toLowerCase();
  $("#tracePath").textContent = pathFromUrl(request.url || meta.url);
  $("#traceTitle").textContent = meta.isSubagent ? "Subagent " + shortId(meta.agentId, 18) : (meta.model || "Anthropic request");
  const actor = meta.isSubagent ? '<span class="actor-tag subagent">Subagent</span>' : '<span class="actor-tag main">Main session</span>';
  $("#traceMeta").innerHTML = actor +
    '<span>' + escapeHtml(formatDate(meta.timestamp)) + '</span>' +
    '<span class="meta-separator">·</span><span>session ' + escapeHtml(shortId(meta.session, 24)) + '</span>' +
    '<span class="meta-separator">·</span><span>trace #' + escapeHtml(meta.sequence) + '</span>';
  renderStats(meta);
  renderSequence(meta);
  renderContent();
}

function renderStats(meta) {
  const usage = meta.usage || {};
  const response = state.selected.response || {};
  const body = response.body || {};
  const statItems = [
    ["Status", response.status_code ?? "-", response.status_code === 200 ? "good" : "warn"],
    ["Input tokens", formatNumber(usage.input), ""],
    ["Output tokens", formatNumber(usage.output), ""],
    ["Messages", formatNumber(meta.messageCount), ""],
    ["Tools exposed", formatNumber(meta.toolCount), ""]
  ];
  $("#traceStats").innerHTML = statItems.map(([label, value, tone]) =>
    '<div class="stat-card"><span>' + escapeHtml(label) + '</span><strong class="' + tone + '">' + escapeHtml(value) + '</strong></div>'
  ).join("");
}

function renderSequence(meta) {
  const sameSession = state.items.filter((item) => item.session === meta.session && (meta.agentId ? item.agentId === meta.agentId : !item.agentId)).sort((a, b) => Number(a.sequence) - Number(b.sequence));
  const index = sameSession.findIndex((item) => item.id === meta.id);
  const previous = sameSession[index - 1];
  const next = sameSession[index + 1];
  $("#traceSequence").innerHTML =
    '<button class="sequence-button" data-id="' + escapeHtml(previous?.id || "") + '"' + (previous ? "" : " disabled") + ' title="Previous trace">←</button>' +
    '<span>' + (index + 1) + ' / ' + sameSession.length + '</span>' +
    '<button class="sequence-button" data-id="' + escapeHtml(next?.id || "") + '"' + (next ? "" : " disabled") + ' title="Next trace">→</button>';
  $$(".sequence-button[data-id]").forEach((button) => {
    button.addEventListener("click", () => button.dataset.id && selectTrace(button.dataset.id));
  });
}

function renderContent() {
  $$(".view-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === state.view));
  if (state.view === "request") renderRequest();
  else if (state.view === "response") renderResponse();
  else renderConversation();
}

function renderConversation() {
  const request = state.selected.forwarded_request || {};
  const body = request.body || {};
  const response = state.selected.response || {};
  const responseBody = response.body || {};
  const messages = Array.isArray(body.messages) ? body.messages : [];
  const system = body.system;
  const requestMarkup = [
    system ? '<div class="message-card system-card"><div class="message-label"><span class="role-dot system"></span>System prompt</div>' + renderBlocks(system, "system") + '</div>' : "",
    ...messages.map((message) => renderMessage(message))
  ].join("");
  const responseMarkup = responseBody.content
    ? '<div class="message-card assistant-card"><div class="message-label"><span class="role-dot assistant"></span>Assistant response <span class="message-status">' + escapeHtml(String(response.status_code ?? "")) + '</span></div>' + renderBlocks(responseBody.content, "assistant") + '</div>'
    : renderResponseSummary(response);
  $("#traceContent").innerHTML =
    '<div class="conversation-flow">' +
      '<div class="flow-label">Request context</div>' + (requestMarkup || '<div class="muted-block">No message content recorded.</div>') +
      '<div class="flow-connector"><span></span></div>' +
      '<div class="flow-label">Model response</div>' + responseMarkup +
    '</div>';
}

function renderMessage(message) {
  const role = message?.role || "unknown";
  const content = message?.content ?? "";
  return '<div class="message-card ' + escapeHtml(role) + '-card"><div class="message-label"><span class="role-dot ' + escapeHtml(role) + '"></span>' + escapeHtml(role) + '</div>' + renderBlocks(content, role) + '</div>';
}

function renderBlocks(value, role) {
  const blocks = Array.isArray(value) ? value : [{ type: "text", text: value }];
  return blocks.map((block) => {
    if (typeof block !== "object" || block === null) {
      return '<div class="text-block">' + escapeHtml(block) + '</div>';
    }
    const type = block.type || "object";
    if (type === "text" || type === "thinking") {
      const label = type === "thinking" ? '<span class="block-type thinking">thinking</span>' : "";
      return '<div class="text-block ' + (type === "thinking" ? "thinking-block" : "") + '">' + label + '<p>' + escapeHtml(block.text || block.thinking || "") + '</p></div>';
    }
    if (type === "tool_use") {
      return '<div class="tool-block"><div class="tool-heading"><span class="tool-icon">⚙</span><strong>' + escapeHtml(block.name || "tool_use") + '</strong><span class="block-type">tool call</span></div><pre>' + pretty(block.input || {}) + '</pre></div>';
    }
    if (type === "tool_result") {
      return '<div class="tool-block result"><div class="tool-heading"><span class="tool-icon">↳</span><strong>Tool result</strong></div><pre>' + pretty(block.content ?? block) + '</pre></div>';
    }
    return '<div class="tool-block"><div class="tool-heading"><strong>' + escapeHtml(type) + '</strong></div><pre>' + pretty(block) + '</pre></div>';
  }).join("");
}

function renderRequest() {
  const request = state.selected.forwarded_request || {};
  const body = request.body || {};
  $("#traceContent").innerHTML =
    '<div class="json-layout">' +
      renderHeaderPanel("Forwarded headers", request.headers) +
      '<section class="json-card"><div class="panel-heading"><div><span class="eyebrow">Payload</span><h3>Request body</h3></div><span class="file-chip">' + escapeHtml(metaLabel("size")) + '</span></div><pre class="json-view">' + pretty(body) + '</pre></section>' +
    '</div>';
}

function renderResponse() {
  const response = state.selected.response || {};
  const body = response.body || {};
  $("#traceContent").innerHTML =
    '<div class="json-layout">' +
      renderHeaderPanel("Response headers", response.headers) +
      '<section class="json-card"><div class="panel-heading"><div><span class="eyebrow">Payload</span><h3>Response body</h3></div><span class="file-chip">HTTP ' + escapeHtml(response.status_code) + '</span></div><pre class="json-view">' + pretty(body) + '</pre></section>' +
    '</div>';
}

function renderHeaderPanel(title, headers) {
  const rows = Object.entries(headers || {}).map(([key, value]) =>
    '<div class="header-row"><code>' + escapeHtml(key) + '</code><span>' + escapeHtml(value) + '</span></div>'
  ).join("");
  return '<section class="json-card"><div class="panel-heading"><div><span class="eyebrow">Headers</span><h3>' + escapeHtml(title) + '</h3></div><span class="file-chip">' + Object.keys(headers || {}).length + '</span></div><div class="header-list">' + (rows || '<div class="muted-block">No headers recorded.</div>') + '</div></section>';
}

function renderResponseSummary(response) {
  const body = response.body || {};
  const message = body.error?.message || body.message || "No response content recorded.";
  return '<div class="response-summary ' + (response.status_code === 200 ? "success" : "failure") + '"><span class="response-icon">' + (response.status_code === 200 ? "✓" : "!") + '</span><div><strong>' + escapeHtml(response.status_code === 200 ? "Request completed" : "Request failed") + '</strong><p>' + escapeHtml(message) + '</p></div></div>';
}

function metaLabel(key) {
  const meta = currentMeta();
  if (key === "size") return formatNumber(meta.size) + " bytes";
  return "";
}

$("#searchInput").addEventListener("input", (event) => {
  state.filter = event.target.value;
  renderSessions();
});

$("#refreshButton").addEventListener("click", () => loadIndex());
$("#rawButton").addEventListener("click", async () => {
  if (!state.selected) return;
  await navigator.clipboard.writeText(JSON.stringify(state.selected, null, 2));
  showToast("Raw JSON copied to clipboard");
});
$("#copyIdButton").addEventListener("click", async () => {
  if (!state.selectedId) return;
  await navigator.clipboard.writeText(state.selectedId);
  showToast("Trace ID copied to clipboard");
});
$$(".view-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    state.view = tab.dataset.view;
    renderContent();
  });
});



