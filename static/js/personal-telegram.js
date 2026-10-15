(function() {
  'use strict';

  var ACCOUNT_ID = 'desktop-telegram-default';
  var STEP_LABEL = {
    idle: '未登录', starting: '启动中', need_phone: '等手机号', need_code: '等验证码',
    need_password: '等 2FA 密码', linked: '已登录', error: '出错', cancelled: '已取消'
  };
  var state = { busy: false, status: {}, config: {}, chats: [], contacts: [], messages: [], protocol: [], records: [], activePeer: '', pollTimer: null };

  function $(id) { return document.getElementById(id); }
  function text(value) { return String(value == null ? '' : value); }
  function esc(value) { return text(value).replace(/[&<>"']/g, function(ch) { return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[ch]; }); }
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
    return fetch(localBase() + path, {
      method: options.method || 'GET',
      headers: headers,
      body: options.json === undefined ? undefined : JSON.stringify(options.json),
      credentials: 'same-origin',
      cache: 'no-store'
    }).then(function(response) {
      return response.json().catch(function() { return {}; }).then(function(data) {
        if (!response.ok) throw new Error(data.detail || data.message || ('请求失败（' + response.status + '）'));
        return data;
      });
    });
  }
  function showError(message, ok) {
    var box = $('personalTelegramError');
    if (!box) return;
    box.textContent = message || '';
    box.style.display = message ? 'block' : 'none';
    box.classList.toggle('err', !!message && !ok);
  }
  function showNotice(message) { showError(message, true); }
  function setBusy(value, label) {
    state.busy = !!value;
    ['personalTelegramRefreshBtn','personalTelegramDepsBtn','personalTelegramLoginStartBtn','personalTelegramCodeBtn',
     'personalTelegramPasswordBtn','personalTelegramLoginCancelBtn','personalTelegramLogoutBtn','personalTelegramSaveConfigBtn',
     'personalTelegramLoadConfigBtn','personalTelegramSyncChatsBtn','personalTelegramLoadChatsBtn','personalTelegramSyncContactsBtn',
     'personalTelegramLoadContactsBtn','personalTelegramLoadRecordsBtn','personalTelegramLoadProtocolBtn'].forEach(function(id) {
      var node = $(id);
      if (node) node.disabled = state.busy;
    });
    if (value && label) showError(label, true);
  }
  function runBusy(label, factory) {
    if (state.busy) return Promise.resolve();
    showError(''); setBusy(true, label);
    return Promise.resolve().then(factory).then(function(result) { showError(''); return result; })
      .catch(function(error) { showError(error.message || '操作失败'); })
      .finally(function() { setBusy(false); });
  }
  function numberField(id, fallback, min, max) {
    var value = Number($(id) && $(id).value);
    if (!isFinite(value)) value = fallback;
    return Math.max(min, Math.min(max, Math.round(value)));
  }
  function statusCard(label, value, tone) {
    return '<div class="pwa-stat"><span>' + esc(label) + '</span><strong style="color:' +
      (tone === 'good' ? '#0a7a4a' : tone === 'bad' ? '#b42318' : tone === 'warn' ? '#9a5b06' : '#3f5a66') + '">' +
      esc(value) + '</strong></div>';
  }
  function phaseMessage(data, fallback) {
    if (data && data.error === 'not_implemented') return data.message || fallback || '这一档还没接入';
    return (data && data.message) || fallback || '';
  }

  // ── 状态 ────────────────────────────────────────────────────────────────
  function renderStatus() {
    var s = state.status || {};
    var dep = s.dependency || {};
    var login = s.login || {};
    var counts = s.counts || {};
    var host = $('personalTelegramStatus');
    if (!host) return;
    host.innerHTML = [
      statusCard('登录状态', STEP_LABEL[login.step] || login.step || '未知', s.linked ? 'good' : 'warn'),
      statusCard('telethon', dep.telethon ? '已安装' : '未安装', dep.telethon ? 'good' : 'bad'),
      statusCard('会话', String(counts.chats || 0) + ' 条', ''),
      statusCard('通讯录', String(counts.contacts || 0) + ' 条', ''),
      statusCard('当前阶段', text(s.phase || 'P0') + '（骨架）', '')
    ].join('');
    var stateNode = $('personalTelegramLoginState');
    if (stateNode) stateNode.textContent = (STEP_LABEL[login.step] || login.step || '未登录') + (s.linked ? ' · session 已落盘' : '');
    highlightStep(login.step);
    var me = s.me || {};
    var meHost = $('personalTelegramMeInfo');
    if (meHost) {
      meHost.innerHTML = (me.first_name || me.username || me.id) ?
        ('<span class="pwa-chip">' + esc(me.first_name || '') + ' ' + esc(me.last_name || '') + '</span>' +
         (me.username ? '<span class="pwa-chip">@' + esc(me.username) + '</span>' : '') +
         (me.phone ? '<span class="pwa-chip">' + esc(me.phone) + '</span>' : '')) : '';
    }
    if (login.error) showError('登录出错：' + login.error);
    else if (login.message && login.step !== 'linked') showNotice(login.message);
  }

  function highlightStep(step) {
    var map = { need_phone: 'personalTelegramStepPhone', need_code: 'personalTelegramStepCode', need_password: 'personalTelegramStepPassword' };
    Object.keys(map).forEach(function(key) {
      var node = $(map[key]);
      if (node) node.classList.toggle('active', key === step);
    });
    if (!map[step]) {
      ['personalTelegramStepPhone','personalTelegramStepCode','personalTelegramStepPassword'].forEach(function(id) {
        var node = $(id);
        if (node) node.classList.remove('active');
      });
    }
  }

  function loadStatus() {
    return request('/api/native-telegram/status').then(function(data) {
      state.status = data || {};
      renderStatus();
      renderDeps();
      return data;
    });
  }

  function renderDeps() {
    var dep = (state.status || {}).dependency || {};
    var detail = $('personalTelegramDepsDetail');
    if (detail) {
      detail.textContent = [
        'telethon: ' + (dep.telethon ? '已安装 ' + text(dep.telethon_source) : '未安装'),
        'pyaes: ' + (dep.pyaes ? '已安装' : '未安装'),
        'python: ' + text(dep.python),
        'bridge: ' + text((state.status || {}).transport && state.status.transport.bridge),
        'bridge 存在: ' + (dep.bridge_exists ? '是' : '否'),
        'wheels 目录: ' + text(dep.wheels_dir) + ' ' + (dep.wheels_dir_exists ? '' : '（不存在）'),
        'read_ready: ' + (dep.read_ready ? 'true' : 'false'),
        'session 文件: ' + text((state.status || {}).transport && state.status.transport.session_file)
      ].join('\n');
    }
  }

  // ── 运行依赖体检 / 修复（形状照「视频复刻」那套：一条条列出来 + 一个按钮）────
  function renderRuntimeDeps(data) {
    var status = data || {};
    var deps = status.dependencies || [];
    var host = $('personalTelegramDepsList');
    if (host) {
      host.innerHTML = deps.length ? deps.map(function(item) {
        var chip = item.ok ? '<span class="pwa-chip">已就绪</span>' : '<span class="pwa-chip bad">缺失</span>';
        return '<div class="pwa-record' + (item.ok ? '' : ' failed') + '"><div class="pwa-item-title">' + esc(item.label || item.key) + ' ' + chip + '</div>' +
          '<div class="pwa-meta">' + esc(item.detail || '') + (item.required_for ? ' · 影响：' + esc(item.required_for) : '') + '</div></div>';
      }).join('') : '<div class="pwa-empty">没有可检查项</div>';
    }
    var textNode = $('personalTelegramDepsText');
    if (textNode) textNode.textContent = status.ready ? '依赖就绪（telethon 已可用）' : '有依赖缺失 —— 点「安装 / 修复运行依赖」';
    var install = status.install || {};
    var stateNode = $('personalTelegramInstallState');
    if (stateNode) {
      stateNode.textContent = (install.status && install.status !== 'idle')
        ? ('安装：' + (install.running ? '进行中' : install.status) + (install.stage ? ' · ' + install.stage : '') + (install.error ? ' · ' + install.error : ''))
        : '';
    }
    var logNode = $('personalTelegramInstallLog');
    if (logNode) {
      var lines = install.log || [];
      logNode.style.display = lines.length ? 'block' : 'none';
      logNode.textContent = lines.slice(-40).join('\n');
    }
  }

  function loadRuntimeDeps() {
    return request('/api/native-telegram/runtime/status').then(function(data) {
      state.runtime = data || {};
      renderRuntimeDeps(state.runtime);
      return data;
    });
  }

  function pollInstall() {
    if (state.installTimer) clearTimeout(state.installTimer);
    state.installTimer = setTimeout(function() {
      loadRuntimeDeps().then(function(data) {
        if ((data.install || {}).running) { pollInstall(); return; }
        var ok = (data.install || {}).status === 'completed';
        showNotice(ok ? '依赖已修复完成' : '依赖安装结束（仍有缺失）');
        loadStatus().catch(function() {});
      }).catch(function() {});
    }, 3000);
  }

  function repairDeps() {
    return runBusy('正在修复运行依赖（首次可能需要下载，请勿关闭客户端）…', function() {
      return request('/api/native-telegram/runtime/install', { method: 'POST', json: {} }).then(function(data) {
        renderRuntimeDeps(data || {});
        pollInstall();
        return data;
      });
    });
  }

  // ── 登录 ────────────────────────────────────────────────────────────────
  function startPoll() {
    if (state.pollTimer) return;
    state.pollTimer = setInterval(function() {
      var step = ((state.status || {}).login || {}).step;
      if (['linked','error','cancelled','idle'].indexOf(step) >= 0) { stopPoll(); return; }
      loadStatus().catch(function() {});
    }, 2500);
  }
  function stopPoll() { if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; } }

  function loginStart() {
    var phone = text($('personalTelegramPhone') && $('personalTelegramPhone').value).trim();
    if (!phone) { showError('先填手机号（国际格式，例如 +8613800000000）'); return Promise.resolve(); }
    return runBusy('正在启动登录…', function() {
      return request('/api/native-telegram/login/start', { method: 'POST', json: { phone: phone } }).then(function(data) {
        if (data && data.ok === false) throw new Error(phaseMessage(data, '登录没启动起来'));
        state.status = Object.assign({}, state.status, { login: { step: data.step, message: data.message, error: data.error }, me: data.me || {} });
        renderStatus(); startPoll();
        return data;
      });
    });
  }
  function loginInput(kind, value) {
    if (!value) { showError('要填的内容是空的'); return Promise.resolve(); }
    return runBusy('正在提交…', function() {
      return request('/api/native-telegram/login/input', { method: 'POST', json: { kind: kind, value: value } }).then(function(data) {
        if (data && data.ok === false) throw new Error(phaseMessage(data, '提交失败'));
        return loadStatus();
      });
    });
  }
  function loginCancel() {
    return runBusy('正在取消…', function() {
      stopPoll();
      return request('/api/native-telegram/login/cancel', { method: 'POST', json: {} }).then(function() { return loadStatus(); });
    });
  }
  function logout() {
    return runBusy('正在解除绑定…', function() {
      return request('/api/native-telegram/logout', { method: 'POST', json: {} }).then(function(data) {
        if (data && data.ok === false) throw new Error(phaseMessage(data, '解除绑定没成功'));
        return loadStatus().then(function() { showNotice('已解除绑定'); });
      });
    });
  }

  // ── 配置 ────────────────────────────────────────────────────────────────
  function loadConfig() {
    return request('/api/native-telegram/config').then(function(data) {
      state.config = (data && data.config) || {};
      fillConfig();
      var node = $('personalTelegramConfigState');
      if (node) node.textContent = state.config.api_hash_set ? 'api_hash 已配置' : 'api_hash 未配置';
      return data;
    });
  }
  function fillConfig() {
    var c = state.config || {};
    if ($('personalTelegramApiId')) $('personalTelegramApiId').value = text(c.api_id);
    if ($('personalTelegramApiHash')) $('personalTelegramApiHash').value = '';
    if ($('personalTelegramConfigPhone')) $('personalTelegramConfigPhone').value = text(c.phone);
    if ($('personalTelegramInterval')) $('personalTelegramInterval').value = c.interval_seconds || 15;
    if ($('personalTelegramMaxUnread')) $('personalTelegramMaxUnread').value = c.max_unread_per_round || 50;
    if ($('personalTelegramInstruction')) $('personalTelegramInstruction').value = text(c.reply_instruction);
  }
  function saveConfig() {
    return runBusy('正在保存设置…', function() {
      var hashValue = text($('personalTelegramApiHash') && $('personalTelegramApiHash').value).trim();
      var body = {
        api_id: text($('personalTelegramApiId') && $('personalTelegramApiId').value).trim(),
        phone: text($('personalTelegramConfigPhone') && $('personalTelegramConfigPhone').value).trim(),
        proxy: text($('personalTelegramProxy') && $('personalTelegramProxy').value).trim(),
        interval_seconds: numberField('personalTelegramInterval', 15, 1, 300),
        max_unread_per_round: numberField('personalTelegramMaxUnread', 50, 1, 100),
        reply_instruction: text($('personalTelegramInstruction') && $('personalTelegramInstruction').value).trim()
      };
      if (hashValue) body.api_hash = hashValue;
      return request('/api/native-telegram/config', { method: 'POST', json: body }).then(function(data) {
        state.config = (data && data.config) || {};
        fillConfig();
        showNotice('设置已保存');
        return loadStatus();
      });
    });
  }

  // ── 会话 / 通讯录 / 记录 ────────────────────────────────────────────────
  function renderChats() {
    var host = $('personalTelegramChatList');
    if (!host) return;
    if (!state.chats.length) { host.innerHTML = '<div class="pwa-empty">还没有同步数据（P2 接入后这里会有会话列表）</div>'; return; }
    host.innerHTML = state.chats.map(function(row) {
      return '<div class="pwa-item' + (row.peer_key === state.activePeer ? ' active' : '') + '" data-ptg-peer="' + esc(row.peer_key) + '">' +
        '<div class="pwa-item-title">' + esc(row.display_name || row.username || row.peer_key) + ' <span class="pwa-badge">' + esc(row.peer_type || 'user') + '</span></div>' +
        '<div class="pwa-meta">' + esc(row.phone || '') + (row.unread ? ' · 未读 ' + esc(row.unread) : '') + '</div></div>';
    }).join('');
  }
  function loadChats() {
    var keyword = text($('personalTelegramChatSearch') && $('personalTelegramChatSearch').value).trim();
    return request('/api/native-telegram/chats?limit=100&keyword=' + encodeURIComponent(keyword)).then(function(data) {
      state.chats = Array.isArray(data.items) ? data.items : [];
      renderChats();
      var node = $('personalTelegramChatState');
      if (node) node.textContent = '共 ' + text(data.total || 0) + ' 条';
      return data;
    });
  }
  function loadMessages(peerKey) {
    state.activePeer = peerKey;
    renderChats();
    return request('/api/native-telegram/chats/' + encodeURIComponent(peerKey) + '/messages?limit=50').then(function(data) {
      var rows = Array.isArray(data.items) ? data.items : [];
      var host = $('personalTelegramMessageList');
      if (!host) return data;
      host.innerHTML = rows.length ? rows.map(function(row) {
        return '<div class="pwa-bubble ' + (row.from_me ? 'outbound' : 'inbound') + '">' + esc(row.content || '') + '</div>';
      }).join('') : '<div class="pwa-muted">这个会话还没有本地消息（P2 接入后才有）</div>';
      return data;
    });
  }
  function renderContacts() {
    var host = $('personalTelegramContactList');
    if (!host) return;
    if (!state.contacts.length) { host.innerHTML = '<div class="pwa-empty">还没有同步数据（P2 接入后这里会有通讯录）</div>'; return; }
    host.innerHTML = state.contacts.map(function(row) {
      return '<div class="pwa-item"><div class="pwa-item-title">' + esc(row.display_name || row.username || row.contact_key) + '</div>' +
        '<div class="pwa-meta">' + esc(row.phone || '无手机号') + (row.username ? ' · @' + esc(row.username) : '') + '</div></div>';
    }).join('');
  }
  function loadContacts() {
    var keyword = text($('personalTelegramContactSearch') && $('personalTelegramContactSearch').value).trim();
    return request('/api/native-telegram/contacts?limit=100&keyword=' + encodeURIComponent(keyword)).then(function(data) {
      state.contacts = Array.isArray(data.items) ? data.items : [];
      renderContacts();
      var node = $('personalTelegramContactState');
      if (node) node.textContent = '共 ' + text(data.total || 0) + ' 条';
      return data;
    });
  }
  function renderRecords() {
    var host = $('personalTelegramRecordList');
    if (!host) return;
    host.innerHTML = state.records.length ? state.records.map(function(row) {
      return '<div class="pwa-record' + (row.status === 'ok' ? '' : ' failed') + '"><div class="pwa-item-title">' +
        esc(row.action) + ' · ' + esc(row.status) + '</div><div class="pwa-meta">' + esc(row.created_at || '') +
        (row.target ? ' · ' + esc(row.target) : '') + (row.message ? ' · ' + esc(row.message) : '') + '</div></div>';
    }).join('') : '<div class="pwa-empty">暂无记录</div>';
  }
  function loadRecords() {
    return request('/api/native-telegram/operations?limit=50').then(function(data) {
      state.records = Array.isArray(data.items) ? data.items : [];
      renderRecords();
      var node = $('personalTelegramRecordState');
      if (node) node.textContent = '共 ' + text(data.total || 0) + ' 条';
      return data;
    });
  }
  function loadProtocol() {
    return request('/api/native-telegram/protocol/log?limit=80').then(function(data) {
      state.protocol = Array.isArray(data.items) ? data.items : [];
      var host = $('personalTelegramProtocolList');
      if (host) {
        host.innerHTML = state.protocol.length ? state.protocol.slice().reverse().map(function(row) {
          return '<div class="pwa-record"><div class="pwa-item-title">' + esc(row.event) + '</div><div class="pwa-meta">' +
            esc(row.at || '') + (row.message ? ' · ' + esc(row.message) : '') + (row.error ? ' · ' + esc(row.error) : '') + '</div></div>';
        }).join('') : '<div class="pwa-empty">暂无日志</div>';
      }
      var node = $('personalTelegramProtocolState');
      if (node) node.textContent = '共 ' + text(data.count || 0) + ' 条';
      var pathNode = $('personalTelegramProtocolPath');
      if (pathNode && data.path) pathNode.textContent = data.path;
      return data;
    });
  }

  // ── 绑定 ────────────────────────────────────────────────────────────────
  function switchTab(tab) {
    document.querySelectorAll('#content-personal-telegram .pwa-tab').forEach(function(node) {
      node.classList.toggle('active', node.dataset.pwaTab === tab);
    });
    document.querySelectorAll('#content-personal-telegram .pwa-panel').forEach(function(node) {
      node.classList.toggle('active', node.dataset.pwaPanel === tab);
    });
  }

  function bind() {
    var root = $('content-personal-telegram');
    if (!root || root.dataset.personalTelegramBound === '1') return;
    root.dataset.personalTelegramBound = '1';

    document.querySelectorAll('#content-personal-telegram .pwa-tab').forEach(function(node) {
      node.addEventListener('click', function() {
        switchTab(node.dataset.pwaTab);
        if (node.dataset.pwaTab === 'chats') loadChats().catch(function() {});
        if (node.dataset.pwaTab === 'contacts') loadContacts().catch(function() {});
    loadRuntimeDeps().catch(function() {});
        if (node.dataset.pwaTab === 'settings') loadConfig().catch(function() {});
        if (node.dataset.pwaTab === 'records') { loadRecords().catch(function() {}); loadProtocol().catch(function() {}); }
      });
    });
    var bindClick = function(id, handler) { var node = $(id); if (node) node.addEventListener('click', handler); };
    bindClick('personalTelegramRefreshBtn', function() { runBusy('正在刷新…', function() { return loadStatus(); }); });
    bindClick('personalTelegramDepsBtn', function() { runBusy('正在检查依赖…', function() { return loadRuntimeDeps(); }); });
    bindClick('personalTelegramRepairDeps', repairDeps);
    bindClick('personalTelegramLoginStartBtn', loginStart);
    bindClick('personalTelegramCodeBtn', function() { loginInput('code', text($('personalTelegramCode') && $('personalTelegramCode').value).trim()); });
    bindClick('personalTelegramPasswordBtn', function() { loginInput('password', text($('personalTelegramPassword') && $('personalTelegramPassword').value)); });
    bindClick('personalTelegramLoginCancelBtn', loginCancel);
    bindClick('personalTelegramLogoutBtn', logout);
    bindClick('personalTelegramSaveConfigBtn', saveConfig);
    bindClick('personalTelegramLoadConfigBtn', function() { runBusy('正在读取设置…', function() { return loadConfig(); }); });
    bindClick('personalTelegramSyncChatsBtn', function() {
      runBusy('正在同步会话…', function() {
        return request('/api/native-telegram/sync/chats', { method: 'POST', json: {} }).then(function(data) {
          if (data && data.error === 'not_implemented') { showNotice(phaseMessage(data)); return data; }
          return loadChats();
        });
      });
    });
    bindClick('personalTelegramLoadChatsBtn', function() { runBusy('正在读取…', function() { return loadChats(); }); });
    bindClick('personalTelegramSyncContactsBtn', function() {
      runBusy('正在同步通讯录…', function() {
        return request('/api/native-telegram/sync/contacts', { method: 'POST', json: {} }).then(function(data) {
          if (data && data.error === 'not_implemented') { showNotice(phaseMessage(data)); return data; }
          return loadContacts();
        });
      });
    });
    bindClick('personalTelegramLoadContactsBtn', function() { runBusy('正在读取…', function() { return loadContacts(); }); });
    bindClick('personalTelegramLoadRecordsBtn', function() { runBusy('正在刷新…', function() { return loadRecords(); }); });
    bindClick('personalTelegramLoadProtocolBtn', function() { runBusy('正在刷新…', function() { return loadProtocol(); }); });
    bindClick('personalTelegramCopyDiag', function() {
      var payload = JSON.stringify({ status: state.status, config: state.config }, null, 2);
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(payload).then(function() { showNotice('诊断信息已复制'); }).catch(function() { showError('复制失败'); });
      } else {
        showError('当前环境不支持剪贴板');
      }
    });
    var chatList = $('personalTelegramChatList');
    if (chatList) {
      chatList.addEventListener('click', function(event) {
        var item = event.target.closest('[data-ptg-peer]');
        if (!item) return;
        loadMessages(item.dataset.ptgPeer).catch(function(error) { showError(error.message || '读取消息失败'); });
      });
    }
    var search = $('personalTelegramChatSearch');
    if (search) {
      var timer = null;
      search.addEventListener('input', function() {
        clearTimeout(timer);
        timer = setTimeout(function() { loadChats().catch(function() {}); }, 280);
      });
    }
    var contactSearch = $('personalTelegramContactSearch');
    if (contactSearch) {
      var timer2 = null;
      contactSearch.addEventListener('input', function() {
        clearTimeout(timer2);
        timer2 = setTimeout(function() { loadContacts().catch(function() {}); }, 280);
      });
    }
  }

  window.initPersonalTelegramView = function() {
    bind();
    loadStatus().then(function() {
      if (((state.status || {}).login || {}).running) startPoll();
    }).catch(function(error) { showError(error.message || 'Telegram 状态读取失败'); });
    loadConfig().catch(function() {});
    loadChats().catch(function() {});
    loadRecords().catch(function() {});
    loadProtocol().catch(function() {});
  };
})();
