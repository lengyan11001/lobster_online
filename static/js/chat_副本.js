/** 旧版全局键；升级后首次登录会迁移到 lobster_chat_sessions_u{userId} 并删除 */
var LEGACY_CHAT_SESSIONS_KEY = 'lobster_chat_sessions';

function getChatSessionsStorageKey() {
  var uid = '';
  if (typeof window.__currentUserId !== 'undefined' && window.__currentUserId != null) {
    uid = String(window.__currentUserId);
  }
  if (!uid && typeof window.getCurrentUserIdFromToken === 'function') {
    uid = window.getCurrentUserIdFromToken();
  }
  return uid ? ('lobster_chat_sessions_u' + uid) : '';
}

function migrateLegacyChatSessionsIfNeeded() {
  var key = getChatSessionsStorageKey();
  if (!key) return;
  try {
    if (localStorage.getItem(key)) return;
    var leg = localStorage.getItem(LEGACY_CHAT_SESSIONS_KEY);
    if (!leg) return;
    localStorage.setItem(key, leg);
    localStorage.removeItem(LEGACY_CHAT_SESSIONS_KEY);
  } catch (e) {}
}

/** 切换用户或登出时清空内存与对话区 DOM（须在设置 __currentUserId 之后立刻 load/init） */
function resetChatSessionsMemory() {
  chatSessions = [];
  currentSessionId = null;
  chatHistory = [];
  chatPendingBySession = {};
  chatAttachmentIds = [];
  chatAttachmentInfos = [];
  var c = document.getElementById('chatMessages');
  if (c) c.innerHTML = '';
  var att = document.getElementById('chatAttachments');
  if (att) {
    att.style.display = 'none';
    att.innerHTML = '';
  }
  var listEl = document.getElementById('chatSessionList');
  if (listEl) listEl.innerHTML = '';
}

window.resetChatSessionsForLogout = function() {
  window.__currentUserId = undefined;
  resetChatSessionsMemory();
};

var chatSessions = [];
var currentSessionId = null;
var chatHistory = [];
var chatPendingBySession = {};
/** 当前 /chat/stream 请求的 AbortController，非空时「取消」可点 */
var chatStreamAbortController = null;
/** 当前流式对话是否已向速推提交生成任务（image/video 的 tasks/create 已成功）；为 true 时取消仅提示不可中止 */
var chatStreamSutuiSubmitted = false;
var chatAttachmentIds = [];
var chatAttachmentInfos = [];

function getSessionById(id) {
  var sid = id != null ? String(id) : '';
  return chatSessions.find(function(s) { return String(s.id) === sid; }) || null;
}
function isSessionPending(id) {
  return !!chatPendingBySession[String(id)];
}
function setSessionPending(id, pending) {
  var sid = String(id || '');
  if (!sid) return;
  if (pending) chatPendingBySession[sid] = true;
  else delete chatPendingBySession[sid];
  var s = getSessionById(sid);
  if (s) s.pending = !!pending;
  refreshChatInputState();
  renderChatSessionList();
}
function refreshChatInputState() {
  var input = document.getElementById('chatInput');
  var btn = document.getElementById('chatSendBtn');
  var cancelBtn = document.getElementById('chatCancelBtn');
  if (!btn) return;
  btn.disabled = !!(currentSessionId && isSessionPending(currentSessionId));
  if (input) input.disabled = false;
  if (cancelBtn) {
    var hasActiveStream = !!chatStreamAbortController;
    cancelBtn.disabled = !hasActiveStream;
    cancelBtn.title = hasActiveStream && chatStreamSutuiSubmitted
      ? '任务已在速推生成中：点击可查看说明'
      : '终止当前进行中的请求；尚未提交到速推时会直接中止';
  }
}
function renderCurrentSessionMessages() {
  var container = document.getElementById('chatMessages');
  if (!container) return;
  container.innerHTML = '';
  var sid = currentSessionId ? String(currentSessionId) : '';
  var session = getSessionById(sid);
  var messages = session && Array.isArray(session.messages) ? session.messages : [];
  chatHistory = messages.slice();
  messages.forEach(function(m) {
    if (m.role === 'assistant' && m.saved_assets && m.saved_assets.length) {
      appendAssistantMessageReveal(m.content || '', m.saved_assets);
    } else if (m.role === 'user') {
      appendUserMessageDisplay(m.content, m.attachment_asset_ids);
    } else {
      appendChatMessage(m.role, m.content);
    }
  });
  if (sid && isSessionPending(sid)) showChatTypingIndicator();
  container.scrollTop = container.scrollHeight;
  refreshChatInputState();
}

