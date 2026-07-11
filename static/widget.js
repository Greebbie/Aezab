/*!
 * HlAB embeddable chat widget (static/widget.js)
 * Plain ES2017 IIFE — no build step, no external dependencies.
 *
 * Usage (customer embeds this on their page):
 *   <script src="https://their-hlab-host/widget.js"
 *           data-agent-id="agent_xxx" data-api-key="ak_invoke_scoped"
 *           data-title="在线客服" data-color="#1677ff" data-position="right"></script>
 *
 * Supported data-* attributes (all read from this script tag's dataset):
 *   data-agent-id   (required) target agent id
 *   data-api-key    (required) an API key scoped to "invoke" only — never
 *                    use a "manage"-scoped key here, it is visible to anyone
 *                    who views the page source (see docs/deployment.md).
 *   data-host       (optional) API origin, defaults to this script's own origin
 *   data-title      (optional) chat panel header title, default "AI 助手"
 *   data-color      (optional) primary color (bubble/header/buttons), default #1677ff
 *   data-position   (optional) "right" (default) or "left"
 *   data-user-id    (optional) end-user id forwarded as InvokeRequest.user_id
 *
 * Transport: fetch POST {host}/api/v1/invoke/stream, parses the SSE byte
 * stream (event/data pair accumulation — mirrors console/src/pages/
 * PlaygroundPage.tsx's sendStreaming()). Renders inside a Shadow DOM so the
 * widget's CSS never leaks into (or is leaked into by) the host page.
 *
 * Security: every piece of text that originates from the server (or from
 * the end user's own typed message) is inserted via `textContent`, never
 * `innerHTML` — this file must stay free of innerHTML assignments fed by
 * network data.
 */
