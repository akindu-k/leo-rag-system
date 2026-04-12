/* ── Leo RAG Chat Application ─────────────────────────────────────────────── */
const API = "/api/v1";

const state = {
  token: localStorage.getItem("leo_token"),
  user: null,
  currentSessionId: null,
  sessions: [],
  isStreaming: false,
};

/* ── Utils ─────────────────────────────────────────────────────────────────── */
const $ = (sel, ctx = document) => ctx.querySelector(sel);
const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

function toast(msg, type = "success", duration = 3000) {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), duration);
}

function formatTime(isoStr) {
  const d = new Date(isoStr);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function escapeHtml(str) {
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/* Very light markdown renderer (bold, italic, bullet lists, code) */
function renderMarkdown(text) {
  // Code blocks
  text = text.replace(/```([\s\S]*?)```/g, (_, code) => `<pre><code>${escapeHtml(code.trim())}</code></pre>`);
  // Inline code
  text = text.replace(/`([^`]+)`/g, (_, c) => `<code>${escapeHtml(c)}</code>`);
  // Bold
  text = text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  // Italic
  text = text.replace(/\*(.+?)\*/g, "<em>$1</em>");
  // Bullet lines
  const lines = text.split("\n");
  let inList = false;
  const out = [];
  for (const line of lines) {
    if (/^[-•]\s+/.test(line)) {
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push(`<li>${line.replace(/^[-•]\s+/, "")}</li>`);
    } else {
      if (inList) { out.push("</ul>"); inList = false; }
      out.push(line ? `<p>${line}</p>` : "");
    }
  }
  if (inList) out.push("</ul>");
  return out.join("");
}

/* ── Auth API ──────────────────────────────────────────────────────────────── */
async function apiRequest(method, path, body = null, isForm = false) {
  const headers = {};
  if (state.token) headers["Authorization"] = `Bearer ${state.token}`;
  if (!isForm && body) headers["Content-Type"] = "application/json";

  const res = await fetch(`${API}${path}`, {
    method,
    headers,
    body: body ? (isForm ? body : JSON.stringify(body)) : null,
  });

  if (res.status === 401) {
    logout();
    return null;
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Unknown error" }));
    let detail = err.detail;
    // FastAPI 422s return detail as an array of validation error objects
    if (Array.isArray(detail)) {
      detail = detail.map((e) => e.msg || JSON.stringify(e)).join(" · ");
    }
    throw new Error(detail || "Request failed");
  }

  if (res.status === 204) return null;
  return res.json();
}

/* ── Auth UI ────────────────────────────────────────────────────────────────── */
function showAuthModal(mode = "login") {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.id = "auth-modal";

  const isLogin = mode === "login";
  overlay.innerHTML = `
    <div class="modal">
      <h2>${isLogin ? "Sign in" : "Create account"}</h2>
      <p>${isLogin ? "Welcome back to Leo RAG" : "Join the Leo RAG system"}</p>
      <div id="auth-alert" style="display:none" class="alert alert-error"></div>
      ${!isLogin ? `<div class="field"><label>Full Name</label><input id="auth-name" type="text" placeholder="Your name"></div>` : ""}
      <div class="field"><label>Email</label><input id="auth-email" type="email" placeholder="you@example.com" autocomplete="email"></div>
      <div class="field"><label>Password</label><input id="auth-pass" type="password" placeholder="••••••••" autocomplete="${isLogin ? "current-password" : "new-password"}"></div>
      <button class="btn-primary" id="auth-submit">${isLogin ? "Sign in" : "Create account"}</button>
      <div class="modal-switch">
        ${isLogin ? 'No account? <a id="auth-switch">Sign up</a>' : 'Have an account? <a id="auth-switch">Sign in</a>'}
      </div>
    </div>
  `;

  document.body.appendChild(overlay);

  $("#auth-switch", overlay).addEventListener("click", () => {
    overlay.remove();
    showAuthModal(isLogin ? "register" : "login");
  });

  $("#auth-submit", overlay).addEventListener("click", async () => {
    const email = $("#auth-email", overlay).value.trim();
    const pass = $("#auth-pass", overlay).value;
    const alertEl = $("#auth-alert", overlay);

    try {
      alertEl.style.display = "none";
      if (isLogin) {
        const data = await apiRequest("POST", "/auth/login", { email, password: pass });
        state.token = data.access_token;
        localStorage.setItem("leo_token", state.token);
      } else {
        const name = ($("#auth-name", overlay)?.value || "").trim();
        await apiRequest("POST", "/auth/register", { email, password: pass, full_name: name || undefined });
        const data = await apiRequest("POST", "/auth/login", { email, password: pass });
        state.token = data.access_token;
        localStorage.setItem("leo_token", state.token);
      }
      overlay.remove();
      await initApp();
    } catch (err) {
      alertEl.textContent = err.message;
      alertEl.style.display = "block";
    }
  });

  // Enter key
  overlay.addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("#auth-submit", overlay).click();
  });
}

function logout() {
  state.token = null;
  state.user = null;
  state.currentSessionId = null;
  state.sessions = [];
  localStorage.removeItem("leo_token");
  renderSidebar();
  renderMessages([]);
  showAuthModal("login");
}

/* ── Sessions ───────────────────────────────────────────────────────────────── */
async function loadSessions() {
  try {
    const data = await apiRequest("GET", "/sessions");
    state.sessions = data?.items || [];
  } catch {
    state.sessions = [];
  }
}

async function createSession() {
  const data = await apiRequest("POST", "/sessions", { title: "New Chat" });
  state.sessions.unshift(data);
  state.currentSessionId = data.id;
  renderSidebar();
  renderMessages([]);
}

async function selectSession(id) {
  state.currentSessionId = id;
  renderSidebar();
  try {
    const data = await apiRequest("GET", `/sessions/${id}`);
    const messages = data.messages || [];
    renderMessages(messages);
  } catch (e) {
    toast("Failed to load session", "error");
  }
}

async function deleteSession(id, e) {
  e.stopPropagation();
  await apiRequest("DELETE", `/sessions/${id}`);
  state.sessions = state.sessions.filter((s) => s.id !== id);
  if (state.currentSessionId === id) {
    state.currentSessionId = null;
    renderMessages([]);
  }
  renderSidebar();
}

/* ── Render ─────────────────────────────────────────────────────────────────── */
function renderSidebar() {
  const list = $("#sessions-list");
  list.innerHTML = "";

  for (const s of state.sessions) {
    const item = document.createElement("div");
    item.className = `session-item${s.id === state.currentSessionId ? " active" : ""}`;
    item.innerHTML = `
      <span class="session-item-title" title="${escapeHtml(s.title)}">${escapeHtml(s.title)}</span>
      <button class="session-item-del" data-id="${s.id}" title="Delete">✕</button>
    `;
    item.addEventListener("click", () => selectSession(s.id));
    $(".session-item-del", item).addEventListener("click", (e) => deleteSession(s.id, e));
    list.appendChild(item);
  }

  // User info
  const userEl = $("#user-info");
  if (state.user && userEl) {
    const initial = (state.user.full_name || state.user.email)[0].toUpperCase();
    userEl.innerHTML = `
      <div class="user-avatar">${initial}</div>
      <span class="user-email">${escapeHtml(state.user.email)}</span>
    `;
  }
}

function renderMessages(messages) {
  const container = $("#chat-messages");
  container.innerHTML = "";

  if (!messages.length) {
    container.innerHTML = `
      <div class="welcome">
        <div class="welcome-icon">💬</div>
        <h2>Leo Movement Assistant</h2>
        <p>Ask any question about the indexed documents. I will answer strictly from the available sources and cite every claim.</p>
      </div>`;
    return;
  }

  for (const msg of messages) {
    container.appendChild(buildMessageEl(msg));
  }
  container.scrollTop = container.scrollHeight;
}

function buildMessageEl(msg) {
  const isUser = msg.role === "user";
  const wrapper = document.createElement("div");
  wrapper.className = `message ${msg.role}`;

  const initial = isUser
    ? (state.user?.full_name || state.user?.email || "U")[0].toUpperCase()
    : "L";

  const citationsHtml = buildCitationsHtml(msg.citations || []);

  wrapper.innerHTML = `
    <div class="message-avatar">${initial}</div>
    <div class="message-body">
      <div class="message-bubble" id="bubble-${msg.id || "tmp"}">${
        isUser ? escapeHtml(msg.content) : renderMarkdown(msg.content)
      }</div>
      ${citationsHtml}
      <div class="message-time">${msg.created_at ? formatTime(msg.created_at) : ""}</div>
    </div>`;

  return wrapper;
}

function buildCitationsHtml(citations) {
  if (!citations.length) return "";
  const cards = citations
    .map(
      (c) => `
    <div class="citation-card">
      <div class="citation-header">
        <span class="citation-title">${escapeHtml(c.document_title || c.file_name || "Source")}</span>
        ${c.page_number ? `<span class="citation-page">p. ${c.page_number}</span>` : ""}
      </div>
      ${c.section_title ? `<div class="citation-section">§ ${escapeHtml(c.section_title)}</div>` : ""}
      ${c.excerpt ? `<div class="citation-excerpt">${escapeHtml(c.excerpt)}</div>` : ""}
    </div>`
    )
    .join("");

  const uid = Math.random().toString(36).slice(2);
  return `
    <div class="citations">
      <button class="citations-toggle" onclick="toggleCitations('${uid}')">
        <span>▶</span> ${citations.length} source${citations.length > 1 ? "s" : ""}
      </button>
      <div class="citations-list" id="cit-${uid}" style="display:none">${cards}</div>
    </div>`;
}

window.toggleCitations = (uid) => {
  const list = $(`#cit-${uid}`);
  const btn = list?.previousElementSibling;
  if (!list) return;
  const hidden = list.style.display === "none";
  list.style.display = hidden ? "flex" : "none";
  list.style.flexDirection = "column";
  if (btn) btn.querySelector("span").textContent = hidden ? "▼" : "▶";
};

/* ── Streaming chat ─────────────────────────────────────────────────────────── */
async function sendMessage() {
  if (state.isStreaming) return;
  const input = $("#message-input");
  const content = input.value.trim();
  if (!content) return;

  // Ensure we have a session
  if (!state.currentSessionId) {
    await createSession();
  }

  input.value = "";
  input.style.height = "auto";
  state.isStreaming = true;
  $("#send-btn").disabled = true;

  // Render user message
  const userMsg = { role: "user", content, created_at: new Date().toISOString() };
  appendMessage(userMsg);

  // Render bot typing indicator
  const typingEl = document.createElement("div");
  typingEl.className = "message assistant";
  typingEl.id = "typing-el";
  typingEl.innerHTML = `
    <div class="message-avatar">L</div>
    <div class="message-body">
      <div class="message-bubble">
        <div class="typing-indicator">
          <div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>
        </div>
      </div>
    </div>`;
  $("#chat-messages").appendChild(typingEl);
  scrollBottom();

  try {
    const res = await fetch(`${API}/chat/${state.currentSessionId}/messages`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${state.token}`,
      },
      body: JSON.stringify({ content }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Server error" }));
      throw new Error(err.detail || "Server error");
    }

    // Replace typing indicator with real bubble
    typingEl.remove();
    const botWrapper = document.createElement("div");
    botWrapper.className = "message assistant";
    const bubbleId = "bot-bubble-" + Date.now();
    botWrapper.innerHTML = `
      <div class="message-avatar">L</div>
      <div class="message-body">
        <div class="message-bubble" id="${bubbleId}"></div>
        <div class="message-time" id="${bubbleId}-time"></div>
      </div>`;
    $("#chat-messages").appendChild(botWrapper);

    const bubble = $(`#${bubbleId}`);
    let fullText = "";
    let citations = [];
    let messageId = null;

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n\n");
      buffer = lines.pop(); // keep incomplete chunk

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        try {
          const event = JSON.parse(line.slice(6));
          if (event.type === "token") {
            fullText += event.content;
            bubble.innerHTML = renderMarkdown(fullText);
            scrollBottom();
          } else if (event.type === "done") {
            messageId = event.message_id;
            citations = event.citations || [];
          } else if (event.type === "error") {
            throw new Error(event.message);
          }
        } catch (parseErr) {
          if (parseErr.message !== "Unexpected end of JSON input") {
            throw parseErr;
          }
        }
      }
    }

    // Render citations
    if (citations.length) {
      const citHtml = buildCitationsHtml(citations);
      const bodyEl = botWrapper.querySelector(".message-body");
      bodyEl.insertAdjacentHTML("beforeend", citHtml);
    }
    $(`#${bubbleId}-time`).textContent = formatTime(new Date().toISOString());

    // Update session title in sidebar
    const sessIdx = state.sessions.findIndex((s) => s.id === state.currentSessionId);
    if (sessIdx !== -1 && state.sessions[sessIdx].title === "New Chat") {
      state.sessions[sessIdx].title = content.slice(0, 60);
      renderSidebar();
    }
  } catch (err) {
    $("#typing-el")?.remove();
    toast(err.message || "Failed to get response", "error");
  } finally {
    state.isStreaming = false;
    $("#send-btn").disabled = false;
    input.focus();
    scrollBottom();
  }
}

