(function() {
  'use strict';

  var ACCOUNT_ID = 'desktop-whatsapp-default';
  var state = {
    tab: 'overview', busy: false, status: {}, config: {}, lastRun: {},
    sessions: [], groups: [], contacts: [], records: [], activeSession: null, messages: [],
    pages: {
      sessions: { page: 1, limit: 30, total: 0, keyword: '', chatType: '' },
      groups: { page: 1, limit: 50, total: 0, keyword: '', chatType: 'group' },
      contacts: { page: 1, limit: 50, total: 0, keyword: '' },
      records: { page: 1, limit: 50, total: 0, keyword: '' }
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
  function showError(message, ok) { var box = $('personalWhatsappError'); if (!box) return; box.textContent = message || ''; box.style.display = message ? 'block' : 'none'; box.classList.toggle('err', !!message && !ok); }
  function setBusy(value, label) {
    state.busy = !!value;
    var ids = ['personalWhatsappRefreshBtn','personalWhatsappSyncAllBtn','personalWhatsappSyncSessionsBtn','personalWhatsappSyncGroupsBtn','personalWhatsappOpenSessionBtn','personalWhatsappSendBtn','personalWhatsappSyncContactsBtn','personalWhatsappAddContactBtn','personalWhatsappSaveBtn','personalWhatsappRunBtn','personalWhatsappRefreshRecordsBtn'];
    ids.forEach(function(id) { var node = $(id); if (node) node.disabled = state.busy || ((id === 'personalWhatsappOpenSessionBtn' || id === 'personalWhatsappSendBtn') && !state.activeSession); });
    var stop = $('personalWhatsappStopBtn'); if (stop) stop.disabled = false;
    if (value && label) showError(label, true);
  }
  function runBusy(label, promiseFactory) {
    if (state.busy) return Promise.resolve();
    showError(''); setBusy(true, label);
    return Promise.resolve().then(promiseFactory).catch(function(error) { showError(error.message || '操作失败'); throw error; }).finally(function() { setBusy(false); });
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
  }
  function saveConfig(showToast){return request('/api/native-whatsapp/config',{method:'POST',json:configFromFields()}).then(function(data){state.config=data.config||configFromFields();fillConfig();if(showToast!==false)toastMessage('WhatsApp 参数已保存');return state.config;});}
  function refreshActiveMessages(){if(!state.activeSession)return Promise.resolve();return request('/api/native-whatsapp/sessions/'+encodeURIComponent(state.activeSession.peer_key)+'/messages?limit=200&offset=0').then(function(data){state.messages=Array.isArray(data.items)?data.items:[];renderMessages();});}

  function bind() {
    var root=$('content-personal-whatsapp');if(!root||root.dataset.personalWhatsappBound==='1')return;root.dataset.personalWhatsappBound='1';
    root.addEventListener('click',function(event){var tab=event.target.closest('[data-pwa-tab]');if(tab){activateTab(tab.dataset.pwaTab);return;}var peer=event.target.closest('[data-pwa-peer]');if(peer){selectSession(peer.dataset.pwaPeer);return;}var groupPeer=event.target.closest('[data-pwa-group-peer]');if(groupPeer){var group=state.groups.filter(function(row){return row.peer_key===groupPeer.dataset.pwaGroupPeer;})[0];activateTab('sessions');if(group)selectContactTarget(group.display_name,'group');return;}var page=event.target.closest('[data-pwa-page]');if(page){var meta=state.pages[page.dataset.pwaPage],dir=Number(page.dataset.pwaDir||0);meta.page=Math.max(1,Math.min(pageCount(meta),meta.page+dir));({sessions:loadSessions,groups:loadGroups,contacts:loadContacts,records:loadRecords}[page.dataset.pwaPage]||function(){return Promise.resolve();})().catch(function(e){showError(e.message);});return;}var contact=event.target.closest('[data-pwa-contact-chat]');if(contact){activateTab('sessions');selectContactTarget(contact.dataset.pwaContactChat);}});
    $('personalWhatsappRefreshBtn').addEventListener('click',function(){runBusy('正在刷新状态...',loadBase).catch(function(){});});
    $('personalWhatsappSyncAllBtn').addEventListener('click',function(){runBusy('正在同步会话...',function(){return request('/api/native-whatsapp/sessions/sync',{method:'POST',json:{account_id:ACCOUNT_ID,limit:1000,max_scrolls:30}}).then(function(){showError('会话同步完成，正在同步通讯录...',true);return request('/api/native-whatsapp/contacts/sync',{method:'POST',json:{account_id:ACCOUNT_ID,limit:2000,max_scrolls:50}});}).then(function(){toastMessage('会话和通讯录同步完成');return Promise.all([loadBase(),loadSessions(),loadContacts(),loadRecords()]);});}).catch(function(){});});
    $('personalWhatsappSyncSessionsBtn').addEventListener('click',function(){runBusy('正在读取桌面会话...',function(){return request('/api/native-whatsapp/sessions/sync',{method:'POST',json:{account_id:ACCOUNT_ID,limit:1000,max_scrolls:30}}).then(function(data){toastMessage(data.message||'会话同步完成');return Promise.all([loadSessions(),loadRecords(),loadBase()]);});}).catch(function(){});});
    $('personalWhatsappSyncGroupsBtn').addEventListener('click',function(){runBusy('正在读取桌面会话...',function(){return request('/api/native-whatsapp/sessions/sync',{method:'POST',json:{account_id:ACCOUNT_ID,limit:1000,max_scrolls:30}}).then(function(data){toastMessage(data.message||'会话同步完成');return Promise.all([loadGroups(),loadRecords(),loadBase()]);});}).catch(function(){});});
    $('personalWhatsappOpenSessionBtn').addEventListener('click',function(){if(!state.activeSession)return;runBusy('正在打开并同步会话...',function(){return request('/api/native-whatsapp/conversations/open',{method:'POST',json:{account_id:ACCOUNT_ID,target:state.activeSession.display_name}}).then(function(data){var peer=data&&data.peer||{};if(peer.peer_key)state.activeSession=peer;state.messages=Array.isArray(data&&data.messages)?data.messages.map(function(item,index){return {id:'live_'+index,direction:item.direction,content:item.text};}):[];renderMessages();return Promise.all([loadSessions(),loadRecords(),loadBase()]);});}).catch(function(){});});
    $('personalWhatsappSendBtn').addEventListener('click',function(){if(!state.activeSession)return;var content=text($('personalWhatsappSendContent').value).trim();if(!content){showError('请输入发送内容');return;}runBusy('正在发送并确认...',function(){$('personalWhatsappSendState').textContent='发送中';return request('/api/native-whatsapp/messages/send',{method:'POST',json:{account_id:ACCOUNT_ID,target:state.activeSession.display_name,content:content}}).then(function(data){if(data&&data.peer&&data.peer.peer_key)state.activeSession=data.peer;$('personalWhatsappSendContent').value='';$('personalWhatsappSendState').textContent='发送成功';toastMessage('WhatsApp 消息发送成功');return Promise.all([refreshActiveMessages(),loadSessions(),loadRecords(),loadBase()]);});}).catch(function(){$('personalWhatsappSendState').textContent='发送失败';});});
    $('personalWhatsappSyncContactsBtn').addEventListener('click',function(){runBusy('正在读取 WhatsApp 通讯录...',function(){return request('/api/native-whatsapp/contacts/sync',{method:'POST',json:{account_id:ACCOUNT_ID,limit:2000,max_scrolls:50}}).then(function(data){toastMessage(data.message||'通讯录同步完成');return Promise.all([loadContacts(),loadRecords(),loadBase()]);});}).catch(function(){});});
    $('personalWhatsappAddContactBtn').addEventListener('click',function(){var body={account_id:ACCOUNT_ID,first_name:text($('personalWhatsappContactFirstName').value).trim(),last_name:text($('personalWhatsappContactLastName').value).trim(),username:text($('personalWhatsappContactUsername').value).trim(),phone:text($('personalWhatsappContactPhone').value).trim(),country_code:$('personalWhatsappCountryCode').value};if(!body.first_name||(!body.username&&!body.phone)){showError('请填写名字，并填写 @用户名或手机号');return;}runBusy('正在填写桌面联系人表单...',function(){$('personalWhatsappAddContactState').textContent='提交中';return request('/api/native-whatsapp/contacts',{method:'POST',json:body}).then(function(data){$('personalWhatsappAddContactState').textContent='保存成功';toastMessage(data.message||'联系人已保存');['personalWhatsappContactFirstName','personalWhatsappContactLastName','personalWhatsappContactUsername','personalWhatsappContactPhone'].forEach(function(id){$(id).value='';});return Promise.all([loadContacts(),loadRecords(),loadBase()]);});}).catch(function(){$('personalWhatsappAddContactState').textContent='保存失败';});});
    $('personalWhatsappSaveBtn').addEventListener('click',function(){runBusy('正在保存参数...',function(){return saveConfig(true);}).catch(function(){});});
    $('personalWhatsappRunBtn').addEventListener('click',function(){runBusy('正在执行一轮 WhatsApp 接管...',function(){return saveConfig(false).then(function(){return request('/api/native-whatsapp/run-once',{method:'POST',json:{account_id:ACCOUNT_ID,config_override:configFromFields()}});}).then(function(result){state.lastRun=result||{};renderLastRun();toastMessage(result&&result.skipped?'已有 WhatsApp 操作正在执行':'WhatsApp 本轮执行完成');return Promise.all([loadBase(),loadSessions(),loadRecords()]);});}).catch(function(){});});
    $('personalWhatsappStopBtn').addEventListener('click',function(){request('/api/native-whatsapp/stop',{method:'POST',json:{account_id:ACCOUNT_ID}}).then(function(result){toastMessage(result.running?'已请求停止当前接管':'当前没有接管任务');return loadBase();}).catch(function(e){showError(e.message);});});
    $('personalWhatsappRefreshRecordsBtn').addEventListener('click',function(){loadRecords().catch(function(e){showError(e.message);});});
    $('personalWhatsappSessionSearch').addEventListener('input',debounce(function(){state.pages.sessions.keyword=text($('personalWhatsappSessionSearch').value).trim();state.pages.sessions.page=1;loadSessions().catch(function(e){showError(e.message);});},300));
    $('personalWhatsappSessionType').addEventListener('change',function(){state.pages.sessions.chatType=this.value;state.pages.sessions.page=1;loadSessions().catch(function(e){showError(e.message);});});
    $('personalWhatsappContactSearch').addEventListener('input',debounce(function(){state.pages.contacts.keyword=text($('personalWhatsappContactSearch').value).trim();state.pages.contacts.page=1;loadContacts().catch(function(e){showError(e.message);});},300));
    $('personalWhatsappRecordSearch').addEventListener('input',debounce(function(){state.pages.records.keyword=text($('personalWhatsappRecordSearch').value).trim();state.pages.records.page=1;loadRecords().catch(function(e){showError(e.message);});},300));
  }

  window.initPersonalWhatsappView=function(){bind();loadBase().catch(function(error){showError(error.message||'WhatsApp 状态读取失败');});};
})();
