renderSessions = function renderSessionsWithAgents() {
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
    const mainItems = items.filter((item) => !item.isSubagent);
    const agents = new Map();
    for (const item of items.filter((item) => item.isSubagent)) {
      if (!agents.has(item.agentId)) agents.set(item.agentId, []);
      agents.get(item.agentId).push(item);
    }
    const active = state.selectedId && items.some((item) => item.id === state.selectedId);
    const agentMarkup = [...agents.entries()].map(([agentId, agentItems]) => {
      const agentActive = state.selectedId && agentItems.some((item) => item.id === state.selectedId);
      const agentLatest = agentItems[0];
      return '<button class="agent-item ' + (agentActive ? "selected" : "") + '" data-session="' + escapeHtml(session) + '" data-agent="' + escapeHtml(agentId) + '">' +
        '<span class="agent-branch">|--</span><span class="agent-copy"><strong>' + escapeHtml(shortId(agentId, 18)) + '</strong><small>' + agentItems.length + ' trace' + (agentItems.length === 1 ? "" : "s") + ' · ' + formatDate(agentLatest.timestamp) + '</small></span><span class="agent-chevron">></span></button>';
    }).join("");
    return '<div class="session-group">' +
      '<button class="session-item ' + (active && !state.selected?.agentId ? "selected" : "") + '" data-session="' + escapeHtml(session) + '">' +
        '<span class="session-icon">O</span><span class="session-copy"><strong>' + escapeHtml(shortId(session, 22)) + '</strong><small>' + mainItems.length + ' main trace' + (mainItems.length === 1 ? "" : "s") + ' · ' + items.length + ' total · ' + formatDate(latest.timestamp) + '</small></span><span class="session-chevron">></span>' +
      '</button>' +
      (agentMarkup ? '<div class="agent-list"><div class="agent-label">Subagents</div>' + agentMarkup + '</div>' : '') +
    '</div>';
  }).join("");
  $$(".session-item").forEach((button) => {
    button.addEventListener("click", () => {
      const first = state.items.find((item) => item.session === button.dataset.session && !item.isSubagent) ||
        state.items.find((item) => item.session === button.dataset.session);
      if (first) selectTrace(first.id);
    });
  });
  $$(".agent-item").forEach((button) => {
    button.addEventListener("click", () => {
      const first = state.items.find((item) => item.session === button.dataset.session && item.agentId === button.dataset.agent);
      if (first) selectTrace(first.id);
    });
  });
};
loadIndex();