function appendMessage(msg) {
  const container = $("#chat-messages");
  // Remove welcome screen if present
  const welcome = $(".welcome", container);
  if (welcome) welcome.remove();
  container.appendChild(buildMessageEl(msg));
  scrollBottom();
}

function scrollBottom() {
  const c = $("#chat-messages");
  c.scrollTop = c.scrollHeight;
}

/* ── Init ────────────────────────────────────────────────────────────────────── */
async function initApp() {
  if (!state.token) {
    showAuthModal("login");
    return;
  }

  try {
    state.user = await apiRequest("GET", "/auth/me");
  } catch {
    logout();
    return;
  }

  await loadSessions();
  renderSidebar();

  // Load first session if exists
  if (state.sessions.length) {
    await selectSession(state.sessions[0].id);
  } else {
    renderMessages([]);
  }
}

/* ── Event Listeners ─────────────────────────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", () => {
  // New chat
  $("#btn-new-chat").addEventListener("click", createSession);

  // Logout
  $("#btn-logout")?.addEventListener("click", logout);

  // Send message
  $("#send-btn").addEventListener("click", sendMessage);

  // Auto-resize textarea
  const input = $("#message-input");
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 160) + "px";
  });

  // Enter to send (Shift+Enter for new line)
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  initApp();
});
