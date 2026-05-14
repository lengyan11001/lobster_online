/** 旧版全局键；升级后首次登录会迁移到 lobster_chat_sessions_u{userId} 并删除 */
var LEGACY_CHAT_SESSIONS_KEY = 'lobster_chat_sessions';
/** 刷新后可续查 task_poll 的最大存活时间（须早于本文件内任意使用它的函数）；过长会导致「每次打开页面都续查旧任务」 */
var _POLL_RESUME_MAX_AGE_MS = 3600000;
/** 刷新续查专用 /chat/stream：超过此时长主动断开，避免「约每 15 秒更新」永远结束不了（后端轮询最长约 40min） */
var _RESUME_CHAT_STREAM_MAX_MS = 50 * 60 * 1000;

/**
 * 是否在刷新/切换会话时自动请求「恢复轮询」(resume_task_poll_task_id)。
 * 默认关闭：否则 localStorage 里遗留的 poll_resume 会让每次打开页面都看到「正在恢复并查询…」并打 /chat/stream。
 * 需要自动续查：localStorage.setItem('lobster_chat_auto_resume_poll','1') 后刷新；或 window.__LOBSTER_CHAT_AUTO_RESUME_POLL = true
 */
function chatAutoResumePollEnabled() {
  try {
    if (typeof window !== 'undefined' && window.__LOBSTER_CHAT_AUTO_RESUME_POLL === true) return true;
    return localStorage.getItem('lobster_chat_auto_resume_poll') === '1';
  } catch (e) {
    return false;
  }
}

function getChatSessionsStorageKey() {
  var uid = '';
  if (typeof window.__currentUserId !== 'undefined' && window.__currentUserId != null) {
    uid = String(window.__currentUserId);
  }
  if (!uid && typeof window.getCurrentUserIdFromToken === 'function') {
    uid = window.getCurrentUserIdFromToken();
  }
  // 无用户 ID 时仍落盘，否则刷新后无法恢复 poll_resume（本地/未注入 __currentUserId 场景）
  return uid ? ('lobster_chat_sessions_u' + uid) : 'lobster_chat_sessions_anon';
}

/** 最后打开的会话 id，刷新后优先恢复（与 poll_resume 会话可能不同） */
function getChatLastSessionStorageKey() {
  var sk = getChatSessionsStorageKey();
  if (!sk) return '';
  return sk.replace(/^lobster_chat_sessions/, 'lobster_chat_last_session');
}

var CHAT_MODE_DEFAULT = 'default';
var CHAT_MODE_WORKSPACE = 'workspace_cli';
// 云端工作台入口先在前端关闭；后端能力保留，后续需要时改这里即可恢复入口。
var CHAT_WORKSPACE_ENTRY_ENABLED = false;
var CHAT_MEMORY_SCOPE_DEFAULT = 'default';
var CHAT_MEMORY_SCOPE_PERSONAL = 'personal';
var CHAT_MEMORY_SCOPE_SYSTEM = 'system';
var CHAT_MEMORY_SCOPE_NONE = 'none';
var CHAT_MEMORY_SCOPE_CONFIG = {
  default: { label: '默认记忆', badge: '' },
  personal: { label: '个人记忆', badge: '个人' },
  system: { label: '系统记忆', badge: '系统' },
  none: { label: '不使用资料', badge: '无记忆' }
};
var WORKSPACE_CATEGORY_DEFAULT = 'web_app';
var WORKSPACE_CATEGORY_CONFIG = {
  web_app: {
    title: '要我在网页应用里帮您处理什么？',
    subtitle: '这里适合创建网页应用、继续开发、预览部署、总结网页、输出文档，以及上传图片和文档后继续处理。',
    placeholder: '描述您想做的网页应用、网页改版、网页总结或网页生成任务',
    hint: '云端工作台模式：默认聚焦网页应用，可继续开发、部署、总结网页、输出文档和处理附件。',
    chips: [
      ['创建网页', '帮我生成一个网站：'],
      ['继续开发', '继续帮我开发这个网站：'],
      ['总结网页', '帮我总结网页，网页地址是：'],
      ['输出文档', '帮我输出一份文档，主题是：'],
      ['上传图片', '我会上传图片，请结合这个网站需求处理：'],
      ['生成页面图', '帮我生成一版网站页面图，需求是：']
    ],
    shortcuts: [
      ['网页结构', '帮我梳理这个网站的页面结构：'],
      ['部署发布', '帮我部署这个网站：'],
      ['查看文档', '帮我整理这个网站的文档：']
    ]
  },
  mobile_app: {
    title: '要我在移动应用里帮您处理什么？',
    subtitle: '这里适合创建移动应用、梳理页面流、继续改功能、输出 PRD 和交互说明，也能结合附件继续开发。',
    placeholder: '描述您想做的移动应用、页面流程、功能开发或文档任务',
    hint: '云端工作台模式：移动应用可直接做页面规划、功能拆解、继续开发和文档输出。',
    chips: [
      ['创建应用', '帮我生成一个移动应用：'],
      ['页面流程', '帮我梳理这个移动应用的页面流程：'],
      ['继续开发', '继续帮我开发这个移动应用：'],
      ['输出文档', '帮我输出一份移动应用文档：'],
      ['上传图片', '我会上传移动端参考图，请结合这个需求处理：'],
      ['生成界面图', '帮我生成一版移动应用界面图：']
    ],
    shortcuts: [
      ['功能拆解', '帮我拆解这个移动应用的功能：'],
      ['测试清单', '帮我整理这个移动应用的测试清单：'],
      ['查看文档', '帮我整理这个移动应用的文档：']
    ]
  },
  mini_program: {
    title: '要我在小程序里帮您处理什么？',
    subtitle: '这里适合搭建小程序、梳理页面与能力边界、输出开发文档、生成视觉素材，并结合上传内容继续推进。',
    placeholder: '描述您想做的小程序功能、页面、开发任务或输出需求',
    hint: '云端工作台模式：小程序支持从需求梳理、页面设计到继续开发和文档输出。',
    chips: [
      ['创建小程序', '帮我生成一个小程序：'],
      ['页面规划', '帮我规划这个小程序的页面：'],
      ['继续开发', '继续帮我开发这个小程序：'],
      ['输出文档', '帮我输出一份小程序文档：'],
      ['上传文档', '我会上传小程序文档，请结合这个需求处理：'],
      ['上传图片', '我会上传小程序参考图，请结合这个需求处理：']
    ],
    shortcuts: [
      ['能力边界', '帮我梳理这个小程序的能力边界：'],
      ['上线准备', '帮我整理这个小程序的上线准备：'],
      ['查看文档', '帮我整理这个小程序的文档：']
    ]
  },
  agent: {
    title: '要我在智能体里帮您处理什么？',
    subtitle: '这里适合设计智能体能力、对话流程、工具接入、任务拆解，以及继续完善智能体的执行逻辑。',
    placeholder: '描述您想做的智能体目标、能力设计、工具接入或对话流程任务',
    hint: '云端工作台模式：智能体支持能力设计、对话流程梳理、工具接入和持续迭代。',
    chips: [
      ['创建智能体', '帮我生成一个智能体：'],
      ['对话流程', '帮我设计这个智能体的对话流程：'],
      ['接入能力', '帮我给这个智能体接入能力：'],
      ['继续优化', '继续帮我优化这个智能体：'],
      ['输出文档', '帮我输出一份智能体文档：'],
      ['上传资料', '我会上传智能体资料，请结合这个需求处理：']
    ],
    shortcuts: [
      ['能力清单', '帮我梳理这个智能体的能力清单：'],
      ['异常处理', '帮我设计这个智能体的异常处理：'],
      ['查看文档', '帮我整理这个智能体的文档：']
    ]
  },
  skills: {
    title: '要我在技能里帮您处理什么？',
    subtitle: '这里适合设计技能结构、接入外部能力、整理参数说明、生成使用文档，也适合结合附件继续配置和迭代。',
    placeholder: '描述您想新增、配置或优化的技能能力和使用场景',
    hint: '云端工作台模式：技能支持能力设计、参数整理、文档生成和后续迭代。',
    chips: [
      ['创建技能', '帮我生成一个技能：'],
      ['参数设计', '帮我设计这个技能的参数：'],
      ['接入能力', '帮我给这个技能接入能力：'],
      ['继续优化', '继续帮我优化这个技能：'],
      ['输出文档', '帮我输出一份技能文档：'],
      ['上传资料', '我会上传技能资料，请结合这个需求处理：']
    ],
    shortcuts: [
      ['技能说明', '帮我整理这个技能的说明：'],
      ['能力边界', '帮我梳理这个技能的能力边界：'],
      ['查看文档', '帮我整理这个技能的文档：']
    ]
  }
};

function _normalizeChatMode(mode) {
  if (String(mode || '').trim() === CHAT_MODE_WORKSPACE && CHAT_WORKSPACE_ENTRY_ENABLED) {
    return CHAT_MODE_WORKSPACE;
  }
  return CHAT_MODE_DEFAULT;
}

function _chatModeStorageKey() {
  var sk = getChatSessionsStorageKey();
  return sk ? (sk + '_mode') : 'lobster_chat_mode_anon';
}

function _getStoredChatMode() {
  try {
    return _normalizeChatMode(localStorage.getItem(_chatModeStorageKey()) || CHAT_MODE_DEFAULT);
  } catch (e) {
    return CHAT_MODE_DEFAULT;
  }
}

function _saveStoredChatMode(mode) {
  try {
    localStorage.setItem(_chatModeStorageKey(), _normalizeChatMode(mode));
  } catch (e) {}
}

function _getSessionMode(session) {
  return _normalizeChatMode(session && session.mode);
}

function _isWorkspaceSession(session) {
  return _getSessionMode(session) === CHAT_MODE_WORKSPACE;
}

function _normalizeChatMemoryScope(scope) {
  var key = String(scope || '').trim();
  return CHAT_MEMORY_SCOPE_CONFIG[key] ? key : CHAT_MEMORY_SCOPE_DEFAULT;
}

function _getSessionMemoryScope(session) {
  if (!session || typeof session !== 'object') return CHAT_MEMORY_SCOPE_DEFAULT;
  return _normalizeChatMemoryScope(session.chat_memory_scope);
}

function _getSessionMemoryBadge(session) {
  var cfg = CHAT_MEMORY_SCOPE_CONFIG[_getSessionMemoryScope(session)] || CHAT_MEMORY_SCOPE_CONFIG.default;
  return cfg.badge || '';
}

function _getWorkspaceCategory(session) {
  var v = session && session.workspace_category;
  return WORKSPACE_CATEGORY_CONFIG[String(v || '').trim()] ? String(v).trim() : WORKSPACE_CATEGORY_DEFAULT;
}

function saveLastActiveChatSessionToStorage(sid) {
  try {
    var k = getChatLastSessionStorageKey();
    if (!k || !sid) return;
    localStorage.setItem(k, String(sid));
  } catch (e) {}
}

function getLastActiveChatSessionIdFromStorage() {
  try {
    var k = getChatLastSessionStorageKey();
    if (!k) return '';
    return String(localStorage.getItem(k) || '').trim();
  } catch (e) {
    return '';
  }
}

function getChatLastSessionByModeStorageKey(mode) {
  var k = getChatLastSessionStorageKey();
  if (!k) return '';
  return k + '_' + _normalizeChatMode(mode);
}

function saveLastActiveChatSessionByModeToStorage(sid, mode) {
  try {
    var k = getChatLastSessionByModeStorageKey(mode);
    if (!k || !sid) return;
    localStorage.setItem(k, String(sid));
  } catch (e) {}
}

function getLastActiveChatSessionIdByModeFromStorage(mode) {
  try {
    var k = getChatLastSessionByModeStorageKey(mode);
    if (!k) return '';
    return String(localStorage.getItem(k) || '').trim();
  } catch (e) {
    return '';
  }
}

function findLastChatSessionIdByMode(mode, excludeSid) {
  var normalized = _normalizeChatMode(mode);
  var exclude = excludeSid != null ? String(excludeSid) : '';
  var stored = getLastActiveChatSessionIdByModeFromStorage(normalized);
  if (stored && stored !== exclude) {
    var storedSession = getSessionById(stored);
    if (storedSession && _getSessionMode(storedSession) === normalized) return stored;
  }
  var best = null;
  chatSessions.forEach(function(s) {
    if (!s || String(s.id) === exclude || _getSessionMode(s) !== normalized) return;
    if (!best || Number(s.updatedAt || 0) > Number(best.updatedAt || 0)) best = s;
  });
  return best && best.id != null ? String(best.id) : '';
}

/** 流式 /chat/stream 根地址：与发送消息一致，缺省时用当前页 origin（同源部署） */
function _chatStreamApiBase() {
  if (typeof EDITION !== 'undefined' && EDITION === 'online') {
    var publicRemote = (typeof LOBSTER_SERVER_PUBLIC !== 'undefined' && LOBSTER_SERVER_PUBLIC)
      ? String(LOBSTER_SERVER_PUBLIC).replace(/\/$/, '')
      : '';
    if (publicRemote) return publicRemote;
    var remote = (typeof API_BASE !== 'undefined' && API_BASE) ? String(API_BASE).replace(/\/$/, '') : '';
    if (remote) return remote;
  }
  var b = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? String(LOCAL_API_BASE).replace(/\/$/, '') : '';
  if (b) return b;
  if (typeof window !== 'undefined' && window.location && window.location.origin) {
    return String(window.location.origin).replace(/\/$/, '');
  }
  return '';
}

function _rawChatStreamError(e) {
  if (!e) return '';
  if (e.detail != null && e.detail !== '') return String(e.detail);
  if (e.message) return String(e.message);
  return '';
}
function _normalizeChatStreamErrorMessage(raw) {
  var s = String(raw || '').trim();
  if (s.indexOf('错误：') === 0) s = s.slice(3).trim();
  var low = s.toLowerCase();
  if (!s) return '请稍后重试';
  if (low === 'failed to fetch' || low.indexOf('failed to fetch') >= 0)
    return '网络连接失败，请检查后端是否已启动或网络是否正常';
  if (low === 'network error' || low.indexOf('network error') >= 0)
    return '网络连接失败，请检查后端是否已启动或网络是否正常';
  if (low.indexOf('networkerror') >= 0)
    return '网络连接失败，请检查后端是否已启动或网络是否正常';
  if (low.indexOf('load failed') >= 0)
    return '网络连接失败，请检查后端是否已启动或网络是否正常';
  /* httpx.RemoteProtocolError 常见英文：对端在未发完 HTTP 时断开（OpenClaw/MCP/速推/网关） */
  if (low.indexOf('server disconnected without sending a response') >= 0) {
    return (
      '上游连接被异常关闭（未返回完整 HTTP 响应），常见于网关或速推、OpenClaw、MCP 重启、代理超时或网络抖动。' +
      '若进度里已出现「✓ 素材已生成」，请到素材库用对应素材 ID 查看；也可刷新后续查或重新发送消息重试。'
    );
  }
  return s;
}
/** 将 done 事件里「错误：…」中的已知英文技术句替换为中文（兼容未重启的旧后端） */
function _normalizeAssistantStreamReply(reply) {
  var r = String(reply || '');
  var t = r.trim();
  if (!t) return r;
  if (t.indexOf('错误：') === 0) {
    var inner = t.slice(3).trim();
    var norm = _normalizeChatStreamErrorMessage(inner);
    if (norm !== inner) return '错误：' + norm;
    return r;
  }
  var norm2 = _normalizeChatStreamErrorMessage(t);
  if (norm2 !== t) return norm2;
  return r;
}
/** 刷新续查 /chat/stream 失败时：不写入历史、保留 poll_resume，避免每次 F5 多一条重复错误 */
function _isTransientResumeStreamFailure(e, rawStr, normalizedMsg) {
  if (e && e.name === 'AbortError') return false;
  if (e && e.name === 'TypeError') return true;
  var st = e && typeof e.status === 'number' ? e.status : NaN;
  if (st === 502 || st === 503 || st === 504) return true;
  var nm = String(normalizedMsg || '');
  if (nm.indexOf('网络连接失败') === 0) return true;
  var raw = String(rawStr || '').toLowerCase();
  if (raw.indexOf('network error') >= 0 || raw.indexOf('failed to fetch') >= 0) return true;
  return false;
}
function _pushAssistantErrorIfNotDuplicate(targetSession, msg) {
  if (!targetSession) return false;
  var line = '错误：' + msg;
  targetSession.messages = Array.isArray(targetSession.messages) ? targetSession.messages : [];
  var last = targetSession.messages.length ? targetSession.messages[targetSession.messages.length - 1] : null;
  if (last && last.role === 'assistant' && String(last.content || '') === line) return false;
  targetSession.messages.push({ role: 'assistant', content: line });
  targetSession.updatedAt = Date.now();
  return true;
}
/** 去掉末尾连续重复的助手错误行（修复历史里已堆积的「错误：network error」） */
function _pruneTrailingDuplicateAssistantErrors(session) {
  var msgs = session && session.messages;
  if (!msgs || msgs.length < 2) return false;
  var changed = false;
  while (msgs.length >= 2) {
    var a = msgs[msgs.length - 1];
    var b = msgs[msgs.length - 2];
    if (!a || !b || a.role !== 'assistant' || b.role !== 'assistant') break;
    var ca = String(a.content || '').trim();
    var cb = String(b.content || '').trim();
    if (ca.indexOf('错误：') !== 0 || cb.indexOf('错误：') !== 0) break;
    if (ca.toLowerCase() !== cb.toLowerCase()) break;
    msgs.pop();
    changed = true;
  }
  return changed;
}

