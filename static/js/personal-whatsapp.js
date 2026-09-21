(function() {
  'use strict';

  var ACCOUNT_ID = 'desktop-whatsapp-default';
  var state = {
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
    var ids = ['personalWhatsappRefreshBtn','personalWhatsappSyncAllBtn','personalWhatsappSyncSessionsBtn','personalWhatsappSyncGroupsBtn','personalWhatsappOpenSessionBtn','personalWhatsappSendBtn','personalWhatsappSyncContactsBtn','personalWhatsappAddContactBtn','personalWhatsappAddFriendBtn','personalWhatsappSaveBtn','personalWhatsappRunBtn','personalWhatsappRefreshRecordsBtn','personalWhatsappAddFriendSubmitBtn','personalWhatsappContactSubmitBtn','personalWhatsappFriendQueueStartBtn','personalWhatsappFriendQueueStopBtn','personalWhatsappFriendQueueSettingsBtn','personalWhatsappFriendSettingsSaveBtn','personalWhatsappTakeoverSettingsBtn','personalWhatsappFriendImportBtn','personalWhatsappLoopStartBtn','personalWhatsappDiagnosticsBtn'];
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
    host.innerHTML = statusCard('桌面客户端', s.desktop_found ? '已检测' : '未检测', s.desktop_found ? 'good' : 'bad') + statusCard('登录状态', s.logged_in ? '已登录' : '未登录', s.logged_in ? 'good' : 'bad') + statusCard('UIA 控件', s.ok ? '可用' : '不可用', s.ok ? 'good' : 'bad') + statusCard('未读会话', Number(s.unread_count || 0), s.unread_count ? 'good' : '') + statusCard('当前操作', s.running ? (s.active_action || '执行中') : '空闲', s.running ? '' : 'good');
  }
  function fillConfig() {
    var c = state.config || {};
    if ($('personalWhatsappAccountId')) $('personalWhatsappAccountId').value = ACCOUNT_ID;
    if ($('personalWhatsappInterval')) $('personalWhatsappInterval').value = Math.max(1, Math.min(300, Number(c.interval_seconds || 15)));
    if ($('personalWhatsappTakeoverMinutes')) $('personalWhatsappTakeoverMinutes').value = Math.max(1, Math.min(1440, Number(c.takeover_session_minutes || 30)));
    if ($('personalWhatsappMaxUnread')) $('personalWhatsappMaxUnread').value = Math.max(1, Math.min(100, Number(c.max_unread_per_round || 50)));
    if ($('personalWhatsappInstruction')) $('personalWhatsappInstruction').value = text(c.reply_instruction || '');
  }
  function configFromFields() { return { interval_seconds:numberField('personalWhatsappInterval',15,1,300), takeover_session_minutes:numberField('personalWhatsappTakeoverMinutes',30,1,1440), max_unread_per_round:numberField('personalWhatsappMaxUnread',50,1,100), reply_instruction:text($('personalWhatsappInstruction') && $('personalWhatsappInstruction').value).trim().slice(0,4000) }; }
  function reasonLabel(value) { return ({duplicate_in_round:'本轮重复',group_chat:'群聊',no_replyable_text:'无可回复文字',last_message_not_inbound:'最后消息不是对方发送'}[value] || value || '跳过'); }
  function renderLastRun() {
    var run = state.lastRun || {}, summary = $('personalWhatsappLastRun'), host = $('personalWhatsappItems'); if (!summary || !host) return;
    if (!run.started_at && !run.finished_at) { summary.textContent = '暂无执行记录'; host.innerHTML = ''; return; }
    summary.textContent = (run.summary_text || '已完成一轮') + (run.finished_at ? ' · ' + run.finished_at : '') + (run.stop_reason ? ' · 已停止' : '');
    var items = Array.isArray(run.items) ? run.items : [];
    host.innerHTML = items.length ? items.map(function(item) { var good = item.status === 'replied'; return '<div class="pwa-record ' + (item.status === 'failed' ? 'failed' : '') + '"><div class="pwa-item-title">' + esc(item.peer_name || '未命名会话') + ' <span class="pwa-badge">' + esc(good ? '已回复' : item.status === 'failed' ? '失败' : '跳过') + '</span></div><div class="pwa-meta">' + esc(item.reply || item.error || reasonLabel(item.reason)) + '</div></div>'; }).join('') : '<div class="pwa-empty">本轮没有会话明细</div>';
  }
  function loadBase() {
    return Promise.all([request('/api/native-whatsapp/status'), request('/api/native-whatsapp/config')]).then(function(values) { state.status = values[0] || {}; state.config = values[1] && values[1].config || state.status.config || {}; state.lastRun = state.config.last_run || {}; renderStatus(); fillConfig(); renderLastRun(); if (!state.status.ok && state.status.reason) showError(state.status.reason); });
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
  function loadGroups() { var meta=state.pages.groups;return request('/api/native-whatsapp/sessions?'+query(meta,{chat_type:'group'})).then(function(data){state.groups=Array.isArray(data.items)?data.items:[];meta.total=Number(data.total||0);var host=$('personalWhatsappGroupList');if(host)host.innerHTML=state.groups.length?state.groups.map(function(row){return '<div class="pwa-item" data-pwa-group-peer="'+esc(row.peer_key)+'"><div class="pwa-item-title">'+esc(row.display_name||'未命名群聊')+' <span class="pwa-badge">群聊</span></div><div class="pwa-meta">'+esc(row.last_message||'尚未同步消息')+'</div></div>';}).join(''):'<div class="pwa-empty">暂无已识别群聊；打开群会话同步后会归入这里</div>';renderPagination('personalWhatsappGroupPagination','groups');}); }
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
  function renderContacts() { var host=$('personalWhatsappContactList');if(!host)return;host.innerHTML=state.contacts.length?state.contacts.map(function(row){var target=row.username||row.phone||row.display_name||'';return '<div class="pwa-item"><div class="pwa-item-title">'+esc(row.display_name||'未命名')+'</div><div class="pwa-meta">'+esc(row.username?('@'+row.username):'')+(row.username&&row.phone?' · ':'')+esc(row.phone||'')+'</div><div class="pwa-contact-actions"><button class="btn btn-primary btn-sm" data-pwa-contact-chat="'+esc(target)+'">打开会话</button></div></div>';}).join(''):'<div class="pwa-empty">暂无联系人，请先同步通讯录</div>';renderPagination('personalWhatsappContactPagination','contacts'); }
  function loadRecords(){var meta=state.pages.records;return request('/api/native-whatsapp/operations?'+query(meta)).then(function(data){state.records=Array.isArray(data.items)?data.items:[];meta.total=Number(data.total||0);renderRecords();});}
  function renderRecords(){var host=$('personalWhatsappRecordList');if(!host)return;host.innerHTML=state.records.length?state.records.map(function(row){return '<div class="pwa-record '+(row.status==='failed'?'failed':'')+'"><div class="pwa-item-title">'+esc(row.action||'操作')+' · '+esc(row.target||'无目标')+' <span class="pwa-badge">'+esc(row.status==='success'?'成功':'失败')+'</span></div><div class="pwa-meta">'+esc(row.message||'')+'</div><div class="pwa-meta">'+esc(row.created_at||'')+'</div></div>';}).join(''):'<div class="pwa-empty">暂无操作记录</div>';renderPagination('personalWhatsappRecordPagination','records');}

  function activateTab(tab) {
    state.tab = tab;
    document.querySelectorAll('#content-personal-whatsapp .pwa-tab').forEach(function(node){node.classList.toggle('active',node.dataset.pwaTab===tab);});
    document.querySelectorAll('#content-personal-whatsapp .pwa-panel').forEach(function(node){node.classList.toggle('active',node.dataset.pwaPanel===tab);});
    if(tab==='sessions')loadSessions().catch(function(e){showError(e.message);});
    if(tab==='groups')loadGroups().catch(function(e){showError(e.message);});
    if(tab==='contacts')loadContacts().catch(function(e){showError(e.message);});
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
    $('personalWhatsappSyncAllBtn').addEventListener('click',function(){runBusy('正在同步会话...',function(){var sessionResult=null,contactResult=null;return request('/api/native-whatsapp/sessions/sync',{method:'POST',json:{account_id:ACCOUNT_ID,limit:1000,max_scrolls:30}}).then(function(data){sessionResult=data||{};showError('会话同步完成，正在同步通讯录...',true);return request('/api/native-whatsapp/contacts/sync',{method:'POST',json:{account_id:ACCOUNT_ID,limit:2000,max_scrolls:50}});}).then(function(data){contactResult=data||{};return Promise.all([loadBase(),loadSessions(),loadContacts(),loadRecords()]);}).then(function(){var summary=[sessionResult&&sessionResult.message,contactResult&&contactResult.message].filter(Boolean).join('；')||'会话和通讯录同步完成';showNotice(summary);toastMessage(summary);return {sessions:sessionResult,contacts:contactResult};});}).catch(function(){});});
    $('personalWhatsappSyncSessionsBtn').addEventListener('click',function(){runBusy('正在读取桌面会话...',function(){return request('/api/native-whatsapp/sessions/sync',{method:'POST',json:{account_id:ACCOUNT_ID,limit:1000,max_scrolls:30}}).then(function(data){toastMessage(data.message||'会话同步完成');return Promise.all([loadSessions(),loadRecords(),loadBase()]);});}).catch(function(){});});
    $('personalWhatsappSyncGroupsBtn').addEventListener('click',function(){runBusy('正在读取桌面会话...',function(){return request('/api/native-whatsapp/sessions/sync',{method:'POST',json:{account_id:ACCOUNT_ID,limit:1000,max_scrolls:30}}).then(function(data){toastMessage(data.message||'会话同步完成');return Promise.all([loadGroups(),loadRecords(),loadBase()]);});}).catch(function(){});});
    $('personalWhatsappOpenSessionBtn').addEventListener('click',function(){if(!state.activeSession)return;runBusy('正在打开并同步会话...',function(){return request('/api/native-whatsapp/conversations/open',{method:'POST',json:{account_id:ACCOUNT_ID,target:state.activeSession.display_name}}).then(function(data){var peer=data&&data.peer||{};if(peer.peer_key)state.activeSession=peer;state.messages=Array.isArray(data&&data.messages)?data.messages.map(function(item,index){return {id:'live_'+index,direction:item.direction,content:item.text};}):[];renderMessages();return Promise.all([loadSessions(),loadRecords(),loadBase()]);});}).catch(function(){});});
    $('personalWhatsappSendBtn').addEventListener('click',function(){if(!state.activeSession)return;var content=text($('personalWhatsappSendContent').value).trim();if(!content){showError('请输入发送内容');return;}runBusy('正在发送并确认...',function(){$('personalWhatsappSendState').textContent='发送中';return request('/api/native-whatsapp/messages/send',{method:'POST',json:{account_id:ACCOUNT_ID,target:state.activeSession.display_name,content:content}}).then(function(data){if(data&&data.peer&&data.peer.peer_key)state.activeSession=data.peer;$('personalWhatsappSendContent').value='';$('personalWhatsappSendState').textContent='发送成功';toastMessage('WhatsApp 消息发送成功');return Promise.all([refreshActiveMessages(),loadSessions(),loadRecords(),loadBase()]);});}).catch(function(){$('personalWhatsappSendState').textContent='发送失败';});});
    $('personalWhatsappSyncContactsBtn').addEventListener('click',function(){runBusy('正在读取 WhatsApp 通讯录...',function(){return request('/api/native-whatsapp/contacts/sync',{method:'POST',json:{account_id:ACCOUNT_ID,limit:2000,max_scrolls:50}}).then(function(data){toastMessage(data.message||'通讯录同步完成');return Promise.all([loadContacts(),loadRecords(),loadBase()]);});}).catch(function(){});});
    $('personalWhatsappRunBtn').addEventListener('click',function(){runBusy('正在执行一轮 WhatsApp 接管...',function(){return saveConfig(false).then(function(){return request('/api/native-whatsapp/run-once',{method:'POST',json:{account_id:ACCOUNT_ID,config_override:configFromFields()}});}).then(function(result){state.lastRun=result||{};renderLastRun();toastMessage(result&&result.skipped?'已有 WhatsApp 操作正在执行':'WhatsApp 本轮执行完成');return Promise.all([loadBase(),loadSessions(),loadRecords()]);});}).catch(function(){});});
    $('personalWhatsappStopBtn').addEventListener('click',function(){request('/api/native-whatsapp/stop',{method:'POST',json:{account_id:ACCOUNT_ID}}).then(function(result){toastMessage(result.running?'已请求停止当前接管':'当前没有接管任务');return loadBase();}).catch(function(e){showError(e.message);});});
    $('personalWhatsappRefreshRecordsBtn').addEventListener('click',function(){loadRecords().catch(function(e){showError(e.message);});});
    $('personalWhatsappAddFriendBtn').addEventListener('click',function(){openFriendAddModal();});
    $('personalWhatsappAddContactBtn').addEventListener('click',function(){openModal('personalWhatsappContactModal');});
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
    $('personalWhatsappContactSearch').addEventListener('input',debounce(function(){state.pages.contacts.keyword=text($('personalWhatsappContactSearch').value).trim();state.pages.contacts.page=1;loadContacts().catch(function(e){showError(e.message);});},300));
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
    });
  }
  function renderFriendRecords() {
    var host = $('personalWhatsappFriendRecordList'); if (!host) return;
    if (!state.friendRecords.length) { host.className = 'pwa-empty'; host.textContent = '暂无加好友记录'; renderPagination('personalWhatsappFriendRecordPagination','friendRecords'); return; }
    host.className = 'pwa-table-wrap';
    host.innerHTML = '<table class="pwa-table"><thead><tr><th>姓名</th><th>号码 / @用户名</th><th>状态</th><th>验证消息</th><th>时间</th></tr></thead><tbody>' + state.friendRecords.map(function(item) {
      var detail = [item.updated_at || item.created_at || '', item.error_message || ''].filter(Boolean).join(' · ');
      var nameText = [item.first_name || '', item.last_name || ''].filter(Boolean).join(' ') || '-';
      var contactText = item.phone ? item.phone : (item.username ? '@' + item.username : '-');
      return '<tr><td>' + esc(nameText) + '</td><td>' + esc(contactText) + '</td><td><span class="pwa-chip' + friendStatusClass(item.status) + '">' + esc(friendStatusText(item.status)) + '</span></td><td>' + esc(item.apply_message || '-') + '</td><td>' + esc(detail || '-') + '</td></tr>';
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
      toastMessage('已导入 ' + lines.length + ' 行目标，请核对后提交');
      return lines.length;
    });
  }
  function downloadFriendTemplate() {
    var content = ['# 一行一个目标：电话 / 名字,电话 / @用户名', '张三,13800138000', '+8613800138001', '@alice_wa', ''].join('\r\n');
    var url = URL.createObjectURL(new Blob([content], { type: 'text/plain;charset=utf-8' }));
    var link = document.createElement('a');
    link.href = url; link.download = 'whatsapp-friend-targets.txt';
    document.body.appendChild(link); link.click(); document.body.removeChild(link);
    setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
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
  window.initPersonalWhatsappView=function(){bind();loadBase().then(function(){renderTakeoverSummary();}).catch(function(error){showError(error.message||'WhatsApp 状态读取失败');});loadFriendQueue().catch(function(){});loadAutoReply().catch(function(){});};
})();
