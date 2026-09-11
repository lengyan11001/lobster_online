(function () {
  'use strict';

  var state = {
    initialized: false,
    sessions: [],
    activeSessionId: '',
    historyItems: [],
    live: {},
    streams: {},
    polls: {},
    lastEventIds: {},
    uploads: [],
    loading: false,
    sending: false,
    requestSeq: 0,
    viewObserver: null,
    composing: false,
    running: { messageId: '', sessionId: '' }
  };

  function el(id) {
    return document.getElementById(id);
  }

  function text(value) {
    return String(value == null ? '' : value);
  }

  function escapeHtml(value) {
    return text(value).replace(/[&<>"']/g, function (ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
  }

  function brandMark() {
    return typeof getLobsterBrandMark === 'function' ? getLobsterBrandMark() : 'bihuo';
  }

  function apiBase() {
    return text(typeof API_BASE !== 'undefined' ? API_BASE : window.location.origin).replace(/\/$/, '');
  }

  function apiUrl(path) {
    var raw = text(path);
    var url = apiBase() + (raw.charAt(0) === '/' ? raw : '/' + raw);
    var separator = url.indexOf('?') >= 0 ? '&' : '?';
    return url + separator + 'brand=' + encodeURIComponent(brandMark());
  }

  function request(path, options) {
    options = options || {};
    var headers = typeof authHeaders === 'function'
      ? authHeaders()
      : { 'Authorization': 'Bearer ' + text(typeof token !== 'undefined' ? token : '') };
    Object.keys(options.headers || {}).forEach(function (key) { headers[key] = options.headers[key]; });
    if (options.json !== undefined) {
      headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(options.json);
    }
    return fetch(apiUrl(path), Object.assign({}, options, { headers: headers })).then(function (response) {
      return response.text().then(function (body) {
        var data = {};
        try { data = body ? JSON.parse(body) : {}; } catch (e) { data = { detail: body }; }
        if (!response.ok) {
          var error = new Error(text(data.detail || data.message || ('HTTP ' + response.status)));
          error.status = response.status;
          throw error;
        }
        return data;
      });
    });
  }

  function installationId() {
    if (typeof getOrCreateInstallationId === 'function') return getOrCreateInstallationId();
    var key = 'lobster_installation_id';
    var deprecatedInstallationIds = {
      '2fc3f43f7a684411a442cb661898aa74': true,
      'fa2d09cfbd9c4b2380352906225f2817': true
    };
    function isDeprecatedInstallationId(value) {
      var text = String(value || '').trim();
      var raw = text.indexOf('--') >= 0 ? text.split('--').slice(1).join('--') : text;
      return !!deprecatedInstallationIds[raw];
    }
    var current = '';
    try { current = localStorage.getItem(key) || ''; } catch (e) {}
    if (isDeprecatedInstallationId(current)) {
      try { localStorage.removeItem(key); } catch (e0) {}
      current = '';
    }
    if (current) return current;
    current = 'online-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 12);
    try { localStorage.setItem(key, current); } catch (e2) {}
    return current;
  }

  function sessionStorageKey() {
    return 'lobster_h5_chat_session_id:' + brandMark();
  }

  function getStoredSessionId() {
    try { return localStorage.getItem(sessionStorageKey()) || ''; } catch (e) { return ''; }
  }

  function storeSessionId(id) {
    try { localStorage.setItem(sessionStorageKey(), text(id)); } catch (e) {}
  }

  function activeSession() {
    return state.sessions.find(function (row) { return text(row.id) === text(state.activeSessionId); }) || null;
  }

  function permissionLabel(mode) {
    return text(mode).toLowerCase() === 'full' ? '完全访问' : '需要确认';
  }

  function formatTime(value) {
    var date = value ? new Date(value) : null;
    if (!date || isNaN(date.getTime())) return '';
    var now = new Date();
    if (date.toDateString() === now.toDateString()) {
      return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    }
    return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
  }

  function sessionPreview(row) {
    var count = Number(row && row.message_count || 0);
    return count ? count + ' 条消息' : '暂无消息';
  }

  function renderSessionList() {
    var host = el('mastraSessionList');
    if (!host) return;
    var query = text(el('mastraSessionSearch') && el('mastraSessionSearch').value).trim().toLowerCase();
    var rows = state.sessions.filter(function (row) {
      return !query || text(row.title || '新会话').toLowerCase().indexOf(query) >= 0;
    });
    if (!rows.length) {
      host.innerHTML = '<div class="chat-session-empty">还没有历史会话</div>';
      return;
    }
    host.innerHTML = rows.map(function (row) {
      var id = escapeHtml(row.id);
      var active = text(row.id) === text(state.activeSessionId) ? ' active' : '';
      return '<div class="chat-session-item' + active + '" data-mastra-session-id="' + id + '">' +
        '<div class="session-row"><div class="session-leading"><span class="session-bubble-icon">◌</span></div>' +
        '<div class="session-copy"><div class="session-title"><div class="session-title-row"><span>' +
        escapeHtml(row.title || '新会话') + '</span><span class="session-mode-badge">' +
        escapeHtml(permissionLabel(row.permission_mode)) + '</span></div></div>' +
        '<div class="session-preview">' + escapeHtml(sessionPreview(row)) + '</div></div>' +
        '<button type="button" class="session-delete-btn" data-mastra-delete-session="' + id + '" title="删除会话" aria-label="删除会话">×</button></div>' +
        '<div class="session-time">' + escapeHtml(formatTime(row.updated_at || row.created_at)) + '</div></div>';
    }).join('');
    host.querySelectorAll('[data-mastra-session-id]').forEach(function (item) {
      item.addEventListener('click', function () { switchSession(item.getAttribute('data-mastra-session-id')); });
    });
    host.querySelectorAll('[data-mastra-delete-session]').forEach(function (button) {
      button.addEventListener('click', function (event) {
        event.preventDefault();
        event.stopPropagation();
        deleteSession(button.getAttribute('data-mastra-delete-session'));
      });
    });
  }

  function renderSessionHeader() {
    var session = activeSession();
    var title = el('onlineMastraSessionTitle');
    var current = el('onlineMastraSessionCurrent');
    var status = el('onlineMastraChatStatus');
    if (title) title.textContent = session ? (session.title || '新会话') : '新会话';
    if (current) current.setAttribute('aria-label', session ? '当前会话：' + (session.title || '新会话') : '当前会话');
    if (status) status.textContent = session ? '与 H5 共享会话 · ' + permissionLabel(session.permission_mode) : '与 H5 共享会话';
    renderPermissionControl();
    renderSessionList();
  }

  function renderPermissionControl() {
    var session = activeSession();
    var label = el('onlineMastraPermissionLabel');
    var button = el('onlineMastraPermissionCurrent');
    var value = session ? permissionLabel(session.permission_mode) : '需要确认';
    if (label) label.textContent = value;
    if (button) {
      button.title = value === '完全访问' ? '当前会话已完全授权，任务会直接执行' : '当前会话需要在执行任务前确认';
      button.setAttribute('aria-label', '执行权限：' + value);
    }
  }

  function closeAllStreams() {
    Object.keys(state.streams).concat(Object.keys(state.polls)).forEach(closeStream);
  }

  function scrollToBottom() {
    var box = el('onlineMastraMessages');
    if (!box) return;
    box.scrollTop = box.scrollHeight;
    requestAnimationFrame(function () { box.scrollTop = box.scrollHeight; });
  }

  function clearMessages() {
    var box = el('onlineMastraMessages');
    if (!box) return;
    box.innerHTML = '<div class="online-mastra-empty" id="onlineMastraEmpty">开始一轮新的对话</div>';
  }

  function enterCompose(initialValue, focusInput) {
    state.composing = true;
    syncActiveViewClass();
    var input = el('onlineMastraInput');
    if (input && initialValue !== undefined) input.value = text(initialValue);
    resizeInput();
    if (focusInput && input) setTimeout(function () { input.focus(); }, 0);
  }

  function resetToHome() {
    state.composing = false;
    closePermissionMenu();
    var input = el('onlineMastraInput');
    if (input) input.value = '';
    var homeInput = el('chatInput');
    if (homeInput) homeInput.value = '';
    syncActiveViewClass();
  }

  var RICH_URL_RE = /https?:\/\/[^\s<>"']+/gi;

  // 富内容样式随模块自带，避免依赖宿主页面的 <link> 顺序。
  // 但它必须跟页面同源加载：以前用 apiUrl() 拼到线上服务端，线上并没有
  // /static/css/rich-content.css（404），于是图片限高、缩略图、灯箱这些样式
  // 整体失效，表现为"本地改完、客户端重启也不生效"。
  var RICH_STYLE_PATH = '/static/css/rich-content.css?v=20260910-rich-content-v2';
  var RICH_CRITICAL_CSS = [
    '.rich-paragraph{white-space:pre-wrap;word-break:break-word;line-height:1.7;}',
    '.rich-media-grid{display:grid;gap:6px;margin-top:.5rem;grid-template-columns:1fr;}',
    '.rich-media-grid.is-multi{grid-template-columns:repeat(2,minmax(0,1fr));}',
    '.rich-media-item{padding:0;border:0;border-radius:12px;overflow:hidden;background:rgba(15,23,42,.05);cursor:zoom-in;line-height:0;}',
    '.rich-media-item img{display:block;width:100%;max-height:420px;object-fit:cover;}',
    '.online-mastra-message-media img,.online-mastra-message-attachments img{max-width:100%;max-height:240px;width:auto;object-fit:cover;border-radius:10px;cursor:zoom-in;}',
    '.online-mastra-message-media video,.online-mastra-message-attachments video{max-width:100%;max-height:260px;border-radius:10px;}',
    '.online-mastra-approval.is-decided{opacity:.62;}',
    '.online-mastra-approval-note{font-size:12px;color:rgba(15,23,42,.62);align-self:center;}',
    '.rich-pending{background:linear-gradient(90deg,rgba(15,23,42,.06) 25%,rgba(15,23,42,.12) 37%,rgba(15,23,42,.06) 63%);background-size:400% 100%;min-height:120px;}',
    '.rich-media-failed{display:flex;align-items:center;justify-content:center;min-height:120px;background:rgba(225,29,72,.08);border:1px dashed rgba(225,29,72,.35);color:#be123c;font-size:13px;cursor:pointer;}',
    '.rich-lightbox{position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.82);cursor:zoom-out;}',
    '.rich-lightbox.hidden{display:none;}',
    '.rich-lightbox img{max-width:94vw;max-height:92vh;border-radius:8px;}',
    '.rich-lightbox button{position:absolute;border:0;background:rgba(255,255,255,.2);color:#fff;cursor:pointer;}',
    '.rich-lightbox-back{top:16px;left:18px;height:34px;padding:0 .9rem;border-radius:999px;font-size:14px;}',
    '.rich-lightbox-prev,.rich-lightbox-next{top:50%;transform:translateY(-50%);width:40px;height:40px;border-radius:50%;font-size:22px;line-height:1;}',
    '.rich-lightbox-prev{left:12px;}',
    '.rich-lightbox-next{right:12px;}',
    '.rich-lightbox-counter{position:absolute;bottom:18px;left:50%;transform:translateX(-50%);color:#fff;font-size:13px;}'
  ].join('');

  function richStyleCandidates() {
    var out = [];
    var origin = '';
    try {
      if (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) origin = text(LOCAL_API_BASE);
    } catch (error) { origin = ''; }
    if (!origin && window.location && window.location.origin) origin = window.location.origin;
    origin = text(origin).replace(/\/$/, '');
    if (origin) out.push(origin + RICH_STYLE_PATH);
    if (out.indexOf(RICH_STYLE_PATH) < 0) out.push(RICH_STYLE_PATH);
    try {
      var remote = apiUrl(RICH_STYLE_PATH);
      if (remote && out.indexOf(remote) < 0) out.push(remote);
    } catch (error) { /* ignore */ }
    return out;
  }

  function injectRichCriticalStyles() {
    if (document.getElementById('onlineRichContentCritical')) return;
    var style = document.createElement('style');
    style.id = 'onlineRichContentCritical';
    style.textContent = RICH_CRITICAL_CSS;
    document.head.appendChild(style);
  }

  function ensureRichStyles() {
    if (document.getElementById('onlineRichContentStyle')) return;
    var link = document.createElement('link');
    link.id = 'onlineRichContentStyle';
    link.rel = 'stylesheet';
    var candidates = richStyleCandidates();
    var index = 0;
    link.onerror = function () {
      index += 1;
      if (index < candidates.length) {
        link.href = candidates[index];
        return;
      }
      injectRichCriticalStyles();
    };
    link.href = candidates[0];
    document.head.appendChild(link);
  }

  var RICH_KINDS = [
    [/\.(png|jpe?g|gif|webp|bmp|avif|svg)(?:[?#].*)?$/i, 'image'],
    [/\.(mp4|webm|mov|m4v|avi|mkv)(?:[?#].*)?$/i, 'video'],
    [/\.(mp3|wav|m4a|aac|ogg|flac)(?:[?#].*)?$/i, 'audio'],
    [/\.(pdf|docx?|xlsx?|pptx?|zip|rar|7z|csv|txt|md|json)(?:[?#].*)?$/i, 'file']
  ];

  function richUrlKind(url) {
    var clean = text(url);
    if (!/^https?:\/\//i.test(clean)) return '';
    for (var i = 0; i < RICH_KINDS.length; i += 1) {
      if (RICH_KINDS[i][0].test(clean)) return RICH_KINDS[i][1];
    }
    return 'link';
  }

  function richHost(url) {
    try { return new URL(url).host.replace(/^www\./i, ''); } catch (error) { return '\u94fe\u63a5'; }
  }

  function richLinkify(value) {
    return escapeHtml(value).replace(RICH_URL_RE, function (raw) {
      return '<a href="' + raw + '" target="_blank" rel="noopener noreferrer">' + raw + '</a>';
    });
  }

  var lightboxItems = [];
  var lightboxIndex = 0;

  function lightboxCollect(url) {
    var found = [];
    document.querySelectorAll('.rich-media-item img, .online-mastra-zoomable, .online-mastra-message-attachments img').forEach(function (node) {
      var src = text(node.getAttribute('src'));
      if (src && found.indexOf(src) < 0) found.push(src);
    });
    if (url && found.indexOf(url) < 0) found.push(url);
    return found;
  }

  function richLightboxShow(index) {
    var box = document.getElementById('onlineRichLightbox');
    if (!box || !lightboxItems.length) return;
    lightboxIndex = (index + lightboxItems.length) % lightboxItems.length;
    var img = box.querySelector('img');
    if (img) img.src = lightboxItems[lightboxIndex];
    var counter = box.querySelector('.rich-lightbox-counter');
    if (counter) counter.textContent = (lightboxIndex + 1) + ' / ' + lightboxItems.length;
    var multi = lightboxItems.length > 1;
    var prev = box.querySelector('.rich-lightbox-prev');
    var next = box.querySelector('.rich-lightbox-next');
    if (prev) prev.style.display = multi ? '' : 'none';
    if (next) next.style.display = multi ? '' : 'none';
    if (counter) counter.style.display = multi ? '' : 'none';
  }

  function richLightbox(url) {
    var box = document.getElementById('onlineRichLightbox');
    if (!box) {
      box = document.createElement('div');
      box.id = 'onlineRichLightbox';
      box.className = 'rich-lightbox hidden';
      box.innerHTML = [
        '<button type="button" class="rich-lightbox-back">\u8fd4\u56de</button>',
        '<button type="button" class="rich-lightbox-prev" aria-label="\u4e0a\u4e00\u5f20">\u2039</button>',
        '<img alt="" />',
        '<button type="button" class="rich-lightbox-next" aria-label="\u4e0b\u4e00\u5f20">\u203a</button>',
        '<span class="rich-lightbox-counter"></span>',
      ].join('');
      var close = function () { box.classList.add('hidden'); };
      box.addEventListener('click', function (event) {
        if (event.target === box) close();
      });
      box.querySelector('.rich-lightbox-back').addEventListener('click', function (event) {
        event.stopPropagation();
        close();
      });
      box.querySelector('.rich-lightbox-prev').addEventListener('click', function (event) {
        event.stopPropagation();
        richLightboxShow(lightboxIndex - 1);
      });
      box.querySelector('.rich-lightbox-next').addEventListener('click', function (event) {
        event.stopPropagation();
        richLightboxShow(lightboxIndex + 1);
      });
      document.addEventListener('keydown', function (event) {
        if (box.classList.contains('hidden')) return;
        if (event.key === 'Escape') close();
        else if (event.key === 'ArrowLeft') richLightboxShow(lightboxIndex - 1);
        else if (event.key === 'ArrowRight') richLightboxShow(lightboxIndex + 1);
      });
      document.body.appendChild(box);
    }
    lightboxItems = lightboxCollect(url);
    richLightboxShow(Math.max(0, lightboxItems.indexOf(url)));
    box.classList.remove('hidden');
  }

  function richImageGrid(urls) {
    var host = document.createElement('div');
    host.className = 'rich-media-grid' + (urls.length > 1 ? ' is-multi' : '');
    urls.forEach(function (url) {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'rich-media-item';
      var img = document.createElement('img');
      img.src = url;
      img.alt = '\u56fe\u7247';
      img.loading = 'lazy';
      button.appendChild(img);
      button.addEventListener('click', function () { richLightbox(url); });
      host.appendChild(button);
    });
    return host;
  }

  function richMediaGroup(urls, kind) {
    if (kind === 'image') return richImageGrid(urls);
    var host = document.createElement('div');
    host.className = 'rich-media-block';
    urls.forEach(function (url) {
      var node;
      if (kind === 'video') {
        node = document.createElement('video');
        node.controls = true;
        node.preload = 'metadata';
      } else if (kind === 'audio') {
        node = document.createElement('audio');
        node.controls = true;
        node.preload = 'metadata';
      } else {
        node = document.createElement('a');
        node.className = 'rich-file-row';
        node.href = url;
        node.target = '_blank';
        node.rel = 'noopener noreferrer';
        node.textContent = '\u6587\u4ef6\uff1a' + (String(url).split(/[?#]/)[0].split('/').pop() || '\u4e0b\u8f7d');
      }
      if (node.tagName !== 'A') node.src = url;
      host.appendChild(node);
    });
    return host;
  }

  function richLinkCard(url) {
    var card = document.createElement('a');
    card.className = 'rich-link-card';
    card.href = url;
    card.target = '_blank';
    card.rel = 'noopener noreferrer';
    var host = document.createElement('span');
    host.className = 'rich-link-host';
    host.textContent = richHost(url);
    var path = document.createElement('span');
    path.className = 'rich-link-path';
    path.textContent = (String(url).replace(/^https?:\/\/[^/]+/i, '') || '/').slice(0, 80);
    card.appendChild(host);
    card.appendChild(path);
    return card;
  }

  function renderRichBody(host, raw) {
    if (!host) return;
    host.textContent = '';
    var lines = text(raw).split(/\r?\n/);
    var buffer = [];
    function flush() {
      if (!buffer.length) return;
      var block = document.createElement('div');
      block.className = 'rich-paragraph';
      block.innerHTML = richLinkify(buffer.join('\n'));
      host.appendChild(block);
      buffer = [];
    }
    for (var index = 0; index < lines.length; index += 1) {
      var line = lines[index];
      var trimmed = line.trim();
      if (/^```/.test(trimmed)) {
        var code = [];
        index += 1;
        while (index < lines.length && !/^```/.test(lines[index].trim())) {
          code.push(lines[index]);
          index += 1;
        }
        flush();
        var pre = document.createElement('pre');
        pre.className = 'rich-code';
        var codeEl = document.createElement('code');
        codeEl.textContent = code.join('\n');
        pre.appendChild(codeEl);
        host.appendChild(pre);
        continue;
      }
      var alone = /^https?:\/\/\S+$/i.test(trimmed) ? trimmed : '';
      if (!alone) {
        buffer.push(line);
        continue;
      }
      var kind = richUrlKind(alone);
      flush();
      if (kind === 'link') {
        host.appendChild(richLinkCard(alone));
        continue;
      }
      var group = [alone];
      while (index + 1 < lines.length) {
        var nextLine = lines[index + 1].trim();
        if (!/^https?:\/\/\S+$/i.test(nextLine) || richUrlKind(nextLine) !== kind) break;
        group.push(nextLine);
        index += 1;
      }
      host.appendChild(richMediaGroup(group, kind));
    }
    flush();
    if (!host.childNodes.length && text(raw)) {
      var fallback = document.createElement('div');
      fallback.className = 'rich-paragraph';
      fallback.innerHTML = richLinkify(raw);
      host.appendChild(fallback);
    }
  }

  function createBubble(role, message) {
    var box = el('onlineMastraMessages');
    if (!box) return null;
    var empty = el('onlineMastraEmpty');
    if (empty) empty.remove();
    var wrapper = document.createElement('article');
    wrapper.className = 'online-mastra-message ' + (role === 'user' ? 'is-user' : 'is-assistant');
    wrapper.dataset.role = role;
    var avatar = document.createElement('div');
    avatar.className = 'online-mastra-avatar';
    avatar.textContent = role === 'user' ? '我' : '调';
    var body = document.createElement('div');
    body.className = 'online-mastra-bubble';
    body.textContent = text(message);
    wrapper.appendChild(avatar);
    wrapper.appendChild(body);
    box.appendChild(wrapper);
    return { wrapper: wrapper, body: body, text: text(message), media: {} };
  }

  function setBubbleText(bubble, value) {
    if (!bubble || !bubble.body) return;
    bubble.text = text(value);
    renderRichBody(bubble.body, bubble.text);
  }

  function appendBubbleText(bubble, value) {
    if (!bubble || !value) return;
    bubble.text += text(value);
    if (bubble.body) renderRichBody(bubble.body, bubble.text);
  }

  function addAttachmentView(bubble, attachments) {
    if (!bubble || !bubble.wrapper || !Array.isArray(attachments) || !attachments.length) return;
    var wrap = document.createElement('div');
    wrap.className = 'online-mastra-message-attachments';
    attachments.forEach(function (item) {
      var url = text(item && (item.url || item.source_url));
      var name = text(item && item.name || '素材');
      var mediaType = text(item && item.media_type).toLowerCase();
      if (url && mediaType === 'image') {
        var image = document.createElement('img');
        image.src = url;
        image.alt = name;
        image.loading = 'lazy';
        image.className = 'online-mastra-zoomable rich-pending';
        image.addEventListener('load', function () { image.classList.remove('rich-pending'); });
        image.addEventListener('error', function () {
          // 生成中先显示骨架；加载失败给一个可点重试的占位，避免整块空白
          image.classList.remove('rich-pending');
          image.classList.add('rich-media-failed');
          image.title = '\u52a0\u8f7d\u5931\u8d25\uff0c\u70b9\u51fb\u91cd\u8bd5';
        });
        image.addEventListener('click', function () {
          if (image.classList.contains('rich-media-failed')) {
            image.classList.remove('rich-media-failed');
            image.classList.add('rich-pending');
            image.src = url + (url.indexOf('?') < 0 ? '?' : '&') + '_retry=' + Date.now();
            return;
          }
          richLightbox(url);
        });
        wrap.appendChild(image);
      } else {
        var link = document.createElement('a');
        link.href = url || '#';
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = name;
        if (!url) link.removeAttribute('href');
        wrap.appendChild(link);
      }
    });
    bubble.wrapper.appendChild(wrap);
  }

  function mediaUrls(payload) {
    var out = [];
    var seen = {};
    function visit(value, depth) {
      if (depth > 3 || out.length >= 8 || value == null) return;
      if (typeof value === 'string') {
        if (/^https?:\/\//i.test(value) && !seen[value]) { seen[value] = true; out.push(value); }
        return;
      }
      if (Array.isArray(value)) { value.forEach(function (item) { visit(item, depth + 1); }); return; }
      if (typeof value !== 'object') return;
      Object.keys(value).forEach(function (key) {
        if (/url|media|asset|output|result|image|video/i.test(key)) visit(value[key], depth + 1);
      });
    }
    visit(payload, 0);
    return out;
  }

  function addMediaView(bubble, payload) {
    var urls = mediaUrls(payload);
    if (!bubble || !urls.length) return;
    var wrap = bubble.wrapper.querySelector('.online-mastra-message-media');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.className = 'online-mastra-message-media';
      bubble.wrapper.appendChild(wrap);
    }
    urls.forEach(function (url) {
      if (bubble.media[url]) return;
      bubble.media[url] = true;
      var link = document.createElement('a');
      link.href = url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = url.split('/').pop().split('?')[0] || '打开结果';
      wrap.appendChild(link);
    });
  }

  // 调度过程按"流式状态行"实时展示：排队、理解、调用能力、等待确认、执行、
  // 发布等事件都补一行进去，用户不用等到最后才知道在干什么。
  var ONLINE_MASTRA_STATUS_EVENTS = [
    'queued',
    'claimed',
    'thinking',
    'progress',
    'tool_start',
    'tool_end',
    'publish_pending',
    'publish_claimed',
    'publish_result',
    'approval_decided'
  ];

  var ONLINE_MASTRA_EVENT_LABELS = {
    queued: '已进入调度队列',
    claimed: '调度助手已接收',
    thinking: '正在理解你的需求…',
    tool_start: '正在调用能力',
    tool_end: '能力调用完成',
    publish_pending: '正在提交发布',
    publish_claimed: '发布任务已接收',
    publish_result: '发布结果已返回',
    approval_decided: '已确认，正在执行'
  };

  function eventStatusText(payload, type) {
    var source = payload && typeof payload === 'object' ? payload : {};
    var value = text(source.text || source.message || source.detail || source.reply_text || '').trim();
    var toolName = text(source.name || source.tool_id || source.tool || '').trim();
    if (value) {
      if (toolName && (type === 'tool_start' || type === 'tool_end')) {
        return (type === 'tool_start' ? '正在调用：' : '调用完成：') + toolName;
      }
      return value;
    }
    if (toolName && (type === 'tool_start' || type === 'tool_end')) {
      return (type === 'tool_start' ? '正在调用：' : '调用完成：') + toolName;
    }
    return ONLINE_MASTRA_EVENT_LABELS[type] || '';
  }

  // 过程面板：正在跑的时候只有一行"当前步骤"在更新（不刷屏），
  // 跑完折叠成"过程 · N 步"，点开能看完整步骤。
  function ensureProcessPanel(bubble) {
    if (bubble.processPanel) return bubble.processPanel;
    var panel = document.createElement('div');
    panel.className = 'online-mastra-process is-running';
    panel.innerHTML = [
      '<button type="button" class="online-mastra-process-head">',
      '<span class="online-mastra-process-dot" aria-hidden="true"></span>',
      '<span class="online-mastra-process-current">正在处理…</span>',
      '<span class="online-mastra-process-count"></span>',
      '<span class="online-mastra-process-chevron" aria-hidden="true"></span>',
      '</button>',
      '<div class="online-mastra-process-steps" hidden></div>'
    ].join('');
    var head = panel.querySelector('.online-mastra-process-head');
    var steps = panel.querySelector('.online-mastra-process-steps');
    head.addEventListener('click', function () {
      var willOpen = steps.hidden;
      steps.hidden = !willOpen;
      panel.classList.toggle('is-open', willOpen);
      if (willOpen && steps.lastChild && steps.lastChild.scrollIntoView) {
        steps.lastChild.scrollIntoView({ block: 'nearest' });
      }
    });
    bubble.wrapper.appendChild(panel);
    bubble.processPanel = panel;
    bubble.processSteps = [];
    return panel;
  }

  function appendStatusLine(bubble, line) {
    var value = text(line).trim();
    if (!bubble || !bubble.wrapper || !value) return;
    var panel = ensureProcessPanel(bubble);
    var current = panel.querySelector('.online-mastra-process-current');
    var steps = panel.querySelector('.online-mastra-process-steps');
    var count = panel.querySelector('.online-mastra-process-count');
    var list = bubble.processSteps || (bubble.processSteps = []);
    var isRepeat = list.length && list[list.length - 1] === value;
    if (!isRepeat) {
      list.push(value);
      if (list.length > 60) list.shift();
      var row = document.createElement('div');
      row.className = 'online-mastra-process-step';
      row.textContent = value;
      steps.appendChild(row);
      while (steps.childNodes.length > 60) steps.removeChild(steps.firstChild);
    }
    if (current) current.textContent = value;
    if (count) count.textContent = list.length > 1 ? list.length + ' 步' : '';
    panel.classList.add('is-running');
    panel.classList.remove('is-done');
    if (isRepeat && !panel.classList.contains('is-open')) return;
    scrollToBottom();
  }

  function finishProcessPanel(bubble) {
    if (!bubble || !bubble.processPanel) return;
    var panel = bubble.processPanel;
    panel.classList.remove('is-running', 'is-open');
    panel.classList.add('is-done');
    var steps = panel.querySelector('.online-mastra-process-steps');
    if (steps) steps.hidden = true;
    var count = panel.querySelector('.online-mastra-process-count');
    if (count) {
      var total = (bubble.processSteps || []).length;
      count.textContent = total ? total + ' 步' : '';
    }
  }

  function approvalModal() {
    var box = document.getElementById('onlineMastraApprovalModal');
    if (box) return box;
    box = document.createElement('div');
    box.id = 'onlineMastraApprovalModal';
    box.className = 'online-mastra-modal hidden';
    box.innerHTML = [
      '<div class="online-mastra-modal-card" role="dialog" aria-modal="true" aria-labelledby="onlineMastraApprovalTitle">',
      '<div class="online-mastra-modal-head">',
      '<span class="online-mastra-modal-icon" aria-hidden="true">!</span>',
      '<div><h2 id="onlineMastraApprovalTitle">需要你确认</h2>',
      '<p>确认后才会真正执行，可能消耗额度或对外发布。</p></div>',
      '</div>',
      '<div class="online-mastra-modal-task"></div>',
      '<div class="online-mastra-modal-actions">',
      '<button type="button" class="ghost" data-mastra-modal="reject">取消</button>',
      '<button type="button" class="primary" data-mastra-modal="approve">确认执行</button>',
      '</div>',
      '</div>'
    ].join('');
    document.body.appendChild(box);
    box.addEventListener('click', function (event) {
      if (event.target === box) closeApprovalModal();
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && !box.classList.contains('hidden')) closeApprovalModal();
    });
    return box;
  }

  function closeApprovalModal() {
    var box = document.getElementById('onlineMastraApprovalModal');
    if (!box || box.classList.contains('hidden')) return;
    box.classList.add('hidden');
    box.dataset.mastraApproval = '';
    box.dataset.mastraMessage = '';
  }

  function openApprovalModal(approval, messageId) {
    var box = approvalModal();
    box.dataset.mastraApproval = text(approval.id);
    box.dataset.mastraMessage = text(messageId || approval.message_id);
    var task = box.querySelector('.online-mastra-modal-task');
    if (task) task.textContent = text(approval.task || approval.reason || '将执行当前任务').trim();
    var actions = box.querySelector('.online-mastra-modal-actions');
    if (actions) {
      actions.querySelectorAll('button').forEach(function (button) {
        button.disabled = false;
        if (button._mastraModalBound) return;
        button._mastraModalBound = true;
        button.addEventListener('click', function () {
          if (box.classList.contains('hidden')) return;
          actions.querySelectorAll('button').forEach(function (item) { item.disabled = true; });
          decideApproval(box.dataset.mastraApproval, button.getAttribute('data-mastra-modal'), box, box.dataset.mastraMessage);
        });
      });
    }
    box.classList.remove('hidden');
    var primary = actions ? actions.querySelector('[data-mastra-modal="approve"]') : null;
    if (primary && primary.focus) setTimeout(function () { primary.focus(); }, 30);
    scrollToBottom();
  }

  function renderApproval(bubble, approval, messageId, historical) {
    if (!bubble || !approval || !approval.id) return;
    var markerId = '[data-mastra-approval-marker="' + text(approval.id) + '"]';
    if (bubble.wrapper.querySelector(markerId)) return;
    // 气泡里只留一条"等待确认/已确认"的痕迹，真正的确认走弹窗。
    var row = document.createElement('div');
    row.className = 'online-mastra-approval-marker is-pending';
    row.setAttribute('data-mastra-approval-marker', text(approval.id));
    var label = document.createElement('span');
    label.textContent = '等待确认：';
    var summary = document.createElement('em');
    summary.textContent = text(approval.task || approval.reason || '将执行当前任务').split('\n')[0].slice(0, 60);
    row.appendChild(label);
    row.appendChild(summary);
    bubble.wrapper.appendChild(row);
    // 历史回放不弹窗，避免一进会话就糊一脸确认框。
    if (!historical) openApprovalModal(approval, messageId);
  }

  function applyEvent(messageId, event, historical) {
    var live = state.live[text(messageId)];
    if (!live || !event) return;
    var type = text(event.type || event.event_type);
    var payload = event.payload || {};
    state.lastEventIds[text(messageId)] = Math.max(Number(state.lastEventIds[text(messageId)] || 0), Number(event.id || 0));
    if (type === 'delta') {
      if (live.bubble.placeholder) {
        setBubbleText(live.bubble, '');
        live.bubble.placeholder = false;
      }
      appendBubbleText(live.bubble, payload.text || '');
    }
    if (ONLINE_MASTRA_STATUS_EVENTS.indexOf(type) >= 0) {
      // 队列/理解/执行进度都当成流式状态行实时补进去，不要只等最终结果。
      // 历史回放不加，避免老会话里堆一屏状态行。
      if (!historical) appendStatusLine(live.bubble, eventStatusText(payload, type));
    }
    if (type === 'progress' && payload.reply_text) setBubbleText(live.bubble, payload.reply_text);
    if (type === 'approval_required') renderApproval(live.bubble, payload, messageId, historical);
    if (type === 'approval_decided') {
      appendStatusLine(live.bubble, eventStatusText(payload, type) || '已确认，正在执行');
    }
    if (type === 'cancelled') {
      setBubbleText(live.bubble, payload.reply_text || payload.text || live.bubble.text || '已取消');
      if (!historical) finishMessage(messageId, false);
    }
    if (type === 'final') {
      setBubbleText(live.bubble, payload.reply_text || payload.text || live.bubble.text || '处理完成。');
      addMediaView(live.bubble, payload);
      if (!historical) finishMessage(messageId, false);
    }
    if (type === 'error') {
      live.bubble.wrapper.classList.add('is-error');
      setBubbleText(live.bubble, payload.error || payload.detail || payload.message || '处理失败');
      if (!historical) finishMessage(messageId, true);
    }
  }

  function finishMessage(messageId, failed) {
    closeStream(messageId);
    var id = text(messageId);
    var live = state.live[id];
    if (live && live.bubble) live.bubble.wrapper.classList.toggle('is-error', !!failed);
    if (live && live.bubble) {
      finishProcessPanel(live.bubble);
      settleApprovalCard(live.bubble, failed);
    }
    if (text(state.running.messageId) === id) state.running = { messageId: '', sessionId: '' };
    loadSessions().catch(function () {});
    state.sending = false;
    syncRunningUi();
  }

  function pollMessage(messageId) {
    var id = text(messageId);
    if (state.polls[id]) return;
    var failures = 0;
    function schedule(delay) {
      if (!state.live[id] || state.streams[id]) return;
      state.polls[id] = setTimeout(run, delay);
    }
    function run() {
      delete state.polls[id];
      if (!state.live[id]) return;
      if (document.visibilityState === 'hidden') {
        schedule(15000);
        return;
      }
      request('/api/h5-chat/messages/' + encodeURIComponent(id) + '?after_event_id=' + Number(state.lastEventIds[id] || 0)).then(function (data) {
        failures = 0;
        (data.events || []).forEach(function (event) { applyEvent(id, event, false); });
        var status = data.message && text(data.message.status);
        if (status === 'completed' || status === 'failed' || status === 'cancelled') {
          var live = state.live[id];
          if (live && data.message.reply_text && !live.bubble.text) setBubbleText(live.bubble, data.message.reply_text);
          finishMessage(id, status === 'failed');
          return;
        }
        schedule(5000);
      }).catch(function (error) {
        if (error && (error.status === 401 || error.status === 403 || error.status === 404)) {
          var live = state.live[id];
          if (live) {
            live.bubble.wrapper.classList.add('is-error');
            setBubbleText(live.bubble, error.message || '查询失败');
          }
          finishMessage(id, true);
          return;
        }
        failures += 1;
        schedule(Math.min(30000, 5000 * Math.pow(2, Math.min(failures, 3))));
      });
    }
    schedule(5000);
  }

  function startStream(messageId) {
    var id = text(messageId);
    if (!id || state.streams[id]) return;
    if (!window.EventSource) { pollMessage(id); return; }
    var url = apiUrl('/api/h5-chat/messages/' + encodeURIComponent(id) + '/events?token=' + encodeURIComponent(text(typeof token !== 'undefined' ? token : '')) + '&last_event_id=' + Number(state.lastEventIds[id] || 0));
    var stream = new EventSource(url);
    state.streams[id] = stream;
    ['queued', 'claimed', 'thinking', 'progress', 'tool_start', 'tool_end', 'delta', 'final', 'error', 'cancelled', 'approval_required', 'publish_pending', 'publish_claimed', 'publish_result'].forEach(function (type) {
      stream.addEventListener(type, function (event) {
        try { applyEvent(id, JSON.parse(event.data || '{}'), false); } catch (e) {}
        scrollToBottom();
      });
    });
    stream.onerror = function () {
      try { stream.close(); } catch (e) {}
      delete state.streams[id];
      pollMessage(id);
    };
  }

  function closeStream(messageId) {
    var id = text(messageId);
    if (state.streams[id]) {
      try { state.streams[id].close(); } catch (e) {}
      delete state.streams[id];
    }
    if (state.polls[id]) {
      clearTimeout(state.polls[id]);
      delete state.polls[id];
    }
  }

  function renderHistoryItem(item) {
    var message = item && item.message ? item.message : {};
    if (!message.id) return;
    var userBubble = createBubble('user', message.content || (message.attachments && message.attachments.length ? '已添加 ' + message.attachments.length + ' 个素材' : ''));
    addAttachmentView(userBubble, message.attachments || []);
    var final = ['completed', 'failed', 'cancelled'].indexOf(text(message.status)) >= 0;
    var historicalHasDelta = (item.events || []).some(function (event) { return event && text(event.type || event.event_type) === 'delta'; });
    var assistantBubble = createBubble('assistant', final ? (message.reply_text || message.error || (message.status === 'cancelled' ? '已取消' : '处理完成。')) : (historicalHasDelta ? '' : '正在处理…'));
    assistantBubble.placeholder = !final && !historicalHasDelta;
    if (message.status === 'failed') assistantBubble.wrapper.classList.add('is-error');
    state.live[message.id] = { bubble: assistantBubble };
    (item.events || []).forEach(function (event) { applyEvent(message.id, event, true); });
    if (message.status === 'completed' && message.reply_text) setBubbleText(assistantBubble, message.reply_text);
    if (message.status === 'failed' && message.error) setBubbleText(assistantBubble, message.error);
    if (!final) startStream(message.id);
  }

  function renderHistory(items) {
    closeAllStreams();
    state.live = {};
    state.lastEventIds = {};
    clearMessages();
    state.historyItems = Array.isArray(items) ? items : [];
    state.historyItems.forEach(renderHistoryItem);
    scrollToBottom();
  }

  function loadHistory() {
    var id = text(state.activeSessionId);
    if (!id) return Promise.resolve();
    var seq = ++state.requestSeq;
    return request('/api/h5-chat/messages?limit=100&include_events=true&session_id=' + encodeURIComponent(id)).then(function (data) {
      if (seq !== state.requestSeq || id !== text(state.activeSessionId)) return;
      renderHistory(data.messages || []);
    }).catch(function (error) {
      if (seq !== state.requestSeq) return;
      clearMessages();
      var empty = el('onlineMastraEmpty');
      if (empty) empty.textContent = error.message || '会话加载失败';
    });
  }

  function switchSession(id, options) {
    options = options || {};
    id = text(id);
    if (!id) return Promise.resolve();
    if (id === text(state.activeSessionId) && state.historyItems.length) {
      if (options.compose !== false) enterCompose('', false);
      return Promise.resolve();
    }
    closeAllStreams();
    state.activeSessionId = id;
    syncRunningUi();
    storeSessionId(id);
    if (options.compose !== false) enterCompose('', false);
    renderSessionHeader();
    return loadHistory();
  }

  function loadSessions() {
    if (!text(typeof token !== 'undefined' ? token : '')) return Promise.resolve([]);
    return request('/api/mastra-chat/sessions').then(function (data) {
      state.sessions = Array.isArray(data.sessions) ? data.sessions : [];
      var stored = getStoredSessionId();
      var chosen = state.sessions.some(function (row) { return text(row.id) === stored; }) ? stored : text(state.sessions[0] && state.sessions[0].id);
      renderSessionHeader();
      if (!chosen) return createSession({ compose: false });
      if (chosen !== text(state.activeSessionId) || !state.historyItems.length) return switchSession(chosen, { compose: false });
      return state.sessions;
    });
  }

  function createSession(options) {
    options = options || {};
    return request('/api/mastra-chat/sessions', { method: 'POST', json: { title: '新会话', permission_mode: 'confirm' } }).then(function (data) {
      var session = data.session || {};
      if (!session.id) throw new Error('创建会话失败');
      state.sessions = [session].concat(state.sessions.filter(function (row) { return text(row.id) !== text(session.id); }));
      state.activeSessionId = text(session.id);
      storeSessionId(state.activeSessionId);
      state.historyItems = [];
      if (options.compose !== false) enterCompose('', true);
      renderSessionHeader();
      clearMessages();
      return session;
    });
  }

  function deleteSession(id) {
    var session = state.sessions.find(function (row) { return text(row.id) === text(id); });
    if (!session || !window.confirm('确定删除会话“' + (session.title || '新会话') + '”吗？')) return;
    request('/api/mastra-chat/sessions/' + encodeURIComponent(id), { method: 'DELETE' }).then(function () {
      state.sessions = state.sessions.filter(function (row) { return text(row.id) !== text(id); });
      if (text(state.activeSessionId) === text(id)) {
        state.activeSessionId = '';
        state.historyItems = [];
        if (state.sessions.length) switchSession(state.sessions[0].id);
        else createSession();
      }
      renderSessionHeader();
    }).catch(function (error) { window.alert(error.message || '删除失败'); });
  }

  function updatePermission(mode) {
    var session = activeSession();
    if (!session) return;
    request('/api/mastra-chat/sessions/' + encodeURIComponent(session.id), { method: 'PATCH', json: { permission_mode: mode === 'full' ? 'full' : 'confirm' } }).then(function (data) {
      Object.assign(session, data.session || { permission_mode: mode });
      closePermissionMenu();
      renderSessionHeader();
    }).catch(function (error) { window.alert(error.message || '权限设置失败'); });
  }

  function closePermissionMenu() {
    var menu = el('onlineMastraSessionMenu');
    var button = el('onlineMastraPermissionCurrent');
    if (menu) menu.hidden = true;
    if (button) button.setAttribute('aria-expanded', 'false');
  }

  function renderUploadList() {
    var host = el('onlineMastraAttachments');
    if (!host) return;
    host.hidden = !state.uploads.length;
    host.innerHTML = state.uploads.map(function (item, index) {
      return '<span class="online-mastra-upload-chip ' + (item.error ? 'is-error' : '') + '">' + escapeHtml(item.name || '素材') +
        '<button type="button" data-mastra-remove-upload="' + index + '" aria-label="移除素材">×</button></span>';
    }).join('');
    host.querySelectorAll('[data-mastra-remove-upload]').forEach(function (button) {
      button.addEventListener('click', function () {
        state.uploads.splice(Number(button.getAttribute('data-mastra-remove-upload')), 1);
        renderUploadList();
      });
    });
  }

  function uploadFiles(files) {
    var list = Array.prototype.slice.call(files || []).slice(0, 8 - state.uploads.length);
    list.forEach(function (file) {
      var item = { name: file.name || '素材', status: 'uploading', error: '', asset_id: '', url: '', media_type: '' };
      state.uploads.push(item);
      renderUploadList();
      var headers = typeof authHeaders === 'function' ? authHeaders() : { 'Authorization': 'Bearer ' + text(typeof token !== 'undefined' ? token : '') };
      delete headers['Content-Type'];
      var form = new FormData();
      form.append('file', file, file.name || 'upload');
      fetch(apiUrl('/api/assets/upload'), { method: 'POST', headers: headers, body: form }).then(function (response) {
        return response.text().then(function (body) {
          var data = {};
          try { data = body ? JSON.parse(body) : {}; } catch (e) {}
          if (!response.ok) throw new Error(text(data.detail || data.message || ('HTTP ' + response.status)));
          return data;
        });
      }).then(function (data) {
        item.asset_id = text(data.asset_id);
        item.url = text(data.source_url || data.url);
        item.media_type = text(data.media_type || 'file');
        item.status = item.asset_id ? 'ready' : 'failed';
        if (item.status === 'failed') item.error = '上传结果缺少素材 ID';
        renderUploadList();
      }).catch(function (error) {
        item.status = 'failed';
        item.error = error.message || '上传失败';
        renderUploadList();
      });
    });
  }

  function readyAttachments() {
    return state.uploads.filter(function (item) { return item.status === 'ready' && item.asset_id; }).map(function (item) {
      return { asset_id: item.asset_id, url: item.url, name: item.name, media_type: item.media_type || 'file' };
    });
  }

  function submitMessage(event) {
    if (event) event.preventDefault();
    var input = el('onlineMastraInput');
    var content = text(input && input.value).trim();
    var attachments = readyAttachments();
    if (!content && !attachments.length) return;
    if (state.sending) return;
    if (state.uploads.some(function (item) { return item.status === 'uploading'; })) {
      window.alert('素材仍在上传，请稍候再发送');
      return;
    }
    if (!state.activeSessionId) {
      createSession({ compose: false }).then(function () { submitMessage(event); }).catch(function (error) { window.alert(error.message || '会话创建失败'); });
      return;
    }
    state.sending = true;
    setComposerEnabled(false);
    if (input) input.value = '';
    resizeInput();
    request('/api/mastra-chat/messages', {
      method: 'POST',
      json: { content: content, installation_id: installationId(), session_id: state.activeSessionId, attachments: attachments }
    }).then(function (data) {
      var message = data.message || {};
      if (!message.id) throw new Error('服务器没有返回消息 ID');
      var item = { message: message, events: data.events || [] };
      state.historyItems.push(item);
      renderHistoryItem(item);
      state.uploads = [];
      renderUploadList();
      state.sending = false;
      setComposerEnabled(true);
      scrollToBottom();
      startStream(message.id);
      loadSessions().catch(function () { renderSessionHeader(); });
    }).catch(function (error) {
      window.alert(error.message || '发送失败');
      state.sending = false;
      setComposerEnabled(true);
    });
  }

  function decideApproval(id, decision, card, messageId) {
    var buttons = card ? card.querySelectorAll('button') : [];
    buttons.forEach(function (button) { button.disabled = true; });
    request('/api/mastra-chat/approvals/' + encodeURIComponent(id) + '/decision', { method: 'POST', json: { decision: decision } }).then(function (data) {
      closeApprovalModal();
      var live = state.live[text(messageId)];
      var marker = live && live.bubble && live.bubble.wrapper
        ? live.bubble.wrapper.querySelector('[data-mastra-approval-marker="' + text(id) + '"]')
        : null;
      if (marker) {
        marker.classList.remove('is-pending');
        marker.classList.add('is-decided');
        var label = marker.querySelector('span');
        if (label) label.textContent = decision === 'approve' ? '已确认：' : '已取消：';
      }
      if (decision === 'approve') {
        if (live && live.bubble) appendStatusLine(live.bubble, '已确认，正在执行…');
        startRunningTask(text(messageId), null);
        return;
      }
      if (live && live.bubble) appendStatusLine(live.bubble, '已取消执行');
    }).catch(function (error) {
      buttons.forEach(function (button) { button.disabled = false; });
      window.alert(error.message || '操作失败');
    });
  }

  function isTaskRunningHere() {
    var id = text(state.running.messageId);
    return !!id && text(state.running.sessionId) === text(state.activeSessionId);
  }

  function settleApprovalCard(bubble, failed) {
    if (!bubble || !bubble.wrapper) return;
    var marker = bubble.wrapper.querySelector('.online-mastra-approval-marker.is-pending');
    if (!marker) return;
    marker.classList.remove('is-pending');
    marker.classList.add('is-decided');
    var label = marker.querySelector('span');
    if (label) label.textContent = failed ? '执行失败：' : '已结束：';
  }

  function syncRunningUi() {
    var status = el('onlineMastraChatStatus');
    var running = isTaskRunningHere();
    if (status) {
      var flag = running ? '1' : '0';
      if (status.dataset.mastraRunning !== flag) {
        status.dataset.mastraRunning = flag;
        if (running) {
          status.dataset.mastraIdleText = status.textContent || '';
          status.textContent = '任务执行中，可点「停止执行」取消';
        } else if (status.dataset.mastraIdleText) {
          status.textContent = status.dataset.mastraIdleText;
        }
      }
    }
    setComposerEnabled(true);
  }

  function startRunningTask(messageId, card) {
    var id = text(messageId);
    if (!id) return;
    state.running = { messageId: id, sessionId: text(state.activeSessionId) };
    state.sending = false;
    if (card) {
      card.classList.add('is-decided');
      card.classList.remove('is-error');
      var actions = card.querySelector('.online-mastra-approval-actions');
      if (actions) {
        actions.innerHTML = '<button type="button" data-mastra-stop-task="1">停止执行</button><span class="online-mastra-approval-note">已确认，正在执行…</span>';
        var stop = actions.querySelector('[data-mastra-stop-task]');
        if (stop) stop.addEventListener('click', function () { stopRunningTask(id, stop); });
      }
    }
    if (!state.streams[id] && !state.polls[id]) startStream(id);
    syncRunningUi();
  }

  function stopRunningTask(messageId, button) {
    var id = text(messageId);
    if (!id) return;
    if (button) button.disabled = true;
    request('/api/mastra-chat/messages/' + encodeURIComponent(id) + '/cancel', { method: 'POST' }).then(function (data) {
      if (data && data.side_effects_may_continue) {
        window.alert('已停止调度；已经开始的外部任务可能仍会继续执行。');
      }
      finishMessage(id, false);
      loadHistory().catch(function () {});
    }).catch(function (error) {
      if (button) button.disabled = false;
      window.alert(error.message || '停止失败');
    });
  }

  function setComposerEnabled(enabled) {
    var input = el('onlineMastraInput');
    var send = el('onlineMastraSend');
    var locked = !enabled || isTaskRunningHere();
    if (input) input.disabled = locked;
    if (send) send.disabled = locked || state.sending;
  }

  function resizeInput() {
    var input = el('onlineMastraInput');
    if (!input) return;
    input.style.height = 'auto';
    input.style.height = Math.min(180, Math.max(58, input.scrollHeight)) + 'px';
  }

  function syncActiveViewClass() {
    var root = el('onlineMastraChat');
    var content = root && root.closest ? root.closest('#content-chat') : null;
    var pageActive = !!(content && content.classList.contains('visible'));
    if (!pageActive) state.composing = false;
    document.body.classList.toggle('online-mastra-chat-page', pageActive);
    document.body.classList.toggle('online-mastra-chat-compose', pageActive && state.composing);
    if (root) root.hidden = !(pageActive && state.composing);
    var home = el('chatWorkspace');
    if (home) home.hidden = !(pageActive && !state.composing);
  }

  function bindHomeEntry() {
    var homeInput = el('chatInput');
    var homeSend = el('chatSendBtn');
    var homeAttach = el('chatAttachBtn');
    function openFromHome(value, submitNow) {
      enterCompose(value, true);
      if (submitNow) setTimeout(function () {
        var form = el('onlineMastraComposer');
        var input = el('onlineMastraInput');
        if (form && (text(input && input.value).trim() || state.uploads.length)) {
          if (form.requestSubmit) form.requestSubmit();
          else submitMessage({ preventDefault: function () {} });
        }
      }, 0);
    }
    if (homeInput) {
      homeInput.addEventListener('focus', function () { openFromHome(homeInput.value, false); });
      homeInput.addEventListener('input', function () {
        if (!state.composing && homeInput.value) openFromHome(homeInput.value, false);
      });
      homeInput.addEventListener('keydown', function (event) {
        if (event.isComposing || event.keyCode === 229) return;
        if (event.key === 'Enter' && !event.shiftKey) {
          event.preventDefault();
          event.stopImmediatePropagation();
          openFromHome(homeInput.value, true);
        }
      }, true);
    }
    if (homeSend) homeSend.addEventListener('click', function (event) {
      event.preventDefault();
      event.stopImmediatePropagation();
      openFromHome(homeInput ? homeInput.value : '', true);
    }, true);
    if (homeAttach) homeAttach.addEventListener('click', function (event) {
      event.preventDefault();
      event.stopImmediatePropagation();
      enterCompose('', true);
      var picker = el('onlineMastraFileInput');
      if (picker) picker.click();
    }, true);
    document.addEventListener('click', function (event) {
      var homeTrigger = event.target.closest && event.target.closest('[data-view="chat"]');
      if (homeTrigger) setTimeout(resetToHome, 0);
    });
  }

  function bind() {
    var root = el('onlineMastraChat');
    if (!root || root.dataset.bound === '1') return;
    root.dataset.bound = '1';
    syncActiveViewClass();
    var chatContent = root.closest ? root.closest('#content-chat') : null;
    if (chatContent && window.MutationObserver) {
      state.viewObserver = new MutationObserver(syncActiveViewClass);
      state.viewObserver.observe(chatContent, { attributes: true, attributeFilter: ['class'] });
    }
    el('onlineMastraComposer').addEventListener('submit', submitMessage);
    el('onlineMastraAttach').addEventListener('click', function () { el('onlineMastraFileInput').click(); });
    el('onlineMastraFileInput').addEventListener('change', function (event) { uploadFiles(event.target.files); event.target.value = ''; });
    el('onlineMastraNewSession').addEventListener('click', function () { createSession().catch(function (error) { window.alert(error.message || '创建会话失败'); }); });
    var sidebarNewSession = el('mastraNewSessionBtn');
    if (sidebarNewSession) sidebarNewSession.addEventListener('click', function () { createSession().catch(function (error) { window.alert(error.message || '创建会话失败'); }); });
    el('onlineMastraPermissionCurrent').addEventListener('click', function () {
      var menu = el('onlineMastraSessionMenu');
      var open = menu.hidden;
      menu.hidden = !open;
      el('onlineMastraPermissionCurrent').setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    el('onlineMastraSessionMenu').querySelectorAll('[data-mastra-permission]').forEach(function (button) {
      button.addEventListener('click', function () { updatePermission(button.getAttribute('data-mastra-permission')); });
    });
    document.addEventListener('click', function (event) {
      if (!event.target.closest('#onlineMastraPermissionCurrent') && !event.target.closest('#onlineMastraSessionMenu')) closePermissionMenu();
    });
    el('mastraSessionSearch').addEventListener('input', renderSessionList);
    el('onlineMastraInput').addEventListener('keydown', function (event) {
      if (event.isComposing || event.keyCode === 229) return;
      if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submitMessage(event); }
    });
    el('onlineMastraInput').addEventListener('input', resizeInput);
    resizeInput();
    bindHomeEntry();
  }

  function init() {
    ensureRichStyles();
    if (!el('onlineMastraChat')) return;
    bind();
    state.initialized = true;
    if (typeof _syncAppSideNavActive === 'function') _syncAppSideNavActive('chat');
    loadSessions().catch(function (error) {
      var status = el('onlineMastraChatStatus');
      if (status) status.textContent = error.message || '会话服务暂不可用';
    });
  }

  function refresh() {
    var status = el('onlineMastraChatStatus');
    state.loading = false;
    state.sending = false;
    closeAllStreams();
    setComposerEnabled(true);
    if (status) status.textContent = '正在重新请求...';
    renderSessionHeader();
    return loadSessions().then(function () {
      if (state.activeSessionId) return loadHistory();
      return true;
    }).then(function () {
      if (status) status.textContent = '已恢复';
      renderSessionHeader();
      return true;
    }).catch(function (error) {
      if (status) status.textContent = error.message || '会话服务暂不可用';
      renderSessionHeader();
      return false;
    });
  }

  window.initMastraOnlineChat = init;
  window.refreshMastraOnlineChat = refresh;
})();
