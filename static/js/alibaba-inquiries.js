(function () {
  'use strict';

  /* ============================================================
   * 阿里国际站接管 · 工作台
   * 一级：左侧导航（接待台/询盘/公海池/客户档案/知识库/人设与话术/排期与红线/账号）
   * 二级：主区列表或概览
   * 三级：右侧抽屉（详情）——不再把一堆东西堆在同一屏
   * 弹窗：新建/编辑/确认/预览/危险操作
   * ============================================================ */

  var NAV = [
    { key: 'desk', label: '接待台', icon: '◎', hint: '今天该处理什么', group: 'work' },
    { key: 'inquiries', label: '询盘', icon: '✉', hint: '按会话逐条处理', group: 'work' },
    { key: 'store', label: '店铺与产品', icon: '▦', hint: '公司资料 + 线上产品', group: 'work' },
    { key: 'pool', label: '公海池', icon: '◍', hint: 'T0 / T+2d / T+5d 激活', group: 'work' },
    { key: 'customers', label: '客户档案', icon: '☰', hint: '背调与分级', group: 'work' },
    { key: 'kb', label: '知识库', icon: '▤', hint: '产品/FAQ/报价/禁忌', group: 'config' },
    { key: 'persona', label: '人设与话术', icon: '✎', hint: '人设与回复策略', group: 'config' },
    { key: 'rules', label: '排期与红线', icon: '⚙', hint: '频率 · 轮次 · 禁词', group: 'config' },
    { key: 'accounts', label: '账号', icon: '⌘', hint: '登录态与同步', group: 'config' }
  ];

  var S = {
    accounts: [],
    accountId: null,
    view: 'desk',
    dashboard: null,
    config: null,
    inquiries: { items: [], total: 0, offset: 0, limit: 20, q: '' },
    archives: { items: [], total: 0, offset: 0, limit: 20, q: '' },
    docs: [],
    summaries: [],
    pool: { items: [], status: '' },
    store: null,
    storeStats: null,
    storeGroups: [],
    products: { items: [], total: 0, offset: 0, limit: 20, q: '', audit_status: '', shelf_status: '' },
    drawer: null,
    busy: false
  };

  /* ------------------------------------------------ 基础工具 */

  function $(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value === undefined || value === null ? '' : value).replace(/[&<>"']/g, function (ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
  }

  function apiBase() {
    return (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE ? String(LOCAL_API_BASE) : '').replace(/\/$/, '');
  }

  function authJson() {
    var token = (typeof LOCAL_AUTH_TOKEN !== 'undefined' && LOCAL_AUTH_TOKEN) || '';
    var headers = { 'Content-Type': 'application/json' };
    if (token) headers.Authorization = 'Bearer ' + token;
    return headers;
  }

  function authOnly() {
    var token = (typeof LOCAL_AUTH_TOKEN !== 'undefined' && LOCAL_AUTH_TOKEN) || '';
    return token ? { Authorization: 'Bearer ' + token } : {};
  }

  function parseErr(data, fallback) {
    if (!data) return fallback;
    if (typeof data === 'string') return data;
    return data.detail || data.message || data.error || fallback;
  }

  function apiJson(path, opts) {
    var base = apiBase();
    if (!base) return Promise.reject(new Error('未配置本机 LOCAL_API_BASE'));
    opts = opts || {};
    var req = { method: opts.method || 'GET', headers: authJson() };
    if (opts.body !== undefined) req.body = JSON.stringify(opts.body);
    return fetch(base + path, req).then(function (resp) {
      return resp.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
        if (!resp.ok) throw new Error(parseErr(data, 'HTTP ' + resp.status));
        return data;
      });
    });
  }

  function apiUpload(path, form) {
    var base = apiBase();
    if (!base) return Promise.reject(new Error('未配置本机 LOCAL_API_BASE'));
    return fetch(base + path, { method: 'POST', headers: authOnly(), body: form }).then(function (resp) {
      return resp.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
        if (!resp.ok) throw new Error(parseErr(data, 'HTTP ' + resp.status));
        return data;
      });
    });
  }

  function toast(message, kind) {
    var host = $('aliToasts');
    if (!host) return;
    var el = document.createElement('div');
    el.className = 'ali-toast' + (kind ? ' ' + kind : '');
    el.textContent = String(message || '');
    host.appendChild(el);
    setTimeout(function () { el.remove(); }, kind === 'err' ? 7000 : 3200);
  }

  function fmtTime(value) {
    if (!value) return '—';
    var d = new Date(value);
    if (isNaN(d.getTime())) return String(value).slice(0, 19);
    var p = function (n) { return (n < 10 ? '0' : '') + n; };
    return p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  }

  function compact(value, len) {
    var text = String(value === undefined || value === null ? '' : value).replace(/\s+/g, ' ').trim();
    var limit = len || 120;
    return text.length > limit ? text.slice(0, limit) + '…' : text;
  }

  function pct(value, total) {
    if (!total) return '0%';
    return Math.round((value / total) * 100) + '%';
  }

  function acct() {
    for (var i = 0; i < S.accounts.length; i += 1) {
      if (String(S.accounts[i].id) === String(S.accountId)) return S.accounts[i];
    }
    return null;
  }

  function needAccount() {
    if (S.accountId) return true;
    toast('先选一个阿里账号', 'err');
    S.view = 'accounts';
    render();
    return false;
  }

  /* ------------------------------------------------ 通用组件：弹窗 / 抽屉 */

  function openModal(options) {
    var opts = options || {};
    var mask = $('aliModalMask');
    var modal = $('aliModal');
    modal.className = 'ali-modal' + (opts.width === 'wide' ? ' is-wide' : opts.width === 'slim' ? ' is-slim' : '');
    $('aliModalHead').innerHTML = '<div>' + esc(opts.title || '') + '</div>' +
      (opts.sub ? '<div class="ali-hint">' + esc(opts.sub) + '</div>' : '');
    $('aliModalBody').innerHTML = opts.body || '';
    $('aliModalFoot').innerHTML = '';
    (opts.actions || []).forEach(function (action) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'ali-btn' + (action.kind ? ' ' + action.kind : '');
      btn.textContent = action.label;
      btn.addEventListener('click', function () { action.onClick && action.onClick(); });
      $('aliModalFoot').appendChild(btn);
    });
    if (!opts.actions || !opts.actions.length) {
      var close = document.createElement('button');
      close.type = 'button';
      close.className = 'ali-btn';
      close.textContent = '关闭';
      close.addEventListener('click', closeModal);
      $('aliModalFoot').appendChild(close);
    }
    mask.hidden = false;
    if (typeof opts.onMount === 'function') opts.onMount($('aliModalBody'));
  }

  function closeModal() {
    $('aliModalMask').hidden = true;
    $('aliModalBody').innerHTML = '';
    $('aliModalFoot').innerHTML = '';
  }

  function openDrawer(options) {
    var opts = options || {};
    S.drawer = opts.key || null;
    $('aliDrawerHead').innerHTML =
      '<div style="min-width:0;">' +
      '<div class="ali-drawer-title">' + esc(opts.title || '') + '</div>' +
      (opts.sub ? '<div class="ali-drawer-sub">' + opts.sub + '</div>' : '') +
      '</div>' +
      '<button type="button" class="ali-btn ghost sm ali-close" id="aliDrawerClose">✕</button>';
    $('aliDrawerBody').innerHTML = opts.body || '';
    $('aliDrawerFoot').innerHTML = '';
    (opts.actions || []).forEach(function (action) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'ali-btn' + (action.kind ? ' ' + action.kind : '');
      btn.textContent = action.label;
      btn.addEventListener('click', function () { action.onClick && action.onClick(); });
      $('aliDrawerFoot').appendChild(btn);
    });
    $('aliDrawer').hidden = false;
    $('aliDrawerMask').hidden = false;
    var closeBtn = $('aliDrawerClose');
    if (closeBtn) closeBtn.addEventListener('click', closeDrawer);
    if (typeof opts.onMount === 'function') opts.onMount($('aliDrawerBody'));
  }

  function closeDrawer() {
    S.drawer = null;
    $('aliDrawer').hidden = true;
    $('aliDrawerMask').hidden = true;
  }

  function confirmModal(message, onOk, options) {
    var opts = options || {};
    openModal({
      title: opts.title || '确认操作',
      width: 'slim',
      body: '<div class="ali-note">' + esc(message) + '</div>',
      actions: [
        { label: '取消', onClick: closeModal },
        { label: opts.okLabel || '确认', kind: opts.danger ? 'danger' : 'primary', onClick: function () { closeModal(); onOk(); } }
      ]
    });
  }

  function setBusy(busy, label) {
    S.busy = !!busy;
    var btn = $('aliSyncBtn');
    if (btn) btn.disabled = !!busy;
    if (busy && label) toast(label);
  }

  function emptyBlock(title, hint, actions) {
    var buttons = (actions || []).map(function (a) {
      return '<button type="button" class="ali-btn' + (a.kind ? ' ' + a.kind : '') + '" data-empty-action="' + esc(a.id) + '">' + esc(a.label) + '</button>';
    }).join('');
    return '<div class="ali-empty">' +
      '<div class="ali-empty-ico">◌</div>' +
      '<div class="ali-empty-title">' + esc(title) + '</div>' +
      '<div class="ali-empty-hint">' + esc(hint || '') + '</div>' +
      (buttons ? '<div class="ali-empty-actions">' + buttons + '</div>' : '') +
      '</div>';
  }

  function bindEmptyActions(root, map) {
    (root || document).querySelectorAll('[data-empty-action]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var handler = map[btn.getAttribute('data-empty-action')];
        if (handler) handler();
      });
    });
  }

  function badge(text, kind) {
    return '<span class="ali-badge' + (kind ? ' ' + kind : '') + '">' + esc(text) + '</span>';
  }

  function readStateBadge(session) {
    if (!session) return badge('未开始', '');
    if (session.human_takeover) return badge('人工接管', 'err');
    var map = {
      read_replied: ['已读已回', 'ok'],
      read_no_reply: ['已读未回', 'warn'],
      unread: ['未读', ''],
      unknown: ['未知', '']
    };
    var item = map[session.read_state] || map.unknown;
    return badge(item[0], item[1]);
  }

  function stageBadge(stage) {
    var map = {
      new: ['新询盘', ''],
      confirm_human: ['确认真人', 'info'],
      collect: ['采集需求', 'violet'],
      background: ['背调中', 'warn'],
      qualified: ['已分级', 'ok'],
      handoff: ['转人工', 'err'],
      closed: ['已结束', '']
    };
    var item = map[stage] || map.new;
    return badge(item[0], item[1]);
  }

  function onlineBadge(session) {
    var state = session && session.online_state;
    if (state === 'online' || state === 'active') return badge('在线', 'ok');
    if (state === 'hot' || state === 'typing') return badge('正在输入', 'violet');
    return badge('离线', '');
  }

  /* ------------------------------------------------ 账号 */

  function loadAccounts(silent) {
    return apiJson('/api/alibaba-inquiries/accounts').then(function (data) {
      S.accounts = (data && data.accounts) || [];
      if (!S.accountId && S.accounts.length) S.accountId = S.accounts[0].id;
      if (S.accountId && !acct()) S.accountId = S.accounts.length ? S.accounts[0].id : null;
      renderAccountPicker();
      if (!silent) render();
      return S.accounts;
    }).catch(function (err) {
      toast('账号加载失败：' + err.message, 'err');
      return [];
    });
  }

  function renderAccountPicker() {
    var select = $('aliAccountSelect');
    if (!select) return;
    select.innerHTML = S.accounts.length
      ? S.accounts.map(function (a) {
        return '<option value="' + esc(a.id) + '"' + (String(a.id) === String(S.accountId) ? ' selected' : '') + '>' +
          esc(a.nickname || ('账号 #' + a.id)) + ' · ' + esc(a.status || '') + '</option>';
      }).join('')
      : '<option value="">（还没有账号）</option>';
    var a = acct();
    $('aliAccountLine').textContent = a
      ? (a.nickname || '') + ' · 询盘 ' + (a.inquiry_count || 0) + ' · 客户 ' + (a.customer_count || 0) +
        ' · 最近同步 ' + fmtTime(a.last_sync_at)
      : '选择账号后开始';
  }

  function refreshHeaderStatus() {
    var cfg = S.config || {};
    var takeover = $('aliTakeoverBadge');
    if (takeover) {
      if (cfg.enabled && !cfg.dry_run) { takeover.className = 'ali-badge ok'; takeover.textContent = 'AI 接管中'; }
      else if (cfg.enabled && cfg.dry_run) { takeover.className = 'ali-badge warn'; takeover.textContent = '演练模式（只生成不发）'; }
      else { takeover.className = 'ali-badge'; takeover.textContent = 'AI 未接管'; }
    }
    var schedule = $('aliScheduleBadge');
    if (schedule) {
      var secs = Number(cfg.next_scan_seconds || (cfg.interval_minutes || 30) * 60);
      var text = secs >= 60 ? Math.round(secs / 60) + ' 分钟' : secs + ' 秒';
      schedule.className = 'ali-badge ' + (cfg.enabled ? 'info' : '');
      schedule.textContent = '排期 ' + text + (cfg.in_work_window === false ? ' · 非工作时段' : '');
    }
  }

  /* ------------------------------------------------ 接待台 */

  function loadDashboard() {
    if (!S.accountId) return Promise.resolve(null);
    return apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) + '/dashboard')
      .then(function (data) {
        S.dashboard = data;
        S.config = (data && data.config) || S.config;
        refreshHeaderStatus();
        if (S.view === 'desk') render();
        return data;
      })
      .catch(function (err) { toast('接待台加载失败：' + err.message, 'err'); return null; });
  }

  function renderDesk(host) {
    var data = S.dashboard;
    if (!data) {
      host.innerHTML = '<div class="ali-card"><div class="ali-card-body"><div class="ali-skel" style="width:40%"></div></div></div>';
      return;
    }
    var stats = data.stats || {};
    var windowDays = stats.pending_window_days === null || stats.pending_window_days === undefined ? 30 : stats.pending_window_days;
    var historyPending = stats.pending_historical || 0;
    var cards = [
      {
        key: 'awaiting', num: stats.awaiting_reply,
        label: '待回复询盘' + (windowDays > 0 ? '（近 ' + windowDays + ' 天）' : '（全部历史）'),
        note: historyPending > 0 ? '另有 ' + historyPending + ' 条历史：可去公海池激活' : '买家最后一条还没回',
        view: 'inquiries'
      },
      { key: 'online', num: stats.online, label: '在线客户', note: '在线要提频到 60s/30s', view: 'inquiries' },
      { key: 'read', num: stats.read_no_reply, label: '已读未回', note: '可以主动撩动', view: 'inquiries' },
      { key: 'nudge', num: stats.nudge_ready, label: '待撩动（换角度）', note: '到点用另一个角度触达', view: 'inquiries' },
      { key: 'sent', num: stats.today_sent, label: '今日已回', note: '含人工与 AI', view: 'inquiries' },
      { key: 'handoff', num: stats.human_takeover, label: '人工接管', note: '命中红线或手动接管', view: 'inquiries' },
      { key: 'pool', num: stats.pool_pending, label: '公海池待激活', note: 'T0/T+2d/T+5d', view: 'pool' },
      { key: 'kb', num: stats.kb_docs, label: '知识库资料', note: '产品/FAQ/报价/禁忌', view: 'kb' }
    ];
    host.innerHTML =
      '<div class="ali-head"><div><div class="ali-h1">接待台</div>' +
      '<div class="ali-hint">先确认真人是活人，再判断价值；命中红线自动转人工</div></div>' +
      '<div class="ali-toolbar">' +
      '<button type="button" class="ali-btn" data-act="run-dry">演练一轮（不发）</button>' +
      '<button type="button" class="ali-btn primary" data-act="run-live">真实跑一轮</button>' +
      '</div></div>' +
      '<div class="ali-stats">' + cards.map(function (c) {
        return '<div class="ali-stat" data-stat-view="' + c.view + '">' +
          '<div class="ali-stat-num">' + (c.num === undefined ? '—' : c.num) + '</div>' +
          '<div class="ali-stat-label">' + esc(c.label) + '</div>' +
          '<div class="ali-stat-note">' + esc(c.note) + '</div></div>';
      }).join('') + '</div>' +
      '<div class="ali-card" style="margin-top:12px;">' +
      '<div class="ali-card-head"><div class="ali-card-title">待处理队列</div>' +
      '<div class="ali-toolbar">' + badge('排期 ' + (S.config && S.config.enabled ? '已开启' : '未开启'), S.config && S.config.enabled ? 'ok' : '') +
      badge((S.config && S.config.dry_run ? '演练模式' : '真实发送'), S.config && S.config.dry_run ? 'warn' : 'err') +
      '</div></div>' +
      '<div id="aliDeskQueue">' + renderQueue(data.queue || []) + '</div></div>' +
      '<div class="ali-card"><div class="ali-card-head"><div class="ali-card-title">准备工作</div>' +
      '<div class="ali-hint">三件事做完，AI 接待才有依据</div></div>' +
      '<div class="ali-card-body"><div class="ali-stats">' +
      '<div class="ali-stat" data-prep="inquiries">' +
      '<div class="ali-stat-num">' + (data.stats && data.stats.inquiries !== undefined ? data.stats.inquiries : '—') + '</div>' +
      '<div class="ali-stat-label">① 同步询盘</div>' +
      '<div class="ali-stat-note">' + (data.stats && data.stats.awaiting_reply ? '待回复 ' + data.stats.awaiting_reply + ' 条' : '已同步') + '</div></div>' +
      '<div class="ali-stat" data-prep="store">' +
      '<div class="ali-stat-num">' + (S.storeStats ? S.storeStats.products : '—') + '</div>' +
      '<div class="ali-stat-label">② 店铺与产品</div>' +
      '<div class="ali-stat-note">' + (S.store && S.store.company_name ? '已同步 · 线上 ' + ((S.storeStats && S.storeStats.online) || 0) + ' 条' : '还没同步') + '</div></div>' +
      '<div class="ali-stat" data-prep="kb">' +
      '<div class="ali-stat-num">' + (data.stats && data.stats.kb_docs !== undefined ? data.stats.kb_docs : '—') + '</div>' +
      '<div class="ali-stat-label">③ 知识库（报价表/FAQ）</div>' +
      '<div class="ali-stat-note">上传真实资料，回复才有依据</div></div>' +
      '</div></div></div>' +
      '<div class="ali-card"><div class="ali-card-head"><div class="ali-card-title">当前策略</div>' +
      '<button type="button" class="ali-btn sm" data-act="goto-persona">去人设与话术</button></div>' +
      '<div class="ali-card-body"><div class="ali-note">' +
      (data.strategy && data.strategy.id
        ? ('已启用策略 #' + data.strategy.id + '：' + esc(compact(data.strategy.content, 220)))
        : '还没有策略：去「人设与话术」上传资料后生成。') +
      '</div></div></div>';

    host.querySelectorAll('[data-stat-view]').forEach(function (el) {
      el.addEventListener('click', function () { S.view = el.getAttribute('data-stat-view'); render(); });
    });
    host.querySelector('[data-act="run-dry"]').addEventListener('click', function () { showRunModal(true); });
    host.querySelector('[data-act="run-live"]').addEventListener('click', function () { showRunModal(false); });
    host.querySelector('[data-act="goto-persona"]').addEventListener('click', function () { S.view = 'persona'; render(); });
    host.querySelectorAll('[data-prep]').forEach(function (el) {
      el.addEventListener('click', function () {
        var kind = el.getAttribute('data-prep');
        if (kind === 'store') {
          S.view = 'store';
          render();
          loadViewData();
          return;
        }
        S.view = kind === 'kb' ? 'kb' : 'inquiries';
        render();
        loadViewData();
      });
    });
    bindQueue(host);
  }

  function renderQueue(queue) {
    if (!queue.length) {
      return emptyBlock('暂时没有待处理的询盘', '同步一次询盘，或稍后再看；公海池可以在「公海池」页激活', []);
    }
    return queue.map(function (item) {
      var session = item.session || {};
      return '<div class="ali-queue-item" data-inquiry="' + esc(item.inquiry_id) + '">' +
        '<div class="ali-avatar">' + esc((item.buyer_name || '?').slice(0, 1)) + '</div>' +
        '<div style="min-width:0;flex:1 1 auto;">' +
        '<div class="ali-cell-main">' + esc(item.buyer_name || item.title || item.inquiry_id) +
        ' <span class="ali-cell-sub">' + esc(item.country || '') + (item.company_name ? ' · ' + esc(item.company_name) : '') + '</span></div>' +
        '<div class="ali-cell-sub">' + esc(compact(item.preview, 110)) + '</div>' +
        '<div class="ali-inline" style="margin-top:6px;gap:6px;flex-wrap:wrap;">' +
        readStateBadge(session) + onlineBadge(session) + stageBadge(session.stage) +
        badge('轮次 ' + (session.turn_count || 0), '') +
        (item.notify === 'business' ? badge('需要业务/老板介入', 'err') : badge(esc(item.next_action || ''), 'info')) +
        (item.info_level ? badge('信息 ' + item.info_level, item.info_level === 'L3' ? 'ok' : 'warn') : '') +
        ((item.next_ask || []).length ? badge('缺 ' + (item.next_ask || []).join('/'), '') : '') +
        '</div></div>' +
        '<div class="ali-cell-sub" style="flex:0 0 auto;">' + fmtTime(item.last_message_at) + '</div>' +
        '</div>';
    }).join('');
  }

  function bindQueue(root) {
    (root || document).querySelectorAll('[data-inquiry]').forEach(function (el) {
      el.addEventListener('click', function () { showInquiryDrawer(el.getAttribute('data-inquiry')); });
    });
  }

  /* ------------------------------------------------ 询盘列表 */

  function loadInquiries(reset) {
    if (!S.accountId) return Promise.resolve();
    if (reset) S.inquiries.offset = 0;
    var q = '/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) + '/inquiries' +
      '?limit=' + S.inquiries.limit + '&offset=' + S.inquiries.offset +
      (S.inquiries.q ? '&q=' + encodeURIComponent(S.inquiries.q) : '');
    return apiJson(q).then(function (data) {
      S.inquiries.items = (data && data.inquiries) || (data && data.items) || [];
      S.inquiries.total = (data && (data.total || 0)) || 0;
      if (S.view === 'inquiries') render();
    }).catch(function (err) { toast('询盘加载失败：' + err.message, 'err'); });
  }

  function renderInquiries(host) {
    var rows = S.inquiries.items || [];
    host.innerHTML =
      '<div class="ali-head"><div><div class="ali-h1">询盘</div>' +
      '<div class="ali-hint">列表只放关键信息，点开右侧抽屉再处理会话与回复</div></div>' +
      '<div class="ali-toolbar">' +
      '<input class="ali-input" id="aliInquirySearch" placeholder="搜索买家 / 公司 / 国家" value="' + esc(S.inquiries.q) + '" style="width:220px">' +
      '<button type="button" class="ali-btn" data-act="search">搜索</button>' +
      '<button type="button" class="ali-btn" data-act="reload">刷新</button>' +
      '</div></div>' +
      '<div class="ali-card"><div class="ali-card-body" style="padding:0;">' +
      (rows.length
        ? '<table class="ali-table"><thead><tr><th>买家</th><th>公司 / 国家</th><th>最近消息</th><th>状态</th><th>时间</th></tr></thead><tbody>' +
          rows.map(function (r) {
            return '<tr data-inquiry="' + esc(r.inquiry_id) + '">' +
              '<td><div class="ali-cell-main">' + esc(r.buyer_name || '—') + '</div>' +
              '<div class="ali-cell-sub">' + esc(compact(r.preview, 70)) + '</div></td>' +
              '<td><div>' + esc(r.company_name || '—') + '</div><div class="ali-cell-sub">' + esc(r.country || '') + '</div></td>' +
              '<td>' + fmtTime(r.last_message_at) + '</td>' +
              '<td>' + badge(r.status || '—', '') + '</td>' +
              '<td>' + fmtTime(r.updated_at || r.created_at) + '</td></tr>';
          }).join('') + '</tbody></table>'
        : emptyBlock('还没有询盘', '先点右上角「同步询盘」把阿里国际站的历史对话拉下来', [
          { id: 'sync', label: '同步询盘', kind: 'primary' }
        ])) +
      '</div>' +
      '<div class="ali-pager">共 ' + (S.inquiries.total || rows.length) + ' 条 · 第 ' +
      (Math.floor(S.inquiries.offset / S.inquiries.limit) + 1) + ' 页' +
      '<button type="button" class="ali-btn sm" data-act="prev">上一页</button>' +
      '<button type="button" class="ali-btn sm" data-act="next">下一页</button></div></div>';

    host.querySelector('[data-act="search"]').addEventListener('click', function () {
      S.inquiries.q = $('aliInquirySearch').value.trim();
      loadInquiries(true);
    });
    host.querySelector('[data-act="reload"]').addEventListener('click', function () { loadInquiries(true); });
    host.querySelector('[data-act="prev"]').addEventListener('click', function () {
      S.inquiries.offset = Math.max(0, S.inquiries.offset - S.inquiries.limit);
      loadInquiries(false);
    });
    host.querySelector('[data-act="next"]').addEventListener('click', function () {
      if (S.inquiries.offset + S.inquiries.limit < S.inquiries.total) {
        S.inquiries.offset += S.inquiries.limit;
        loadInquiries(false);
      }
    });
    var input = $('aliInquirySearch');
    if (input) {
      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { S.inquiries.q = input.value.trim(); loadInquiries(true); }
      });
    }
    bindEmptyActions(host, { sync: runSync });
    bindQueue(host);
  }

  /* ------------------------------------------------ 询盘抽屉 */

  function showInquiryDrawer(inquiryId) {
    openDrawer({
      key: 'inquiry:' + inquiryId,
      title: '询盘详情',
      sub: '<span class="ali-skel" style="display:inline-block;width:120px;"></span>',
      body: '<div class="ali-card"><div class="ali-card-body"><div class="ali-skel" style="width:60%"></div></div></div>'
    });
    Promise.all([
      apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) +
        '/inquiries/' + encodeURIComponent(inquiryId)).catch(function (e) { return { __err: e.message }; }),
      apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) +
        '/inquiries/' + encodeURIComponent(inquiryId) + '/reception').catch(function (e) { return { __err: e.message }; })
    ]).then(function (res) {
      if (S.drawer !== 'inquiry:' + inquiryId) return;
      var detail = res[0] || {};
      var reception = res[1] || {};
      if (detail.__err && reception.__err) { toast('详情加载失败：' + detail.__err, 'err'); return; }
      paintInquiryDrawer(inquiryId, detail, reception);
    });
  }

  function paintInquiryDrawer(inquiryId, detail, reception) {
    var inquiry = detail.inquiry || detail || {};
    var messages = detail.messages || inquiry.messages || [];
    var session = reception.session || {};
    var cfg = reception.config || S.config || {};
    var archive = detail.archive || null;
    var verdict = reception.verdict || null;
    var sufficiency = reception.info_sufficiency || (verdict && verdict.sufficiency) || {};
    var nextAsk = (reception.next_ask || (verdict && verdict.gaps) || []);

    var msgHtml = messages.length
      ? messages.slice(-40).map(function (m) {
        var buyer = String(m.direction || '') === 'buyer';
        return '<div class="ali-msg ' + (buyer ? 'buyer' : 'seller') + '">' + esc(m.content || '') +
          '<div class="ali-msg-meta">' + (buyer ? '买家' : '我方') + ' · ' + fmtTime(m.sent_at || m.created_at) +
          (m.raw && m.raw.source ? ' · ' + esc(m.raw.source) : '') + '</div></div>';
      }).join('')
      : '<div class="ali-note">本地还没有消息记录：点下面「同步详情」拉一次。</div>';

    var body =
      '<div class="ali-card"><div class="ali-card-body">' +
      '<div class="ali-inline" style="flex-wrap:wrap;gap:6px;">' +
      readStateBadge(session) + onlineBadge(session) + stageBadge(session.stage) +
      badge('轮次 ' + (session.turn_count || 0) + ' / ' + (cfg.max_turns || 8), '') +
      badge('剩余可回 ' + (reception.turns_left === undefined ? '—' : reception.turns_left) + ' 轮', 'info') +
      (session.human_takeover ? badge('人工接管中', 'err') : '') +
      '</div>' +
      '<dl class="ali-kv" style="margin-top:10px;">' +
      '<dt>买家</dt><dd>' + esc(inquiry.buyer_name || '—') + '</dd>' +
      '<dt>公司</dt><dd>' + esc(inquiry.company_name || '—') + '</dd>' +
      '<dt>国家</dt><dd>' + esc(inquiry.country || '—') + '</dd>' +
      '<dt>询盘ID</dt><dd>' + esc(inquiryId) + '</dd>' +
      '<dt>最近消息</dt><dd>' + fmtTime(inquiry.last_message_at) + '</dd>' +
      '</dl></div></div>' +
      '<div class="ali-card"><div class="ali-card-head"><div class="ali-card-title">会话记录（最近 40 条）</div>' +
      '<button type="button" class="ali-btn sm" data-act="sync-detail">同步详情</button></div>' +
      '<div class="ali-card-body"><div class="ali-msgs">' + msgHtml + '</div></div></div>' +
      '<div class="ali-card"><div class="ali-card-head"><div class="ali-card-title">回复</div>' +
      '<div class="ali-toolbar">' + badge('上限 ' + (cfg.max_chars || 380) + ' 字', '') +
      badge('延迟 ' + (cfg.delay_min_seconds || 25) + '-' + (cfg.delay_max_seconds || 90) + 's', '') + '</div></div>' +
      '<div class="ali-card-body">' +
      (nextAsk.length
        ? '<div class="ali-note" style="margin-bottom:8px;">还缺：' + esc(nextAsk.join('、')) +
          '　<button type="button" class="ali-btn sm" data-act="ask-missing">按缺口生成追问</button>' +
          (sufficiency.level ? '　' + badge('信息 ' + sufficiency.level, 'warn') : '') + '</div>'
        : (sufficiency.level
          ? '<div class="ali-note" style="margin-bottom:8px;">信息 ' + esc(sufficiency.level) + '：' +
            esc(sufficiency.level_desc || '') + '</div>'
          : '')) +
      '<textarea class="ali-input" id="aliReplyText" placeholder="可以直接写，或先点「生成草稿」"></textarea>' +
      '<div class="ali-toolbar" style="margin-top:10px;">' +
      '<button type="button" class="ali-btn" data-act="draft">生成草稿</button>' +
      '<button type="button" class="ali-btn" data-act="send-dry">演练发送</button>' +
      '<button type="button" class="ali-btn primary" data-act="send">发送回复</button>' +
      '<button type="button" class="ali-btn ' + (session.human_takeover ? '' : 'danger') + '" data-act="takeover">' +
      (session.human_takeover ? '交回 AI' : '人工接管') + '</button>' +
      '</div>' +
      (session.human_takeover ? '<div class="ali-note" style="margin-top:8px;">已人工接管：AI 不会再自动回这条。</div>' : '') +
      '</div></div>' +
      '<div class="ali-card"><div class="ali-card-head"><div class="ali-card-title">客户档案</div>' +
      '<div class="ali-toolbar">' +
      '<button type="button" class="ali-btn sm" data-act="enrich">生成/更新档案</button>' +
      (archive ? '<button type="button" class="ali-btn sm" data-act="open-archive">打开档案</button>' : '') +
      '</div></div>' +
      '<div class="ali-card-body">' +
      (archive
        ? '<dl class="ali-kv"><dt>分级</dt><dd>' + esc(archive.grade || '—') + '</dd>' +
          '<dt>评分</dt><dd>' + (archive.score === null || archive.score === undefined ? '—' : archive.score) + '</dd>' +
          '<dt>证据</dt><dd>' + (archive.evidence_count || 0) + ' 条</dd></dl>' +
          '<div class="ali-note" style="margin-top:8px;">' + esc(compact(archive.summary || '', 260)) + '</div>'
        : '<div class="ali-note">还没有档案：生成后会自动做背调（官网/公开信息源）并给出分级。</div>') +
      '</div></div>';

    if (verdict) {
      body +=
        '<div class="ali-card"><div class="ali-card-head"><div class="ali-card-title">背调结论</div>' +
        '<div class="ali-toolbar">' +
        badge(verdict.verdict_label || verdict.verdict || '—',
          verdict.verdict === 'qualified' ? 'ok' : verdict.verdict === 'needs_more_info' ? 'warn' : 'info') +
        badge('置信度 ' + (verdict.confidence || '—'), verdict.confidence === 'high' ? 'ok' : '') +
        (verdict.notify === 'business' ? badge('需要业务/老板介入', 'err') : '') +
        '</div></div><div class="ali-card-body">' +
        '<ul style="margin:0;padding-left:18px;line-height:1.7;">' +
        (verdict.why || []).map(function (line) { return '<li>' + esc(line) + '</li>'; }).join('') +
        '</ul>' +
        ((verdict.gaps || []).length
          ? '<div class="ali-note" style="margin-top:8px;">缺口：' + esc((verdict.gaps || []).join('、')) + '（补上后可升级置信度）</div>'
          : '') +
        '</div></div>';
    }

    openDrawer({
      key: 'inquiry:' + inquiryId,
      title: inquiry.buyer_name || '询盘详情',
      sub: esc(inquiry.company_name || '') + (inquiry.country ? ' · ' + esc(inquiry.country) : '') +
        ' · <span class="ali-cell-sub">' + esc(inquiryId) + '</span>',
      body: body,
      actions: [
        { label: '关闭', onClick: closeDrawer }
      ],
      onMount: function (root) {
        function on(act, handler) {
          var el = root.querySelector('[data-act="' + act + '"]');
          if (el) el.addEventListener('click', handler);
        }
        on('sync-detail', function () {
          toast('正在同步详情…');
          apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) +
            '/inquiries/' + encodeURIComponent(inquiryId) + '/sync-detail', { method: 'POST', body: {} })
            .then(function () { toast('详情已同步', 'ok'); showInquiryDrawer(inquiryId); loadInquiries(true); })
            .catch(function (e) { toast('同步失败：' + e.message, 'err'); });
        });
        on('draft', function () {
          toast('正在生成草稿…');
          apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) + '/reply/draft',
            { method: 'POST', body: { inquiry_id: inquiryId, instruction: '' } })
            .then(function (data) {
              var text = ((data || {}).draft || {}).reply || '';
              var box = $('aliReplyText');
              if (box) box.value = text;
              toast(text ? '草稿已生成' : '模型没给出草稿', text ? 'ok' : 'err');
            })
            .catch(function (e) { toast('生成失败：' + e.message, 'err'); });
        });
        on('ask-missing', function () {
          var missingText = nextAsk.length ? nextAsk.join('、') : '公司名称、官网或邮箱';
          var instruction = '本轮目的：继续要信息（资料不够做背景核验）。要问的字段：' + missingText +
            '。要求：一次只问 1-2 个字段；先给一句对对方有用的价值/确认，再自然地问；不要罗列清单、不要催促成交。';
          toast('正在按缺口生成追问…');
          apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) + '/reply/draft',
            { method: 'POST', body: { inquiry_id: inquiryId, instruction: instruction } })
            .then(function (data) {
              var text = ((data || {}).draft || {}).reply || '';
              var box = $('aliReplyText');
              if (box) box.value = text;
              toast(text ? '追问已生成' : '模型没给出内容', text ? 'ok' : 'err');
            })
            .catch(function (e) { toast('生成失败：' + e.message, 'err'); });
        });
        on('send-dry', function () { sendReply(inquiryId, true); });
        on('send', function () { sendReply(inquiryId, false); });
        on('takeover', function () {
          var next = !session.human_takeover;
          apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) +
            '/inquiries/' + encodeURIComponent(inquiryId) + '/takeover',
            { method: 'POST', body: { on: next } })
            .then(function () {
              toast(next ? '已转人工' : '已交回 AI', 'ok');
              showInquiryDrawer(inquiryId);
              loadDashboard();
            })
            .catch(function (e) { toast('操作失败：' + e.message, 'err'); });
        });
        on('enrich', function () {
          toast('正在生成客户档案（背调可能需要一会儿）…');
          apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) +
            '/inquiries/' + encodeURIComponent(inquiryId) + '/archive/enrich',
            { method: 'POST', body: { force: true, max_results: 8 } })
            .then(function () { toast('档案任务已提交', 'ok'); showInquiryDrawer(inquiryId); })
            .catch(function (e) { toast('提交失败：' + e.message, 'err'); });
        });
        on('open-archive', function () { showArchiveDrawer(archive.id); });
      }
    });
  }

  function sendReply(inquiryId, dryRun) {
    var box = $('aliReplyText');
    var content = box ? box.value.trim() : '';
    if (!content) { toast('回复内容不能为空', 'err'); return; }
    confirmModal(dryRun ? '演练发送：只打开页面不会真的发出，确定吗？' : '确认发送这条回复？', function () {
      apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) + '/reply/send',
        { method: 'POST', body: { inquiry_id: inquiryId, content: content, dry_run: !!dryRun } })
        .then(function (data) {
          if (data && data.ok === false) { toast(data.message || '发送失败', 'err'); return; }
          toast(dryRun ? '演练完成（未发送）' : '回复已发送', 'ok');
          showInquiryDrawer(inquiryId);
          loadInquiries(true);
          loadDashboard();
        })
        .catch(function (e) { toast('发送失败：' + e.message, 'err'); });
    }, { okLabel: dryRun ? '演练' : '发送' });
  }

  /* ------------------------------------------------ 客户档案 */

  function loadArchives(reset) {
    if (!needAccount()) return Promise.resolve();
    if (reset) S.archives.offset = 0;
    var q = '/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) + '/customer-archives' +
      '?limit=' + S.archives.limit + '&offset=' + S.archives.offset +
      (S.archives.q ? '&q=' + encodeURIComponent(S.archives.q) : '');
    return apiJson(q).then(function (data) {
      S.archives.items = (data && (data.items || data.archives)) || [];
      S.archives.total = (data && (data.total || 0)) || 0;
      if (S.view === 'customers') render();
      renderNav();
    }).catch(function (err) { toast('档案加载失败：' + err.message, 'err'); });
  }

  function renderCustomers(host) {
    var rows = S.archives.items || [];
    host.innerHTML =
      '<div class="ali-head"><div><div class="ali-h1">客户档案</div>' +
      '<div class="ali-hint">背调结果默认只给结论与分级；证据链在抽屉里核验</div></div>' +
      '<div class="ali-toolbar">' +
      '<input class="ali-input" id="aliArchiveSearch" placeholder="搜索公司 / 域名 / 邮箱" value="' + esc(S.archives.q) + '" style="width:230px">' +
      '<button type="button" class="ali-btn" data-act="search">搜索</button>' +
      '<button type="button" class="ali-btn" data-act="reload">刷新</button>' +
      '</div></div>' +
      '<div class="ali-card"><div class="ali-card-body" style="padding:0;">' +
      (rows.length
        ? '<table class="ali-table"><thead><tr><th>客户</th><th>国家 / 域名</th><th>分级</th><th>证据</th><th>更新时间</th></tr></thead><tbody>' +
          rows.map(function (r) {
            return '<tr data-archive="' + esc(r.id) + '">' +
              '<td><div class="ali-cell-main">' + esc(r.display_name || r.company_name || '—') + '</div>' +
              '<div class="ali-cell-sub">' + esc(r.buyer_name || '') + '</div></td>' +
              '<td><div>' + esc(r.country || '—') + '</div><div class="ali-cell-sub">' + esc(r.domain || '') + '</div></td>' +
              '<td>' + badge(r.grade || '未分级', r.grade === 'A' ? 'ok' : r.grade === 'B' ? 'info' : '') +
              (r.score === null || r.score === undefined ? '' : ' <span class="ali-cell-sub">' + r.score + '</span>') + '</td>' +
              '<td>' + badge((r.evidence_count || 0) + ' 条', '') + '</td>' +
              '<td>' + fmtTime(r.updated_at) + '</td></tr>';
          }).join('') + '</tbody></table>'
        : emptyBlock('还没有客户档案', '在询盘抽屉里点「生成/更新档案」，或在接待台跑一轮', [])) +
      '</div><div class="ali-pager">共 ' + (S.archives.total || rows.length) + ' 条' +
      '<button type="button" class="ali-btn sm" data-act="prev">上一页</button>' +
      '<button type="button" class="ali-btn sm" data-act="next">下一页</button></div></div>';
    host.querySelector('[data-act="search"]').addEventListener('click', function () {
      S.archives.q = $('aliArchiveSearch').value.trim();
      loadArchives(true);
    });
    host.querySelector('[data-act="reload"]').addEventListener('click', function () { loadArchives(true); });
    host.querySelector('[data-act="prev"]').addEventListener('click', function () {
      S.archives.offset = Math.max(0, S.archives.offset - S.archives.limit);
      loadArchives(false);
    });
    host.querySelector('[data-act="next"]').addEventListener('click', function () {
      if (S.archives.offset + S.archives.limit < S.archives.total) {
        S.archives.offset += S.archives.limit;
        loadArchives(false);
      }
    });
    host.querySelectorAll('[data-archive]').forEach(function (el) {
      el.addEventListener('click', function () { showArchiveDrawer(el.getAttribute('data-archive')); });
    });
  }

  function showArchiveDrawer(archiveId) {
    openDrawer({
      key: 'archive:' + archiveId,
      title: '客户档案',
      body: '<div class="ali-card"><div class="ali-card-body"><div class="ali-skel" style="width:50%"></div></div></div>'
    });
    apiJson('/api/alibaba-inquiries/customer-archives/' + encodeURIComponent(archiveId))
      .then(function (data) {
        if (S.drawer !== 'archive:' + archiveId) return;
        var archive = (data && (data.archive || data)) || {};
        var evidence = (data && data.evidence) || archive.evidence || [];
        var basics = archive.basics || archive.profile || {};
        var body =
          '<div class="ali-card"><div class="ali-card-body">' +
          '<div class="ali-inline" style="gap:6px;flex-wrap:wrap;">' +
          badge('分级 ' + (archive.grade || '—'), archive.grade === 'A' ? 'ok' : 'info') +
          badge('评分 ' + (archive.score === null || archive.score === undefined ? '—' : archive.score), '') +
          badge('证据 ' + (evidence.length || archive.evidence_count || 0) + ' 条', '') +
          '</div>' +
          '<div class="ali-note" style="margin-top:10px;">' + esc(archive.summary || '暂无结论') + '</div>' +
          '</div></div>' +
          '<div class="ali-card"><div class="ali-card-head"><div class="ali-card-title">基础信息</div>' +
          '<button type="button" class="ali-btn sm" data-act="edit">人工修正</button></div>' +
          '<div class="ali-card-body"><dl class="ali-kv">' +
          '<dt>公司</dt><dd>' + esc(basics.company_name || archive.company_name || '—') + '</dd>' +
          '<dt>域名</dt><dd>' + esc(basics.domain || archive.domain || '—') + '</dd>' +
          '<dt>邮箱</dt><dd>' + esc(basics.email || archive.email || '—') + '</dd>' +
          '<dt>电话</dt><dd>' + esc(basics.phone || archive.phone || '—') + '</dd>' +
          '<dt>国家</dt><dd>' + esc(archive.country || '—') + '</dd>' +
          '</dl></div></div>' +
          '<div class="ali-card"><div class="ali-card-head"><div class="ali-card-title">证据链（仅内部核验）</div>' +
          '<button type="button" class="ali-btn sm" data-act="rerun">重新背调</button></div>' +
          '<div class="ali-card-body">' +
          (evidence.length
            ? evidence.slice(0, 30).map(function (e) {
              return '<div class="ali-evidence">' +
                '<div class="ali-evidence-title">' + esc(e.title || e.source_label || e.source_type || '来源') + '</div>' +
                '<div class="ali-evidence-snip">' + esc(compact(e.snippet || '', 260)) + '</div>' +
                (e.url ? '<div class="ali-note" style="margin-top:4px;">' + esc(compact(e.url, 90)) + '</div>' : '') +
                '</div>';
            }).join('')
            : '<div class="ali-note">还没有证据：点右上角重新背调</div>') +
          '</div></div>';
        openDrawer({
          key: 'archive:' + archiveId,
          title: archive.display_name || archive.company_name || ('档案 #' + archiveId),
          sub: esc(archive.country || '') + ' · ' + esc(archive.domain || ''),
          body: body,
          actions: [{ label: '关闭', onClick: closeDrawer }],
          onMount: function (root) {
            var edit = root.querySelector('[data-act="edit"]');
            if (edit) edit.addEventListener('click', function () { showArchiveEditModal(archive); });
            var rerun = root.querySelector('[data-act="rerun"]');
            if (rerun) rerun.addEventListener('click', function () {
              apiJson('/api/alibaba-inquiries/customer-archives/' + encodeURIComponent(archiveId) + '/rerun',
                { method: 'POST', body: { force: true, max_results: 8 } })
                .then(function () { toast('已提交重新背调', 'ok'); })
                .catch(function (e) { toast('提交失败：' + e.message, 'err'); });
            });
          }
        });
      })
      .catch(function (err) { toast('档案加载失败：' + err.message, 'err'); });
  }

  function showArchiveEditModal(archive) {
    var basics = archive.basics || archive.profile || {};
    openModal({
      title: '人工修正客户档案',
      sub: '只影响本机档案，不会写回阿里',
      body:
        '<div class="ali-grid-2">' +
        '<div class="ali-field"><div class="ali-field-label">显示名</div><input class="ali-input" id="aliArchName" value="' + esc(archive.display_name || '') + '"></div>' +
        '<div class="ali-field"><div class="ali-field-label">公司</div><input class="ali-input" id="aliArchCompany" value="' + esc(basics.company_name || archive.company_name || '') + '"></div>' +
        '<div class="ali-field"><div class="ali-field-label">国家</div><input class="ali-input" id="aliArchCountry" value="' + esc(archive.country || '') + '"></div>' +
        '<div class="ali-field"><div class="ali-field-label">域名</div><input class="ali-input" id="aliArchDomain" value="' + esc(basics.domain || archive.domain || '') + '"></div>' +
        '<div class="ali-field"><div class="ali-field-label">邮箱</div><input class="ali-input" id="aliArchEmail" value="' + esc(basics.email || archive.email || '') + '"></div>' +
        '<div class="ali-field"><div class="ali-field-label">电话</div><input class="ali-input" id="aliArchPhone" value="' + esc(basics.phone || archive.phone || '') + '"></div>' +
        '<div class="ali-field"><div class="ali-field-label">分级</div><input class="ali-input" id="aliArchGrade" value="' + esc(archive.grade || '') + '" placeholder="A / B / C / D"></div>' +
        '<div class="ali-field"><div class="ali-field-label">评分</div><input class="ali-input" id="aliArchScore" value="' + esc(archive.score === null || archive.score === undefined ? '' : archive.score) + '" placeholder="0-100"></div>' +
        '</div>' +
        '<div class="ali-field"><div class="ali-field-label">备注</div><textarea class="ali-input" id="aliArchNotes">' + esc(archive.notes || '') + '</textarea></div>',
      actions: [
        { label: '取消', onClick: closeModal },
        {
          label: '保存',
          kind: 'primary',
          onClick: function () {
            var payload = {
              display_name: $('aliArchName').value.trim(),
              company_name: $('aliArchCompany').value.trim(),
              country: $('aliArchCountry').value.trim(),
              grade: $('aliArchGrade').value.trim(),
              notes: $('aliArchNotes').value.trim(),
              score: $('aliArchScore').value === '' ? null : Number($('aliArchScore').value),
              basics: {
                company_name: $('aliArchCompany').value.trim(),
                domain: $('aliArchDomain').value.trim(),
                email: $('aliArchEmail').value.trim(),
                phone: $('aliArchPhone').value.trim()
              }
            };
            apiJson('/api/alibaba-inquiries/customer-archives/' + encodeURIComponent(archive.id),
              { method: 'PATCH', body: payload })
              .then(function () {
                closeModal();
                toast('已保存', 'ok');
                showArchiveDrawer(archive.id);
                loadArchives(true);
              })
              .catch(function (e) { toast('保存失败：' + e.message, 'err'); });
          }
        }
      ]
    });
  }

  /* ------------------------------------------------ 知识库 */

  /* ------------------------------------------------ 店铺与产品（准备工作②） */

  function loadStore() {
    if (!S.accountId) return Promise.resolve();
    return apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) + '/store')
      .then(function (data) {
        S.store = (data && data.store) || null;
        S.storeStats = (data && data.stats) || null;
        S.storeGroups = (data && data.groups) || [];
        if (S.view === 'store') render();
        return data;
      })
      .catch(function (err) { toast('店铺资料加载失败：' + err.message, 'err'); });
  }

  function loadProducts(reset) {
    if (!S.accountId) return Promise.resolve();
    if (reset) S.products.offset = 0;
    var q = '/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) + '/products?limit=' +
      S.products.limit + '&offset=' + S.products.offset +
      (S.products.q ? '&q=' + encodeURIComponent(S.products.q) : '') +
      (S.products.audit_status ? '&audit_status=' + encodeURIComponent(S.products.audit_status) : '') +
      (S.products.shelf_status ? '&shelf_status=' + encodeURIComponent(S.products.shelf_status) : '');
    return apiJson(q).then(function (data) {
      S.products.items = (data && data.products) || [];
      S.products.total = (data && data.total) || 0;
      if (S.view === 'store') render();
    }).catch(function (err) { toast('产品列表加载失败：' + err.message, 'err'); });
  }

  function syncStore() {
    if (!needAccount()) return;
    confirmModal('同步会打开阿里后台把「公司资料 + 线上产品」拉下来（首次 300+ 产品约 1-2 分钟），继续吗？', function () {
      setBusy(true, '正在同步店铺与产品…');
      apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) + '/store/sync',
        { method: 'POST', body: { products: true, store: true, max_pages: 60 } })
        .then(function (data) {
          setBusy(false);
          var products = (data && data.products) || {};
          var reported = products.reported_total || 0;
          var found = products.found || 0;
          toast('同步完成：接口报告 ' + reported + ' 条 / 实际入库 ' + found +
            ' 条（新增 ' + (products.created || 0) + ' / 更新 ' + (products.updated || 0) +
            '），页数 ' + (products.pages_scanned || 0) +
            (reported && found && reported !== found ? '（差了 ' + (reported - found) + ' 条，可再点一次同步）' : ''),
            reported && found && reported !== found ? 'err' : 'ok');
          if (data && (data.store_error || data.products_error)) {
            toast('部分失败：' + (data.store_error || data.products_error), 'err');
          }
          loadStore().then(function () { loadProducts(true); });
          loadDashboard();
        })
        .catch(function (e) { setBusy(false); toast('同步失败：' + e.message, 'err'); });
    }, { okLabel: '开始同步' });
  }

  function renderStore(host) {
    var store = S.store || {};
    var stats = S.storeStats || {};
    var groups = S.storeGroups || [];
    var rows = S.products.items || [];
    var hasStore = !!(store && store.company_name);
    host.innerHTML =
      '<div class="ali-head"><div><div class="ali-h1">店铺与产品</div>' +
      '<div class="ali-hint">店铺资料来自「管理公司信息」；产品默认只抓线上（审核通过 / 已上架）</div></div>' +
      '<div class="ali-toolbar">' +
      badge('产品 ' + (stats.products || 0) + ' 条', 'info') +
      badge('线上 ' + (stats.online || 0) + ' 条', 'ok') +
      '<button type="button" class="ali-btn primary" data-act="sync">同步店铺与产品</button>' +
      '</div></div>' +
      (hasStore
        ? '<div class="ali-card"><div class="ali-card-head"><div class="ali-card-title">店铺资料</div>' +
          '<div class="ali-toolbar">' + badge('完整度 ' + (store.completeness || '—'), 'ok') +
          (store.storefront_url ? '<a class="ali-btn sm" href="' + esc(store.storefront_url) + '" target="_blank" rel="noreferrer">查看线上店铺</a>' : '') +
          '</div></div><div class="ali-card-body"><dl class="ali-kv">' +
          '<dt>公司名称</dt><dd>' + esc(store.company_name || '—') + '</dd>' +
          '<dt>注册地</dt><dd>' + esc(store.registered_place || '—') + '</dd>' +
          '<dt>运营地址</dt><dd>' + esc([store.street_address, store.city, store.province, store.country].filter(Boolean).join(' / ') || '—') + '</dd>' +
          '<dt>经营模式</dt><dd>' + esc(store.biz_type || '—') + '</dd>' +
          '<dt>主营类目</dt><dd>' + esc(store.main_category || '—') + '</dd>' +
          '<dt>主营业务</dt><dd>' + esc(store.main_business || '—') + '</dd>' +
          '<dt>更多经营产品</dt><dd>' + esc((store.more_products || []).join('、') || '—') + '</dd>' +
          '<dt>注册年份</dt><dd>' + esc(store.register_year || '—') + '</dd>' +
          '<dt>员工数</dt><dd>' + esc(store.employees || '—') + '</dd>' +
          '<dt>公司网址</dt><dd>' + esc(store.website || '—') + '</dd>' +
          '<dt>同步时间</dt><dd>' + fmtTime(store.synced_at) + '</dd>' +
          '</dl></div></div>'
        : '<div class="ali-card"><div class="ali-card-body">' +
          emptyBlock('还没有同步店铺资料', '点右上角「同步店铺与产品」，会把公司资料和线上产品一起拉进来', [
            { id: 'sync-store', label: '同步店铺与产品', kind: 'primary' }
          ]) + '</div></div>') +
      '<div class="ali-card"><div class="ali-card-head"><div class="ali-card-title">线上产品</div>' +
      '<div class="ali-toolbar">' +
      '<input class="ali-input" id="aliProductSearch" placeholder="搜索标题 / 型号 / 分组" value="' + esc(S.products.q) + '" style="width:220px">' +
      '<select class="ali-select" id="aliProductShelf">' +
      [['', '全部状态'], ['已上架', '已上架'], ['已下架', '已下架']].map(function (pair) {
        return '<option value="' + pair[0] + '"' + (S.products.shelf_status === pair[0] ? ' selected' : '') + '>' + pair[1] + '</option>';
      }).join('') + '</select>' +
      '<select class="ali-select" id="aliProductGroup">' +
      '<option value="">全部分组</option>' +
      groups.map(function (g) {
        return '<option value="' + esc(g.name || '') + '"' + (S.products.group_name === g.name ? ' selected' : '') + '>' +
          esc(g.name || '未分组') + ' (' + g.count + ')</option>';
      }).join('') + '</select>' +
      '<button type="button" class="ali-btn" data-act="search">搜索</button>' +
      '</div></div><div class="ali-card-body" style="padding:0;">' +
      (rows.length
        ? '<table class="ali-table"><thead><tr><th style="width:64px;"></th><th>产品</th><th>价格</th><th>库存</th><th>状态</th><th>月曝光</th></tr></thead><tbody>' +
          rows.map(function (p) {
            return '<tr data-product="' + esc(p.product_id) + '">' +
              '<td>' + (p.image_url ? '<img src="' + esc(p.image_url) + '" style="width:44px;height:44px;object-fit:cover;border-radius:6px;">' : '') + '</td>' +
              '<td><div class="ali-cell-main">' + esc(compact(p.subject, 90)) + '</div>' +
              '<div class="ali-cell-sub">' + esc(p.model_no || '') + (p.group_name ? ' · ' + esc(p.group_name) : '') +
              ' · ID ' + esc(p.product_id) + (p.owner ? ' · ' + esc(p.owner) : '') + '</div></td>' +
              '<td>' + esc(p.price_text || '—') +
              '<div class="ali-cell-sub">' + (p.moq ? 'MOQ ' + esc(p.moq) + ' · ' : '') +
              (p.score === null || p.score === undefined ? '' : '评分 ' + esc(p.score)) + '</div></td>' +
              '<td>' + esc(p.stock_text || '—') + '</td>' +
              '<td>' + badge(p.shelf_status || '—', p.shelf_status === '已上架' ? 'ok' : '') +
              (p.audit_status ? ' ' + badge(p.audit_status, p.audit_status === '审核通过' ? 'info' : 'warn') : '') + '</td>' +
              '<td>' + (p.monthly_exposure === null || p.monthly_exposure === undefined ? '—' : p.monthly_exposure) + '</td></tr>';
          }).join('') + '</tbody></table>'
        : emptyBlock('还没有产品数据', hasStore ? '点右上角同步，或先同步一次' : '先同步店铺与产品', [])) +
      '</div><div class="ali-pager">共 ' + (S.products.total || rows.length) + ' 条 · 第 ' +
      (Math.floor(S.products.offset / S.products.limit) + 1) + ' 页' +
      '<button type="button" class="ali-btn sm" data-act="prev">上一页</button>' +
      '<button type="button" class="ali-btn sm" data-act="next">下一页</button></div></div>';

    host.querySelector('[data-act="sync"]').addEventListener('click', syncStore);
    host.querySelector('[data-act="search"]').addEventListener('click', function () {
      S.products.q = $('aliProductSearch').value.trim();
      S.products.shelf_status = $('aliProductShelf').value;
      S.products.group_name = $('aliProductGroup').value;
      loadProducts(true);
    });
    host.querySelector('[data-act="prev"]').addEventListener('click', function () {
      S.products.offset = Math.max(0, S.products.offset - S.products.limit);
      loadProducts(false);
    });
    host.querySelector('[data-act="next"]').addEventListener('click', function () {
      if (S.products.offset + S.products.limit < S.products.total) {
        S.products.offset += S.products.limit;
        loadProducts(false);
      }
    });
    host.querySelectorAll('[data-product]').forEach(function (el) {
      el.addEventListener('click', function () { showProductDrawer(el.getAttribute('data-product')); });
    });
    bindEmptyActions(host, { 'sync-store': syncStore });
  }

  function showProductDrawer(productId) {
    var product = null;
    (S.products.items || []).forEach(function (p) { if (String(p.product_id) === String(productId)) product = p; });
    if (!product) return;
    openDrawer({
      title: product.model_no || product.product_id,
      sub: esc(compact(product.subject, 90)),
      body:
        '<div class="ali-card"><div class="ali-card-body">' +
        (product.image_url ? '<img src="' + esc(product.image_url) + '" style="width:100%;max-width:320px;border-radius:10px;">' : '') +
        '<dl class="ali-kv" style="margin-top:10px;">' +
        '<dt>产品 ID</dt><dd>' + esc(product.product_id) + '</dd>' +
        '<dt>型号 / 分组</dt><dd>' + esc((product.model_no || '—') + ' / ' + (product.group_name || '—')) + '</dd>' +
        '<dt>类型</dt><dd>' + esc(product.product_type || '—') + '</dd>' +
        '<dt>价格</dt><dd>' + esc(product.price_text || '—') + '</dd>' +
        '<dt>MOQ</dt><dd>' + esc(product.moq || '—') + (product.second_order_quantity ? ' · 二次起订 ' + esc(product.second_order_quantity) : '') + '</dd>' +
        '<dt>库存</dt><dd>' + esc(product.stock_text || '—') + '</dd>' +
        '<dt>状态</dt><dd>' + esc((product.audit_status || '—') + ' / ' + (product.shelf_status || '—')) + (product.note ? ' · ' + esc(product.note) : '') + '</dd>' +
        '<dt>点击 / 访客</dt><dd>' + (product.click_num === null || product.click_num === undefined ? '—' : product.click_num) +
        ' / ' + (product.visitor_cnt === null || product.visitor_cnt === undefined ? '—' : product.visitor_cnt) + '</dd>' +
        '<dt>评分</dt><dd>' + (product.score === null || product.score === undefined ? '—' : product.score) + '</dd>' +
        '<dt>负责人</dt><dd>' + esc(product.owner || '—') + '</dd>' +
        '<dt>更新时间</dt><dd>' + esc(product.gmt_modified || '—') + '</dd>' +
        '<dt>关键词</dt><dd>' + esc(compact(product.keywords || '', 160) || '—') + '</dd>' +
        '<dt>标签</dt><dd>' + esc((product.tags || []).join('、') || '—') + '</dd>' +
        '</dl></div></div>',
      actions: [
        product.detail_url ? { label: '打开线上页', onClick: function () { window.open(product.detail_url, '_blank'); } } : null,
        product.edit_url ? { label: '后台编辑', onClick: function () { window.open(product.edit_url, '_blank'); } } : null,
        { label: '关闭', onClick: closeDrawer }
      ].filter(Boolean)
    });
  }

  function loadDocs() {
    if (!needAccount()) return Promise.resolve();
    return apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) + '/training-docs')
      .then(function (data) {
        // 接口返回 {"ok":true,"items":[...]}：三种字段名都兼容，避免"角标有数字、列表空的"
        S.docs = (data && (data.items || data.docs || data.documents)) || [];
        if (S.view === 'kb') render();
      })
      .catch(function (err) { toast('资料加载失败：' + err.message, 'err'); });
  }

  var DOC_KINDS = {
    product: '产品资料',
    faq: 'FAQ',
    price: '报价表',
    delivery: '交付/物流',
    script: '优秀话术',
    ban: '禁用话术'
  };

  function renderKb(host) {
    host.innerHTML =
      '<div class="ali-head"><div><div class="ali-h1">知识库</div>' +
      '<div class="ali-hint">商家只上传真资料：产品/FAQ/报价/交付/禁忌，AI 回复都要基于这里</div></div>' +
      '<div class="ali-toolbar">' +
      '<button type="button" class="ali-btn" data-act="reload">刷新</button>' +
      '<button type="button" class="ali-btn primary" data-act="upload">上传资料</button>' +
      '</div></div>' +
      '<div class="ali-card"><div class="ali-card-body" style="padding:0;">' +
      (S.docs.length
        ? '<table class="ali-table"><thead><tr><th>标题</th><th>类型</th><th>来源</th><th>时间</th><th></th></tr></thead><tbody>' +
          S.docs.map(function (d) {
            return '<tr><td><div class="ali-cell-main">' + esc(d.title || d.filename || '—') + '</div>' +
              '<div class="ali-cell-sub">' + esc(compact(d.content || '', 80)) + '</div></td>' +
              '<td>' + badge(DOC_KINDS[d.kind] || d.kind || '—', d.kind === 'ban' ? 'err' : 'info') + '</td>' +
              '<td>' + esc(d.filename || '手动输入') + '</td>' +
              '<td>' + fmtTime(d.created_at) + '</td>' +
              '<td style="text-align:right;"><button type="button" class="ali-btn sm danger" data-doc-del="' + esc(d.id) + '">删除</button></td></tr>';
          }).join('') + '</tbody></table>'
        : emptyBlock('知识库还是空的', '先上传产品资料 + FAQ + 真实报价表，回复质量立刻不一样', [
          { id: 'upload', label: '上传资料', kind: 'primary' }
        ])) +
      '</div></div>';
    host.querySelector('[data-act="reload"]').addEventListener('click', function () { loadDocs(); });
    host.querySelector('[data-act="upload"]').addEventListener('click', showDocModal);
    bindEmptyActions(host, { upload: showDocModal });
    host.querySelectorAll('[data-doc-del]').forEach(function (el) {
      el.addEventListener('click', function (ev) {
        ev.stopPropagation();
        var id = el.getAttribute('data-doc-del');
        confirmModal('删除这条资料？', function () {
          apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) +
            '/training-docs/' + encodeURIComponent(id), { method: 'DELETE' })
            .then(function () { toast('已删除', 'ok'); loadDocs(); })
            .catch(function (e) { toast('删除失败：' + e.message, 'err'); });
        }, { danger: true, okLabel: '删除' });
      });
    });
  }

  function showDocModal() {
    if (!needAccount()) return;
    openModal({
      title: '上传知识库资料',
      sub: '文本 / Word / PDF 都行；报价表建议用表格截成 PDF 或直接贴文本',
      width: 'wide',
      body:
        '<div class="ali-grid-2">' +
        '<div class="ali-field"><div class="ali-field-label">类型</div><select class="ali-input" id="aliDocKind">' +
        Object.keys(DOC_KINDS).map(function (k) { return '<option value="' + k + '">' + DOC_KINDS[k] + '</option>'; }).join('') +
        '</select></div>' +
        '<div class="ali-field"><div class="ali-field-label">标题</div><input class="ali-input" id="aliDocTitle" placeholder="例如：2026 报价表 / 常见问题"></div>' +
        '</div>' +
        '<div class="ali-field"><div class="ali-field-label">正文（可留空，用文件）</div><textarea class="ali-input" id="aliDocContent" placeholder="直接贴 FAQ / 话术 / 报价要点"></textarea></div>' +
        '<div class="ali-field"><div class="ali-field-label">文件（可选）</div><input class="ali-input" type="file" id="aliDocFile"></div>',
      actions: [
        { label: '取消', onClick: closeModal },
        {
          label: '上传并入库',
          kind: 'primary',
          onClick: function () {
            var form = new FormData();
            form.append('kind', $('aliDocKind').value);
            form.append('title', $('aliDocTitle').value.trim());
            form.append('content', $('aliDocContent').value.trim());
            var file = $('aliDocFile').files[0];
            if (file) form.append('file', file);
            if (!form.get('title') && !file && !$('aliDocContent').value.trim()) {
              toast('标题/正文/文件至少填一个', 'err');
              return;
            }
            apiUpload('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) + '/training-docs', form)
              .then(function () { closeModal(); toast('已入库', 'ok'); loadDocs(); })
              .catch(function (e) { toast('上传失败：' + e.message, 'err'); });
          }
        }
      ]
    });
  }

  /* ------------------------------------------------ 人设与话术 */

  function loadSummaries() {
    if (!needAccount()) return Promise.resolve();
    return apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) + '/summaries')
      .then(function (data) {
        S.summaries = (data && (data.summaries || data.items)) || [];
        if (S.view === 'persona') render();
      })
      .catch(function (err) { toast('策略加载失败：' + err.message, 'err'); });
  }

  function renderPersona(host) {
    var persona = (S.config && S.config.persona) || {};
    host.innerHTML =
      '<div class="ali-head"><div><div class="ali-h1">人设与话术</div>' +
      '<div class="ali-hint">AI 用这里的人设说话；策略只是风格与顺序提示，内容以知识库为准</div></div>' +
      '<div class="ali-toolbar">' +
      '<button type="button" class="ali-btn" data-act="reload">刷新策略</button>' +
      '<button type="button" class="ali-btn primary" data-act="edit-persona">编辑人设</button>' +
      '</div></div>' +
      '<div class="ali-card"><div class="ali-card-head"><div class="ali-card-title">当前人设</div>' +
      badge(persona.name ? '已配置' : '未配置', persona.name ? 'ok' : 'warn') + '</div>' +
      '<div class="ali-card-body"><dl class="ali-kv">' +
      '<dt>姓名 / 职位</dt><dd>' + esc((persona.name || '—') + ' ' + (persona.title || '')) + '</dd>' +
      '<dt>公司</dt><dd>' + esc(persona.company || '—') + '</dd>' +
      '<dt>风格</dt><dd>' + esc(persona.style || '—') + '</dd>' +
      '<dt>索取顺序</dt><dd>' + esc((persona.ask_order || []).join(' → ') || '—') + '</dd>' +
      '</dl></div></div>' +
      '<div class="ali-card"><div class="ali-card-head"><div class="ali-card-title">回复策略</div>' +
      '<button type="button" class="ali-btn sm primary" data-act="analyze">生成回复策略</button></div>' +
      '<div class="ali-card-body" style="padding:0;">' +
      (S.summaries.length
        ? '<table class="ali-table"><thead><tr><th>策略</th><th>来源条数</th><th>时间</th><th></th></tr></thead><tbody>' +
          S.summaries.map(function (s) {
            return '<tr><td><div class="ali-cell-main">#' + esc(s.id) + ' ' + esc(s.summary_type || '') + '</div>' +
              '<div class="ali-cell-sub">' + esc(compact(s.content || '', 140)) + '</div></td>' +
              '<td>' + (s.source_count || 0) + '</td><td>' + fmtTime(s.created_at) + '</td>' +
              '<td style="text-align:right;"><button type="button" class="ali-btn sm" data-enable="' + esc(s.id) + '">启用</button></td></tr>';
          }).join('') + '</tbody></table>'
        : emptyBlock('还没有策略', '上传资料后点「生成回复策略」，或者直接用接待台跑一轮', [])) +
      '</div></div>';
    host.querySelector('[data-act="reload"]').addEventListener('click', function () { loadSummaries(); });
    host.querySelector('[data-act="edit-persona"]').addEventListener('click', showPersonaModal);
    host.querySelector('[data-act="analyze"]').addEventListener('click', function () {
      toast('正在生成策略…');
      apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) + '/analyze',
        { method: 'POST', body: { doc_ids: [] } })
        .then(function () { toast('策略已生成', 'ok'); loadSummaries(); })
        .catch(function (e) { toast('生成失败：' + e.message, 'err'); });
    });
    host.querySelectorAll('[data-enable]').forEach(function (el) {
      el.addEventListener('click', function () {
        apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) +
          '/summaries/' + encodeURIComponent(el.getAttribute('data-enable')) + '/enable',
          { method: 'POST', body: {} })
          .then(function () { toast('已启用', 'ok'); loadSummaries(); })
          .catch(function (e) { toast('启用失败：' + e.message, 'err'); });
      });
    });
  }

  function showPersonaModal() {
    var persona = (S.config && S.config.persona) || {};
    openModal({
      title: '编辑人设',
      sub: 'AI 对外就是这个人，越具体越像真人',
      body:
        '<div class="ali-grid-2">' +
        '<div class="ali-field"><div class="ali-field-label">姓名</div><input class="ali-input" id="aliPersonaName" value="' + esc(persona.name || '') + '"></div>' +
        '<div class="ali-field"><div class="ali-field-label">职位</div><input class="ali-input" id="aliPersonaTitle" value="' + esc(persona.title || '') + '"></div>' +
        '</div>' +
        '<div class="ali-field"><div class="ali-field-label">公司</div><input class="ali-input" id="aliPersonaCompany" value="' + esc(persona.company || '') + '"></div>' +
        '<div class="ali-field"><div class="ali-field-label">风格</div><textarea class="ali-input" id="aliPersonaStyle">' + esc(persona.style || '') + '</textarea></div>' +
        '<div class="ali-field"><div class="ali-field-label">索取顺序（逗号分隔）</div><input class="ali-input" id="aliPersonaAsk" value="' + esc((persona.ask_order || []).join(',')) + '"></div>',
      actions: [
        { label: '取消', onClick: closeModal },
        {
          label: '保存',
          kind: 'primary',
          onClick: function () {
            var payload = {
              persona: {
                name: $('aliPersonaName').value.trim(),
                title: $('aliPersonaTitle').value.trim(),
                company: $('aliPersonaCompany').value.trim(),
                style: $('aliPersonaStyle').value.trim(),
                ask_order: $('aliPersonaAsk').value.split(',').map(function (x) { return x.trim(); }).filter(Boolean)
              }
            };
            apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) + '/reception-config',
              { method: 'POST', body: payload })
              .then(function (data) {
                S.config = (data && data.config) || S.config;
                closeModal();
                toast('已保存', 'ok');
                render();
              })
              .catch(function (e) { toast('保存失败：' + e.message, 'err'); });
          }
        }
      ]
    });
  }

  /* ------------------------------------------------ 排期与红线 */

  function loadConfig() {
    if (!S.accountId) return Promise.resolve();
    return apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) + '/reception-config')
      .then(function (data) {
        S.config = (data && data.config) || S.config;
        refreshHeaderStatus();
        if (S.view === 'rules') render();
        return S.config;
      })
      .catch(function (err) { toast('配置加载失败：' + err.message, 'err'); });
  }

  function renderRules(host) {
    var cfg = S.config || {};
    var num = function (key, fallback) { return cfg[key] === undefined || cfg[key] === null ? fallback : cfg[key]; };
    var secs = Number(cfg.next_scan_seconds || 1800);
    host.innerHTML =
      '<div class="ali-head"><div><div class="ali-h1">排期与红线</div>' +
      '<div class="ali-hint">阿里考核 1 小时回复率：常态 30 分钟必扫一遍；客户在线自动提到 60s/30s</div></div>' +
      '<div class="ali-toolbar">' +
      badge('下次扫描间隔 ' + (secs >= 60 ? Math.round(secs / 60) + ' 分钟' : secs + ' 秒'), 'info') +
      (cfg.in_work_window === false ? badge('当前不在工作时段', 'warn') : badge('工作时段内', 'ok')) +
      '</div></div>' +
      '<div class="ali-card"><div class="ali-card-head"><div class="ali-card-title">总开关</div>' +
      '<div class="ali-toolbar">' +
      '<label class="ali-switch"><input type="checkbox" id="aliCfgEnabled"' + (cfg.enabled ? ' checked' : '') + '> 开启 AI 接管</label>' +
      '<label class="ali-switch"><input type="checkbox" id="aliCfgDryRun"' + (cfg.dry_run ? ' checked' : '') + '> 演练模式（只生成不发）</label>' +
      '</div></div><div class="ali-card-body"><div class="ali-note">' +
      '先把「演练模式」跑一周：每天看队列里 AI 会怎么回，再关掉演练真发。' +
      '</div></div></div>' +
      '<div class="ali-card"><div class="ali-card-head"><div class="ali-card-title">排期</div></div><div class="ali-card-body">' +
      '<div class="ali-grid-3">' +
      '<div class="ali-field"><div class="ali-field-label">常态间隔（分钟）</div><input class="ali-input" id="aliCfgInterval" value="' + esc(num('interval_minutes', 30)) + '"></div>' +
      '<div class="ali-field"><div class="ali-field-label">客户在线（秒）</div><input class="ali-input" id="aliCfgOnline" value="' + esc(num('online_interval_seconds', 60)) + '"></div>' +
      '<div class="ali-field"><div class="ali-field-label">正在输入（秒）</div><input class="ali-input" id="aliCfgHot" value="' + esc(num('hot_interval_seconds', 30)) + '"></div>' +
      '<div class="ali-field"><div class="ali-field-label">工作时段开始</div><input class="ali-input" id="aliCfgWs" value="' + esc(num('work_window_start', '08:00')) + '"></div>' +
      '<div class="ali-field"><div class="ali-field-label">工作时段结束</div><input class="ali-input" id="aliCfgWe" value="' + esc(num('work_window_end', '23:00')) + '"></div>' +
      '<div class="ali-field"><div class="ali-field-label">待处理时间窗（天，0=全部）</div><input class="ali-input" id="aliCfgWindow" value="' + esc(num('pending_window_days', 30)) + '"></div>' +
      '</div>' +
      '<div class="ali-note">时间窗只影响接待台/跑一轮的"待处理"口径：比如设 30 天，3 个月前的老询盘就不再算待回复，' +
      '会显示成「历史」引导去公海池激活（避免"14 条待办其实都是几个月前的"）。</div>' +
      '</div></div>' +
      '<div class="ali-card"><div class="ali-card-head"><div class="ali-card-title">红线</div></div><div class="ali-card-body">' +
      '<div class="ali-grid-3">' +
      '<div class="ali-field"><div class="ali-field-label">轮次上限</div><input class="ali-input" id="aliCfgTurns" value="' + esc(num('max_turns', 8)) + '"></div>' +
      '<div class="ali-field"><div class="ali-field-label">回复字数上限</div><input class="ali-input" id="aliCfgChars" value="' + esc(num('max_chars', 380)) + '"></div>' +
      '<div class="ali-field"><div class="ali-field-label">延迟下限（秒）</div><input class="ali-input" id="aliCfgDelayMin" value="' + esc(num('delay_min_seconds', 25)) + '"></div>' +
      '<div class="ali-field"><div class="ali-field-label">延迟上限（秒）</div><input class="ali-input" id="aliCfgDelayMax" value="' + esc(num('delay_max_seconds', 90)) + '"></div>' +
      '</div>' +
      '<div class="ali-field"><div class="ali-field-label">命中即转人工（逗号分隔）</div><textarea class="ali-input" id="aliCfgHandoff">' + esc((cfg.handoff_triggers || []).join(',')) + '</textarea></div>' +
      '<div class="ali-field"><div class="ali-field-label">禁词（逗号分隔）</div><textarea class="ali-input" id="aliCfgBanned">' + esc((cfg.banned_words || []).join(',')) + '</textarea></div>' +
      '<div class="ali-inline">' +
      '<label class="ali-switch"><input type="checkbox" id="aliCfgSmall"' + (cfg.accept_small_orders ? ' checked' : '') + '> 接小单</label>' +
      '<label class="ali-switch"><input type="checkbox" id="aliCfgPersonal"' + (cfg.accept_personal_orders ? ' checked' : '') + '> 接个人订单</label>' +
      '</div>' +
      '</div></div>' +
      '<div class="ali-card"><div class="ali-card-head"><div class="ali-card-title">已读未回 · 换角度触达</div>' +
      '<div class="ali-toolbar">' +
      '<label class="ali-switch"><input type="checkbox" id="aliCfgNudgeEnabled"' + (cfg.read_no_reply_enabled ? ' checked' : '') + '> 开启撩动</label>' +
      '</div></div><div class="ali-card-body">' +
      '<div class="ali-grid-2">' +
      '<div class="ali-field"><div class="ali-field-label">最多触达次数</div><input class="ali-input" id="aliCfgNudgeMax" value="' + esc(num('nudge_max', 3)) + '"></div>' +
      '<div class="ali-field"><div class="ali-field-label">字节数上限</div><input class="ali-input" id="aliCfgNudgeChars" value="' + esc(num('nudge_max_chars', 240)) + '"></div>' +
      '</div>' +
      '<div class="ali-field"><div class="ali-field-label">间隔（分钟，逗号分隔；第 1/2/3 次分别等多久）</div>' +
      '<input class="ali-input" id="aliCfgNudgeIntervals" value="' + esc((cfg.nudge_intervals_minutes || [120, 1440, 4320]).join(',')) + '"></div>' +
      '<div class="ali-field"><div class="ali-field-label">角度列表（每行一个：标签｜目标）</div>' +
      '<textarea class="ali-input" id="aliCfgNudgeAngles">' +
      esc((cfg.nudge_angles || []).map(function (a) { return (a.label || '') + '｜' + (a.goal || ''); }).join('\n')) +
      '</textarea></div>' +
      '<div class="ali-note">逻辑：我方最后一条发出后对方一直不回 → 间隔到点就用<b>不同角度</b>再触达一次（不重复上一轮角度），' +
      '超次数后标为「休眠」并建议进公海池；期间对方一回复就回到正常接待。</div>' +
      '</div></div>' +
      '<div class="ali-inline" style="justify-content:flex-end;margin-top:12px;">' +
      '<button type="button" class="ali-btn primary" data-act="save">保存配置</button>' +
      '</div>';
    host.querySelector('[data-act="save"]').addEventListener('click', function () {
      var splitList = function (id) {
        return ($(id).value || '').split(',').map(function (x) { return x.trim(); }).filter(Boolean);
      };
      var angleLines = ($('aliCfgNudgeAngles').value || '').split('\n').map(function (line) {
        var text = line.trim();
        if (!text) return null;
        var parts = text.split('｜');
        var label = (parts[0] || '').trim();
        var goal = (parts[1] || '').trim();
        if (!label && !goal) return null;
        return { key: label || goal, label: label || goal, goal: goal || label };
      }).filter(Boolean);
      var payload = {
        enabled: $('aliCfgEnabled').checked,
        dry_run: $('aliCfgDryRun').checked,
        interval_minutes: Number($('aliCfgInterval').value) || 30,
        online_interval_seconds: Number($('aliCfgOnline').value) || 60,
        hot_interval_seconds: Number($('aliCfgHot').value) || 30,
        work_window_start: $('aliCfgWs').value.trim() || '08:00',
        work_window_end: $('aliCfgWe').value.trim() || '23:00',
        pending_window_days: Number($('aliCfgWindow').value) || 0,
        max_turns: Number($('aliCfgTurns').value) || 8,
        max_chars: Number($('aliCfgChars').value) || 380,
        delay_min_seconds: Number($('aliCfgDelayMin').value) || 25,
        delay_max_seconds: Number($('aliCfgDelayMax').value) || 90,
        handoff_triggers: splitList('aliCfgHandoff'),
        banned_words: splitList('aliCfgBanned'),
        accept_small_orders: $('aliCfgSmall').checked,
        accept_personal_orders: $('aliCfgPersonal').checked
        ,
        read_no_reply_enabled: $('aliCfgNudgeEnabled').checked,
        nudge_max: Number($('aliCfgNudgeMax').value) || 0,
        nudge_max_chars: Number($('aliCfgNudgeChars').value) || 240,
        nudge_intervals_minutes: splitList('aliCfgNudgeIntervals').map(Number).filter(function (n) { return n > 0; }),
        nudge_angles: angleLines
      };
      apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) + '/reception-config',
        { method: 'POST', body: payload })
        .then(function (data) {
          S.config = (data && data.config) || S.config;
          refreshHeaderStatus();
          toast('已保存', 'ok');
          render();
        })
        .catch(function (e) { toast('保存失败：' + e.message, 'err'); });
    });
  }

  /* ------------------------------------------------ 跑一轮 */

  function showRunModal(dryRun) {
    if (!needAccount()) return;
    openModal({
      title: dryRun ? '演练一轮（只生成不发送）' : '真实跑一轮',
      sub: dryRun ? '用于在速腾环境看 AI 会怎么回、像不像真人' : '会真的通过浏览器把消息发给客户',
      body:
        '<div class="ali-field"><div class="ali-field-label">本轮最多处理</div>' +
        '<input class="ali-input" id="aliRunLimit" value="5"></div>' +
        '<div class="ali-note">当前红线：轮次上限 ' + esc((S.config && S.config.max_turns) || 8) +
        ' · 字数上限 ' + esc((S.config && S.config.max_chars) || 380) +
        ' · 延迟 ' + esc((S.config && S.config.delay_min_seconds) || 25) + '-' + esc((S.config && S.config.delay_max_seconds) || 90) + 's' +
        (dryRun ? '' : ' · 命中转人工/禁词会自动跳过') + '</div>',
      actions: [
        { label: '取消', onClick: closeModal },
        {
          label: dryRun ? '开始演练' : '开始发送',
          kind: dryRun ? 'primary' : 'danger',
          onClick: function () {
            var limit = Number($('aliRunLimit').value) || 5;
            closeModal();
            setBusy(true);
            toast(dryRun ? '演练中…' : '开始真实发送…');
            apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) + '/reception/run',
              { method: 'POST', body: { dry_run: !!dryRun, limit: limit } })
              .then(function (data) {
                setBusy(false);
                showRunResult(data || {}, dryRun);
                loadDashboard();
                loadInquiries(true);
              })
              .catch(function (e) { setBusy(false); toast('执行失败：' + e.message, 'err'); });
          }
        }
      ]
    });
  }

  function showRunResult(data, dryRun) {
    var items = data.items || [];
    openModal({
      title: dryRun ? '演练结果（未发送）' : '发送结果',
      width: 'wide',
      sub: '预览 ' + (data.previewed || 0) + ' 条 · 已发 ' + (data.sent || 0) + ' 条 · 跳过 ' + (data.skipped || 0) +
        ' 条 · 转人工 ' + (data.handoff || 0) + ' 条 · 拦禁词 ' + (data.blocked || 0) + ' 条',
      body: items.length
        ? items.map(function (it) {
          var kind = it.status === 'sent' ? 'ok' : it.status === 'dry_run' ? 'info' : it.status === 'handoff' ? 'err' : '';
          return '<div class="ali-card" style="margin-bottom:8px;"><div class="ali-card-body">' +
            '<div class="ali-inline" style="gap:6px;flex-wrap:wrap;">' + badge(it.status || '', kind) +
            (it.kind === 'nudge' ? badge('换角度触达 第 ' + (it.nudge_index || 1) + ' 次 · ' + (it.angle || ''), 'violet') : '') +
            badge(it.buyer || it.inquiry_id, '') + '</div>' +
            (it.draft ? '<div style="margin-top:8px;white-space:pre-wrap;">' + esc(it.draft) + '</div>' : '') +
            (it.reason ? '<div class="ali-note" style="margin-top:6px;">' + esc(it.reason) + '</div>' : '') +
            (it.error ? '<div class="ali-note" style="margin-top:6px;color:#dc2626;">' + esc(it.error) + '</div>' : '') +
            '</div></div>';
        }).join('')
        : '<div class="ali-note">本轮没有可处理的询盘（可能都回过、在冷却或已人工接管）。</div>',
      actions: [{ label: '知道了', kind: 'primary', onClick: closeModal }]
    });
  }

  /* ------------------------------------------------ 公海池 */

  function loadPool() {
    if (!needAccount()) return Promise.resolve();
    return apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) +
      '/public-pool/targets' + (S.pool.status ? '?status=' + encodeURIComponent(S.pool.status) : ''))
      .then(function (data) {
        S.pool.items = (data && data.targets) || [];
        if (S.view === 'pool') render();
      })
      .catch(function (err) { toast('公海池加载失败：' + err.message, 'err'); });
  }

  function renderPool(host) {
    var rows = S.pool.items || [];
    host.innerHTML =
      '<div class="ali-head"><div><div class="ali-h1">公海池激活</div>' +
      '<div class="ali-hint">主/子账号公海池都能用；标准话术连发三次，对方回复后算商机</div></div>' +
      '<div class="ali-toolbar">' +
      '<select class="ali-select" id="aliPoolStatus">' +
      ['', 'pending', 'touching', 'contacted', 'replied', 'converted'].map(function (s) {
        return '<option value="' + s + '"' + (S.pool.status === s ? ' selected' : '') + '>' +
          (s === '' ? '全部状态' : s) + '</option>';
      }).join('') + '</select>' +
      '<button type="button" class="ali-btn" data-act="reload">刷新</button>' +
      '<button type="button" class="ali-btn primary" data-act="import">导入公海客户</button>' +
      '</div></div>' +
      '<div class="ali-card"><div class="ali-card-body" style="padding:0;">' +
      (rows.length
        ? '<table class="ali-table"><thead><tr><th>客户</th><th>国家 / 产品线</th><th>进度</th><th>下次触达</th><th></th></tr></thead><tbody>' +
          rows.map(function (r) {
            return '<tr data-pool="' + esc(r.id) + '">' +
              '<td><div class="ali-cell-main">' + esc(r.buyer_name || r.company_name || '—') + '</div>' +
              '<div class="ali-cell-sub">' + esc(r.company_name || '') + '</div></td>' +
              '<td><div>' + esc(r.country || '—') + '</div><div class="ali-cell-sub">' + esc(r.product_line || '') + '</div></td>' +
              '<td>' + badge('已触达 ' + (r.touch_count || 0) + '/3', r.touch_count >= 3 ? 'warn' : 'info') +
              badge(r.status || '', r.status === 'replied' || r.status === 'converted' ? 'ok' : '') + '</td>' +
              '<td>' + fmtTime(r.next_touch_at) + '</td>' +
              '<td style="text-align:right;"><button type="button" class="ali-btn sm" data-pool-act="plan" data-id="' + esc(r.id) + '">生成话术</button>' +
              '<button type="button" class="ali-btn sm primary" data-pool-act="touch" data-id="' + esc(r.id) + '">触达</button></td></tr>';
          }).join('') + '</tbody></table>'
        : emptyBlock('公海池还没有客户', '把主/子账号里"聊过没成交"的客户贴进来，按 T0/T+2d/T+5d 连发三次', [
          { id: 'import', label: '导入公海客户', kind: 'primary' }
        ])) +
      '</div></div>' +
      '<div class="ali-card"><div class="ali-card-body"><div class="ali-note">' +
      '说明：阿里公海池页面自动抓取还没接选择器（需要真实 DOM）；现在支持①手动导入 ②把成都公司对话框插件的数据源接进同一张 targets 表。' +
      '</div></div></div>';
    host.querySelector('[data-act="reload"]').addEventListener('click', loadPool);
    host.querySelector('[data-act="import"]').addEventListener('click', showPoolImportModal);
    host.querySelector('#aliPoolStatus').addEventListener('change', function (e) {
      S.pool.status = e.target.value;
      loadPool();
    });
    host.querySelectorAll('[data-pool-act]').forEach(function (el) {
      el.addEventListener('click', function (ev) {
        ev.stopPropagation();
        var id = el.getAttribute('data-id');
        if (el.getAttribute('data-pool-act') === 'plan') {
          apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) +
            '/public-pool/targets/' + encodeURIComponent(id) + '/plan', { method: 'POST', body: {} })
            .then(function (data) {
              var plan = (data && data.plan) || [];
              openModal({
                title: '三次触达话术',
                body: plan.map(function (p) {
                  return '<div class="ali-evidence"><div class="ali-evidence-title">' + esc(p.step) + '</div>' +
                    '<div class="ali-evidence-snip">' + esc(p.content) + '</div></div>';
                }).join(''),
                actions: [{ label: '关闭', onClick: closeModal }]
              });
              loadPool();
            })
            .catch(function (e) { toast('生成失败：' + e.message, 'err'); });
        } else {
          showPoolTouchModal(id);
        }
      });
    });
    host.querySelectorAll('[data-pool]').forEach(function (el) {
      el.addEventListener('click', function () { showPoolDrawer(el.getAttribute('data-pool')); });
    });
    bindEmptyActions(host, { import: showPoolImportModal });
  }

  function showPoolImportModal() {
    openModal({
      title: '导入公海池客户',
      sub: '一行一个：买家名 | 公司 | 国家 | 产品线 | 聊天链接（可空）',
      width: 'wide',
      body:
        '<div class="ali-field"><div class="ali-field-label">粘贴数据</div>' +
        '<textarea class="ali-input" id="aliPoolRows" style="min-height:180px;" placeholder="John | ABC Trading | US | Rugged PDA | https://message.alibaba.com/..."></textarea></div>' +
        '<div class="ali-note">聊天链接填了才能自动触达（打开对方窗口）；不填只能生成话术人工发。</div>',
      actions: [
        { label: '取消', onClick: closeModal },
        {
          label: '导入',
          kind: 'primary',
          onClick: function () {
            var text = ($('aliPoolRows').value || '').trim();
            if (!text) { toast('先贴点数据', 'err'); return; }
            var rows = text.split('\n').map(function (line) {
              var parts = line.split('|').map(function (x) { return x.trim(); });
              if (!parts[0]) return null;
              return {
                buyer_name: parts[0] || '',
                company_name: parts[1] || '',
                country: parts[2] || '',
                product_line: parts[3] || '',
                chat_url: parts[4] || ''
              };
            }).filter(Boolean);
            apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) + '/public-pool/targets',
              { method: 'POST', body: { rows: rows, source_scope: 'main' } })
              .then(function (data) {
                closeModal();
                toast('导入完成：新增 ' + (data.created || 0) + '，更新 ' + (data.updated || 0), 'ok');
                loadPool();
              })
              .catch(function (e) { toast('导入失败：' + e.message, 'err'); });
          }
        }
      ]
    });
  }

  function showPoolTouchModal(targetId) {
    var target = null;
    (S.pool.items || []).forEach(function (item) { if (String(item.id) === String(targetId)) target = item; });
    if (!target) return;
    var plan = target.plan || [];
    var nextStep = Math.min(3, (target.touch_count || 0) + 1);
    var preset = (plan[nextStep - 1] || {}).content || '';
    openModal({
      title: '触达 ' + (target.buyer_name || target.company_name || '') + '（第 ' + nextStep + '/3 次）',
      sub: '真实发送会打开对方聊天窗口；建议先演练',
      body:
        '<div class="ali-field"><div class="ali-field-label">话术</div>' +
        '<textarea class="ali-input" id="aliPoolTouchText" style="min-height:120px;">' + esc(preset) + '</textarea></div>' +
        (target.chat_url ? '<div class="ali-note">聊天链接：' + esc(compact(target.chat_url, 90)) + '</div>'
          : '<div class="ali-note" style="color:#dc2626;">这个目标没有聊天链接，真实发送会失败；可以先只演练。</div>'),
      actions: [
        { label: '取消', onClick: closeModal },
        {
          label: '演练',
          onClick: function () {
            apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) +
              '/public-pool/targets/' + encodeURIComponent(targetId) + '/touch',
              { method: 'POST', body: { step: nextStep, content: $('aliPoolTouchText').value.trim(), dry_run: true } })
              .then(function () { closeModal(); toast('演练通过（未发送）', 'ok'); })
              .catch(function (e) { toast('演练失败：' + e.message, 'err'); });
          }
        },
        {
          label: '发送',
          kind: 'primary',
          onClick: function () {
            apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) +
              '/public-pool/targets/' + encodeURIComponent(targetId) + '/touch',
              { method: 'POST', body: { step: nextStep, content: $('aliPoolTouchText').value.trim(), dry_run: false } })
              .then(function (data) {
                if (data && data.sent === false) { toast(data.message || '未发送', 'err'); return; }
                closeModal();
                toast('已触达第 ' + (data.touch_count || nextStep) + ' 次', 'ok');
                loadPool();
              })
              .catch(function (e) { toast('发送失败：' + e.message, 'err'); });
          }
        }
      ]
    });
  }

  function showPoolDrawer(targetId) {
    var target = null;
    (S.pool.items || []).forEach(function (item) { if (String(item.id) === String(targetId)) target = item; });
    if (!target) return;
    openDrawer({
      title: target.buyer_name || target.company_name || '公海客户',
      sub: esc(target.country || '') + ' · ' + esc(target.product_line || ''),
      body:
        '<div class="ali-card"><div class="ali-card-body"><dl class="ali-kv">' +
        '<dt>公司</dt><dd>' + esc(target.company_name || '—') + '</dd>' +
        '<dt>状态</dt><dd>' + esc(target.status || '—') + '</dd>' +
        '<dt>已触达</dt><dd>' + (target.touch_count || 0) + ' / 3</dd>' +
        '<dt>上次触达</dt><dd>' + fmtTime(target.last_touch_at) + '</dd>' +
        '<dt>下次触达</dt><dd>' + fmtTime(target.next_touch_at) + '</dd>' +
        '<dt>聊天链接</dt><dd>' + esc(target.chat_url || '—') + '</dd>' +
        '</dl></div></div>' +
        '<div class="ali-card"><div class="ali-card-head"><div class="ali-card-title">触达话术</div>' +
        '<button type="button" class="ali-btn sm" data-act="plan">生成/刷新</button></div>' +
        '<div class="ali-card-body">' +
        ((target.plan || []).length
          ? target.plan.map(function (p) {
            return '<div class="ali-evidence"><div class="ali-evidence-title">' + esc(p.step) + '</div>' +
              '<div class="ali-evidence-snip">' + esc(p.content) + '</div></div>';
          }).join('')
          : '<div class="ali-note">还没生成话术</div>') +
        '</div></div>',
      actions: [
        {
          label: '删除',
          kind: 'danger',
          onClick: function () {
            confirmModal('从公海池列表移除这条？', function () {
              apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) +
                '/public-pool/targets/' + encodeURIComponent(targetId), { method: 'DELETE' })
                .then(function () { closeDrawer(); toast('已删除', 'ok'); loadPool(); })
                .catch(function (e) { toast('删除失败：' + e.message, 'err'); });
            }, { danger: true, okLabel: '删除' });
          }
        },
        { label: '关闭', onClick: closeDrawer }
      ],
      onMount: function (root) {
        var plan = root.querySelector('[data-act="plan"]');
        if (plan) {
          plan.addEventListener('click', function () {
            apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) +
              '/public-pool/targets/' + encodeURIComponent(targetId) + '/plan', { method: 'POST', body: {} })
              .then(function () { toast('已生成', 'ok'); loadPool().then(function () { showPoolDrawer(targetId); }); })
              .catch(function (e) { toast('生成失败：' + e.message, 'err'); });
          });
        }
      }
    });
  }

  /* ------------------------------------------------ 账号页 */

  function renderAccounts(host) {
    host.innerHTML =
      '<div class="ali-head"><div><div class="ali-h1">阿里账号</div>' +
      '<div class="ali-hint">一个账号一个浏览器实例；登录态保存在本机 browser_data</div></div>' +
      '<div class="ali-toolbar">' +
      '<button type="button" class="ali-btn" data-act="reload">刷新</button>' +
      '<button type="button" class="ali-btn primary" data-act="add">添加账号</button>' +
      '</div></div>' +
      '<div class="ali-card"><div class="ali-card-body" style="padding:0;">' +
      (S.accounts.length
        ? '<table class="ali-table"><thead><tr><th>账号</th><th>状态</th><th>询盘 / 客户</th><th>最近同步</th><th></th></tr></thead><tbody>' +
          S.accounts.map(function (a) {
            return '<tr data-account="' + esc(a.id) + '">' +
              '<td><div class="ali-cell-main">' + esc(a.nickname || ('账号 #' + a.id)) + '</div>' +
              '<div class="ali-cell-sub">' + esc(a.last_error ? compact(a.last_error, 60) : (a.sync_progress || '')) + '</div></td>' +
              '<td>' + badge(a.status || '—', a.status === 'online' ? 'ok' : a.status === 'pending' ? 'warn' : '') +
              (a.auto_reply_enabled ? badge('接管中', 'info') : '') + '</td>' +
              '<td>' + (a.inquiry_count || 0) + ' / ' + (a.customer_count || 0) + '</td>' +
              '<td>' + fmtTime(a.last_sync_at) + '</td>' +
              '<td style="text-align:right;">' +
              '<button type="button" class="ali-btn sm" data-acct-act="login" data-id="' + esc(a.id) + '">打开/检测</button>' +
              '<button type="button" class="ali-btn sm" data-acct-act="sync" data-id="' + esc(a.id) + '">同步</button>' +
              '</td></tr>';
          }).join('') + '</tbody></table>'
        : emptyBlock('还没有阿里账号', '添加账号后会打开浏览器让你登录一次，之后用本机保存的登录态', [
          { id: 'add', label: '添加账号', kind: 'primary' }
        ])) +
      '</div></div>';
    host.querySelector('[data-act="reload"]').addEventListener('click', function () { loadAccounts(); });
    host.querySelector('[data-act="add"]').addEventListener('click', showAccountModal);
    bindEmptyActions(host, { add: showAccountModal });
    host.querySelectorAll('[data-acct-act]').forEach(function (el) {
      el.addEventListener('click', function (ev) {
        ev.stopPropagation();
        var id = el.getAttribute('data-id');
        if (el.getAttribute('data-acct-act') === 'login') {
          toast('正在打开阿里国际站…');
          apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(id) + '/login', { method: 'POST', body: {} })
            .then(function () { toast('浏览器已打开，完成登录后回来点「同步」', 'ok'); })
            .catch(function (e) { toast('打开失败：' + e.message, 'err'); });
        } else {
          S.accountId = id;
          renderAccountPicker();
          runSync();
        }
      });
    });
    host.querySelectorAll('[data-account]').forEach(function (el) {
      el.addEventListener('click', function () {
        S.accountId = el.getAttribute('data-account');
        renderAccountPicker();
        loadConfig();
        toast('已切换账号', 'ok');
        render();
      });
    });
  }

  function showAccountModal() {
    openModal({
      title: '添加阿里账号',
      sub: '建议用子账号跑接待，主账号留给自己',
      body: '<div class="ali-field"><div class="ali-field-label">备注名</div>' +
        '<input class="ali-input" id="aliNewAccountName" placeholder="例如：国际站-主账号 / 业务员A子账号"></div>',
      actions: [
        { label: '取消', onClick: closeModal },
        {
          label: '创建',
          kind: 'primary',
          onClick: function () {
            var nickname = ($('aliNewAccountName').value || '').trim() || '阿里国际站账号';
            apiJson('/api/alibaba-inquiries/accounts', { method: 'POST', body: { nickname: nickname } })
              .then(function () {
                closeModal();
                toast('已创建，点「打开/检测」登录一次', 'ok');
                return loadAccounts();
              })
              .catch(function (e) { toast('创建失败：' + e.message, 'err'); });
          }
        }
      ]
    });
  }

  function runSync() {
    if (!needAccount()) return;
    confirmModal('同步会把阿里国际站的询盘列表和详情拉一遍，可能需要几分钟，继续吗？', function () {
      setBusy(true, '同步中…');
      apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(S.accountId) + '/sync', {
        method: 'POST',
        body: { max_scrolls: 180, max_pages: 200, stop_after_idle_rounds: 8, sync_details: true, detail_limit: 0 }
      })
        .then(function () {
          setBusy(false);
          toast('同步完成', 'ok');
          loadAccounts(true);
          loadInquiries(true);
          loadDashboard();
        })
        .catch(function (e) { setBusy(false); toast('同步失败：' + e.message, 'err'); });
    }, { okLabel: '开始同步' });
  }

  /* ------------------------------------------------ 主渲染 */

  function renderNav() {
    var host = $('aliNav');
    if (!host) return;
    var counts = {
      inquiries: S.dashboard && S.dashboard.stats ? S.dashboard.stats.awaiting_reply : null,
      store: S.storeStats ? S.storeStats.products : null,
      pool: S.dashboard && S.dashboard.stats ? S.dashboard.stats.pool_pending : null,
      customers: (S.archives && S.archives.total)
        ? S.archives.total
        : (S.dashboard && S.dashboard.stats ? S.dashboard.stats.customer_archives : null),
      kb: S.dashboard && S.dashboard.stats ? S.dashboard.stats.kb_docs : null
    };
    function itemHtml(item) {
      return '<button type="button" class="ali-nav-item' + (S.view === item.key ? ' is-active' : '') + '" data-nav="' + item.key + '">' +
        '<span class="ali-nav-ico">' + item.icon + '</span><span>' + esc(item.label) + '</span>' +
        (counts[item.key] ? '<span class="ali-nav-count">' + counts[item.key] + '</span>' : '') +
        '</button>';
    }
    host.innerHTML =
      '<div class="ali-nav-group">工作</div>' +
      NAV.filter(function (item) { return item.group === 'work'; }).map(itemHtml).join('') +
      '<div class="ali-nav-group">配置</div>' +
      NAV.filter(function (item) { return item.group === 'config'; }).map(itemHtml).join('');
    host.querySelectorAll('[data-nav]').forEach(function (el) {
      el.addEventListener('click', function () {
        S.view = el.getAttribute('data-nav');
        render();
        loadViewData();
      });
    });
  }

  function render() {
    renderAccountPicker();
    refreshHeaderStatus();
    renderNav();
    var main = $('aliMain');
    if (!main) return;
    var item = null;
    NAV.forEach(function (n) { if (n.key === S.view) item = n; });
    var title = item ? item.label + ' · ' + item.hint : '';
    if (!S.accounts.length) {
      main.innerHTML = '<div class="ali-card">' +
        emptyBlock('先添加一个阿里国际站账号', '添加后打开浏览器登录一次，之后同步询盘、跑 AI 接待', [
          { id: 'add-account', label: '添加账号', kind: 'primary' }
        ]) + '</div>';
      bindEmptyActions(main, { 'add-account': showAccountModal });
      return;
    }
    if (!main.getAttribute('data-title') || main.getAttribute('data-title') !== title) {
      main.setAttribute('data-title', title);
    }
    if (S.view === 'desk') renderDesk(main);
    else if (S.view === 'inquiries') renderInquiries(main);
    else if (S.view === 'store') renderStore(main);
    else if (S.view === 'pool') renderPool(main);
    else if (S.view === 'customers') renderCustomers(main);
    else if (S.view === 'kb') renderKb(main);
    else if (S.view === 'persona') renderPersona(main);
    else if (S.view === 'rules') renderRules(main);
    else if (S.view === 'accounts') renderAccounts(main);
  }

  /* ------------------------------------------------ 初始化 */

  function ensureData() {
    if (!S.accountId) return Promise.resolve();
    return Promise.all([loadDashboard(), loadConfig()]);
  }

  function loadViewData() {
    if (!S.accountId) return Promise.resolve();
    if (S.view === 'desk') return loadDashboard().then(function () { return loadStore(); });
    if (S.view === 'inquiries') return loadInquiries(true);
    if (S.view === 'store') return loadStore().then(function () { return loadProducts(true); });
    if (S.view === 'pool') return loadPool();
    if (S.view === 'customers') return loadArchives(true);
    if (S.view === 'kb') return loadDocs();
    if (S.view === 'persona') return loadSummaries();
    if (S.view === 'rules') return loadConfig();
    return Promise.resolve();
  }

  function init() {
    var back = $('aliBackBtn');
    if (back) {
      back.addEventListener('click', function () {
        if (typeof window.showLobsterView === 'function') {
          window.showLobsterView('skill-store', back).catch(function () {});
        } else if (typeof window.switchToSkillStore === 'function') {
          window.switchToSkillStore();
        }
      });
    }
    $('aliAccountSelect').addEventListener('change', function (e) {
      S.accountId = e.target.value || null;
      render();
      ensureData().then(loadViewData);
    });
    $('aliSyncBtn').addEventListener('click', runSync);
    $('aliRunBtn').addEventListener('click', function () {
      showRunModal(!!(S.config && S.config.dry_run));
    });
    $('aliSettingsBtn').addEventListener('click', function () { S.view = 'rules'; render(); });
    $('aliDrawerMask').addEventListener('click', closeDrawer);
    $('aliModalMask').addEventListener('click', function (e) {
      if (e.target === $('aliModalMask')) closeModal();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      if (!$('aliModalMask').hidden) { closeModal(); return; }
      if (!$('aliDrawer').hidden) closeDrawer();
    });
    loadAccounts(true).then(function () {
      S.view = S.accounts.length ? 'desk' : 'accounts';
      return ensureData();
    }).then(loadViewData);
  }

  window.initAlibabaInquiriesView = function () {
    try {
      closeDrawer();
      closeModal();
      init();
    } catch (err) {
      if (window.console) console.warn('[ali] init failed', err);
    }
  };

  if (typeof window.registerLobsterView === 'function') {
    window.registerLobsterView('alibaba-inquiries', {
      html: '/static/views/alibaba-inquiries.html?v=20260921-ali-store-v3',
      scripts: '/static/js/alibaba-inquiries.js?v=20260921-ali-store-v3',
      init: 'initAlibabaInquiriesView',
      cache: 'reload'
    });
  }
})();
