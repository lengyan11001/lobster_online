(function() {
  var PAGE_SIZE = 20;
  var state = {
    accounts: [],
    activeAccountId: 0,
    activeTab: 'inquiries',
    inquiryOffset: 0,
    inquiryTotal: 0,
    customerOffset: 0,
    customerTotal: 0,
    selectedInquiryId: '',
    selectedProfile: null,
    busy: false
  };

  function $(id) { return document.getElementById(id); }

  function esc(value) {
    if (typeof escapeHtml === 'function') return escapeHtml(String(value || ''));
    return String(value || '').replace(/[&<>"']/g, function(ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
  }

  function apiBase() {
    return (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE ? String(LOCAL_API_BASE) : '').replace(/\/$/, '');
  }

  function authJson() {
    var h = typeof authHeaders === 'function' ? Object.assign({}, authHeaders() || {}) : {};
    if (!h.Authorization && typeof token !== 'undefined' && token) h.Authorization = 'Bearer ' + token;
    if (typeof getOrCreateInstallationId === 'function') h['X-Installation-Id'] = getOrCreateInstallationId();
    h['Content-Type'] = 'application/json';
    return h;
  }

  function authOnly() {
    var h = typeof authHeaders === 'function' ? Object.assign({}, authHeaders() || {}) : {};
    if (!h.Authorization && typeof token !== 'undefined' && token) h.Authorization = 'Bearer ' + token;
    if (typeof getOrCreateInstallationId === 'function') h['X-Installation-Id'] = getOrCreateInstallationId();
    return h;
  }

  function parseErr(data, fallback) {
    if (!data) return fallback || '请求失败';
    if (typeof data === 'string') return data;
    var detail = data.detail || data.error || data.message;
    if (typeof detail === 'string') return detail;
    try { return JSON.stringify(detail || data); } catch (e) { return fallback || '请求失败'; }
  }

  function apiJson(path, opts) {
    opts = opts || {};
    var base = apiBase();
    if (!base) return Promise.reject(new Error('未配置本机 LOCAL_API_BASE'));
    var req = { method: opts.method || 'GET', headers: authJson() };
    if (opts.body !== undefined) req.body = JSON.stringify(opts.body || {});
    return fetch(base + path, req).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok || data.ok === false) throw new Error(parseErr(data, '请求失败'));
        return data;
      });
    });
  }

  function apiUpload(path, form) {
    var base = apiBase();
    if (!base) return Promise.reject(new Error('未配置本机 LOCAL_API_BASE'));
    return fetch(base + path, { method: 'POST', headers: authOnly(), body: form }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok || data.ok === false) throw new Error(parseErr(data, '上传失败'));
        return data;
      });
    });
  }

  function setMsg(text, isErr) {
    var el = $('aliMsg');
    if (!el) return;
    el.textContent = text || '';
    el.className = 'ali-msg' + (isErr ? ' err' : '');
    el.style.display = text ? 'block' : 'none';
  }

  function setBusy(busy) {
    state.busy = !!busy;
    ['aliSyncBtn', 'aliOpenLoginBtn', 'aliCheckLoginBtn', 'aliCreateAccountBtn', 'aliUploadDocBtn', 'aliAnalyzeBtn'].forEach(function(id) {
      var el = $(id);
      if (el) el.disabled = !!busy;
    });
  }

  function fmtTime(value) {
    if (!value) return '-';
    try {
      var d = new Date(value);
      if (!isNaN(d.getTime())) return d.toLocaleString();
    } catch (e) {}
    return String(value || '-');
  }

  function compact(value, len) {
    var text = String(value || '').replace(/\s+/g, ' ').trim();
    len = len || 120;
    return text.length > len ? text.slice(0, len - 1) + '…' : text;
  }

  function statusBadge(status) {
    var st = String(status || '').toLowerCase();
    var label = {
      active: '已登录',
      pending: '待登录',
      error: '异常',
      idle: '空闲',
      running: '同步中',
      failed: '失败',
      ongoing: 'Ongoing',
      unread: '未读',
      replied: '已回复',
      closed: '已关闭'
    }[st] || (status || '-');
    var tone = st === 'active' || st === 'idle' || st === 'replied' ? ' ok' : (st === 'error' || st === 'failed' ? ' err' : ' warn');
    return '<span class="ali-badge' + tone + '">' + esc(label) + '</span>';
  }

  function currentAccount() {
    return state.accounts.find(function(x) { return Number(x.id) === Number(state.activeAccountId); }) || null;
  }

  function renderAccounts() {
    var host = $('aliAccountList');
    if (!host) return;
    var totalInquiries = 0;
    var totalCustomers = 0;
    state.accounts.forEach(function(a) {
      totalInquiries += Number(a.inquiry_count || 0);
      totalCustomers += Number(a.customer_count || 0);
    });
    if ($('aliStatAccounts')) $('aliStatAccounts').textContent = state.accounts.length;
    if ($('aliStatInquiries')) $('aliStatInquiries').textContent = totalInquiries;
    if ($('aliStatCustomers')) $('aliStatCustomers').textContent = totalCustomers;
    if (!state.accounts.length) {
      host.innerHTML = '<div class="ali-empty" style="grid-column:1/-1;">还没有账号，点右上角“添加账号”后打开登录。</div>';
      return;
    }
    host.innerHTML = state.accounts.map(function(a) {
      var active = Number(a.id) === Number(state.activeAccountId) ? ' is-active' : '';
      return '<article class="ali-account-card' + active + '" data-account-id="' + esc(a.id) + '">'
        + '<div class="ali-card-head" style="margin-bottom:0.45rem;">'
        + '<strong>' + esc(a.nickname || ('账号 ' + a.id)) + '</strong>'
        + statusBadge(a.status)
        + '</div>'
        + '<div class="ali-item-meta">'
        + '<span class="ali-badge">询盘 ' + esc(a.inquiry_count || 0) + '</span>'
        + '<span class="ali-badge">客户 ' + esc(a.customer_count || 0) + '</span>'
        + statusBadge(a.sync_status || 'idle')
        + '</div>'
        + '<div class="ali-muted" style="margin-top:0.55rem;">上次同步：' + esc(fmtTime(a.last_sync_at)) + '</div>'
        + (a.sync_progress ? '<div class="ali-muted" style="margin-top:0.25rem;">' + esc(a.sync_progress) + '</div>' : '')
        + (a.last_error ? '<div class="ali-muted" style="margin-top:0.25rem;color:#be123c;">' + esc(compact(a.last_error, 120)) + '</div>' : '')
        + '</article>';
    }).join('');
  }

  function loadAccounts(silent) {
    if (!silent) setMsg('正在加载阿里账号…');
    return apiJson('/api/alibaba-inquiries/accounts').then(function(data) {
      state.accounts = data.accounts || [];
      if (!state.activeAccountId && state.accounts.length) state.activeAccountId = Number(state.accounts[0].id);
      renderAccounts();
      renderWorkspace();
      if (!silent) setMsg('');
    }).catch(function(err) {
      setMsg(err.message || String(err), true);
    });
  }

  function renderWorkspace() {
    var ws = $('aliWorkspace');
    var acct = currentAccount();
    if (!ws) return;
    ws.classList.toggle('is-visible', !!acct);
    if (!acct) return;
    if ($('aliAccountTitle')) $('aliAccountTitle').textContent = acct.nickname || ('账号 ' + acct.id);
    if ($('aliAccountMeta')) {
      $('aliAccountMeta').textContent = '状态：' + (acct.status || '-') + ' · 询盘 ' + (acct.inquiry_count || 0) + ' · 客户 ' + (acct.customer_count || 0) + ' · 上次同步 ' + fmtTime(acct.last_sync_at);
    }
  }

  function openAccount(accountId) {
    state.activeAccountId = Number(accountId || 0);
    state.inquiryOffset = 0;
    state.customerOffset = 0;
    state.selectedInquiryId = '';
    state.selectedProfile = null;
    renderAccounts();
    renderWorkspace();
    switchTab('inquiries');
    loadInquiries(true);
  }

  function switchTab(tab) {
    state.activeTab = tab || 'inquiries';
    document.querySelectorAll('#content-alibaba-inquiries [data-ali-tab]').forEach(function(btn) {
      btn.classList.toggle('is-active', btn.getAttribute('data-ali-tab') === state.activeTab);
    });
    document.querySelectorAll('#content-alibaba-inquiries [data-ali-panel]').forEach(function(panel) {
      panel.classList.toggle('is-active', panel.getAttribute('data-ali-panel') === state.activeTab);
    });
    if (state.activeTab === 'customers') loadCustomers(true);
    if (state.activeTab === 'training') loadDocs(true);
    if (state.activeTab === 'analysis') loadSummaries(true);
  }

  function renderPager(hostId, total, offset, onPage) {
    var host = $(hostId);
    if (!host) return;
    var page = Math.floor(offset / PAGE_SIZE) + 1;
    var pages = Math.max(1, Math.ceil((total || 0) / PAGE_SIZE));
    host.innerHTML = '<button type="button" class="btn btn-ghost btn-sm" data-page-dir="-1"' + (page <= 1 ? ' disabled' : '') + '>上一页</button>'
      + '<span class="ali-muted">第 ' + page + ' / ' + pages + ' 页 · 共 ' + (total || 0) + ' 条 · 每页 20</span>'
      + '<button type="button" class="btn btn-ghost btn-sm" data-page-dir="1"' + (page >= pages ? ' disabled' : '') + '>下一页</button>';
    host.querySelectorAll('[data-page-dir]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var dir = Number(btn.getAttribute('data-page-dir') || 0);
        onPage(Math.max(0, offset + dir * PAGE_SIZE));
      });
    });
  }

  function loadInquiries(reset) {
    if (!state.activeAccountId) return Promise.resolve();
    if (reset) state.inquiryOffset = 0;
    var q = (($('aliInquirySearch') || {}).value || '').trim();
    var path = '/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId)
      + '/inquiries?limit=' + PAGE_SIZE
      + '&offset=' + state.inquiryOffset
      + '&q=' + encodeURIComponent(q);
    return apiJson(path).then(function(data) {
      state.inquiryTotal = data.total || 0;
      renderInquiryList(data.items || []);
      renderPager('aliInquiryPager', state.inquiryTotal, state.inquiryOffset, function(nextOffset) {
        state.inquiryOffset = nextOffset;
        loadInquiries(false);
      });
    }).catch(function(err) {
      setMsg(err.message || String(err), true);
    });
  }

  function avatarHtml(item) {
    var profile = item.profile || {};
    var url = profile.avatar_url || item.avatar_url || '';
    var name = item.buyer_name || item.title || '?';
    var initial = String(name || '?').trim().slice(0, 1).toUpperCase() || '?';
    if (url) return '<span class="ali-avatar" data-avatar-inquiry="' + esc(item.inquiry_id) + '"><img src="' + esc(url) + '" alt=""></span>';
    return '<span class="ali-avatar" data-avatar-inquiry="' + esc(item.inquiry_id) + '">' + esc(initial) + '</span>';
  }

  function renderInquiryList(items) {
    var host = $('aliInquiryList');
    if (!host) return;
    if (!items.length) {
      host.innerHTML = '<div class="ali-empty">暂无询盘。本页只显示本地库数据，请先点“滚动同步全部询盘”。</div>';
      return;
    }
    host.innerHTML = items.map(function(item) {
      var active = item.inquiry_id === state.selectedInquiryId ? ' is-active' : '';
      return '<article class="ali-item' + active + '" data-inquiry-id="' + esc(item.inquiry_id) + '">'
        + '<div class="ali-item-main">'
        + avatarHtml(item)
        + '<div>'
        + '<div class="ali-card-head" style="margin-bottom:0;">'
        + '<div class="ali-item-title">' + esc(item.title || ('询盘 ' + item.inquiry_id)) + '</div>'
        + statusBadge(item.status || 'ongoing')
        + '</div>'
        + '<div class="ali-item-meta">'
        + '<span class="ali-badge">ID ' + esc(item.inquiry_id) + '</span>'
        + (item.buyer_name ? '<span class="ali-badge">' + esc(item.buyer_name) + '</span>' : '')
        + (item.country ? '<span class="ali-badge">' + esc(item.country) + '</span>' : '')
        + '<span class="ali-badge">' + esc(fmtTime(item.last_message_at)) + '</span>'
        + '</div>'
        + '<div class="ali-preview">' + esc(item.preview || '暂无摘要') + '</div>'
        + '</div>'
        + '</div>'
        + '</article>';
    }).join('');
  }

  function loadDetail(inquiryId, switchCustomer) {
    if (!state.activeAccountId || !inquiryId) return;
    state.selectedInquiryId = inquiryId;
    if ($('aliSyncDetailBtn')) $('aliSyncDetailBtn').disabled = false;
    if ($('aliDraftReplyBtn')) $('aliDraftReplyBtn').disabled = false;
    if ($('aliSendReplyBtn')) $('aliSendReplyBtn').disabled = false;
    var box = $('aliDetailBox');
    if (box) box.innerHTML = '<div class="ali-empty">正在加载详情…</div>';
    return apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId) + '/inquiries/' + encodeURIComponent(inquiryId))
      .then(function(data) {
        renderDetail(data);
        if (switchCustomer && data.profile) {
          state.selectedProfile = data.profile;
          switchTab('customers');
          renderCustomerDetail(data.profile);
        }
      }).catch(function(err) {
        setMsg(err.message || String(err), true);
      });
  }

  function renderDetail(data) {
    var box = $('aliDetailBox');
    if (!box) return;
    var inquiry = data.inquiry || {};
    var profile = data.profile || null;
    var messages = data.messages || [];
    var profileHtml = profile ? renderProfileBlock(profile) : '<div class="ali-empty">还没有客户属性，点“同步详情”进入阿里详情页抓取。</div>';
    var msgHtml = messages.length ? messages.map(function(m) {
      var cls = m.direction === 'seller' ? ' seller' : '';
      return '<div class="ali-bubble' + cls + '">'
        + '<div class="ali-bubble-meta">' + esc(m.sender_name || m.direction || '-') + ' · ' + esc(fmtTime(m.sent_at)) + '</div>'
        + '<div>' + esc(m.content || '').replace(/\n/g, '<br>') + '</div>'
        + '</div>';
    }).join('') : '<div class="ali-empty">本地还没有聊天记录，点“同步详情”。</div>';
    box.className = '';
    box.innerHTML = '<div class="ali-item-title">' + esc(inquiry.title || ('询盘 ' + inquiry.inquiry_id)) + '</div>'
      + '<div class="ali-item-meta" style="margin-bottom:0.75rem;">'
      + '<span class="ali-badge">ID ' + esc(inquiry.inquiry_id || '') + '</span>'
      + (inquiry.buyer_name ? '<span class="ali-badge">' + esc(inquiry.buyer_name) + '</span>' : '')
      + '<span class="ali-badge">' + esc(fmtTime(inquiry.last_message_at)) + '</span>'
      + '</div>'
      + profileHtml
      + '<div class="ali-card-title" style="margin:0.9rem 0 0.55rem;">消息记录</div>'
      + '<div class="ali-chat">' + msgHtml + '</div>';
  }

  function renderProfileBlock(profile) {
    var rawTags = (profile.attributes || {}).preference_tags || [];
    if (!Array.isArray(rawTags)) rawTags = String(rawTags || '').split(/[,，;；\n]+/).filter(Boolean);
    var tags = rawTags.map(function(x) {
      return '<span class="ali-badge">' + esc(x) + '</span>';
    }).join('');
    var activity = Object.keys(profile.activity || {}).map(function(k) {
      return '<span class="ali-badge">' + esc(k) + ' ' + esc(profile.activity[k]) + '</span>';
    }).join('');
    return '<div class="ali-profile-grid">'
      + '<div class="ali-profile-cell"><span>客户</span><strong>' + esc(profile.buyer_name || '-') + '</strong></div>'
      + '<div class="ali-profile-cell"><span>国家</span><strong>' + esc(profile.country || '-') + '</strong></div>'
      + '<div class="ali-profile-cell"><span>公司</span><strong>' + esc(profile.company_name || '-') + '</strong></div>'
      + '<div class="ali-profile-cell"><span>邮箱</span><strong>' + esc(profile.email || '-') + '</strong></div>'
      + '<div class="ali-profile-cell"><span>注册时间</span><strong>' + esc(profile.registration_time || '-') + '</strong></div>'
      + '<div class="ali-profile-cell"><span>询盘ID</span><strong>' + esc(profile.inquiry_id || '-') + '</strong></div>'
      + '</div>'
      + (tags ? '<div class="ali-item-meta" style="margin-top:0.6rem;">' + tags + '</div>' : '')
      + (activity ? '<div class="ali-item-meta" style="margin-top:0.45rem;">' + activity + '</div>' : '');
  }

  function syncSelectedDetail() {
    if (!state.selectedInquiryId) return;
    setBusy(true);
    setMsg('正在进入阿里询盘详情页同步消息和客户属性…');
    return apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId) + '/inquiries/' + encodeURIComponent(state.selectedInquiryId) + '/sync-detail', { method: 'POST', body: {} })
      .then(function(data) {
        setMsg(data.message || '详情已同步');
        return loadDetail(state.selectedInquiryId);
      }).catch(function(err) {
        setMsg(err.message || String(err), true);
      }).finally(function() {
        setBusy(false);
      });
  }

  function loadCustomers(reset) {
    if (!state.activeAccountId) return Promise.resolve();
    if (reset) state.customerOffset = 0;
    var q = (($('aliCustomerSearch') || {}).value || '').trim();
    var path = '/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId)
      + '/customers?limit=' + PAGE_SIZE
      + '&offset=' + state.customerOffset
      + '&q=' + encodeURIComponent(q);
    return apiJson(path).then(function(data) {
      state.customerTotal = data.total || 0;
      renderCustomerList(data.items || []);
      renderPager('aliCustomerPager', state.customerTotal, state.customerOffset, function(nextOffset) {
        state.customerOffset = nextOffset;
        loadCustomers(false);
      });
    }).catch(function(err) {
      setMsg(err.message || String(err), true);
    });
  }

  function renderCustomerList(items) {
    var host = $('aliCustomerList');
    if (!host) return;
    if (!items.length) {
      host.innerHTML = '<div class="ali-empty">暂无客户档案。点询盘里的“同步详情”后会在这里出现。</div>';
      return;
    }
    host.innerHTML = items.map(function(item) {
      var active = state.selectedProfile && String(state.selectedProfile.inquiry_id) === String(item.inquiry_id) ? ' is-active' : '';
      var initial = String(item.buyer_name || item.company_name || '?').slice(0, 1).toUpperCase();
      return '<article class="ali-item' + active + '" data-customer-inquiry="' + esc(item.inquiry_id) + '">'
        + '<div class="ali-item-main">'
        + (item.avatar_url ? '<span class="ali-avatar"><img src="' + esc(item.avatar_url) + '" alt=""></span>' : '<span class="ali-avatar">' + esc(initial || '?') + '</span>')
        + '<div>'
        + '<div class="ali-item-title">' + esc(item.buyer_name || item.company_name || '未命名客户') + '</div>'
        + '<div class="ali-item-meta">'
        + (item.country ? '<span class="ali-badge">' + esc(item.country) + '</span>' : '')
        + (item.company_name ? '<span class="ali-badge">' + esc(item.company_name) + '</span>' : '')
        + '<span class="ali-badge">询盘 ' + esc(item.inquiry_id) + '</span>'
        + '</div>'
        + '</div>'
        + '</div>'
        + '</article>';
    }).join('');
  }

  function renderCustomerDetail(profile) {
    var host = $('aliCustomerDetail');
    if (!host) return;
    if (!profile) {
      host.className = 'ali-empty';
      host.textContent = '请选择客户';
      return;
    }
    host.className = '';
    var avatar = profile.avatar_url ? '<span class="ali-avatar" style="width:64px;height:64px;"><img src="' + esc(profile.avatar_url) + '" alt=""></span>' : '';
    host.innerHTML = '<div style="display:flex;gap:0.7rem;align-items:center;margin-bottom:0.7rem;">'
      + avatar
      + '<div><div class="ali-item-title">' + esc(profile.buyer_name || profile.company_name || '未命名客户') + '</div>'
      + '<div class="ali-muted">询盘 ' + esc(profile.inquiry_id || '-') + '</div></div></div>'
      + renderProfileBlock(profile);
  }

  function loadDocs(silent) {
    if (!state.activeAccountId) return Promise.resolve();
    return apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId) + '/training-docs')
      .then(function(data) {
        renderDocs(data.items || []);
        if (!silent) setMsg('资料列表已刷新');
      }).catch(function(err) {
        setMsg(err.message || String(err), true);
      });
  }

  function renderDocs(items) {
    var host = $('aliDocList');
    if (!host) return;
    if (!items.length) {
      host.innerHTML = '<div class="ali-empty">暂无资料。新号可以先上传产品资料、FAQ 或优秀话术训练。</div>';
      return;
    }
    host.innerHTML = items.map(function(item) {
      return '<article class="ali-item">'
        + '<div class="ali-card-head" style="margin-bottom:0.35rem;">'
        + '<strong>' + esc(item.title || item.filename || '资料') + '</strong>'
        + '<span class="ali-badge">' + esc(item.kind || 'script') + '</span>'
        + '</div>'
        + '<div class="ali-muted">' + esc(item.filename || '') + ' · ' + esc(fmtTime(item.created_at)) + '</div>'
        + '<div class="ali-preview">' + esc(item.content_preview || '') + '</div>'
        + '</article>';
    }).join('');
  }

  function uploadDoc() {
    if (!state.activeAccountId) return;
    var fileInput = $('aliDocFile');
    if (!fileInput || !fileInput.files || !fileInput.files[0]) {
      setMsg('请选择要上传的资料文件', true);
      return;
    }
    var form = new FormData();
    form.append('file', fileInput.files[0]);
    form.append('kind', (($('aliDocKind') || {}).value || 'script'));
    form.append('title', (($('aliDocTitle') || {}).value || '').trim());
    setBusy(true);
    setMsg('正在上传资料…');
    apiUpload('/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId) + '/training-docs', form)
      .then(function() {
        if (fileInput) fileInput.value = '';
        if ($('aliDocTitle')) $('aliDocTitle').value = '';
        setMsg('资料已上传并入库');
        return loadDocs(true);
      }).catch(function(err) {
        setMsg(err.message || String(err), true);
      }).finally(function() {
        setBusy(false);
      });
  }

  function loadSummaries(silent) {
    if (!state.activeAccountId) return Promise.resolve();
    return apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId) + '/summaries')
      .then(function(data) {
        renderSummaries(data.items || []);
        if (!silent) setMsg('总结已刷新');
      }).catch(function(err) {
        setMsg(err.message || String(err), true);
      });
  }

  function renderSummaries(items) {
    var host = $('aliSummaryList');
    if (!host) return;
    if (!items.length) {
      host.innerHTML = '<div class="ali-empty">暂无 AI 总结。同步询盘或上传资料后，点“开始总结话术”。</div>';
      return;
    }
    host.innerHTML = items.map(function(item) {
      var meta = item.meta || {};
      var good = (meta.good_scripts || []).slice(0, 5).map(function(x) { return '<span class="ali-badge ok">' + esc(compact(x, 80)) + '</span>'; }).join('');
      var neg = (meta.negative_scripts || []).slice(0, 5).map(function(x) { return '<span class="ali-badge err">' + esc(compact(x, 80)) + '</span>'; }).join('');
      return '<article class="ali-item">'
        + '<div class="ali-card-head" style="margin-bottom:0.35rem;">'
        + '<strong>' + esc(meta.overview || '历史询盘话术总结') + '</strong>'
        + '<span class="ali-badge">' + esc(fmtTime(item.created_at)) + '</span>'
        + '</div>'
        + (good ? '<div class="ali-muted" style="margin-top:0.5rem;">好话术</div><div class="ali-item-meta">' + good + '</div>' : '')
        + (neg ? '<div class="ali-muted" style="margin-top:0.5rem;">负面话术</div><div class="ali-item-meta">' + neg + '</div>' : '')
        + '<details style="margin-top:0.6rem;"><summary class="ali-muted" style="cursor:pointer;">查看完整 JSON</summary>'
        + '<pre style="white-space:pre-wrap;font-size:0.76rem;line-height:1.5;max-height:320px;overflow:auto;">' + esc(item.content || '') + '</pre></details>'
        + '</article>';
    }).join('');
  }

  function analyzeHistory() {
    if (!state.activeAccountId) return;
    var sample = Math.max(10, Math.min(1000, Number(($('aliAnalyzeLimit') || {}).value || 120)));
    setBusy(true);
    setMsg('AI 正在分析历史询盘和话术资料…');
    apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId) + '/analyze', {
      method: 'POST',
      body: { sample_limit: sample }
    }).then(function() {
      setMsg('话术总结已生成');
      return loadSummaries(true);
    }).catch(function(err) {
      setMsg(err.message || String(err), true);
    }).finally(function() {
      setBusy(false);
    });
  }

  function syncAll() {
    if (!state.activeAccountId) return;
    var syncDetails = !!(($('aliSyncDetailsChk') || {}).checked);
    setBusy(true);
    setMsg(syncDetails ? '正在滚动同步全部询盘，并逐条进入详情页抓取消息/客户属性…' : '正在滚动同步全部询盘列表…');
    apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId) + '/sync', {
      method: 'POST',
      body: {
        max_scrolls: 180,
        stop_after_idle_rounds: 8,
        sync_details: syncDetails,
        detail_limit: 0
      }
    }).then(function(data) {
      setMsg(data.message || '同步完成');
      return loadAccounts(true).then(function() {
        return loadInquiries(true);
      });
    }).catch(function(err) {
      setMsg(err.message || String(err), true);
      return loadAccounts(true);
    }).finally(function() {
      setBusy(false);
    });
  }

  function openLogin() {
    if (!state.activeAccountId) return;
    setMsg('正在打开阿里国际站登录页…');
    apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId) + '/login', { method: 'POST', body: {} })
      .then(function(data) {
        setMsg(data.message || '已打开登录页');
        return loadAccounts(true);
      }).catch(function(err) {
        setMsg(err.message || String(err), true);
      });
  }

  function checkLogin() {
    if (!state.activeAccountId) return;
    setMsg('正在检测登录状态…');
    apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId) + '/login-status')
      .then(function(data) {
        setMsg(data.logged_in ? '当前账号已登录' : '未检测到登录态，请先扫码登录');
        return loadAccounts(true);
      }).catch(function(err) {
        setMsg(err.message || String(err), true);
      });
  }

  function draftReply() {
    if (!state.activeAccountId || !state.selectedInquiryId) return;
    setBusy(true);
    setMsg('正在生成回复草稿…');
    apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId) + '/reply/draft', {
      method: 'POST',
      body: { inquiry_id: state.selectedInquiryId, instruction: '' }
    }).then(function(data) {
      var draft = data.draft || {};
      if ($('aliReplyText')) $('aliReplyText').value = draft.reply || '';
      setMsg(draft.risk ? ('草稿已生成：' + draft.risk) : '草稿已生成，请确认后发送');
    }).catch(function(err) {
      setMsg(err.message || String(err), true);
    }).finally(function() {
      setBusy(false);
    });
  }

  function sendReply() {
    if (!state.activeAccountId || !state.selectedInquiryId) return;
    var content = (($('aliReplyText') || {}).value || '').trim();
    if (!content) {
      setMsg('回复内容不能为空', true);
      return;
    }
    if (!window.confirm('确认发送这条阿里询盘回复吗？')) return;
    setBusy(true);
    setMsg('正在打开询盘并模拟人工输入发送…');
    apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId) + '/reply/send', {
      method: 'POST',
      body: { inquiry_id: state.selectedInquiryId, content: content, dry_run: false }
    }).then(function(data) {
      setMsg(data.message || '回复已发送');
      if ($('aliReplyText')) $('aliReplyText').value = '';
      return loadDetail(state.selectedInquiryId);
    }).catch(function(err) {
      setMsg(err.message || String(err), true);
    }).finally(function() {
      setBusy(false);
    });
  }

  function showAccountModal(show) {
    var modal = $('aliAccountModal');
    if (modal) modal.classList.toggle('is-visible', !!show);
  }

  function createAccount() {
    var nickname = (($('aliAccountNickname') || {}).value || '').trim() || '阿里国际站账号';
    setBusy(true);
    apiJson('/api/alibaba-inquiries/accounts', {
      method: 'POST',
      body: { nickname: nickname }
    }).then(function(data) {
      showAccountModal(false);
      if ($('aliAccountNickname')) $('aliAccountNickname').value = '';
      state.activeAccountId = Number((data.account || {}).id || 0);
      setMsg('账号已添加，请打开登录完成扫码');
      return loadAccounts(true);
    }).catch(function(err) {
      setMsg(err.message || String(err), true);
    }).finally(function() {
      setBusy(false);
    });
  }

  function bindEvents() {
    var root = $('content-alibaba-inquiries');
    if (!root || root.dataset.alibabaInquiryBound === '1') return;
    root.dataset.alibabaInquiryBound = '1';
    root.addEventListener('click', function(ev) {
      var account = ev.target.closest('.ali-account-card[data-account-id]');
      if (account) {
        openAccount(account.getAttribute('data-account-id'));
        return;
      }
      var avatar = ev.target.closest('[data-avatar-inquiry]');
      if (avatar) {
        ev.stopPropagation();
        loadDetail(avatar.getAttribute('data-avatar-inquiry'), true);
        return;
      }
      var inquiry = ev.target.closest('.ali-item[data-inquiry-id]');
      if (inquiry) {
        loadDetail(inquiry.getAttribute('data-inquiry-id'));
        return;
      }
      var customer = ev.target.closest('.ali-item[data-customer-inquiry]');
      if (customer) {
        var inquiryId = customer.getAttribute('data-customer-inquiry');
        apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId) + '/inquiries/' + encodeURIComponent(inquiryId))
          .then(function(data) {
            state.selectedProfile = data.profile || null;
            renderCustomerDetail(state.selectedProfile);
            loadCustomers(false);
          }).catch(function(err) {
            setMsg(err.message || String(err), true);
          });
      }
    });
    document.querySelectorAll('#content-alibaba-inquiries [data-ali-tab]').forEach(function(btn) {
      btn.addEventListener('click', function() { switchTab(btn.getAttribute('data-ali-tab') || 'inquiries'); });
    });
    var bind = function(id, fn) {
      var el = $(id);
      if (el) el.addEventListener('click', fn);
    };
    bind('aliBackSkillStoreBtn', function() {
      if (typeof window.showLobsterView === 'function') {
        window.showLobsterView('skill-store', document.querySelector('.nav-left-item[data-view="skill-store"]')).catch(function() {});
      } else {
        location.hash = 'skill-store';
      }
    });
    bind('aliRefreshAccountsBtn', function() { loadAccounts(false); });
    bind('aliAddAccountBtn', function() { showAccountModal(true); });
    bind('aliCloseAccountModal', function() { showAccountModal(false); });
    bind('aliCreateAccountBtn', createAccount);
    bind('aliOpenLoginBtn', openLogin);
    bind('aliCheckLoginBtn', checkLogin);
    bind('aliSyncBtn', syncAll);
    bind('aliInquirySearchBtn', function() { loadInquiries(true); });
    bind('aliInquiryReloadBtn', function() { loadInquiries(true); });
    bind('aliSyncDetailBtn', syncSelectedDetail);
    bind('aliDraftReplyBtn', draftReply);
    bind('aliSendReplyBtn', sendReply);
    bind('aliCustomerSearchBtn', function() { loadCustomers(true); });
    bind('aliUploadDocBtn', uploadDoc);
    bind('aliReloadDocsBtn', function() { loadDocs(false); });
    bind('aliAnalyzeBtn', analyzeHistory);
    bind('aliReloadSummaryBtn', function() { loadSummaries(false); });
    ['aliInquirySearch', 'aliCustomerSearch'].forEach(function(id) {
      var input = $(id);
      if (input) input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') {
          e.preventDefault();
          if (id === 'aliInquirySearch') loadInquiries(true);
          else loadCustomers(true);
        }
      });
    });
  }

  window.initAlibabaInquiriesView = function() {
    bindEvents();
    loadAccounts(true).then(function() {
      if (state.activeAccountId) loadInquiries(true);
    });
  };

  window._openAlibabaInquiriesView = function() {
    if (typeof window.registerLobsterView === 'function') {
      window.registerLobsterView('alibaba-inquiries', {
        html: '/static/views/alibaba-inquiries.html?v=20260721-alibaba-inquiries',
        scripts: '/static/js/alibaba-inquiries.js?v=20260721-alibaba-inquiries',
        init: 'initAlibabaInquiriesView',
        cache: 'reload'
      });
    }
    if (typeof window.showLobsterView === 'function') {
      window.showLobsterView('alibaba-inquiries', document.querySelector('.nav-left-item[data-view="skill-store"]')).catch(function() {});
    } else {
      location.hash = 'alibaba-inquiries';
    }
  };
})();