/**
 * 选一个需要续查轮询的会话：优先 preferSid（若其 poll_resume 仍有效）；
 * pickAny 为 true 时（仅页面初始化）：当前会话无续查任务则取 poll_resume_at 最新的会话（用于 F5 恢复）。
 * pickAny 为 false 时：禁止因其它会话有 poll_resume 而自动切换会话（避免切换左侧导航反复触发恢复）。
 */
function _pickSessionIdNeedingPollResume(preferSid, pickAny) {
  var now = Date.now();
  var pref = String(preferSid || '').trim();
  if (pref) {
    var ps = getSessionById(pref);
    if (ps) {
      var ptid = (ps.poll_resume_task_id || '').trim();
      if (ptid && (now - (ps.poll_resume_at || 0)) <= _POLL_RESUME_MAX_AGE_MS) {
        return pref;
      }
    }
  }
  if (!pickAny) {
    return null;
  }
  var bestSid = '';
  var bestAt = -1;
  for (var i = 0; i < chatSessions.length; i++) {
    var s = chatSessions[i];
    var tid = (s.poll_resume_task_id || '').trim();
    if (!tid) continue;
    var age = now - (s.poll_resume_at || 0);
    if (age > _POLL_RESUME_MAX_AGE_MS) continue;
    var at = Number(s.poll_resume_at) || 0;
    if (at > bestAt) {
      bestAt = at;
      bestSid = String(s.id);
    }
  }
  return bestSid || null;
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
  try {
    var k = getChatLastSessionStorageKey();
    if (k) localStorage.removeItem(k);
    [CHAT_MODE_DEFAULT, CHAT_MODE_WORKSPACE].forEach(function(mode) {
      var mk = getChatLastSessionByModeStorageKey(mode);
      if (mk) localStorage.removeItem(mk);
    });
  } catch (e) {}
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
/** 中止进行中的 /chat/stream，防止新请求覆盖 controller 后旧流的 finally 把全局置空、导致无法取消或 pending 错乱 */
function abortActiveChatStream() {
  var c = chatStreamAbortController;
  if (!c) return;
  try {
    c.abort();
  } catch (e) {}
  chatStreamAbortController = null;
}
var chatAttachmentIds = [];
var chatAttachmentInfos = [];

var H5_MIRROR_SESSION_ID = 'h5_remote_messages';
var H5_MIRROR_SYNC_INTERVAL_MS = 30000;
var h5MirrorSyncTimer = null;
var h5MirrorSyncInFlight = false;

function _isH5MirrorSession(session) {
  return !!(session && session.kind === 'h5_remote_mirror');
}

function _h5MirrorTitle() {
  return 'H5 \u6d88\u606f';
}

function _h5MirrorWaitingText() {
  return '\u5df2\u540c\u6b65\uff0c\u7b49\u5f85\u672c\u5730\u8bbe\u5907\u5904\u7406...';
}

function _h5MirrorErrorText(message) {
  return 'H5 \u6d88\u606f\u540c\u6b65\u5931\u8d25\uff1a' + (message || '\u672a\u77e5\u9519\u8bef');
}

function _h5EventLabel(ev) {
  if (!ev) return '';
  var payload = ev.payload || {};
  if (ev.type === 'queued') return '\u5df2\u8fdb\u5165\u4e91\u7aef\u961f\u5217';
  if (ev.type === 'claimed') return '\u672c\u5730\u8bbe\u5907\u5df2\u9886\u53d6';
  if (ev.type === 'thinking') return payload.text || '\u6b63\u5728\u5904\u7406';
  if (ev.type === 'tool_start') return payload.name ? '\u8c03\u7528\u80fd\u529b\uff1a' + payload.name : '\u8c03\u7528\u80fd\u529b';
  if (ev.type === 'tool_end') return payload.name ? '\u80fd\u529b\u5b8c\u6210\uff1a' + payload.name : '\u80fd\u529b\u5b8c\u6210';
  if (ev.type === 'progress') return payload.text || payload.message || '\u5904\u7406\u4e2d';
  return '';
}

function _h5DeltaText(events) {
  return (events || []).map(function(ev) {
    if (!ev || ev.type !== 'delta' || !ev.payload) return '';
    return ev.payload.text || '';
  }).join('');
}

function _h5EventFinalText(events) {
  for (var i = (events || []).length - 1; i >= 0; i--) {
    var ev = events[i] || {};
    var payload = ev.payload || {};
    if (ev.type === 'final') return payload.reply_text || payload.text || '';
    if (ev.type === 'error') return payload.error || payload.detail || '';
  }
  return '';
}

function _h5MirrorAssistantText(msg, events) {
  msg = msg || {};
  events = Array.isArray(events) ? events : [];
  var finalText = _h5EventFinalText(events);
  if (msg.status === 'completed') return msg.reply_text || finalText || '\u5904\u7406\u5b8c\u6210\u3002';
  if (msg.status === 'failed') return '\u5904\u7406\u5931\u8d25\uff1a' + (msg.error || finalText || '\u672a\u77e5\u9519\u8bef');
  if (msg.status === 'cancelled') return '\u5df2\u53d6\u6d88';
  var delta = _h5DeltaText(events).trim();
  if (delta) return delta;
  var labels = [];
  events.forEach(function(ev) {
    var label = _h5EventLabel(ev);
    if (label) labels.push(label);
  });
  if (labels.length) return labels.map(function(label) { return '\u00b7 ' + label; }).join('\n');
  return _h5MirrorWaitingText();
}

function _h5ItemsToChatMessages(items) {
  var out = [];
  (Array.isArray(items) ? items : []).forEach(function(item) {
    var msg = (item && item.message) || {};
    if (!msg.id && !msg.content) return;
    var events = Array.isArray(item.events) ? item.events : [];
    out.push({
      role: 'user',
      content: msg.content || '',
      h5_message_id: msg.id || '',
      h5_status: msg.status || ''
    });
    out.push({
      role: 'assistant',
      content: _h5MirrorAssistantText(msg, events),
      h5_message_id: msg.id || '',
      h5_status: msg.status || '',
      h5_mirror: true
    });
  });
  return out;
}

function _h5LatestTime(items) {
  var latest = 0;
  (Array.isArray(items) ? items : []).forEach(function(item) {
    var msg = (item && item.message) || {};
    [msg.updated_at, msg.finished_at, msg.created_at].forEach(function(v) {
      var t = v ? Date.parse(v) : 0;
      if (t && t > latest) latest = t;
    });
  });
  return latest || Date.now();
}

function getOrCreateH5MirrorSession() {
  var session = getSessionById(H5_MIRROR_SESSION_ID);
  if (session) {
    session.kind = 'h5_remote_mirror';
    session.title = _h5MirrorTitle();
    session.mode = CHAT_MODE_DEFAULT;
    session.pending = false;
    return session;
  }
  session = {
    id: H5_MIRROR_SESSION_ID,
    title: _h5MirrorTitle(),
    messages: [],
    updatedAt: Date.now(),
    pending: false,
    mode: CHAT_MODE_DEFAULT,
    kind: 'h5_remote_mirror',
    readonly: true
  };
  chatSessions.unshift(session);
  saveChatSessionsToStorage();
  return session;
}

function _applyH5MirrorMessages(messages, updatedAt) {
  var session = getOrCreateH5MirrorSession();
  session.messages = Array.isArray(messages) ? messages : [];
  session.updatedAt = updatedAt || Date.now();
  session.pending = false;
  session.title = _h5MirrorTitle();
  if (String(currentSessionId || '') === H5_MIRROR_SESSION_ID) {
    chatHistory = session.messages.slice();
    renderCurrentSessionMessages();
  }
  saveChatSessionsToStorage();
  renderChatSessionList();
}

function _setH5MirrorSyncError(message) {
  _applyH5MirrorMessages([{ role: 'assistant', content: _h5MirrorErrorText(message), h5_mirror: true }], Date.now());
}

function syncH5ChatMirrorSession(force) {
  if (h5MirrorSyncInFlight && !force) return Promise.resolve(false);
  var base = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? String(LOCAL_API_BASE).replace(/\/$/, '') : '';
  if (!base) {
    _setH5MirrorSyncError('\u672c\u5730\u540e\u7aef\u672a\u8fde\u63a5');
    return Promise.resolve(false);
  }
  h5MirrorSyncInFlight = true;
  var headers = typeof authHeaders === 'function' ? authHeaders() : {};
  return fetch(base + '/api/h5-chat/messages?limit=40', { headers: headers })
    .then(function(r) {
      return r.text().then(function(text) {
        var data = {};
        try { data = text ? JSON.parse(text) : {}; } catch (e) {}
        if (!r.ok) throw new Error((data && (data.detail || data.message)) || text || ('HTTP ' + r.status));
        return data;
      });
    })
    .then(function(data) {
      var items = Array.isArray(data && data.messages) ? data.messages : [];
      _applyH5MirrorMessages(_h5ItemsToChatMessages(items), _h5LatestTime(items));
      return true;
    })
    .catch(function(err) {
      _setH5MirrorSyncError(err && err.message ? err.message : String(err || 'sync failed'));
      return false;
    })
    .finally(function() {
      h5MirrorSyncInFlight = false;
    });
}

function _startH5MirrorAutoSync() {
  if (h5MirrorSyncTimer) return;
  h5MirrorSyncTimer = setInterval(function() {
    if (_isH5MirrorSession(getSessionById(currentSessionId))) syncH5ChatMirrorSession(false);
  }, H5_MIRROR_SYNC_INTERVAL_MS);
}

function _stopH5MirrorAutoSync() {
  if (!h5MirrorSyncTimer) return;
  clearInterval(h5MirrorSyncTimer);
  h5MirrorSyncTimer = null;
}

function _restartH5MirrorAutoSync() {
  _stopH5MirrorAutoSync();
  _startH5MirrorAutoSync();
}

function _syncH5MirrorAutoSyncForCurrentSession() {
  if (_isH5MirrorSession(getSessionById(currentSessionId))) _startH5MirrorAutoSync();
  else _stopH5MirrorAutoSync();
}

function _setChatSidebarConversationActive(kind) {
  document.querySelectorAll('.chat-sidebar-nav .chat-sidebar-entry').forEach(function(btn) {
    var isH5 = btn.hasAttribute('data-h5-chat-sync');
    var isDefault = btn.hasAttribute('data-chat-open-default');
    btn.classList.toggle('is-active', kind === 'h5' ? isH5 : isDefault);
  });
}

function openH5ChatMirrorSession() {
  var session = getOrCreateH5MirrorSession();
  _setChatSidebarConversationActive('h5');
  if (String(currentSessionId || '') !== H5_MIRROR_SESSION_ID) {
    switchChatSession(session.id);
  } else {
    renderChatSessionList();
    _syncH5MirrorAutoSyncForCurrentSession();
  }
  _restartH5MirrorAutoSync();
  syncH5ChatMirrorSession(true);
}

function openDefaultChatSession() {
  _setChatSidebarConversationActive('default');
  var target = chatSessions.find(function(s) {
    return !_isH5MirrorSession(s) && _getSessionMode(s) === CHAT_MODE_DEFAULT;
  });
  if (target) {
    switchChatSession(target.id);
    return;
  }
  createNewSession(CHAT_MODE_DEFAULT);
}

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
  var next = !!pending;
  var s = getSessionById(sid);
  if (s && !!s.pending === next) {
    if (next) chatPendingBySession[sid] = true;
    else delete chatPendingBySession[sid];
    refreshChatInputState();
    return;
  }
  if (next) chatPendingBySession[sid] = true;
  else delete chatPendingBySession[sid];
  if (s) {
    s.pending = next;
    if (!next) { delete s._typingState; _stopTypingStateSyncTimer(); }
  }
  try {
    saveChatSessionsToStorage();
  } catch (e1) {}
  refreshChatInputState();
  renderChatSessionList();
}

function _saveSessionTypingState(sid, mainText, step, stepMode) {
  var s = getSessionById(String(sid || ''));
  if (!s) return;
  if (!s._typingState) s._typingState = { mainText: '正在处理…', steps: [], _ver: 0 };
  if (mainText != null) { s._typingState.mainText = mainText; s._typingState._ver = (s._typingState._ver || 0) + 1; }
  if (step != null) {
    s._typingState._ver = (s._typingState._ver || 0) + 1;
    if (stepMode === 'replace_last' && s._typingState.steps.length) {
      s._typingState.steps[s._typingState.steps.length - 1] = step;
    } else if (stepMode === 'replace_last') {
      s._typingState.steps.push(step);
    } else if (stepMode === 'append') {
      s._typingState.steps.push(step);
    }
  }
}
function _clearSessionTypingState(sid) {
  var s = getSessionById(String(sid || ''));
  if (s) delete s._typingState;
  _stopTypingStateSyncTimer();
}

var _typingStateSyncTimer = null;
var _typingStateSyncLastVer = -1;
var _typingStateSyncLastStepCount = -1;

function _startTypingStateSyncTimer(sid) {
  _stopTypingStateSyncTimer();
  _typingStateSyncLastVer = -1;
  _typingStateSyncLastStepCount = -1;
  _typingStateSyncTimer = setInterval(function() {
    if (String(currentSessionId) !== String(sid)) { _stopTypingStateSyncTimer(); return; }
    var s = getSessionById(String(sid));
    if (!s || !s._typingState || !isSessionPending(sid)) { _stopTypingStateSyncTimer(); return; }
    var ts = s._typingState;
    var ver = ts._ver || 0;
    var stepCount = (ts.steps || []).length;
    if (ver === _typingStateSyncLastVer && stepCount === _typingStateSyncLastStepCount) return;
    setChatTypingMainText(ts.mainText || '正在处理…');
    if (stepCount > _typingStateSyncLastStepCount && _typingStateSyncLastStepCount >= 0) {
      for (var i = _typingStateSyncLastStepCount; i < stepCount; i++) {
        appendChatTypingStep(ts.steps[i]);
      }
    }
    _typingStateSyncLastVer = ver;
    _typingStateSyncLastStepCount = stepCount;
  }, 2000);
}
function _stopTypingStateSyncTimer() {
  if (_typingStateSyncTimer != null) { clearInterval(_typingStateSyncTimer); _typingStateSyncTimer = null; }
}

/** 与 chatSessions JSON 并列：按会话 id 单独存 poll_resume，避免会话对象未找到时整段不落盘 */
function _pollResumeBackupStorageKey(sid) {
  var u = getChatSessionsStorageKey();
  if (!u || sid == null || sid === '') return '';
  return u + '_poll_' + String(sid);
}

function mergePollResumeFromBackupIntoSession(s) {
  if (!s || s.id == null) return;
  if ((s.poll_resume_task_id || '').trim()) return;
  try {
    var bk = _pollResumeBackupStorageKey(s.id);
    if (!bk) return;
    var raw = localStorage.getItem(bk);
    if (!raw) return;
    var o = JSON.parse(raw);
    if (!o || !(o.task_id || '').toString().trim()) return;
    var at = Number(o.at) || Date.now();
    if (Date.now() - at > _POLL_RESUME_MAX_AGE_MS) {
      localStorage.removeItem(bk);
      return;
    }
    s.poll_resume_task_id = String(o.task_id).trim();
    s.poll_resume_at = at;
  } catch (e) {}
}

