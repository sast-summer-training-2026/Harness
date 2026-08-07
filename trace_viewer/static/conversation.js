const conversationVisibility = {
  system: true,
  tools: true
};

function cardJson(value) {
  return escapeHtml(JSON.stringify(value ?? null));
}

function collapsibleCard(className, label, content, raw, suffix = "") {
  return '<article class="message-card ' + className + '" role="button" tabindex="0" aria-expanded="false" data-card-json="' + cardJson(raw) + '">' +
    '<div class="message-label"><span class="role-dot ' + escapeHtml(className.replace("-card", "")) + '"></span>' +
    escapeHtml(label) + suffix + '<span class="card-json-hint" title="Inspect JSON">{}</span></div>' +
    content + '<pre class="inline-json hidden"></pre></article>';
}

function renderConversationOptions() {
  return '<div class="conversation-options" role="group" aria-label="Conversation sections">' +
    '<span class="conversation-options-title">Show</span>' +
    '<label class="conversation-option"><input type="checkbox" data-conversation-option="system"' + (conversationVisibility.system ? " checked" : "") + '>System prompt</label>' +
    '<label class="conversation-option"><input type="checkbox" data-conversation-option="tools"' + (conversationVisibility.tools ? " checked" : "") + '>Available tools</label>' +
  '</div>';
}

function renderSystemCards(value, source) {
  const blocks = Array.isArray(value) ? value : [value];
  return blocks.map((block, index) => {
    const normalized = typeof block === "object" && block !== null
      ? block
      : {type: "text", text: String(block ?? "")};
    const type = normalized.type || "field";
    return collapsibleCard("system-card", source + " - field " + String(index + 1).padStart(2, "0") + " - " + type, renderBlocks([normalized], "system"), normalized);
  }).join("");
}

function renderToolPreview(tool) {
  const description = tool?.description || "No description recorded.";
  const schema = tool?.input_schema || tool?.parameters;
  const schemaType = schema?.type || "object";
  const propertyCount = schema?.properties && typeof schema.properties === "object"
    ? Object.keys(schema.properties).length
    : 0;
  return '<div class="tool-definition-preview">' +
    '<p class="tool-description">' + escapeHtml(description) + '</p>' +
    '<div class="tool-schema-summary"><span>Input schema</span><strong>' + escapeHtml(schemaType) + '</strong><span>' + propertyCount + ' propert' + (propertyCount === 1 ? "y" : "ies") + '</span></div>' +
  '</div>';
}

function renderToolCards(value) {
  const tools = Array.isArray(value)
    ? value
    : value && typeof value === "object"
      ? Object.values(value)
      : [];
  if (!tools.length) return "";
  return '<section class="available-tools"><div class="flow-label">Available tools <span class="tool-count">' + tools.length + '</span></div>' +
    tools.map((tool, index) => {
      const name = tool?.name || "Unnamed tool";
      return collapsibleCard("tool-card", String(index + 1).padStart(2, "0") + " - " + name, renderToolPreview(tool), tool);
    }).join("") +
    '</section>';
}

renderMessage = function renderCollapsibleMessage(message) {
  const role = message?.role || "unknown";
  const content = message?.content ?? "";
  return collapsibleCard(role + "-card", role, renderBlocks(content, role), message);
};

renderConversation = function renderCollapsibleConversation() {
  const request = state.selected.forwarded_request || {};
  const body = request.body || {};
  const response = state.selected.response || {};
  const responseBody = response.body || {};
  const messages = Array.isArray(body.messages) ? body.messages : [];
  const requestMarkup = [
    conversationVisibility.tools && body.tools !== undefined && body.tools !== null ? renderToolCards(body.tools) : "",
    conversationVisibility.system && body.system !== undefined && body.system !== null ? renderSystemCards(body.system, "System prompt") : "",
    ...messages.flatMap((message) => message?.role === "system"
      ? conversationVisibility.system ? [renderSystemCards(message.content, "System message")] : []
      : [renderMessage(message)])
  ].join("");
  const responseMarkup = responseBody.content
    ? collapsibleCard("assistant-card", "Assistant response", renderBlocks(responseBody.content, "assistant"), responseBody.content, '<span class="message-status">' + escapeHtml(String(response.status_code ?? "")) + "</span>")
    : renderResponseSummary(response);
  $("#traceContent").innerHTML =
    '<div class="conversation-flow">' +
      renderConversationOptions() +
      '<div class="flow-label">Request context</div>' + (requestMarkup || '<div class="muted-block">No message content recorded.</div>') +
      '<div class="flow-connector"><span></span></div>' +
      '<div class="flow-label">Model response</div>' + responseMarkup +
    '</div>';
};

function toggleCard(card) {
  const panel = card.querySelector(".inline-json");
  if (!panel) return;
  if (!panel.dataset.ready) {
    try {
      panel.innerHTML = pretty(JSON.parse(card.dataset.cardJson));
      panel.dataset.ready = "1";
    } catch {
      panel.textContent = "Unable to decode this card JSON.";
    }
  }
  const expanded = !card.classList.contains("expanded");
  panel.classList.toggle("hidden", !expanded);
  card.classList.toggle("expanded", expanded);
  card.setAttribute("aria-expanded", String(expanded));
}

$("#traceContent").addEventListener("change", (event) => {
  const input = event.target.closest("input[data-conversation-option]");
  if (!input) return;
  conversationVisibility[input.dataset.conversationOption] = input.checked;
  renderConversation();
});

$("#traceContent").addEventListener("click", (event) => {
  const card = event.target.closest(".message-card[data-card-json]");
  if (!card || event.target.closest(".inline-json")) return;
  toggleCard(card);
});

$("#traceContent").addEventListener("keydown", (event) => {
  const card = event.target.closest(".message-card[data-card-json]");
  if (!card || event.target.closest(".inline-json")) return;
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    toggleCard(card);
  }
});
