(function() {
  'use strict';

  var ACCOUNT_ID = 'desktop-whatsapp-default';
  var state = {
    friendRecordTimer: null,
    memoryDocs: [],
    tab: 'overview', busy: false, status: {}, config: {}, lastRun: {},
    sessions: [], groups: [], contacts: [], records: [], activeSession: null, messages: [],
    friendQueue: {}, friendSummary: {}, friendRecords: [], autoReply: {}, diagnostics: {},
    pages: {
      sessions: { page: 1, limit: 30, total: 0, keyword: '', chatType: '' },
      groups: { page: 1, limit: 50, total: 0, keyword: '', chatType: 'group' },
      contacts: { page: 1, limit: 50, total: 0, keyword: '' },
      records: { page: 1, limit: 50, total: 0, keyword: '' },
      friendRecords: { page: 1, limit: 50, total: 0, keyword: '', status: '' }
    }
  };

  function $(id) { return document.getElementById(id); }
  function text(value) { return String(value == null ? '' : value); }
  function esc(value) { return text(value).replace(/[&<>"']/g, function(ch) { return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[ch]; }); }
  function debounce(fn, wait) { var timer; return function() { var args = arguments; clearTimeout(timer); timer = setTimeout(function() { fn.apply(null, args); }, wait || 280); }; }
  function localBase() {
    var value = '';
    try { value = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) || ''; } catch (e) {}
    value = value || window.__LOCAL_API_BASE || '';
    return text(value || window.location.origin).replace(/\/$/, '');
  }
  function request(path, options) {
    options = options || {};
    var headers = {};
    try { headers = typeof authHeaders === 'function' ? Object.assign({}, authHeaders() || {}) : {}; } catch (e) {}
    if (options.json !== undefined) headers['Content-Type'] = 'application/json';
    return fetch(localBase() + path, { method: options.method || 'GET', headers: headers, body: options.json === undefined ? undefined : JSON.stringify(options.json), credentials: 'same-origin', cache: 'no-store' })
      .then(function(response) { return response.json().catch(function() { return {}; }).then(function(data) { if (!response.ok) throw new Error(data.detail || data.message || ('请求失败（' + response.status + '）')); return data; }); });
  }
  function toastMessage(message) { if (typeof toast === 'function') toast(message); }
  function showError(message, ok) { var box = $('personalWhatsappError'); if (!box) return; box.textContent = message || ''; box.style.display = message ? 'block' : 'none'; box.classList.toggle('err', !!message && !ok); box.dataset.pwaMessageKind = message ? (ok ? 'progress' : 'error') : ''; }
  function showNotice(message) { var box = $('personalWhatsappError'); showError(message, true); if (box && message) box.dataset.pwaMessageKind = 'success'; }
  function clearProgress() { var box = $('personalWhatsappError'); if (box && box.dataset.pwaMessageKind === 'progress') showError(''); }
  function setBusy(value, label) {
    state.busy = !!value;
    var ids = ['personalWhatsappRefreshBtn','personalWhatsappSyncAllBtn','personalWhatsappSyncSessionsBtn','personalWhatsappOpenSessionBtn','personalWhatsappSendBtn','personalWhatsappAddContactBtn','personalWhatsappAddFriendBtn','personalWhatsappSaveBtn','personalWhatsappRunBtn','personalWhatsappRefreshRecordsBtn','personalWhatsappAddFriendSubmitBtn','personalWhatsappContactSubmitBtn','personalWhatsappFriendQueueStartBtn','personalWhatsappFriendQueueStopBtn','personalWhatsappFriendQueueSettingsBtn','personalWhatsappFriendSettingsSaveBtn','personalWhatsappTakeoverSettingsBtn','personalWhatsappFriendImportBtn','personalWhatsappLoopStartBtn','personalWhatsappDiagnosticsBtn'];
    ids.forEach(function(id) { var node = $(id); if (node) node.disabled = state.busy || ((id === 'personalWhatsappOpenSessionBtn' || id === 'personalWhatsappSendBtn') && !state.activeSession); });
    var stop = $('personalWhatsappStopBtn'); if (stop) stop.disabled = false;
    if (value && label) showError(label, true);
  }
  function runBusy(label, promiseFactory) {
    if (state.busy) return Promise.resolve();
    showError(''); setBusy(true, label);
    return Promise.resolve().then(promiseFactory).then(function(result) { clearProgress(); return result; }).catch(function(error) { showError(error.message || '操作失败'); throw error; }).finally(function() { setBusy(false); });
  }
  function numberField(id, fallback, min, max) { var value = Number($(id) && $(id).value); if (!isFinite(value)) value = fallback; return Math.max(min, Math.min(max, Math.round(value))); }
  function query(meta, extra) { var params = new URLSearchParams(); params.set('limit', meta.limit); params.set('offset', Math.max(0, (meta.page - 1) * meta.limit)); if (meta.keyword) params.set('keyword', meta.keyword); Object.keys(extra || {}).forEach(function(key) { if (extra[key]) params.set(key, extra[key]); }); return params.toString(); }
  function pageCount(meta) { return Math.max(1, Math.ceil(Number(meta.total || 0) / Math.max(1, meta.limit))); }
  function renderPagination(id, key) {
    var host = $(id), meta = state.pages[key]; if (!host || !meta) return;
    var pages = pageCount(meta), page = Math.min(meta.page, pages);
    host.innerHTML = '<span>共 ' + meta.total + ' 条 · 第 ' + page + '/' + pages + ' 页</span><div class="pwa-actions"><button class="btn btn-ghost btn-sm" data-pwa-page="' + key + '" data-pwa-dir="-1"' + (page <= 1 ? ' disabled' : '') + '>上一页</button><button class="btn btn-ghost btn-sm" data-pwa-page="' + key + '" data-pwa-dir="1"' + (page >= pages ? ' disabled' : '') + '>下一页</button></div>';
  }

  function statusCard(label, value, tone) { return '<div class="pwa-stat"><span>' + esc(label) + '</span><strong style="color:' + (tone === 'good' ? '#087443' : tone === 'bad' ? '#b42318' : '#3f5a4e') + '">' + esc(value) + '</strong></div>'; }
  function renderStatus() {
    var s = state.status || {};
    var host = $('personalWhatsappStatus'); if (!host) return;
    host.innerHTML = statusCard('桌面客户端', s.desktop_found ? '已检测' : '未检测', s.desktop_found ? 'good' : 'bad') + statusCard('登录状态', s.logged_in ? '已登录' : '未登录', s.logged_in ? 'good' : 'bad') + statusCard('UIA 接管', s.ok ? '正常' : '不可用', s.ok ? 'good' : 'bad') + statusCard('未读会话', Number(s.unread_count || 0), s.unread_count ? 'good' : '') + statusCard('当前动作', s.running ? (s.active_action || '执行中') : '空闲', s.running ? '' : 'good');
    renderRuntimeNotice(s);
  }

  // 只有真的缺依赖 / 桌面端异常时，状态里才多出这一块（正常时整块隐藏，不再堆一堆没人看的诊断）
  function renderRuntimeNotice(s) {
    var wrap = $('personalWhatsappDepsWarn');
    if (!wrap) return;
    var deps = s.dependencies || {};
    var missing = Object.keys(deps).filter(function (key) { return !deps[key]; });
    var problems = [];
    if (missing.length) problems.push('缺少依赖：' + missing.join('、') + '（点「修复运行依赖」）');
    if (!s.ok && s.reason) problems.push(String(s.reason));
    if (s.repair_done) problems.push('上次自动修复：' + String(s.repair_done));
    if (!problems.length) { wrap.style.display = 'none'; return; }
    wrap.style.display = 'block';
    var text = $('personalWhatsappDepsWarnText');
    if (text) text.textContent = problems.join('；');
    var detail = $('personalWhatsappDiagDetail');
    if (detail) detail.innerHTML = runtimeDetailHtml(s);
  }

  function runtimeDetailHtml(s) {
    var deps = s.dependencies || {};
    var sources = s.dependency_sources || {};
    var caps = s.capabilities || {};
    var processes = Array.isArray(s.processes) ? s.processes : [];
    var v = state.version || {};
    return '版本：' + esc(v.static_version || '-') + '-' + esc(v.static_build == null ? '-' : v.static_build)
      + ' / 客户端：' + esc(v.client_version || '-') + '-' + esc(v.client_build == null ? '-' : v.client_build)
      + '<br>依赖：' + esc(Object.keys(deps).map(function (key) { return key + (deps[key] ? ':\u2713' + (sources[key] === 'vendor' ? '(内置副本)' : '') : ':\u2717'); }).join('\u3000'))
      + '<br>能力：进程扫描 ' + esc(caps.process_scan || '-') + ' / 剪贴板 ' + esc(caps.clipboard || '-') + ' / UIA ' + esc(caps.uia || '-')
      + '<br>原因：' + esc(s.reason || '（无）')
      + '<br>WhatsApp 进程：' + (processes.length ? esc(processes.map(function (p) { return (p.name || '?') + '(' + p.pid + ')'; }).join('、')) : '（没检测到进程）');
  }

  function loadMemoryDocs() {
    return request('/api/openclaw/memory/list').then(function(data) {
      state.memoryDocs = Array.isArray(data.documents) ? data.documents : [];
      var select = $('personalWhatsappMemoryDoc');
      if (!select) return state.memoryDocs;
      var options = ['<option value="">不指定，使用系统优先记忆</option>'];
      state.memoryDocs.forEach(function(doc) {
        var id = String(doc.id || doc.doc_id || '');
        if (!id) return;
        options.push('<option value="' + esc(id) + '">' + esc(doc.title || doc.filename || id) + '</option>');
      });
      select.innerHTML = options.join('');
      var current = (state.config && Array.isArray(state.config.memory_doc_ids)) ? state.config.memory_doc_ids : [];
      if (current.length) select.value = String(current[0]);
      var inviteSelect = $('personalWhatsappGroupInviteMemoryDoc');
      if (inviteSelect) {
        var inviteOptions = ['<option value="">不指定拉群规则文件</option>'];
        state.memoryDocs.forEach(function(doc) {
          var id = String(doc.id || doc.doc_id || '');
          if (!id) return;
          inviteOptions.push('<option value="' + esc(id) + '">' + esc(doc.title || doc.filename || id) + '</option>');
        });
        inviteSelect.innerHTML = inviteOptions.join('');
        if (state.config && state.config.group_invite_memory_doc_id) {
          inviteSelect.value = String(state.config.group_invite_memory_doc_id);
        }
      }
      return state.memoryDocs;
    }).catch(function() { return []; });
  }
  function fillConfig() {
    var c = state.config || {};
    if ($('personalWhatsappAccountId')) $('personalWhatsappAccountId').value = ACCOUNT_ID;
    if ($('personalWhatsappInterval')) $('personalWhatsappInterval').value = Math.max(1, Math.min(300, Number(c.interval_seconds || 15)));
    if ($('personalWhatsappTakeoverMinutes')) $('personalWhatsappTakeoverMinutes').value = Math.max(1, Math.min(1440, Number(c.takeover_session_minutes || 30)));
    if ($('personalWhatsappMaxUnread')) $('personalWhatsappMaxUnread').value = Math.max(1, Math.min(100, Number(c.max_unread_per_round || 50)));
    if ($('personalWhatsappInstruction')) $('personalWhatsappInstruction').value = text(c.reply_instruction || '');
    var memorySelect = $('personalWhatsappMemoryDoc');
    if (memorySelect) {
      var selectedDocs = Array.isArray(c.memory_doc_ids) ? c.memory_doc_ids : [];
      memorySelect.value = selectedDocs.length ? String(selectedDocs[0]) : '';
    }
    if ($('personalWhatsappGroupInviteEnabled')) $('personalWhatsappGroupInviteEnabled').checked = !!c.group_invite_enabled;
    if ($('personalWhatsappGroupInviteMemoryDoc')) $('personalWhatsappGroupInviteMemoryDoc').value = text(c.group_invite_memory_doc_id || '');
    if ($('personalWhatsappGroupInviteKeywords')) $('personalWhatsappGroupInviteKeywords').value = text(c.group_invite_keywords || '');
    if ($('personalWhatsappGroupInviteContacts')) $('personalWhatsappGroupInviteContacts').value = (Array.isArray(c.group_invite_contacts) ? c.group_invite_contacts : []).join('\n');
    if ($('personalWhatsappGroupInviteGroupName')) $('personalWhatsappGroupInviteGroupName').value = text(c.group_invite_group_name || '');
    if ($('personalWhatsappGroupInviteWelcome')) $('personalWhatsappGroupInviteWelcome').value = text(c.group_invite_welcome_message || '');
  }
  function configFromFields() { return { group_invite_enabled:(function(){ var node=$('personalWhatsappGroupInviteEnabled'); return !!(node && node.checked); })(), group_invite_memory_doc_id:(function(){ var node=$('personalWhatsappGroupInviteMemoryDoc'); return node?text(node.value).trim():''; })(), group_invite_keywords:(function(){ var node=$('personalWhatsappGroupInviteKeywords'); return node?text(node.value).trim().slice(0,500):''; })(), group_invite_contacts:splitTargetLines($('personalWhatsappGroupInviteContacts') && $('personalWhatsappGroupInviteContacts').value), group_invite_group_name:(function(){ var node=$('personalWhatsappGroupInviteGroupName'); return node?text(node.value).trim().slice(0,60):''; })(), group_invite_welcome_message:(function(){ var node=$('personalWhatsappGroupInviteWelcome'); return node?text(node.value).trim().slice(0,1000):''; })(), memory_doc_ids:(function(){ var node=$('personalWhatsappMemoryDoc'); var value=node?text(node.value).trim():''; return value?[value]:[]; })(), interval_seconds:numberField('personalWhatsappInterval',15,1,300), takeover_session_minutes:numberField('personalWhatsappTakeoverMinutes',30,1,1440), max_unread_per_round:numberField('personalWhatsappMaxUnread',50,1,100), reply_instruction:text($('personalWhatsappInstruction') && $('personalWhatsappInstruction').value).trim().slice(0,4000) }; }
  function reasonLabel(value) { return ({duplicate_in_round:'本轮重复',group_chat:'群聊',no_replyable_text:'无可回复文字',last_message_not_inbound:'最后消息不是对方发送'}[value] || value || '跳过'); }
  function renderLastRun() {
    var run = state.lastRun || {}, summary = $('personalWhatsappLastRun'), host = $('personalWhatsappItems'); if (!summary || !host) return;
    if (!run.started_at && !run.finished_at) { summary.textContent = '暂无执行记录'; host.innerHTML = ''; return; }
    summary.textContent = (run.summary_text || '已完成一轮') + (run.finished_at ? ' · ' + run.finished_at : '') + (run.stop_reason ? ' · 已停止' : '');
    var items = Array.isArray(run.items) ? run.items : [];
    host.innerHTML = items.length ? items.map(function(item) { var good = item.status === 'replied'; return '<div class="pwa-record ' + (item.status === 'failed' ? 'failed' : '') + '"><div class="pwa-item-title">' + esc(item.peer_name || '未命名会话') + ' <span class="pwa-badge">' + esc(good ? '已回复' : item.status === 'failed' ? '失败' : '跳过') + '</span></div><div class="pwa-meta">' + esc(item.reply || item.error || reasonLabel(item.reason)) + '</div></div>'; }).join('') : '<div class="pwa-empty">本轮没有会话明细</div>';
  }
  function loadBase() {
    return Promise.all([
      request('/api/native-whatsapp/status'),
      request('/api/native-whatsapp/config'),
      request('/api/version').catch(function() { return {}; })
    ]).then(function(values) { state.status = values[0] || {}; state.config = values[1] && values[1].config || state.status.config || {}; state.version = values[2] || {}; state.lastRun = state.config.last_run || {}; renderStatus(); fillConfig(); renderLastRun(); if (!state.status.ok && state.status.reason) showError(state.status.reason); });
  }

  function loadSessions() {
    var meta = state.pages.sessions;
    return request('/api/native-whatsapp/sessions?' + query(meta, {chat_type:meta.chatType})).then(function(data) { state.sessions = Array.isArray(data.items) ? data.items : []; meta.total = Number(data.total || 0); renderSessions(); });
  }
  function renderSessions() {
    var host = $('personalWhatsappSessionList'); if (!host) return;
    host.innerHTML = state.sessions.length ? state.sessions.map(function(row) { var active = state.activeSession && state.activeSession.peer_key === row.peer_key; return '<div class="pwa-item' + (active ? ' active' : '') + '" data-pwa-peer="' + esc(row.peer_key) + '"><div class="pwa-item-title">' + esc(row.display_name || '未命名') + ' ' + (row.chat_type === 'group' ? '<span class="pwa-badge">群聊</span>' : '') + '</div><div class="pwa-meta">' + esc(row.last_message || '尚未同步消息') + '</div><div class="pwa-meta">' + esc(row.updated_at || '') + '</div></div>'; }).join('') : '<div class="pwa-empty">暂无会话，请先同步</div>';
    renderPagination('personalWhatsappSessionPagination','sessions');
  }
  function loadGroups() { var meta=state.pages.groups;return request('/api/native-whatsapp/sessions?'+query(meta,{chat_type:'group'})).then(function(data){state.groups=Array.isArray(data.items)?data.items:[];meta.total=Number(data.total||0);var host=$('personalWhatsappGroupList');if(host)host.innerHTML=state.groups.length?state.groups.map(function(row){return '<div class="pwa-item" data-pwa-group-peer="'+esc(row.peer_key)+'"><div class="pwa-item-title">'+esc(row.display_name||'未命名群聊')+' <span class="pwa-badge">群聊</span></div><div class="pwa-meta">'+esc(row.last_message||'尚未同步消息')+'</div></div>';}).join(''):'<div class=\"pwa-empty\">暂无已识别群聊；先同步会话，群聊会自动归入这里</div>';renderPagination('personalWhatsappGroupPagination','groups');}); }
  function selectSession(key) {
    state.activeSession = state.sessions.filter(function(row) { return row.peer_key === key; })[0] || null;
    state.messages = []; renderSessions(); renderMessages();
    if ($('personalWhatsappOpenSessionBtn')) $('personalWhatsappOpenSessionBtn').disabled = state.busy || !state.activeSession;
    if ($('personalWhatsappSendBtn')) $('personalWhatsappSendBtn').disabled = state.busy || !state.activeSession;
    if (!state.activeSession) return;
    $('personalWhatsappActiveSession').textContent = state.activeSession.display_name || '未命名会话';
    $('personalWhatsappActiveSessionSub').textContent = state.activeSession.chat_type === 'group' ? '群聊只可查看，不参与自动回复。' : '可打开桌面会话、同步消息并手动回复。';
    request('/api/native-whatsapp/sessions/' + encodeURIComponent(key) + '/messages?limit=200&offset=0').then(function(data) { state.messages = Array.isArray(data.items) ? data.items : []; renderMessages(); }).catch(function(error) { showError(error.message); });
  }
  function selectContactTarget(target, chatType) {
    var value = text(target).trim();
    if (!value) return;
    state.activeSession = { peer_key: '', display_name: value, chat_type: chatType || 'direct', contact_target: true };
    state.messages = [];
    $('personalWhatsappActiveSession').textContent = value;
    $('personalWhatsappActiveSessionSub').textContent = (chatType === 'group' ? '群聊' : '联系人') + '尚未同步消息，点击“打开并同步”从桌面 WhatsApp 查找。';
    renderMessages();
    if ($('personalWhatsappOpenSessionBtn')) $('personalWhatsappOpenSessionBtn').disabled = false;
    if ($('personalWhatsappSendBtn')) $('personalWhatsappSendBtn').disabled = false;
  }
  function renderMessages() { var host = $('personalWhatsappMessageList'); if (!host) return; host.innerHTML = state.messages.length ? state.messages.map(function(row) { var direction = ['inbound','outbound'].indexOf(row.direction) >= 0 ? row.direction : 'unknown'; return '<div class="pwa-bubble ' + direction + '">' + esc(row.content || '') + '</div>'; }).join('') : '<div class="pwa-empty">暂无消息，点击“打开并同步”读取桌面气泡</div>'; host.scrollTop = host.scrollHeight; }

  function loadContacts() { var meta=state.pages.contacts; return request('/api/native-whatsapp/contacts?' + query(meta)).then(function(data){state.contacts=Array.isArray(data.items)?data.items:[];meta.total=Number(data.total||0);renderContacts();}); }
  var whatsappWebPoll = null;
  function setWebState(text, isErr) { var n = $('personalWhatsappWebState'); if (!n) return; n.textContent = text; n.style.color = isErr ? '#c0392b' : ''; }
  function renderWebQr(qr) {
    var wrap = $('personalWhatsappWebQrWrap'); var img = $('personalWhatsappWebQrImg');
    if (!wrap || !img) return;
    if (!qr) { wrap.style.display = 'none'; img.removeAttribute('src'); return; }
    img.src = 'https://api.qrserver.com/v1/create-qr-code/?size=220x220&margin=2&data=' + encodeURIComponent(qr);
    wrap.style.display = 'block';
  }
  function loadWhatsappContactStats() {
    if (!$('personalWhatsappContactStats')) return Promise.resolve();
    return request('/api/native-whatsapp/contacts/stats').then(function (data) {
      var node = $('personalWhatsappContactStats');
      if (node) node.textContent = '已取号 ' + Number(data.with_phone || 0) + ' / ' + Number(data.total || 0);
    }).catch(function () {});
  }
  function loadWhatsappWebState() {
    if (!$('personalWhatsappWebState')) return Promise.resolve();
    return request('/api/native-whatsapp/web/login').then(function (data) {
      if (data.linked) {
        var me = data.me || {};
        setWebState('已绑定：' + (me.phone ? ('+' + me.phone) : (me.name || '设备已连接')), false);
        renderWebQr('');
        if (whatsappWebPoll) { clearInterval(whatsappWebPoll); whatsappWebPoll = null; }
      } else if (data.qr) { setWebState('等待扫码…', false); renderWebQr(data.qr); }
      else { setWebState('未绑定（点「扫码绑定」）', false); renderWebQr(''); }
    }).catch(function (err) { setWebState(err.message || '状态读取失败', true); });
  }
  function startWhatsappWebLogin() {
    setWebState('正在生成二维码…', false);
    return request('/api/native-whatsapp/web/login', { method: 'POST' }).then(function (data) {
      if (data.linked) return loadWhatsappWebState();
      if (data.qr) { renderWebQr(data.qr); setWebState('等待扫码…', false); }
      else { setWebState('正在启动绑定程序，稍等…', false); }
      if (whatsappWebPoll) clearInterval(whatsappWebPoll);
      whatsappWebPoll = setInterval(loadWhatsappWebState, 2500);
      setTimeout(function () { if (whatsappWebPoll) { clearInterval(whatsappWebPoll); whatsappWebPoll = null; } }, 180000);
    }).catch(function (err) { setWebState(err.message || '生成二维码失败', true); });
  }
  function syncWhatsappContactsViaProtocol() {
    setWebState('正在走协议同步通讯录…', false);
    return request('/api/native-whatsapp/web/contacts/sync', { method: 'POST' }).then(function (data) {
      setWebState(data.message || ('同步完成：' + data.count + ' 个'), false);
      return Promise.all([loadContacts(), loadWhatsappContactStats()]);
    }).catch(function (err) { setWebState(err.message || '协议同步失败', true); });
  }
  function exportWhatsappContactsCsv() {
    var rows = []; var offset = 0;
    function next() {
      return request('/api/native-whatsapp/contacts?limit=200&offset=' + offset).then(function (data) {
        var items = data.items || [];
        items.forEach(function (item) { rows.push([String(item.phone || ''), String(item.display_name || ''), String(item.username || ''), String(item.source || '')]); });
        offset += items.length;
        if (items.length && offset < Number(data.total || 0)) return next();
        var csv = '﻿手机号,名字,@用户名,来源\n' + rows.map(function (r) { return r.map(function (c) { return '"' + String(c).replace(/"/g, '""') + '"'; }).join(','); }).join('\n');
        var blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a'); a.href = url; a.download = 'whatsapp-contacts.csv'; document.body.appendChild(a); a.click();
        setTimeout(function () { URL.revokeObjectURL(url); a.remove(); }, 800);
        setWebState('已导出 ' + rows.length + ' 条到 CSV', false);
      });
    }
    return next().catch(function (err) { setWebState(err.message || '导出失败', true); });
  }
  function logoutWhatsappWeb() {
    if (!window.confirm('确定解除当前机器上的 WhatsApp Web 绑定？')) return Promise.resolve();
    return request('/api/native-whatsapp/web/logout', { method: 'POST' }).then(function () { return loadWhatsappWebState(); });
  }

  function ensureWhatsappWebBlock() {
    if (document.getElementById('personalWhatsappWebBlock')) return;
    var host = document.getElementById('personalWhatsappContactList') || document.querySelector('.pwa-shell') || document.body;
    var wrap = document.createElement('div');
    var BlockMarkup = '<div class="pwa-weblock" id="personalWhatsappWebBlock" style="border:1px solid rgba(37,211,102,.35);border-radius:12px;padding:12px;margin-bottom:12px;background:rgba(255,255,255,.72);"><div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;"><strong style="font-size:14px;">通讯录（WhatsApp 协议直连）</strong><span class="pwa-meta" id="personalWhatsappWebState">检查中…</span></div><div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:10px 0;"><button class="btn btn-primary btn-sm" type="button" id="personalWhatsappWebLoginBtn">扫码绑定</button><button class="btn btn-ghost btn-sm" type="button" id="personalWhatsappWebSyncBtn">同步通讯录</button><button class="btn btn-ghost btn-sm" type="button" id="personalWhatsappWebExportBtn">导出 CSV</button><button class="btn btn-ghost btn-sm" type="button" id="personalWhatsappWebLogoutBtn">解绑</button><span class="pwa-meta" id="personalWhatsappContactStats">已取号 —</span></div><div id="personalWhatsappWebQrWrap" style="display:none;text-align:center;"><img id="personalWhatsappWebQrImg" alt="WhatsApp 扫码绑定" style="width:220px;height:220px;background:#fff;border-radius:8px;"><div class="pwa-meta">手机 WhatsApp → 设置 → 已连接的设备 → 连接设备</div></div></div>';
    wrap.innerHTML = BlockMarkup;
    var block = wrap.firstElementChild;
    if (!block) return;
    host.parentNode ? host.parentNode.insertBefore(block, host) : host.appendChild(block);
    var b = function (id) { return document.getElementById(id); };
    if (b('personalWhatsappWebLoginBtn')) b('personalWhatsappWebLoginBtn').addEventListener('click', function () { startWhatsappWebLogin(); });
    if (b('personalWhatsappWebSyncBtn')) b('personalWhatsappWebSyncBtn').addEventListener('click', function () { syncWhatsappContactsViaProtocol(); });
    if (b('personalWhatsappWebExportBtn')) b('personalWhatsappWebExportBtn').addEventListener('click', function () { exportWhatsappContactsCsv(); });
    if (b('personalWhatsappWebLogoutBtn')) b('personalWhatsappWebLogoutBtn').addEventListener('click', function () { logoutWhatsappWeb(); });
  }

  function renderContacts() { var host=$('personalWhatsappContactList');if(!host)return;host.innerHTML=state.contacts.length?state.contacts.map(function(row){var target=row.username||row.phone||row.display_name||'';return '<div class="pwa-item"><div class="pwa-item-title">'+esc(row.display_name||'未命名')+'</div><div class="pwa-meta">'+esc(row.username?('@'+row.username):'')+(row.username&&row.phone?' · ':'')+esc(row.phone||'')+'</div><div class="pwa-contact-actions"><button class="btn btn-primary btn-sm" data-pwa-contact-chat="'+esc(target)+'">打开会话</button></div></div>';}).join(''):'<div class="pwa-empty">暂无联系人，请先同步通讯录</div>';renderPagination('personalWhatsappContactPagination','contacts'); }
  function loadRecords(){var meta=state.pages.records;return request('/api/native-whatsapp/operations?'+query(meta)).then(function(data){state.records=Array.isArray(data.items)?data.items:[];meta.total=Number(data.total||0);renderRecords();});}
  function renderRecords(){var host=$('personalWhatsappRecordList');if(!host)return;host.innerHTML=state.records.length?state.records.map(function(row){return '<div class="pwa-record '+(row.status==='failed'?'failed':'')+'"><div class="pwa-item-title">'+esc(row.action||'操作')+' · '+esc(row.target||'无目标')+' <span class="pwa-badge">'+esc(row.status==='success'?'成功':'失败')+'</span></div><div class="pwa-meta">'+esc(row.message||'')+'</div><div class="pwa-meta">'+esc(row.created_at||'')+'</div></div>';}).join(''):'<div class="pwa-empty">暂无操作记录</div>';renderPagination('personalWhatsappRecordPagination','records');}

  function activateTab(tab) {
    state.tab = tab;
    document.querySelectorAll('#content-personal-whatsapp .pwa-tab').forEach(function(node){node.classList.toggle('active',node.dataset.pwaTab===tab);});
    document.querySelectorAll('#content-personal-whatsapp .pwa-panel').forEach(function(node){node.classList.toggle('active',node.dataset.pwaPanel===tab);});
    if(tab==='overview'){loadProtocolOverview().catch(function(e){showError(e.message);});loadWhatsappWebState().catch(function(){});loadWhatsappContactStats().catch(function(){});loadProtoWatch().catch(function(){});}
    if(tab==='contacts'){loadContacts().catch(function(e){showError(e.message);});loadProtocolLog().catch(function(){});}
    if(tab==='sessions'){loadProtoChats(false).catch(function(){});loadProtoSendPanel().catch(function(){});}
  if(tab==='sessions')loadSessions().catch(function(e){showError(e.message);});
    if(tab==='records')loadRecords().catch(function(e){showError(e.message);});
    if(tab==='friends'){loadFriendQueue().catch(function(e){showError(e.message);});loadFriendRecords().catch(function(e){showError(e.message);});}
    if(tab==='takeover'){loadAutoReply().catch(function(e){showError(e.message);});loadDiagnostics().catch(function(e){showError(e.message);});}
  }
  function saveConfig(showToast){return request('/api/native-whatsapp/config',{method:'POST',json:configFromFields()}).then(function(data){state.config=data.config||configFromFields();fillConfig();if(showToast!==false)toastMessage('WhatsApp 参数已保存');return state.config;});}
  function refreshActiveMessages(){if(!state.activeSession)return Promise.resolve();return request('/api/native-whatsapp/sessions/'+encodeURIComponent(state.activeSession.peer_key)+'/messages?limit=200&offset=0').then(function(data){state.messages=Array.isArray(data.items)?data.items:[];renderMessages();});}

  function bind() {
    var root=$('content-personal-whatsapp');if(!root||root.dataset.personalWhatsappBound==='1')return;root.dataset.personalWhatsappBound='1';
    root.addEventListener('click',function(event){var modalClose=event.target.closest('[data-pwa-modal-close]');if(modalClose){closeModalOf(modalClose);return;}if(event.target.classList&&event.target.classList.contains('pwa-modal-mask')){closeModal(event.target.id);return;}var tab=event.target.closest('[data-pwa-tab]');if(tab){activateTab(tab.dataset.pwaTab);return;}var peer=event.target.closest('[data-pwa-peer]');if(peer){selectSession(peer.dataset.pwaPeer);return;}var groupPeer=event.target.closest('[data-pwa-group-peer]');if(groupPeer){var group=state.groups.filter(function(row){return row.peer_key===groupPeer.dataset.pwaGroupPeer;})[0];activateTab('sessions');if(group)selectContactTarget(group.display_name,'group');return;}var page=event.target.closest('[data-pwa-page]');if(page){var meta=state.pages[page.dataset.pwaPage],dir=Number(page.dataset.pwaDir||0);meta.page=Math.max(1,Math.min(pageCount(meta),meta.page+dir));({sessions:loadSessions,groups:loadGroups,contacts:loadContacts,records:loadRecords,friendRecords:loadFriendRecords}[page.dataset.pwaPage]||function(){return Promise.resolve();})().catch(function(e){showError(e.message);});return;}var contact=event.target.closest('[data-pwa-contact-chat]');if(contact){activateTab('sessions');selectContactTarget(contact.dataset.pwaContactChat);}});
    $('personalWhatsappRefreshBtn').addEventListener('click',function(){runBusy('正在刷新状态...',loadBase).catch(function(){});});
    $('personalWhatsappSyncAllBtn').addEventListener('click',function(){runBusy('正在同步会话...',function(){var sessionResult=null;return request('/api/native-whatsapp/sessions/sync',{method:'POST',json:{account_id:ACCOUNT_ID,limit:1000,max_scrolls:30}}).then(function(data){sessionResult=data||{};return Promise.all([loadBase(),loadSessions(),loadRecords()]);}).then(function(){var summary=(sessionResult&&sessionResult.message)||'会话同步完成';showNotice(summary);toastMessage(summary);return {sessions:sessionResult};});}).catch(function(){});});
    $('personalWhatsappSyncSessionsBtn').addEventListener('click',function(){runBusy('正在读取桌面会话...',function(){return request('/api/native-whatsapp/sessions/sync',{method:'POST',json:{account_id:ACCOUNT_ID,limit:1000,max_scrolls:30}}).then(function(data){toastMessage(data.message||'会话同步完成');return Promise.all([loadSessions(),loadRecords(),loadBase()]);});}).catch(function(){});});
    $('personalWhatsappOpenSessionBtn').addEventListener('click',function(){if(!state.activeSession)return;runBusy('正在打开并同步会话...',function(){return request('/api/native-whatsapp/conversations/open',{method:'POST',json:{account_id:ACCOUNT_ID,target:state.activeSession.display_name}}).then(function(data){var peer=data&&data.peer||{};if(peer.peer_key)state.activeSession=peer;state.messages=Array.isArray(data&&data.messages)?data.messages.map(function(item,index){return {id:'live_'+index,direction:item.direction,content:item.text};}):[];renderMessages();return Promise.all([loadSessions(),loadRecords(),loadBase()]);});}).catch(function(){});});
    $('personalWhatsappSendBtn').addEventListener('click',function(){if(!state.activeSession)return;var content=text($('personalWhatsappSendContent').value).trim();if(!content){showError('请输入发送内容');return;}runBusy('正在发送并确认...',function(){$('personalWhatsappSendState').textContent='发送中';return request('/api/native-whatsapp/messages/send',{method:'POST',json:{account_id:ACCOUNT_ID,target:state.activeSession.display_name,content:content}}).then(function(data){if(data&&data.peer&&data.peer.peer_key)state.activeSession=data.peer;$('personalWhatsappSendContent').value='';$('personalWhatsappSendState').textContent='发送成功';toastMessage('WhatsApp 消息发送成功');return Promise.all([refreshActiveMessages(),loadSessions(),loadRecords(),loadBase()]);});}).catch(function(){$('personalWhatsappSendState').textContent='发送失败';});});
    $('personalWhatsappRunBtn').addEventListener('click',function(){runBusy('正在执行一轮 WhatsApp 接管...',function(){return saveConfig(false).then(function(){return request('/api/native-whatsapp/run-once',{method:'POST',json:{account_id:ACCOUNT_ID,config_override:configFromFields()}});}).then(function(result){state.lastRun=result||{};renderLastRun();toastMessage(result&&result.skipped?'已有 WhatsApp 操作正在执行':'WhatsApp 本轮执行完成');return Promise.all([loadBase(),loadSessions(),loadRecords()]);});}).catch(function(){});});
    $('personalWhatsappStopBtn').addEventListener('click',function(){request('/api/native-whatsapp/stop',{method:'POST',json:{account_id:ACCOUNT_ID}}).then(function(result){toastMessage(result.running?'已请求停止当前接管':'当前没有接管任务');return loadBase();}).catch(function(e){showError(e.message);});});
    $('personalWhatsappRefreshRecordsBtn').addEventListener('click',function(){loadRecords().catch(function(e){showError(e.message);});});
    $('personalWhatsappAddFriendBtn').addEventListener('click',function(){openFriendAddModal();});
    $('personalWhatsappAddContactBtn').addEventListener('click',function(){openModal('personalWhatsappContactModal');});
    $('personalWhatsappRefreshContactsBtn').addEventListener('click',function(){loadContacts(1);});
    if ($('personalWhatsappCopyDiag')) $('personalWhatsappCopyDiag').addEventListener('click',function(){var payload=JSON.stringify({version:state.version||{},status:state.status||{}},null,1);var done=function(){toastMessage('诊断信息已复制，发给支持即可');};try{if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(payload).then(done,done);return;}}catch(e){}window.prompt('复制下面的诊断信息',payload);});
    if ($('personalWhatsappDiagToggle')) $('personalWhatsappDiagToggle').addEventListener('click',function(){var detail=$('personalWhatsappDiagDetail');if(!detail)return;var show=detail.style.display==='none';detail.style.display=show?'block':'none';this.textContent=show?'收起诊断详情':'查看诊断详情';});
    if ($('personalWhatsappRepairDeps')) $('personalWhatsappRepairDeps').addEventListener('click',function(){runBusy('正在修复运行依赖（首次可能需要下载）...',function(){return request('/api/settings/repair-runtime-dependencies',{method:'POST',json:{}}).then(function(data){var ok=data&&data.ok;showNotice((ok?'依赖已修复完成。':'仍有依赖缺失。')+((data&&data.message)||''));toastMessage(ok?'依赖修复完成':'依赖仍不可用，请看提示');return loadBase();});}).catch(function(){});});
  $('personalWhatsappContactSearch').addEventListener('input',function(){state.pages.contacts.keyword=this.value;loadContacts(1);});
  $('personalWhatsappWebLoginBtn').addEventListener('click',function(){startWhatsappWebLogin();});
  $('personalWhatsappWebSyncBtn').addEventListener('click',function(){syncWhatsappContactsViaProtocol();});
  $('personalWhatsappWebExportBtn').addEventListener('click',function(){exportWhatsappContactsCsv();});
  $('personalWhatsappWebLogoutBtn').addEventListener('click',function(){logoutWhatsappWeb();});
  $('personalWhatsappContactList').addEventListener('click',function(ev){var b=ev.target.closest('[data-pwa-contact-edit]');if(!b)return;var key=b.getAttribute('data-pwa-contact-edit')||'';var cur=b.getAttribute('data-pwa-contact-phone')||'';var next=window.prompt('修改手机号（留空 = 清空），带国家码如 8613800138000',cur);if(next===null)return;request('/api/native-whatsapp/contacts/update',{method:'POST',json:{contact_key:key,phone:String(next).trim()}}).then(function(){return loadContacts();}).catch(function(err){setWebState(err.message||'修改失败',true);});});
  $('personalWhatsappContactSubmitBtn').addEventListener('click',function(){runBusy('正在填写桌面联系人表单...',submitSingleContact).catch(function(){});});
    $('personalWhatsappAddFriendSubmitBtn').addEventListener('click',function(){runBusy('正在提交好友申请...',submitFriendQueue).catch(function(){});});
    $('personalWhatsappFriendQueueStartBtn').addEventListener('click',function(){runBusy('正在启动加好友队列...',startFriendQueue).catch(function(){});});
    $('personalWhatsappFriendQueueStopBtn').addEventListener('click',function(){runBusy('正在停止加好友队列...',stopFriendQueue).catch(function(){});});
    $('personalWhatsappFriendQueueSettingsBtn').addEventListener('click',function(){openFriendSettingsModal();});
    $('personalWhatsappFriendSettingsSaveBtn').addEventListener('click',function(){runBusy('正在保存加好友队列设置...',saveFriendQueueSettings).catch(function(){});});
    $('personalWhatsappFriendImportBtn').addEventListener('click',function(){$('personalWhatsappFriendImportInput').click();});
    $('personalWhatsappFriendImportInput').addEventListener('change',function(){var input=this;Promise.resolve(importFriendFiles(input.files)).catch(function(e){showError(e&&e.message||'导入文件失败');}).then(function(){input.value='';});});
    $('personalWhatsappDownloadTxtTemplateBtn').addEventListener('click',downloadFriendTemplate);
    $('personalWhatsappFriendRecordSearch').addEventListener('input',debounce(function(){state.pages.friendRecords.keyword=text($('personalWhatsappFriendRecordSearch').value).trim();state.pages.friendRecords.page=1;loadFriendRecords().catch(function(e){showError(e.message);});},300));
    $('personalWhatsappFriendRecordStatus').addEventListener('change',function(){state.pages.friendRecords.status=this.value;state.pages.friendRecords.page=1;loadFriendRecords().catch(function(e){showError(e.message);});});
    $('personalWhatsappTakeoverSettingsBtn').addEventListener('click',function(){openTakeoverSettingsModal();});
    $('personalWhatsappSaveBtn').addEventListener('click',function(){runBusy('正在保存接管参数...',saveTakeoverSettings).catch(function(){});});
    $('personalWhatsappLoopStartBtn').addEventListener('click',function(){runBusy('正在启动常驻接管...',startAutoReplyLoop).catch(function(){});});
    $('personalWhatsappLoopStopBtn').addEventListener('click',function(){runBusy('正在停止常驻接管...',stopAutoReplyLoop).catch(function(){});});
    $('personalWhatsappDiagnosticsBtn').addEventListener('click',function(){loadDiagnostics().catch(function(e){showError(e.message);});});
    document.addEventListener('keydown',function(event){if(event.key==='Escape')closeModal(null);});
    $('personalWhatsappSessionSearch').addEventListener('input',debounce(function(){state.pages.sessions.keyword=text($('personalWhatsappSessionSearch').value).trim();state.pages.sessions.page=1;loadSessions().catch(function(e){showError(e.message);});},300));
    $('personalWhatsappSessionType').addEventListener('change',function(){state.pages.sessions.chatType=this.value;state.pages.sessions.page=1;loadSessions().catch(function(e){showError(e.message);});});
    root.addEventListener('click', function(event) {
      var cancelBtn = event.target.closest && event.target.closest('[data-pwa-friend-cancel]');
      if (cancelBtn) {
        var cancelId = cancelBtn.dataset.pwaFriendCancel;
        runBusy('正在停止这条记录...', function() {
          return request('/api/native-whatsapp/friends/records/' + encodeURIComponent(cancelId) + '/cancel', { method: 'POST', json: { account_id: ACCOUNT_ID } }).then(function(data) {
            toastMessage((data && data.message) || '已停止');
            return Promise.all([loadFriendRecords(), loadFriendQueue(), loadRecords(), loadBase()]);
          });
        }).catch(function() {});
        return;
      }
      var retryBtn = event.target.closest && event.target.closest('[data-pwa-friend-retry]');
      if (retryBtn) {
        var retryId = retryBtn.dataset.pwaFriendRetry;
        runBusy('正在重新排队...', function() {
          return request('/api/native-whatsapp/friends/records/' + encodeURIComponent(retryId) + '/retry', { method: 'POST', json: { account_id: ACCOUNT_ID } }).then(function(data) {
            toastMessage((data && data.message) || '已重新排队');
            return Promise.all([loadFriendRecords(), loadFriendQueue(), loadRecords(), loadBase()]);
          });
        }).catch(function() {});
        return;
      }
      var delBtn = event.target.closest && event.target.closest('[data-pwa-friend-delete]');
      if (delBtn) {
        if (delBtn.dataset.confirm !== '1') {
          delBtn.dataset.confirm = '1';
          delBtn.dataset.label = delBtn.textContent;
          delBtn.textContent = '确认删除';
          setTimeout(function() { if (delBtn.dataset.confirm === '1') { delBtn.dataset.confirm = ''; delBtn.textContent = delBtn.dataset.label || '删除'; } }, 4000);
          return;
        }
        var delId = delBtn.dataset.pwaFriendDelete;
        runBusy('正在删除记录...', function() {
          return request('/api/native-whatsapp/friends/records/' + encodeURIComponent(delId), { method: 'DELETE' }).then(function(data) {
            toastMessage((data && data.message) || '记录已删除');
            return Promise.all([loadFriendRecords(), loadFriendQueue(), loadRecords(), loadBase()]);
          });
        }).catch(function() {});
        return;
      }
    });
    $('personalWhatsappRecordSearch').addEventListener('input',debounce(function(){state.pages.records.keyword=text($('personalWhatsappRecordSearch').value).trim();state.pages.records.page=1;loadRecords().catch(function(e){showError(e.message);});},300));
  }

  // ── 弹窗：新增/编辑一律进弹窗，页面上只留列表与工具栏 ─────────────────────
  function openModal(id) { var node = $(id); if (!node) return null; node.classList.add('show'); return node; }
  function closeModal(id) {
    var masks = id ? [$(id)] : Array.prototype.slice.call(document.querySelectorAll('#content-personal-whatsapp .pwa-modal-mask'));
    masks.forEach(function(mask) { if (mask) mask.classList.remove('show'); });
  }
  function closeModalOf(node) { var mask = node && node.closest ? node.closest('.pwa-modal-mask') : null; if (mask) mask.classList.remove('show'); }

  // ── 加好友队列（对齐微信协议助手 /friends 流程，桌面动作仍走 WhatsApp UIA）──
  function splitTargetLines(value) {
    var seen = {};
    return text(value).split(/[\r\n;；]+/).map(function(item) { return item.trim(); }).filter(function(item) {
      var key = item.toLowerCase();
      if (!item || seen[key]) return false;
      seen[key] = true;
      return true;
    });
  }
  function friendStatusText(value) { return ({queued:'排队中',pending:'排队中',running:'执行中',success:'成功',failed:'失败',partial_failed:'部分成功',cancelled:'已停止'}[String(value || '').toLowerCase()] || value || '-'); }
  function friendStatusClass(value) { var key = String(value || '').toLowerCase(); if (key === 'success') return ''; if (key === 'failed') return ' bad'; return ' warn'; }
  function uniqueRequestId() { return 'wa-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8); }
  function friendIntervalField() { return numberField('personalWhatsappFriendInterval', 60, 1, 86400); }
  function friendDailyLimitField() { return numberField('personalWhatsappFriendDailyLimit', 30, 0, 1000); }
  function friendAutoStartChecked() { var node = $('personalWhatsappFriendAutoStart'); return !!(node && node.checked); }
  function showModalError(hostId, message) { var node = $(hostId); if (!node) return; node.textContent = message || ''; node.style.display = message ? 'block' : 'none'; }
  function namelessTargetLines(lines) {
    // WhatsApp 添加联系人表单必须有姓名：只写号码的行先在前端拦下来
    return lines.filter(function(line) {
      var parts = line.split(/[,，\t]+/).map(function(part) { return part.trim(); }).filter(Boolean);
      if (!parts.length) return false;
      var head = parts.length >= 2 ? parts.slice(0, -1).join('') : '';
      return !head && /^[+\d\s\-()]+$/.test(parts[parts.length - 1]);
    });
  }

  function renderFriendQueue() {
    var control = state.friendQueue || {}, summary = state.friendSummary || {};
    var running = !!control.running, enabled = !!control.enabled;
    var chip = $('personalWhatsappFriendQueueState');
    if (chip) { chip.className = 'pwa-chip' + (running ? '' : (enabled ? ' warn' : '')); chip.textContent = running ? '运行中' : (enabled ? '已启动' : '未启动'); }
    var label = $('personalWhatsappFriendQueueSummary');
    if (label) label.textContent = (running ? '运行中' : (enabled ? '已启动' : '已停止')) + ' · 间隔 ' + Number(control.interval_seconds || 60) + ' 秒 · 日上限 ' + Number(control.daily_limit || 0) + ' 条 · 今日已加 ' + Number(summary.added_today || 0);
    var stats = $('personalWhatsappFriendQueueStats');
    if (stats) stats.innerHTML = statusCard('排队中', Number(summary.queued || 0) + ' 条') + statusCard('执行中', Number(summary.running || 0) + ' 条') + statusCard('累计成功', Number(summary.success || 0)) + statusCard('今日成功', Number(summary.today_success || 0) + ' / ' + Number(summary.added_today || 0), Number(summary.today_success || 0) ? 'good' : '') + statusCard('今日失败', Number(summary.today_failed || 0), Number(summary.today_failed || 0) ? 'bad' : '');
    var interval = $('personalWhatsappFriendInterval');
    if (interval && document.activeElement !== interval) interval.value = Math.max(1, Math.min(86400, Number(control.interval_seconds || 60)));
    var limit = $('personalWhatsappFriendDailyLimit');
    if (limit && document.activeElement !== limit) limit.value = Math.max(0, Math.min(1000, Number(control.daily_limit || 0)));
  }

  function loadFriendQueue() {
    return request('/api/native-whatsapp/friends/queue?account_id=' + encodeURIComponent(ACCOUNT_ID)).then(function(data) {
      state.friendQueue = data.control || {}; state.friendSummary = data.summary || {}; renderFriendQueue(); return state.friendQueue;
    });
  }
  function loadFriendRecords() {
    var meta = state.pages.friendRecords;
    var params = new URLSearchParams();
    params.set('account_id', ACCOUNT_ID); params.set('limit', meta.limit); params.set('offset', Math.max(0, (meta.page - 1) * meta.limit));
    if (meta.keyword) params.set('keyword', meta.keyword);
    if (meta.status) params.set('status', meta.status);
    return request('/api/native-whatsapp/friends/records?' + params.toString()).then(function(data) {
      state.friendRecords = Array.isArray(data.items) ? data.items : [];
      meta.total = Number(data.total || data.count || 0);
      renderFriendRecords();
      scheduleFriendRecordRefresh();
    });
  }
  // 有「执行中」的记录时每 5 秒自动刷新一次，避免用户看到一条永远不动的「执行中」
  function scheduleFriendRecordRefresh() {
    if (state.friendRecordTimer) { clearTimeout(state.friendRecordTimer); state.friendRecordTimer = null; }
    var running = (state.friendRecords || []).some(function(item) { return item.status === 'running'; });
    if (!running) return;
    state.friendRecordTimer = setTimeout(function() { loadFriendRecords().catch(function() {}); }, 5000);
  }
  function renderFriendRecords() {
    var host = $('personalWhatsappFriendRecordList'); if (!host) return;
    if (!state.friendRecords.length) { host.className = 'pwa-empty'; host.textContent = '暂无加好友记录'; renderPagination('personalWhatsappFriendRecordPagination','friendRecords'); return; }
    host.className = 'pwa-table-wrap';
    host.innerHTML = '<table class="pwa-table"><thead><tr><th>姓名</th><th>号码 / @用户名</th><th>状态</th><th>验证消息</th><th>时间</th><th>操作</th></tr></thead><tbody>' + state.friendRecords.map(function(item) {
      var detail = [item.updated_at || item.created_at || '', item.error_message || ''].filter(Boolean).join(' · ');
      var nameText = [item.first_name || '', item.last_name || ''].filter(Boolean).join(' ') || '-';
      var contactText = item.phone ? item.phone : (item.username ? '@' + item.username : '-');
      var actions = '';
      if (item.status === 'running') {
        actions += '<button type="button" class="btn btn-ghost btn-sm" data-pwa-friend-cancel="' + esc(item.id) + '">停止</button>';
      } else {
        if (item.status === 'failed' || item.status === 'partial_failed' || item.status === 'cancelled') actions += '<button type="button" class="btn btn-ghost btn-sm" data-pwa-friend-retry="' + esc(item.id) + '">重试</button>';
        actions += '<button type="button" class="btn btn-ghost btn-sm" data-pwa-friend-delete="' + esc(item.id) + '">删除</button>';
      }
      return '<tr><td>' + esc(nameText) + '</td><td>' + esc(contactText) + '</td><td><span class="pwa-chip' + friendStatusClass(item.status) + '">' + esc(friendStatusText(item.status)) + '</span></td><td>' + esc(item.apply_message || '-') + '</td><td>' + esc(detail || '-') + '</td><td>' + actions + '</td></tr>';
    }).join('') + '</tbody></table>';
    renderPagination('personalWhatsappFriendRecordPagination','friendRecords');
  }

  function openFriendAddModal() { var node = $('personalWhatsappAddFriendState'); if (node) node.textContent = '等待提交'; showModalError('personalWhatsappFriendAddError', ''); openModal('personalWhatsappFriendAddModal'); }
  function submitFriendQueue() {
    var targets = splitTargetLines($('personalWhatsappFriendKeyword') && $('personalWhatsappFriendKeyword').value);
    if (!targets.length) { showError('请先填写至少一个目标（一行一个）'); showModalError('personalWhatsappFriendAddError', '请先填写至少一个目标（一行一个）'); return Promise.resolve(); }
    var nameless = namelessTargetLines(targets);
    if (nameless.length) {
      var tip = '这几行只写了号码、没写姓名：' + nameless.slice(0, 3).join('、') + '。WhatsApp 添加联系人表单必须有名字，请写成「姓名,电话」或「姓名,姓氏,电话」';
      showModalError('personalWhatsappFriendAddError', tip);
      return Promise.resolve();
    }
    showModalError('personalWhatsappFriendAddError', '');
    var body = {
      account_id: ACCOUNT_ID,
      targets: targets,
      apply_message: text($('personalWhatsappFriendApplyMessage') && $('personalWhatsappFriendApplyMessage').value).trim().slice(0, 1000),
      interval_seconds: friendIntervalField(),
      daily_limit: friendDailyLimitField(),
      queue_only: true,
      client_request_id: uniqueRequestId()
    };
    var submitState = $('personalWhatsappAddFriendState');
    if (submitState) submitState.textContent = '提交中';
    var autoStart = friendAutoStartChecked();
    return request('/api/native-whatsapp/friends/add', { method: 'POST', json: body }).then(function(data) {
      var planned = Number(data && data.planned_total || targets.length);
      if ($('personalWhatsappFriendKeyword')) $('personalWhatsappFriendKeyword').value = '';
      if ($('personalWhatsappFriendApplyMessage')) $('personalWhatsappFriendApplyMessage').value = '';
      closeModal('personalWhatsappFriendAddModal');
      showModalError('personalWhatsappFriendAddError', '');
      showNotice((autoStart ? '已入队并启动队列：' : '已加入加好友队列：') + planned + ' 条目标');
      toastMessage('已加入加好友队列：' + planned + ' 条');
      var chain = autoStart
        ? request('/api/native-whatsapp/friends/queue/start', { method: 'POST', json: { account_id: ACCOUNT_ID, interval_seconds: friendIntervalField(), daily_limit: friendDailyLimitField() } })
        : Promise.resolve(null);
      return chain.then(function() { return Promise.all([loadFriendQueue(), loadFriendRecords(), loadRecords()]); });
    }).catch(function(error) {
      if (submitState) submitState.textContent = '提交失败';
      var message = (error && error.message) || '提交好友申请失败';
      showModalError('personalWhatsappFriendAddError', message);
      showError(message);
    });
  }
  function openFriendSettingsModal() { loadFriendQueue().catch(function() {}).then(function() { openModal('personalWhatsappFriendSettingsModal'); }); }
  function saveFriendQueueSettings() {
    return request('/api/native-whatsapp/friends/queue/settings', { method: 'POST', json: { account_id: ACCOUNT_ID, interval_seconds: friendIntervalField(), daily_limit: friendDailyLimitField() } }).then(function(data) {
      state.friendQueue = data.control || {}; state.friendSummary = data.summary || {}; renderFriendQueue();
      closeModal('personalWhatsappFriendSettingsModal');
      toastMessage('加好友队列设置已保存');
      return state.friendQueue;
    });
  }
  function startFriendQueue() {
    return request('/api/native-whatsapp/friends/queue/start', { method: 'POST', json: { account_id: ACCOUNT_ID, interval_seconds: friendIntervalField(), daily_limit: friendDailyLimitField() } }).then(function(data) {
      state.friendQueue = data.control || {}; state.friendSummary = data.summary || {}; renderFriendQueue();
      toastMessage('加好友队列已启动');
      return Promise.all([loadFriendQueue(), loadFriendRecords()]);
    });
  }
  function stopFriendQueue() {
    return request('/api/native-whatsapp/friends/queue/stop', { method: 'POST', json: { account_id: ACCOUNT_ID } }).then(function(data) {
      state.friendQueue = data.control || {}; state.friendSummary = data.summary || {}; renderFriendQueue();
      toastMessage('加好友队列已停止');
      return Promise.all([loadFriendQueue(), loadFriendRecords()]);
    });
  }
  function importFriendFiles(fileList) {
    var files = Array.prototype.slice.call(fileList || []);
    if (!files.length) return Promise.resolve(0);
    return Promise.all(files.map(function(file) { return file.text(); })).then(function(contents) {
      var lines = [];
      contents.forEach(function(content) { lines = lines.concat(splitTargetLines(content)); });
      var area = $('personalWhatsappFriendKeyword');
      if (area) { var existing = area.value.trim(); area.value = (existing ? existing + '\n' : '') + lines.join('\n'); }
      openFriendAddModal();
      toastMessage('已导入 ' + lines.length + ' 行目标');
      return lines.length;
    });
  }
  function downloadFriendTemplate() {
    // 与个人微信那套一致：有 pywebview 就走原生保存（save_text_file），否则退回浏览器下载
    var content = ['# 一行一个目标：电话 / 名字,电话 / @用户名', '张三,13800138000', '张三,+8613800138001', 'Li,+393311234567', '@alice_wa', ''].join('\r\n');
    var filename = 'whatsapp-friend-targets.txt';
    function browserDownload() {
      var blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
      var url = URL.createObjectURL(blob);
      var link = document.createElement('a');
      link.href = url; link.download = filename; link.style.display = 'none';
      document.body.appendChild(link); link.click();
      window.setTimeout(function() { link.remove(); URL.revokeObjectURL(url); }, 1000);
      toastMessage('模板已下载，请到默认下载目录查看');
      return Promise.resolve();
    }
    if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.save_text_file === 'function') {
      return Promise.resolve(window.pywebview.api.save_text_file(filename, content)).then(function(result) {
        if (result && result.cancelled) return;
        if (result && result.ok) toastMessage('模板已保存：' + (result.path || result.filename || ''));
        else return browserDownload();
      }).catch(function() { return browserDownload(); });
    }
    return browserDownload();
  }

  function submitSingleContact() {
    var body = { account_id: ACCOUNT_ID, first_name: text($('personalWhatsappContactFirstName').value).trim(), last_name: text($('personalWhatsappContactLastName').value).trim(), username: text($('personalWhatsappContactUsername').value).trim(), phone: text($('personalWhatsappContactPhone').value).trim(), country_code: $('personalWhatsappCountryCode').value };
    if (!body.first_name || (!body.username && !body.phone)) {
      showModalError('personalWhatsappContactError', '请填写名字，并填写 @用户名或手机号');
      showError('请填写名字，并填写 @用户名或手机号');
      return Promise.resolve();
    }
    showModalError('personalWhatsappContactError', '');
    var stateNode = $('personalWhatsappAddContactState');
    if (stateNode) stateNode.textContent = '提交中';
    return request('/api/native-whatsapp/contacts', { method: 'POST', json: body }).then(function(data) {
      closeModal('personalWhatsappContactModal');
      toastMessage(data.message || '联系人已保存');
      ['personalWhatsappContactFirstName','personalWhatsappContactLastName','personalWhatsappContactUsername','personalWhatsappContactPhone'].forEach(function(id) { if ($(id)) $(id).value = ''; });
      return Promise.all([loadContacts(), loadRecords(), loadBase()]);
    }).catch(function(error) {
      if (stateNode) stateNode.textContent = '保存失败';
      var message = (error && error.message) || '添加联系人失败';
      showModalError('personalWhatsappContactError', message);
      showError(message);
    });
  }

  // ── 接管：手动一轮 / 常驻循环 + 诊断 ─────────────────────────────────────
  function renderTakeoverSummary() {
    var c = state.config || {}, node = $('personalWhatsappTakeoverSummary');
    if (node) node.textContent = '间隔 ' + Math.max(1, Math.min(300, Number(c.interval_seconds || 15))) + ' 秒 · 每轮最多 ' + Math.max(1, Math.min(100, Number(c.max_unread_per_round || 50))) + ' 个会话 · 单轮 ' + Math.max(1, Math.min(1440, Number(c.takeover_session_minutes || 30))) + ' 分钟';
  }
  function renderAutoReply() {
    var autoState = state.autoReply || {};
    var running = !!autoState.running;
    var chip = $('personalWhatsappTakeoverState');
    if (chip) { chip.className = 'pwa-chip' + (running ? '' : ' warn'); chip.textContent = running ? '常驻接管中' : '未接管'; }
    var label = $('personalWhatsappLoopSummary');
    if (label) label.textContent = running ? ('常驻接管运行中 · 间隔 ' + Number(autoState.interval_seconds || 0) + ' 秒') : '常驻接管未启动';
  }
  function loadAutoReply() {
    return request('/api/native-whatsapp/auto-reply/config').then(function(data) {
      state.autoReply = data.state || {};
      if (data.config) { state.config = data.config; state.lastRun = data.config.last_run || state.lastRun || {}; fillConfig(); renderLastRun(); }
      renderTakeoverSummary(); renderAutoReply();
      return state.autoReply;
    });
  }
  function openTakeoverSettingsModal() { fillConfig(); renderTakeoverSummary(); openModal('personalWhatsappTakeoverSettingsModal'); }
  function saveTakeoverSettings() {
    return saveConfig(true).then(function(config) {
      state.config = config || state.config;
      closeModal('personalWhatsappTakeoverSettingsModal');
      renderTakeoverSummary();
      return config;
    });
  }
  function startAutoReplyLoop() {
    return saveConfig(false).then(function() {
      return request('/api/native-whatsapp/auto-reply/loop/start', { method: 'POST', json: { account_id: ACCOUNT_ID, interval_seconds: numberField('personalWhatsappInterval', 15, 5, 300), config_override: configFromFields() } });
    }).then(function(data) {
      state.autoReply = data.state || {}; renderAutoReply(); toastMessage('常驻接管已启动');
      return loadDiagnostics();
    });
  }
  function stopAutoReplyLoop() {
    return request('/api/native-whatsapp/auto-reply/loop/stop', { method: 'POST', json: { account_id: ACCOUNT_ID } }).then(function(data) {
      state.autoReply = data.state || {}; renderAutoReply(); toastMessage('常驻接管已停止');
      return loadDiagnostics();
    });
  }
  function loadDiagnostics() {
    return request('/api/native-whatsapp/auto-reply/diagnostics?limit=20').then(function(data) {
      state.diagnostics = data || {};
      state.autoReply = data.state || state.autoReply || {};
      if (data.last_run) { state.lastRun = data.last_run; renderLastRun(); }
      renderAutoReply(); renderDiagnostics();
    });
  }
  function renderDiagnostics() {
    var host = $('personalWhatsappDiagnostics'); if (!host) return;
    var events = state.diagnostics && Array.isArray(state.diagnostics.events) ? state.diagnostics.events.slice().reverse() : [];
    host.innerHTML = events.length ? events.map(function(item) {
      var detail = [];
      Object.keys(item || {}).forEach(function(key) { if (key !== 'event' && key !== 'ts') detail.push(key + '=' + text(item[key])); });
      return '<div class="pwa-record"><div class="pwa-item-title">' + esc(item.event || '事件') + ' <span class="pwa-badge">' + esc(item.ts || '') + '</span></div><div class="pwa-meta">' + esc(detail.join(' · ') || '-') + '</div></div>';
    }).join('') : '<div class="pwa-empty">暂无日志</div>';
  }

  // ── 协议直连：会话 / 读消息 / 群 / 号码校验 / 头像资料 / 发送（限速） / 协议日志 ──
  var proto = { chats: [], messages: [], control: {}, summary: {}, records: [], log: [], target: '', chatKeyword: '' };

  function protoRows(rows, render, empty) {
    return (rows && rows.length) ? rows.map(render).join('') : '<div class="pwa-empty">' + esc(empty) + '</div>';
  }
  function protoOn(id, fn) { var node = $(id); if (node) node.addEventListener('click', fn); }
  function setProtoState(message, isErr) {
    var node = $('personalWhatsappProtocolState');
    if (!node) return;
    node.textContent = message || '';
    node.style.color = isErr ? '#c0392b' : '';
  }

  function ensureProtocolPanel() {
    // 结构现在写在视图里（static/views/personal-whatsapp.html），这里只保证事件绑上
    bindProtocolPanel();
  }

  function bindProtocolPanel() {
    var root = $('personalWhatsappProtocolBlock');
    if (!root || root.dataset.protoBound === '1') return;
    root.dataset.protoBound = '1';
    protoOn('personalWhatsappProtoChatsBtn', function () { loadProtoChats(true).catch(function (e) { setProtoState(e.message || '读取会话失败', true); }); });
    protoOn('personalWhatsappProtoGroupsBtn', function () { loadProtoGroups(true).catch(function (e) { setProtoState(e.message || '读取群聊失败', true); }); });
    protoOn('personalWhatsappProtoLogBtn', function () { loadProtocolLog().catch(function (e) { setProtoState(e.message || '读取日志失败', true); }); });
    protoOn('personalWhatsappProtoWatchToggleBtn', function () { toggleProtoWatch().catch(function (e) { setProtoState(e.message || '常驻操作失败', true); }); });
    protoOn('personalWhatsappProtoCheckBtn', function () { runProtoCheck(); });
    protoOn('personalWhatsappProtoProfileBtn', function () { runProtoProfile(); });
    protoOn('personalWhatsappProtoSendSaveBtn', function () { saveProtoSendControl(); });
    protoOn('personalWhatsappProtoSendBtn', function () { submitProtoSend(); });
    protoOn('personalWhatsappProtoSendStartBtn', function () { toggleProtoSendQueue(true); });
    protoOn('personalWhatsappProtoSendStopBtn', function () { toggleProtoSendQueue(false); });
    var search = $('personalWhatsappProtoChatSearch');
    if (search) search.addEventListener('input', debounce(function () { proto.chatKeyword = text(search.value).trim(); loadProtoChats(false).catch(function () {}); }, 300));
    var list = $('personalWhatsappProtoChatList');
    if (list) list.addEventListener('click', function (ev) {
      var read = ev.target.closest('[data-proto-read]');
      if (read) { readProtoMessages(read.getAttribute('data-proto-read')); return; }
      var pick = ev.target.closest('[data-proto-target]');
      if (pick) { proto.target = pick.getAttribute('data-proto-target'); renderProtoTarget(); }
    });
  }

  function renderProtoTarget() {
    var node = $('personalWhatsappProtoTarget');
    if (node) node.textContent = proto.target ? ('发送目标：' + proto.target) : '发送目标：未选择';
  }

  function loadProtocolPanel() {
    return Promise.all([loadProtocolOverview(), loadProtoChats(false), loadProtoSendPanel(), loadProtocolLog(), loadProtoWatch(), loadWhatsappWebState(), loadWhatsappContactStats()]).then(function () {});
  }

  // 常驻：登录之后接「后面新加的好友」——长连接 + 每次重连/每 N 秒按版本游标补增量，不需要重新扫码。

  // 扫码后拿到的信息：账号 / 号码 / 联系人 / 协议会话 / 常驻增量，都摆在「状态」里
  function loadProtocolOverview() {
    var host = $('personalWhatsappWebInfo');
    if (!host) return Promise.resolve();
    return Promise.all([
      request('/api/native-whatsapp/web/login').catch(function () { return {}; }),
      request('/api/native-whatsapp/contacts/stats').catch(function () { return {}; }),
      request('/api/native-whatsapp/web/chats?limit=200&refresh=0').catch(function () { return { count: 0 }; }),
      request('/api/native-whatsapp/web/groups?limit=200&refresh=0').catch(function () { return { count: 0 }; }),
      request('/api/native-whatsapp/web/watch').catch(function () { return {}; })
    ]).then(function (rows) {
      var login = rows[0] || {}; var stats = rows[1] || {}; var chats = rows[2] || {}; var groups = rows[3] || {}; var watch = rows[4] || {};
      var me = login.me || {};
      var account = me.phone ? ('+' + me.phone) : (me.name || '');
      host.innerHTML = statusCard('绑定账号', login.linked ? (account || '已绑定') : '未绑定', login.linked ? 'good' : 'bad')
        + statusCard('绑定名称', me.name || '—', '')
        + statusCard('联系人', Number(stats.total || 0) + ' 个', '')
        + statusCard('其中带手机号', Number(stats.with_phone || 0) + ' 个', '')
        + statusCard('协议会话', Number(chats.count || 0) + ' 个', '')
        + statusCard('群聊', Number(groups.count || 0) + ' 个', '')
        + statusCard('常驻增量', watch.running ? ('新好友 ' + Number(watch.contacts_new || 0) + ' / 新消息 ' + Number(watch.messages || 0)) : '未开启', watch.running ? 'good' : '')
        + statusCard('登录进程', login.state ? String(login.state) : 'idle', login.error ? 'bad' : '');
      return rows;
    });
  }

  function loadProtoWatch() {
    if (!$('personalWhatsappProtoWatchState')) return Promise.resolve();
    return request('/api/native-whatsapp/web/watch').then(function (data) {
      proto.watch = data || {};
      var node = $('personalWhatsappProtoWatchState');
      if (node) {
        node.textContent = data.running
          ? ('常驻中：新好友 ' + Number(data.contacts_new || 0) + ' · 新会话 ' + Number(data.chats || 0) + ' · 新消息 ' + Number(data.messages || 0) + (data.last_event ? (' · ' + data.last_event) : ''))
          : (data.logged_out ? '常驻已停：手机解绑了，请重新扫码一次' : '常驻未开启（开启后新好友/新消息自动入库）');
        node.style.color = data.running ? '' : '#8a6d3b';
      }
      var btn = $('personalWhatsappProtoWatchToggleBtn');
      if (btn) btn.textContent = data.running ? '停止常驻' : '开启常驻（接新好友）';
      return data;
    }).catch(function (err) {
      var node = $('personalWhatsappProtoWatchState');
      if (node) node.textContent = err.message || '常驻状态读取失败';
    });
  }

  function toggleProtoWatch() {
    var start = !(proto.watch && proto.watch.running);
    return request(start ? '/api/native-whatsapp/web/watch/start' : '/api/native-whatsapp/web/watch/stop', { method: 'POST', json: { account_id: ACCOUNT_ID } })
      .then(function (data) { proto.watch = data || {}; return loadProtoWatch(); })
      .then(function () { toastMessage(start ? '协议常驻已开启：新好友/新会话/新消息会自动入库' : '协议常驻已停止'); });
  }

  function loadProtoChats(refresh) {
    var params = '?limit=200&refresh=' + (refresh ? 1 : 0) + (proto.chatKeyword ? ('&keyword=' + encodeURIComponent(proto.chatKeyword)) : '');
    if (refresh) setProtoState('正在走协议读取会话…', false);
    return request('/api/native-whatsapp/web/chats' + params).then(function (data) {
      proto.chats = Array.isArray(data.items) ? data.items : [];
      var host = $('personalWhatsappProtoChatList');
      if (host) host.innerHTML = protoRows(proto.chats, function (row) {
        var jid = text(row.jid);
        return '<div class="pwa-item"><div class="pwa-item-title">' + esc(row.name || (row.is_group ? '未命名群聊' : jid)) + (row.is_group ? ' <span class="pwa-badge">群聊</span>' : '') + '</div>'
          + '<div class="pwa-meta">' + esc(jid) + (row.unread ? (' · 未读 ' + esc(row.unread)) : '') + '</div>'
          + '<div class="pwa-contact-actions"><button class="btn btn-ghost btn-sm" data-proto-read="' + esc(jid) + '">读消息</button>'
          + (row.is_group ? '' : '<button class="btn btn-primary btn-sm" data-proto-target="' + esc(jid) + '">选为发送目标</button>') + '</div></div>';
      }, '暂无会话（先「同步通讯录」或点刷新从协议读一次）');
      if (refresh) setProtoState('协议会话已更新：' + proto.chats.length + ' 条', false);
      renderProtoTarget();
      return data;
    });
  }

  function loadProtoGroups(refresh) {
    if (refresh) setProtoState('正在走协议读取群聊…', false);
    return request('/api/native-whatsapp/web/groups?limit=200&refresh=' + (refresh ? 1 : 0)).then(function (data) {
      var rows = Array.isArray(data.items) ? data.items : [];
      var host = $('personalWhatsappProtoChatList');
      if (host) host.innerHTML = protoRows(rows, function (row) {
        return '<div class="pwa-item"><div class="pwa-item-title">' + esc(row.name || '未命名群聊') + ' <span class="pwa-badge">群聊</span></div><div class="pwa-meta">' + esc(text(row.jid)) + '</div></div>';
      }, '暂无群聊');
      if (refresh) setProtoState('协议群聊已更新：' + rows.length + ' 个', false);
      return data;
    });
  }

  function readProtoMessages(jid) {
    setProtoState('正在读 ' + jid + ' 的消息…', false);
    return request('/api/native-whatsapp/web/messages?refresh=1&limit=60&jid=' + encodeURIComponent(jid)).then(function (data) {
      var host = $('personalWhatsappProtoMessageList');
      var rows = Array.isArray(data.items) ? data.items : [];
      if (host) host.innerHTML = '<div class="pwa-item-title">' + esc(jid) + ' <span class="pwa-badge">' + rows.length + ' 条</span></div>' + protoRows(rows, function (row) {
        return '<div class="pwa-bubble ' + (row.from_me ? 'outbound' : 'inbound') + '">' + esc(row.content || '') + '</div>';
      }, '这次没读到消息（首次同步可能还没下发）');
      setProtoState('已读 ' + rows.length + ' 条（协议直读，不再走 UIA）', false);
      return data;
    });
  }

  function runProtoCheck() {
    var raw = text($('personalWhatsappProtoCheckInput') && $('personalWhatsappProtoCheckInput').value).split(/[,\n\s]+/).filter(Boolean);
    if (!raw.length) { setProtoState('先填写要校验的号码', true); return Promise.resolve(); }
    setProtoState('正在校验 ' + raw.length + ' 个号码…', false);
    return request('/api/native-whatsapp/web/check', { method: 'POST', json: { phones: raw } }).then(function (data) {
      var host = $('personalWhatsappProtoCheckResult');
      var rows = Array.isArray(data.items) ? data.items : [];
      if (host) host.innerHTML = protoRows(rows, function (row) {
        return '<div class="pwa-record"><div class="pwa-item-title">' + esc(text(row.input)) + ' <span class="pwa-badge">' + (row.exists ? '已注册' : '没有') + '</span></div><div class="pwa-meta">' + esc(text(row.jid || row.error || '')) + '</div></div>';
      }, '没有结果');
      setProtoState(data.message || '校验完成', false);
      return data;
    }).catch(function (err) { setProtoState(err.message || '校验失败', true); });
  }

  function runProtoProfile() {
    var jid = text($('personalWhatsappProtoProfileInput') && $('personalWhatsappProtoProfileInput').value).trim();
    if (!jid) { setProtoState('先填写 jid 或手机号', true); return Promise.resolve(); }
    setProtoState('正在读资料…', false);
    return request('/api/native-whatsapp/web/profile?jid=' + encodeURIComponent(jid)).then(function (data) {
      var host = $('personalWhatsappProtoProfileResult');
      if (host) host.innerHTML = '<div class="pwa-record"><div class="pwa-item-title">' + esc(text(data.jid)) + '</div>'
        + '<div class="pwa-meta">签名：' + esc(text(data.about) || '（没设签名或不可见）') + '</div>'
        + '<div class="pwa-meta">头像：' + esc(text(data.picture) || '（没头像或不可见）') + '</div></div>'
        + (data.picture ? ('<div style="margin-top:6px;"><img src="' + esc(data.picture) + '" alt="头像" style="width:64px;height:64px;border-radius:50%;background:#fff;"></div>') : '');
      setProtoState('资料已读取', false);
      return data;
    }).catch(function (err) { setProtoState(err.message || '读取资料失败', true); });
  }

  function protoSendFields() {
    return {
      account_id: ACCOUNT_ID,
      min_gap: numberField('personalWhatsappProtoSendMinGap', 12, 3, 600),
      max_gap: numberField('personalWhatsappProtoSendMaxGap', 28, 3, 900),
      pause_every: numberField('personalWhatsappProtoSendPauseEvery', 3, 1, 100),
      pause_seconds: numberField('personalWhatsappProtoSendPauseSeconds', 120, 0, 3600),
      daily_limit: numberField('personalWhatsappProtoSendDailyLimit', 80, 0, 1000)
    };
  }

  function fillProtoSendControl(control) {
    control = control || {};
    var map = {
      personalWhatsappProtoSendMinGap: control.min_gap,
      personalWhatsappProtoSendMaxGap: control.max_gap,
      personalWhatsappProtoSendPauseEvery: control.pause_every,
      personalWhatsappProtoSendPauseSeconds: control.pause_seconds,
      personalWhatsappProtoSendDailyLimit: control.daily_limit
    };
    Object.keys(map).forEach(function (id) { var node = $(id); if (node && map[id] != null) node.value = map[id]; });
  }

  function renderProtoSendState() {
    var node = $('personalWhatsappProtoSendState');
    var summary = proto.summary || {};
    if (!node) return;
    node.textContent = (summary.enabled ? '队列运行中' : '队列已停止')
      + ' · 今日 ' + Number(summary.sent_today || 0) + '/' + Number(summary.daily_limit || 0)
      + ' · 排队 ' + Number(summary.queued || 0)
      + (summary.next_action_at ? (' · 下一条 ' + text(summary.next_action_at).slice(11, 19)) : '')
      + (summary.last_error ? (' · ' + summary.last_error) : '');
    node.style.color = summary.last_error ? '#c0392b' : '';
  }

  function loadProtoSendPanel() {
    return request('/api/native-whatsapp/web/send/queue?limit=20').then(function (data) {
      proto.control = data.control || {};
      proto.summary = data.summary || {};
      proto.records = Array.isArray(data.items) ? data.items : [];
      fillProtoSendControl(proto.control);
      renderProtoSendState();
      var host = $('personalWhatsappProtoSendSummary');
      if (host) {
        host.innerHTML = statusCard('今日已发', Number(proto.summary.sent_today || 0) + ' / ' + Number(proto.summary.daily_limit || 0), proto.summary.sent_today >= proto.summary.daily_limit && proto.summary.daily_limit ? 'bad' : '')
          + statusCard('排队', Number(proto.summary.queued || 0), '')
          + statusCard('成功', Number(proto.summary.success || 0), 'good')
          + statusCard('失败', Number(proto.summary.failed || 0), proto.summary.failed ? 'bad' : '');
      }
      var list = $('personalWhatsappProtoSendRecords');
      if (list) list.innerHTML = protoRows(proto.records, function (row) {
        var targets = Array.isArray(row.targets) ? row.targets : [];
        var first = targets[0] || {};
        var jid = typeof first === 'string' ? first : text(first.jid);
        return '<div class="pwa-record ' + (row.status === 'failed' ? 'failed' : '') + '"><div class="pwa-item-title">' + esc(jid || '发送') + ' <span class="pwa-badge">' + esc(friendStatusText(row.status)) + '</span></div><div class="pwa-meta">' + esc(text((row.payload || {}).text || '').slice(0, 80)) + '</div><div class="pwa-meta">' + esc(row.error_message || '') + ' ' + esc(row.updated_at || '') + '</div></div>';
      }, '暂无发送记录');
      return data;
    });
  }

  function saveProtoSendControl() {
    setProtoState('正在保存限速参数…', false);
    return request('/api/native-whatsapp/web/send/config', { method: 'POST', json: protoSendFields() }).then(function (data) {
      proto.control = data.control || {};
      proto.summary = data.summary || proto.summary;
      fillProtoSendControl(proto.control); renderProtoSendState();
      toastMessage('发送限速已保存');
      setProtoState('限速已保存：单条 ' + proto.control.min_gap + '–' + proto.control.max_gap + 's，每 ' + proto.control.pause_every + ' 条停 ' + proto.control.pause_seconds + 's，日上限 ' + proto.control.daily_limit, false);
      return data;
    }).catch(function (err) { setProtoState(err.message || '保存失败', true); });
  }

  function submitProtoSend() {
    var content = text($('personalWhatsappProtoSendText') && $('personalWhatsappProtoSendText').value).trim();
    if (!proto.target) { setProtoState('先在会话列表里选一个发送目标（只发已有会话，陌生号会被拒）', true); return Promise.resolve(); }
    if (!content) { setProtoState('发送内容不能为空', true); return Promise.resolve(); }
    var autoStart = !!($('personalWhatsappProtoSendAutoStart') && $('personalWhatsappProtoSendAutoStart').checked);
    var body = protoSendFields();
    body.targets = [proto.target];
    body.text = content;
    body.auto_start = autoStart;
    setProtoState('正在入队…', false);
    return request('/api/native-whatsapp/web/send', { method: 'POST', json: body }).then(function (data) {
      var skipped = Array.isArray(data.skipped) ? data.skipped : [];
      if (data.queued) {
        toastMessage('已入队 ' + data.queued + ' 条');
        setProtoState('已入队 ' + data.queued + ' 条' + (skipped.length ? ('，跳过 ' + skipped.length + ' 条：' + text(skipped[0].reason)) : ''), false);
      } else {
        setProtoState('没有入队：' + (skipped.length ? text(skipped[0].reason) : '内容为空') + '（发送前请把限速和队列确认好）', true);
      }
      proto.control = data.control || proto.control;
      proto.summary = data.summary || proto.summary;
      renderProtoSendState();
      return loadProtoSendPanel();
    }).catch(function (err) { setProtoState(err.message || '入队失败', true); });
  }

  function toggleProtoSendQueue(start) {
    var path = start ? '/api/native-whatsapp/web/send/start' : '/api/native-whatsapp/web/send/stop';
    setProtoState(start ? '正在启动发送队列…' : '正在停止发送队列…', false);
    return request(path, { method: 'POST', json: { account_id: ACCOUNT_ID } }).then(function (data) {
      proto.control = data.control || proto.control;
      proto.summary = data.summary || proto.summary;
      renderProtoSendState();
      toastMessage(start ? '发送队列已启动（按限速慢慢发）' : '发送队列已停止');
      setProtoState(start ? '队列已启动：按限速逐条发送，连续失败会自动停' : '队列已停止', false);
      return loadProtoSendPanel();
    }).catch(function (err) { setProtoState(err.message || '操作失败', true); });
  }

  function loadProtocolLog() {
    return request('/api/native-whatsapp/web/protocol/log?limit=50').then(function (data) {
      proto.log = Array.isArray(data.items) ? data.items : [];
      var host = $('personalWhatsappProtoLog');
      if (host) host.innerHTML = protoRows(proto.log, function (row) {
        var detail = [];
        Object.keys(row || {}).forEach(function (key) {
          if (key === 'at' || key === 'event' || key === 'source') return;
          var value = row[key];
          if (value && typeof value === 'object') detail.push(key + '=' + JSON.stringify(value).slice(0, 90));
          else detail.push(key + '=' + text(value));
        });
        return '<div class="pwa-record"><div class="pwa-item-title">' + esc(row.event || '事件') + ' <span class="pwa-badge">' + esc(text(row.at || '').slice(11, 19)) + '</span></div><div class="pwa-meta">' + esc(detail.join(' · ')) + '</div></div>';
      }, '暂无协议日志（点刷新，或先扫码绑定一次）');
      return data;
    });
  }

  window.initPersonalWhatsappView=function(){bind();loadProtocolPanel().catch(function(){});loadMemoryDocs().catch(function(){});loadBase().then(function(){renderTakeoverSummary();}).catch(function(error){showError(error.message||'WhatsApp 状态读取失败');});loadFriendQueue().catch(function(){});loadAutoReply().catch(function(){});};
})();
