(function() {
  var state = {
    accounts: [],
    activeAccountId: '',
    peers: [],
    activePeerId: '',
    activePeer: null,
    messages: [],
    contacts: [],
    groups: [],
    activeGroupKey: '',
    groupMembers: [],
    tasks: [],
    strategy: null,
    autoReply: null,
    autoReplyMemoryDocs: [],
    autoReplyBusy: false,
    driver: null,
    lastDiagnostic: null,
    sendFiles: [],
    momentsFiles: [],
    momentsContentRecord: null,
    momentsSubmitting: false,
    contactSelected: {},
    contactPickerContacts: [],
    groupInviteContactSearch: '',
    pagination: {
      peers: { page: 1, limit: 100, total: 0, keyword: '', loading: false },
      contacts: { page: 1, limit: 100, total: 0, keyword: '', loading: false },
      groups: { page: 1, limit: 100, total: 0, keyword: '', loading: false },
      groupMembers: { page: 1, limit: 100, total: 0, keyword: '', loading: false },
      contactPicker: { page: 1, limit: 100, total: 0, keyword: '', loading: false },
      tasks: { page: 1, limit: 80, total: 0, keyword: '', loading: false }
    },
    messageHistory: { limit: 100, total: 0, loading: false },
    requestSeq: {},
    tab: 'messages'
  };

  function $(id) { return document.getElementById(id); }

  function debounce(fn, wait) {
    var timer = null;
    return function() {
      var args = arguments;
      var context = this;
      window.clearTimeout(timer);
      timer = window.setTimeout(function() { fn.apply(context, args); }, wait || 250);
    };
  }

  function pageCount(meta) {
    return Math.max(1, Math.ceil(Number(meta.total || 0) / Math.max(1, Number(meta.limit || 100))));
  }

  function resetPagination(key) {
    var meta = state.pagination[key];
    if (!meta) return;
    meta.page = 1;
    meta.total = 0;
    meta.keyword = '';
    meta.loading = false;
  }

  function resetAllPagination() {
    ['peers', 'contacts', 'groups', 'groupMembers', 'contactPicker', 'tasks', 'messages'].forEach(beginRequest);
    Object.keys(state.pagination).forEach(resetPagination);
    state.contactPickerContacts = [];
    state.messageHistory = { limit: 100, total: 0, loading: false };
  }

  function beginRequest(key) {
    state.requestSeq[key] = Number(state.requestSeq[key] || 0) + 1;
    return state.requestSeq[key];
  }

  function isCurrentRequest(key, sequence) {
    return state.requestSeq[key] === sequence;
  }

  function paginationContainerId(key) {
    return {
      peers: 'nativeWechatPeerPagination',
      contacts: 'nativeWechatContactPagination',
      groups: 'nativeWechatGroupPagination',
      groupMembers: 'nativeWechatGroupMemberPagination',
      contactPicker: 'nativeWechatContactPickerPagination',
      tasks: 'nativeWechatTaskPagination'
    }[key] || '';
  }

  function renderPagination(key) {
    var node = $(paginationContainerId(key));
    var meta = state.pagination[key];
    if (!node || !meta) return;
    var total = Number(meta.total || 0);
    var pages = pageCount(meta);
    var page = Math.min(Math.max(1, Number(meta.page || 1)), pages);
    var start = total ? ((page - 1) * meta.limit + 1) : 0;
    var end = total ? Math.min(total, page * meta.limit) : 0;
    var disabledPrev = page <= 1 || meta.loading;
    var disabledNext = page >= pages || meta.loading;
    var summary = total ? ('共 ' + total + ' 条，本页 ' + start + '-' + end) : (meta.keyword ? '没有匹配数据' : '共 0 条');
    node.innerHTML = '<span>' + summary + '</span>' +
      '<div class="native-wechat-pagination-actions">' +
        '<button type="button" class="native-wechat-page-btn" data-native-page-list="' + key + '" data-native-page-action="first"' + (disabledPrev ? ' disabled' : '') + '>首页</button>' +
        '<button type="button" class="native-wechat-page-btn" data-native-page-list="' + key + '" data-native-page-action="prev"' + (disabledPrev ? ' disabled' : '') + '>上一页</button>' +
        '<span class="native-wechat-page-current">第 ' + page + ' / ' + pages + ' 页</span>' +
        '<button type="button" class="native-wechat-page-btn" data-native-page-list="' + key + '" data-native-page-action="next"' + (disabledNext ? ' disabled' : '') + '>下一页</button>' +
        '<button type="button" class="native-wechat-page-btn" data-native-page-list="' + key + '" data-native-page-action="last"' + (disabledNext ? ' disabled' : '') + '>末页</button>' +
      '</div>';
  }

  function esc(text) {
    if (typeof escapeHtml === 'function') return escapeHtml(String(text || ''));
    return String(text || '').replace(/[&<>"']/g, function(ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
  }

  function apiBase() {
    var local = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? String(LOCAL_API_BASE) : '';
    if (local) return local.replace(/\/$/, '');
    var host = (window.location && window.location.hostname) || '';
    if (/^(127\.0\.0\.1|localhost|\[::1\])$/i.test(host)) return window.location.origin.replace(/\/$/, '');
    return '';
  }

  function authHeaderJson() {
    var h = typeof authHeaders === 'function' ? Object.assign({}, authHeaders() || {}) : {};
    if (!h.Authorization && typeof token !== 'undefined' && token) h.Authorization = 'Bearer ' + token;
    h['Content-Type'] = 'application/json';
    return h;
  }

  function authHeaderForm() {
    var h = typeof authHeaders === 'function' ? Object.assign({}, authHeaders() || {}) : {};
    if (!h.Authorization && typeof token !== 'undefined' && token) h.Authorization = 'Bearer ' + token;
    delete h['Content-Type'];
    return h;
  }

  function parseErr(data, fallback) {
    if (!data) return fallback || '请求失败';
    if (typeof data === 'string') return data;
    var detail = data.detail || data.error || data.message || data.msg;
    if (typeof detail === 'string' && detail.trim()) return withDiagnostic(detail.trim(), data);
    if (detail && typeof detail === 'object') {
      if (typeof detail.message === 'string') return withDiagnostic(detail.message, data);
      if (typeof detail.msg === 'string') return withDiagnostic(detail.msg, data);
    }
    return withDiagnostic(fallback || '请求失败', data);
  }

  function diagnosticCode(data) {
    if (!data || typeof data === 'string') return '';
    var detail = data.detail || null;
    if (detail && typeof detail === 'object') {
      if (detail.diagnostic_code) return String(detail.diagnostic_code);
      if (detail.diagnostic && detail.diagnostic.code) return String(detail.diagnostic.code);
    }
    if (data.diagnostic_code) return String(data.diagnostic_code);
    if (data.diagnostic && data.diagnostic.code) return String(data.diagnostic.code);
    if (data.code && /^NWX-/i.test(String(data.code))) return String(data.code);
    return '';
  }

  function withDiagnostic(text, data) {
    var code = diagnosticCode(data);
    if (!code) return text || '';
    var base = String(text || '').trim();
    if (base.indexOf(code) >= 0) return base;
    return base + '\n\u65e5\u5fd7\u7801\uff1a' + code + '\uff08\u8bf7\u628a\u8fd9\u4e2a\u7801\u53d1\u7ed9\u6211\u4eec\u5b9a\u4f4d\uff09';
  }

  function splitTargets(text) {
    var seen = {};
    return String(text || '').split(/[\s,，;；]+/).map(function(x) {
      return x.trim();
    }).filter(function(x) {
      var key = x.toLowerCase();
      if (!x || seen[key]) return false;
      seen[key] = true;
      return true;
    });
  }

  function mergeTargets() {
    var seen = {};
    var out = [];
    Array.prototype.slice.call(arguments).forEach(function(list) {
      (list || []).forEach(function(raw) {
        var value = String(raw || '').trim();
        var key = value.toLowerCase();
        if (!value || seen[key]) return;
        seen[key] = true;
        out.push(value);
      });
    });
    return out;
  }

  function apiJson(path, opts) {
    opts = opts || {};
    var base = apiBase();
    if (!base) return Promise.reject(new Error('未配置本机 API 地址'));
    var req = { method: opts.method || 'GET', headers: authHeaderJson() };
    if (opts.body !== undefined) req.body = JSON.stringify(opts.body || {});
    return fetch(base + path, req).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok) throw new Error(parseErr(data, '请求失败'));
        return data;
      });
    });
  }

  function cloudJson(path, opts) {
    opts = opts || {};
    var base = (typeof API_BASE !== 'undefined' && API_BASE) ? String(API_BASE).replace(/\/$/, '') : '';
    if (!base) return Promise.reject(new Error('未配置云端 API 地址'));
    var req = { method: opts.method || 'GET', headers: authHeaderJson() };
    if (opts.body !== undefined) req.body = JSON.stringify(opts.body || {});
    return fetch(base + path, req).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok) throw new Error(parseErr(data, '云端请求失败'));
        return data;
      });
    });
  }

  function formatFileSize(size) {
    var n = Number(size || 0);
    if (n >= 1024 * 1024 * 1024) return (n / 1024 / 1024 / 1024).toFixed(1) + 'GB';
    if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + 'MB';
    if (n >= 1024) return (n / 1024).toFixed(1) + 'KB';
    return n + 'B';
  }

  function renderSendFiles() {
    var list = $('nativeWechatFileList');
    if (!list) return;
    if (!state.sendFiles.length) {
      list.className = 'native-wechat-muted';
      list.innerHTML = '未选择附件';
      return;
    }
    list.className = 'native-wechat-list';
    list.innerHTML = state.sendFiles.map(function(file, idx) {
      return '<div class="native-wechat-item">' +
        '<div class="native-wechat-card-head" style="margin:0;">' +
          '<div>' +
            '<div class="native-wechat-item-title">' + esc(file.name || 'file') + '</div>' +
            '<div class="native-wechat-meta">' + esc(formatFileSize(file.size || 0)) + '</div>' +
          '</div>' +
          '<button type="button" class="btn btn-ghost btn-sm" data-native-file-remove="' + idx + '">删除</button>' +
        '</div>' +
      '</div>';
    }).join('');
  }

  function renderMomentsFiles() {
    var list = $('nativeWechatMomentsFileList');
    if (!list) return;
    if (!state.momentsFiles.length) {
      list.className = 'native-wechat-muted';
      list.innerHTML = '未选择素材';
      return;
    }
    list.className = 'native-wechat-list';
    list.innerHTML = state.momentsFiles.map(function(file, idx) {
      var remote = !!(file && (file.remote || file.source_url || file.url));
      return '<div class="native-wechat-item">' +
        '<div class="native-wechat-card-head" style="margin:0;">' +
          '<div>' +
            '<div class="native-wechat-item-title">' + esc(file.name || 'file') + '</div>' +
            '<div class="native-wechat-meta">' + esc(remote ? '内容库图片' : formatFileSize(file.size || 0)) + '</div>' +
          '</div>' +
          '<button type="button" class="btn btn-ghost btn-sm" data-native-moments-file-remove="' + idx + '">删除</button>' +
        '</div>' +
      '</div>';
    }).join('');
  }

  function uploadWechatFile(file) {
    var base = apiBase();
    if (!base) return Promise.reject(new Error('未配置本机 API 地址'));
    var fd = new FormData();
    fd.append('file', file);
    return fetch(base + '/api/native-wechat/files/upload', {
      method: 'POST',
      headers: authHeaderForm(),
      body: fd
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok) throw new Error(parseErr(data, '文件上传失败'));
        if (!data.file || !data.file.local_path) throw new Error('文件上传失败');
        return data.file;
      });
    });
  }

  function setMsg(text, isErr) {
    var node = $('nativeWechatMsg');
    if (!node) return;
    node.textContent = text || '';
    node.className = 'native-wechat-msg' + (isErr ? ' err' : '');
    node.style.display = text ? 'block' : 'none';
  }

  function setChip(id, text) {
    var node = $(id);
    if (node) node.textContent = text || '';
  }

  function activeAccountId() {
    var sel = $('nativeWechatAccountSelect');
    return String((sel && sel.value) || state.activeAccountId || '');
  }

  function formatTime(value) {
    if (!value) return '';
    var raw = String(value);
    var date = new Date(raw.indexOf('T') >= 0 ? raw : raw.replace(' ', 'T') + 'Z');
    if (Number.isNaN(date.getTime())) return raw;
    return date.toLocaleString();
  }

  function peerTitle(item) {
    return item.display_name || item.peer_id || item.id || '未命名会话';
  }

  function contactTitle(item) {
    return item.display_name || item.remark || item.wx_no || item.contact_key || item.id || '未命名联系人';
  }

  function contactSub(item) {
    var parts = [];
    if (item.remark) parts.push('备注 ' + item.remark);
    if (item.wx_no) parts.push(item.wx_no);
    if (item.source) parts.push(item.source);
    return parts.join(' · ');
  }

  function contactValue(item) {
    return item.contact_key || item.wx_no || item.display_name || item.remark || item.id || contactTitle(item);
  }

  function groupInviteContactValue(item) {
    return item.wx_no || item.contact_key || item.remark || item.display_name || item.id || contactTitle(item);
  }

  function groupInviteContactMatches(item, selected) {
    var wanted = String(selected || '').trim();
    if (!wanted) return false;
    return [groupInviteContactValue(item), item.wx_no, item.contact_key, item.remark, item.display_name, item.id, contactTitle(item)]
      .some(function(value) { return String(value || '').trim() === wanted; });
  }

  function selectedContactValues() {
    return Object.keys(state.contactSelected).map(function(key) { return state.contactSelected[key]; }).filter(Boolean);
  }

  function renderContactSelectionState() {
    var count = selectedContactValues().length;
    setChip('nativeWechatContactSelectedCount', '已选 ' + count);
  }

  function groupTitle(item) {
    return item.display_name || item.group_key || item.id || '未命名群';
  }

  function groupSub(item) {
    var parts = [];
    if (item.member_count) parts.push(item.member_count + ' 人');
    if (item.group_key) parts.push(item.group_key);
    if (item.source) parts.push(item.source);
    return parts.join(' · ');
  }

  function peerSub(item) {
    var parts = [];
    if (item.chat_type) parts.push(item.chat_type === 'group' ? '群' : '个人');
    if (item.peer_id) parts.push(item.peer_id);
    if (Number(item.unread_count || 0) > 0) parts.push('未读 ' + Number(item.unread_count || 0));
    if (item.last_content) parts.push(item.last_content);
    if (item.session_time) parts.push(item.session_time);
    if (item.last_inbound_at) parts.push('收 ' + formatTime(item.last_inbound_at));
    if (item.last_outbound_at) parts.push('发 ' + formatTime(item.last_outbound_at));
    return parts.join(' · ');
  }

  function renderAccountSelect() {
    var sel = $('nativeWechatAccountSelect');
    if (!sel) return;
    if (!state.accounts.length) {
      sel.innerHTML = '<option value="">暂无账号</option>';
      state.activeAccountId = '';
      return;
    }
    if (!state.activeAccountId || !state.accounts.some(function(x) { return x.account_id === state.activeAccountId; })) {
      state.activeAccountId = state.accounts[0].account_id;
    }
    sel.innerHTML = state.accounts.map(function(item) {
      var label = item.name || item.user_id || item.account_id;
      if (item.source === 'pc_wechat' && item.version) label += ' · ' + item.version;
      return '<option value="' + esc(item.account_id) + '">' + esc(label) + '</option>';
    }).join('');
    sel.value = state.activeAccountId;
  }

  function renderAccountList() {
    var list = $('nativeWechatAccountList');
    if (!list) return;
    if (!state.accounts.length) {
      list.className = 'native-wechat-empty';
      list.innerHTML = '未检测到本机微信。请打开已登录的 PC 微信主窗口后重试。';
      return;
    }
    list.className = 'native-wechat-list';
    list.innerHTML = state.accounts.map(function(item) {
      var bits = [];
      if (item.source === 'pc_wechat') bits.push('本机微信');
      else if (item.source) bits.push(item.source);
      if (item.version) bits.push(item.version);
      if (item.pid) bits.push('PID ' + item.pid);
      if (item.hwnd) bits.push('HWND ' + item.hwnd);
      var status = item.driver_ready ? '可下发任务' : '控制组件不可用';
      if (item.driver_ready && item.full_driver_ready === false) status += ' · 通讯录增强驱动未就绪';
      return '<div class="native-wechat-item" data-native-account="' + esc(item.account_id) + '">' +
        '<div class="native-wechat-item-title">' + esc(item.name || item.account_id) + '</div>' +
        '<div class="native-wechat-meta">' + esc(bits.join(' · ') || item.account_id) + '</div>' +
        '<div class="native-wechat-meta">' + esc(status) + '</div>' +
      '</div>';
    }).join('');
  }

  function renderStrategy() {
    var box = $('nativeWechatStrategyList');
    if (!box) return;
    var s = state.strategy || {};
    if (!Object.keys(s).length) {
      box.className = 'native-wechat-empty';
      box.innerHTML = '暂无策略';
      return;
    }
    var rows = [
      ['发送间隔', (s.send_sleep_min || 0) + ' - ' + (s.send_sleep_max || 0) + ' 秒'],
      ['批量数量', s.batch_size || 0],
      ['批量休息', (s.batch_sleep || 0) + ' 秒'],
      ['失败重试', (s.retry_max || 0) + ' 次'],
      ['单账号任务', s.one_active_task_per_account ? '同一时间只执行一个' : '允许并行']
    ];
    box.className = 'native-wechat-detail-list';
    box.innerHTML = rows.map(function(row) {
      return '<strong>' + esc(row[0]) + '</strong><span>' + esc(row[1]) + '</span>';
    }).join('');
  }

  function renderAutoReply() {
    var enabled = $('nativeWechatAutoReplyEnabled');
    var status = $('nativeWechatAutoReplyStatus');
    var runBtn = $('nativeWechatAutoReplyRunBtn');
    var cfg = state.autoReply || {};
    if (enabled) {
      enabled.checked = !!cfg.enabled;
      enabled.disabled = state.autoReplyBusy || !activeAccountId();
    }
    if (runBtn) runBtn.disabled = state.autoReplyBusy || !activeAccountId();
    if (!status) return;
    if (!activeAccountId()) {
      status.textContent = '请先选择本机微信账号。';
      return;
    }
    if (state.autoReplyBusy) {
      status.textContent = '正在检查私聊消息，处理时会按会话顺序慢速回复。';
      return;
    }
    var parts = [];
    parts.push(cfg.enabled ? '已开启' : '未开启');
    parts.push('每' + Math.max(1, Number(cfg.interval_seconds || 15)) + '秒检查一次');
    parts.push('只回复私聊');
    if (cfg.running) parts.push('运行中');
    if (cfg.last_checked_at) parts.push('上次 ' + formatTime(cfg.last_checked_at));
    var result = cfg.last_result || {};
    if (result && (result.replied || result.skipped || result.failed)) {
      parts.push('回复 ' + (result.replied || 0) + '，跳过 ' + (result.skipped || 0) + '，失败 ' + (result.failed || 0));
    }
    if (result && (result.friend_requests_checked || result.friend_requests_accepted || result.friend_requests_failed)) {
      parts.push('好友申请检查 ' + (result.friend_requests_checked || 0) + '，已同意 ' + (result.friend_requests_accepted || 0));
    }
    if (cfg.last_error) parts.push('错误：' + cfg.last_error);
    status.textContent = parts.join(' · ');
  }

  function renderAutoReplyConfig() {
    var cfg = state.autoReply || {};
    var inviteEnabled = $('nativeWechatGroupInviteEnabled');
    if (inviteEnabled) inviteEnabled.checked = !!cfg.group_invite_enabled;
    var select = $('nativeWechatAutoReplyMemoryDoc');
    if (select) {
      var selected = Array.isArray(cfg.memory_doc_ids) && cfg.memory_doc_ids.length ? String(cfg.memory_doc_ids[0] || '') : '';
      var options = ['<option value="">不指定，使用系统优先记忆</option>'];
      (state.autoReplyMemoryDocs || []).forEach(function(doc) {
        var id = String(doc.id || doc.doc_id || '');
        if (!id) return;
        options.push('<option value="' + esc(id) + '">' + esc(doc.title || doc.filename || id) + '</option>');
      });
      select.innerHTML = options.join('');
      select.value = selected;
    }
    var inviteMemorySelect = $('nativeWechatGroupInviteMemoryDoc');
    if (inviteMemorySelect) {
      var inviteMemoryOptions = ['<option value="">不指定拉群规则文件</option>'];
      (state.autoReplyMemoryDocs || []).forEach(function(doc) {
        var inviteDocId = String(doc.id || doc.doc_id || '');
        if (!inviteDocId) return;
        inviteMemoryOptions.push('<option value="' + esc(inviteDocId) + '">' + esc(doc.title || doc.filename || inviteDocId) + '</option>');
      });
      inviteMemorySelect.innerHTML = inviteMemoryOptions.join('');
      inviteMemorySelect.value = String(cfg.group_invite_memory_doc_id || '');
    }
    var primaryInput = $('nativeWechatGroupInvitePrimaryContact');
    if (primaryInput) {
      var selected = String(cfg.group_invite_primary_contact || ((cfg.group_invite_contacts || [])[0]) || '').trim();
      primaryInput.value = selected;
      primaryInput.setAttribute('data-contact-name', selected);
    }
    var welcome = $('nativeWechatGroupInviteWelcomeMessage');
    if (welcome && document.activeElement !== welcome) {
      welcome.value = cfg.group_invite_welcome_message || '';
    }
  }

  function renderGroupInviteContactPicker() {
    var list = $('nativeWechatContactPickerList');
    if (!list) return;
    var meta = state.pagination.contactPicker;
    var count = $('nativeWechatContactPickerCount');
    if (count) count.textContent = '共 ' + Number(meta.total || 0) + ' 位联系人';
    var selected = (($('nativeWechatGroupInvitePrimaryContact') || {}).value || '').trim();
    if (!state.contactPickerContacts.length) {
      list.innerHTML = '<div class="native-wechat-empty">' + (meta.keyword ? '没有匹配的联系人' : '暂无通讯录，请先同步') + '</div>';
      renderPagination('contactPicker');
      return;
    }
    list.innerHTML = state.contactPickerContacts.map(function(item) {
      var value = groupInviteContactValue(item);
      var name = contactTitle(item) || value;
      var sub = contactSub(item) || value;
      var isSelected = groupInviteContactMatches(item, selected);
      return '<button type="button" class="native-wechat-contact-picker-row' + (isSelected ? ' selected' : '') + '" data-native-group-contact="' + esc(value) + '" data-native-group-contact-name="' + esc(name) + '">' +
        '<span class="native-wechat-contact-picker-avatar">' + esc(String(name || '联系').slice(0, 2)) + '</span>' +
        '<span class="native-wechat-contact-picker-copy"><strong>' + esc(name) + '</strong><small>' + esc(sub) + '</small></span>' +
        '<span class="native-wechat-contact-picker-check">' + (isSelected ? '已选' : '') + '</span>' +
      '</button>';
    }).join('');
    renderPagination('contactPicker');
  }

  function setGroupInvitePrimaryContact(value, name) {
    var input = $('nativeWechatGroupInvitePrimaryContact');
    if (input) {
      input.value = String(value || '').trim();
      input.setAttribute('data-contact-name', String(name || value || '').trim());
    }
    var text = $('nativeWechatGroupInvitePrimaryContactText');
    if (text) text.textContent = String(name || value || '请选择主联系人').trim();
    closeModals();
  }

  function openGroupInviteContactPicker() {
    state.groupInviteContactSearch = '';
    resetPagination('contactPicker');
    state.contactPickerContacts = [];
    var search = $('nativeWechatContactPickerSearch');
    if (search) search.value = '';
    var modal = $('nativeWechatContactPickerModal');
    if (modal) modal.classList.add('show');
    renderGroupInviteContactPicker();
    loadContactPicker(1);
    window.setTimeout(function() { if (search) search.focus(); }, 80);
  }

  function splitConfigValues(value) {
    return String(value || '').split(/[,，;；\n]+/).map(function(item) { return item.trim(); }).filter(Boolean);
  }

  function autoReplyConfigBody(enabled) {
    var memoryId = (($('nativeWechatAutoReplyMemoryDoc') || {}).value || '').trim();
    var primaryInput = $('nativeWechatGroupInvitePrimaryContact');
    var primaryContact = ((primaryInput || {}).value || '').trim();
    var primaryLabel = primaryContact;
    var current = state.autoReply || {};
    return {
      account_id: activeAccountId(),
      enabled: !!enabled,
      interval_seconds: 15,
      memory_doc_ids: memoryId ? [memoryId] : [],
      group_invite_enabled: !!(($('nativeWechatGroupInviteEnabled') || {}).checked),
      group_invite_memory_doc_id: (($('nativeWechatGroupInviteMemoryDoc') || {}).value || '').trim(),
      group_invite_keywords: String(current.group_invite_keywords || '').trim(),
      group_invite_contacts: primaryContact ? [primaryContact] : [],
      group_invite_primary_contact: primaryContact,
      group_invite_primary_contact_name: primaryLabel,
      group_invite_welcome_message: (($('nativeWechatGroupInviteWelcomeMessage') || {}).value || '').trim()
    };
  }

  function cloudAutoReplyConfigBody(body) {
    var payload = Object.assign({}, body || {});
    payload.installation_id = typeof getOrCreateInstallationId === 'function' ? getOrCreateInstallationId() : '';
    payload.account_key = 'wechat:pc-default';
    return payload;
  }

  function syncAutoReplyConfigToCloud(body) {
    return cloudJson('/api/h5-chat/mounted-accounts/wechat-auto-reply', {
      method: 'POST',
      body: cloudAutoReplyConfigBody(body)
    });
  }

  function cloudAutoReplyConfig() {
    return cloudJson('/api/h5-chat/mounted-accounts', { json: false }).then(function(data) {
      var installationId = typeof getOrCreateInstallationId === 'function' ? getOrCreateInstallationId() : '';
      var accounts = Array.isArray(data && data.accounts) ? data.accounts : [];
      var row = accounts.find(function(item) {
        return item && item.scope === 'wechat' && String(item.installation_id || '') === String(installationId || '');
      });
      var defaultRow = data && data.defaults && data.defaults.wechat_auto_reply;
      if (!row || !defaultRow || !defaultRow.updated_at) return null;
      return {
        account_id: String(row.account_id || activeAccountId() || 'pc-wechat-default'),
        updated_at: String(defaultRow.updated_at || ''),
        enabled: !!row.auto_reply_enabled,
        interval_seconds: Number(row.auto_reply_interval_seconds || 15),
        memory_doc_ids: Array.isArray(row.auto_reply_memory_doc_ids) ? row.auto_reply_memory_doc_ids : [],
        group_invite_enabled: !!row.group_invite_enabled,
        group_invite_memory_doc_id: String(row.group_invite_memory_doc_id || ''),
        group_invite_keywords: String(row.group_invite_keywords || ''),
        group_invite_contacts: Array.isArray(row.group_invite_contacts) ? row.group_invite_contacts : [],
        group_invite_primary_contact: String(row.group_invite_primary_contact || ''),
        group_invite_primary_contact_name: String(row.group_invite_primary_contact_name || row.group_invite_primary_contact || ''),
        group_invite_welcome_message: String(row.group_invite_welcome_message || '')
      };
    });
  }

  function applyCloudAutoReplyConfig() {
    return cloudAutoReplyConfig().then(function(body) {
      if (!body) return null;
      var local = state.autoReply || {};
      var localTime = Date.parse(String(local.updated_at || '')) || 0;
      var cloudTime = Date.parse(String(body.updated_at || '')) || 0;
      if (localTime > cloudTime) {
        return syncAutoReplyConfigToCloud(autoReplyConfigBody(!!local.enabled)).then(function() {
          return state.autoReply;
        });
      }
      return apiJson('/api/native-wechat/auto-reply/config', {
        method: 'POST',
        body: body
      }).then(function(data) {
        state.autoReply = data.config || body;
        renderAutoReply();
        renderAutoReplyConfig();
        return state.autoReply;
      });
    });
  }

  function renderPeers() {
    var list = $('nativeWechatPeerList');
    if (!list) return;
    if (!state.peers.length) {
      list.className = 'native-wechat-empty';
      list.innerHTML = state.pagination.peers.keyword ? '没有匹配会话' : '暂无会话，先点击“收消息”。';
      renderPagination('peers');
      return;
    }
    list.className = 'native-wechat-list';
    list.innerHTML = state.peers.map(function(item) {
      var active = item.peer_id === state.activePeerId ? ' active' : '';
      return '<div class="native-wechat-item' + active + '" data-native-peer="' + esc(item.peer_id) + '">' +
        '<div class="native-wechat-item-title">' + esc(peerTitle(item)) + '</div>' +
        '<div class="native-wechat-meta">' + esc(peerSub(item)) + '</div>' +
      '</div>';
    }).join('');
    renderPagination('peers');
  }

  function renderContacts() {
    var list = $('nativeWechatContactList');
    if (!list) return;
    if (!state.contacts.length) {
      list.className = 'native-wechat-empty';
      list.innerHTML = state.pagination.contacts.keyword ? '没有匹配的联系人' : '暂无联系人';
      renderContactSelectionState();
      renderPagination('contacts');
      return;
    }
    list.className = 'native-wechat-list';
    list.innerHTML = state.contacts.map(function(item) {
      var value = contactValue(item);
      var key = value.toLowerCase();
      var checked = state.contactSelected[key] ? ' checked' : '';
      return '<label class="native-wechat-item" style="display:flex;gap:10px;align-items:flex-start;">' +
        '<input type="checkbox" data-native-contact-select="' + esc(value) + '"' + checked + ' style="margin-top:3px;">' +
        '<span style="min-width:0;">' +
          '<span class="native-wechat-item-title" style="display:block;">' + esc(contactTitle(item)) + '</span>' +
          '<span class="native-wechat-meta" style="display:block;">' + esc(contactSub(item)) + '</span>' +
        '</span>' +
      '</label>';
    }).join('');
    renderContactSelectionState();
    renderPagination('contacts');
  }

  function renderGroups() {
    var list = $('nativeWechatGroupList');
    if (!list) return;
    if (!state.groups.length) {
      list.className = 'native-wechat-empty';
      list.innerHTML = state.pagination.groups.keyword ? '没有匹配的群' : '暂无群';
      renderPagination('groups');
      return;
    }
    list.className = 'native-wechat-list';
    list.innerHTML = state.groups.map(function(item) {
      var key = item.group_key || item.display_name || '';
      var active = key === state.activeGroupKey ? ' active' : '';
      return '<div class="native-wechat-item' + active + '" data-native-group="' + esc(key) + '">' +
        '<div class="native-wechat-item-title">' + esc(groupTitle(item)) + '</div>' +
        '<div class="native-wechat-meta">' + esc(groupSub(item)) + '</div>' +
      '</div>';
    }).join('');
    renderPagination('groups');
  }

  function renderGroupMembers() {
    var title = $('nativeWechatGroupMemberTitle');
    var list = $('nativeWechatGroupMemberList');
    if (title) title.textContent = state.activeGroupKey ? ('群成员：' + state.activeGroupKey) : '群成员';
    if (!list) return;
    if (!state.activeGroupKey) {
      list.className = 'native-wechat-empty';
      list.innerHTML = '请选择群';
      renderPagination('groupMembers');
      return;
    }
    if (!state.groupMembers.length) {
      list.className = 'native-wechat-empty';
      list.innerHTML = '暂无成员';
      renderPagination('groupMembers');
      return;
    }
    list.className = 'native-wechat-list';
    list.innerHTML = state.groupMembers.map(function(item) {
      return '<div class="native-wechat-item">' +
        '<div class="native-wechat-item-title">' + esc(item.display_name || item.member_key || '') + '</div>' +
      '</div>';
    }).join('');
    renderPagination('groupMembers');
  }

  function renderMessageHistoryState() {
    var tools = $('nativeWechatMessageHistoryTools');
    var button = $('nativeWechatLoadOlderMessagesBtn');
    var count = $('nativeWechatMessageHistoryCount');
    var total = Number(state.messageHistory.total || 0);
    var shown = state.messages.length;
    if (tools) tools.classList.toggle('show', !!state.activePeerId);
    if (count) count.textContent = '已显示 ' + shown + ' / ' + total;
    if (button) {
      button.disabled = state.messageHistory.loading || !state.activePeerId || shown >= total;
      button.textContent = state.messageHistory.loading ? '正在加载...' : (shown >= total && total ? '已加载全部' : '加载更早消息');
    }
  }

  function renderMessages(options) {
    options = options || {};
    var list = $('nativeWechatMessageList');
    var title = $('nativeWechatActivePeerTitle');
    var sub = $('nativeWechatActivePeerSub');
    var recipient = $('nativeWechatRecipientInput');
    var peer = state.activePeer || state.peers.find(function(x) { return x.peer_id === state.activePeerId; });
    if (title) title.textContent = peer ? peerTitle(peer) : '未选择会话';
    if (sub) sub.textContent = peer ? peerSub(peer) : '也可以手动输入 Weixin ID 发送。';
    if (recipient && peer && !recipient.value) recipient.value = peer.peer_id;
    if (!list) return;
    if (!state.activePeerId) {
      list.className = 'native-wechat-empty';
      list.innerHTML = '选择会话后查看消息。';
      renderMessageHistoryState();
      return;
    }
    if (!state.messages.length) {
      list.className = 'native-wechat-empty';
      list.innerHTML = '暂无消息';
      renderMessageHistoryState();
      return;
    }
    list.className = 'native-wechat-chat';
    list.innerHTML = state.messages.slice().reverse().map(function(item) {
      if (item.is_system || item.direction === 'system' || item.msg_type === 'time') {
        return '<div class="native-wechat-time">' + esc(item.content || formatTime(item.created_at)) + '</div>';
      }
      var dir = item.direction === 'out' ? 'out' : 'in';
      var text = item.content || (item.msg_type && item.msg_type !== 'text' ? '[' + item.msg_type + ']' : '');
      var raw = item.raw_json || {};
      var sender = raw.sender || raw.sender_remark || '';
      var attachments = Array.isArray(item.attachments) ? item.attachments : [];
      var mediaHtml = attachments.map(renderMessageAttachment).join('');
      var metaParts = [];
      if (sender && sender !== 'self' && sender !== 'system') metaParts.push(sender);
      if (item.msg_type) metaParts.push(item.msg_type);
      metaParts.push(formatTime(item.created_at));
      if (item.status) metaParts.push(item.status);
      if (sender && sender !== 'self' && sender !== 'system') text = sender + '：' + text;
      return '<div class="native-wechat-bubble ' + dir + '">' +
        '<div>' + esc(text || '(空消息)') + '</div>' +
        mediaHtml +
        '<div class="native-wechat-meta">' + esc(formatTime(item.created_at)) + (item.status ? ' · ' + esc(item.status) : '') + '</div>' +
      '</div>';
    }).join('');
    if (options.preservePosition) {
      list.scrollTop = Math.max(0, list.scrollHeight - Number(options.previousHeight || 0) + Number(options.previousTop || 0));
    } else {
      list.scrollTop = list.scrollHeight;
    }
    renderMessageHistoryState();
  }

  function mediaUrl(url) {
    url = String(url || '');
    if (!url) return '';
    if (/^https?:\/\//i.test(url)) return url;
    var base = apiBase();
    return base ? base + url : url;
  }

  function renderMessageAttachment(file) {
    file = file || {};
    var kind = String(file.kind || '').toLowerCase();
    var url = mediaUrl(file.url || '');
    var name = file.filename || (kind === 'image' ? '图片' : (kind === 'video' ? '视频' : '文件'));
    if (file.download_error && !url) {
      return '<div class="native-wechat-meta">附件未下载：' + esc(file.download_error) + '</div>';
    }
    if (!url) return '';
    if (kind === 'image') {
      return '<a href="' + esc(url) + '" target="_blank" rel="noopener"><img class="native-wechat-media" src="' + esc(url) + '" alt="' + esc(name) + '"></a>';
    }
    if (kind === 'video') {
      return '<video class="native-wechat-media" src="' + esc(url) + '" controls preload="metadata"></video>';
    }
    return '<a class="native-wechat-file-link" href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(name) + '</a>';
  }

  function renderTasks() {
    var list = $('nativeWechatTaskList');
    if (!list) return;
    if (!state.tasks.length) {
      list.className = 'native-wechat-empty';
      list.innerHTML = '暂无任务';
      renderPagination('tasks');
      return;
    }
    list.className = '';
    list.innerHTML = '<table class="native-wechat-table"><thead><tr>' +
      '<th>时间</th><th>类型</th><th>状态</th><th>进度</th><th>操作</th>' +
      '</tr></thead><tbody>' + state.tasks.map(function(item) {
        return '<tr>' +
          '<td>' + esc(formatTime(item.created_at)) + '</td>' +
          '<td>' + esc(taskTypeText(item.task_type)) + '</td>' +
          '<td>' + esc(taskStatusText(item.status)) + '</td>' +
          '<td>' + esc((item.processed || 0) + '/' + (item.planned_total || 0) + '，成功 ' + (item.success || 0) + '，失败 ' + (item.failed || 0)) + '</td>' +
          '<td><button type="button" class="btn btn-ghost btn-sm" data-native-task="' + esc(item.id) + '">详情</button></td>' +
        '</tr>';
      }).join('') + '</tbody></table>';
    renderPagination('tasks');
  }

  function taskTypeText(type) {
    var map = { send_text: '发送消息', send_message: '发送消息', add_friend: '添加好友', moments_like: '朋友圈点赞', moments_comment: '朋友圈评论', moments_publish: '朋友圈发布', create_group: '创建群' };
    return map[type] || type || '-';
  }

  function taskStatusText(status) {
    var map = { pending: '排队中', running: '执行中', success: '成功', failed: '失败', partial_failed: '部分失败' };
    return map[status] || status || '-';
  }

  function switchTab(tab) {
    state.tab = tab || 'messages';
    document.querySelectorAll('[data-native-wechat-tab]').forEach(function(btn) {
      btn.classList.toggle('active', btn.getAttribute('data-native-wechat-tab') === state.tab);
    });
    document.querySelectorAll('[data-native-wechat-panel]').forEach(function(panel) {
      panel.classList.toggle('active', panel.getAttribute('data-native-wechat-panel') === state.tab);
    });
    if (state.tab === 'tasks') loadTasks();
    if (state.tab === 'contacts') loadContacts();
    if (state.tab === 'groups') {
      loadGroups();
      if (state.activeGroupKey) loadGroupMembers();
    }
    if (state.tab === 'accounts') {
      loadAccounts();
      loadStrategy();
    }
  }

  function loadAccounts() {
    return apiJson('/api/native-wechat/accounts').then(function(data) {
      state.accounts = Array.isArray(data.items) ? data.items : [];
      state.driver = data.driver || null;
      if (data.diagnostic || data.diagnostic_code) state.lastDiagnostic = data;
      renderAccountSelect();
      renderAccountList();
      return state.accounts;
    }).catch(function(err) {
      state.accounts = [];
      renderAccountSelect();
      renderAccountList();
      setMsg(err.message || '账号加载失败', true);
    });
  }

  function loadStrategy() {
    return apiJson('/api/native-wechat/strategy').then(function(data) {
      state.strategy = data.strategy || {};
      renderStrategy();
    }).catch(function(err) {
      setMsg(err.message || '策略加载失败', true);
    });
  }

  function loadAutoReplyConfig() {
    var id = activeAccountId();
    if (!id) {
      state.autoReply = null;
      renderAutoReply();
      return Promise.resolve();
    }
    return apiJson('/api/native-wechat/auto-reply/config?account_id=' + encodeURIComponent(id)).then(function(data) {
      state.autoReply = data.config || {};
      renderAutoReply();
      renderAutoReplyConfig();
      return applyCloudAutoReplyConfig().catch(function() { return state.autoReply; });
    }).catch(function(err) {
      state.autoReply = null;
      renderAutoReply();
      setMsg(err.message || '自动回复配置加载失败', true);
    });
  }

  function loadAutoReplyMemoryDocs() {
    return apiJson('/api/openclaw/memory/list').then(function(data) {
      state.autoReplyMemoryDocs = Array.isArray(data.documents) ? data.documents : [];
      renderAutoReplyConfig();
    }).catch(function() {
      state.autoReplyMemoryDocs = [];
      renderAutoReplyConfig();
    });
  }

  function saveAutoReplyConfig(enabled) {
    var id = activeAccountId();
    if (!id) return setMsg('请先选择本机微信账号', true);
    state.autoReplyBusy = true;
    renderAutoReply();
    var body = autoReplyConfigBody(enabled);
    return apiJson('/api/native-wechat/auto-reply/config', {
      method: 'POST',
      body: body
    }).then(function(data) {
      state.autoReply = data.config || {};
      renderAutoReplyConfig();
      return syncAutoReplyConfigToCloud(body).then(function() {
        setMsg(enabled ? '个人微信自动回复已开启，设置已同步服务器。' : '个人微信自动回复已关闭，设置已同步服务器。', false);
      });
    }).catch(function(err) {
      setMsg(err.message || '自动回复配置保存失败，服务器可能未同步', true);
      return loadAutoReplyConfig();
    }).finally(function() {
      state.autoReplyBusy = false;
      renderAutoReply();
    });
  }

  function saveAutoReplyDetails() {
    var id = activeAccountId();
    if (!id) return setMsg('请先选择本机微信账号', true);
    state.autoReplyBusy = true;
    renderAutoReply();
    var body = autoReplyConfigBody(!!(state.autoReply || {}).enabled);
    return apiJson('/api/native-wechat/auto-reply/config', {
      method: 'POST',
      body: body
    }).then(function(data) {
      state.autoReply = data.config || {};
      renderAutoReplyConfig();
      return syncAutoReplyConfigToCloud(body).then(function() {
        setMsg('接管设置已保存，H5 与 Online 已同步服务器。', false);
      });
    }).catch(function(err) {
      setMsg(err.message || '接管设置保存失败，服务器可能未同步', true);
    }).finally(function() {
      state.autoReplyBusy = false;
      renderAutoReply();
    });
  }

  function runAutoReplyOnce() {
    var id = activeAccountId();
    if (!id) return setMsg('请先选择本机微信账号', true);
    state.autoReplyBusy = true;
    renderAutoReply();
    setMsg('正在检查私聊消息，会按会话顺序慢速回复...', false);
    return apiJson('/api/native-wechat/auto-reply/run-once', {
      method: 'POST',
      body: { account_id: id, force: true }
    }).then(function(data) {
      state.autoReply = data.config || state.autoReply || {};
      var replied = Number(data.replied || 0);
      var skipped = Number(data.skipped || 0);
      var failed = Number(data.failed || 0);
      var friendAccepted = Number(data.friend_requests_accepted || 0);
      var friendFailed = Number(data.friend_requests_failed || 0);
      setMsg('检查完成：同意好友申请 ' + friendAccepted + '，回复 ' + replied + '，跳过 ' + skipped + '，失败 ' + (failed + friendFailed) + '。', !!(failed + friendFailed));
      return Promise.all([loadPeers(), loadTasks(), loadAutoReplyConfig()]);
    }).catch(function(err) {
      setMsg(err.message || '自动回复检查失败', true);
      return loadAutoReplyConfig();
    }).finally(function() {
      state.autoReplyBusy = false;
      renderAutoReply();
    });
  }

  function preparePagination(key, requestedPage, keyword) {
    var meta = state.pagination[key];
    keyword = String(keyword || '').trim();
    if (keyword !== meta.keyword) {
      meta.keyword = keyword;
      meta.page = 1;
      meta.total = 0;
    } else if (typeof requestedPage === 'number' && Number.isFinite(requestedPage)) {
      meta.page = Math.max(1, Math.floor(requestedPage));
    }
    meta.loading = true;
    renderPagination(key);
    return meta;
  }

  function applyPaginationResponse(meta, data) {
    meta.total = Math.max(0, Number(data.count || 0));
    var pages = pageCount(meta);
    if (meta.page > pages) {
      meta.page = pages;
      return false;
    }
    return true;
  }

  function loadPeers(page) {
    var id = activeAccountId();
    var keyword = (($('nativeWechatPeerSearch') || {}).value || '').trim();
    var meta = preparePagination('peers', page, keyword);
    if (!id) {
      state.peers = [];
      meta.total = 0;
      meta.loading = false;
      renderPeers();
      return Promise.resolve();
    }
    var sequence = beginRequest('peers');
    var path = '/api/native-wechat/peers?account_id=' + encodeURIComponent(id) +
      '&limit=' + meta.limit + '&offset=' + ((meta.page - 1) * meta.limit) +
      (keyword ? '&keyword=' + encodeURIComponent(keyword) : '');
    return apiJson(path).then(function(data) {
      if (!isCurrentRequest('peers', sequence) || id !== activeAccountId()) return;
      if (!applyPaginationResponse(meta, data)) return loadPeers(meta.page);
      state.peers = Array.isArray(data.items) ? data.items : [];
      var activeOnPage = state.peers.find(function(x) { return x.peer_id === state.activePeerId; });
      if (activeOnPage) state.activePeer = activeOnPage;
      renderPeers();
      renderMessages();
    }).catch(function(err) {
      if (isCurrentRequest('peers', sequence)) setMsg(err.message || '会话加载失败', true);
    }).finally(function() {
      if (!isCurrentRequest('peers', sequence)) return;
      meta.loading = false;
      renderPagination('peers');
    });
  }

  function loadContacts(page) {
    var id = activeAccountId();
    var keyword = (($('nativeWechatContactSearch') || {}).value || '').trim();
    var meta = preparePagination('contacts', page, keyword);
    if (!id) {
      state.contacts = [];
      meta.total = 0;
      meta.loading = false;
      renderContacts();
      return Promise.resolve();
    }
    var sequence = beginRequest('contacts');
    var path = '/api/native-wechat/contacts?account_id=' + encodeURIComponent(id) +
      '&limit=' + meta.limit + '&offset=' + ((meta.page - 1) * meta.limit) +
      (keyword ? '&keyword=' + encodeURIComponent(keyword) : '');
    return apiJson(path).then(function(data) {
      if (!isCurrentRequest('contacts', sequence) || id !== activeAccountId()) return;
      if (!applyPaginationResponse(meta, data)) return loadContacts(meta.page);
      state.contacts = Array.isArray(data.items) ? data.items : [];
      renderContacts();
      renderAutoReplyConfig();
    }).catch(function(err) {
      if (isCurrentRequest('contacts', sequence)) setMsg(err.message || '通讯录加载失败', true);
    }).finally(function() {
      if (!isCurrentRequest('contacts', sequence)) return;
      meta.loading = false;
      renderPagination('contacts');
    });
  }

  function loadContactPicker(page) {
    var id = activeAccountId();
    var keyword = String(state.groupInviteContactSearch || '').trim();
    var meta = preparePagination('contactPicker', page, keyword);
    if (!id) {
      state.contactPickerContacts = [];
      meta.total = 0;
      meta.loading = false;
      renderGroupInviteContactPicker();
      return Promise.resolve();
    }
    var sequence = beginRequest('contactPicker');
    var path = '/api/native-wechat/contacts?account_id=' + encodeURIComponent(id) +
      '&limit=' + meta.limit + '&offset=' + ((meta.page - 1) * meta.limit) +
      (keyword ? '&keyword=' + encodeURIComponent(keyword) : '');
    return apiJson(path).then(function(data) {
      if (!isCurrentRequest('contactPicker', sequence) || id !== activeAccountId()) return;
      if (!applyPaginationResponse(meta, data)) return loadContactPicker(meta.page);
      state.contactPickerContacts = Array.isArray(data.items) ? data.items : [];
      renderGroupInviteContactPicker();
    }).catch(function(err) {
      if (isCurrentRequest('contactPicker', sequence)) setMsg(err.message || '联系人加载失败', true);
    }).finally(function() {
      if (!isCurrentRequest('contactPicker', sequence)) return;
      meta.loading = false;
      renderPagination('contactPicker');
    });
  }

  function loadGroups(page) {
    var id = activeAccountId();
    var keyword = (($('nativeWechatGroupSearch') || {}).value || '').trim();
    var meta = preparePagination('groups', page, keyword);
    if (!id) {
      state.groups = [];
      meta.total = 0;
      meta.loading = false;
      renderGroups();
      return Promise.resolve();
    }
    var sequence = beginRequest('groups');
    var path = '/api/native-wechat/groups?account_id=' + encodeURIComponent(id) +
      '&limit=' + meta.limit + '&offset=' + ((meta.page - 1) * meta.limit) +
      (keyword ? '&keyword=' + encodeURIComponent(keyword) : '');
    return apiJson(path).then(function(data) {
      if (!isCurrentRequest('groups', sequence) || id !== activeAccountId()) return;
      if (!applyPaginationResponse(meta, data)) return loadGroups(meta.page);
      state.groups = Array.isArray(data.items) ? data.items : [];
      renderGroups();
      renderGroupMembers();
    }).catch(function(err) {
      if (isCurrentRequest('groups', sequence)) setMsg(err.message || '群加载失败', true);
    }).finally(function() {
      if (!isCurrentRequest('groups', sequence)) return;
      meta.loading = false;
      renderPagination('groups');
    });
  }

  function loadGroupMembers(page) {
    var id = activeAccountId();
    var groupKey = state.activeGroupKey;
    var meta = preparePagination('groupMembers', page, '');
    if (!id || !groupKey) {
      state.groupMembers = [];
      meta.total = 0;
      meta.loading = false;
      renderGroupMembers();
      return Promise.resolve();
    }
    var sequence = beginRequest('groupMembers');
    var path = '/api/native-wechat/groups/members?account_id=' + encodeURIComponent(id) +
      '&group_key=' + encodeURIComponent(groupKey) + '&limit=' + meta.limit +
      '&offset=' + ((meta.page - 1) * meta.limit);
    return apiJson(path).then(function(data) {
      if (!isCurrentRequest('groupMembers', sequence) || id !== activeAccountId() || groupKey !== state.activeGroupKey) return;
      if (!applyPaginationResponse(meta, data)) return loadGroupMembers(meta.page);
      state.groupMembers = Array.isArray(data.items) ? data.items : [];
      renderGroupMembers();
    }).catch(function(err) {
      if (isCurrentRequest('groupMembers', sequence)) setMsg(err.message || '群成员加载失败', true);
    }).finally(function() {
      if (!isCurrentRequest('groupMembers', sequence)) return;
      meta.loading = false;
      renderPagination('groupMembers');
    });
  }

  function messageIdentity(item) {
    return String(item.id || [item.created_at, item.direction, item.msg_type, item.content].join('|'));
  }

  function loadMessages(options) {
    options = options || {};
    var append = !!options.append;
    var id = activeAccountId();
    var peerId = state.activePeerId;
    if (!id || !peerId) {
      state.messages = [];
      state.messageHistory.total = 0;
      state.messageHistory.loading = false;
      renderMessages();
      return Promise.resolve();
    }
    var list = $('nativeWechatMessageList');
    var previousHeight = append && list ? list.scrollHeight : 0;
    var previousTop = append && list ? list.scrollTop : 0;
    var offset = append ? state.messages.length : 0;
    var sequence = beginRequest('messages');
    state.messageHistory.loading = append;
    renderMessageHistoryState();
    var path = '/api/native-wechat/messages?account_id=' + encodeURIComponent(id) +
      '&peer_id=' + encodeURIComponent(peerId) + '&limit=' + state.messageHistory.limit + '&offset=' + offset;
    return apiJson(path).then(function(data) {
      if (!isCurrentRequest('messages', sequence) || id !== activeAccountId() || peerId !== state.activePeerId) return;
      var items = Array.isArray(data.items) ? data.items : [];
      if (append) {
        var seen = {};
        state.messages.forEach(function(item) { seen[messageIdentity(item)] = true; });
        items.forEach(function(item) {
          var key = messageIdentity(item);
          if (!seen[key]) {
            seen[key] = true;
            state.messages.push(item);
          }
        });
      } else {
        state.messages = items;
      }
      state.messageHistory.total = Math.max(0, Number(data.count || 0));
      renderMessages({ preservePosition: append, previousHeight: previousHeight, previousTop: previousTop });
    }).catch(function(err) {
      if (isCurrentRequest('messages', sequence)) setMsg(err.message || '消息加载失败', true);
    }).finally(function() {
      if (!isCurrentRequest('messages', sequence)) return;
      state.messageHistory.loading = false;
      renderMessageHistoryState();
    });
  }

  function loadOlderMessages() {
    if (state.messageHistory.loading || state.messages.length >= Number(state.messageHistory.total || 0)) return Promise.resolve();
    return loadMessages({ append: true });
  }

  function loadPaginationPage(key, page) {
    if (key === 'peers') return loadPeers(page);
    if (key === 'contacts') return loadContacts(page);
    if (key === 'groups') return loadGroups(page);
    if (key === 'groupMembers') return loadGroupMembers(page);
    if (key === 'contactPicker') return loadContactPicker(page);
    if (key === 'tasks') return loadTasks(page);
    return Promise.resolve();
  }

  function syncActiveMessages() {
    var id = activeAccountId();
    if (!id || !state.activePeerId) {
      return setMsg('请先选择会话', true);
    }
    setMsg('正在读取当前会话消息...', false);
    return apiJson('/api/native-wechat/messages/sync', {
      method: 'POST',
      body: { account_id: id, peer_id: state.activePeerId, load_more_pages: 0 }
    }).then(function(data) {
      setMsg('消息读取完成，新增 ' + (data.count || 0) + ' 条', false);
      state.activePeerId = data.peer_id || state.activePeerId;
      return Promise.all([loadPeers(), loadMessages()]);
    }).catch(function(err) {
      setMsg(err.message || '消息读取失败', true);
    });
  }

  function loadTasks(page) {
    var id = activeAccountId();
    var meta = preparePagination('tasks', page, '');
    var sequence = beginRequest('tasks');
    var path = '/api/native-wechat/tasks?limit=' + meta.limit + '&offset=' + ((meta.page - 1) * meta.limit) +
      (id ? '&account_id=' + encodeURIComponent(id) : '');
    return apiJson(path).then(function(data) {
      if (!isCurrentRequest('tasks', sequence) || id !== activeAccountId()) return;
      if (!applyPaginationResponse(meta, data)) return loadTasks(meta.page);
      state.tasks = Array.isArray(data.items) ? data.items : [];
      renderTasks();
    }).catch(function(err) {
      if (isCurrentRequest('tasks', sequence)) setMsg(err.message || '任务加载失败', true);
    }).finally(function() {
      if (!isCurrentRequest('tasks', sequence)) return;
      meta.loading = false;
      renderPagination('tasks');
    });
  }

  function pollUpdates() {
    var id = activeAccountId();
    if (!id) return setMsg('请先连接或选择账号', true);
    setMsg('正在收消息...', false);
    apiJson('/api/native-wechat/updates/poll', {
      method: 'POST',
      body: { account_id: id, timeout_ms: 8000 }
    }).then(function(data) {
      if (!data || data.ok === false) {
        throw new Error((data && (data.message || data.error)) || '收消息失败');
      }
      var current = data.current_session || {};
      var name = current.display_name || current.peer_id || '';
      var messageCount = Number(data.new_message_count || data.message_count || 0);
      var unreadSessions = Number(data.unread_session_count || 0);
      var unreadMessages = Number(data.unread_message_count || 0);
      var text = '收消息完成：左侧未读 ' + unreadSessions + ' 个会话 / ' + unreadMessages + ' 条';
      if (name) text += '，当前会话 ' + name + ' 新增 ' + messageCount + ' 条';
      setMsg(text, false);
      return Promise.all([loadPeers(), loadGroups()]);
    }).then(function() {
      if (state.activePeerId) return loadMessages();
    }).catch(function(err) {
      setMsg(err.message || '收消息失败', true);
    });
  }

  function syncSessions() {
    var id = activeAccountId();
    if (!id) return setMsg('请先选择账号', true);
    setMsg('正在同步会话...', false);
    return apiJson('/api/native-wechat/sessions/sync', {
      method: 'POST',
      body: { account_id: id, limit: 2000 }
    }).then(function(data) {
      var sessionCount = data.count || 0;
      return apiJson('/api/native-wechat/groups/sync', {
        method: 'POST',
        body: { account_id: id, limit: 2000 }
      }).then(function(groupData) {
        setMsg('同步完成：' + sessionCount + ' 条，会话中识别群聊 ' + (groupData.count || 0) + ' 个', false);
      }).catch(function() {
        setMsg('同步完成：' + sessionCount + ' 条', false);
      }).then(function() {
        return Promise.all([loadPeers(), loadContacts(), loadGroups()]);
      });
    }).catch(function(err) {
      setMsg(err.message || '同步会话失败', true);
    });
  }

  function syncContacts() {
    var id = activeAccountId();
    if (!id) return setMsg('请先选择账号', true);
    setMsg('正在同步通讯录...', false);
    return apiJson('/api/native-wechat/contacts/sync', {
      method: 'POST',
      body: { account_id: id, limit: 10000 }
    }).then(function(data) {
      var count = Number(data.count || 0);
      var total = Number(data.total_after || 0);
      var note = total && total !== count ? ('，库内共 ' + total + ' 条') : '';
      if (data.partial) note += '，本次可能未扫到底，已保留历史记录';
      setMsg('同步完成：本次读取 ' + count + ' 条' + note, false);
      return Promise.all([loadContacts(), loadPeers()]);
    }).catch(function(err) {
      setMsg(err.message || '同步通讯录失败', true);
    });
  }

  function syncGroupMembers() {
    var id = activeAccountId();
    if (!id) return setMsg('请先选择账号', true);
    if (!state.activeGroupKey) return setMsg('请先选择群', true);
    setMsg('正在同步群成员...', false);
    return apiJson('/api/native-wechat/groups/members/sync', {
      method: 'POST',
      body: { account_id: id, group_key: state.activeGroupKey }
    }).then(function(data) {
      setMsg('同步完成：' + (data.count || 0) + ' 人', false);
      return loadGroupMembers();
    }).catch(function(err) {
      setMsg(err.message || '同步群成员失败', true);
    });
  }

  function addFriend() {
    var id = activeAccountId();
    var keywords = splitTargets((($('nativeWechatFriendKeyword') || {}).value || '').trim());
    var applyMessage = (($('nativeWechatFriendApplyMessage') || {}).value || '').trim();
    var remark = (($('nativeWechatFriendRemark') || {}).value || '').trim();
    var permission = (($('nativeWechatFriendPermission') || {}).value || '朋友圈').trim();
    var tags = (($('nativeWechatFriendTags') || {}).value || '').split(/[,，]/).map(function(x) { return x.trim(); }).filter(Boolean);
    if (!id) return setMsg('请先选择账号', true);
    if (!keywords.length) return setMsg('请填写好友关键词', true);
    setChip('nativeWechatFriendState', '排队中');
    return apiJson('/api/native-wechat/friends/add', {
      method: 'POST',
      body: {
        account_id: id,
        keywords: keywords,
        apply_message: applyMessage,
        remark: remark,
        tags: tags,
        permission: permission,
        prepare_only: false
      }
    }).then(function(data) {
      setChip('nativeWechatFriendState', '已排队');
      setMsg((data && data.message) || ('已加入队列：' + keywords.length + ' 个目标'), false);
      return loadTasks();
    }).catch(function(err) {
      setChip('nativeWechatFriendState', '失败');
      setMsg(err.message || '提交好友申请失败', true);
    });
  }

  function submitMomentsLike() {
    var id = activeAccountId();
    var targets = selectedContactValues();
    var dryRunNode = $('nativeWechatContactMomentsDryRun');
    var scrollNode = $('nativeWechatContactMomentsMaxScrolls');
    var maxScrolls = Number((scrollNode && scrollNode.value) || 20);
    if (!id) return setMsg('请先选择账号', true);
    if (!targets.length) return setMsg('请先选择联系人', true);
    setMsg('正在加入朋友圈点赞队列...', false);
    return apiJson('/api/native-wechat/moments/like', {
      method: 'POST',
      body: {
        account_id: id,
        targets: targets,
        dry_run: dryRunNode ? !!dryRunNode.checked : true,
        max_scrolls: maxScrolls || 20
      }
    }).then(function(data) {
      setMsg((data && data.message) || ('已加入队列：' + targets.length + ' 个目标'), false);
      return loadTasks();
    }).catch(function(err) {
      setMsg(err.message || '朋友圈任务提交失败', true);
    });
  }

  function submitMomentsComment() {
    var id = activeAccountId();
    var targets = selectedContactValues();
    var dryRunNode = $('nativeWechatContactMomentsDryRun');
    var scrollNode = $('nativeWechatContactMomentsMaxScrolls');
    var maxScrolls = Math.min(30, Number((scrollNode && scrollNode.value) || 6));
    if (!id) return setMsg('请先选择账号', true);
    if (!targets.length) return setMsg('请先选择联系人', true);
    setMsg('正在加入朋友圈评论队列...', false);
    return apiJson('/api/native-wechat/moments/comment', {
      method: 'POST',
      body: {
        account_id: id,
        targets: targets,
        dry_run: dryRunNode ? !!dryRunNode.checked : false,
        max_scrolls: maxScrolls || 6
      }
    }).then(function(data) {
      setMsg((data && data.message) || ('已加入队列：' + targets.length + ' 个目标'), false);
      return loadTasks();
    }).catch(function(err) {
      setMsg(err.message || '朋友圈评论任务提交失败', true);
    });
  }

  function momentsLocalFileKind(file) {
    var type = String((file && file.type) || '').toLowerCase();
    var name = String((file && file.name) || '').toLowerCase();
    if (type.indexOf('image/') === 0 || /\.(png|jpe?g|webp|gif|bmp)$/i.test(name)) return 'image';
    if (type.indexOf('video/') === 0 || /\.(mp4|mov|webm|avi|mkv)$/i.test(name)) return 'video';
    return '';
  }

  function submitMomentsPublish() {
    var id = activeAccountId();
    var content = (($('nativeWechatMomentsContent') || {}).value || '').trim();
    var mediaType = (($('nativeWechatMomentsMediaType') || {}).value || 'image_text').trim() || 'image_text';
    var visibility = (($('nativeWechatMomentsVisibility') || {}).value || 'public').trim() || 'public';
    var files = state.momentsFiles || [];
    var contentRecord = state.momentsContentRecord && typeof state.momentsContentRecord === 'object' ? state.momentsContentRecord : null;
    var remoteFiles = files.filter(function(file) { return !!(file && (file.remote || file.source_url || file.url)); });
    var imageCount = files.filter(function(file) { return momentsLocalFileKind(file) === 'image'; }).length;
    var videoCount = files.filter(function(file) { return momentsLocalFileKind(file) === 'video'; }).length;
    if (!id) return setMsg('请先连接或选择账号', true);
    if (remoteFiles.length && (!contentRecord || !contentRecord.source || !contentRecord.source_id)) return setMsg('内容库图片缺少来源记录，请重新从内容库进入发布。', true);
    if (!content && !files.length) return setMsg('请填写正文或选择素材', true);
    if (mediaType === 'image_text' && videoCount > 0) return setMsg('图文朋友圈只能选择图片；发视频请切换到视频类型', true);
    if (mediaType === 'image_text' && imageCount > 9) return setMsg('朋友圈图文一次最多选择 9 张图片', true);
    if (mediaType === 'video' && (videoCount !== files.length || videoCount > 1)) return setMsg('朋友圈视频一次只能选择 1 个视频', true);
    if (state.momentsSubmitting) return;
    state.momentsSubmitting = true;
    var submitBtn = $('nativeWechatMomentsPublishBtn');
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = '提交中...';
    }
    setChip('nativeWechatMomentsState', '排队中');
    setMsg(files.length ? '正在准备朋友圈素材...' : '正在提交朋友圈发布...', false);
    if (remoteFiles.length && contentRecord && contentRecord.source && contentRecord.source_id) {
      var selectedAccount = state.accounts.find(function(item) { return String(item.account_id || '') === String(id); }) || {};
      var remoteRefs = [];
      remoteFiles.forEach(function(file) {
        var url = String(file.source_url || file.url || '').trim();
        var assetId = String(file.asset_id || file.image_asset_id || '').trim();
        var existing = remoteRefs.find(function(ref) {
          return (url && ref.image_url === url) || (assetId && ref.image_asset_id === assetId);
        });
        if (existing) {
          if (url && !existing.image_url) existing.image_url = url;
          if (assetId && !existing.image_asset_id) existing.image_asset_id = assetId;
        } else if (url || assetId) {
          remoteRefs.push({ image_url: url, image_asset_id: assetId });
        }
      });
      var remoteUrls = remoteRefs.map(function(ref) { return ref.image_url || ''; });
      var remoteAssetIds = remoteRefs.map(function(ref) { return ref.image_asset_id || ''; });
      cloudJson('/api/content-records/publish-request', {
        method: 'POST',
        body: {
          source: contentRecord.source,
          source_id: contentRecord.source_id,
          platform: 'wechat_moments',
          platform_name: '微信朋友圈',
          account_id: id,
          account_nickname: selectedAccount.name || selectedAccount.user_id || id,
          installation_id: typeof getOrCreateInstallationId === 'function' ? getOrCreateInstallationId() : '',
          title: contentRecord.title || '',
          description: content,
          content: content,
          media_type: mediaType,
          visibility: visibility,
          image_urls: remoteUrls.slice(0, 9),
          image_asset_ids: remoteAssetIds.slice(0, 9),
          publish_draft: {
            images: remoteRefs.slice(0, 9),
            image_urls: remoteUrls.slice(0, 9),
            image_asset_ids: remoteAssetIds.slice(0, 9)
          }
        }
      }).then(function(data) {
        setChip('nativeWechatMomentsState', '已提交');
        setMsg((data && data.reused ? '已复用已有朋友圈发布任务' : '朋友圈发布任务已提交，Online 将自动执行') , false);
        var input = $('nativeWechatMomentsContent');
        if (input) input.value = '';
        state.momentsFiles = [];
        state.momentsContentRecord = null;
        renderMomentsFiles();
      }).catch(function(err) {
        setChip('nativeWechatMomentsState', '失败');
        setMsg(err.message || '朋友圈发布失败', true);
      }).finally(function() {
        state.momentsSubmitting = false;
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.textContent = '发布到朋友圈';
        }
      });
      return;
    }
    Promise.all(files.map(uploadWechatFile)).then(function(uploaded) {
      setMsg('正在加入朋友圈发布队列...', false);
      return apiJson('/api/native-wechat/moments/publish', {
        method: 'POST',
        body: {
          account_id: id,
          content: content,
          attachments: uploaded,
          media_type: mediaType,
          visibility: visibility
        }
      });
    }).then(function(data) {
      setChip('nativeWechatMomentsState', '已排队');
      setMsg((data && data.message) || '朋友圈发布任务已加入队列', false);
      var input = $('nativeWechatMomentsContent');
      if (input) input.value = '';
      state.momentsFiles = [];
      renderMomentsFiles();
      return loadTasks();
    }).catch(function(err) {
      setChip('nativeWechatMomentsState', '失败');
      setMsg(err.message || '朋友圈发布失败', true);
    }).finally(function() {
      state.momentsSubmitting = false;
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = '发布到朋友圈';
      }
    });
  }

  window.prefillNativeWechatMoments = function(payload) {
    payload = payload && typeof payload === 'object' ? payload : {};
    switchTab('moments');
    var input = $('nativeWechatMomentsContent');
    if (input) input.value = String(payload.content || payload.body || '').trim();
    var mediaType = $('nativeWechatMomentsMediaType');
    if (mediaType) mediaType.value = String(payload.media_type || 'image_text').trim() || 'image_text';
    var urls = Array.isArray(payload.image_urls) ? payload.image_urls : [];
    var assetIds = Array.isArray(payload.image_asset_ids) ? payload.image_asset_ids : [];
    var imageRefs = Array.isArray(payload.images)
      ? payload.images.slice()
      : (Array.isArray(payload.attachments) ? payload.attachments.slice() : []);
    if (!imageRefs.length) {
      for (var refIndex = 0; refIndex < Math.max(urls.length, assetIds.length); refIndex += 1) {
        imageRefs.push({ image_url: urls[refIndex] || '', image_asset_id: assetIds[refIndex] || '' });
      }
    }
    state.momentsFiles = [];
    for (var index = 0; index < imageRefs.length && index < 9; index += 1) {
      var imageRef = imageRefs[index] && typeof imageRefs[index] === 'object' ? imageRefs[index] : {};
      var url = String(imageRef.image_url || imageRef.source_url || imageRef.url || '').trim();
      var assetId = String(imageRef.image_asset_id || imageRef.asset_id || '').trim();
      if (!url && !assetId) continue;
      state.momentsFiles.push({
        remote: true,
        source_url: url,
        asset_id: assetId,
        name: '生成图片 ' + (index + 1) + '.jpg',
        type: 'image/jpeg',
        size: 0
      });
    }
    state.momentsContentRecord = {
      source: String(payload.source || '').trim(),
      source_id: String(payload.source_id || '').trim(),
      title: String(payload.title || '').trim()
    };
    renderMomentsFiles();
    setChip('nativeWechatMomentsState', '已带入');
    setMsg('已带入文案和生成图片，确认账号后即可发布。', false);
    if (input) input.focus();
  };

  function selectedContactsToSend() {
    var recipients = selectedContactValues();
    if (!recipients.length) return setMsg('请先选择联系人', true);
    var input = $('nativeWechatRecipientInput');
    if (input) input.value = recipients.join('\n');
    state.activePeerId = recipients[0] || '';
    switchTab('messages');
    renderPeers();
    renderMessages();
    setMsg('已填入 ' + recipients.length + ' 个联系人', false);
  }

  function createGroupFromSelectedContacts() {
    var id = activeAccountId();
    var contacts = selectedContactValues();
    if (!id) return setMsg('请先选择账号', true);
    if (contacts.length < 2) return setMsg('创建群至少选择2个联系人', true);
    setMsg('正在加入创建群队列...', false);
    return apiJson('/api/native-wechat/groups/create', {
      method: 'POST',
      body: { account_id: id, contacts: contacts }
    }).then(function(data) {
      setMsg((data && data.message) || ('已加入队列：' + contacts.length + ' 个联系人'), false);
      return loadTasks();
    }).catch(function(err) {
      setMsg(err.message || '创建群失败', true);
    });
  }

  function sendMessage() {
    var id = activeAccountId();
    var recipients = splitTargets((($('nativeWechatRecipientInput') || {}).value || '').trim());
    var content = (($('nativeWechatSendContent') || {}).value || '').trim();
    if (!id) return setMsg('请先连接或选择账号', true);
    if (!recipients.length) return setMsg('请填写接收人', true);
    if (!content && !state.sendFiles.length) return setMsg('请填写发送内容或选择附件', true);
    setChip('nativeWechatSendState', '排队中');
    setMsg(state.sendFiles.length ? '正在上传附件...' : '正在发送...', false);
    Promise.all(state.sendFiles.map(uploadWechatFile)).then(function(files) {
      setMsg('正在加入发送队列...', false);
      return apiJson('/api/native-wechat/messages/send', {
      method: 'POST',
        body: { account_id: id, to_usernames: recipients, content: content, target_type: 'direct', attachments: files }
      });
    }).then(function(data) {
      var failed = Number(data.failed_count || 0);
      var taskError = data && data.task && data.task.error_message ? String(data.task.error_message) : '';
      setChip('nativeWechatSendState', failed ? '发送失败' : '已排队');
      setMsg(failed ? ('发送失败' + (taskError ? '：' + taskError : '，请查看任务记录')) : ('已加入队列：' + recipients.length + ' 个目标'), failed > 0);
      if (!failed) {
        var input = $('nativeWechatSendContent');
        if (input) input.value = '';
        state.sendFiles = [];
        renderSendFiles();
      }
      state.activePeerId = recipients[0] || '';
      return loadPeers();
    }).then(function() {
      return loadTasks();
    }).catch(function(err) {
      setChip('nativeWechatSendState', '发送失败');
      setMsg(err.message || '发送失败', true);
    });
  }

  function closeModals() {
    document.querySelectorAll('.native-wechat-modal-mask.show').forEach(function(node) {
      node.classList.remove('show');
    });
  }

  function shortText(value, maxLen) {
    var text = String(value || '').replace(/\s+/g, ' ').trim();
    var limit = maxLen || 240;
    return text.length > limit ? text.slice(0, limit) + '...' : text;
  }

  function commentStatusText(status) {
    var map = {
      submitted: '已评论',
      ready: '已生成',
      failed: '失败',
      skipped: '跳过',
      already_commented: '已评论过',
      unknown: '未知'
    };
    return map[status] || status || '';
  }

  function formatMomentsCommentResult(item) {
    if (!item) return '';
    var lines = [];
    var head = [item.target || '', commentStatusText(item.status)].filter(Boolean).join(' · ');
    if (head) lines.push(head);
    if (item.post_text) lines.push('帖子：' + shortText(item.post_text, 360));
    if (item.media_summary) lines.push('AI理解：' + shortText(item.media_summary, 360));
    if (item.reply) lines.push('评论：' + item.reply);
    if (item.reason) lines.push('原因：' + item.reason);
    if (item.error) lines.push('错误：' + item.error);
    return lines.join('\n');
  }

  function formatMomentsCommentStep(item) {
    if (!item || !item.step) return '';
    var map = {
      open_contact_profile: '打开联系人',
      open_contact_moments: '进入朋友圈',
      moments_comment_open_post: '打开帖子',
      moments_comment_post_found: '找到帖子',
      moments_comment_snapshot: '截取帖子',
      moments_comment_media_understood: 'AI理解完成',
      moments_comment_media_understand_failed: 'AI理解失败',
      moments_comment_reply_generated: '生成评论',
      moments_comment_ready: '等待提交',
      moments_comment_submit_failed: '提交失败',
      moments_comment_submitted: '评论成功',
      moments_comment_skip: '跳过',
      moments_comment_stop_after_24h: '超过24小时停止',
      contact_album_back: '返回列表',
      close_contact_moments: '关闭朋友圈'
    };
    var label = map[item.step] || item.step;
    var parts = [label];
    if (item.target) parts.push(item.target);
    if (item.time) parts.push(item.time);
    if (item.reply) parts.push('评论：' + item.reply);
    if (item.summary) parts.push('AI理解：' + shortText(item.summary, 220));
    if (item.post_text) parts.push('帖子：' + shortText(item.post_text, 220));
    if (item.reason) parts.push('原因：' + item.reason);
    if (item.error) parts.push('错误：' + item.error);
    return parts.filter(Boolean).join(' · ');
  }

  function detectLocalWechat() {
    setMsg('正在检测本机微信...', false);
    return apiJson('/api/native-wechat/local/status').then(function(status) {
      state.driver = status || null;
      if (status && (status.diagnostic || status.diagnostic_code)) state.lastDiagnostic = status;
      return loadAccounts().then(function(items) {
        return { status: status || {}, items: items || [] };
      });
    }).then(function(result) {
      var status = result.status || {};
      var items = result.items || [];
      if (!items.length) {
        if (state.lastDiagnostic || status.diagnostic || status.diagnostic_code) {
          setMsg(withDiagnostic('\u672a\u68c0\u6d4b\u5230\u672c\u673a\u5fae\u4fe1\uff0c\u8bf7\u6253\u5f00\u5df2\u767b\u5f55\u7684 PC \u5fae\u4fe1\u4e3b\u7a97\u53e3\u540e\u91cd\u8bd5\u3002', state.lastDiagnostic || status), true);
          return;
        }
        setMsg('未检测到本机微信，请打开已登录的 PC 微信主窗口后重试。', true);
        return;
      }
      var ready = !!status.driver_ready || items.some(function(item) { return item.source === 'pc_wechat' && item.driver_ready; });
      setMsg(ready ? '已检测到本机微信，可以下发任务。' : '已检测到本机微信，但本机控制组件不可用，请检查 pywin32。', !ready);
      return loadPeers();
    }).catch(function(err) {
      setMsg(err.message || '检测本机微信失败', true);
    });
  }

  function showJsonDetail(titleText, data) {
    var modal = $('nativeWechatDetailModal');
    var title = $('nativeWechatDetailTitle');
    var body = $('nativeWechatDetailBody');
    if (title) title.textContent = titleText || '详情';
    if (body) {
      body.className = '';
      body.innerHTML = '<pre style="white-space:pre-wrap;word-break:break-word;margin:0;font-size:12px;line-height:1.55;">' +
        esc(JSON.stringify(data || {}, null, 2)) +
        '</pre>';
    }
    if (modal) modal.classList.add('show');
  }

  function diagnoseWechatControls() {
    var id = activeAccountId();
    if (!id) {
      setMsg('\u6b63\u5728\u751f\u6210\u5fae\u4fe1\u68c0\u6d4b\u65e5\u5fd7\u7801...', false);
      return apiJson('/api/native-wechat/local/diagnostic-code').then(function(data) {
        if (data && (data.diagnostic || data.diagnostic_code)) state.lastDiagnostic = data;
        setMsg(withDiagnostic('\u65e5\u5fd7\u7801\u5df2\u751f\u6210\uff0c\u8bf7\u628a\u8fd9\u4e2a\u7801\u53d1\u7ed9\u6211\u4eec\u5b9a\u4f4d\u3002', data), false);
        showJsonDetail('\u5fae\u4fe1\u68c0\u6d4b\u65e5\u5fd7', data);
      }).catch(function(err) {
        setMsg(err.message || '\u751f\u6210\u65e5\u5fd7\u7801\u5931\u8d25', true);
      });
    }
    if (!id) return setMsg('请先选择本机微信账号', true);
    setMsg('正在诊断微信控件树...', false);
    return apiJson('/api/native-wechat/local/diagnose?account_id=' + encodeURIComponent(id)).then(function(data) {
      if (data && (data.diagnostic || data.diagnostic_code)) state.lastDiagnostic = data;
      setMsg('控件诊断完成，把弹窗里的结果发回来即可定位。', false);
      showJsonDetail('微信控件诊断', data);
    }).catch(function(err) {
      setMsg(err.message || '控件诊断失败', true);
    });
  }

  function showTaskDetail(task) {
    var modal = $('nativeWechatDetailModal');
    var title = $('nativeWechatDetailTitle');
    var body = $('nativeWechatDetailBody');
    if (title) title.textContent = '任务详情';
    if (body) body.className = 'native-wechat-detail-list';
    if (body) {
      var targets = Array.isArray(task.targets) ? task.targets.join('，') : '';
      var payload = task.payload || {};
      var attachments = Array.isArray(payload.attachments) ? payload.attachments : [];
      var attachmentText = attachments.map(function(item) {
        return item.filename || item.name || item.local_path || item.path || '';
      }).filter(Boolean).join('\n');
      var commentResults = Array.isArray(payload.comment_results) ? payload.comment_results : [];
      var commentText = commentResults.map(formatMomentsCommentResult).filter(Boolean).join('\n\n');
      var stepText = (Array.isArray(payload.steps) ? payload.steps : []).map(formatMomentsCommentStep).filter(Boolean).join('\n');
      var rows = [
        ['任务', taskTypeText(task.task_type)],
        ['状态', taskStatusText(task.status)],
        ['账号', task.account_id],
        ['接收人', targets],
        ['内容', payload.text || payload.content || ''],
        ['评论结果', commentText],
        ['执行过程', stepText],
        ['进度', (task.processed || 0) + '/' + (task.planned_total || 0)],
        ['附件', attachmentText],
        ['成功', task.success || 0],
        ['失败', task.failed || 0],
        ['错误', task.error_message || ''],
        ['创建时间', formatTime(task.created_at)]
      ].filter(function(row) {
        return row[1] !== undefined && row[1] !== null && String(row[1]).trim() !== '';
      });
      body.innerHTML = rows.map(function(row) {
        return '<strong>' + esc(row[0]) + '</strong><span>' + esc(row[1]) + '</span>';
      }).join('');
    }
    if (modal) modal.classList.add('show');
  }

  function resetAccountScopedState() {
    state.peers = [];
    state.activePeerId = '';
    state.activePeer = null;
    state.messages = [];
    state.contacts = [];
    state.groups = [];
    state.activeGroupKey = '';
    state.groupMembers = [];
    state.tasks = [];
    state.contactSelected = {};
    state.autoReply = null;
    resetAllPagination();
    ['nativeWechatPeerSearch', 'nativeWechatContactSearch', 'nativeWechatGroupSearch', 'nativeWechatContactPickerSearch'].forEach(function(id) {
      var input = $(id);
      if (input) input.value = '';
    });
    state.groupInviteContactSearch = '';
    renderPeers();
    renderContacts();
    renderGroups();
    renderGroupMembers();
    renderMessages();
    renderAutoReply();
    renderAutoReplyConfig();
  }

  function bindEvents(root) {
    var back = $('nativeWechatBackBtn');
    if (back) back.addEventListener('click', function() {
      if (typeof showView === 'function') showView('skill-store');
      else if (typeof window.showLobsterView === 'function') window.showLobsterView('skill-store');
    });
    var accountSelect = $('nativeWechatAccountSelect');
    if (accountSelect) accountSelect.addEventListener('change', function() {
      state.activeAccountId = accountSelect.value || '';
      resetAccountScopedState();
      closeModals();
      loadPeers(1);
      loadContacts(1);
      loadGroups(1);
      loadTasks();
      loadAutoReplyConfig();
      loadAutoReplyMemoryDocs();
    });
    var peerSearch = $('nativeWechatPeerSearch');
    if (peerSearch) peerSearch.addEventListener('input', debounce(function() { loadPeers(1); }, 250));
    var contactSearch = $('nativeWechatContactSearch');
    if (contactSearch) contactSearch.addEventListener('input', debounce(function() { loadContacts(1); }, 250));
    var groupSearch = $('nativeWechatGroupSearch');
    if (groupSearch) groupSearch.addEventListener('input', debounce(function() { loadGroups(1); }, 250));

    [
      ['nativeWechatRefreshAccountsBtn', loadAccounts],
      ['nativeWechatRefreshAccountsBtn2', loadAccounts],
      ['nativeWechatRefreshStrategyBtn', loadStrategy],
      ['nativeWechatRefreshPeersBtn', loadPeers],
      ['nativeWechatRefreshContactsBtn', loadContacts],
      ['nativeWechatRefreshGroupsBtn', loadGroups],
      ['nativeWechatRefreshMessagesBtn', syncActiveMessages],
      ['nativeWechatRefreshTasksBtn', loadTasks]
    ].forEach(function(pair) {
      var node = $(pair[0]);
      if (node) node.addEventListener('click', pair[1]);
    });

    var loginBtn = $('nativeWechatLoginBtn');
    if (loginBtn) loginBtn.addEventListener('click', detectLocalWechat);
    var diagnoseBtn = $('nativeWechatDiagnoseBtn');
    if (diagnoseBtn) diagnoseBtn.addEventListener('click', diagnoseWechatControls);
    var poll = $('nativeWechatPollBtn');
    if (poll) poll.addEventListener('click', pollUpdates);
    var autoReplyToggle = $('nativeWechatAutoReplyEnabled');
    if (autoReplyToggle) {
      autoReplyToggle.addEventListener('change', function() {
        saveAutoReplyConfig(autoReplyToggle.checked);
      });
    }
    var autoReplyRunBtn = $('nativeWechatAutoReplyRunBtn');
    if (autoReplyRunBtn) autoReplyRunBtn.addEventListener('click', runAutoReplyOnce);
    var autoReplyConfigSaveBtn = $('nativeWechatAutoReplyConfigSaveBtn');
    if (autoReplyConfigSaveBtn) autoReplyConfigSaveBtn.addEventListener('click', saveAutoReplyDetails);
    var send = $('nativeWechatSendBtn');
    if (send) send.addEventListener('click', sendMessage);
    var fileInput = $('nativeWechatFileInput');
    var chooseFileBtn = $('nativeWechatChooseFileBtn');
    if (chooseFileBtn && fileInput) {
      chooseFileBtn.addEventListener('click', function() {
        fileInput.click();
      });
    }
    if (fileInput) {
      fileInput.addEventListener('change', function() {
        var files = Array.prototype.slice.call(fileInput.files || []);
        if (files.length) {
          state.sendFiles = state.sendFiles.concat(files);
          renderSendFiles();
          setChip('nativeWechatSendState', '待发送');
        }
        fileInput.value = '';
      });
    }
    var clearFileBtn = $('nativeWechatClearFilesBtn');
    if (clearFileBtn) {
      clearFileBtn.addEventListener('click', function() {
        state.sendFiles = [];
        renderSendFiles();
      });
    }
    var momentsFileInput = $('nativeWechatMomentsFileInput');
    var momentsChooseFileBtn = $('nativeWechatMomentsChooseFileBtn');
    if (momentsChooseFileBtn && momentsFileInput) {
      momentsChooseFileBtn.addEventListener('click', function() {
        momentsFileInput.click();
      });
    }
    if (momentsFileInput) {
      momentsFileInput.addEventListener('change', function() {
        var files = Array.prototype.slice.call(momentsFileInput.files || []);
        if (files.length) {
          state.momentsFiles = state.momentsFiles.concat(files);
          renderMomentsFiles();
          setChip('nativeWechatMomentsState', '待发布');
        }
        momentsFileInput.value = '';
      });
    }
    var momentsClearFileBtn = $('nativeWechatMomentsClearFilesBtn');
    if (momentsClearFileBtn) {
      momentsClearFileBtn.addEventListener('click', function() {
        state.momentsFiles = [];
        renderMomentsFiles();
      });
    }
    var momentsPublishBtn = $('nativeWechatMomentsPublishBtn');
    if (momentsPublishBtn) momentsPublishBtn.addEventListener('click', submitMomentsPublish);
    var syncSessionBtn = $('nativeWechatSyncSessionsBtn');
    if (syncSessionBtn) syncSessionBtn.addEventListener('click', syncSessions);
    var syncContactsBtn = $('nativeWechatSyncContactsBtn');
    if (syncContactsBtn) syncContactsBtn.addEventListener('click', syncContacts);
    var syncGroupMembersBtn = $('nativeWechatSyncGroupMembersBtn');
    if (syncGroupMembersBtn) syncGroupMembersBtn.addEventListener('click', syncGroupMembers);
    var loadOlderMessagesBtn = $('nativeWechatLoadOlderMessagesBtn');
    if (loadOlderMessagesBtn) loadOlderMessagesBtn.addEventListener('click', loadOlderMessages);
    var addFriendBtn = $('nativeWechatAddFriendBtn');
    if (addFriendBtn) addFriendBtn.addEventListener('click', addFriend);
    var contactSelectAllBtn = $('nativeWechatContactSelectAllBtn');
    if (contactSelectAllBtn) contactSelectAllBtn.addEventListener('click', function() {
      state.contacts.forEach(function(item) {
        var value = contactValue(item);
        if (value) state.contactSelected[value.toLowerCase()] = value;
      });
      renderContacts();
    });
    var contactClearBtn = $('nativeWechatContactClearBtn');
    if (contactClearBtn) contactClearBtn.addEventListener('click', function() {
      state.contactSelected = {};
      renderContacts();
    });
    var contactSendBtn = $('nativeWechatContactSendBtn');
    if (contactSendBtn) contactSendBtn.addEventListener('click', selectedContactsToSend);
    var contactMomentsLikeBtn = $('nativeWechatContactMomentsLikeBtn');
    if (contactMomentsLikeBtn) contactMomentsLikeBtn.addEventListener('click', submitMomentsLike);
    var contactMomentsCommentBtn = $('nativeWechatContactMomentsCommentBtn');
    if (contactMomentsCommentBtn) contactMomentsCommentBtn.addEventListener('click', submitMomentsComment);
    var contactCreateGroupBtn = $('nativeWechatContactCreateGroupBtn');
    if (contactCreateGroupBtn) contactCreateGroupBtn.addEventListener('click', createGroupFromSelectedContacts);
    var groupContactSearch = $('nativeWechatContactPickerSearch');
    if (groupContactSearch) groupContactSearch.addEventListener('input', debounce(function() {
      state.groupInviteContactSearch = groupContactSearch.value || '';
      loadContactPicker(1);
    }, 250));
    var clearPrimaryContactBtn = $('nativeWechatContactPickerClear');
    if (clearPrimaryContactBtn) clearPrimaryContactBtn.addEventListener('click', function() { setGroupInvitePrimaryContact('', ''); });

    root.querySelectorAll('[data-native-wechat-tab]').forEach(function(btn) {
      btn.addEventListener('click', function() { switchTab(btn.getAttribute('data-native-wechat-tab')); });
    });
    root.querySelectorAll('[data-native-wechat-close]').forEach(function(btn) {
      btn.addEventListener('click', closeModals);
    });
    root.addEventListener('click', function(evt) {
      var pageButton = evt.target.closest('[data-native-page-list][data-native-page-action]');
      if (pageButton) {
        if (pageButton.disabled) return;
        var pageKey = pageButton.getAttribute('data-native-page-list') || '';
        var action = pageButton.getAttribute('data-native-page-action') || '';
        var pageMeta = state.pagination[pageKey];
        if (!pageMeta) return;
        var targetPage = Number(pageMeta.page || 1);
        if (action === 'first') targetPage = 1;
        if (action === 'prev') targetPage -= 1;
        if (action === 'next') targetPage += 1;
        if (action === 'last') targetPage = pageCount(pageMeta);
        loadPaginationPage(pageKey, Math.min(pageCount(pageMeta), Math.max(1, targetPage)));
        return;
      }
      var removeFile = evt.target.closest('[data-native-file-remove]');
      if (removeFile) {
        var idx = Number(removeFile.getAttribute('data-native-file-remove'));
        if (!Number.isNaN(idx)) state.sendFiles.splice(idx, 1);
        renderSendFiles();
        return;
      }
      var removeMomentsFile = evt.target.closest('[data-native-moments-file-remove]');
      if (removeMomentsFile) {
        var momentsIdx = Number(removeMomentsFile.getAttribute('data-native-moments-file-remove'));
        if (!Number.isNaN(momentsIdx)) state.momentsFiles.splice(momentsIdx, 1);
        renderMomentsFiles();
        return;
      }
      var peer = evt.target.closest('[data-native-peer]');
      if (peer) {
        state.activePeerId = peer.getAttribute('data-native-peer') || '';
        state.activePeer = state.peers.find(function(item) { return item.peer_id === state.activePeerId; }) || null;
        state.messages = [];
        state.messageHistory.total = 0;
        state.messageHistory.loading = false;
        beginRequest('messages');
        var input = $('nativeWechatRecipientInput');
        if (input) input.value = state.activePeerId;
        renderPeers();
        renderMessages();
        loadMessages();
        return;
      }
      var groupContact = evt.target.closest('[data-native-group-contact]');
      if (groupContact) {
        setGroupInvitePrimaryContact(
          groupContact.getAttribute('data-native-group-contact') || '',
          groupContact.getAttribute('data-native-group-contact-name') || ''
        );
        return;
      }
      var account = evt.target.closest('[data-native-account]');
      if (account) {
        state.activeAccountId = account.getAttribute('data-native-account') || '';
        resetAccountScopedState();
        renderAccountSelect();
        closeModals();
        loadPeers(1);
        loadAutoReplyConfig();
        loadAutoReplyMemoryDocs();
        loadContacts(1);
        loadGroups(1);
        loadTasks();
        return;
      }
      var contactSelect = evt.target.closest('[data-native-contact-select]');
      if (contactSelect) {
        var contactName = contactSelect.getAttribute('data-native-contact-select') || '';
        var contactKey = contactName.toLowerCase();
        if (contactSelect.checked) state.contactSelected[contactKey] = contactName;
        else delete state.contactSelected[contactKey];
        renderContactSelectionState();
        return;
      }
      var group = evt.target.closest('[data-native-group]');
      if (group) {
        state.activeGroupKey = group.getAttribute('data-native-group') || '';
        state.groupMembers = [];
        resetPagination('groupMembers');
        beginRequest('groupMembers');
        var groupInput = $('nativeWechatRecipientInput');
        if (groupInput) groupInput.value = state.activeGroupKey;
        renderGroups();
        loadGroupMembers();
        return;
      }
      var taskBtn = evt.target.closest('[data-native-task]');
      if (taskBtn) {
        var taskId = taskBtn.getAttribute('data-native-task');
        var local = state.tasks.find(function(x) { return x.id === taskId; });
        if (local) showTaskDetail(local);
      }
    });
  }

  window.initJuheWechatView = function() {
    var root = $('content-juhe-wechat');
    if (!root || root.dataset.inited === '1') return;
    root.dataset.inited = '1';
    bindEvents(root);
    renderAccountSelect();
    renderPeers();
    renderContacts();
    renderGroups();
    renderGroupMembers();
    renderMessages();
    renderTasks();
    renderSendFiles();
    renderMomentsFiles();
    renderAutoReply();
    renderAutoReplyConfig();
    loadAccounts().then(function() {
      return Promise.all([loadPeers(), loadContacts(), loadTasks(), loadStrategy(), loadAutoReplyConfig(), loadAutoReplyMemoryDocs()]);
    });
  };
})();
