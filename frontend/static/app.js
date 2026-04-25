/* ── Leo RAG Chat Application ─────────────────────────────────────────────── */
const API = "/api/v1";

const state = {
  history: [],   // [{role, content}, ...] — maintained client-side
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

/* ── Render ─────────────────────────────────────────────────────────────────── */
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

  const initial = isUser ? "U" : "L";
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
    const res = await fetch(`${API}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, history: state.history }),
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

    // Append to client-side history
    state.history.push({ role: "user", content });
    state.history.push({ role: "assistant", content: fullText });

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

function newChat() {
  state.history = [];
  renderMessages([]);
}

/* ── Init ────────────────────────────────────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", () => {
  // New chat
  $("#btn-new-chat").addEventListener("click", newChat);

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

  renderMessages([]);
});