(function () {
  'use strict';

  var CURRENT_SCRIPT = document.currentScript;
  if (!CURRENT_SCRIPT) {
    return; // Can't read config without the executing <script> element.
  }

  var ds = CURRENT_SCRIPT.dataset || {};
  var AGENT_ID = ds.agentId || '';
  var API_KEY = ds.apiKey || '';

  if (!AGENT_ID || !API_KEY) {
    if (window.console && console.error) {
      console.error('[hlab-widget] data-agent-id and data-api-key are required attributes on the widget <script> tag.');
    }
    return;
  }

  var TITLE = ds.title || 'AI 助手';
  var COLOR = ds.color || '#1677ff';
  var POSITION = ds.position === 'left' ? 'left' : 'right';
  var USER_ID = ds.userId || null;

  var HOST = ds.host;
  if (!HOST) {
    try {
      HOST = new URL(CURRENT_SCRIPT.src, window.location.href).origin;
    } catch (e) {
      HOST = window.location.origin;
    }
  }
  HOST = HOST.replace(/\/+$/, '');

  var SESSION_STORAGE_KEY = 'hlab_widget_session_' + AGENT_ID;

  /* ── State ─────────────────────────────────────────────────────── */

  var sessionId = null;
  try {
    sessionId = window.localStorage.getItem(SESSION_STORAGE_KEY) || null;
  } catch (e) {
    sessionId = null; // localStorage unavailable (privacy mode, sandboxed iframe, ...)
  }

  var isOpen = false;
  var isSending = false;
  var lastUserMessage = null;

  /* ── Build Shadow DOM host ────────────────────────────────────── */

  var hostEl = document.createElement('div');
  hostEl.id = 'hlab-widget-host';
  var shadow = hostEl.attachShadow({ mode: 'open' });

  var style = document.createElement('style');
  style.textContent = buildCSS(COLOR, POSITION);
  shadow.appendChild(style);

  var root = document.createElement('div');
  root.className = 'hlab-root';
  shadow.appendChild(root);

  /* Floating bubble button */
  var bubbleBtn = document.createElement('button');
  bubbleBtn.type = 'button';
  bubbleBtn.className = 'hlab-bubble-btn';
  bubbleBtn.setAttribute('aria-label', TITLE);
  bubbleBtn.appendChild(makeIcon('chat'));
  root.appendChild(bubbleBtn);

  /* Chat panel */
  var panel = document.createElement('div');
  panel.className = 'hlab-panel hlab-hidden';
  root.appendChild(panel);

  /* Header */
  var header = document.createElement('div');
  header.className = 'hlab-header';

  var headerTitle = document.createElement('div');
  headerTitle.className = 'hlab-header-title';
  headerTitle.textContent = TITLE;
  header.appendChild(headerTitle);

  var headerActions = document.createElement('div');
  headerActions.className = 'hlab-header-actions';

  var resetBtn = document.createElement('button');
  resetBtn.type = 'button';
  resetBtn.className = 'hlab-icon-btn';
  resetBtn.title = '新会话';
  resetBtn.setAttribute('aria-label', '新会话');
  resetBtn.appendChild(makeIcon('reset'));
  headerActions.appendChild(resetBtn);

  var closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'hlab-icon-btn';
  closeBtn.title = '关闭';
  closeBtn.setAttribute('aria-label', '关闭');
  closeBtn.appendChild(makeIcon('close'));
  headerActions.appendChild(closeBtn);

  header.appendChild(headerActions);
  panel.appendChild(header);

  /* Message list */
  var messagesEl = document.createElement('div');
  messagesEl.className = 'hlab-messages';
  panel.appendChild(messagesEl);

  /* Input row */
  var inputRow = document.createElement('div');
  inputRow.className = 'hlab-input-row';

  var textInput = document.createElement('textarea');
  textInput.className = 'hlab-input';
  textInput.rows = 1;
  textInput.placeholder = '输入消息...';
  inputRow.appendChild(textInput);

  var sendBtn = document.createElement('button');
  sendBtn.type = 'button';
  sendBtn.className = 'hlab-send-btn';
  sendBtn.setAttribute('aria-label', '发送');
  sendBtn.appendChild(makeIcon('send'));
  inputRow.appendChild(sendBtn);

  panel.appendChild(inputRow);

  document.body.appendChild(hostEl);

  /* ── Icons (static, trusted markup — not fed by network data) ───── */

  function makeIcon(name) {
    var svgNS = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', '20');
    svg.setAttribute('height', '20');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');

    var paths = {
      chat: ['M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z'],
      close: ['M18 6 6 18', 'M6 6l12 12'],
      reset: ['M3 12a9 9 0 1 0 3-6.7', 'M3 4v5h5'],
      send: ['M22 2 11 13', 'M22 2 15 22l-4-9-9-4 20-7z'],
    };
    (paths[name] || []).forEach(function (d) {
      var p = document.createElementNS(svgNS, 'path');
      p.setAttribute('d', d);
      svg.appendChild(p);
    });
    return svg;
  }

  /* ── Rendering helpers ────────────────────────────────────────── */

  function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function appendMessage(role, text, isError) {
    var msgEl = document.createElement('div');
    msgEl.className = 'hlab-msg hlab-msg-' + role + (isError ? ' hlab-msg-error' : '');
    var bubble = document.createElement('div');
    bubble.className = 'hlab-bubble';
    bubble.textContent = text; // server/user text — textContent only
    msgEl.appendChild(bubble);
    messagesEl.appendChild(msgEl);
    scrollToBottom();
    return { root: msgEl, bubble: bubble };
  }

  function appendTyping() {
    var msgEl = document.createElement('div');
    msgEl.className = 'hlab-msg hlab-msg-bot hlab-typing-msg';
    var dots = document.createElement('div');
    dots.className = 'hlab-typing';
    for (var i = 0; i < 3; i++) {
      var dot = document.createElement('span');
      dots.appendChild(dot);
    }
    msgEl.appendChild(dots);
    messagesEl.appendChild(msgEl);
    scrollToBottom();
    return msgEl;
  }

  function appendRetriableError(text, retryFn) {
    var wrap = appendMessage('bot', text, true);
    var retryBtn = document.createElement('button');
    retryBtn.type = 'button';
    retryBtn.className = 'hlab-retry-btn';
    retryBtn.textContent = '重试';
    retryBtn.addEventListener('click', function () {
      wrap.root.remove();
      retryFn();
    });
    wrap.root.appendChild(retryBtn);
  }

  function renderFollowups(list, container) {
    if (!list || !list.length) return;
    var wrap = document.createElement('div');
    wrap.className = 'hlab-followups';
    list.forEach(function (text) {
      if (!text) return;
      var chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'hlab-chip';
      chip.textContent = String(text); // server text — textContent only
      chip.addEventListener('click', function () {
        wrap.remove();
        sendMessage(String(text));
      });
      wrap.appendChild(chip);
    });
    container.appendChild(wrap);
    scrollToBottom();
  }

  /* ── SSE parsing + send ───────────────────────────────────────── */
  /* Mirrors PlaygroundPage.tsx's sendStreaming(): accumulate an
     "event: ...\ndata: ...\n\n" triple, decoding a blank line as the
     terminator of one complete SSE frame. */

  function sendMessage(text) {
    var trimmed = (text || '').trim();
    if (!trimmed || isSending) return;

    lastUserMessage = trimmed;
    appendMessage('user', trimmed);
    textInput.value = '';
    autoGrow();

    isSending = true;
    sendBtn.disabled = true;

    var typingEl = appendTyping();
    var botWrap = null;
    var streamedText = '';
    var finalContent = '';

    var body = { agent_id: AGENT_ID, message: trimmed };
    if (sessionId) body.session_id = sessionId;
    if (USER_ID) body.user_id = USER_ID;

    function ensureBotBubble() {
      if (!typingEl.isConnected && !botWrap) {
        // typing indicator already removed elsewhere; nothing to do
      }
      if (typingEl.isConnected) {
        typingEl.remove();
      }
      if (!botWrap) {
        botWrap = appendMessage('bot', '');
      }
      return botWrap;
    }

    fetch(HOST + '/api/v1/invoke/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': API_KEY,
      },
      body: JSON.stringify(body),
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error('HTTP ' + response.status);
        }
        var reader = response.body && response.body.getReader ? response.body.getReader() : null;
        if (!reader) {
          throw new Error('ReadableStream not supported in this browser');
        }

        var decoder = new TextDecoder();
        var buffer = '';
        var currentEvent = '';
        var currentData = '';

        function pump() {
          return reader.read().then(function (result) {
            if (result.done) return;

            buffer += decoder.decode(result.value, { stream: true });
            var lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (var i = 0; i < lines.length; i++) {
              var line = lines[i];
              if (line.indexOf('event: ') === 0) {
                currentEvent = line.slice(7).trim();
                currentData = '';
              } else if (line.indexOf('data: ') === 0) {
                currentData = line.slice(6);
              } else if (line === '' && currentEvent && currentData) {
                handleEvent(currentEvent, currentData);
                currentEvent = '';
                currentData = '';
              }
            }

            return pump();
          });
        }

        function handleEvent(eventName, rawData) {
          var parsed;
          try {
            parsed = JSON.parse(rawData);
          } catch (e) {
            return; // ignore malformed frames
          }

          if (eventName === 'status') {
            /* Typing indicator stays visible until real content arrives. */
            return;
          }
          if (eventName === 'answer_delta') {
            var wrap = ensureBotBubble();
            streamedText += parsed.text || '';
            wrap.bubble.textContent = streamedText;
            scrollToBottom();
          } else if (eventName === 'answer_reset') {
            streamedText = '';
            if (botWrap) botWrap.bubble.textContent = '';
          } else if (eventName === 'answer') {
            var wrap2 = ensureBotBubble();
            finalContent = parsed.content || '';
            streamedText = finalContent;
            wrap2.bubble.textContent = finalContent;
            scrollToBottom();
          } else if (eventName === 'done') {
            if (parsed.session_id) {
              sessionId = parsed.session_id;
              try {
                window.localStorage.setItem(SESSION_STORAGE_KEY, sessionId);
              } catch (e) {
                /* localStorage unavailable — session continuity best-effort only */
              }
            }
            if (botWrap) {
              renderFollowups(parsed.followups, botWrap.root);
            }
          } else if (eventName === 'error') {
            if (typingEl.isConnected) typingEl.remove();
            var errMsg = parsed.error_msg || parsed.detail || '发生未知错误';
            if (botWrap && !botWrap.bubble.textContent) {
              botWrap.root.remove();
            }
            appendRetriableError(errMsg, function () {
              sendMessage(lastUserMessage);
            });
          }
        }

        return pump();
      })
      .catch(function () {
        if (typingEl.isConnected) typingEl.remove();
        if (botWrap && !botWrap.bubble.textContent) {
          botWrap.root.remove();
        }
        appendRetriableError('网络请求失败，请检查网络连接后重试。', function () {
          sendMessage(lastUserMessage);
        });
      })
      .then(function () {
        if (!finalContent && streamedText && botWrap) {
          botWrap.bubble.textContent = streamedText;
        }
        if (typingEl.isConnected) typingEl.remove();
        isSending = false;
        sendBtn.disabled = false;
      });
  }

  /* ── UI wiring ────────────────────────────────────────────────── */

  function openPanel() {
    isOpen = true;
    panel.classList.remove('hlab-hidden');
    bubbleBtn.classList.add('hlab-hidden');
    textInput.focus();
  }

  function closePanel() {
    isOpen = false;
    panel.classList.add('hlab-hidden');
    bubbleBtn.classList.remove('hlab-hidden');
  }

  function resetSession() {
    sessionId = null;
    try {
      window.localStorage.removeItem(SESSION_STORAGE_KEY);
    } catch (e) {
      /* ignore */
    }
    while (messagesEl.firstChild) {
      messagesEl.removeChild(messagesEl.firstChild);
    }
  }

  function autoGrow() {
    textInput.style.height = 'auto';
    var next = Math.min(textInput.scrollHeight, 96);
    textInput.style.height = next + 'px';
  }

  bubbleBtn.addEventListener('click', openPanel);
  closeBtn.addEventListener('click', closePanel);
  resetBtn.addEventListener('click', resetSession);

  sendBtn.addEventListener('click', function () {
    sendMessage(textInput.value);
  });

  textInput.addEventListener('input', autoGrow);
  textInput.addEventListener('keydown', function (evt) {
    if (evt.key === 'Enter' && !evt.shiftKey) {
      evt.preventDefault();
      sendMessage(textInput.value);
    }
  });

  /* ── CSS ──────────────────────────────────────────────────────── */

  function buildCSS(color, position) {
    var side = position === 'left' ? 'left' : 'right';
    return '' +
      ':host{all:initial;position:fixed;z-index:2147483000;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;}' +
      '*,*::before,*::after{box-sizing:border-box;}' +
      '.hlab-root{position:fixed;bottom:20px;' + side + ':20px;display:flex;flex-direction:column;align-items:' + (side === 'left' ? 'flex-start' : 'flex-end') + ';gap:12px;}' +
      '.hlab-hidden{display:none !important;}' +
      '.hlab-bubble-btn{width:56px;height:56px;border-radius:50%;border:none;background:' + color + ';color:#fff;cursor:pointer;display:flex;align-items:center;justify-content:center;box-shadow:0 6px 18px rgba(0,0,0,.2);transition:transform .15s ease;}' +
      '.hlab-bubble-btn:hover{transform:scale(1.06);}' +
      '.hlab-panel{width:min(380px,calc(100vw - 32px));height:min(600px,calc(100vh - 120px));max-height:80vh;background:#fff;border-radius:14px;box-shadow:0 12px 32px rgba(0,0,0,.22);display:flex;flex-direction:column;overflow:hidden;}' +
      '.hlab-header{background:' + color + ';color:#fff;padding:14px 16px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0;}' +
      '.hlab-header-title{font-size:15px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}' +
      '.hlab-header-actions{display:flex;gap:4px;flex-shrink:0;}' +
      '.hlab-icon-btn{background:transparent;border:none;color:#fff;opacity:.85;cursor:pointer;width:28px;height:28px;display:flex;align-items:center;justify-content:center;border-radius:6px;}' +
      '.hlab-icon-btn:hover{opacity:1;background:rgba(255,255,255,.15);}' +
      '.hlab-messages{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px;background:#f5f6f8;}' +
      '.hlab-msg{display:flex;max-width:86%;}' +
      '.hlab-msg-user{align-self:flex-end;justify-content:flex-end;}' +
      '.hlab-msg-bot{align-self:flex-start;justify-content:flex-start;}' +
      '.hlab-bubble{padding:9px 12px;border-radius:12px;font-size:14px;line-height:1.5;white-space:pre-wrap;word-break:break-word;}' +
      '.hlab-msg-user .hlab-bubble{background:' + color + ';color:#fff;border-bottom-right-radius:2px;}' +
      '.hlab-msg-bot .hlab-bubble{background:#fff;color:#1f1f1f;border:1px solid #e8e8ec;border-bottom-left-radius:2px;}' +
      '.hlab-msg-error .hlab-bubble{background:#fff1f0;color:#cf1322;border:1px solid #ffa39e;}' +
      '.hlab-retry-btn{margin-top:6px;font-size:12px;background:transparent;border:1px solid #cf1322;color:#cf1322;border-radius:6px;padding:3px 10px;cursor:pointer;}' +
      '.hlab-typing{display:flex;gap:4px;padding:11px 14px;background:#fff;border:1px solid #e8e8ec;border-radius:12px;border-bottom-left-radius:2px;}' +
      '.hlab-typing span{width:6px;height:6px;border-radius:50%;background:#b5b8c0;animation:hlab-bounce 1.1s infinite ease-in-out;}' +
      '.hlab-typing span:nth-child(2){animation-delay:.15s;}' +
      '.hlab-typing span:nth-child(3){animation-delay:.3s;}' +
      '@keyframes hlab-bounce{0%,80%,100%{transform:translateY(0);opacity:.5;}40%{transform:translateY(-4px);opacity:1;}}' +
      '.hlab-followups{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;}' +
      '.hlab-chip{font-size:12px;background:#fff;border:1px solid ' + color + ';color:' + color + ';border-radius:14px;padding:4px 10px;cursor:pointer;}' +
      '.hlab-chip:hover{background:' + color + ';color:#fff;}' +
      '.hlab-input-row{display:flex;gap:8px;align-items:flex-end;padding:10px;border-top:1px solid #ececf0;background:#fff;flex-shrink:0;}' +
      '.hlab-input{flex:1;resize:none;border:1px solid #dcdfe6;border-radius:10px;padding:8px 10px;font-size:14px;font-family:inherit;max-height:96px;line-height:1.4;outline:none;}' +
      '.hlab-input:focus{border-color:' + color + ';}' +
      '.hlab-send-btn{width:36px;height:36px;border-radius:50%;border:none;background:' + color + ';color:#fff;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0;}' +
      '.hlab-send-btn:disabled{opacity:.5;cursor:not-allowed;}' +
      '@media (max-width: 480px){.hlab-panel{width:calc(100vw - 24px);height:calc(100vh - 100px);}}';
  }
})();
