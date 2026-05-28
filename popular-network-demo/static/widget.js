/*
 * Popular Network — Chat widget (Phase H.2.1)
 *
 * Embed on a publisher's page:
 *   <script src="https://app.popularnetwork.example/static/widget.js"></script>
 *   <script>
 *     PopularNetworkWidget.init({
 *       businessId: 1,
 *       apiBaseUrl: 'https://app.popularnetwork.example',   // omit when same-origin
 *       title: 'Ask Quadd',
 *       greeting: 'Hi! What can I help you with?',
 *       accent: '#3b82f6',
 *     });
 *   </script>
 *
 * No frameworks. ~8KB minified. Persists sessionId to sessionStorage so a
 * page refresh continues the same conversation.
 *
 * Disabled-visible: bubble and panel render immediately; input is enabled
 * even before the first network call. A loading state shows while waiting
 * for a reply. Errors paint as a system message in the panel.
 */
(function () {
  'use strict';

  if (window.PopularNetworkWidget) return;  // idempotent — guard double-load

  const STORAGE_KEY = 'pnw_session_v1';

  const DEFAULT_OPTIONS = {
    businessId: null,
    apiBaseUrl: '',                 // '' = same-origin
    title: 'Ask us anything',
    greeting: 'Hi! How can I help today?',
    accent: '#3b82f6',              // Tailwind blue-500
    bubbleIcon: '💬',
  };

  const CSS = `
    .pnw-root, .pnw-root * { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }
    .pnw-bubble {
      position: fixed; bottom: 24px; right: 24px; z-index: 2147483646;
      width: 56px; height: 56px; border-radius: 28px;
      background: var(--pnw-accent); color: #fff;
      display: flex; align-items: center; justify-content: center;
      font-size: 26px; line-height: 1;
      box-shadow: 0 6px 24px rgba(0, 0, 0, 0.18);
      border: none; cursor: pointer; transition: transform 0.15s ease;
    }
    .pnw-bubble:hover { transform: scale(1.06); }
    .pnw-bubble:focus-visible { outline: 3px solid rgba(59, 130, 246, 0.5); outline-offset: 2px; }
    .pnw-panel {
      position: fixed; bottom: 96px; right: 24px; z-index: 2147483647;
      width: 380px; max-width: calc(100vw - 32px);
      height: 540px; max-height: calc(100vh - 120px);
      background: #ffffff;
      border-radius: 16px;
      box-shadow: 0 12px 40px rgba(0, 0, 0, 0.18);
      display: none; flex-direction: column;
      overflow: hidden;
      animation: pnw-slide-in 0.18s ease;
    }
    .pnw-panel.open { display: flex; }
    @keyframes pnw-slide-in { from { transform: translateY(12px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
    .pnw-header {
      background: var(--pnw-accent); color: #fff;
      padding: 14px 16px; font-weight: 600; font-size: 15px;
      display: flex; align-items: center; justify-content: space-between;
    }
    .pnw-close {
      background: transparent; border: none; color: #fff; cursor: pointer;
      font-size: 22px; line-height: 1; padding: 0 4px;
      opacity: 0.85;
    }
    .pnw-close:hover { opacity: 1; }
    .pnw-messages {
      flex: 1; overflow-y: auto; padding: 16px;
      background: #f7f8fa; display: flex; flex-direction: column; gap: 8px;
    }
    .pnw-msg { max-width: 85%; padding: 10px 12px; border-radius: 14px; font-size: 14px; line-height: 1.4; white-space: pre-wrap; word-wrap: break-word; }
    .pnw-msg-bot       { align-self: flex-start; background: #ffffff; color: #1f2937; border: 1px solid #e5e7eb; border-bottom-left-radius: 4px; }
    .pnw-msg-consumer  { align-self: flex-end;   background: var(--pnw-accent); color: #fff;                                  border-bottom-right-radius: 4px; }
    .pnw-msg-system    { align-self: center;     background: #fef3c7; color: #92400e; border: 1px solid #fde68a; font-size: 12px; }
    .pnw-typing { align-self: flex-start; padding: 10px 14px; background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; }
    .pnw-typing span { display: inline-block; width: 6px; height: 6px; background: #94a3b8; border-radius: 50%; margin: 0 1px; animation: pnw-typing-bounce 1.2s infinite; }
    .pnw-typing span:nth-child(2) { animation-delay: 0.15s; }
    .pnw-typing span:nth-child(3) { animation-delay: 0.3s; }
    @keyframes pnw-typing-bounce { 0%, 60%, 100% { transform: translateY(0); } 30% { transform: translateY(-5px); } }
    .pnw-input-row {
      border-top: 1px solid #e5e7eb; background: #ffffff;
      padding: 10px; display: flex; gap: 8px; align-items: flex-end;
    }
    .pnw-input {
      flex: 1; border: 1px solid #d1d5db; border-radius: 10px;
      padding: 10px 12px; font-size: 14px; resize: none; min-height: 40px; max-height: 100px;
      font-family: inherit; outline: none;
    }
    .pnw-input:focus { border-color: var(--pnw-accent); box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2); }
    .pnw-send {
      background: var(--pnw-accent); color: #fff; border: none; padding: 10px 16px;
      border-radius: 10px; font-size: 14px; font-weight: 500; cursor: pointer;
      transition: opacity 0.15s ease;
    }
    .pnw-send:disabled { opacity: 0.4; cursor: not-allowed; }
    .pnw-footer { text-align: center; padding: 6px; background: #ffffff; border-top: 1px solid #f1f5f9; font-size: 10px; color: #94a3b8; }
    .pnw-footer a { color: inherit; text-decoration: underline; }
    @media (max-width: 480px) {
      .pnw-panel { right: 8px; left: 8px; width: auto; bottom: 88px; }
      .pnw-bubble { right: 16px; bottom: 16px; }
    }
  `;

  function injectStyles() {
    if (document.getElementById('pnw-styles')) return;
    const style = document.createElement('style');
    style.id = 'pnw-styles';
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  function loadSession() {
    try { return JSON.parse(sessionStorage.getItem(STORAGE_KEY)) || null; } catch { return null; }
  }
  function saveSession(state) {
    try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch { /* private mode etc. */ }
  }

  let state = {
    sessionId: null,
    transcript: [],
    sending: false,
  };

  let opts = { ...DEFAULT_OPTIONS };
  let root, bubble, panel, messagesEl, inputEl, sendBtn;

  function render() {
    injectStyles();
    if (root) return;

    root = document.createElement('div');
    root.className = 'pnw-root';
    root.style.setProperty('--pnw-accent', opts.accent);

    bubble = document.createElement('button');
    bubble.className = 'pnw-bubble';
    bubble.setAttribute('aria-label', 'Open chat');
    bubble.textContent = opts.bubbleIcon;
    bubble.addEventListener('click', togglePanel);

    panel = document.createElement('div');
    panel.className = 'pnw-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', opts.title);
    panel.innerHTML = `
      <div class="pnw-header">
        <span>${escapeHtml(opts.title)}</span>
        <button class="pnw-close" aria-label="Close">&times;</button>
      </div>
      <div class="pnw-messages" aria-live="polite"></div>
      <div class="pnw-input-row">
        <textarea class="pnw-input" rows="1" placeholder="Type a message…"></textarea>
        <button class="pnw-send" aria-label="Send">Send</button>
      </div>
      <div class="pnw-footer">Powered by Popular Network</div>
    `;

    panel.querySelector('.pnw-close').addEventListener('click', closePanel);
    messagesEl = panel.querySelector('.pnw-messages');
    inputEl = panel.querySelector('.pnw-input');
    sendBtn = panel.querySelector('.pnw-send');

    inputEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    });
    sendBtn.addEventListener('click', send);

    root.appendChild(bubble);
    root.appendChild(panel);
    document.body.appendChild(root);
  }

  function togglePanel() {
    if (panel.classList.contains('open')) { closePanel(); } else { openPanel(); }
  }

  function openPanel() {
    panel.classList.add('open');
    if (state.transcript.length === 0 && opts.greeting) {
      appendBubble('bot', opts.greeting);
    }
    setTimeout(() => inputEl.focus(), 50);
  }
  function closePanel() {
    panel.classList.remove('open');
  }

  function appendBubble(who, text) {
    const div = document.createElement('div');
    div.className = 'pnw-msg pnw-msg-' + who;
    div.textContent = text;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    if (who !== 'system') {
      state.transcript.push({ who, text });
      saveSession(state);
    }
  }

  function setTyping(on) {
    const existing = messagesEl.querySelector('.pnw-typing');
    if (on && !existing) {
      const t = document.createElement('div');
      t.className = 'pnw-typing';
      t.innerHTML = '<span></span><span></span><span></span>';
      messagesEl.appendChild(t);
      messagesEl.scrollTop = messagesEl.scrollHeight;
    } else if (!on && existing) {
      existing.remove();
    }
  }

  async function send() {
    if (state.sending) return;
    const text = inputEl.value.trim();
    if (!text) return;
    inputEl.value = '';
    appendBubble('consumer', text);
    state.sending = true;
    sendBtn.disabled = true;
    setTyping(true);

    try {
      const url = (opts.apiBaseUrl || '').replace(/\/$/, '') + '/api/widget/chat';
      const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Business-Id': String(opts.businessId) },
        body: JSON.stringify({
          business_id: opts.businessId,
          session_id: state.sessionId,
          message: text,
          referrer: location.host,
        }),
      });
      setTyping(false);
      if (!resp.ok) {
        let detail = 'Sorry — something went wrong (' + resp.status + ').';
        if (resp.status === 429) detail = 'Too many messages right now. Please try again in a moment.';
        if (resp.status === 404) detail = 'This chat widget is misconfigured (business not found).';
        if (resp.status === 403) detail = 'This widget isn’t enabled for this site.';
        if (resp.status === 503) detail = 'Chat is temporarily unavailable.';
        appendBubble('system', detail);
        return;
      }
      const data = await resp.json();
      if (data.sessionId) {
        state.sessionId = data.sessionId;
        saveSession(state);
      }
      appendBubble('bot', data.reply || '…');
    } catch (err) {
      setTyping(false);
      appendBubble('system', 'Network error. Check your connection and try again.');
    } finally {
      state.sending = false;
      sendBtn.disabled = false;
      inputEl.focus();
    }
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
  }

  function init(userOpts) {
    opts = { ...DEFAULT_OPTIONS, ...(userOpts || {}) };
    if (!opts.businessId) {
      console.error('[PopularNetworkWidget] init() requires `businessId`');
      return;
    }
    const persisted = loadSession();
    if (persisted && persisted.sessionId) {
      state.sessionId = persisted.sessionId;
      state.transcript = persisted.transcript || [];
    }
    if (document.body) {
      render();
    } else {
      document.addEventListener('DOMContentLoaded', render, { once: true });
    }
    // Re-render the persisted transcript on panel-open (only the first time).
    // We don't auto-paint on init so the bubble is the only chrome until clicked.
  }

  // Public API
  window.PopularNetworkWidget = {
    init,
    open: () => { if (panel) openPanel(); },
    close: () => { if (panel) closePanel(); },
    reset: () => {
      state = { sessionId: null, transcript: [], sending: false };
      try { sessionStorage.removeItem(STORAGE_KEY); } catch {}
      if (messagesEl) messagesEl.innerHTML = '';
    },
  };
})();