function loadChatSessionsFromStorage() {
  /** 必须先清空，避免「新用户 localStorage 无数据」时仍沿用上一用户内存中的 chatSessions */
  chatSessions = [];
  try {
    migrateLegacyChatSessionsIfNeeded();
    var key = getChatSessionsStorageKey();
    if (!key) return;
    var raw = localStorage.getItem(key);
    if (!raw) return;
    var parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      chatSessions = parsed;
      chatSessions.forEach(function(s) {
        if (s.id != null) s.id = String(s.id);
        var m = s.messages || s.history;
        s.messages = Array.isArray(m) ? m : [];
      });
    }
  } catch (e) {
    chatSessions = [];
  }
}
function saveChatSessionsToStorage() {
  try {
    var key = getChatSessionsStorageKey();
    if (!key) return;
    localStorage.setItem(key, JSON.stringify(chatSessions));
  } catch (e) {}
}
function getSessionTitle(session) {
  var msg = (session.messages || []).find(function(m) { return m.role === 'user' && (m.content || '').trim(); });
  if (msg) {
    var t = (msg.content || '').trim();
    return t.length > 24 ? t.slice(0, 24) + '…' : t;
  }
  return session.title || '新对话';
}
function getSessionPreview(session) {
  var messages = session.messages || [];
  for (var i = messages.length - 1; i >= 0; i--) {
    var m = messages[i];
    if (m && (m.content || '').trim()) {
      var t = (m.content || '').trim();
      return t.length > 32 ? t.slice(0, 32) + '…' : t;
    }
  }
  return '暂无消息';
}
function formatSessionTime(ts) {
  if (!ts) return '';
  var d = new Date(ts);
  var now = new Date();
  var diff = (now - d) / 60000;
  if (diff < 1) return '刚刚';
  if (diff < 60) return Math.floor(diff) + ' 分钟前';
  if (diff < 1440) return Math.floor(diff / 60) + ' 小时前';
  if (diff < 43200) return Math.floor(diff / 1440) + ' 天前';
  return d.toLocaleDateString();
}
function createNewSession() {
  var id = 's' + Date.now();
  var session = { id: id, title: '新对话', messages: [], updatedAt: Date.now(), pending: false };
  chatSessions.unshift(session);
  saveChatSessionsToStorage();
  switchChatSession(id);
  renderChatSessionList();
}
function switchChatSession(id) {
  var sid = id != null ? String(id) : '';
  if (currentSessionId === sid) return;
  saveCurrentSessionToStore();
  currentSessionId = sid;
  renderCurrentSessionMessages();
  renderChatSessionList();
}
function saveCurrentSessionToStore() {
  if (!currentSessionId) return;
  var session = chatSessions.find(function(s) { return String(s.id) === String(currentSessionId); });
  if (session) {
    session.messages = Array.isArray(chatHistory) ? chatHistory.slice() : [];
    session.updatedAt = Date.now();
    if (session.messages.length) {
      var firstUser = session.messages.find(function(m) { return m && m.role === 'user'; });
      if (firstUser && (firstUser.content || '').trim()) session.title = getSessionTitle(session);
    }
    saveChatSessionsToStorage();
  }
}
window.addEventListener('beforeunload', function() { if (typeof saveCurrentSessionToStore === 'function') saveCurrentSessionToStore(); });
function renderChatSessionList() {
  var listEl = document.getElementById('chatSessionList');
  var searchVal = (document.getElementById('chatSessionSearch') && document.getElementById('chatSessionSearch').value || '').trim().toLowerCase();
  if (!listEl) return;
  var filtered = searchVal
    ? chatSessions.filter(function(s) {
        var title = getSessionTitle(s); var preview = getSessionPreview(s);
        return title.toLowerCase().indexOf(searchVal) >= 0 || preview.toLowerCase().indexOf(searchVal) >= 0;
      })
    : chatSessions.slice();
  if (filtered.length === 0) {
    listEl.innerHTML = '<p class="meta" style="padding:0.5rem;font-size:0.8rem;color:var(--text-muted);">暂无对话</p>';
    return;
  }
  listEl.innerHTML = filtered.map(function(s) {
    var title = getSessionTitle(s);
    var preview = getSessionPreview(s);
    var time = formatSessionTime(s.updatedAt);
    var active = s.id === currentSessionId ? ' active' : '';
    return '<div class="chat-session-item' + active + '" data-session-id="' + escapeAttr(s.id) + '">' +
      '<div class="session-title">' + escapeHtml(title) + '</div>' +
      '<div class="session-preview">' + escapeHtml(preview) + '</div>' +
      '<div class="session-time">' + escapeHtml(time) + '</div></div>';
  }).join('');
  listEl.querySelectorAll('.chat-session-item').forEach(function(el) {
    el.addEventListener('click', function() { switchChatSession(el.getAttribute('data-session-id')); });
  });
}
function initChatSessions() {
  loadChatSessionsFromStorage();
  if (chatSessions.length === 0) {
    createNewSession();
    return;
  }
  var targetId = currentSessionId;
  if (!targetId || !chatSessions.find(function(s) { return s.id === targetId; })) {
    targetId = chatSessions[0].id;
  }
  currentSessionId = null;
  setTimeout(function() {
    if (document.getElementById('chatMessages')) switchChatSession(targetId);
    renderChatSessionList();
  }, 0);
}

/** 模型常用 Markdown 行内代码包裹 URL，导致无法点击；去掉 URL 两侧反引号 */
function stripBackticksAroundUrls(text) {
  if (!text) return text;
  var t = text;
  t = t.replace(/`+\s*(https?:\/\/[^\s`<>]+)\s*`+/gi, '$1');
  t = t.replace(/`+\s*(https?:\/\/[^\s`<>]+)/gi, '$1');
  t = t.replace(/(https?:\/\/[^\s`<>]+)\s*`+/g, '$1');
  return t;
}

/** 正文中显式写的素材 ID（与上传附图合并展示） */
function extractAssetIdsFromUserMessageText(text) {
  var found = [];
  var seen = {};
  if (!text) return found;
  var patterns = [
    /asset_id[：:\s]+([a-f0-9]{12})\b/gi,
    /素材\s*ID[：:\s]*([a-f0-9]{12})\b/gi,
    // 「素材3108855349d9」「素材 3108855349d9」等（与 mergeUserMessageAssetIds 展示一致，并随请求带上附图 ID）
    /素材\D*([a-f0-9]{12})\b/gi
  ];
  patterns.forEach(function(re) {
    var m;
    var r = new RegExp(re.source, re.flags);
    while ((m = r.exec(text)) !== null) {
      var id = (m[1] || '').toLowerCase();
      if (id && !seen[id]) {
        seen[id] = true;
        found.push(id);
      }
    }
  });
  return found;
}

function mergeUserMessageAssetIds(attachmentIds, content) {
  var seen = {};
  var out = [];
  (attachmentIds || []).forEach(function(id) {
    id = String(id || '').trim().toLowerCase();
    if (!id || seen[id]) return;
    seen[id] = true;
    out.push(id);
  });
  extractAssetIdsFromUserMessageText(content || '').forEach(function(id) {
    if (!seen[id]) {
      seen[id] = true;
      out.push(id);
    }
  });
  return out;
}

function linkifyText(text) {
  var raw = stripBackticksAroundUrls(text || '');
  var escaped = raw.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    var result = escaped.replace(/https?:\/\/[^\s<>"'`]+/g, function(raw) {
    var url = raw;
    var suffix = '';
    while (/[)\]}\u3002\uff0c\uff01\uff1f,.]$/.test(url)) {
      if (url.endsWith(')')) {
        var opens = (url.match(/\(/g) || []).length;
        var closes = (url.match(/\)/g) || []).length;
        if (closes <= opens) break;
      }
      suffix = url.slice(-1) + suffix;
      url = url.slice(0, -1);
    }
    var rewritten = url.replace(/^https?:\/\/(?:localhost|127\.0\.0\.1):8000\/media\//, window.location.origin + '/media/');
    return '<a href="' + rewritten + '" target="_blank" rel="noopener noreferrer">' + rewritten + '</a>' + suffix;
  });
  result = result.replace(/(^|[^a-zA-Z0-9/">=])\/media\/[^\s<>"'`]+/g, function(match, prefix) {
    var path = match.slice(prefix.length);
    var full = window.location.origin + path;
    return prefix + '<a href="' + full + '" target="_blank" rel="noopener noreferrer">' + full + '</a>';
  });
  return result;
}