/** 刷新后可据 task_id 恢复轮询；由 task_poll 事件更新 */
function persistSessionPollResumeTaskId(sid, taskId) {
  var tid = (taskId || '').trim();
  var sid0 = String(sid != null ? sid : '').trim();
  if (!tid || !sid0) return;
  var s = getSessionById(sid0);
  if (s) {
    s.poll_resume_task_id = tid;
    s.poll_resume_at = Date.now();
  }
  try {
    var bk = _pollResumeBackupStorageKey(sid0);
    if (bk) {
      localStorage.setItem(bk, JSON.stringify({ task_id: tid, at: Date.now() }));
    }
  } catch (e0) {}
  scheduleSaveChatSessionsToStorage();
}

function clearSessionPollResume(sid) {
  var sid0 = String(sid != null ? sid : '').trim();
  var s = sid0 ? getSessionById(sid0) : null;
  if (s) {
    delete s.poll_resume_task_id;
    delete s.poll_resume_at;
  }
  try {
    var bk = _pollResumeBackupStorageKey(sid0);
    if (bk) localStorage.removeItem(bk);
  } catch (eRm) {}
  try {
    saveChatSessionsToStorage();
  } catch (e3) {}
}
function refreshChatInputState() {
  var input = document.getElementById('chatInput');
  var btn = document.getElementById('chatSendBtn');
  var cancelBtn = document.getElementById('chatCancelBtn');
  if (!btn) return;
  var current = getSessionById(currentSessionId);
  if (_isH5MirrorSession(current)) {
    btn.disabled = true;
    if (input) {
      input.disabled = true;
      input.placeholder = 'H5 \u6d88\u606f\u955c\u50cf\u4f1a\u8bdd\u4ec5\u5c55\u793a\uff0c\u70b9\u51fb\u5de6\u4fa7 H5 \u6d88\u606f\u53ef\u5237\u65b0';
    }
    if (cancelBtn) {
      cancelBtn.disabled = true;
      cancelBtn.title = 'H5 \u6d88\u606f\u955c\u50cf\u4f1a\u8bdd\u65e0\u9700\u53d6\u6d88';
    }
    return;
  }
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

function escapeHtmlChat(str) {
  if (str == null || str === '') return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** 在线版：后端固定 sutui/deepseek-chat，与 backend lobster_default_sutui_chat_model 一致 */
function _isOnlineFixedSutuiChat() {
  return typeof EDITION !== 'undefined' && EDITION === 'online';
}
function _onlineFixedChatModelPayload() {
  return 'sutui/deepseek-chat';
}

/** 发送按钮旁：费用与模型确认 */
function openChatCapabilityCostConfirm(opts) {
  opts = opts || {};
  var capId = opts.capability_id || '';
  var invokeModel = (opts.invoke_model || '').trim();
  var creditDisplay = opts.credit_display || '未知';
  var note = (opts.note || '').trim();
  var secLeft = opts.timeout_seconds != null ? opts.timeout_seconds : 300;

  return new Promise(function(resolve) {
    var sendBtn = document.getElementById('chatSendBtn');
    if (!sendBtn) {
      resolve(false);
      return;
    }

    var backdrop = document.createElement('div');
    backdrop.className = 'chat-cost-confirm-backdrop';
    backdrop.style.cssText =
      'position:fixed;inset:0;z-index:10040;background:rgba(0,0,0,0.45);' +
      '-webkit-backdrop-filter:blur(3px);backdrop-filter:blur(3px);';

    var pop = document.createElement('div');
    pop.className = 'chat-cost-confirm-popover chat-cost-confirm-popover--floating';
    pop.setAttribute('role', 'dialog');
    pop.setAttribute('aria-modal', 'true');
    pop.setAttribute('aria-labelledby', 'chatCostConfirmTitle');
    pop.style.cssText =
      'position:fixed;z-index:10050;box-sizing:border-box;margin:0;' +
      'width:min(92vw,300px);max-width:300px;padding:0.9rem 1rem;' +
      'background:rgba(18,18,24,0.97);border:1px solid rgba(255,255,255,0.1);' +
      'border-radius:12px;box-shadow:0 12px 40px rgba(0,0,0,0.55),0 0 0 1px rgba(6,182,212,0.15);' +
      'color:#e4e4e7;font-size:0.82rem;line-height:1.45;';

    pop.innerHTML =
      '<div id="chatCostConfirmTitle" class="chat-cost-confirm-title" role="heading" aria-level="2" ' +
      'style="font-weight:600;font-size:0.9rem;margin:0 0 0.5rem;color:#e4e4e7;line-height:1.3;">确认调用能力</div>' +
      '<div class="chat-cost-confirm-cap-label" style="font-size:0.72rem;color:#a1a1aa;text-transform:uppercase;letter-spacing:0.04em;">能力</div>' +
      '<div class="chat-cost-confirm-cap-value" style="font-family:ui-monospace,Consolas,monospace;font-size:0.8rem;word-break:break-all;margin:0.15rem 0 0.35rem;color:#e4e4e7;">' +
      escapeHtmlChat(capId || '（未指定）') +
      '</div>' +
      (invokeModel
        ? '<div class="chat-cost-confirm-cap-label" style="font-size:0.72rem;color:#a1a1aa;margin-top:0.35rem;">模型（与本次调用一致）</div>' +
          '<div class="chat-cost-confirm-cap-value" style="font-family:ui-monospace,Consolas,monospace;font-size:0.8rem;word-break:break-all;margin:0.15rem 0 0.5rem;color:#e4e4e7;">' +
          escapeHtmlChat(invokeModel) +
          '</div>'
        : '') +
      '<div class="chat-cost-confirm-credits" style="font-weight:600;font-size:0.95rem;color:#06b6d4;margin:0 0 0.35rem;">' +
      escapeHtmlChat('参考算力：' + creditDisplay) +
      '</div>' +
      (note
        ? '<div class="chat-cost-confirm-note" style="color:#a1a1aa;font-size:0.78rem;max-height:6.5rem;overflow-y:auto;margin:0 0 0.5rem;white-space:pre-wrap;word-break:break-word;padding:0.45rem 0.5rem;background:rgba(0,0,0,0.25);border-radius:8px;border:1px solid rgba(255,255,255,0.06);">' +
          escapeHtmlChat(note) +
          '</div>'
        : '') +
      '<div class="chat-cost-confirm-timeout" style="font-size:0.72rem;color:#a1a1aa;margin-bottom:0.55rem;">' +
      escapeHtmlChat('约 ' + secLeft + ' 秒内有效，超时将自动取消') +
      '</div>' +
      '<div class="chat-cost-confirm-actions" style="display:flex;gap:0.45rem;justify-content:flex-end;flex-wrap:wrap;">' +
      '<button type="button" class="btn btn-ghost btn-sm" data-cc-cancel>取消</button>' +
      '<button type="button" class="btn btn-primary btn-sm" data-cc-ok>确认调用</button>' +
      '</div>';

    function positionPopover() {
      var rect = sendBtn.getBoundingClientRect();
      var gap = 10;
      var vw = window.innerWidth || document.documentElement.clientWidth || 0;
      var popW = Math.min(300, Math.max(260, vw - 16));
      pop.style.width = popW + 'px';
      var left = rect.right - popW;
      if (left < 8) left = 8;
      if (left + popW > vw - 8) left = Math.max(8, vw - popW - 8);
      pop.style.left = left + 'px';
      pop.style.right = 'auto';
      var th = pop.offsetHeight || 200;
      var topEdge = rect.top - gap - th;
      if (topEdge < 8) topEdge = 8;
      pop.style.top = topEdge + 'px';
    }

    document.body.appendChild(backdrop);
    document.body.appendChild(pop);
    requestAnimationFrame(function() {
      positionPopover();
      requestAnimationFrame(positionPopover);
    });

    var onResize = function() {
      positionPopover();
    };
    window.addEventListener('resize', onResize);

    var settled = false;
    function cleanup() {
      window.removeEventListener('resize', onResize);
      document.removeEventListener('keydown', onKey);
      if (backdrop.parentNode) backdrop.parentNode.removeChild(backdrop);
      if (pop.parentNode) pop.parentNode.removeChild(pop);
    }

    function finish(accept) {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(accept);
    }

    function onKey(e) {
      if (e.key === 'Escape') finish(false);
    }
    document.addEventListener('keydown', onKey);

    backdrop.addEventListener('click', function() {
      finish(false);
    });

    var ok = pop.querySelector('[data-cc-ok]');
    var cancel = pop.querySelector('[data-cc-cancel]');
    if (ok) ok.addEventListener('click', function() { finish(true); });
    if (cancel) cancel.addEventListener('click', function() { finish(false); });

    if (ok) ok.focus();
  });
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
  _stopTypingStateSyncTimer();
  if (sid && isSessionPending(sid)) {
    showChatTypingIndicator();
    var s0 = getSessionById(sid);
    if (s0 && s0._typingState) {
      setChatTypingMainText(s0._typingState.mainText || '正在处理…');
      var savedSteps = s0._typingState.steps || [];
      for (var si = 0; si < savedSteps.length; si++) {
        appendChatTypingStep(savedSteps[si]);
      }
      _typingStateSyncLastVer = s0._typingState._ver || 0;
      _typingStateSyncLastStepCount = savedSteps.length;
      _startTypingStateSyncTimer(sid);
    } else if (s0 && (s0.poll_resume_task_id || '').trim()) {
      setChatTypingMainText('正在查询生成结果…（恢复连接）');
    }
  }
  container.scrollTop = container.scrollHeight;
  refreshChatInputState();
}

/** 刷新后仅「有可续查的 task_poll task_id」才保留 pending；否则流已断无法恢复，不应再显示正在思考 */

function _normalizeSessionPendingAfterLoad(s) {
  if (!s) return false;
  delete s._typingState;
  var changed = false;
  var tid = (s.poll_resume_task_id || '').trim();
  if (tid) {
    var age = Date.now() - (s.poll_resume_at || 0);
    if (age > _POLL_RESUME_MAX_AGE_MS) {
      if (s.pending) s.pending = false;
      delete s.poll_resume_task_id;
      delete s.poll_resume_at;
      return true;
    }
    if (!chatAutoResumePollEnabled()) {
      if (s.pending) {
        s.pending = false;
        changed = true;
      }
      return changed;
    }
    // 有可续查 task_id 时：保留 poll_resume，不因「末条是 assistant」清空（多轮对话里上一轮常以助手结尾）
    if (!s.pending) {
      s.pending = true;
      changed = true;
    }
    return changed;
  }
  var msgs = s.messages || [];
  var last = msgs.length ? msgs[msgs.length - 1] : null;
  if (last && last.role === 'assistant') {
    if (s.pending) {
      s.pending = false;
      changed = true;
    }
    return changed;
  }
  if (s.pending) {
    s.pending = false;
    changed = true;
  }
  return changed;
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
      chatPendingBySession = {};
      var storageDirty = false;
      chatSessions.forEach(function(s) {
        if (s.id != null) s.id = String(s.id);
        s.mode = _getSessionMode(s);
        var normalizedMemoryScope = _getSessionMemoryScope(s);
        if (s.chat_memory_scope !== normalizedMemoryScope) {
          s.chat_memory_scope = normalizedMemoryScope;
          storageDirty = true;
        }
        var m = s.messages || s.history;
        s.messages = Array.isArray(m) ? m : [];
        mergePollResumeFromBackupIntoSession(s);
        if (_normalizeSessionPendingAfterLoad(s)) storageDirty = true;
        if (_pruneTrailingDuplicateAssistantErrors(s)) storageDirty = true;
        if (s.pending) chatPendingBySession[s.id] = true;
      });
      if (storageDirty) {
        try {
          saveChatSessionsToStorage();
        } catch (e0) {}
      }
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
/** task_poll 高频时避免每次全量 stringify 写盘卡死主线程；备份键仍即时写入 */
var _saveChatSessionsScheduledTimer = null;
function scheduleSaveChatSessionsToStorage() {
  if (_saveChatSessionsScheduledTimer != null) clearTimeout(_saveChatSessionsScheduledTimer);
  _saveChatSessionsScheduledTimer = setTimeout(function() {
    _saveChatSessionsScheduledTimer = null;
    try {
      saveChatSessionsToStorage();
    } catch (e) {}
  }, 500);
}
function flushPendingChatSessionsSave() {
  if (_saveChatSessionsScheduledTimer != null) {
    clearTimeout(_saveChatSessionsScheduledTimer);
    _saveChatSessionsScheduledTimer = null;
    try {
      saveChatSessionsToStorage();
    } catch (e) {}
  }
}
function getSessionTitle(session) {
  if (_isH5MirrorSession(session)) return session.title || _h5MirrorTitle();
  var msg = (session.messages || []).find(function(m) { return m.role === 'user' && (m.content || '').trim(); });
  if (msg) {
    var t = (msg.content || '').trim();
    return t.length > 24 ? t.slice(0, 24) + '…' : t;
  }
  if (session && _isWorkspaceSession(session)) return session.title || '云端工作台';
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
function _getRequestedNewChatMode(mode) {
  if (mode) return _normalizeChatMode(mode);
  var active = document.querySelector('.chat-mode-pill.is-active[data-chat-mode]');
  if (active) return _normalizeChatMode(active.getAttribute('data-chat-mode'));
  return _getStoredChatMode();
}

function _renderChatEmptyTitle(rawText) {
  var el = document.getElementById('chatEmptyTitle');
  if (!el) return;
  var text = String(rawText || '').trim();
  if (!text) {
    el.textContent = '';
    return;
  }
  if (text.indexOf('AI 员工') >= 0) {
    el.innerHTML = escapeHtml(text).replace('AI 员工', '<span class="chat-empty-title-emphasis">AI 员工</span>');
    return;
  }
  el.textContent = text;
}

function _getChatSuggestionMeta(title) {
  var key = String(title || '').trim();
  var map = {
    '生成视频': { tone: 'video', icon: '▶', desc: '一键生成创意视频' },
    '电商套图': { tone: 'ecommerce', icon: '👜', desc: '商品图快速生成' },
    '电商图器': { tone: 'ecommerce', icon: '👜', desc: '商品图快速生成' },
    '自动上架': { tone: 'publish', icon: '↥', desc: '批量发布到平台' },
    '小红书运营': { tone: 'content', icon: '✦', desc: '笔记创作与运营' },
    '图片生成': { tone: 'image', icon: '▣', desc: 'AI 生成精美图片' },
    '运营规划': { tone: 'plan', icon: '▤', desc: '制定运营策略' },
    '创建网页': { tone: 'video', icon: '⌘', desc: '从需求直接起一个网页' },
    '继续开发': { tone: 'ecommerce', icon: '↺', desc: '接着当前进度继续往下做' },
    '总结网页': { tone: 'plan', icon: '⌂', desc: '快速梳理页面结构和内容' },
    '输出文档': { tone: 'publish', icon: '✎', desc: '整理成可交付文档' },
    '上传图片': { tone: 'image', icon: '⬆', desc: '结合参考图继续处理' },
    '生成页面图': { tone: 'content', icon: '▣', desc: '先看界面草图和方向' },
    '创建应用': { tone: 'video', icon: '◫', desc: '快速搭一个移动应用方案' },
    '页面流程': { tone: 'plan', icon: '⇄', desc: '梳理关键页面和跳转逻辑' },
    '生成界面图': { tone: 'image', icon: '▣', desc: '先出界面视觉方案' },
    '创建小程序': { tone: 'video', icon: '◧', desc: '从零搭建小程序能力' },
    '页面规划': { tone: 'plan', icon: '▤', desc: '先把结构和模块排清楚' },
    '上传文档': { tone: 'publish', icon: '⇪', desc: '根据资料继续拆解执行' },
    '创建智能体': { tone: 'video', icon: '◎', desc: '设计一个可执行的智能体' },
    '对话流程': { tone: 'content', icon: '✦', desc: '梳理对话节点和流程' },
    '接入能力': { tone: 'ecommerce', icon: '+', desc: '给工作台接更多能力' },
    '继续优化': { tone: 'plan', icon: '↻', desc: '在已有基础上继续优化' },
    '上传资料': { tone: 'publish', icon: '⇪', desc: '结合资料继续完善方案' },
    '创建技能': { tone: 'video', icon: '✳', desc: '从零搭建新的技能能力' },
    '参数设计': { tone: 'plan', icon: '≣', desc: '把参数和规则设计清楚' },
    'AI对话': { tone: 'video', icon: '⌕', desc: '回到本机智能对话' },
    'H5对话': { tone: 'ecommerce', icon: '○', desc: '查看远程会话消息' },
    '帮我创作': { tone: 'publish', icon: '≡', desc: '填入创作需求' },
    '创作图片': { tone: 'content', icon: '▣', desc: '进入图片工作台' },
    '爆款TVC': { tone: 'image', icon: '▶', desc: '填入视频生成话术' },
    '视频分镜': { tone: 'plan', icon: '▶', desc: '进入分镜工作台' },
    '发布中心': { tone: 'publish', icon: '▣', desc: '管理发布账号和记录' },
    '技能商店': { tone: 'ecommerce', icon: '</>', desc: '查看可用技能' }
  };
  return map[key] || { tone: 'plan', icon: '•', desc: '' };
}

function _clearChatSuggestionActionAttrs(el) {
  if (!el) return;
  el.removeAttribute('data-chat-prompt');
  el.removeAttribute('data-open-hidden-view');
  el.removeAttribute('data-jump-view');
  el.removeAttribute('data-chat-quick-mode');
  el.removeAttribute('data-chat-open-default');
  el.removeAttribute('data-h5-chat-sync');
}

function _setChatSuggestionAction(el, attr, value) {
  if (!el || !attr) return;
  _clearChatSuggestionActionAttrs(el);
  el.setAttribute(attr, value == null ? '1' : String(value));
}

function _renderChatSuggestionChip(el, title, prompt) {
  if (!el) return;
  var safeTitle = String(title || '').trim();
  var meta = _getChatSuggestionMeta(safeTitle);
  if (safeTitle === '图片生成') {
    meta.desc = '文生图 / 参考图合成';
  }
  el.style.display = '';
  el.setAttribute('data-chip-tone', meta.tone || 'plan');
  _clearChatSuggestionActionAttrs(el);
  if (prompt) el.setAttribute('data-chat-prompt', prompt);
  el.innerHTML =
    '<span class="chat-suggestion-chip-icon">' + escapeHtml(meta.icon || '•') + '</span>' +
    '<span class="chat-suggestion-chip-copy">' +
      '<span class="chat-suggestion-chip-title">' + escapeHtml(safeTitle) + '</span>' +
      (meta.desc ? '<span class="chat-suggestion-chip-desc">' + escapeHtml(meta.desc) + '</span>' : '') +
    '</span>';
}

var CHAT_DEFAULT_PLACEHOLDER = '发送消息或输入 / 选择技能';
var CHAT_DEFAULT_COMPOSER_LEAD = '告诉我您想做什么？我会先帮您理清任务，再继续生成和执行~';
var CHAT_QUICK_MODE_CONFIG = {
  video: {
    badge: '视频合成',
    title: '当前模式：视频合成',
    desc: '直接在下面输入一句话描述视频内容；如果要调分镜、时长和镜头节奏，再进工作台精调。',
    lead: '直接描述您要合成的视频内容，我会先帮您开始生成；需要更细参数时再进工作台。',
    placeholder: '例如：我要合成一个20秒的护肤品短视频，晨光感，人物手持产品，镜头缓慢推进',
    starter: '我要合成一个视频，视频内容是：',
    advancedView: 'seedance-tvc-studio'
  },
  image: {
    badge: '图片生成',
    title: '当前模式：图片生成',
    desc: '直接输入一句话描述画面内容；如果要上传参考图、选比例和模型，再进工作台精调。',
    lead: '直接描述您想生成的图片内容，我会先帮您开始生成；需要更细参数时再进工作台。',
    placeholder: '例如：我要生成一张香薰蜡烛主视觉，奶油风桌面，暖白自然光，构图高级干净',
    starter: '我要生成一张图片，画面内容是：',
    advancedView: 'image-composer-studio'
  }
};

function _normalizeChatQuickMode(mode) {
  var key = String(mode || '').trim();
  return CHAT_QUICK_MODE_CONFIG[key] ? key : '';
}

function _getSessionQuickMode(session) {
  if (!session || typeof session !== 'object') return '';
  return _normalizeChatQuickMode(session.chat_quick_mode);
}

function _focusChatInputToEnd() {
  var input = document.getElementById('chatInput');
  if (!input) return;
  input.focus();
  if (typeof input.setSelectionRange === 'function') {
    var cursor = (input.value || '').length;
    input.setSelectionRange(cursor, cursor);
  }
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

function renderChatQuickModeUi(mode) {
  var normalized = _normalizeChatQuickMode(mode);
  var bar = document.getElementById('chatQuickModeBar');
  var badge = document.getElementById('chatQuickModeBadge');
  var title = document.getElementById('chatQuickModeTitle');
  var desc = document.getElementById('chatQuickModeDesc');
  var advancedBtn = document.getElementById('chatQuickModeAdvancedBtn');
  var composerLead = document.getElementById('chatComposerLead');
  var input = document.getElementById('chatInput');
  var current = getSessionById(currentSessionId);
  var sessionMode = _getSessionMode(current);
  if (!bar || sessionMode === CHAT_MODE_WORKSPACE) {
    if (bar) bar.classList.remove('is-visible');
    return;
  }
  if (!normalized) {
    bar.classList.remove('is-visible');
    if (badge) badge.textContent = '';
    if (title) title.textContent = '';
    if (desc) desc.textContent = '';
    if (advancedBtn) advancedBtn.setAttribute('data-open-hidden-view', '');
    if (composerLead) composerLead.textContent = CHAT_DEFAULT_COMPOSER_LEAD;
    if (input) input.placeholder = CHAT_DEFAULT_PLACEHOLDER;
    return;
  }
  var cfg = CHAT_QUICK_MODE_CONFIG[normalized];
  bar.classList.add('is-visible');
  if (badge) badge.textContent = cfg.badge;
  if (title) title.textContent = cfg.title;
  if (desc) desc.textContent = cfg.desc;
  if (advancedBtn) advancedBtn.setAttribute('data-open-hidden-view', cfg.advancedView || '');
  if (composerLead) composerLead.textContent = cfg.lead;
  if (input) input.placeholder = cfg.placeholder;
}

function setChatQuickMode(mode, options) {
  var normalized = _normalizeChatQuickMode(mode);
  var current = getSessionById(currentSessionId);
  if (!current) return;
  current.chat_quick_mode = normalized || '';
  saveChatSessionsToStorage();
  renderChatQuickModeUi(normalized);
  if (!normalized) return;
  var cfg = CHAT_QUICK_MODE_CONFIG[normalized];
  var input = document.getElementById('chatInput');
  if (input && cfg) {
    var shouldPrefill = !options || options.prefill !== false;
    if (shouldPrefill) input.value = cfg.starter;
    _focusChatInputToEnd();
  }
}

function renderChatMemoryScopeUi() {
  var select = document.getElementById('chatMemoryScopeSelect');
  if (!select) return;
  var current = getSessionById(currentSessionId);
  var isDefaultChat = current && !_isWorkspaceSession(current) && !_isH5MirrorSession(current);
  var scope = _getSessionMemoryScope(current);
  select.value = scope;
  select.disabled = !isDefaultChat;
  select.title = isDefaultChat ? '本会话使用的记忆范围' : '当前会话不使用智能对话记忆范围';
}

function setChatMemoryScope(scope) {
  var current = getSessionById(currentSessionId);
  if (!current || _isWorkspaceSession(current) || _isH5MirrorSession(current)) {
    renderChatMemoryScopeUi();
    return;
  }
  current.chat_memory_scope = _normalizeChatMemoryScope(scope);
  current.updatedAt = Date.now();
  saveChatSessionsToStorage();
  renderChatMemoryScopeUi();
  renderChatSessionList();
}

function applyChatMemoryScopeHeader(headers, session) {
  if (!headers || !session || _isWorkspaceSession(session) || _isH5MirrorSession(session)) return headers;
  headers['X-Lobster-Memory-Scope'] = _getSessionMemoryScope(session);
  return headers;
}

function bindChatMemoryScopeSelect() {
  var select = document.getElementById('chatMemoryScopeSelect');
  if (!select || select.dataset.bound === '1') return;
  select.dataset.bound = '1';
  select.addEventListener('change', function() {
    setChatMemoryScope(select.value);
  });
}

function updateWorkspaceCategoryUi(category) {
  var normalized = WORKSPACE_CATEGORY_CONFIG[category] ? category : WORKSPACE_CATEGORY_DEFAULT;
  document.querySelectorAll('.workspace-category-tab[data-workspace-category]').forEach(function(btn) {
    btn.classList.toggle('is-active', btn.getAttribute('data-workspace-category') === normalized);
  });
  var current = getSessionById(currentSessionId);
  if (current && _isWorkspaceSession(current)) {
    current.workspace_category = normalized;
    saveChatSessionsToStorage();
  }
  var cfg = WORKSPACE_CATEGORY_CONFIG[normalized];
  if (!cfg) return;
  var subtitle = document.getElementById('chatEmptySubtitle');
  var composerLead = document.getElementById('chatComposerLead');
  var input = document.getElementById('chatInput');
  var hint = document.getElementById('chatModeHint');
  var chipIds = [
    'chatSuggestionChip1',
    'chatSuggestionChip2',
    'chatSuggestionChip3',
    'chatSuggestionChip4',
    'chatSuggestionChip5',
    'chatSuggestionChip6',
    'chatSuggestionChip7',
    'chatSuggestionChip8'
  ];
  var shortcutIds = ['chatShortcutLink1', 'chatShortcutLink2', 'chatShortcutLink3'];
  _renderChatEmptyTitle(cfg.title);
  if (subtitle) subtitle.textContent = cfg.subtitle;
  if (composerLead) composerLead.textContent = '直接描述您要搭建的' + cfg.label + '目标、页面结构或关键功能，我会先帮您拆成可执行方案。';
  if (input) input.placeholder = cfg.placeholder;
  if (hint) hint.textContent = cfg.hint;
  chipIds.forEach(function(id, idx) {
    var el = document.getElementById(id);
    var item = cfg.chips[idx];
    if (!el) return;
    if (!item) {
      el.style.display = 'none';
      return;
    }
    _renderChatSuggestionChip(el, item[0], item[1]);
  });
  shortcutIds.forEach(function(id, idx) {
    var el = document.getElementById(id);
    var item = cfg.shortcuts[idx];
    if (!el || !item) return;
    el.textContent = item[0];
    el.setAttribute('data-chat-prompt', item[1]);
    el.removeAttribute('data-jump-view');
  });
}

function updateWorkspaceStatusUi(options) {
  var strip = document.getElementById('workspaceStatusStrip');
  var primary = document.getElementById('workspaceStatusPrimary');
  var secondary = document.getElementById('workspaceStatusSecondary');
  var cliStatus = document.getElementById('workspaceCliAuthStatus');
  if (!strip) return;
  var cfg = options || {};
  var visible = !!cfg.visible;
  strip.style.display = visible ? 'flex' : 'none';
  if (!visible) return;
  if (primary) primary.textContent = cfg.primary || '测试模式';
  if (secondary) secondary.textContent = cfg.secondary || '当前未强制校验龙虾登录态';
  if (cliStatus) cliStatus.textContent = cfg.cli || '未连接';
}

function maybeUpdateWorkspaceStatusFromMessage(message) {
  var text = String(message || '').trim();
  if (!text) return;
  var current = getSessionById(currentSessionId);
  if (!current || !_isWorkspaceSession(current)) return;
  if (text.indexOf('未强制校验') >= 0 || text.indexOf('测试模式') >= 0) {
    updateWorkspaceStatusUi({
      visible: true,
      primary: '测试模式',
      secondary: '当前未强制校验龙虾登录态，可先直接体验工作台能力'
    });
    return;
  }
  if (text.indexOf('登录授权') >= 0 || text.indexOf('需要登录') >= 0 || text.indexOf('未登录') >= 0) {
    updateWorkspaceStatusUi({
      visible: true,
      primary: '待连接账号',
      secondary: '工作台主体可先测试，账号类能力需要登录授权后再执行'
    });
    return;
  }
  if (text.indexOf('云端') >= 0 || text.indexOf('处理') >= 0 || text.indexOf('查询') >= 0 || text.indexOf('提交') >= 0) {
    updateWorkspaceStatusUi({
      visible: true,
      primary: '云端处理中',
      secondary: text,
      cli: '已连接'
    });
  }
}

function updateChatModeUi(mode) {
  var normalized = _normalizeChatMode(mode);
  document.querySelectorAll('[data-chat-mode="' + CHAT_MODE_WORKSPACE + '"], [data-chat-home-mode="' + CHAT_MODE_WORKSPACE + '"]').forEach(function(btn) {
    btn.hidden = !CHAT_WORKSPACE_ENTRY_ENABLED;
    btn.setAttribute('aria-hidden', CHAT_WORKSPACE_ENTRY_ENABLED ? 'false' : 'true');
  });
  document.querySelectorAll('.chat-mode-pill[data-chat-mode]').forEach(function(btn) {
    btn.classList.toggle('is-active', _normalizeChatMode(btn.getAttribute('data-chat-mode')) === normalized);
  });
  _saveStoredChatMode(normalized);
  var hint = document.getElementById('chatModeHint');
  var input = document.getElementById('chatInput');
  var eyebrow = document.getElementById('chatEmptyEyebrow');
  var homeWorkspacePill = document.getElementById('chatHomeWorkspacePill');
  var subtitle = document.getElementById('chatEmptySubtitle');
  var composerLead = document.getElementById('chatComposerLead');
  var attachBtn = document.getElementById('chatAttachBtn');
  var directChip = document.getElementById('chatDirectLlmChip');
  var directChk = document.getElementById('chatDirectLlmCheck');
  var chip1 = document.getElementById('chatSuggestionChip1');
  var chip2 = document.getElementById('chatSuggestionChip2');
  var chip3 = document.getElementById('chatSuggestionChip3');
  var chip4 = document.getElementById('chatSuggestionChip4');
  var chip5 = document.getElementById('chatSuggestionChip5');
  var chip6 = document.getElementById('chatSuggestionChip6');
  var chip7 = document.getElementById('chatSuggestionChip7');
  var chip8 = document.getElementById('chatSuggestionChip8');
  var shortcut1 = document.getElementById('chatShortcutLink1');
  var shortcut2 = document.getElementById('chatShortcutLink2');
  var shortcut3 = document.getElementById('chatShortcutLink3');
  var categoryTabs = document.getElementById('workspaceCategoryTabs');
  if (eyebrow) eyebrow.classList.toggle('is-active', normalized !== CHAT_MODE_WORKSPACE);
  if (homeWorkspacePill) homeWorkspacePill.classList.toggle('is-active', normalized === CHAT_MODE_WORKSPACE);
  if (eyebrow) eyebrow.textContent = '智能对话';
  if (homeWorkspacePill) homeWorkspacePill.textContent = '云端工作台';
  if (normalized === CHAT_MODE_WORKSPACE) {
    if (hint) hint.textContent = '当前模式：云端工作台。当前消息会走独立工作台链路，不走原来的 AI 对话。';
    if (composerLead) composerLead.textContent = '告诉我要搭建的页面、应用或工作流，我会先拆成明确步骤，再继续执行。';
    if (categoryTabs) categoryTabs.classList.add('is-visible');
    updateWorkspaceStatusUi({
      visible: true,
      primary: '测试模式',
      secondary: '当前未强制校验龙虾登录态，可先直接体验工作台能力'
    });
    if (attachBtn) attachBtn.style.display = '';
    if (directChip) directChip.style.display = 'none';
    if (directChk) directChk.checked = false;
    var current = getSessionById(currentSessionId);
    updateWorkspaceCategoryUi(_getWorkspaceCategory(current));
  } else {
    if (hint) hint.textContent = '默认模式：继续走现在这套智能对话链路。';
    if (input) input.placeholder = '发送消息或输入 / 选择技能';
    if (eyebrow) eyebrow.textContent = '智能对话';
    _renderChatEmptyTitle('👋 您好，我是 AI 员工');
    if (subtitle) subtitle.textContent = '我可以帮您创作内容、生成视频、处理数据、运营分析等';
    if (composerLead) composerLead.textContent = '告诉我您想做什么？我会先帮您理清任务，再继续生成和执行~';
    if (categoryTabs) categoryTabs.classList.remove('is-visible');
    updateWorkspaceStatusUi({ visible: false });
    if (chip1) {
      _renderChatSuggestionChip(chip1, 'AI对话');
      _setChatSuggestionAction(chip1, 'data-chat-open-default', '1');
    }
    if (chip2) {
      _renderChatSuggestionChip(chip2, 'H5对话');
      _setChatSuggestionAction(chip2, 'data-h5-chat-sync', '1');
    }
    if (chip3) {
      _renderChatSuggestionChip(chip3, '帮我创作', '帮我写一版电商详情页文案、短视频脚本和发布标题。');
    }
    if (chip4) {
      _renderChatSuggestionChip(chip4, '创作图片');
      _setChatSuggestionAction(chip4, 'data-open-hidden-view', 'image-composer-studio');
    }
    if (chip5) {
      _renderChatSuggestionChip(chip5, '爆款TVC', '用爆款tvc帮我生成一个视频。');
    }
    if (chip6) {
      _renderChatSuggestionChip(chip6, '视频分镜');
      _setChatSuggestionAction(chip6, 'data-open-hidden-view', 'seedance-tvc-studio');
    }
    if (chip7) {
      _renderChatSuggestionChip(chip7, '发布中心');
      _setChatSuggestionAction(chip7, 'data-jump-view', 'publish');
    }
    if (chip8) {
      _renderChatSuggestionChip(chip8, '技能商店');
      _setChatSuggestionAction(chip8, 'data-jump-view', 'skill-store');
    }
    if (shortcut1) {
      shortcut1.textContent = '打开技能商店';
      shortcut1.setAttribute('data-jump-view', 'skill-store');
      shortcut1.removeAttribute('data-chat-prompt');
    }
    if (shortcut2) {
      shortcut2.textContent = '前往发布中心';
      shortcut2.setAttribute('data-jump-view', 'publish');
      shortcut2.removeAttribute('data-chat-prompt');
    }
    if (shortcut3) {
      shortcut3.textContent = '查看系统配置';
      shortcut3.setAttribute('data-jump-view', 'sys-config');
      shortcut3.removeAttribute('data-chat-prompt');
    }
    if (attachBtn) attachBtn.style.display = '';
    if (directChip) directChip.style.display = '';
  }
  renderChatQuickModeUi(normalized === CHAT_MODE_WORKSPACE ? '' : _getSessionQuickMode(getSessionById(currentSessionId)));
  renderChatMemoryScopeUi();
  if (homeWorkspacePill) homeWorkspacePill.textContent = '云端工作台';
}

function createNewSession(mode) {
  var id = 's' + Date.now();
  var sessionMode = _getRequestedNewChatMode(mode);
  var session = {
    id: id,
    title: '新对话',
    messages: [],
    updatedAt: Date.now(),
    pending: false,
    mode: sessionMode,
    chat_memory_scope: CHAT_MEMORY_SCOPE_DEFAULT
  };
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
  saveLastActiveChatSessionToStorage(sid);
  var nextSession = getSessionById(sid);
  if (nextSession) saveLastActiveChatSessionByModeToStorage(sid, _getSessionMode(nextSession));
  updateChatModeUi(_getSessionMode(nextSession));
  renderChatMemoryScopeUi();
  renderCurrentSessionMessages();
  renderChatSessionList();
  _syncH5MirrorAutoSyncForCurrentSession();
  _setChatSidebarConversationActive(_isH5MirrorSession(nextSession) ? 'h5' : 'default');
  /** 默认不自动续查，见 chatAutoResumePollEnabled */
  if (typeof maybeAutoResumeChatTaskPoll === 'function' && chatAutoResumePollEnabled())
    maybeAutoResumeChatTaskPoll({ pickAnySession: false });
}
function saveCurrentSessionToStore() {
  if (!currentSessionId) return;
  var session = chatSessions.find(function(s) { return String(s.id) === String(currentSessionId); });
  if (session) {
    session.messages = Array.isArray(chatHistory) ? chatHistory.slice() : [];
    session.updatedAt = Date.now();
    if (_isH5MirrorSession(session)) {
      session.title = _h5MirrorTitle();
      saveChatSessionsToStorage();
      return;
    }
    if (session.messages.length) {
      var firstUser = session.messages.find(function(m) { return m && m.role === 'user'; });
      if (firstUser && (firstUser.content || '').trim()) session.title = getSessionTitle(session);
    }
    saveChatSessionsToStorage();
  }
}

function clearStoredChatSessionReferences(sid) {
  var id = String(sid || '');
  if (!id) return;
  try {
    var lastKey = getChatLastSessionStorageKey();
    if (lastKey && String(localStorage.getItem(lastKey) || '') === id) localStorage.removeItem(lastKey);
    [CHAT_MODE_DEFAULT, CHAT_MODE_WORKSPACE].forEach(function(mode) {
      var modeKey = getChatLastSessionByModeStorageKey(mode);
      if (modeKey && String(localStorage.getItem(modeKey) || '') === id) localStorage.removeItem(modeKey);
    });
  } catch (e) {}
}

function deleteChatSession(id, ev) {
  if (ev) {
    ev.preventDefault();
    ev.stopPropagation();
  }
  var sid = String(id || '');
  var session = getSessionById(sid);
  if (!sid || !session) return;
  var isCurrent = String(currentSessionId || '') === sid;
  var deletedMode = _getSessionMode(session);
  var title = getSessionTitle(session);
  var message = isSessionPending(sid)
    ? '这个会话还有任务在进行中，删除后会停止当前页面继续展示结果。确定删除「' + title + '」吗？'
    : '确定删除会话「' + title + '」吗？';
  if (!window.confirm(message)) return;
  if (isCurrent) {
    abortActiveChatStream();
    removeChatTypingIndicator();
    chatHistory = [];
  }
  clearSessionPollResume(sid);
  delete chatPendingBySession[sid];
  chatSessions = chatSessions.filter(function(s) { return String(s.id) !== sid; });
  clearStoredChatSessionReferences(sid);
  saveChatSessionsToStorage();

  if (isCurrent) {
    currentSessionId = null;
    var nextSid = findLastChatSessionIdByMode(deletedMode, sid);
    if (!nextSid && chatSessions.length) nextSid = String(chatSessions[0].id || '');
    if (nextSid) {
      switchChatSession(nextSid);
    } else {
      createNewSession(deletedMode);
    }
    return;
  }
  renderChatSessionList();
}

window.addEventListener('beforeunload', function() {
  flushPendingChatSessionsSave();
  abortActiveChatStream();
  if (typeof saveCurrentSessionToStore === 'function') saveCurrentSessionToStore();
});
window.addEventListener('pagehide', function() {
  abortActiveChatStream();
});
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
    listEl.innerHTML = '<div class="chat-session-empty">还没有历史对话</div>';
    return;
  }
  listEl.innerHTML = filtered.map(function(s) {
    var title = getSessionTitle(s);
    var preview = getSessionPreview(s);
    var time = formatSessionTime(s.updatedAt);
    var active = s.id === currentSessionId ? ' active' : '';
    var memoryBadgeText = (!_isH5MirrorSession(s) && !_isWorkspaceSession(s)) ? _getSessionMemoryBadge(s) : '';
    var modeBadge = _isH5MirrorSession(s)
      ? '<span class="session-mode-badge">H5</span>'
      : (_isWorkspaceSession(s)
        ? '<span class="session-mode-badge">云端</span>'
        : (memoryBadgeText ? '<span class="session-mode-badge">' + escapeHtml(memoryBadgeText) + '</span>' : ''));
    var pendingDot = isSessionPending(s.id)
      ? '<span class="session-pending-dot" title="任务进行中"></span>'
      : '<span class="session-bubble-icon">◌</span>';
    return '<div class="chat-session-item' + active + '" data-session-id="' + escapeAttr(s.id) + '">' +
      '<div class="session-row">' +
        '<div class="session-leading">' + pendingDot + '</div>' +
        '<div class="session-copy">' +
          '<div class="session-title"><div class="session-title-row"><span>' + escapeHtml(title) + '</span>' + modeBadge + '</div></div>' +
          '<div class="session-preview">' + escapeHtml(preview) + '</div>' +
        '</div>' +
        '<button type="button" class="session-delete-btn" title="删除会话" aria-label="删除会话" data-delete-session-id="' + escapeAttr(s.id) + '">×</button>' +
      '</div>' +
      '<div class="session-time">' + escapeHtml(time) + '</div></div>';
  }).join('');
  listEl.querySelectorAll('.chat-session-item').forEach(function(el) {
    el.addEventListener('click', function() { switchChatSession(el.getAttribute('data-session-id')); });
  });
  listEl.querySelectorAll('.session-delete-btn').forEach(function(btn) {
    btn.addEventListener('click', function(ev) {
      deleteChatSession(btn.getAttribute('data-delete-session-id'), ev);
    });
  });
}

function setChatMode(mode) {
  var normalized = _normalizeChatMode(mode);
  var current = getSessionById(currentSessionId);
  var currentMode = _getSessionMode(current);
  if (!current) {
    var existingSid = findLastChatSessionIdByMode(normalized, '');
    if (existingSid) switchChatSession(existingSid);
    else createNewSession(normalized);
    return;
  }
  if (current && currentMode !== normalized) {
    saveCurrentSessionToStore();
    saveLastActiveChatSessionByModeToStorage(currentSessionId, currentMode);
    var targetSid = findLastChatSessionIdByMode(normalized, currentSessionId);
    if (targetSid) {
      switchChatSession(targetSid);
      return;
    }
    createNewSession(normalized);
    return;
  }
  updateChatModeUi(normalized);
  renderChatSessionList();
}

function bindChatModeSwitch() {
  if (window.__chatModeSwitchBound) return;
  window.__chatModeSwitchBound = true;
  document.querySelectorAll('.chat-mode-pill[data-chat-mode]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      if (btn.getAttribute('data-chat-mode') === CHAT_MODE_WORKSPACE && !CHAT_WORKSPACE_ENTRY_ENABLED) return;
      setChatMode(btn.getAttribute('data-chat-mode'));
    });
  });
  document.querySelectorAll('.workspace-category-tab[data-workspace-category]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      updateWorkspaceCategoryUi(btn.getAttribute('data-workspace-category'));
    });
  });
  bindChatMemoryScopeSelect();
}

