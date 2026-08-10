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
    composing: false
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
        if (!response.ok) throw new Error(text(data.detail || data.message || ('HTTP ' + response.status)));
        return data;
      });
    });
  }

  function installationId() {
    if (typeof getOrCreateInstallationId === 'function') return getOrCreateInstallationId();
    var key = 'lobster_installation_id';
    var current = '';
    try { current = localStorage.getItem(key) || ''; } catch (e) {}
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
    bubble.body.textContent = bubble.text;
  }

  function appendBubbleText(bubble, value) {
    if (!bubble || !value) return;
    bubble.text += text(value);
    if (bubble.body) bubble.body.textContent = bubble.text;
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

  function renderApproval(bubble, approval) {
    if (!bubble || !approval || !approval.id || bubble.wrapper.querySelector('[data-mastra-approval]')) return;
    var card = document.createElement('div');
    card.className = 'online-mastra-approval';
    card.dataset.mastraApproval = approval.id;
    card.innerHTML = '<strong>需要确认后执行</strong><p></p><div class="online-mastra-approval-actions"><button type="button" data-mastra-approval-decision="reject">取消</button><button type="button" class="primary" data-mastra-approval-decision="approve">确认执行</button></div>';
    card.querySelector('p').textContent = text(approval.task || approval.reason || '将执行当前任务');
    card.querySelectorAll('[data-mastra-approval-decision]').forEach(function (button) {
      button.addEventListener('click', function () { decideApproval(approval.id, button.getAttribute('data-mastra-approval-decision'), card); });
    });
    bubble.wrapper.appendChild(card);
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
    if (type === 'progress' && payload.reply_text) setBubbleText(live.bubble, payload.reply_text);
    if (type === 'approval_required') renderApproval(live.bubble, payload);
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
    var live = state.live[text(messageId)];
    if (live && live.bubble) live.bubble.wrapper.classList.toggle('is-error', !!failed);
    loadSessions().catch(function () {});
    state.sending = false;
    setComposerEnabled(true);
  }

  function pollMessage(messageId) {
    var id = text(messageId);
    if (state.polls[id]) return;
    state.polls[id] = setInterval(function () {
      request('/api/h5-chat/messages/' + encodeURIComponent(id) + '?after_event_id=' + Number(state.lastEventIds[id] || 0)).then(function (data) {
        (data.events || []).forEach(function (event) { applyEvent(id, event, false); });
        var status = data.message && text(data.message.status);
        if (status === 'completed' || status === 'failed' || status === 'cancelled') {
          var live = state.live[id];
          if (live && data.message.reply_text && !live.bubble.text) setBubbleText(live.bubble, data.message.reply_text);
          finishMessage(id, status === 'failed');
        }
      }).catch(function (error) {
        var live = state.live[id];
        if (live) {
          live.bubble.wrapper.classList.add('is-error');
          setBubbleText(live.bubble, error.message || '查询失败');
        }
        finishMessage(id, true);
      });
    }, 1400);
  }

  function startStream(messageId) {
    var id = text(messageId);
    if (!id || state.streams[id]) return;
    if (!window.EventSource) { pollMessage(id); return; }
    var url = apiUrl('/api/h5-chat/messages/' + encodeURIComponent(id) + '/events?token=' + encodeURIComponent(text(typeof token !== 'undefined' ? token : '')) + '&last_event_id=' + Number(state.lastEventIds[id] || 0));
    var stream = new EventSource(url);
    state.streams[id] = stream;
    ['queued', 'claimed', 'thinking', 'progress', 'tool_start', 'tool_end', 'delta', 'final', 'error', 'approval_required', 'publish_pending', 'publish_claimed', 'publish_result'].forEach(function (type) {
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
      clearInterval(state.polls[id]);
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

  function decideApproval(id, decision, card) {
    var buttons = card ? card.querySelectorAll('button') : [];
    buttons.forEach(function (button) { button.disabled = true; });
    request('/api/mastra-chat/approvals/' + encodeURIComponent(id) + '/decision', { method: 'POST', json: { decision: decision } }).then(function () {
      if (card) {
        card.classList.add('is-decided');
        card.querySelector('.online-mastra-approval-actions').textContent = decision === 'approve' ? '已确认，正在执行' : '已取消执行';
      }
    }).catch(function (error) {
      buttons.forEach(function (button) { button.disabled = false; });
      window.alert(error.message || '操作失败');
    });
  }

  function setComposerEnabled(enabled) {
    var input = el('onlineMastraInput');
    var send = el('onlineMastraSend');
    if (input) input.disabled = !enabled;
    if (send) send.disabled = !enabled || state.sending;
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