function appendUserMessageDisplay(content, attachmentAssetIds) {
  var container = document.getElementById('chatMessages');
  if (!container) return;
  var div = document.createElement('div');
  div.className = 'chat-msg user';
  var roleDiv = document.createElement('div');
  roleDiv.className = 'role';
  roleDiv.textContent = '我';
  var bodyDiv = document.createElement('div');
  bodyDiv.className = 'chat-msg-body';
  div.appendChild(roleDiv);
  div.appendChild(bodyDiv);
  var ids = mergeUserMessageAssetIds(attachmentAssetIds, content);
  if (ids.length) {
    var refsWrap = document.createElement('div');
    refsWrap.className = 'chat-user-refs';
    var base = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? String(LOCAL_API_BASE).replace(/\/$/, '') : '';
    ids.forEach(function(aid) {
      var box = document.createElement('div');
      box.className = 'chat-user-ref-box';
      var cap = document.createElement('div');
      cap.className = 'chat-user-ref-id';
      cap.textContent = aid;
      box.appendChild(cap);
      if (base && typeof authHeaders === 'function') {
        fetch(base + '/api/assets/' + encodeURIComponent(aid) + '/content', { headers: authHeaders() })
          .then(function(r) {
            if (!r.ok) throw new Error('no');
            return r.blob();
          })
          .then(function(blob) {
            var u = URL.createObjectURL(blob);
            box._blobUrl = u;
            if ((blob.type || '').indexOf('video') >= 0) {
              var v = document.createElement('video');
              v.src = u;
              v.muted = true;
              v.playsInline = true;
              v.controls = true;
              v.preload = 'metadata';
              box.insertBefore(v, cap);
            } else {
              var img = document.createElement('img');
              img.src = u;
              img.alt = aid;
              box.insertBefore(img, cap);
            }
          })
          .catch(function() {
            cap.textContent = '素材 ' + aid + '（预览失败）';
          });
      } else {
        cap.textContent = '素材 ' + aid;
      }
      refsWrap.appendChild(box);
    });
    bodyDiv.appendChild(refsWrap);
  }
  var textDiv = document.createElement('div');
  textDiv.className = 'chat-msg-text';
  var text = (content || '').trim() || (ids.length ? '' : '（无内容）');
  if (text) textDiv.innerHTML = linkifyText(text);
  bodyDiv.appendChild(textDiv);
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function appendChatMessage(role, content) {
  if (role === 'user') {
    appendUserMessageDisplay(content, null);
    return;
  }
  var container = document.getElementById('chatMessages');
  if (!container) return;
  var div = document.createElement('div');
  div.className = 'chat-msg ' + role;
  var text = (content || '').trim() || '（无内容）';
  var html = linkifyText(text);
  div.innerHTML = '<div class="role">' + (role === 'user' ? '我' : '龙虾') + '</div>' + html;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}
var _toolNameLabels = {
  invoke_capability: '调用能力',
  publish_content: '发布内容',
  publish_youtube_video: '上传到 YouTube',
  list_youtube_accounts: 'YouTube 账号列表',
  list_assets: '查看素材',
  list_publish_accounts: '查看账号',
  check_account_login: '检查登录',
  open_account_browser: '打开浏览器'
};
function _toolLabel(name) { return _toolNameLabels[name] || name; }

function showChatTypingIndicator() {
  var container = document.getElementById('chatMessages');
  if (!container) return;
  var div = document.createElement('div');
  div.id = 'chatTypingIndicator';
  div.className = 'chat-msg assistant typing';
  div.innerHTML = '<div class="role">龙虾</div><div class="typing-dots"><span></span><span></span><span></span></div> <span class="typing-text">正在思考...</span><div class="typing-steps" id="chatTypingSteps"></div>';
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}
function appendChatTypingStep(text) {
  var steps = document.getElementById('chatTypingSteps');
  if (!steps) return;
  var line = document.createElement('div');
  line.className = 'typing-step';
  line.style.cssText = 'font-size:0.82rem;color:var(--text-muted);margin-top:0.35rem;';
  line.textContent = text;
  steps.appendChild(line);
  var container = document.getElementById('chatMessages');
  if (container) container.scrollTop = container.scrollHeight;
}
function updateLastChatTypingStep(text) {
  var steps = document.getElementById('chatTypingSteps');
  if (!steps || !steps.lastElementChild) return;
  steps.lastElementChild.textContent = text;
  var container = document.getElementById('chatMessages');
  if (container) container.scrollTop = container.scrollHeight;
}
function setChatTypingMainText(text) {
  var el = document.querySelector('#chatTypingIndicator .typing-text');
  if (el) el.textContent = text || '正在思考...';
}
function _extractPollSeconds(message) {
  var s = String(message || '');
  var m = s.match(/（(\d+)秒）/);
  if (m && m[1]) return m[1];
  m = s.match(/\((\d+)\s*sec\)/i);
  return (m && m[1]) ? m[1] : '';
}

function getLocalApiBaseForAssets() {
  var b = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? String(LOCAL_API_BASE).replace(/\/$/, '') : '';
  return b;
}

/** 会话素材「显示内容」：优先 prompt/说明，否则缩短后的链接 */
function savedAssetDisplayText(a) {
  if (!a) return '';
  var p = (a.prompt || a.caption || a.description || a.title || a.label || '').trim();
  if (p) return p;
  var u = (a.source_url || a.url || '').trim();
  if (!u) return '';
  return u.length > 140 ? u.slice(0, 140) + '…' : u;
}

function savedAssetPrimaryHttpUrl(a) {
  return ((a && (a.source_url || a.url)) || '').trim();
}

function scrollChatMessagesToBottom() {
  var container = document.getElementById('chatMessages');
  if (container) container.scrollTop = container.scrollHeight;
}

/**
 * 流式/终态：展示素材 ID、显示内容，并优先用本机 GET /api/assets/:id/content 加载预览（图/视频 blob）。
 */
function appendSavedAssetDom(parent, a, opts) {
  if (!parent || !a) return;
  opts = opts || {};
  var compact = !!opts.compact;
  var assetId = (a.asset_id || '').trim();
  var mediaType = (a.media_type || 'image').toLowerCase();
  var box = document.createElement('div');
  box.className = 'chat-generated-asset-item';

  var idEl = document.createElement('div');
  idEl.className = 'chat-generated-asset-id';
  var httpUrlEarly = savedAssetPrimaryHttpUrl(a);
  var pendingDedup = (!assetId && httpUrlEarly) ? _pendingAssetDedupAttrKey(httpUrlEarly) : '';
  if (pendingDedup) box.setAttribute('data-pending-asset-dedup', pendingDedup);
  if (assetId) {
    idEl.textContent = '素材 ID · ' + assetId;
  } else if (pendingDedup) {
    idEl.textContent = '素材（正在写入素材库…）';
  } else {
    idEl.textContent = '素材（未入库 ID）';
  }
  box.appendChild(idEl);

  var disp = savedAssetDisplayText(a);
  if (disp) {
    var metaEl = document.createElement('div');
    metaEl.className = 'chat-generated-asset-meta';
    metaEl.textContent = '内容 · ' + disp;
    box.appendChild(metaEl);
  }

  var mediaWrap = document.createElement('div');
  mediaWrap.className = 'chat-generated-asset-media';

  var loadingEl = document.createElement('div');
  loadingEl.className = 'chat-generated-asset-loading';
  loadingEl.style.cssText = 'font-size:0.8rem;color:var(--text-muted);';
  loadingEl.textContent = assetId ? '正在加载本地预览…' : '正在加载预览…';
  mediaWrap.appendChild(loadingEl);

  var maxH = compact ? '140px' : '200px';
  var maxHVid = compact ? '160px' : '220px';

  function removeLoading() {
    if (loadingEl.parentNode) loadingEl.parentNode.removeChild(loadingEl);
  }

  function appendBlobPreview(blob) {
    removeLoading();
    var t = (blob.type || '').toLowerCase();
    var asVideo = (mediaType === 'video') || /^video\//.test(t) || /\.(mp4|webm|mov|m4v)(\?|$)/i.test(savedAssetPrimaryHttpUrl(a) || '');
    var u = URL.createObjectURL(blob);
    box._blobUrl = u;
    if (asVideo) {
      var v = document.createElement('video');
      v.src = u;
      v.controls = true;
      v.playsInline = true;
      v.preload = 'metadata';
      v.style.cssText = 'width:100%;max-height:' + maxHVid + ';border-radius:6px;background:#000;';
      mediaWrap.appendChild(v);
    } else {
      var img = document.createElement('img');
      img.src = u;
      img.alt = assetId || '素材';
      img.style.cssText = 'width:100%;max-height:' + maxH + ';object-fit:contain;border-radius:6px;display:block;';
      mediaWrap.appendChild(img);
    }
    scrollChatMessagesToBottom();
  }

  function appendHttpPreview(url) {
    removeLoading();
    var u = url;
    var looksVideo = (mediaType === 'video') || /\.(mp4|webm|mov|m4v)(\?|$)/i.test(u);
    if (looksVideo) {
      var v = document.createElement('video');
      v.src = u;
      v.controls = true;
      v.playsInline = true;
      v.preload = 'metadata';
      v.style.cssText = 'width:100%;max-height:' + maxHVid + ';border-radius:6px;background:#000;';
      mediaWrap.appendChild(v);
    } else {
      var img = document.createElement('img');
      img.src = u;
      img.alt = assetId || '素材';
      img.style.cssText = 'width:100%;max-height:' + maxH + ';object-fit:contain;border-radius:6px;display:block;';
      mediaWrap.appendChild(img);
    }
    scrollChatMessagesToBottom();
  }

  function showLoadError(fallbackUrl) {
    removeLoading();
    var err = document.createElement('div');
    err.style.cssText = 'font-size:0.78rem;color:var(--text-muted);margin-bottom:0.25rem;';
    err.textContent = '本地预览不可用';
    mediaWrap.appendChild(err);
    if (fallbackUrl) appendHttpPreview(fallbackUrl);
    else if (savedAssetPrimaryHttpUrl(a)) appendHttpPreview(savedAssetPrimaryHttpUrl(a));
    else scrollChatMessagesToBottom();
  }

  box.appendChild(mediaWrap);
  parent.appendChild(box);

  var base = getLocalApiBaseForAssets();
  if (assetId && base && typeof authHeaders === 'function') {
    fetch(base + '/api/assets/' + encodeURIComponent(assetId) + '/content', { headers: authHeaders() })
      .then(function(r) {
        if (!r.ok) throw new Error('bad');
        return r.blob();
      })
      .then(appendBlobPreview)
      .catch(function() {
        showLoadError(savedAssetPrimaryHttpUrl(a) || '');
      });
  } else {
    var httpUrl = savedAssetPrimaryHttpUrl(a);
    if (httpUrl) {
      appendHttpPreview(httpUrl);
    } else {
      removeLoading();
      var hint = document.createElement('div');
      hint.style.cssText = 'font-size:0.78rem;color:var(--text-muted);';
      hint.textContent = assetId ? '无可用预览（请确认已登录且素材在本机）' : '无预览地址';
      mediaWrap.appendChild(hint);
      scrollChatMessagesToBottom();
    }
  }

  scrollChatMessagesToBottom();
}

function appendChatGeneratedAssetsToTyping(assets) {
  var steps = document.getElementById('chatTypingSteps');
  if (!steps || !assets || !assets.length) return;
  var wrap = document.createElement('div');
  wrap.className = 'chat-generated-assets-preview';
  wrap.style.cssText = 'margin-top:0.5rem;display:flex;flex-wrap:wrap;gap:0.5rem;align-items:flex-start;';
  assets.forEach(function(a) {
    appendSavedAssetDom(wrap, a, { compact: true });
  });
  steps.appendChild(wrap);
  var container = document.getElementById('chatMessages');
  if (container) container.scrollTop = container.scrollHeight;
}
function removeChatTypingIndicator() {
  var el = document.getElementById('chatTypingIndicator');
  if (el && el.parentNode) el.parentNode.removeChild(el);
}
function appendAssistantMessageReveal(fullText, savedAssets) {
  var container = document.getElementById('chatMessages');
  if (!container) return;
  var text = (fullText || '').trim() || '（无内容）';
  var lines = text.split('\n');
  var div = document.createElement('div');
  div.className = 'chat-msg assistant';
  var roleDiv = document.createElement('div');
  roleDiv.className = 'role';
  roleDiv.textContent = '龙虾';
  var bodyDiv = document.createElement('div');
  bodyDiv.className = 'chat-msg-body';
  div.appendChild(roleDiv);
  div.appendChild(bodyDiv);
  if (savedAssets && savedAssets.length) {
    var assetsWrap = document.createElement('div');
    assetsWrap.className = 'chat-generated-assets';
    assetsWrap.style.cssText = 'display:flex;flex-wrap:wrap;gap:0.5rem;margin-bottom:0.75rem;align-items:flex-start;';
    savedAssets.forEach(function(a) {
      appendSavedAssetDom(assetsWrap, a, { compact: false });
    });
    bodyDiv.appendChild(assetsWrap);
  }
  container.appendChild(div);
  var lineDelay = 150;
  var i = 0;
  function showNext() {
    if (i >= lines.length) {
      container.scrollTop = container.scrollHeight;
      return;
    }
    var line = lines[i];
    var lineEl = document.createElement('div');
    lineEl.className = 'chat-msg-line';
    lineEl.innerHTML = linkifyText(line);
    bodyDiv.appendChild(lineEl);
    i++;
    container.scrollTop = container.scrollHeight;
    if (i < lines.length) setTimeout(showNext, lineDelay);
  }
  if (lines.length) setTimeout(showNext, lineDelay); else container.scrollTop = container.scrollHeight;
}
function renderChatAttachments() {
  var container = document.getElementById('chatAttachments');
  if (!container) return;
  if (chatAttachmentIds.length === 0) {
    container.style.display = 'none';
    container.innerHTML = '';
    return;
  }
  container.style.display = 'flex';
  container.innerHTML = '';
  chatAttachmentInfos.forEach(function(info, idx) {
    var wrap = document.createElement('div');
    wrap.className = 'chat-attach-item';
    if (info.media_type === 'video') {
      var v = document.createElement('video');
      v.src = info.previewUrl || '';
      v.muted = true;
      v.playsInline = true;
      wrap.appendChild(v);
    } else {
      var img = document.createElement('img');
      img.src = info.previewUrl || '';
      img.alt = '附件';
      wrap.appendChild(img);
    }
    var rm = document.createElement('button');
    rm.type = 'button';
    rm.className = 'attach-remove';
    rm.textContent = '×';
    rm.setAttribute('data-idx', String(idx));
    rm.addEventListener('click', function() {
      var i = parseInt(rm.getAttribute('data-idx'), 10);
      if (chatAttachmentInfos[i] && chatAttachmentInfos[i].previewUrl) {
        try { URL.revokeObjectURL(chatAttachmentInfos[i].previewUrl); } catch (e) {}
      }
      chatAttachmentIds.splice(i, 1);
      chatAttachmentInfos.splice(i, 1);
      renderChatAttachments();
    });
    wrap.appendChild(rm);
    container.appendChild(wrap);
  });
}
function addChatAttachment(assetId, mediaType) {
  chatAttachmentIds.push(assetId);
  var info = { asset_id: assetId, media_type: mediaType || 'image', previewUrl: '' };
  chatAttachmentInfos.push(info);
  fetch((typeof LOCAL_API_BASE !== 'undefined' ? LOCAL_API_BASE : '') + '/api/assets/' + assetId + '/content', { headers: authHeaders() })
    .then(function(r) { return r.blob(); })
    .then(function(blob) {
      info.previewUrl = URL.createObjectURL(blob);
      renderChatAttachments();
    })
    .catch(function() { renderChatAttachments(); });
}
function _canonicalAssetSaveKey(url) {
  if (!url) return '';
  var s = String(url).split('?')[0].split('#')[0];
  var sl = s.toLowerCase();
  if (sl.indexOf('/v3-tasks/') >= 0) return s;
  if (s.indexOf('/assets/') >= 0) return s;
  return s;
}
function _pendingAssetDedupAttrKey(rawUrl) {
  var k = _canonicalAssetSaveKey(rawUrl);
  return k ? encodeURIComponent(k) : '';
}
function _updateChatAssetDomAfterSaveUrl(attrKey, newAssetId) {
  if (!attrKey || !newAssetId) return;
  try {
    var boxes = document.querySelectorAll('[data-pending-asset-dedup="' + attrKey + '"]');
    for (var i = 0; i < boxes.length; i++) {
      var box = boxes[i];
      var idEl = box.querySelector('.chat-generated-asset-id');
      if (idEl) idEl.textContent = '素材 ID · ' + newAssetId;
      box.removeAttribute('data-pending-asset-dedup');
    }
  } catch (e) {}
}
function saveGeneratedAssetsToLocal(assets, dedupSet) {
  if (!assets || !assets.length) return;
  var base = ((typeof LOCAL_API_BASE !== 'undefined' ? LOCAL_API_BASE : '') || '');
  var headers = Object.assign({ 'Content-Type': 'application/json' }, typeof authHeaders === 'function' ? authHeaders() : { 'Authorization': 'Bearer ' + (typeof token !== 'undefined' ? token : '') });
  var seen = dedupSet || {};
  assets.forEach(function(a) {
    if (!a) return;
    if (a.asset_id) return;
    var rawUrl = (a.url || a.source_url || '').trim();
    if (!rawUrl) return;
    var dedupKey = _canonicalAssetSaveKey(rawUrl);
    if (seen[dedupKey]) return;
    seen[dedupKey] = true;
    var attrKey = _pendingAssetDedupAttrKey(rawUrl);
    var tagStr = (a.tags && String(a.tags).trim()) ? String(a.tags).trim() : 'auto,task.get_result';
    var saveBody = { url: rawUrl, media_type: (a.media_type || 'image'), tags: tagStr };
    if (a.prompt && String(a.prompt).trim()) saveBody.prompt = String(a.prompt).trim().slice(0, 500);
    if (a.model && String(a.model).trim()) saveBody.model = String(a.model).trim().slice(0, 128);
    if (a.generation_task_id && String(a.generation_task_id).trim()) saveBody.generation_task_id = String(a.generation_task_id).trim().slice(0, 128);
    fetch(base + '/api/assets/save-url', {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(saveBody)
    })
      .then(function(r) {
        if (!r.ok) return null;
        return r.json();
      })
      .then(function(d) {
        if (!d || !d.asset_id) return;
        a.asset_id = d.asset_id;
        if (d.source_url) a.source_url = d.source_url;
        if (attrKey) _updateChatAssetDomAfterSaveUrl(attrKey, d.asset_id);
      })
      .catch(function() {});
  });
}
function clearChatAttachments() {
  chatAttachmentInfos.forEach(function(info) {
    if (info.previewUrl) try { URL.revokeObjectURL(info.previewUrl); } catch (e) {}
  });
  chatAttachmentIds = [];
  chatAttachmentInfos = [];
  renderChatAttachments();
}
function sendChatMessage() {
  var input = document.getElementById('chatInput');
  var btn = document.getElementById('chatSendBtn');
  if (!input || !btn) return;
  var message = (input.value || '').trim();
  if (!message && chatAttachmentIds.length === 0) return;
  if (!currentSessionId) {
    if (chatSessions.length) switchChatSession(chatSessions[0].id);
    else createNewSession();
  }
  var sid = String(currentSessionId);
  var session = getSessionById(sid);
  if (!session) return;
  if (isSessionPending(sid)) return;

  var chatBase = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? String(LOCAL_API_BASE).replace(/\/$/, '') : '';
  if (!chatBase) {
    alert('智能对话须连接本机 lobster_online 后端（含 OpenClaw/MCP）。请运行 backend/run.py 后用该后端地址打开页面，或在页面中设置 window.__LOCAL_API_BASE。');
    return;
  }

  input.value = '';
  var attachIds = mergeUserMessageAssetIds(chatAttachmentIds.slice(), message);
  clearChatAttachments();
  session.messages = Array.isArray(session.messages) ? session.messages : [];
  session.messages.push({
    role: 'user',
    content: message,
    attachment_asset_ids: attachIds.length ? attachIds.slice() : undefined
  });
  session.updatedAt = Date.now();
  if (String(currentSessionId) === sid) {
    appendUserMessageDisplay(message, attachIds);
    chatHistory = session.messages.slice();
  }
  saveCurrentSessionToStore();
  renderChatSessionList();
  setSessionPending(sid, true);
  showChatTypingIndicator();
  var historyForRequest = session.messages.slice(0, -1);
  var modelSel = document.getElementById('modelSelect');
  var model = modelSel ? (modelSel.value || '') : '';
  var subSel = document.getElementById('sutuiModelSelect');
  if (model === 'sutui_aggregate') {
    if (!subSel || !subSel.value) {
      alert('请先在「速推 LLM」子下拉中选择对话模型（仅 LLM/text 类，列表由服务器提供）。若列表为空，请稍后再试或联系管理员检查速推 Token。');
      setSessionPending(sid, false);
      if (String(currentSessionId) === sid) removeChatTypingIndicator();
      return;
    }
    model = 'sutui/' + subSel.value;
  }
  var body = {
    message: message,
    history: historyForRequest,
    session_id: sid,
    context_id: null,
    model: model || undefined
  };
  if (attachIds.length) body.attachment_asset_ids = attachIds;
  var bodyStr = JSON.stringify(body);
  var headers = authHeaders();
  headers['Content-Type'] = 'application/json';
  var taskPollingStarted = false;
  var taskPollingCompleted = false;
  var videoGeneratedShown = false;
  var statusReplyStepShown = false;
  var streamGeneratedAssets = [];
  var savedAssetUrls = {};
  var taskPollLocalSaveDone = false;
  var streamAbortedByUser = false;
  chatStreamSutuiSubmitted = false;
  var abortController = new AbortController();
  chatStreamAbortController = abortController;
  refreshChatInputState();
  fetch(chatBase + '/chat/stream', { method: 'POST', headers: headers, body: bodyStr, signal: abortController.signal })
    .then(function(r) {
      if (!r.ok) {
        return r.json().then(function(d) { throw { status: r.status, detail: (d && d.detail) || r.statusText }; });
      }
      if (!r.body) throw new Error('No body');
      var decoder = new TextDecoder();
      var buf = '';
      var reader = r.body.getReader();
      function processChunk(result) {
        if (result.done) return Promise.resolve(null);
        buf += decoder.decode(result.value, { stream: true });
        var parts = buf.split('\n\n');
        buf = parts.pop() || '';
        for (var i = 0; i < parts.length; i++) {
          var block = parts[i];
          var dataLine = block.split('\n').filter(function(l) { return l.indexOf('data:') === 0; })[0];
          if (!dataLine) continue;
          try {
            var ev = JSON.parse(dataLine.slice(5).trim());
            if (ev.type === 'tool_start' && String(currentSessionId) === sid) {
              if (ev.name === 'list_capabilities') {
                appendChatTypingStep('正在查询可用能力…');
              } else if (ev.phase === 'video_submit') {
                appendChatTypingStep('正在提交视频生成任务…');
              } else if (ev.phase === 'image_submit') {
                appendChatTypingStep('正在提交图片生成任务…');
              } else if (ev.phase === 'task_polling') {
                chatStreamSutuiSubmitted = true;
                refreshChatInputState();
                setChatTypingMainText('正在查询生成结果…');
                if (!taskPollingStarted) {
                  appendChatTypingStep('正在查询生成结果…（每 15 秒自动查询一次）');
                  taskPollingStarted = true;
                }
              } else {
                appendChatTypingStep('正在 ' + _toolLabel(ev.name) + '…');
              }
            } else if (ev.type === 'tool_end' && String(currentSessionId) === sid) {
              if ((ev.phase === 'image_submit' || ev.phase === 'video_submit') && ev.success !== false) {
                chatStreamSutuiSubmitted = true;
                refreshChatInputState();
              }
              if (ev.phase === 'video_submit') {
                if (ev.success === false) {
                  var failPrev = (ev.preview || '').trim();
                  updateLastChatTypingStep(
                    failPrev ? ('✗ 提交未成功：' + (failPrev.length > 140 ? failPrev.slice(0, 140) + '…' : failPrev))
                      : '✗ 任务提交失败，请查看下方回复'
                  );
                } else {
                  updateLastChatTypingStep('✓ 任务已提交成功，正在查询生成结果…');
                }
              } else if (ev.phase === 'image_submit') {
                if (ev.success === false) {
                  var failPrevImg = (ev.preview || '').trim();
                  updateLastChatTypingStep(
                    failPrevImg ? ('✗ 提交未成功：' + (failPrevImg.length > 140 ? failPrevImg.slice(0, 140) + '…' : failPrevImg))
                      : '✗ 任务提交失败，请查看下方回复'
                  );
                } else {
                  if (ev.saved_assets && ev.saved_assets.length) {
                    streamGeneratedAssets = ev.saved_assets.slice();
                    saveGeneratedAssetsToLocal(streamGeneratedAssets, savedAssetUrls);
                    updateLastChatTypingStep('✓ 素材已生成');
                  } else {
                    updateLastChatTypingStep('✓ 任务已提交成功，正在查询生成结果…');
                  }
                }
              } else if (ev.phase === 'task_polling') {
                var stillInProgress = ev.in_progress === true;
                if (!stillInProgress) {
                  taskPollingCompleted = true;
                  if (ev.saved_assets && ev.saved_assets.length) streamGeneratedAssets = ev.saved_assets;
                  if (streamGeneratedAssets.length && !taskPollLocalSaveDone) {
                    taskPollLocalSaveDone = true;
                    saveGeneratedAssetsToLocal(streamGeneratedAssets, savedAssetUrls);
                  }
                  if (!videoGeneratedShown) {
                    updateLastChatTypingStep('✓ 素材已生成');
                    videoGeneratedShown = true;
                  }
                  if (streamGeneratedAssets.length) appendChatGeneratedAssetsToTyping(streamGeneratedAssets);
                  setChatTypingMainText('正在请模型撰写回复…');
                } else if (!taskPollingCompleted) {
                  updateLastChatTypingStep('正在查询生成结果…');
                }
              } else if (ev.name === 'list_capabilities') {
                updateLastChatTypingStep('✓ 能力列表已获取');
              } else {
                if (ev.success !== false && ev.saved_assets && ev.saved_assets.length) {
                  streamGeneratedAssets = ev.saved_assets.slice();
                  saveGeneratedAssetsToLocal(streamGeneratedAssets, savedAssetUrls);
                  appendChatGeneratedAssetsToTyping(streamGeneratedAssets);
                  updateLastChatTypingStep('✓ 素材已生成');
                  setChatTypingMainText('正在请模型撰写回复…');
                } else {
                  updateLastChatTypingStep('✓ ' + _toolLabel(ev.name) + ' 完成');
                }
              }
            } else if (ev.type === 'task_poll' && String(currentSessionId) === sid && ev.message) {
              if (taskPollingCompleted) continue;
              var sec = _extractPollSeconds(ev.message);
              var line = sec ? ('正在查询生成结果…（' + sec + '秒）') : '正在查询生成结果…';
              if (ev.result_hint) line += ' · ' + ev.result_hint;
              else if (ev.task_id) line += ' · task_id: ' + ev.task_id;
              setChatTypingMainText(line);
            } else if (ev.type === 'status' && String(currentSessionId) === sid && ev.message) {
              if ((ev.message === '正在请模型撰写回复…' || ev.message === '正在生成回复…') && statusReplyStepShown) continue;
              if (ev.message === '正在请模型撰写回复…' || ev.message === '正在生成回复…') statusReplyStepShown = true;
              appendChatTypingStep(ev.message);
            } else if (ev.type === 'done') {
              return Promise.resolve(ev);
            }
          } catch (e) {}
        }
        return reader.read().then(processChunk);
      }
      return reader.read().then(processChunk);
    })
    .then(function(doneEv) {
      var targetSession = getSessionById(sid);
      if (!targetSession) return;
      if (String(currentSessionId) === sid) removeChatTypingIndicator();
      var reply = (doneEv && doneEv.reply) ? doneEv.reply : (doneEv ? '' : '请求异常结束');
      targetSession.messages = Array.isArray(targetSession.messages) ? targetSession.messages : [];
      targetSession.messages.push({
        role: 'assistant',
        content: reply,
        saved_assets: streamGeneratedAssets && streamGeneratedAssets.length ? streamGeneratedAssets : undefined
      });
      targetSession.updatedAt = Date.now();
      if (String(currentSessionId) === sid) {
        appendAssistantMessageReveal(reply, streamGeneratedAssets);
        chatHistory = targetSession.messages.slice();
      }
      saveChatSessionsToStorage();
    })
    .catch(function(e) {
      if (e && e.name === 'AbortError') {
        streamAbortedByUser = true;
        setSessionPending(sid, false);
        if (String(currentSessionId) === sid) {
          setChatTypingMainText('已取消');
          appendChatTypingStep('已终止当前任务，可重新发送消息继续');
          setTimeout(removeChatTypingIndicator, 1500);
        }
        saveChatSessionsToStorage();
        return;
      }
      var targetSession = getSessionById(sid);
      var raw = (e && e.detail) ? e.detail : (e && e.message ? e.message : '请稍后重试');
      var msg = (raw === 'Failed to fetch' || (typeof raw === 'string' && raw.toLowerCase().indexOf('failed to fetch') >= 0))
        ? '网络连接失败，请检查后端是否已启动或网络是否正常'
        : raw;
      if (targetSession) {
        targetSession.messages = Array.isArray(targetSession.messages) ? targetSession.messages : [];
        targetSession.messages.push({ role: 'assistant', content: '错误：' + msg });
        targetSession.updatedAt = Date.now();
      }
      if (String(currentSessionId) === sid) {
        removeChatTypingIndicator();
        appendChatMessage('assistant', '错误：' + msg);
        if (targetSession) chatHistory = targetSession.messages.slice();
      }
      saveChatSessionsToStorage();
    })
    .finally(function() {
      chatStreamAbortController = null;
      chatStreamSutuiSubmitted = false;
      refreshChatInputState();
      setSessionPending(sid, false);
      if (!streamAbortedByUser && String(currentSessionId) === sid) removeChatTypingIndicator();
    });
}
var chatSendBtn = document.getElementById('chatSendBtn');
var chatInput = document.getElementById('chatInput');
var chatAttachBtn = document.getElementById('chatAttachBtn');
var chatFileInput = document.getElementById('chatFileInput');
if (chatSendBtn) chatSendBtn.addEventListener('click', sendChatMessage);
var chatCancelBtn = document.getElementById('chatCancelBtn');
if (chatCancelBtn) {
  chatCancelBtn.addEventListener('click', function() {
    if (!chatStreamAbortController) return;
    if (chatStreamSutuiSubmitted) {
      alert('任务已在速推生成中，无法取消。请等待生成完成。');
      return;
    }
    try { chatStreamAbortController.abort(); } catch (err) {}
  });
}
if (chatAttachBtn && chatFileInput) {
  chatAttachBtn.addEventListener('click', function() { chatFileInput.click(); });
  chatFileInput.addEventListener('change', function() {
    var files = chatFileInput.files;
    if (!files || !files.length) return;
    for (var i = 0; i < files.length; i++) {
      (function(file) {
        var fd = new FormData();
        fd.append('file', file);
        fetch((typeof LOCAL_API_BASE !== 'undefined' ? LOCAL_API_BASE : '') + '/api/assets/upload', { method: 'POST', headers: { 'Authorization': 'Bearer ' + (typeof token !== 'undefined' ? token : '') }, body: fd })
          .then(function(r) { return r.json(); })
          .then(function(d) {
            if (d && d.asset_id) addChatAttachment(d.asset_id, d.media_type || 'image');
          })
          .catch(function() {});
      })(files[i]);
    }
    chatFileInput.value = '';
  });
}
if (chatInput) {
  var chatInputComposing = false;
  chatInput.addEventListener('compositionstart', function() { chatInputComposing = true; });
  chatInput.addEventListener('compositionend', function() { chatInputComposing = false; });
  chatInput.addEventListener('keydown', function(e) {
    if (chatInputComposing || e.isComposing || e.keyCode === 229) return;
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatMessage(); }
  });
}
var chatNewSessionBtn = document.getElementById('chatNewSessionBtn');
if (chatNewSessionBtn) chatNewSessionBtn.addEventListener('click', createNewSession);
var chatSessionSearch = document.getElementById('chatSessionSearch');
if (chatSessionSearch) chatSessionSearch.addEventListener('input', renderChatSessionList);