function initChatSessions() {
  loadChatSessionsFromStorage();
  if (chatSessions.length === 0) {
    createNewSession(_getStoredChatMode());
    return;
  }
  // 登录后 currentSessionId 已被清空：优先恢复「上次正在看的会话」，再考虑带 poll_resume 的会话。
  // 若用全局最新 poll_resume_at 选会话，会话1 轮询会不断刷新时间戳，刷新页面后会盖住正在会话2 里做的任务。
  var lastSid = getLastActiveChatSessionIdFromStorage();
  var targetId = '';
  if (lastSid && chatSessions.some(function(s) { return String(s.id) === lastSid; })) {
    targetId = lastSid;
  } else {
    var resumeSid = _pickSessionIdNeedingPollResume(null, true);
    if (resumeSid && chatSessions.some(function(s) { return String(s.id) === resumeSid; })) {
      targetId = resumeSid;
    } else if (chatSessions[0]) {
      targetId = String(chatSessions[0].id);
    }
  }
  if (!targetId) {
    createNewSession(_getStoredChatMode());
    return;
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

function normalizeMarkdownLinkBreaks(text) {
  if (!text) return text;
  return String(text).replace(/\[([^\]\n]{1,160})\]\s*\n+\s*\((https?:\/\/[^\s<>"'`]+)\)/g, '[$1]($2)');
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
  var raw = normalizeMarkdownLinkBreaks(stripBackticksAroundUrls(text || ''));
  var escaped = raw.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  var markdownLinks = [];
  escaped = escaped.replace(/\[([^\]\n]{1,160})\]\s*\((https?:\/\/[^\s<>"'`]+)\)/g, function(match, label, rawUrl) {
    var url = String(rawUrl || '').trim();
    var rewritten = url.replace(/^https?:\/\/(?:localhost|127\.0\.0\.1):8000\/media\//, window.location.origin + '/media/');
    var token = '\u0000CHAT_LINK_' + markdownLinks.length + '_\u0000';
    markdownLinks.push(
      '<a href="' + rewritten + '" target="_blank" rel="noopener noreferrer">' + label + '</a>'
    );
    return token;
  });
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
  markdownLinks.forEach(function(html, idx) {
    result = result.replace('\u0000CHAT_LINK_' + idx + '_\u0000', html);
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
  var text = role === 'assistant' ? _compactAssistantReplyForDisplay(content, null) : (content || '');
  text = (text || '').trim() || '（无内容）';
  var html = linkifyText(text);
  div.innerHTML = '<div class="role">' + (role === 'user' ? '我' : '龙虾') + '</div>' + html;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}
var _toolNameLabels = {
  invoke_capability: '调用能力',
  save_asset: '保存素材',
  publish_content: '发布内容',
  publish_youtube_video: '上传到 YouTube',
  list_youtube_accounts: 'YouTube 账号列表',
  get_youtube_analytics: 'YouTube 数据',
  sync_youtube_analytics: '同步 YouTube 数据',
  list_meta_social_accounts: 'IG/FB 账号列表',
  publish_meta_social: '发布到 IG/FB',
  get_meta_social_data: '读取 IG/FB 数据',
  sync_meta_social_data: '同步 IG/FB 数据',
  get_social_report: '社交媒体报告',
  list_assets: '查看素材',
  list_publish_accounts: '查看账号',
  check_account_login: '检查登录',
  open_account_browser: '打开浏览器'
};
var _capabilityLabels = {
  'image.generate': '生成图片',
  'image.understand': '理解图片',
  'video.generate': '生成视频',
  'video.understand': '理解视频',
  'task.get_result': '查询结果',
  'media.edit': '素材编辑',
  'sutui.search_models': '搜索模型',
  'sutui.guide': '查询指南',
  'sutui.transfer_url': '转存链接',
  // 新名（首选）
  'comfly.daihuo': '爆款TVC 单段',
  'comfly.daihuo.pipeline': '爆款TVC 带货视频',
  // 老名兼容（历史 task 仍用老 capability_id 显示）
  'comfly.veo': '爆款TVC 单段',
  'comfly.veo.daihuo_pipeline': '爆款TVC 带货视频',
  'comfly.ecommerce.detail_pipeline': '电商详情页'
};
function _toolLabel(name, capId) {
  if (name === 'invoke_capability' && capId && _capabilityLabels[capId]) return _capabilityLabels[capId];
  return _toolNameLabels[name] || name;
}

function showChatTypingIndicator() {
  var container = document.getElementById('chatMessages');
  if (!container) return;
  removeChatTypingIndicator();
  var div = document.createElement('div');
  div.id = 'chatTypingIndicator';
  div.className = 'chat-msg assistant typing chat-typing-indicator chat-task-card';
  div.innerHTML =
    '<div class="role">\u9f99\u867e</div>' +
    '<div class="chat-typing-shell">' +
      '<div class="typing-status-row">' +
        '<div class="typing-status-main">' +
          '<span class="typing-status-badge">\u6b63\u5728\u6267\u884c</span>' +
          '<span class="typing-text typing-main">\u6b63\u5728\u5904\u7406\u2026</span>' +
        '</div>' +
        '<div class="typing-dots"><span></span><span></span><span></span></div>' +
      '</div>' +
      '<div class="typing-steps" id="chatTypingSteps"></div>' +
    '</div>';
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}
function appendChatTypingStep(text) {
  var steps = document.getElementById('chatTypingSteps');
  if (!steps) return;
  var line = document.createElement('div');
  line.className = 'typing-step';
  var bullet = document.createElement('span');
  bullet.className = 'typing-step-bullet';
  var body = document.createElement('span');
  body.className = 'typing-step-text';
  body.textContent = text;
  line.appendChild(bullet);
  line.appendChild(body);
  steps.appendChild(line);
  var container = document.getElementById('chatMessages');
  if (container) container.scrollTop = container.scrollHeight;
}
function upsertChatTypingStep(text) {
  var steps = document.getElementById('chatTypingSteps');
  if (!steps || !steps.lastElementChild) {
    appendChatTypingStep(text);
    return;
  }
  if (steps.lastElementChild.classList.contains('chat-generated-assets-preview')) {
    appendChatTypingStep(text);
    return;
  }
  var body = steps.lastElementChild.querySelector('.typing-step-text');
  if (body) body.textContent = text;
  var container = document.getElementById('chatMessages');
  if (container) container.scrollTop = container.scrollHeight;
}
function updateLastChatTypingStep(text) {
  var steps = document.getElementById('chatTypingSteps');
  if (!steps || !steps.lastElementChild) return;
  if (steps.lastElementChild.classList.contains('chat-generated-assets-preview')) {
    appendChatTypingStep(text);
    return;
  }
  var body = steps.lastElementChild.querySelector('.typing-step-text');
  if (body) body.textContent = text;
  var container = document.getElementById('chatMessages');
  if (container) container.scrollTop = container.scrollHeight;
}
function setChatTypingMainText(text) {
  var el = document.querySelector('#chatTypingIndicator .typing-text');
  if (el) el.textContent = text || '\u6b63\u5728\u5904\u7406\u2026';
}
function _formatTaskPollTypingLine(ev) {
  var msg = String(ev.message || '').trim();
  var line = msg || '正在查询生成结果…';
  if (ev.result_hint) {
    var h = String(ev.result_hint);
    if (line.indexOf(h) === -1) line += ' · ' + h;
  }
  return line;
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
      v.style.cssText = 'width:100%;max-height:' + maxHVid + ';border-radius:6px;background:#000;cursor:pointer;';
      v.addEventListener('dblclick', function() { if (v.requestFullscreen) v.requestFullscreen(); });
      mediaWrap.appendChild(v);
    } else {
      var link = document.createElement('a');
      var openUrl = savedAssetPrimaryHttpUrl(a) || u;
      link.href = openUrl;
      link.target = '_blank';
      link.rel = 'noopener';
      link.title = '点击在新标签页查看大图';
      var img = document.createElement('img');
      img.src = u;
      img.alt = assetId || '素材';
      img.style.cssText = 'width:100%;max-height:' + maxH + ';object-fit:contain;border-radius:6px;display:block;cursor:pointer;';
      link.appendChild(img);
      mediaWrap.appendChild(link);
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
      v.style.cssText = 'width:100%;max-height:' + maxHVid + ';border-radius:6px;background:#000;cursor:pointer;';
      v.addEventListener('dblclick', function() { if (v.requestFullscreen) v.requestFullscreen(); });
      mediaWrap.appendChild(v);
    } else {
      var link = document.createElement('a');
      link.href = u;
      link.target = '_blank';
      link.rel = 'noopener';
      link.title = '点击在新标签页查看大图';
      var img = document.createElement('img');
      img.src = u;
      img.alt = assetId || '素材';
      img.style.cssText = 'width:100%;max-height:' + maxH + ';object-fit:contain;border-radius:6px;display:block;cursor:pointer;';
      link.appendChild(img);
      mediaWrap.appendChild(link);
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
  var container = document.getElementById('chatMessages');
  if (container) {
    var list = container.querySelectorAll('.chat-typing-indicator');
    for (var i = 0; i < list.length; i++) {
      var n = list[i];
      if (n.parentNode) n.parentNode.removeChild(n);
    }
  }
  var el;
  while ((el = document.getElementById('chatTypingIndicator'))) {
    if (el.parentNode) el.parentNode.removeChild(el);
  }
}
/**
 * OpenClaw messaging 通道（如 weixin 扩展 sendMessage）失败时，LLM 会把 logger 错误回流成
 * "⚠️ ✉️ Message: `<url>` failed" 这种噪音；主对话用户根本没用 messaging，渲染前去掉避免误导。
 * 后端 _strip_dsml 已清理一道；这里是兜底（旧服务器、history 或 stream 中段都会经过）。
 */
function _stripOpenclawMessagingNoise(text) {
  var s = String(text == null ? '' : text);
  if (!s) return s;
  return s.replace(/(?:⚠️\s*)?(?:✉️\s*)?Message:\s*`?[^`\r\n]+`?\s+failed[^\r\n]*/ig, '').replace(/\n{3,}/g, '\n\n').trim();
}

/** 将 task.get_result 等大段 JSON 压成简短说明（素材仍以卡片展示） */
function _compactAssistantReplyForDisplay(fullText, savedAssets) {
  var t = _stripOpenclawMessagingNoise(fullText);
  if (!t) return t;
  var toParse = t;
  var prefix = '';
  var code = t.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (code) toParse = (code[1] || '').trim();
  else {
    var br0 = t.indexOf('{');
    var br1 = t.lastIndexOf('}');
    if (br0 >= 0 && br1 > br0) {
      if (br0 > 0) prefix = t.slice(0, br0).trim();
      toParse = t.slice(br0, br1 + 1);
    }
  }
  var obj;
  try {
    obj = JSON.parse(toParse);
  } catch (e) {
    return t;
  }
  if (!obj || typeof obj !== 'object' || obj.capability_id !== 'task.get_result' || typeof obj.result !== 'object')
    return t;
  var r = obj.result;
  var st = String(r.status || '').toLowerCase();
  var prompt = '';
  if (r.params && r.params.prompt) prompt = String(r.params.prompt).trim();
  else if (r.output && r.output.prompt) prompt = String(r.output.prompt).trim();
  var hasAssets = savedAssets && savedAssets.length;
  if (st === 'completed') {
    var short = hasAssets
      ? (prompt ? '已生成：' + (prompt.length > 120 ? prompt.slice(0, 120) + '…' : prompt) : '图片已生成，见上方素材卡片。')
      : (prompt ? '已完成：' + (prompt.length > 100 ? prompt.slice(0, 100) + '…' : prompt) : '任务已完成。');
    if (prefix) return prefix + '\n\n' + short;
    return short;
  }
  if (st === 'failed' || st === 'error' || st === 'cancelled') {
    return prefix ? prefix + '\n\n生成未成功。' : '生成未成功。';
  }
  return t;
}
function appendAssistantMessageReveal(fullText, savedAssets) {
  var container = document.getElementById('chatMessages');
  if (!container) return;
  var text = normalizeMarkdownLinkBreaks(_compactAssistantReplyForDisplay(fullText, savedAssets));
  text = (text || '').trim() || '\uFF08\u65E0\u5185\u5BB9\uFF09';
  var lines = text.split('\n');
  var div = document.createElement('div');
  div.className = 'chat-msg assistant chat-task-card';
  if (savedAssets && savedAssets.length) div.classList.add('has-assets');
  var roleDiv = document.createElement('div');
  roleDiv.className = 'role';
  roleDiv.textContent = '\u9f99\u867e';
  var bodyDiv = document.createElement('div');
  bodyDiv.className = 'chat-msg-body';
  div.appendChild(roleDiv);
  div.appendChild(bodyDiv);
  if (savedAssets && savedAssets.length) {
    var assetsWrap = document.createElement('div');
    assetsWrap.className = 'chat-generated-assets';
    savedAssets.forEach(function(a) {
      appendSavedAssetDom(assetsWrap, a, { compact: false });
    });
    bodyDiv.appendChild(assetsWrap);
  }
  appendAssistantActionRow(bodyDiv, !!(savedAssets && savedAssets.length));
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
function _legacyRenderChatAttachments_unused() {
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
function renderChatAttachments() {
  var container = document.getElementById('chatAttachments');
  if (!container) return;
  var composer = container.closest ? container.closest('.chat-composer-shell') : null;
  if (chatAttachmentIds.length === 0) {
    container.style.display = 'none';
    container.innerHTML = '';
    if (composer) composer.classList.remove('has-attachments');
    return;
  }
  container.style.display = 'flex';
  if (composer) composer.classList.add('has-attachments');
  container.innerHTML = '';
  chatAttachmentInfos.forEach(function(info, idx) {
    var wrap = document.createElement('div');
    wrap.className = 'chat-attach-item';

    var thumb = document.createElement('div');
    thumb.className = 'chat-attach-thumb';
    if (info.media_type === 'video') {
      var video = document.createElement('video');
      video.src = info.previewUrl || '';
      video.muted = true;
      video.playsInline = true;
      video.preload = 'metadata';
      thumb.appendChild(video);
    } else if (info.media_type === 'image') {
      var img = document.createElement('img');
      img.src = info.previewUrl || '';
      img.alt = '附件预览';
      thumb.appendChild(img);
    } else {
      var glyph = document.createElement('div');
      glyph.className = 'chat-attach-file-glyph';
      var strong = document.createElement('strong');
      strong.textContent = 'DOC';
      var span = document.createElement('span');
      span.textContent = info.filename || '文档附件';
      glyph.appendChild(strong);
      glyph.appendChild(span);
      thumb.appendChild(glyph);
    }
    wrap.appendChild(thumb);

    var meta = document.createElement('div');
    meta.className = 'chat-attach-meta';

    var type = document.createElement('div');
    type.className = 'chat-attach-type';
    type.textContent = info.media_type === 'video' ? '视频素材' : (info.media_type === 'image' ? '图片素材' : '文档附件');
    meta.appendChild(type);

    var hint = document.createElement('div');
    hint.className = 'chat-attach-hint';
    hint.textContent = info.media_type === 'video'
      ? '继续生成口播视频'
      : (info.media_type === 'image' ? '可直接做解图和生成' : '可读取内容并整理输出');
    meta.appendChild(hint);

    wrap.appendChild(meta);

    var action = document.createElement('button');
    action.type = 'button';
    action.className = 'chat-attach-action';
    action.textContent = info.media_type === 'video'
      ? '继续创作 ->'
      : (info.media_type === 'image' ? '解析图片 ->' : '读取文档 ->');
    action.addEventListener('click', function() {
      var input = document.getElementById('chatInput');
      if (!input) return;
      var suggestion = info.media_type === 'video'
        ? '请基于这个视频素材继续帮我生成更适合抖音带货的优化方案和新版本脚本。'
        : (info.media_type === 'image'
          ? '请先帮我解析这张图片里的商品、卖点和适合的电商内容方向。'
          : '请先读取这个文档的内容，帮我总结重点并输出结构清晰的结果。');
      var current = (input.value || '').trim();
      input.value = current ? (current + '\n' + suggestion) : suggestion;
      input.focus();
      try { input.setSelectionRange(input.value.length, input.value.length); } catch (e) {}
    });
    wrap.appendChild(action);

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
function addChatAttachment(assetId, mediaType, filename) {
  chatAttachmentIds.push(assetId);
  var normalizedType = (mediaType === 'video' || mediaType === 'image') ? mediaType : 'document';
  var info = { asset_id: assetId, media_type: normalizedType, previewUrl: '', filename: filename || '' };
  chatAttachmentInfos.push(info);
  if (normalizedType === 'document') {
    renderChatAttachments();
    return;
  }
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
/** 与 appendSavedAssetDom 的 data-pending-asset-dedup 一致，便于 save-url 完成后回写 UI */
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

/** 刷新后根据 localStorage 中的 poll_resume_task_id 自动续连 /chat/stream（仅轮询，不重复插入 user） */
var _maybeAutoResumeDebounceTimer = null;
/** 下一帧 maybeAutoResumeChatTaskPollRun 是否允许扫描全部会话（默认不再全局扫，避免刷新后误续查其它会话的旧 task） */
var _resumePollPickAnyOnce = false;
function maybeAutoResumeChatTaskPoll(opts) {
  if (opts && opts.pickAnySession) _resumePollPickAnyOnce = true;
  if (_maybeAutoResumeDebounceTimer != null) clearTimeout(_maybeAutoResumeDebounceTimer);
  _maybeAutoResumeDebounceTimer = setTimeout(function() {
    _maybeAutoResumeDebounceTimer = null;
    maybeAutoResumeChatTaskPollRun();
  }, 200);
}
function maybeAutoResumeChatTaskPollRun() {
  if (window._chatResumePollInFlight && !chatStreamAbortController) window._chatResumePollInFlight = false;
  if (window._chatResumePollInFlight) return;
  var chatBase = _chatStreamApiBase();
  if (!chatBase) return;
  var pickAny = _resumePollPickAnyOnce;
  _resumePollPickAnyOnce = false;
  var needSid = _pickSessionIdNeedingPollResume(String(currentSessionId || ''), pickAny);
  if (!needSid) return;
  if (String(currentSessionId || '') !== needSid) {
    switchChatSession(needSid);
  }
  var sid = String(currentSessionId || '');
  var s = getSessionById(sid);
  if (!s || !s.poll_resume_task_id) return;
  var age = Date.now() - (s.poll_resume_at || 0);
  if (age > _POLL_RESUME_MAX_AGE_MS) {
    clearSessionPollResume(sid);
    setSessionPending(sid, false);
    return;
  }
  resumeChatStreamForTaskPoll(sid, String(s.poll_resume_task_id).trim());
}

/** 刷新后续查时「速推 LLM」子下拉可能尚未异步加载，不能因无 value 放弃 /chat/stream */
function _resolveModelForResumePoll() {
  if (_isOnlineFixedSutuiChat()) return _onlineFixedChatModelPayload();
  var modelSel = document.getElementById('modelSelect');
  var model = modelSel ? (modelSel.value || '') : '';
  if (model !== 'sutui_aggregate') return model;
  var subSel = document.getElementById('sutuiModelSelect');
  var subId = '';
  if (subSel && subSel.value) subId = String(subSel.value).trim();
  else if (subSel && subSel.options && subSel.options.length)
    subId = String(subSel.options[0].value || '').trim();
  if (!subId) {
    try {
      subId = (localStorage.getItem('lobster_last_sutui_submodel') || '').trim();
    } catch (e) {}
  }
  if (!subId) subId = 'deepseek-chat';
  return 'sutui/' + subId;
}

function resumeChatStreamForTaskPoll(sid, taskId) {
  var tid = (taskId || '').trim();
  if (!tid) return;
  if (window._chatResumePollInFlight && !chatStreamAbortController) window._chatResumePollInFlight = false;
  if (window._chatResumePollInFlight) return;
  var chatBase = _chatStreamApiBase();
  if (!chatBase) return;
  abortActiveChatStream();
  window._chatResumePollInFlight = true;
  var session = getSessionById(sid);
  if (!session) {
    window._chatResumePollInFlight = false;
    return;
  }
  var model = _resolveModelForResumePoll();
  var body = {
    message: '（页面恢复后继续查询生成进度）',
    history: Array.isArray(session.messages) ? session.messages.slice() : [],
    session_id: sid,
    context_id: null,
    model: model || undefined,
    resume_task_poll_task_id: tid
  };
  var bodyStr = JSON.stringify(body);
  var headers = authHeaders();
  headers['Content-Type'] = 'application/json';
  applyChatMemoryScopeHeader(headers, session);
  var taskPollingCompleted = false;
  var videoGeneratedShown = false;
  var streamGeneratedAssets = [];
  var savedAssetUrls = {};
  var taskPollLocalSaveDone = false;
  var assetsPreviewAppended = false;
  var streamAbortedByUser = false;
  var resumeAbortReason = null;
  chatStreamSutuiSubmitted = false;
  var abortController = new AbortController();
  chatStreamAbortController = abortController;
  var resumeDeadlineTimer = window.setTimeout(function() {
    resumeAbortReason = 'deadline';
    try {
      abortController.abort();
    } catch (eDeadline) {}
  }, _RESUME_CHAT_STREAM_MAX_MS);
  refreshChatInputState();
  setSessionPending(sid, true);
  if (String(currentSessionId) === sid) {
    var cm = document.getElementById('chatMessages');
    if (!cm || !cm.querySelector('.chat-typing-indicator')) showChatTypingIndicator();
  }
  var streamKindResume = true;
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
            if (ev.type === 'capability_cost_confirm' && String(currentSessionId) === sid) {
              var capId = ev.capability_id || '';
              var cr = ev.estimated_credits;
              var note = (ev.estimate_note || '').trim();
              var secLeft = ev.timeout_seconds != null ? ev.timeout_seconds : 300;
              var cn = (cr != null && cr !== '') ? Number(cr) : NaN;
              var creditLine = !isNaN(cn) && cn > 0 ? String(cn) : (!isNaN(cn) && cn === 0 ? '0' : '未知');
              appendChatTypingStep('等待确认…');
              return openChatCapabilityCostConfirm({
                capability_id: capId,
                invoke_model: ev.invoke_model || '',
                credit_display: creditLine,
                note: note,
                timeout_seconds: secLeft
              }).then(function(userAccept) {
                var tokenBody = JSON.stringify({
                  confirm_token: String(ev.confirm_token || '').trim(),
                  accept: !!userAccept
                });
                var confirmHdr = Object.assign({}, headers, { 'Content-Type': 'application/json' });
                return fetch(chatBase + '/capabilities/confirm-invoke', {
                  method: 'POST',
                  headers: confirmHdr,
                  body: tokenBody
                }).catch(function() {
                  return fetch(chatBase + '/capabilities/confirm-invoke', {
                    method: 'POST',
                    headers: confirmHdr,
                    body: JSON.stringify({
                      confirm_token: String(ev.confirm_token || '').trim(),
                      accept: false
                    })
                  });
                }).then(function() {
                  if (String(currentSessionId) === sid) {
                    updateLastChatTypingStep(userAccept ? '已确认，继续执行…' : '已取消本次能力调用');
                  }
                  return reader.read().then(processChunk);
                });
              });
            } else if (ev.type === 'tool_start') {
              var _isAct = String(currentSessionId) === sid;
              if (ev.task_id) persistSessionPollResumeTaskId(sid, ev.task_id);
              if (ev.name === 'list_capabilities') {
                _saveSessionTypingState(sid, null, '正在查询可用能力…', 'append');
                if (_isAct) appendChatTypingStep('正在查询可用能力…');
              } else if (ev.phase === 'video_submit') {
                _saveSessionTypingState(sid, null, '正在提交视频生成任务…', 'append');
                if (_isAct) appendChatTypingStep('正在提交视频生成任务…');
              } else if (ev.phase === 'image_submit') {
                _saveSessionTypingState(sid, null, '正在提交图片生成任务…', 'append');
                if (_isAct) appendChatTypingStep('正在提交图片生成任务…');
              } else if (ev.phase === 'task_polling') {
                chatStreamSutuiSubmitted = true;
                if (_isAct) refreshChatInputState();
                var _pollMain = streamKindResume
                  ? '正在恢复并查询生成结果（约每 15 秒更新；超过 ' +
                      Math.round(_RESUME_CHAT_STREAM_MAX_MS / 60000) +
                      ' 分钟仍未结束将自动停止恢复，任务可能在后台继续）…'
                  : '正在查询生成结果（约每 15 秒自动更新）…';
                _saveSessionTypingState(sid, _pollMain, null, null);
                if (_isAct) setChatTypingMainText(_pollMain);
              } else {
                var _sStep = '正在 ' + _toolLabel(ev.name, ev.capability_id) + '…';
                _saveSessionTypingState(sid, null, _sStep, 'append');
                if (_isAct) appendChatTypingStep(_sStep);
              }
            } else if (ev.type === 'tool_end') {
              var _isAct2 = String(currentSessionId) === sid;
              if (ev.task_id) persistSessionPollResumeTaskId(sid, ev.task_id);
              if ((ev.phase === 'image_submit' || ev.phase === 'video_submit') && ev.success !== false) {
                chatStreamSutuiSubmitted = true;
                if (_isAct2) refreshChatInputState();
              }
              if (ev.phase === 'video_submit') {
                if (ev.success === false) {
                  var failPrev = (ev.preview || '').trim();
                  var _ft = failPrev ? ('✗ 提交未成功：' + (failPrev.length > 140 ? failPrev.slice(0, 140) + '…' : failPrev))
                    : '✗ 任务提交失败，请查看下方回复';
                  _saveSessionTypingState(sid, null, _ft, 'replace_last');
                  if (_isAct2) updateLastChatTypingStep(_ft);
                } else {
                  _saveSessionTypingState(sid, null, '✓ 任务已提交成功，正在查询生成结果…', 'replace_last');
                  if (_isAct2) updateLastChatTypingStep('✓ 任务已提交成功，正在查询生成结果…');
                }
              } else if (ev.phase === 'image_submit') {
                if (ev.success === false) {
                  var failPrevImg = (ev.preview || '').trim();
                  var _fti = failPrevImg ? ('✗ 提交未成功：' + (failPrevImg.length > 140 ? failPrevImg.slice(0, 140) + '…' : failPrevImg))
                    : '✗ 任务提交失败，请查看下方回复';
                  _saveSessionTypingState(sid, null, _fti, 'replace_last');
                  if (_isAct2) updateLastChatTypingStep(_fti);
                } else {
                  if (ev.saved_assets && ev.saved_assets.length) {
                    streamGeneratedAssets = ev.saved_assets.slice();
                    saveGeneratedAssetsToLocal(streamGeneratedAssets, savedAssetUrls);
                    _saveSessionTypingState(sid, null, '✓ 素材已生成', 'replace_last');
                    if (_isAct2) {
                      updateLastChatTypingStep('✓ 素材已生成');
                      if (!assetsPreviewAppended) {
                        assetsPreviewAppended = true;
                        appendChatGeneratedAssetsToTyping(streamGeneratedAssets);
                      }
                    }
                  } else {
                    _saveSessionTypingState(sid, null, '✓ 任务已提交成功，正在查询生成结果…', 'replace_last');
                    if (_isAct2) updateLastChatTypingStep('✓ 任务已提交成功，正在查询生成结果…');
                  }
                }
              } else if (ev.phase === 'task_polling') {
                var stillInProgress = ev.in_progress === true;
                if (!stillInProgress && ev.understand_text) {
                  taskPollingCompleted = true;
                  _saveSessionTypingState(sid, null, '✓ 理解完成', 'replace_last');
                  if (_isAct2) updateLastChatTypingStep('✓ 理解完成');
                  _saveSessionTypingState(sid, null, null, null);
                } else if (!stillInProgress) {
                  taskPollingCompleted = true;
                  if (ev.saved_assets && ev.saved_assets.length) streamGeneratedAssets = ev.saved_assets;
                  if (streamGeneratedAssets.length && !taskPollLocalSaveDone) {
                    taskPollLocalSaveDone = true;
                    saveGeneratedAssetsToLocal(streamGeneratedAssets, savedAssetUrls);
                  }
                  if (!videoGeneratedShown) {
                    _saveSessionTypingState(sid, null, '✓ 素材已生成', 'replace_last');
                    if (_isAct2) updateLastChatTypingStep('✓ 素材已生成');
                    videoGeneratedShown = true;
                  }
                  if (streamGeneratedAssets.length && _isAct2 && !assetsPreviewAppended) {
                    assetsPreviewAppended = true;
                    appendChatGeneratedAssetsToTyping(streamGeneratedAssets);
                  }
                  _saveSessionTypingState(sid, null, null, null);
                  /* 不强制改主行：避免「撰写」与后续 save/list 步骤语义冲突；done 时会清指示器 */
                } else if (!taskPollingCompleted) {
                  _saveSessionTypingState(sid, null, '正在查询生成结果…', 'replace_last');
                  if (_isAct2) updateLastChatTypingStep('正在查询生成结果…');
                }
              } else if (ev.phase === 'understand_submit') {
                _saveSessionTypingState(sid, null, '✓ 已提交，正在获取理解结果…', 'replace_last');
                if (_isAct2) updateLastChatTypingStep('✓ 已提交，正在获取理解结果…');
              } else if (ev.name === 'list_capabilities') {
                _saveSessionTypingState(sid, null, '✓ 能力列表已获取', 'replace_last');
                if (_isAct2) updateLastChatTypingStep('✓ 能力列表已获取');
              } else {
                if (ev.success !== false && ev.saved_assets && ev.saved_assets.length) {
                  streamGeneratedAssets = ev.saved_assets.slice();
                  saveGeneratedAssetsToLocal(streamGeneratedAssets, savedAssetUrls);
                  _saveSessionTypingState(sid, null, '✓ 素材已生成', 'replace_last');
                  if (_isAct2) {
                    if (!assetsPreviewAppended) {
                      assetsPreviewAppended = true;
                      appendChatGeneratedAssetsToTyping(streamGeneratedAssets);
                    }
                    updateLastChatTypingStep('✓ 素材已生成');
                  }
                } else {
                  var _endT = '✓ ' + _toolLabel(ev.name, ev.capability_id) + ' 完成';
                  _saveSessionTypingState(sid, null, _endT, 'replace_last');
                  if (_isAct2) updateLastChatTypingStep(_endT);
                }
              }
            } else if (ev.type === 'task_poll') {
              if (ev.task_id) persistSessionPollResumeTaskId(sid, ev.task_id);
              if (!ev.message || taskPollingCompleted) continue;
              var _pollLine = _formatTaskPollTypingLine(ev);
              _saveSessionTypingState(sid, _pollLine, null, null);
              if (String(currentSessionId) === sid) setChatTypingMainText(_pollLine);
            } else if (ev.type === 'status' && ev.message) {
              if (ev.message === '正在请模型撰写回复…' || ev.message === '正在生成回复…') continue;
              _saveSessionTypingState(sid, null, ev.message, 'replace_last');
              if (String(currentSessionId) === sid) maybeUpdateWorkspaceStatusFromMessage(ev.message);
              if (String(currentSessionId) === sid) upsertChatTypingStep(ev.message);
            } else if (ev.type === 'done') {
              _clearSessionTypingState(sid);
              return Promise.resolve(ev);
            }
          } catch (e) {}
        }
        return reader.read().then(processChunk);
      }
      return reader.read().then(processChunk);
    })
    .then(function(doneEv) {
      _clearSessionTypingState(sid);
      clearSessionPollResume(sid);
      var targetSession = getSessionById(sid);
      if (!targetSession) return;
      if (String(currentSessionId) === sid) removeChatTypingIndicator();
      var reply = _normalizeAssistantStreamReply(
        (doneEv && doneEv.reply) ? doneEv.reply : (doneEv ? '' : '请求异常结束')
      );
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
        if (resumeAbortReason === 'deadline') {
          _clearSessionTypingState(sid);
          clearSessionPollResume(sid);
          var targetDead = getSessionById(sid);
          var tdetail =
            '恢复查询已超时自动停止（任务可能仍在后台）。请重新发消息续查，或到素材库查看是否已生成。';
          _pushAssistantErrorIfNotDuplicate(targetDead, tdetail);
          if (String(currentSessionId) === sid) {
            removeChatTypingIndicator();
            appendChatMessage('assistant', '错误：' + tdetail);
          }
          saveChatSessionsToStorage();
          return;
        }
        streamAbortedByUser = true;
        setSessionPending(sid, false);
        if (String(currentSessionId) === sid) {
          setChatTypingMainText('已取消');
          appendChatTypingStep('已终止恢复查询；刷新页面可再次续查');
          setTimeout(removeChatTypingIndicator, 1500);
        }
        saveChatSessionsToStorage();
        return;
      }
      var targetSession = getSessionById(sid);
      var raw0 = _rawChatStreamError(e);
      var msg = _normalizeChatStreamErrorMessage(raw0 || '请稍后重试');
      var transient = _isTransientResumeStreamFailure(e, raw0, msg);
      var httpErr = e && e.status != null;
      if (httpErr && !transient) clearSessionPollResume(sid);
      var addedErr = false;
      if (!transient) addedErr = _pushAssistantErrorIfNotDuplicate(targetSession, msg);
      if (String(currentSessionId) === sid) {
        removeChatTypingIndicator();
        if (!transient && addedErr) appendChatMessage('assistant', '错误：' + msg);
        if (targetSession) chatHistory = targetSession.messages.slice();
      }
      saveChatSessionsToStorage();
    })
    .finally(function() {
      if (resumeDeadlineTimer != null) {
        try {
          clearTimeout(resumeDeadlineTimer);
        } catch (eClr) {}
        resumeDeadlineTimer = null;
      }
      window._chatResumePollInFlight = false;
      if (chatStreamAbortController === abortController) chatStreamAbortController = null;
      chatStreamSutuiSubmitted = false;
      refreshChatInputState();
      setSessionPending(sid, false);
      if (!streamAbortedByUser && resumeAbortReason !== 'deadline' && String(currentSessionId) === sid)
        removeChatTypingIndicator();
    });
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
  if (_isH5MirrorSession(session)) {
    syncH5ChatMirrorSession(true);
    return;
  }
  if (isSessionPending(sid)) return;
  var sessionMode = _getSessionMode(session);

  var chatBase = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? String(LOCAL_API_BASE).replace(/\/$/, '') : '';
  if (!chatBase) {
    alert('对话功能须连接本机 lobster_online 后端。请运行 backend/run.py 后用该后端地址打开页面，或在页面中设置 window.__LOCAL_API_BASE。');
    return;
  }

  input.value = '';
  var attachIds = sessionMode === CHAT_MODE_WORKSPACE ? [] : mergeUserMessageAssetIds(chatAttachmentIds.slice(), message);
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
  abortActiveChatStream();
  setSessionPending(sid, true);
  showChatTypingIndicator();
  var historyForRequest = session.messages.slice(0, -1);
  var model = '';
  if (_isOnlineFixedSutuiChat()) {
    model = _onlineFixedChatModelPayload();
  } else {
    var modelSel = document.getElementById('modelSelect');
    model = modelSel ? (modelSel.value || '') : '';
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
  }
  var body = {
    message: message,
    history: historyForRequest,
    session_id: sid,
    context_id: null,
    model: model || undefined
  };
  var directChk = document.getElementById('chatDirectLlmCheck');
  if (sessionMode !== CHAT_MODE_WORKSPACE && directChk && directChk.checked) body.direct_llm = true;
  if (attachIds.length) body.attachment_asset_ids = attachIds;
  var bodyStr = JSON.stringify(body);
  var headers = authHeaders();
  headers['Content-Type'] = 'application/json';
  applyChatMemoryScopeHeader(headers, session);
  var taskPollingCompleted = false;
  var videoGeneratedShown = false;
  var streamGeneratedAssets = [];
  var savedAssetUrls = {};
  var taskPollLocalSaveDone = false;
  var assetsPreviewAppended = false;
  var streamAbortedByUser = false;
  chatStreamSutuiSubmitted = false;
  var abortController = new AbortController();
  chatStreamAbortController = abortController;
  refreshChatInputState();
  var streamKindResume = false;
  var streamPath = sessionMode === CHAT_MODE_WORKSPACE ? '/chat/workbench/stream' : '/chat/stream';
  try {
    console.info('[chat] stream request', {
      base: chatBase,
      path: streamPath,
      model: body.model || '',
      edition: typeof EDITION !== 'undefined' ? EDITION : '',
      apiBase: typeof API_BASE !== 'undefined' ? API_BASE : '',
      localApiBase: typeof LOCAL_API_BASE !== 'undefined' ? LOCAL_API_BASE : ''
    });
  } catch (eLog) {}
  fetch(chatBase + streamPath, { method: 'POST', headers: headers, body: bodyStr, signal: abortController.signal })
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
            if (ev.type === 'capability_cost_confirm' && String(currentSessionId) === sid) {
              var capId = ev.capability_id || '';
              var cr = ev.estimated_credits;
              var note = (ev.estimate_note || '').trim();
              var secLeft = ev.timeout_seconds != null ? ev.timeout_seconds : 300;
              var cn = (cr != null && cr !== '') ? Number(cr) : NaN;
              var creditLine = !isNaN(cn) && cn > 0 ? String(cn) : (!isNaN(cn) && cn === 0 ? '0' : '未知');
              appendChatTypingStep('等待确认…');
              return openChatCapabilityCostConfirm({
                capability_id: capId,
                invoke_model: ev.invoke_model || '',
                credit_display: creditLine,
                note: note,
                timeout_seconds: secLeft
              }).then(function(userAccept) {
                var tokenBody = JSON.stringify({
                  confirm_token: String(ev.confirm_token || '').trim(),
                  accept: !!userAccept
                });
                var confirmHdr = Object.assign({}, headers, { 'Content-Type': 'application/json' });
                return fetch(chatBase + '/capabilities/confirm-invoke', {
                  method: 'POST',
                  headers: confirmHdr,
                  body: tokenBody
                }).catch(function() {
                  return fetch(chatBase + '/capabilities/confirm-invoke', {
                    method: 'POST',
                    headers: confirmHdr,
                    body: JSON.stringify({
                      confirm_token: String(ev.confirm_token || '').trim(),
                      accept: false
                    })
                  });
                }).then(function() {
                  if (String(currentSessionId) === sid) {
                    updateLastChatTypingStep(userAccept ? '已确认，继续执行…' : '已取消本次能力调用');
                  }
                  return reader.read().then(processChunk);
                });
              });
            } else if (ev.type === 'tool_start') {
              var _isAct = String(currentSessionId) === sid;
              if (ev.task_id) persistSessionPollResumeTaskId(sid, ev.task_id);
              if (ev.name === 'list_capabilities') {
                _saveSessionTypingState(sid, null, '正在查询可用能力…', 'append');
                if (_isAct) appendChatTypingStep('正在查询可用能力…');
              } else if (ev.phase === 'video_submit') {
                _saveSessionTypingState(sid, null, '正在提交视频生成任务…', 'append');
                if (_isAct) appendChatTypingStep('正在提交视频生成任务…');
              } else if (ev.phase === 'image_submit') {
                _saveSessionTypingState(sid, null, '正在提交图片生成任务…', 'append');
                if (_isAct) appendChatTypingStep('正在提交图片生成任务…');
              } else if (ev.phase === 'task_polling') {
                chatStreamSutuiSubmitted = true;
                if (_isAct) refreshChatInputState();
                var _pollMain = streamKindResume
                  ? '正在恢复并查询生成结果（约每 15 秒更新；超过 ' +
                      Math.round(_RESUME_CHAT_STREAM_MAX_MS / 60000) +
                      ' 分钟仍未结束将自动停止恢复，任务可能在后台继续）…'
                  : '正在查询生成结果（约每 15 秒自动更新）…';
                _saveSessionTypingState(sid, _pollMain, null, null);
                if (_isAct) setChatTypingMainText(_pollMain);
              } else {
                var _sStep = '正在 ' + _toolLabel(ev.name, ev.capability_id) + '…';
                _saveSessionTypingState(sid, null, _sStep, 'append');
                if (_isAct) appendChatTypingStep(_sStep);
              }
            } else if (ev.type === 'tool_end') {
              var _isAct2 = String(currentSessionId) === sid;
              if (ev.task_id) persistSessionPollResumeTaskId(sid, ev.task_id);
              if ((ev.phase === 'image_submit' || ev.phase === 'video_submit') && ev.success !== false) {
                chatStreamSutuiSubmitted = true;
                if (_isAct2) refreshChatInputState();
              }
              if (ev.phase === 'video_submit') {
                if (ev.success === false) {
                  var failPrev = (ev.preview || '').trim();
                  var _ft = failPrev ? ('✗ 提交未成功：' + (failPrev.length > 140 ? failPrev.slice(0, 140) + '…' : failPrev))
                    : '✗ 任务提交失败，请查看下方回复';
                  _saveSessionTypingState(sid, null, _ft, 'replace_last');
                  if (_isAct2) updateLastChatTypingStep(_ft);
                } else {
                  _saveSessionTypingState(sid, null, '✓ 任务已提交成功，正在查询生成结果…', 'replace_last');
                  if (_isAct2) updateLastChatTypingStep('✓ 任务已提交成功，正在查询生成结果…');
                }
              } else if (ev.phase === 'image_submit') {
                if (ev.success === false) {
                  var failPrevImg = (ev.preview || '').trim();
                  var _fti = failPrevImg ? ('✗ 提交未成功：' + (failPrevImg.length > 140 ? failPrevImg.slice(0, 140) + '…' : failPrevImg))
                    : '✗ 任务提交失败，请查看下方回复';
                  _saveSessionTypingState(sid, null, _fti, 'replace_last');
                  if (_isAct2) updateLastChatTypingStep(_fti);
                } else {
                  if (ev.saved_assets && ev.saved_assets.length) {
                    streamGeneratedAssets = ev.saved_assets.slice();
                    saveGeneratedAssetsToLocal(streamGeneratedAssets, savedAssetUrls);
                    _saveSessionTypingState(sid, null, '✓ 素材已生成', 'replace_last');
                    if (_isAct2) {
                      updateLastChatTypingStep('✓ 素材已生成');
                      if (!assetsPreviewAppended) {
                        assetsPreviewAppended = true;
                        appendChatGeneratedAssetsToTyping(streamGeneratedAssets);
                      }
                    }
                  } else {
                    _saveSessionTypingState(sid, null, '✓ 任务已提交成功，正在查询生成结果…', 'replace_last');
                    if (_isAct2) updateLastChatTypingStep('✓ 任务已提交成功，正在查询生成结果…');
                  }
                }
              } else if (ev.phase === 'task_polling') {
                var stillInProgress = ev.in_progress === true;
                if (!stillInProgress && ev.understand_text) {
                  taskPollingCompleted = true;
                  _saveSessionTypingState(sid, null, '✓ 理解完成', 'replace_last');
                  if (_isAct2) updateLastChatTypingStep('✓ 理解完成');
                  _saveSessionTypingState(sid, null, null, null);
                } else if (!stillInProgress) {
                  taskPollingCompleted = true;
                  if (ev.saved_assets && ev.saved_assets.length) streamGeneratedAssets = ev.saved_assets;
                  if (streamGeneratedAssets.length && !taskPollLocalSaveDone) {
                    taskPollLocalSaveDone = true;
                    saveGeneratedAssetsToLocal(streamGeneratedAssets, savedAssetUrls);
                  }
                  if (!videoGeneratedShown) {
                    _saveSessionTypingState(sid, null, '✓ 素材已生成', 'replace_last');
                    if (_isAct2) updateLastChatTypingStep('✓ 素材已生成');
                    videoGeneratedShown = true;
                  }
                  if (streamGeneratedAssets.length && _isAct2 && !assetsPreviewAppended) {
                    assetsPreviewAppended = true;
                    appendChatGeneratedAssetsToTyping(streamGeneratedAssets);
                  }
                  _saveSessionTypingState(sid, null, null, null);
                  /* 不强制改主行：避免「撰写」与后续 save/list 步骤语义冲突；done 时会清指示器 */
                } else if (!taskPollingCompleted) {
                  _saveSessionTypingState(sid, null, '正在查询生成结果…', 'replace_last');
                  if (_isAct2) updateLastChatTypingStep('正在查询生成结果…');
                }
              } else if (ev.phase === 'understand_submit') {
                _saveSessionTypingState(sid, null, '✓ 已提交，正在获取理解结果…', 'replace_last');
                if (_isAct2) updateLastChatTypingStep('✓ 已提交，正在获取理解结果…');
              } else if (ev.name === 'list_capabilities') {
                _saveSessionTypingState(sid, null, '✓ 能力列表已获取', 'replace_last');
                if (_isAct2) updateLastChatTypingStep('✓ 能力列表已获取');
              } else {
                if (ev.success !== false && ev.saved_assets && ev.saved_assets.length) {
                  streamGeneratedAssets = ev.saved_assets.slice();
                  saveGeneratedAssetsToLocal(streamGeneratedAssets, savedAssetUrls);
                  _saveSessionTypingState(sid, null, '✓ 素材已生成', 'replace_last');
                  if (_isAct2) {
                    if (!assetsPreviewAppended) {
                      assetsPreviewAppended = true;
                      appendChatGeneratedAssetsToTyping(streamGeneratedAssets);
                    }
                    updateLastChatTypingStep('✓ 素材已生成');
                  }
                } else {
                  var _endT = '✓ ' + _toolLabel(ev.name, ev.capability_id) + ' 完成';
                  _saveSessionTypingState(sid, null, _endT, 'replace_last');
                  if (_isAct2) updateLastChatTypingStep(_endT);
                }
              }
            } else if (ev.type === 'task_poll') {
              if (ev.task_id) persistSessionPollResumeTaskId(sid, ev.task_id);
              if (!ev.message || taskPollingCompleted) continue;
              var _pollLine = _formatTaskPollTypingLine(ev);
              _saveSessionTypingState(sid, _pollLine, null, null);
              if (String(currentSessionId) === sid) setChatTypingMainText(_pollLine);
            } else if (ev.type === 'status' && ev.message) {
              if (ev.message === '正在请模型撰写回复…' || ev.message === '正在生成回复…') continue;
              _saveSessionTypingState(sid, null, ev.message, sessionMode === CHAT_MODE_WORKSPACE ? 'replace_last' : 'append');
              if (String(currentSessionId) === sid) maybeUpdateWorkspaceStatusFromMessage(ev.message);
              if (String(currentSessionId) === sid) {
                if (sessionMode === CHAT_MODE_WORKSPACE) upsertChatTypingStep(ev.message);
                else appendChatTypingStep(ev.message);
              }
            } else if (ev.type === 'done') {
              _clearSessionTypingState(sid);
              return Promise.resolve(ev);
            }
          } catch (e) {}
        }
        return reader.read().then(processChunk);
      }
      return reader.read().then(processChunk);
    })
    .then(function(doneEv) {
      _clearSessionTypingState(sid);
      clearSessionPollResume(sid);
      var targetSession = getSessionById(sid);
      if (!targetSession) return;
      if (String(currentSessionId) === sid) removeChatTypingIndicator();
      var reply = _normalizeAssistantStreamReply(
        (doneEv && doneEv.reply) ? doneEv.reply : (doneEv ? '' : '请求异常结束')
      );
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
        if (!chatStreamSutuiSubmitted) clearSessionPollResume(sid);
        if (String(currentSessionId) === sid) {
          setChatTypingMainText('已取消');
          appendChatTypingStep('已终止当前任务，可重新发送消息继续');
          setTimeout(removeChatTypingIndicator, 1500);
        }
        saveChatSessionsToStorage();
        return;
      }
      var targetSession = getSessionById(sid);
      var raw0 = _rawChatStreamError(e);
      var msg = _normalizeChatStreamErrorMessage(raw0 || '请稍后重试');
      var addedErr = _pushAssistantErrorIfNotDuplicate(targetSession, msg);
      if (String(currentSessionId) === sid) {
        removeChatTypingIndicator();
        if (addedErr) appendChatMessage('assistant', '错误：' + msg);
        if (targetSession) chatHistory = targetSession.messages.slice();
      }
      saveChatSessionsToStorage();
    })
    .finally(function() {
      if (chatStreamAbortController === abortController) chatStreamAbortController = null;
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
            if (d && d.asset_id) addChatAttachment(d.asset_id, d.media_type || 'image', d.filename || file.name || '');
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

function syncChatWorkspaceState() {
  var workspace = document.getElementById('chatWorkspace');
  var messages = document.getElementById('chatMessages');
  var main = workspace ? workspace.closest('.chat-main') : null;
  var shell = workspace ? workspace.closest('.chat-area-wrap') : null;
  if (!workspace || !messages) return;
  var isEmpty = messages.children.length === 0;
  workspace.classList.toggle('is-empty', isEmpty);
  if (main) main.classList.toggle('is-home-empty', isEmpty);
  if (shell) shell.classList.toggle('is-home-empty', isEmpty);
}

function bindChatHomeActions() {
  if (window.__chatHomeActionsBound) return;
  window.__chatHomeActionsBound = true;
  document.addEventListener('click', function(e) {
    var modeBtn = e.target.closest('[data-chat-home-mode]');
    if (modeBtn) {
      if (modeBtn.getAttribute('data-chat-home-mode') === CHAT_MODE_WORKSPACE && !CHAT_WORKSPACE_ENTRY_ENABLED) return;
      setChatMode(modeBtn.getAttribute('data-chat-home-mode'));
      return;
    }
    var quickModeBtn = e.target.closest('[data-chat-quick-mode]');
    if (quickModeBtn) {
      setChatQuickMode(quickModeBtn.getAttribute('data-chat-quick-mode'), { prefill: true });
      return;
    }
    var defaultChatBtn = e.target.closest('[data-chat-open-default]');
    if (defaultChatBtn) {
      openDefaultChatSession();
      return;
    }
    var h5SyncBtn = e.target.closest('[data-h5-chat-sync]');
    if (h5SyncBtn) {
      openH5ChatMirrorSession();
      return;
    }
    var hiddenViewBtn = e.target.closest('[data-open-hidden-view]');
    if (hiddenViewBtn) {
      var hiddenView = hiddenViewBtn.getAttribute('data-open-hidden-view');
      if (hiddenView === 'hifly-digital-human') {
        openHiddenWorkspaceFallback(hiddenView);
      } else if (typeof window._openHiddenWorkspaceView === 'function') {
        window._openHiddenWorkspaceView(hiddenView);
      } else {
        openHiddenWorkspaceFallback(hiddenView);
      }
      return;
    }
    var jumpBtn = e.target.closest('[data-jump-view]');
    if (jumpBtn) {
      var view = jumpBtn.getAttribute('data-jump-view');
      var navBtn = document.querySelector('.nav-left-item[data-view="' + view + '"]');
      if (navBtn) navBtn.click();
      return;
    }
    var promptBtn = e.target.closest('[data-chat-prompt]');
    if (promptBtn) {
      if (typeof _isH5MirrorSession === 'function' && _isH5MirrorSession(getSessionById(currentSessionId)) && typeof openDefaultChatSession === 'function') {
        openDefaultChatSession();
      }
      var input = document.getElementById('chatInput');
      if (!input) return;
      input.value = promptBtn.getAttribute('data-chat-prompt') || '';
      input.focus();
      if (typeof input.setSelectionRange === 'function') {
        var cursor = input.value.length;
        input.setSelectionRange(cursor, cursor);
      }
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  });
}

function bindChatQuickModeActions() {
  if (window.__chatQuickModeActionsBound) return;
  window.__chatQuickModeActionsBound = true;
  var closeBtn = document.getElementById('chatQuickModeCloseBtn');
  if (closeBtn) {
    closeBtn.addEventListener('click', function() {
      setChatQuickMode('', { prefill: false });
    });
  }
  var advancedBtn = document.getElementById('chatQuickModeAdvancedBtn');
  if (advancedBtn) {
    advancedBtn.addEventListener('click', function() {
      var hiddenView = advancedBtn.getAttribute('data-open-hidden-view');
      if (!hiddenView) return;
      if (hiddenView === 'hifly-digital-human') {
        openHiddenWorkspaceFallback(hiddenView);
      } else if (typeof window._openHiddenWorkspaceView === 'function') {
        window._openHiddenWorkspaceView(hiddenView);
      } else {
        openHiddenWorkspaceFallback(hiddenView);
      }
    });
  }
}

function openHiddenWorkspaceFallback(view) {
  var target = String(view || '').trim();
  if (!target) return;
  try { location.hash = target; } catch (e) {}
  document.querySelectorAll('.nav-left-item').forEach(function(b) { b.classList.remove('active'); });
  document.querySelectorAll('.content-block').forEach(function(p) { p.classList.remove('visible'); });
  var contentEl = document.getElementById('content-' + target);
  if (contentEl) contentEl.classList.add('visible');
  if (target === 'hifly-digital-human' && typeof window.initHiflyDigitalHumanView === 'function') {
    window.initHiflyDigitalHumanView();
  } else if (target === 'viral-video-remix' && typeof window.initViralVideoRemixView === 'function') {
    window.initViralVideoRemixView();
  } else if (target === 'seedance-tvc-studio' && typeof window.initSeedanceTvcStudioView === 'function') {
    window.initSeedanceTvcStudioView();
  } else if (target === 'image-composer-studio' && typeof window.initImageComposerStudioView === 'function') {
    window.initImageComposerStudioView();
  } else if (target === 'ecommerce-detail-studio' && typeof window.initEcommerceDetailStudioView === 'function') {
    window.initEcommerceDetailStudioView();
  }
}

function initChatWorkspaceShell() {
  bindChatModeSwitch();
  syncChatWorkspaceState();
  bindChatHomeActions();
  bindChatQuickModeActions();
  var messages = document.getElementById('chatMessages');
  if (!messages || typeof MutationObserver === 'undefined') return;
  var observer = new MutationObserver(syncChatWorkspaceState);
  observer.observe(messages, { childList: true, subtree: false });
}

initChatWorkspaceShell();

function appendAssistantActionRow(parent, hasAssets) {
  if (!parent) return;
  var wrap = document.createElement('div');
  wrap.className = 'chat-result-actions';
  var actions = hasAssets
    ? [
        { label: '再做一版', prompt: '基于刚才的结果，再给我一版不同风格但用途相同的方案。', primary: true },
        { label: '继续优化', prompt: '基于刚才的结果继续优化，提升转化感和完成度。' },
        { label: '去发布中心', jump: 'publish' }
      ]
    : [
        { label: '继续细化', prompt: '基于刚才的内容继续细化，并给我更可执行的版本。', primary: true },
        { label: '看技能商店', jump: 'skill-store' },
        { label: '打开系统配置', jump: 'sys-config' }
      ];
  actions.forEach(function(action) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'chat-result-action' + (action.primary ? ' is-primary' : '');
    btn.textContent = action.label;
    if (action.prompt) btn.setAttribute('data-chat-prompt', action.prompt);
    if (action.jump) btn.setAttribute('data-jump-view', action.jump);
    wrap.appendChild(btn);
  });
  parent.appendChild(wrap);
}
