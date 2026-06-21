(function() {
  var state = {
    configs: [],
    activeConfigId: '',
    contacts: [],
    groups: [],
    selectedContacts: new Set(),
    activeRoomUsername: '',
    lastUpload: null,
    mediaFiles: [],
    aiSessions: [],
    aiMessages: [],
    aiMemoryDocs: [],
    aiSelectedMemoryDocIds: new Set(),
    activeAiContact: '',
    tab: 'contacts'
  };

  function $(id) { return document.getElementById(id); }

  function esc(text) {
    if (typeof escapeHtml === 'function') return escapeHtml(String(text || ''));
    return String(text || '').replace(/[&<>"']/g, function(ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
  }

  function apiBase() {
    return (typeof API_BASE !== 'undefined' && API_BASE ? String(API_BASE) : '').replace(/\/$/, '');
  }

  function authHeaderJson() {
    var h = typeof authHeaders === 'function' ? Object.assign({}, authHeaders() || {}) : {};
    if (!h.Authorization && typeof token !== 'undefined' && token) h.Authorization = 'Bearer ' + token;
    h['Content-Type'] = 'application/json';
    return h;
  }

  function authHeaderUpload() {
    var h = typeof authHeaders === 'function' ? Object.assign({}, authHeaders() || {}) : {};
    if (!h.Authorization && typeof token !== 'undefined' && token) h.Authorization = 'Bearer ' + token;
    delete h['Content-Type'];
    return h;
  }

  function parseErr(data, fallback) {
    if (!data) return fallback || '请求失败';
    if (typeof data === 'string') return data;
    var detail = data.detail || data.error || data.message || data.msg;
    if (typeof detail === 'string') return detail;
    try { return JSON.stringify(detail || data); } catch (e) { return fallback || '请求失败'; }
  }

  function apiJson(path, opts) {
    opts = opts || {};
    var base = apiBase();
    if (!base) return Promise.reject(new Error('未配置服务器 API_BASE'));
    var req = { method: opts.method || 'GET', headers: authHeaderJson() };
    if (opts.body !== undefined) req.body = JSON.stringify(opts.body || {});
    return fetch(base + path, req).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok) throw new Error(parseErr(data, '请求失败'));
        return data;
      });
    });
  }

  function uploadLocalFile(file) {
    var base = apiBase();
    if (!base) return Promise.reject(new Error('未配置服务器 API_BASE'));
    var fd = new FormData();
    fd.append('file', file);
    return fetch(base + '/api/assets/upload', {
      method: 'POST',
      headers: authHeaderUpload(),
      body: fd
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok) throw new Error(parseErr(data, '文件上传失败'));
        var url = data.source_url || data.public_url || data.url || (data.asset && data.asset.source_url) || '';
        if (!url || String(url).indexOf('http') !== 0) {
          throw new Error('文件上传后没有可公网访问的链接，无法交给微信协议发送');
        }
        return { url: url, asset: data };
      });
    });
  }

  function setMsg(text, isErr) {
    var node = $('juheMsg');
    if (!node) return;
    node.textContent = text || '';
    node.className = 'juhe-msg' + (isErr ? ' err' : '');
    node.style.display = text ? 'block' : 'none';
  }

  function activeConfigId() {
    var sel = $('juheConfigSelect');
    return String((sel && sel.value) || state.activeConfigId || '');
  }

  function selectedUsernames() {
    return Array.from(state.selectedContacts);
  }

  function itemUsername(item) {
    if (!item) return '';
    if (typeof item === 'string') return item;
    return item.username || item.contact_key || item.user_name || item.UserName || item.wxid || item.room_username || item.chatroom_username || '';
  }

  function itemTitle(item) {
    if (!item) return '';
    if (typeof item === 'string') return item;
    if (item.display_name) return item.display_name;
    return item.nickname || item.nick_name || item.NickName || item.remark || item.RemarkName || item.alias || itemUsername(item) || '未命名';
  }

  function itemSub(item) {
    if (!item || typeof item === 'string') return '';
    var parts = [];
    var username = itemUsername(item);
    if (item.source || item.status) parts.push([item.source, item.status].filter(Boolean).join(' '));
    if (username) parts.push(username);
    if (item.signature) parts.push(item.signature);
    if (item.last_error) parts.push(item.last_error);
    if (item.province || item.city) parts.push([item.province, item.city].filter(Boolean).join(' '));
    return parts.join(' · ');
  }

  function formatJson(data) {
    try { return JSON.stringify(data || {}, null, 2); } catch (e) { return String(data || ''); }
  }

  function showDetail(title, data) {
    var modal = $('juheDetailModal');
    var t = $('juheDetailTitle');
    var body = $('juheDetailBody');
    if (t) t.textContent = title || '详情';
    if (body) body.textContent = typeof data === 'string' ? data : formatJson(data);
    if (modal) modal.classList.add('show');
  }

  function closeModals() {
    document.querySelectorAll('.juhe-modal-mask.show').forEach(function(node) {
      node.classList.remove('show');
    });
  }

  function switchTab(tab) {
    state.tab = tab || 'contacts';
    document.querySelectorAll('[data-juhe-tab]').forEach(function(btn) {
      btn.classList.toggle('active', btn.getAttribute('data-juhe-tab') === state.tab);
    });
    document.querySelectorAll('[data-juhe-panel]').forEach(function(panel) {
      panel.classList.toggle('active', panel.getAttribute('data-juhe-panel') === state.tab);
    });
    if (state.tab === 'ai') {
      loadAiConfig();
      loadAiMemoryDocs();
      loadAiSessions();
    }
    if (state.tab === 'logs') loadLogs();
  }

  function renderConfigSelect() {
    var sel = $('juheConfigSelect');
    if (!sel) return;
    if (!state.configs.length) {
      sel.innerHTML = '<option value="">暂无可用实例</option>';
      return;
    }
    if (!state.activeConfigId) state.activeConfigId = String(state.configs[0].id);
    sel.innerHTML = state.configs.map(function(cfg) {
      return '<option value="' + esc(cfg.id) + '">' + esc(cfg.label || ('实例 ' + cfg.id)) + '</option>';
    }).join('');
    sel.value = state.activeConfigId;
  }

  function loadConfigs() {
    setMsg('', false);
    return apiJson('/api/juhe-wechat/configs').then(function(data) {
      state.configs = Array.isArray(data.configs) ? data.configs : [];
      if (state.activeConfigId && !state.configs.some(function(c) { return String(c.id) === state.activeConfigId; })) {
        state.activeConfigId = '';
      }
      renderConfigSelect();
    }).catch(function(err) {
      state.configs = [];
      renderConfigSelect();
      setMsg(err.message || '加载实例失败', true);
    });
  }

  function checkStatus() {
    var id = activeConfigId();
    if (!id) return setMsg('请先选择可用实例', true);
    setMsg('正在检测在线状态...', false);
    apiJson('/api/juhe-wechat/configs/' + encodeURIComponent(id) + '/status', { method: 'POST', body: {} })
      .then(function(data) {
        setMsg('检测完成：' + (data.status_label || data.status || '未知'), !data.ok);
        return loadConfigs();
      })
      .catch(function(err) { setMsg(err.message || '检测失败', true); });
  }

  function loadContactCache() {
    var id = activeConfigId();
    if (!id) {
      state.contacts = [];
      state.selectedContacts.clear();
      renderContacts();
      return Promise.resolve();
    }
    return apiJson('/api/juhe-wechat/contacts/cache?config_id=' + encodeURIComponent(id)).then(function(data) {
      state.contacts = Array.isArray(data.contacts) ? data.contacts : (Array.isArray(data.items) ? data.items : []);
      state.selectedContacts.clear();
      renderContacts();
    }).catch(function(err) {
      setMsg(err.message || 'load saved contacts failed', true);
    });
  }

  function renderContacts() {
    var list = $('juheContactList');
    var count = $('juheSelectedCount');
    var targetCount = $('juheSendTargetCount');
    if (count) count.textContent = '已选 ' + state.selectedContacts.size;
    if (targetCount) targetCount.textContent = state.selectedContacts.size + ' 人';
    if (!list) return;
    var keyword = (($('juheContactSearch') || {}).value || '').trim().toLowerCase();
    var contacts = state.contacts.filter(function(item) {
      if (!keyword) return true;
      return [itemTitle(item), itemSub(item), itemUsername(item)].join(' ').toLowerCase().indexOf(keyword) >= 0;
    });
    if (!contacts.length) {
      list.className = 'juhe-empty';
      list.innerHTML = state.contacts.length ? '没有匹配联系人。' : '点击“刷新通讯录”加载联系人。';
      return;
    }
    list.className = 'juhe-list';
    list.innerHTML = contacts.map(function(item) {
      var username = itemUsername(item);
      var checked = state.selectedContacts.has(username);
      return '<div class="juhe-item' + (checked ? ' active' : '') + '" data-juhe-contact="' + esc(username) + '">' +
        '<input type="checkbox" class="juhe-contact-check" ' + (checked ? 'checked' : '') + '>' +
        '<div><div class="juhe-item-title">' + esc(itemTitle(item)) + '</div><div class="juhe-meta">' + esc(itemSub(item)) + '</div></div>' +
        '<button type="button" class="btn btn-ghost btn-sm" data-juhe-contact-detail="' + esc(username) + '">详情</button>' +
      '</div>';
    }).join('');
  }

  function refreshContacts() {
    var id = activeConfigId();
    if (!id) return setMsg('请先选择可用实例', true);
    setMsg('正在刷新通讯录...', false);
    apiJson('/api/juhe-wechat/contacts/refresh', {
      method: 'POST',
      body: { config_id: Number(id) }
    }).then(function(data) {
      state.contacts = Array.isArray(data.contacts) ? data.contacts : (Array.isArray(data.items) ? data.items.filter(function(x) { return String(itemUsername(x)).indexOf('@chatroom') < 0; }) : []);
      state.groups = Array.isArray(data.groups) ? data.groups : state.groups;
      state.selectedContacts.clear();
      renderContacts();
      renderRooms();
      setMsg('通讯录刷新完成：' + state.contacts.length + ' 个联系人', false);
    }).catch(function(err) {
      setMsg(err.message || '刷新通讯录失败', true);
    });
  }

  function toggleContact(username, checked) {
    if (!username) return;
    if (checked) state.selectedContacts.add(username);
    else state.selectedContacts.delete(username);
    renderContacts();
  }

  function selectAllContacts() {
    var keyword = (($('juheContactSearch') || {}).value || '').trim().toLowerCase();
    var visible = state.contacts.filter(function(item) {
      if (!keyword) return true;
      return [itemTitle(item), itemSub(item), itemUsername(item)].join(' ').toLowerCase().indexOf(keyword) >= 0;
    });
    var allSelected = visible.length && visible.every(function(item) { return state.selectedContacts.has(itemUsername(item)); });
    visible.forEach(function(item) {
      var username = itemUsername(item);
      if (!username) return;
      if (allSelected) state.selectedContacts.delete(username);
      else state.selectedContacts.add(username);
    });
    renderContacts();
  }

  function contactDetail(username) {
    var id = activeConfigId();
    var target = username || selectedUsernames()[0];
    if (!id) return setMsg('请先选择可用实例', true);
    if (!target || state.selectedContacts.size > 1 && !username) return setMsg('查看详情请单选一个联系人', true);
    setMsg('正在获取联系人详情...', false);
    apiJson('/api/juhe-wechat/contacts/detail', {
      method: 'POST',
      body: { config_id: Number(id), username: target }
    }).then(function(data) {
      setMsg('联系人详情已返回', false);
      showDetail('联系人详情', data.detail || data.upstream || data);
      loadContactCache();
      loadLogs();
    }).catch(function(err) { setMsg(err.message || '获取联系人详情失败', true); });
  }

  function modifyRemark() {
    var id = activeConfigId();
    var names = selectedUsernames();
    var remark = (($('juheRemarkInput') || {}).value || '').trim();
    if (!id) return setMsg('请先选择可用实例', true);
    if (names.length !== 1) return setMsg('修改备注请单选一个联系人', true);
    setMsg('正在修改备注...', false);
    apiJson('/api/juhe-wechat/contacts/remark', {
      method: 'POST',
      body: { config_id: Number(id), username: names[0], remark: remark }
    }).then(function(data) {
      setMsg('备注修改请求已提交', !data.ok);
      loadLogs();
    }).catch(function(err) { setMsg(err.message || '修改备注失败', true); });
  }

  function resultHtml(item, titleKey) {
    var ok = !!item.ok;
    var title = item[titleKey || 'to_username'] || item.contact || item.resolved_username || '-';
    var err = item.error || '';
    return '<div class="juhe-result ' + (ok ? 'ok' : 'fail') + '">' +
      '<div><b>' + esc(title) + '</b> · ' + esc(ok ? '成功' : '失败') + '</div>' +
      (err ? '<div class="juhe-meta">' + esc(err) + '</div>' : '') +
    '</div>';
  }

  function renderResults(nodeId, items, emptyText) {
    var node = $(nodeId);
    if (!node) return;
    if (!items || !items.length) {
      node.innerHTML = emptyText ? '<div class="juhe-empty">' + esc(emptyText) + '</div>' : '';
      return;
    }
    node.innerHTML = items.map(function(item) { return resultHtml(item); }).join('');
  }

  function appendResults(nodeId, items) {
    var node = $(nodeId);
    if (!node || !items || !items.length) return;
    node.innerHTML = (node.innerHTML || '') + items.map(function(item) { return resultHtml(item); }).join('');
  }

  function formatFileSize(size) {
    var n = Number(size || 0);
    if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
    if (n >= 1024) return Math.ceil(n / 1024) + ' KB';
    return n + ' B';
  }

  function renderMediaPreview() {
    var wrap = $('juheMediaPreview');
    var summary = $('juheMediaSummary');
    if (summary) {
      summary.textContent = state.mediaFiles.length
        ? ('已选择 ' + state.mediaFiles.length + ' 个文件')
        : '未选择文件';
    }
    if (!wrap) return;
    if (!state.mediaFiles.length) {
      wrap.innerHTML = '';
      return;
    }
    wrap.innerHTML = state.mediaFiles.map(function(item, idx) {
      var file = item.file;
      var isImage = String(file.type || '').indexOf('image/') === 0;
      return '<div class="juhe-media-card" data-juhe-media-index="' + idx + '">' +
        '<div class="juhe-media-thumb">' +
          (isImage && item.previewUrl ? '<img src="' + esc(item.previewUrl) + '" alt="">' : esc((file.name || 'FILE').split('.').pop() || 'FILE')) +
        '</div>' +
        '<div class="juhe-media-info">' +
          '<div class="juhe-media-name" title="' + esc(file.name || '') + '">' + esc(file.name || '未命名文件') + '</div>' +
          '<div class="juhe-media-actions"><span class="juhe-meta">' + esc(formatFileSize(file.size)) + '</span><button type="button" class="juhe-link-btn" data-juhe-remove-media="' + idx + '">移除</button></div>' +
        '</div>' +
      '</div>';
    }).join('');
  }

  function addMediaFiles(files) {
    Array.from(files || []).forEach(function(file) {
      var previewUrl = '';
      if (String(file.type || '').indexOf('image/') === 0 && typeof URL !== 'undefined' && URL.createObjectURL) {
        previewUrl = URL.createObjectURL(file);
      }
      state.mediaFiles.push({ file: file, previewUrl: previewUrl });
    });
    renderMediaPreview();
  }

  function removeMediaFile(index) {
    var item = state.mediaFiles[index];
    if (item && item.previewUrl && typeof URL !== 'undefined' && URL.revokeObjectURL) {
      URL.revokeObjectURL(item.previewUrl);
    }
    state.mediaFiles.splice(index, 1);
    renderMediaPreview();
  }

  function clearMediaFiles() {
    state.mediaFiles.forEach(function(item) {
      if (item.previewUrl && typeof URL !== 'undefined' && URL.revokeObjectURL) URL.revokeObjectURL(item.previewUrl);
    });
    state.mediaFiles = [];
    var fileInput = $('juheMediaFile');
    if (fileInput) fileInput.value = '';
    renderMediaPreview();
  }

  function sendTextOnly(id, names, content) {
    return apiJson('/api/juhe-wechat/messages/send', {
      method: 'POST',
      body: { config_id: Number(id), to_usernames: names, message_type: 'text', content: content }
    });
  }

  function sendText() {
    var id = activeConfigId();
    var names = selectedUsernames();
    var content = (($('juheMessageContent') || {}).value || '').trim();
    if (!id) return setMsg('请先选择可用实例', true);
    if (!names.length) return setMsg('请先选择联系人', true);
    if (!content) return setMsg('请输入要发送的文案', true);
    setMsg('正在发送文本...', false);
    sendTextOnly(id, names, content).then(function(data) {
      renderResults('juheSendResults', data.items || [], '没有发送结果');
      setMsg('文本发送完成：成功 ' + (data.success_count || 0) + '，失败 ' + (data.failed_count || 0), data.failed_count > 0);
      loadLogs();
    }).catch(function(err) { setMsg(err.message || '发送失败', true); });
  }

  function uploadToWechatCdn(file, type) {
    var base = apiBase();
    var id = activeConfigId();
    var fileType = type === 'image' ? 2 : 4;
    if (!base) return Promise.reject(new Error('鏈厤缃湇鍔″櫒 API_BASE'));
    var fd = new FormData();
    fd.append('file', file);
    return fetch(base + '/api/juhe-wechat/media/upload-file?config_id=' + encodeURIComponent(id) + '&file_type=' + encodeURIComponent(fileType), {
      method: 'POST',
      headers: authHeaderUpload(),
      body: fd
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok) throw new Error(parseErr(data, '寰俊 CDN 涓婁紶澶辫触'));
        state.lastUpload = { local: data.source || {}, cdn: data.upload || data.upstream || data };
        return state.lastUpload;
      });
    });
  }

  function sendMediaOnly(id, names, file, type) {
    return uploadToWechatCdn(file, type).then(function(uploaded) {
      return apiJson('/api/juhe-wechat/messages/send', {
        method: 'POST',
        body: { config_id: Number(id), to_usernames: names, message_type: type, upload: uploaded.cdn }
      });
    });
  }

  function sendMedia(type) {
    var id = activeConfigId();
    var names = selectedUsernames();
    var fileInput = $('juheMediaFile');
    var file = state.mediaFiles[0] ? state.mediaFiles[0].file : (fileInput && fileInput.files && fileInput.files[0]);
    if (!id) return setMsg('请先选择可用实例', true);
    if (!names.length) return setMsg('请先选择联系人', true);
    if (!file) return setMsg('请先选择要发送的图片或文件', true);
    setMsg('正在上传并发送' + (type === 'image' ? '图片' : '文件') + '...', false);
    sendMediaOnly(id, names, file, type).then(function(data) {
      renderResults('juheSendResults', data.items || [], '没有发送结果');
      setMsg((type === 'image' ? '图片' : '文件') + '发送完成：成功 ' + (data.success_count || 0) + '，失败 ' + (data.failed_count || 0), data.failed_count > 0);
      clearMediaFiles();
      loadLogs();
    }).catch(function(err) {
      setMsg(err.message || '媒体发送失败', true);
    });
  }

  async function sendSelectedContent() {
    var id = activeConfigId();
    var names = selectedUsernames();
    var content = (($('juheMessageContent') || {}).value || '').trim();
    var files = state.mediaFiles.map(function(item) { return item.file; });
    if (!id) return setMsg('请先选择可用实例', true);
    if (!names.length) return setMsg('请先选择联系人', true);
    if (!content && !files.length) return setMsg('请输入文案或选择图片/文件', true);

    var resultNode = $('juheSendResults');
    if (resultNode) resultNode.innerHTML = '';
    var ok = 0;
    var fail = 0;
    try {
      if (content) {
        setMsg('正在发送文案...', false);
        var textResult = await sendTextOnly(id, names, content);
        appendResults('juheSendResults', (textResult.items || []).map(function(x) {
          return Object.assign({}, x, { to_username: '文案 / ' + (x.to_username || '') });
        }));
        ok += Number(textResult.success_count || 0);
        fail += Number(textResult.failed_count || 0);
      }
      for (var i = 0; i < files.length; i++) {
        var file = files[i];
        var isImage = String(file.type || '').indexOf('image/') === 0;
        var type = isImage ? 'image' : 'file';
        setMsg('正在发送 ' + (i + 1) + '/' + files.length + '：' + file.name, false);
        var mediaResult = await sendMediaOnly(id, names, file, type);
        appendResults('juheSendResults', (mediaResult.items || []).map(function(x) {
          return Object.assign({}, x, { to_username: (isImage ? '图片 ' : '文件 ') + file.name + ' / ' + (x.to_username || '') });
        }));
        ok += Number(mediaResult.success_count || 0);
        fail += Number(mediaResult.failed_count || 0);
      }
      setMsg('发送完成：成功 ' + ok + '，失败 ' + fail, fail > 0);
      clearMediaFiles();
      loadLogs();
    } catch (err) {
      setMsg(err.message || '发送选中内容失败', true);
      loadLogs();
    }
  }

  function parseLines(text) {
    return String(text || '')
      .split(/\r?\n|,|，|;|；|\t/g)
      .map(function(x) { return x.trim(); })
      .filter(Boolean)
      .filter(function(x, idx, arr) { return arr.indexOf(x) === idx; });
  }

  function openImportModal() {
    var modal = $('juheImportModal');
    if (modal) modal.classList.add('show');
  }

  function startImportFriends() {
    var id = activeConfigId();
    if (!id) return setMsg('请先选择可用实例', true);
    var contacts = parseLines(($('juheImportTextarea') || {}).value || '').map(function(x) { return { contact: x }; });
    if (!contacts.length) return setMsg('请先粘贴要添加的联系人', true);
    var verify = (($('juheVerifyContentInput') || {}).value || '').trim();
    setMsg('正在发送好友申请...', false);
    apiJson('/api/juhe-wechat/friend-requests', {
      method: 'POST',
      body: { config_id: Number(id), verify_content: verify, contacts: contacts }
    }).then(function(data) {
      renderResults('juheImportResults', data.items || [], '没有导入结果');
      setMsg('好友申请完成：成功 ' + (data.success_count || 0) + '，失败 ' + (data.failed_count || 0), data.failed_count > 0);
      loadLogs();
    }).catch(function(err) { setMsg(err.message || '批量加好友失败', true); });
  }

  function renderRooms() {
    var list = $('juheRoomList');
    var active = $('juheActiveRoomName');
    if (active) active.textContent = state.activeRoomUsername || '未选择群';
    if (!list) return;
    if (!state.groups.length) {
      list.className = 'juhe-empty';
      list.innerHTML = '点击“刷新群”加载群列表，或先刷新通讯录。';
      return;
    }
    list.className = 'juhe-list';
    list.innerHTML = state.groups.map(function(item) {
      var username = itemUsername(item);
      var activeCls = username === state.activeRoomUsername ? ' active' : '';
      return '<div class="juhe-item' + activeCls + '" data-juhe-room="' + esc(username) + '">' +
        '<span class="juhe-chip">群</span>' +
        '<div><div class="juhe-item-title">' + esc(itemTitle(item)) + '</div><div class="juhe-meta">' + esc(itemSub(item)) + '</div></div>' +
        '<button type="button" class="btn btn-ghost btn-sm" data-juhe-room-detail="' + esc(username) + '">详情</button>' +
      '</div>';
    }).join('');
  }

  function refreshRooms() {
    var id = activeConfigId();
    if (!id) return setMsg('请先选择可用实例', true);
    setMsg('正在刷新群列表...', false);
    apiJson('/api/juhe-wechat/rooms/list', {
      method: 'POST',
      body: { config_id: Number(id) }
    }).then(function(data) {
      state.groups = Array.isArray(data.items) ? data.items : [];
      renderRooms();
      setMsg('群列表刷新完成：' + state.groups.length + ' 个群', false);
    }).catch(function(err) { setMsg(err.message || '刷新群失败', true); });
  }

  function roomUsernameRequired() {
    if (!state.activeRoomUsername) {
      setMsg('请先选择一个群', true);
      return '';
    }
    return state.activeRoomUsername;
  }

  function roomAction(path, body, successText) {
    var id = activeConfigId();
    if (!id) return setMsg('请先选择可用实例', true);
    setMsg('正在执行群操作...', false);
    body = Object.assign({ config_id: Number(id) }, body || {});
    apiJson(path, { method: 'POST', body: body }).then(function(data) {
      renderResults('juheRoomResults', [{ ok: data.ok !== false, to_username: successText || '群操作', error: data.ok === false ? parseErr(data.upstream || data) : '' }]);
      setMsg(successText || '群操作已提交', data.ok === false);
      if (path.indexOf('/rooms/create') >= 0 || path.indexOf('/rooms/rename') >= 0) refreshRooms();
      loadLogs();
    }).catch(function(err) { setMsg(err.message || '群操作失败', true); });
  }

  function createRoom() {
    var names = selectedUsernames();
    if (!names.length) return setMsg('请先在通讯录选择要拉群的联系人', true);
    roomAction('/api/juhe-wechat/rooms/create', { username_list: names }, '建群请求已提交');
  }

  function roomDetail() {
    var room = roomUsernameRequired();
    if (!room) return;
    apiJson('/api/juhe-wechat/rooms/detail', {
      method: 'POST',
      body: { config_id: Number(activeConfigId()), room_username: room }
    }).then(function(data) {
      renderResults('juheRoomResults', [{ ok: data.ok !== false, to_username: '群详情', error: data.ok === false ? parseErr(data.upstream || data) : '' }]);
      setMsg('群详情已返回', data.ok === false);
      showDetail('群详情', data.detail || data.upstream || data);
      loadLogs();
    }).catch(function(err) { setMsg(err.message || '获取群详情失败', true); });
  }

  function roomMembers() {
    var room = roomUsernameRequired();
    if (!room) return;
    apiJson('/api/juhe-wechat/rooms/members', {
      method: 'POST',
      body: { config_id: Number(activeConfigId()), room_username: room }
    }).then(function(data) {
      setMsg('群成员已返回：' + ((data.items || []).length || 0) + ' 条', false);
      showDetail('群成员', data.items && data.items.length ? data.items : (data.detail || data.upstream || data));
      loadLogs();
    }).catch(function(err) { setMsg(err.message || '获取群成员失败', true); });
  }

  function addMembers(invite) {
    var room = roomUsernameRequired();
    var names = selectedUsernames();
    if (!room) return;
    if (!names.length) return setMsg('请先在通讯录选择联系人', true);
    roomAction(invite ? '/api/juhe-wechat/rooms/invite-members' : '/api/juhe-wechat/rooms/add-members', {
      room_username: room,
      username_list: names
    }, invite ? '邀请成员请求已提交' : '添加成员请求已提交');
  }

  function renameRoom() {
    var room = roomUsernameRequired();
    var name = (($('juheRoomNameInput') || {}).value || '').trim();
    if (!room) return;
    if (!name) return setMsg('请输入新的群名称', true);
    roomAction('/api/juhe-wechat/rooms/rename', { room_username: room, name: name }, '改群名请求已提交');
  }

  function setAnnouncement() {
    var room = roomUsernameRequired();
    var announcement = (($('juheRoomAnnouncementInput') || {}).value || '').trim();
    if (!room) return;
    if (!announcement) return setMsg('请输入群公告', true);
    roomAction('/api/juhe-wechat/rooms/announcement', { room_username: room, announcement: announcement }, '群公告请求已提交');
  }

  function setDisplayName() {
    var room = roomUsernameRequired();
    var displayName = (($('juheRoomDisplayNameInput') || {}).value || '').trim();
    if (!room) return;
    roomAction('/api/juhe-wechat/rooms/display-name', { room_username: room, display_name: displayName }, '群昵称请求已提交');
  }

  function loadAiConfig() {
    var id = activeConfigId();
    if (!id) return Promise.resolve();
    return apiJson('/api/juhe-wechat/ai-reply/config?config_id=' + encodeURIComponent(id)).then(function(data) {
      var cfg = data.config || {};
      state.aiSelectedMemoryDocIds = new Set(Array.isArray(cfg.auto_reply_memory_doc_ids) ? cfg.auto_reply_memory_doc_ids.map(String) : []);
      var enabled = $('juheAiEnabled');
      var maxContext = $('juheAiMaxContext');
      var knowledge = $('juheAiKnowledge');
      var prompt = $('juheAiPrompt');
      var handoff = $('juheAiHandoffKeywords');
      if (enabled) enabled.value = cfg.auto_reply_enabled ? '1' : '0';
      if (maxContext) maxContext.value = cfg.auto_reply_max_context || 12;
      if (knowledge) knowledge.value = cfg.knowledge || '';
      if (prompt) prompt.value = cfg.auto_reply_prompt || '';
      if (handoff) handoff.value = cfg.auto_reply_handoff_keywords || '';
      renderAiMemoryDocs();
    }).catch(function(err) {
      setMsg(err.message || 'AI客服配置加载失败', true);
    });
  }

  function renderAiMemoryDocs() {
    var list = $('juheAiMemoryDocs');
    if (!list) return;
    if (!state.aiMemoryDocs.length) {
      list.className = 'juhe-empty';
      list.innerHTML = '暂无可选记忆文件，请先在个人记忆上传，或由管理后台下发资料。';
      return;
    }
    list.className = 'juhe-list';
    list.style.maxHeight = '220px';
    list.innerHTML = state.aiMemoryDocs.map(function(doc) {
      var docId = String(doc.doc_id || '');
      var checked = state.aiSelectedMemoryDocIds.has(docId) || !!doc.selected;
      var layer = doc.memory_layer === 'agent' ? '下发资料' : '个人记忆';
      return '<label class="juhe-item" style="grid-template-columns:24px minmax(0,1fr); cursor:pointer;">' +
        '<input type="checkbox" data-juhe-ai-memory-doc="' + esc(docId) + '"' + (checked ? ' checked' : '') + '>' +
        '<div><div class="juhe-item-title">' + esc(doc.title || doc.filename || docId) + '</div>' +
        '<div class="juhe-meta">' + esc(layer) + ' · ' + esc(doc.filename || '') + '</div>' +
        '<div class="juhe-muted">' + esc(doc.notes || doc.content_preview || '') + '</div></div>' +
      '</label>';
    }).join('');
  }

  function loadAiMemoryDocs() {
    var id = activeConfigId();
    if (!id) return Promise.resolve();
    return apiJson('/api/juhe-wechat/ai-reply/memory-docs?config_id=' + encodeURIComponent(id)).then(function(data) {
      state.aiMemoryDocs = Array.isArray(data.items) ? data.items : [];
      var selected = Array.isArray(data.selected_doc_ids) ? data.selected_doc_ids.map(String) : [];
      state.aiSelectedMemoryDocIds = new Set(selected);
      renderAiMemoryDocs();
    }).catch(function(err) {
      state.aiMemoryDocs = [];
      renderAiMemoryDocs();
      setMsg(err.message || '记忆文件加载失败', true);
    });
  }

  function selectedAiMemoryDocIds() {
    var fromDom = [];
    document.querySelectorAll('[data-juhe-ai-memory-doc]').forEach(function(input) {
      if (input.checked) fromDom.push(String(input.getAttribute('data-juhe-ai-memory-doc') || ''));
    });
    if (fromDom.length || $('juheAiMemoryDocs')) {
      state.aiSelectedMemoryDocIds = new Set(fromDom.filter(Boolean));
    }
    return Array.from(state.aiSelectedMemoryDocIds);
  }

  function saveAiConfig() {
    var id = activeConfigId();
    if (!id) return setMsg('请先选择可用实例', true);
    var body = {
      config_id: Number(id),
      enabled: (($('juheAiEnabled') || {}).value || '0') === '1',
      memory_doc_ids: selectedAiMemoryDocIds(),
      knowledge: (($('juheAiKnowledge') || {}).value || '').trim(),
      prompt: (($('juheAiPrompt') || {}).value || '').trim(),
      handoff_keywords: (($('juheAiHandoffKeywords') || {}).value || '').trim(),
      max_context: Math.max(2, Math.min(parseInt((($('juheAiMaxContext') || {}).value || '12'), 10) || 12, 40)),
      cooldown_seconds: 8
    };
    setMsg('正在保存AI客服设置...', false);
    apiJson('/api/juhe-wechat/ai-reply/config', { method: 'POST', body: body }).then(function() {
      setMsg('AI客服设置已保存', false);
      return loadConfigs();
    }).catch(function(err) {
      setMsg(err.message || 'AI客服设置保存失败', true);
    });
  }

  function uploadAiKnowledgeFile(file) {
    var id = activeConfigId();
    if (!id) return setMsg('请先选择可用实例', true);
    if (!file) return;
    var status = $('juheAiKnowledgeUploadStatus');
    var mode = (($('juheAiKnowledgeUploadMode') || {}).value || 'append');
    var base = apiBase();
    if (!base) return setMsg('未配置服务器 API_BASE', true);
    var fd = new FormData();
    fd.append('config_id', String(id));
    fd.append('mode', mode);
    fd.append('file', file);
    if (status) status.textContent = '正在解析：' + (file.name || '资料文件');
    fetch(base + '/api/juhe-wechat/ai-reply/knowledge-upload', {
      method: 'POST',
      headers: authHeaderUpload(),
      body: fd
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok) throw new Error(parseErr(data, '资料上传失败'));
        return data;
      });
    }).then(function(data) {
      var knowledge = $('juheAiKnowledge');
      if (knowledge) knowledge.value = data.knowledge || (data.config && data.config.knowledge) || knowledge.value || '';
      if (status) status.textContent = '已解析 ' + (data.chars || 0) + ' 字：' + (data.filename || file.name || '');
      setMsg('资料文件已写入客服资料', false);
    }).catch(function(err) {
      if (status) status.textContent = err.message || '资料上传失败';
      setMsg(err.message || '资料上传失败', true);
    });
  }

  function renderAiSessions() {
    var list = $('juheAiSessionList');
    if (!list) return;
    if (!state.aiSessions.length) {
      list.className = 'juhe-empty';
      list.innerHTML = '暂无会话';
      return;
    }
    list.className = 'juhe-list';
    list.innerHTML = state.aiSessions.map(function(item) {
      var active = item.contact_key === state.activeAiContact;
      return '<div class="juhe-item' + (active ? ' active' : '') + '" data-juhe-ai-session="' + esc(item.contact_key || '') + '" style="grid-template-columns:minmax(0,1fr) auto;">' +
        '<div><div class="juhe-item-title">' + esc(item.contact_name || item.contact_key || '-') + '</div>' +
        '<div class="juhe-meta">' + esc(item.last_status || '') + ' · ' + esc(item.last_at || '') + '</div>' +
        '<div class="juhe-meta">' + esc(item.last_message || '') + '</div></div>' +
        '<span class="juhe-chip">' + esc(item.message_count || 0) + '</span>' +
      '</div>';
    }).join('');
  }

  function renderAiMessages() {
    var title = $('juheAiActiveTitle');
    var list = $('juheAiMessageList');
    if (title) title.textContent = state.activeAiContact || '未选择会话';
    if (!list) return;
    if (!state.activeAiContact) {
      list.className = 'juhe-empty';
      list.innerHTML = '点击左侧会话查看消息。';
      return;
    }
    if (!state.aiMessages.length) {
      list.className = 'juhe-empty';
      list.innerHTML = '暂无消息';
      return;
    }
    list.className = 'juhe-ai-chat';
    list.innerHTML = state.aiMessages.map(function(msg) {
      var failed = msg.status === 'failed' || msg.status === 'reply_send_failed';
      var cls = 'juhe-ai-bubble ' + (msg.direction === 'out' ? 'out' : 'in') + (failed ? ' failed' : '');
      var retry = failed ? '<button type="button" class="btn btn-ghost btn-sm" data-juhe-ai-retry="' + esc(msg.id) + '">重试</button>' : '';
      return '<div class="' + cls + '">' +
        '<div class="juhe-meta">' + esc(msg.direction === 'out' ? 'AI客服' : '客户') + ' · ' + esc(msg.status || '') + ' · ' + esc(msg.created_at || '') + '</div>' +
        '<div class="juhe-ai-message">' + esc(msg.content || '') + '</div>' +
        (msg.error_message ? '<div class="juhe-meta">' + esc(msg.error_message) + '</div>' : '') +
        (retry ? '<div style="margin-top:6px;">' + retry + '</div>' : '') +
      '</div>';
    }).join('');
  }

  function loadAiSessions() {
    var id = activeConfigId();
    if (!id) return Promise.resolve();
    return apiJson('/api/juhe-wechat/ai-reply/sessions?config_id=' + encodeURIComponent(id) + '&limit=80').then(function(data) {
      state.aiSessions = Array.isArray(data.items) ? data.items : [];
      if (state.activeAiContact && !state.aiSessions.some(function(x) { return x.contact_key === state.activeAiContact; })) {
        state.activeAiContact = '';
        state.aiMessages = [];
      }
      renderAiSessions();
      renderAiMessages();
    }).catch(function(err) {
      setMsg(err.message || 'AI客服会话加载失败', true);
    });
  }

  function loadAiMessages(contactKey) {
    var id = activeConfigId();
    if (!id || !contactKey) return Promise.resolve();
    state.activeAiContact = contactKey;
    renderAiSessions();
    return apiJson('/api/juhe-wechat/ai-reply/messages?config_id=' + encodeURIComponent(id) + '&contact_key=' + encodeURIComponent(contactKey) + '&limit=100').then(function(data) {
      state.aiMessages = Array.isArray(data.items) ? data.items : [];
      renderAiMessages();
    }).catch(function(err) {
      setMsg(err.message || 'AI客服消息加载失败', true);
    });
  }

  function sendAiTestIncoming() {
    var id = activeConfigId();
    var contact = (($('juheAiTestContact') || {}).value || '').trim();
    var name = (($('juheAiTestName') || {}).value || '').trim();
    var content = (($('juheAiTestContent') || {}).value || '').trim();
    if (!id) return setMsg('请先选择可用实例', true);
    if (!contact) return setMsg('请输入客户 username', true);
    if (!content) return setMsg('请输入客户消息', true);
    setMsg('正在生成AI客服回复...', false);
    apiJson('/api/juhe-wechat/ai-reply/incoming', {
      method: 'POST',
      body: {
        config_id: Number(id),
        contact_key: contact,
        contact_name: name,
        content: content,
        msg_type: 'text'
      }
    }).then(function(data) {
      setMsg(data.ok ? 'AI客服处理完成' : ('AI客服处理失败：' + (data.error || '')), !data.ok);
      state.activeAiContact = contact;
      return loadAiSessions().then(function() { return loadAiMessages(contact); });
    }).catch(function(err) {
      setMsg(err.message || 'AI客服测试失败', true);
    });
  }

  function retryAiMessage(messageId) {
    if (!messageId) return;
    setMsg('正在重试AI客服消息...', false);
    apiJson('/api/juhe-wechat/ai-reply/messages/' + encodeURIComponent(messageId) + '/retry', { method: 'POST', body: {} }).then(function(data) {
      setMsg(data.ok ? '重试完成' : ('重试失败：' + (data.error || '')), !data.ok);
      return loadAiSessions().then(function() {
        if (state.activeAiContact) return loadAiMessages(state.activeAiContact);
      });
    }).catch(function(err) {
      setMsg(err.message || '重试失败', true);
    });
  }

  function loadLogs() {
    var id = activeConfigId();
    var path = '/api/juhe-wechat/call-logs?limit=80' + (id ? '&config_id=' + encodeURIComponent(id) : '');
    return apiJson(path).then(function(data) {
      var list = $('juheLogList');
      if (!list) return;
      var items = Array.isArray(data.items) ? data.items : [];
      if (!items.length) {
        list.className = 'juhe-empty';
        list.innerHTML = '暂无记录';
        return;
      }
      list.className = 'juhe-list';
      list.innerHTML = items.map(function(item) {
        return '<div class="juhe-result ' + (item.success ? 'ok' : 'fail') + '">' +
          '<div><b>' + esc(item.action || '-') + '</b> · ' + esc(item.success ? '成功' : '失败') + '</div>' +
          '<div class="juhe-meta">' + esc(item.created_at || '') + ' · ' + esc(item.upstream_path || '') + ' · ' + esc(item.latency_ms || '-') + 'ms</div>' +
          (item.error_message ? '<div class="juhe-meta">' + esc(item.error_message) + '</div>' : '') +
          '<div style="margin-top:6px;"><button type="button" class="btn btn-ghost btn-sm" data-juhe-log-detail="' + esc(item.id) + '">详情</button></div>' +
        '</div>';
      }).join('');
      list.querySelectorAll('[data-juhe-log-detail]').forEach(function(btn) {
        btn.addEventListener('click', function() {
          var id = Number(btn.getAttribute('data-juhe-log-detail'));
          var item = items.find(function(x) { return Number(x.id) === id; });
          showDetail('调用记录', item || {});
        });
      });
    }).catch(function(err) { setMsg(err.message || '加载记录失败', true); });
  }

  function bindEvents(root) {
    var back = $('juheBackBtn');
    if (back) back.addEventListener('click', function() {
      var nav = document.querySelector('.nav-left-item[data-view="skill-store"]');
      if (nav) nav.click();
    });
    var refresh = $('juheRefreshBtn');
    if (refresh) refresh.addEventListener('click', function() {
      loadConfigs().then(loadContactCache);
    });
    var sel = $('juheConfigSelect');
    if (sel) sel.addEventListener('change', function() {
      state.activeConfigId = sel.value;
      state.contacts = [];
      state.groups = [];
      state.selectedContacts.clear();
      state.activeRoomUsername = '';
      renderContacts();
      renderRooms();
      loadContactCache();
      if (state.tab === 'ai') {
        loadAiConfig();
        loadAiMemoryDocs();
        loadAiSessions();
      }
      loadLogs();
    });
    var status = $('juheCheckStatusBtn');
    if (status) status.addEventListener('click', checkStatus);
    var refreshContactsBtn = $('juheRefreshContactsBtn');
    if (refreshContactsBtn) refreshContactsBtn.addEventListener('click', refreshContacts);
    var search = $('juheContactSearch');
    if (search) search.addEventListener('input', renderContacts);
    var selectAll = $('juheSelectAllContactsBtn');
    if (selectAll) selectAll.addEventListener('click', selectAllContacts);
    var detail = $('juheContactDetailBtn');
    if (detail) detail.addEventListener('click', function() { contactDetail(''); });
    var remark = $('juheModifyRemarkBtn');
    if (remark) remark.addEventListener('click', modifyRemark);
    var sendSelectedBtn = $('juheSendSelectedBtn');
    if (sendSelectedBtn) sendSelectedBtn.addEventListener('click', sendSelectedContent);
    var mediaInput = $('juheMediaFile');
    if (mediaInput) mediaInput.addEventListener('change', function() {
      addMediaFiles(mediaInput.files || []);
      mediaInput.value = '';
    });
    var importBtn = $('juheImportFriendBtn');
    if (importBtn) importBtn.addEventListener('click', openImportModal);
    var startImport = $('juheStartImportBtn');
    if (startImport) startImport.addEventListener('click', startImportFriends);
    var refreshRoomsBtn = $('juheRefreshRoomsBtn');
    if (refreshRoomsBtn) refreshRoomsBtn.addEventListener('click', refreshRooms);
    var createRoomBtn = $('juheCreateRoomBtn');
    if (createRoomBtn) createRoomBtn.addEventListener('click', createRoom);
    var roomDetailBtn = $('juheRoomDetailBtn');
    if (roomDetailBtn) roomDetailBtn.addEventListener('click', roomDetail);
    var roomMembersBtn = $('juheRoomMembersBtn');
    if (roomMembersBtn) roomMembersBtn.addEventListener('click', roomMembers);
    var addMembersBtn = $('juheRoomAddMembersBtn');
    if (addMembersBtn) addMembersBtn.addEventListener('click', function() { addMembers(false); });
    var inviteMembersBtn = $('juheRoomInviteMembersBtn');
    if (inviteMembersBtn) inviteMembersBtn.addEventListener('click', function() { addMembers(true); });
    var roomRenameBtn = $('juheRoomRenameBtn');
    if (roomRenameBtn) roomRenameBtn.addEventListener('click', renameRoom);
    var roomAnnouncementBtn = $('juheRoomAnnouncementBtn');
    if (roomAnnouncementBtn) roomAnnouncementBtn.addEventListener('click', setAnnouncement);
    var roomDisplayNameBtn = $('juheRoomDisplayNameBtn');
    if (roomDisplayNameBtn) roomDisplayNameBtn.addEventListener('click', setDisplayName);
    var refreshLogs = $('juheRefreshLogsBtn');
    if (refreshLogs) refreshLogs.addEventListener('click', loadLogs);
    var aiSave = $('juheAiSaveBtn');
    if (aiSave) aiSave.addEventListener('click', saveAiConfig);
    var aiRefresh = $('juheAiRefreshBtn');
    if (aiRefresh) aiRefresh.addEventListener('click', function() {
      loadAiConfig();
      loadAiMemoryDocs();
      loadAiSessions();
    });
    var aiSendTest = $('juheAiSendTestBtn');
    if (aiSendTest) aiSendTest.addEventListener('click', sendAiTestIncoming);
    root.querySelectorAll('[data-juhe-tab]').forEach(function(btn) {
      btn.addEventListener('click', function() { switchTab(btn.getAttribute('data-juhe-tab')); });
    });
    root.querySelectorAll('[data-juhe-close-modal]').forEach(function(btn) {
      btn.addEventListener('click', closeModals);
    });
    root.addEventListener('click', function(evt) {
      var contactDetailBtn = evt.target.closest('[data-juhe-contact-detail]');
      var removeMediaBtn = evt.target.closest('[data-juhe-remove-media]');
      var aiSession = evt.target.closest('[data-juhe-ai-session]');
      var aiRetry = evt.target.closest('[data-juhe-ai-retry]');
      if (aiRetry) {
        retryAiMessage(aiRetry.getAttribute('data-juhe-ai-retry'));
        return;
      }
      if (aiSession) {
        loadAiMessages(aiSession.getAttribute('data-juhe-ai-session') || '');
        return;
      }
      if (removeMediaBtn) {
        removeMediaFile(Number(removeMediaBtn.getAttribute('data-juhe-remove-media')));
        return;
      }
      if (contactDetailBtn) {
        contactDetail(contactDetailBtn.getAttribute('data-juhe-contact-detail'));
        return;
      }
      var contactNode = evt.target.closest('[data-juhe-contact]');
      if (contactNode && !evt.target.closest('[data-juhe-contact-detail]')) {
        var username = contactNode.getAttribute('data-juhe-contact');
        var checkbox = contactNode.querySelector('.juhe-contact-check');
        var checked = evt.target.classList.contains('juhe-contact-check') ? evt.target.checked : !(checkbox && checkbox.checked);
        toggleContact(username, checked);
        return;
      }
      var roomDetailBtn2 = evt.target.closest('[data-juhe-room-detail]');
      if (roomDetailBtn2) {
        state.activeRoomUsername = roomDetailBtn2.getAttribute('data-juhe-room-detail') || '';
        renderRooms();
        roomDetail();
        return;
      }
      var roomNode = evt.target.closest('[data-juhe-room]');
      if (roomNode) {
        state.activeRoomUsername = roomNode.getAttribute('data-juhe-room') || '';
        renderRooms();
      }
    });
  }

  window.initJuheWechatView = function() {
    var root = $('content-juhe-wechat');
    if (!root || root.dataset.inited === '1') return;
    root.dataset.inited = '1';
    bindEvents(root);
    renderContacts();
    renderRooms();
    loadConfigs().then(function() {
      return loadContactCache();
    }).then(function() {
      return loadLogs();
    });
  };
})();
