(function() {
  var PAGE_SIZE = 20;
  var state = {
    accounts: [],
    activeAccountId: 0,
    mode: 'accounts',
    activeTab: 'inquiries',
    inquiryOffset: 0,
    inquiryTotal: 0,
    customerOffset: 0,
    customerTotal: 0,
    selectedInquiryId: '',
    selectedProfile: null,
    selectedArchiveId: 0,
    selectedArchivePayload: null,
    activeArchiveJobId: 0,
    archivePollTimer: 0,
    expandedSummaryId: 0,
    docs: [],
    summaries: [],
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
    delete h['Content-Type'];
    delete h['content-type'];
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
    ['aliSyncBtn', 'aliOpenLoginBtn', 'aliCreateAccountBtn', 'aliUploadDocBtn', 'aliAnalyzeBtn', 'aliEnrichArchiveBtn'].forEach(function(id) {
      var el = $(id);
      if (el) el.disabled = !!busy;
    });
    document.querySelectorAll('#content-alibaba-inquiries [data-ali-enrich-current]').forEach(function(el) {
      el.disabled = !!busy;
    });
    if (!busy && !state.selectedInquiryId) {
      ['aliSyncDetailBtn', 'aliEnrichArchiveBtn', 'aliDraftReplyBtn', 'aliSendReplyBtn'].forEach(function(id) {
        var el = $(id);
        if (el) el.disabled = true;
      });
    }
  }

  function clearArchivePoll() {
    if (state.archivePollTimer) {
      clearTimeout(state.archivePollTimer);
      state.archivePollTimer = 0;
    }
  }

  function updateArchiveLiveStatus(job, isErr) {
    job = job || {};
    var text = job.progress || job.status || '';
    var el = $('aliArchiveLiveStatus');
    if (!el) return false;
    if (!text) {
      el.style.display = 'none';
      el.textContent = '';
      return true;
    }
    el.className = 'ali-inline-note' + (isErr ? ' err' : '');
    el.style.display = 'block';
    el.textContent = '客户档案生成中：' + text;
    return true;
  }

  function watchArchiveJob(jobId, archiveId) {
    jobId = Number(jobId || 0);
    archiveId = Number(archiveId || 0);
    if (!jobId) return;
    state.activeArchiveJobId = jobId;
    clearArchivePoll();
    var tick = function() {
      apiJson('/api/alibaba-inquiries/customer-archive-jobs/' + encodeURIComponent(jobId))
        .then(function(data) {
          var job = data.job || {};
          var archive = data.archive || {};
          if (archive.id) state.selectedArchiveId = Number(archive.id);
          var st = String(job.status || '').toLowerCase();
          if (st === 'queued' || st === 'running') {
            if (!updateArchiveLiveStatus(job, false)) setMsg('客户档案补全中：' + (job.progress || st));
            state.archivePollTimer = setTimeout(tick, 3000);
            return;
          }
          clearArchivePoll();
          state.activeArchiveJobId = 0;
          if (st === 'succeeded') {
            setMsg('客户档案补全完成');
            updateArchiveLiveStatus({ progress: '已完成，正在刷新结果…' }, false);
          } else if (st === 'failed') {
            setMsg('客户档案补全失败：' + (job.error || job.progress || '未知错误'), true);
            updateArchiveLiveStatus({ progress: job.error || job.progress || '生成失败' }, true);
          }
          return loadAccounts(true).then(function() {
            return loadInquiries(false);
          }).then(function() {
            return loadCustomers(false);
          }).then(function() {
            return loadArchiveDetail(state.selectedArchiveId || archiveId);
          });
        }).catch(function(err) {
          clearArchivePoll();
          setMsg(err.message || String(err), true);
        });
    };
    tick();
  }

  function fmtTime(value) {
    if (!value) return '-';
    try {
      var d = new Date(value);
      if (!isNaN(d.getTime())) return d.toLocaleString();
    } catch (e) {}
    return String(value || '-');
  }

  function fmtDateOnly(value) {
    if (!value) return '';
    try {
      var d = new Date(value);
      if (!isNaN(d.getTime())) {
        var y = d.getFullYear();
        var m = String(d.getMonth() + 1).padStart(2, '0');
        var day = String(d.getDate()).padStart(2, '0');
        return y + '-' + m + '-' + day;
      }
    } catch (e) {}
    var text = String(value || '').trim();
    return text ? text.split(/[ T]/)[0].replace(/\//g, '-') : '';
  }

  function compact(value, len) {
    var text = String(value || '').replace(/\s+/g, ' ').trim();
    len = len || 120;
    return text.length > len ? text.slice(0, len - 1) + '…' : text;
  }

  function isUrl(value) {
    return /^https?:\/\//i.test(String(value || '').trim());
  }

  function stripRawUrls(value) {
    return String(value || '').replace(/https?:\/\/[^\s，。；、)）\]}]+/gi, '已提取公开网页内容');
  }

  function archiveText(value, len) {
    if (value === null || value === undefined || value === '') return '';
    if (isUrl(value)) return '已提取公开网页内容';
    if (Array.isArray(value)) {
      return compact(value.map(function(item) { return archiveText(item, len); }).filter(Boolean).join('；'), len || 180);
    }
    if (typeof value === 'object') {
      var preferred = ['summary', 'signal', 'desc', 'description', 'content', 'snippet', 'text', 'title', 'name', 'value', 'notes'];
      var parts = [];
      preferred.forEach(function(key) {
        if (value[key] !== null && value[key] !== undefined && value[key] !== '') parts.push(archiveText(value[key], len));
      });
      if (!parts.length) {
        Object.keys(value).forEach(function(key) {
          if (/url|href|link/i.test(key)) return;
          var text = archiveText(value[key], len);
          if (text) parts.push((ARCHIVE_KEY_LABELS[key] || key) + '：' + text);
        });
      }
      return compact(parts.filter(Boolean).join('；'), len || 180);
    }
    return compact(stripRawUrls(value), len || 180);
  }

  function sameText(a, b) {
    return String(a || '').trim().toLowerCase() === String(b || '').trim().toLowerCase();
  }

  var SOURCE_LABELS = {
    alibaba_inquiry: '阿里询盘原始消息',
    alibaba_profile: '阿里询盘右侧客户属性',
    official_website: '官方公开网站',
    web_search: '公开网页搜索',
    company_registry: '企业主体库',
    research_plan: '字段深度调研计划',
    source_inventory: '公开资料调研状态',
    professional_network_company: '职业社媒公司资料',
    short_video_account_search: '短视频账号公开资料',
    short_video_content_search: '短视频内容公开资料',
    commerce_product_search: '电商商品公开资料',
    local_video_account_search: '视频号账号公开资料',
    local_video_content_search: '视频号内容公开资料',
    visual_social_search: '图片社媒公开资料',
    public_discussion_search: '海外公开讨论资料',
    tikhub_linkedin_company: '职业社媒公司资料',
    tikhub_linkedin_posts: '职业社媒公司动态',
    tikhub_tiktok_user_search: '短视频账号公开资料',
    tikhub_tiktok_video_search: '短视频内容公开资料',
    tikhub_tiktok_shop_product_search: '电商商品公开资料',
    tikhub_wechat_channels_user_search: '视频号账号公开资料',
    tikhub_wechat_channels_search: '视频号内容公开资料',
    tikhub_instagram_search: '图片社媒公开资料',
    tikhub_x_search: '海外公开讨论资料'
  };

  var FIELD_LABELS = {
    company_name: '公司名称',
    buyer_name: '买家姓名',
    country: '国家/地区',
    domain: '官网/域名',
    email: '邮箱',
    phone: '电话',
    product_keywords: '产品关键词',
    messages_text: '询盘消息'
  };

  var ARCHIVE_KEY_LABELS = {
    summary: '摘要',
    business_scope: '主营/业务范围',
    registration_signals: '注册/信用证据',
    website_signals: '官网核验',
    important_findings: '重要发现',
    platforms: '已核验平台账号',
    signals: '补充信号',
    products: '已核验产品',
    marketplace_signals: '商品/交易证据',
    email_domain_match: '邮箱域名是否一致',
    phone_signals: '电话核验',
    notes: '核验说明'
  };

  function sourceLabel(value) {
    var key = String(value || '').trim();
    return SOURCE_LABELS[key] || key || '未知信息源';
  }

  function fieldLabel(value) {
    var key = String(value || '').trim();
    return FIELD_LABELS[key] || key || '未知字段';
  }

  function renderBadgeList(items, limit) {
    items = Array.isArray(items) ? items.filter(Boolean) : [];
    if (!items.length) return '';
    return '<div class="ali-item-meta">' + items.slice(0, limit || 10).map(function(x) {
      return '<span class="ali-badge">' + esc(archiveText(x, 58)) + '</span>';
    }).join('') + '</div>';
  }

  function renderSourceNotes(notes) {
    notes = Array.isArray(notes) ? notes.filter(Boolean) : [];
    if (!notes.length) return '';
    return '<div class="ali-source-notes">' + notes.slice(0, 4).map(function(note) {
      return '<div class="ali-source-note">' + esc(archiveText(note, 180)) + '</div>';
    }).join('') + '</div>';
  }

  function renderSourceLink(url) {
    if (!isUrl(url)) return '';
    return '<a class="ali-source-link" href="' + esc(url) + '" target="_blank" rel="noopener">打开来源</a>';
  }

  function archiveQualityLabel(payload, status) {
    payload = payload || {};
    var quality = payload.data_quality || {};
    var st = String(status || '').toLowerCase();
    if (quality.ready) return { text: '可跟进', tone: 'ok' };
    if (st === 'completed' || st === 'succeeded') return { text: '已生成', tone: 'ok' };
    if (st === 'failed') return { text: '生成失败', tone: 'err' };
    if (st === 'running' || st === 'queued') return { text: '补全中', tone: 'warn' };
    return { text: '需核验', tone: 'warn' };
  }

  function renderArchiveResultCard(payload, archive) {
    payload = payload || {};
    archive = archive || {};
    var entity = payload.entity_resolution || {};
    var demand = payload.demand_profile || {};
    var lead = payload.lead_score || {};
    var risk = payload.risk || {};
    var companyProfile = payload.company_profile || {};
    var quality = archiveQualityLabel(payload, archive.status);
    var nextActions = Array.isArray(payload.next_actions) ? payload.next_actions.filter(Boolean) : [];
    var summary = archiveText(payload.overview || archive.summary || companyProfile.summary || '暂无最终结论，请重新补全或补充企业信息。', 460);
    var demandText = archiveText(demand.summary || (Array.isArray(demand.product_keywords) ? demand.product_keywords.join('、') : ''), 180) || '暂未判断';
    var riskText = archiveText((Array.isArray(risk.signals) && risk.signals.length ? risk.signals[0] : risk.level) || '未发现明确风险，仍建议人工复核。', 160);
    var nextText = archiveText(nextActions[0] || '先核验主体与联系方式，再按询盘需求回复。', 190);
    return '<div class="ali-result-card">'
      + '<div class="ali-card-head" style="margin-bottom:0.45rem;"><h4>最终结论</h4><span class="ali-badge ' + esc(quality.tone) + '">' + esc(quality.text) + '</span></div>'
      + '<div class="ali-preview" style="-webkit-line-clamp:5;">' + esc(summary) + '</div>'
      + '<div class="ali-result-grid">'
      + '<div class="ali-result-cell"><span>主体核验</span><strong>' + esc(archiveText(entity.reason || entity.status || '-', 170)) + '</strong></div>'
      + '<div class="ali-result-cell"><span>需求判断</span><strong>' + esc(demandText) + '</strong></div>'
      + '<div class="ali-result-cell"><span>跟进建议</span><strong>' + esc(nextText) + '</strong></div>'
      + '<div class="ali-result-cell"><span>客户等级</span><strong>' + esc((lead.grade || archive.grade || '-') + (lead.score || archive.score ? ' · ' + (lead.score || archive.score) : '')) + '</strong></div>'
      + '<div class="ali-result-cell"><span>风险提示</span><strong>' + esc(riskText) + '</strong></div>'
      + '<div class="ali-result-cell"><span>证据状态</span><strong>' + esc((payload.data_quality && payload.data_quality.usable_evidence_count !== undefined ? payload.data_quality.usable_evidence_count : (archive.evidence_count || 0)) + ' 条有效证据') + '</strong></div>'
      + '</div>'
      + '</div>';
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
      ongoing: '进行中',
      unread: '未读',
      replied: '已回复',
      closed: '已关闭'
    }[st] || (status || '-');
    var tone = st === 'active' || st === 'idle' || st === 'replied' ? ' ok' : (st === 'error' || st === 'failed' ? ' err' : ' warn');
    return '<span class="ali-badge' + tone + '">' + esc(label) + '</span>';
  }

  function archiveStatusBadge(status) {
    var st = String(status || '').toLowerCase();
    var label = {
      pending: '待补全',
      queued: '排队中',
      running: '补全中',
      completed: '已完成',
      succeeded: '已完成',
      needs_review: '需人工核验',
      failed: '失败'
    }[st] || (status || '待补全');
    var tone = st === 'completed' || st === 'succeeded' ? ' ok' : (st === 'failed' ? ' err' : ' warn');
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
      var active = state.mode === 'detail' && Number(a.id) === Number(state.activeAccountId) ? ' is-active' : '';
      return '<article class="ali-account-card' + active + '" data-account-id="' + esc(a.id) + '">'
        + '<div class="ali-card-head" style="margin-bottom:0.45rem;">'
        + '<strong>' + esc(a.nickname || ('账号 ' + a.id)) + '</strong>'
        + statusBadge(a.status)
        + '</div>'
        + '<div class="ali-item-meta">'
        + '<span class="ali-badge">询盘 ' + esc(a.inquiry_count || 0) + '</span>'
        + '<span class="ali-badge">客户 ' + esc(a.customer_count || 0) + '</span>'
        + (a.auto_reply_enabled ? '<span class="ali-badge ok">AI接管中</span>' : '<span class="ali-badge warn">AI未接管</span>')
        + statusBadge(a.sync_status || 'idle')
        + '</div>'
        + '<div class="ali-muted" style="margin-top:0.55rem;">上次同步：' + esc(fmtTime(a.last_sync_at)) + '</div>'
        + (a.sync_progress ? '<div class="ali-muted" style="margin-top:0.25rem;">' + esc(a.sync_progress) + '</div>' : '')
        + (a.last_error ? '<div class="ali-muted" style="margin-top:0.25rem;color:#be123c;">' + esc(compact(a.last_error, 120)) + '</div>' : '')
        + '<div class="ali-account-enter">进入账号工作台 →</div>'
        + '</article>';
    }).join('');
  }

  function loadAccounts(silent) {
    if (!silent) setMsg('正在加载阿里账号…');
    return apiJson('/api/alibaba-inquiries/accounts').then(function(data) {
      state.accounts = data.accounts || [];
      if (state.activeAccountId && !currentAccount()) {
        state.activeAccountId = 0;
        state.mode = 'accounts';
      }
      renderAccounts();
      renderWorkspace();
      if (!silent) setMsg('');
    }).catch(function(err) {
      setMsg(err.message || String(err), true);
    });
  }

  function renderWorkspace() {
    var ws = $('aliWorkspace');
    var accountLevel = $('aliAccountLevel');
    var acct = currentAccount();
    if (!ws) return;
    var showDetail = state.mode === 'detail' && !!acct;
    if (accountLevel) accountLevel.classList.toggle('is-active', !showDetail);
    ws.classList.toggle('is-active', showDetail);
    document.querySelectorAll('#content-alibaba-inquiries .ali-account-level-only').forEach(function(el) {
      el.style.display = showDetail ? 'none' : '';
    });
    if (!showDetail) return;
    if ($('aliAccountTitle')) $('aliAccountTitle').textContent = acct.nickname || ('账号 ' + acct.id);
    if ($('aliAccountMeta')) {
      $('aliAccountMeta').textContent = '状态：' + (acct.status || '-') + ' · 询盘 ' + (acct.inquiry_count || 0) + ' · 客户 ' + (acct.customer_count || 0) + ' · ' + (acct.auto_reply_enabled ? 'AI接管中' : 'AI未接管') + ' · 上次同步 ' + fmtTime(acct.last_sync_at);
    }
  }

  function openAccount(accountId) {
    clearArchivePoll();
    state.activeAccountId = Number(accountId || 0);
    state.mode = 'detail';
    state.inquiryOffset = 0;
    state.customerOffset = 0;
    state.selectedInquiryId = '';
    state.selectedProfile = null;
    state.selectedArchiveId = 0;
    state.selectedArchiveId = 0;
    renderAccounts();
    renderWorkspace();
    switchTab('inquiries');
    loadInquiries(true);
    try { window.scrollTo({ top: 0, behavior: 'smooth' }); } catch (e) {}
  }

  function backToAccounts() {
    clearArchivePoll();
    state.mode = 'accounts';
    state.selectedInquiryId = '';
    state.selectedProfile = null;
    if ($('aliReplyText')) $('aliReplyText').value = '';
    if ($('aliDetailBox')) {
      $('aliDetailBox').className = 'ali-empty';
      $('aliDetailBox').textContent = '请选择一条询盘';
    }
    if ($('aliCustomerDetail')) {
      $('aliCustomerDetail').className = 'ali-empty';
      $('aliCustomerDetail').textContent = '请选择客户';
    }
    if ($('aliSyncDetailBtn')) $('aliSyncDetailBtn').disabled = true;
    if ($('aliEnrichArchiveBtn')) $('aliEnrichArchiveBtn').disabled = true;
    if ($('aliDraftReplyBtn')) $('aliDraftReplyBtn').disabled = true;
    if ($('aliSendReplyBtn')) $('aliSendReplyBtn').disabled = true;
    renderAccounts();
    renderWorkspace();
    try { window.scrollTo({ top: 0, behavior: 'smooth' }); } catch (e) {}
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
    if (state.activeTab === 'analysis') {
      loadDocs(true);
      loadSummaries(true);
    }
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
      host.innerHTML = '<div class="ali-empty">暂无询盘。本页只显示本地库数据，请先点“同步全量询盘”。</div>';
      return;
    }
    host.innerHTML = items.map(function(item) {
      var active = item.inquiry_id === state.selectedInquiryId ? ' is-active' : '';
      var displayTitle = item.inquiry_id ? ('询价单号：' + item.inquiry_id) : (item.title || '未命名询盘');
      var updatedDate = fmtDateOnly(item.updated_at || item.last_message_at || item.created_at);
      var people = [item.buyer_name || '', item.country || ''].filter(Boolean).join(' · ');
      var preview = item.preview || item.title || '暂无摘要';
      return '<article class="ali-item ali-inquiry-compact' + active + '" data-inquiry-id="' + esc(item.inquiry_id) + '">'
        + '<div class="ali-item-main">'
        + avatarHtml(item)
        + '<div>'
        + '<div class="ali-item-title">' + esc(displayTitle) + '</div>'
        + (people ? '<div class="ali-muted" style="margin-top:0.18rem;">' + esc(people) + '</div>' : '')
        + '<div class="ali-preview">' + esc(preview) + '</div>'
        + '<div class="ali-item-meta">'
        + (item.archive ? archiveStatusBadge(item.archive.status) : '<span class="ali-badge warn">未生成档案</span>')
        + (updatedDate ? '<span class="ali-badge">更新 ' + esc(updatedDate) + '</span>' : '')
        + '</div>'
        + '</div>'
        + '<div class="ali-inquiry-status">'
        + statusBadge(item.status || 'ongoing')
        + '</div>'
        + '</div>'
        + '</article>';
    }).join('');
  }

  function loadDetail(inquiryId, switchCustomer) {
    if (!state.activeAccountId || !inquiryId) return;
    state.selectedInquiryId = inquiryId;
    if ($('aliSyncDetailBtn')) $('aliSyncDetailBtn').disabled = false;
    if ($('aliEnrichArchiveBtn')) $('aliEnrichArchiveBtn').disabled = false;
    if ($('aliDraftReplyBtn')) $('aliDraftReplyBtn').disabled = false;
    if ($('aliSendReplyBtn')) $('aliSendReplyBtn').disabled = false;
    var box = $('aliDetailBox');
    if (box) box.innerHTML = '<div class="ali-empty">正在加载详情…</div>';
    return apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId) + '/inquiries/' + encodeURIComponent(inquiryId))
      .then(function(data) {
        renderDetail(data);
        if (switchCustomer && data.archive) {
          state.selectedArchiveId = Number(data.archive.id || 0);
          switchTab('customers');
          loadArchiveDetail(state.selectedArchiveId);
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
    var archive = data.archive || null;
    var messages = data.messages || [];
    state.selectedArchiveId = archive && archive.id ? Number(archive.id) : 0;
    var archiveBasics = archive && archive.profile && archive.profile.basics ? archive.profile.basics : {};
    var detailCompany = (profile && profile.company_name) || archiveBasics.company_name || (archive && archive.company_name) || inquiry.company_name || '';
    var detailBuyer = (profile && profile.buyer_name) || archiveBasics.buyer_name || (archive && archive.buyer_name) || inquiry.buyer_name || '';
    var detailCountry = (profile && profile.country) || archiveBasics.country || (archive && archive.country) || inquiry.country || '';
    var detailNotice = detailCompany && detailBuyer && !sameText(detailCompany, detailBuyer)
      ? '<div class="ali-inline-note">公司主体和买家联系人已分开识别：档案按公司补全，沟通按联系人跟进。</div>'
      : '';
    var actionLabel = archive ? '重新生成客户档案' : '生成客户档案';
    var detailActionHtml = '<div class="ali-detail-action-strip">'
      + '<div><strong>客户档案</strong><div class="ali-muted">系统会读取询盘内容并补全公开资料，直接给出可用结论。</div></div>'
      + '<button type="button" class="btn btn-primary btn-sm" data-ali-enrich-current' + (state.busy ? ' disabled' : '') + '>' + actionLabel + '</button>'
      + '</div>';
    var detailSummaryHtml = '<div class="ali-detail-summary-grid">'
      + '<div class="ali-profile-cell"><span>公司</span><strong>' + esc(detailCompany || '-') + '</strong></div>'
      + '<div class="ali-profile-cell"><span>买家</span><strong>' + esc(detailBuyer || '-') + '</strong></div>'
      + '<div class="ali-profile-cell"><span>国家/地区</span><strong>' + esc(detailCountry || '-') + '</strong></div>'
      + '<div class="ali-profile-cell"><span>询盘号</span><strong>' + esc(inquiry.inquiry_id || '-') + '</strong></div>'
      + '</div>' + detailNotice;
    var profileHtml = profile ? renderProfileBlock(profile) : '<div class="ali-empty">还没有阿里原始客户属性，点“同步详情”进入阿里详情页抓取。</div>';
    var archiveHtml = archive
      ? '<div class="ali-archive-section"><div class="ali-card-head" style="margin-bottom:0.2rem;"><h4>客户档案状态</h4>' + archiveStatusBadge(archive.status) + '</div>'
        + '<div class="ali-preview">' + esc(archive.summary || '档案已生成，可到“客户档案”tab 查看补全结论。') + '</div>'
        + '<div class="ali-item-meta"><span class="ali-badge">证据 ' + esc(archive.evidence_count || 0) + '</span><span class="ali-badge">待核验 ' + esc(archive.pending_count || 0) + '</span>'
        + (archive.grade ? '<span class="ali-badge">' + esc(archive.grade) + (archive.score !== null && archive.score !== undefined ? ' · ' + esc(archive.score) : '') + '</span>' : '')
        + '</div></div>'
      : '<div class="ali-archive-section"><div class="ali-card-head" style="margin-bottom:0;"><h4>客户档案状态</h4><span class="ali-badge warn">未生成</span></div><div class="ali-muted">点击上方“生成客户档案”，系统会从询盘提取线索并补充公开资料。</div></div>';
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
      + (fmtDateOnly(inquiry.last_message_at || inquiry.updated_at || inquiry.created_at) ? '<span class="ali-badge">最近消息 ' + esc(fmtDateOnly(inquiry.last_message_at || inquiry.updated_at || inquiry.created_at)) + '</span>' : '')
      + '</div>'
      + detailActionHtml
      + detailSummaryHtml
      + archiveHtml
      + '<div class="ali-card-title" style="margin:0.9rem 0 0.55rem;">阿里原始客户属性</div>'
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

  function runArchiveForInquiry(inquiryId) {
    if (!state.activeAccountId || !inquiryId) return;
    setBusy(true);
    setMsg('正在下发客户档案补全任务…');
    return apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId) + '/inquiries/' + encodeURIComponent(inquiryId) + '/archive/enrich', {
      method: 'POST',
      body: { force: false, max_results: 8 }
    }).then(function(data) {
      var archive = data.archive || {};
      var job = data.job || {};
      state.selectedArchiveId = Number(archive.id || 0);
      setMsg('客户档案任务已下发：' + (job.progress || '排队中'));
      switchTab('customers');
      return loadAccounts(true).then(function() {
        return loadInquiries(false);
      }).then(function() {
        return loadArchiveDetail(state.selectedArchiveId);
      }).then(function() {
        if (job.id) watchArchiveJob(job.id, state.selectedArchiveId);
      });
    }).catch(function(err) {
      setMsg(err.message || String(err), true);
    }).finally(function() {
      setBusy(false);
    });
  }

  function enrichSelectedArchive() {
    if (!state.selectedInquiryId) {
      setMsg('请先选择一条询盘', true);
      return;
    }
    runArchiveForInquiry(state.selectedInquiryId);
  }

  function loadCustomers(reset) {
    if (!state.activeAccountId) return Promise.resolve();
    if (reset) state.customerOffset = 0;
    var q = (($('aliCustomerSearch') || {}).value || '').trim();
    var path = '/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId)
      + '/customer-archives?limit=' + PAGE_SIZE
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
      host.innerHTML = '<div class="ali-empty">暂无客户档案。先在询盘详情里点“生成/更新客户档案”。</div>';
      return;
    }
    host.innerHTML = items.map(function(item) {
      var active = Number(state.selectedArchiveId || 0) === Number(item.id || 0) ? ' is-active' : '';
      var initial = String(item.display_name || item.buyer_name || item.company_name || '?').slice(0, 1).toUpperCase();
      var payload = item.profile || {};
      var quality = archiveQualityLabel(payload, item.status);
      var entity = payload.entity_resolution || {};
      var lead = payload.lead_score || {};
      var nextActions = Array.isArray(payload.next_actions) ? payload.next_actions.filter(Boolean) : [];
      var conclusion = archiveText(payload.overview || item.summary || '点击查看客户档案详情', 260);
      var next = archiveText(nextActions[0] || entity.reason || '', 140);
      return '<article class="ali-item' + active + '" data-customer-archive-id="' + esc(item.id) + '">'
        + '<div class="ali-item-main">'
        + '<span class="ali-avatar">' + esc(initial || '?') + '</span>'
        + '<div>'
        + '<div class="ali-card-head" style="margin-bottom:0;"><div class="ali-item-title">' + esc(item.display_name || item.company_name || item.buyer_name || '未命名客户') + '</div><span class="ali-badge ' + esc(quality.tone) + '">' + esc(quality.text) + '</span></div>'
        + '<div class="ali-item-meta">'
        + ((lead.grade || item.grade) ? '<span class="ali-badge">' + esc(lead.grade || item.grade) + ((lead.score || item.score) !== null && (lead.score || item.score) !== undefined ? ' · ' + esc(lead.score || item.score) : '') + '</span>' : '')
        + (item.country ? '<span class="ali-badge">' + esc(item.country) + '</span>' : '')
        + (item.domain ? '<span class="ali-badge">' + esc(item.domain) + '</span>' : '')
        + '<span class="ali-badge">有效证据 ' + esc((payload.data_quality || {}).usable_evidence_count || item.evidence_count || 0) + '</span>'
        + (item.pending_count ? '<span class="ali-badge warn">待核验 ' + esc(item.pending_count || 0) + '</span>' : '')
        + '<span class="ali-badge">询盘 ' + esc(item.inquiry_id) + '</span>'
        + '</div>'
        + '<div class="ali-preview">' + esc(conclusion) + '</div>'
        + (next ? '<div class="ali-source-note">下一步：' + esc(next) + '</div>' : '')
        + '</div>'
        + '</div>'
        + '</article>';
    }).join('');
  }

  function renderArchiveListBlock(title, items, empty) {
    items = Array.isArray(items) ? items.filter(Boolean) : [];
    if (!items.length) return '<div class="ali-archive-section"><h4>' + esc(title) + '</h4><div class="ali-muted">' + esc(empty || '暂无') + '</div></div>';
    return '<div class="ali-archive-section"><h4>' + esc(title) + '</h4><ul class="ali-archive-list">'
      + items.map(function(x) { return '<li>' + esc(archiveText(x, 220)) + '</li>'; }).join('')
      + '</ul></div>';
  }

  function renderArchiveInsightBlock(title, data, empty) {
    if (!data || typeof data !== 'object') {
      return renderArchiveListBlock(title, [], empty || '暂无调研结果。');
    }
    var parts = [];
    function weakSignalText(x) {
      var text = archiveText(x, 260);
      var compactText = String(text || '').replace(/\s+/g, ' ').trim();
      if (!compactText) return true;
      if (isUrl(compactText)) return true;
      if (/^(公司名称|产品关键词|买家姓名|国家\/地区|官网\/域名)\s*[·:：]/.test(compactText)) return true;
      if (/^(company name|product keyword|buyer name|country|domain)\s*[·:：]/i.test(compactText)) return true;
      if (compactText.length < 18 && !/[。；;,.，]/.test(compactText)) return true;
      return false;
    }
    Object.keys(data).forEach(function(key) {
      var value = data[key];
      if (value === null || value === undefined || value === '' || (Array.isArray(value) && !value.length)) return;
      var label = ARCHIVE_KEY_LABELS[key] || key;
      if (Array.isArray(value)) {
        if (value.length && typeof value[0] === 'object') {
          var visibleItems = value.filter(function(item) {
            if (!item || typeof item !== 'object') return false;
            var confidence = String(item.confidence || '').trim().toLowerCase();
            if (confidence === 'low' || confidence === 'c') return false;
            var summary = item.signal || item.summary || item.desc || item.description || item.title || item.name || item.account || '';
            return !weakSignalText(summary);
          });
          if (!visibleItems.length) return;
          parts.push('<div class="ali-source-grid">' + visibleItems.slice(0, 8).map(function(item) {
            item = item || {};
            return '<div class="ali-evidence-item">'
              + '<strong>' + esc(archiveText(item.platform || item.account || item.name || item.title || label, 90)) + '</strong>'
              + renderBadgeList([item.confidence ? ('置信 ' + item.confidence) : '', item.source || '', item.account || ''], 4)
              + '<div class="ali-preview" style="-webkit-line-clamp:3;">' + esc(archiveText(item.signal || item.summary || item.desc || item, 220)) + '</div>'
              + '</div>';
          }).join('') + '</div>');
        } else {
          var visible = value.filter(function(x) { return !weakSignalText(x); });
          if (!visible.length) return;
          parts.push('<div style="margin-top:0.45rem;"><strong>' + esc(label) + '</strong><ul class="ali-archive-list">'
            + visible.slice(0, 8).map(function(x) { return '<li>' + esc(archiveText(x, 240)) + '</li>'; }).join('')
            + '</ul></div>');
        }
      } else if (typeof value === 'boolean') {
        parts.push('<div class="ali-profile-cell"><span>' + esc(label) + '</span><strong>' + (value ? '是' : '否') + '</strong></div>');
      } else if (typeof value === 'object') {
        parts.push('<div style="margin-top:0.45rem;"><strong>' + esc(label) + '</strong><div class="ali-preview" style="-webkit-line-clamp:4;">' + esc(archiveText(value, 260)) + '</div></div>');
      } else {
        parts.push('<div style="margin-top:0.45rem;"><strong>' + esc(label) + '</strong><div class="ali-preview" style="-webkit-line-clamp:4;">' + esc(archiveText(value, 260)) + '</div></div>');
      }
    });
    if (!parts.length) return renderArchiveListBlock(title, [], empty || '暂无调研结果。');
    return '<div class="ali-archive-section"><h4>' + esc(title) + '</h4>' + parts.join('') + '</div>';
  }

  function renderArchiveDataQuality(payload) {
    payload = payload || {};
    var quality = payload.data_quality || {};
    var resources = Array.isArray(payload.required_resources) ? payload.required_resources : [];
    var pending = payload.pending_review || {};
    var pendingItems = Array.isArray(pending.items) ? pending.items : [];
    var ready = !!quality.ready;
    var items = resources.length ? resources : pendingItems;
    var badge = ready ? '<span class="ali-badge ok">可用于跟进</span>' : '<span class="ali-badge warn">资料不足</span>';
    return '<div class="ali-archive-section ali-quality-section">'
      + '<div class="ali-card-head" style="margin-bottom:0.45rem;"><h4>资料可用性</h4>' + badge + '</div>'
      + '<div class="ali-preview" style="-webkit-line-clamp:4;">' + esc(archiveText(quality.reason || (ready ? '已拿到可核验证据。' : '当前资料不足，不能当作完整客户档案。'), 360)) + '</div>'
      + '<div class="ali-item-meta" style="margin-top:0.45rem;">'
      + '<span class="ali-badge">置信 ' + esc(quality.confidence || '-') + '</span>'
      + '<span class="ali-badge">有效证据 ' + esc(quality.usable_evidence_count || 0) + '</span>'
      + '</div>'
      + (items.length ? '<div style="margin-top:0.6rem;"><strong>需要补充</strong><ul class="ali-archive-list">'
        + items.slice(0, 8).map(function(x) { return '<li>' + esc(archiveText(x, 220)) + '</li>'; }).join('')
        + '</ul></div>' : '')
      + '</div>';
  }

  function renderArchiveSources(catalog, usedSources) {
    catalog = Array.isArray(catalog) ? catalog : [];
    usedSources = Array.isArray(usedSources) ? usedSources : [];
    var usedTypes = {};
    usedSources.forEach(function(src) {
      if (src && src.source_type) usedTypes[String(src.source_type)] = true;
    });
    catalog = catalog.filter(function(src) {
      return src && (src.used || usedTypes[String(src.source_type || '')]);
    });
    if (!usedSources.length && !catalog.length) return '';
    var usedHtml = usedSources.length ? '<div class="ali-source-grid">'
      + usedSources.map(function(src) {
        var fields = Array.isArray(src.fields) ? src.fields : [];
        var count = Number(src.count || 0);
        return '<div class="ali-evidence-item">'
          + '<div class="ali-card-head" style="margin-bottom:0.25rem;"><strong>' + esc(src.title || sourceLabel(src.source_type)) + '</strong><span class="ali-badge ok">已提取 ' + esc(count || 0) + ' 条</span></div>'
          + renderBadgeList(fields, 8)
          + '<div class="ali-source-note">已读取并提炼公开内容，原始链接收起在证据链中，仅用于核验。</div>'
          + '</div>';
      }).join('') + '</div>' : '';
    var catalogHtml = catalog.length ? '<div class="ali-source-grid">'
      + catalog.map(function(src) {
        var state = '本次已使用';
        return '<div class="ali-evidence-item">'
          + '<div class="ali-card-head" style="margin-bottom:0.25rem;"><strong>' + esc(src.title || sourceLabel(src.source_type)) + '</strong><span class="ali-badge ok">' + esc(state) + '</span></div>'
          + '<div class="ali-muted">' + esc(src.category || '') + '</div>'
          + '<div class="ali-preview" style="-webkit-line-clamp:3;">' + esc(src.role || '') + '</div>'
          + '</div>';
      }).join('') + '</div>' : '';
    return '<details class="ali-archive-section ali-collapsed-sources"><summary><strong>信息来源摘要</strong><span class="ali-badge">已折叠</span></summary>'
      + '<div class="ali-muted" style="margin-bottom:0.45rem;">这里只说明系统实际读取了哪些内容，默认不展示长链接。</div>'
      + usedHtml
      + (catalogHtml && !usedHtml ? catalogHtml : '')
      + '</details>';
  }

  function renderArchiveFieldEvidence(fieldEvidence) {
    fieldEvidence = fieldEvidence || {};
    var keys = Object.keys(fieldEvidence).filter(function(k) { return fieldEvidence[k]; });
    if (!keys.length) return renderArchiveListBlock('字段级证据', [], '暂无字段级证据，建议重新补全或人工核验。');
    return '<div class="ali-archive-section"><h4>字段级证据</h4><div class="ali-field-evidence">'
      + keys.map(function(k) {
        var item = fieldEvidence[k] || {};
        var sources = Array.isArray(item.sources) ? item.sources.filter(function(x) { return !isUrl(x); }) : [];
        var notes = Array.isArray(item.source_notes) ? item.source_notes : [];
        return '<div class="ali-evidence-item">'
          + '<div class="ali-card-head" style="margin-bottom:0.25rem;"><strong>' + esc(fieldLabel(k)) + '</strong><span class="ali-badge">置信 ' + esc(item.confidence || '-') + '</span></div>'
          + '<div class="ali-preview" style="-webkit-line-clamp:3;">' + esc(archiveText(item.value || '-', 160)) + '</div>'
          + renderSourceNotes(notes)
          + (item.note ? '<div class="ali-muted" style="margin-top:0.3rem;">' + esc(archiveText(item.note, 160)) + '</div>' : '')
          + (sources.length ? '<div class="ali-item-meta">' + sources.slice(0, 6).map(function(x) {
            return '<span class="ali-badge">' + esc(sourceLabel(x)) + '</span>';
          }).join('') + '</div>' : '')
          + '</div>';
      }).join('')
      + '</div></div>';
  }

  function renderArchiveDetailPayload(data) {
    var host = $('aliCustomerDetail');
    if (!host) return;
    var archive = (data || {}).archive || data || null;
    if (!archive || !archive.id) {
      host.className = 'ali-empty';
      host.textContent = '请选择客户档案';
      return;
    }
    state.selectedArchiveId = Number(archive.id || 0);
    state.selectedArchivePayload = archive;
    host.className = '';
    var payload = archive.profile || {};
    var basics = payload.basics || {};
    var demand = payload.demand_profile || {};
    var risk = payload.risk || {};
    var entity = payload.entity_resolution || {};
    var companyProfile = payload.company_profile || {};
    var socialPresence = payload.social_presence || {};
    var commerceSignals = payload.commerce_signals || {};
    var contactValidation = payload.contact_validation || {};
    var contacts = Array.isArray(payload.contacts) ? payload.contacts : [];
    var pending = archive.pending_review || payload.pending_review || {};
    var fieldEvidence = archive.field_evidence || payload.field_evidence || {};
    var sourceCatalog = archive.source_catalog || payload.source_catalog || ((archive.raw || {}).source_catalog) || [];
    var usedSources = archive.used_sources || payload.used_sources || ((archive.raw || {}).used_sources) || [];
    var researchGaps = Array.isArray(payload.research_gaps) ? payload.research_gaps : [];
    var evidence = (data || {}).evidence || [];
    var jobs = (data || {}).jobs || [];
    var activeJob = jobs.find(function(j) {
      var st = String(j.status || '').toLowerCase();
      return st === 'queued' || st === 'running';
    });
    if (activeJob && activeJob.id && Number(state.activeArchiveJobId || 0) !== Number(activeJob.id || 0)) {
      watchArchiveJob(activeJob.id, archive.id);
    }
    var companyName = basics.company_name || archive.company_name || '';
    var buyerName = basics.buyer_name || archive.buyer_name || '';
    var countryName = basics.country || archive.country || '';
    var archiveNotice = companyName && buyerName && !sameText(companyName, buyerName)
      ? '<div class="ali-inline-note">已区分公司主体和买家联系人：公开资料按公司主体补全，后续沟通按联系人跟进。</div>'
      : '';
    var topActions = '<div class="ali-detail-action-strip">'
      + '<div><strong>档案操作</strong><div class="ali-muted">优先看上方结论；来源和补全任务已收起在底部。</div></div>'
      + '<div class="ali-actions"><button type="button" class="btn btn-primary btn-sm" data-ali-rerun-archive="' + esc(archive.id) + '">重新补全</button>'
      + '<button type="button" class="btn btn-ghost btn-sm" data-ali-edit-archive="' + esc(archive.id) + '">编辑档案</button></div>'
      + '</div><div id="aliArchiveLiveStatus" class="ali-inline-note" style="display:none;"></div>';
    var contactHtml = contacts.length ? '<div class="ali-archive-section"><h4>联系人</h4><div class="ali-evidence">'
      + contacts.map(function(c) {
        return '<div class="ali-evidence-item"><strong>' + esc(archiveText(c.name || c.role || '联系人', 90)) + '</strong>'
          + '<div class="ali-item-meta">'
          + (c.role ? '<span class="ali-badge">' + esc(archiveText(c.role, 80)) + '</span>' : '')
          + (c.email ? '<span class="ali-badge">' + esc(archiveText(c.email, 80)) + '</span>' : '')
          + (c.phone ? '<span class="ali-badge">' + esc(archiveText(c.phone, 80)) + '</span>' : '')
          + (c.confidence ? '<span class="ali-badge">置信 ' + esc(c.confidence) + '</span>' : '')
          + '</div><div class="ali-muted" style="margin-top:0.35rem;">来源：' + esc(sourceLabel(c.source || '-')) + '</div></div>';
      }).join('') + '</div></div>' : renderArchiveListBlock('联系人', [], '暂未从证据中确认联系人。');
    var evidenceHtml = evidence.length ? '<details class="ali-archive-section ali-collapsed-sources"><summary><strong>证据链</strong><span class="ali-badge">' + esc(evidence.length) + ' 条</span></summary><div class="ali-evidence">'
      + evidence.map(function(e) {
        var raw = e.raw || {};
        var badges = [e.source_label || sourceLabel(e.source_type || '-'), '置信 ' + (e.confidence || '-')];
        if (raw.field) badges.push(fieldLabel(raw.field));
        return '<div class="ali-evidence-item"><strong>' + esc(archiveText(e.title || e.source_type || '证据', 100)) + '</strong>'
          + '<div class="ali-item-meta">' + badges.map(function(x) { return '<span class="ali-badge">' + esc(x) + '</span>'; }).join('') + '</div>'
          + (e.snippet ? '<div class="ali-source-note">' + esc(archiveText(e.snippet, 240)) + '</div>' : '')
          + (raw.purpose ? '<div class="ali-muted" style="margin-top:0.3rem;">调研目的：' + esc(archiveText(raw.purpose, 160)) + '</div>' : '')
          + renderSourceLink(e.url)
          + '</div>';
      }).join('') + '</div></details>' : renderArchiveListBlock('证据链', [], '暂无外部证据。');
    var jobHtml = jobs.length ? '<details class="ali-archive-section ali-collapsed-sources"><summary><strong>补全任务</strong><span class="ali-badge">' + esc(jobs.length) + ' 条</span></summary><div class="ali-evidence">'
      + jobs.map(function(j) {
        var failedAction = String(j.status || '').toLowerCase() === 'failed'
          ? '<button type="button" class="btn btn-ghost btn-sm" data-ali-resume-archive-job="' + esc(j.id) + '">恢复</button>'
          : '';
        return '<div class="ali-evidence-item"><div class="ali-card-head" style="margin-bottom:0.2rem;"><strong>' + esc(j.progress || '补全任务') + '</strong>' + archiveStatusBadge(j.status) + '</div>'
          + '<div class="ali-muted">' + esc(fmtTime(j.created_at)) + (j.error ? ' · ' + esc(j.error) : '') + '</div>'
          + (failedAction ? '<div class="ali-actions" style="margin-top:0.45rem;">' + failedAction + '</div>' : '')
          + '</div>';
      }).join('') + '</div></details>' : '';
    host.innerHTML = topActions
      + '<div class="ali-archive-hero">'
      + '<span class="ali-avatar" style="width:64px;height:64px;">' + esc(String(archive.display_name || '?').slice(0, 1).toUpperCase()) + '</span>'
      + '<div><div class="ali-item-title">' + esc(archive.display_name || archive.company_name || archive.buyer_name || '未命名客户') + '</div>'
      + '<div class="ali-item-meta">' + archiveStatusBadge(archive.status)
      + (archive.grade ? '<span class="ali-badge">' + esc(archive.grade) + (archive.score !== null && archive.score !== undefined ? ' · ' + esc(archive.score) : '') + '</span>' : '')
      + '<span class="ali-badge">询盘 ' + esc(archive.inquiry_id || '-') + '</span>'
      + '<span class="ali-badge">关联 ' + esc((archive.linked_inquiry_ids || []).length || 1) + '</span></div></div></div>'
      + '<div class="ali-profile-grid">'
      + '<div class="ali-profile-cell"><span>公司</span><strong>' + esc(companyName || '-') + '</strong></div>'
      + '<div class="ali-profile-cell"><span>买家</span><strong>' + esc(buyerName || '-') + '</strong></div>'
      + '<div class="ali-profile-cell"><span>国家/地区</span><strong>' + esc(countryName || '-') + '</strong></div>'
      + '<div class="ali-profile-cell"><span>官网/域名</span><strong>' + esc(basics.domain || archive.domain || '-') + '</strong></div>'
      + '<div class="ali-profile-cell"><span>邮箱</span><strong>' + esc(basics.email || archive.email || '-') + '</strong></div>'
      + '<div class="ali-profile-cell"><span>电话</span><strong>' + esc(basics.phone || archive.phone || '-') + '</strong></div>'
      + '</div>' + archiveNotice
      + renderArchiveResultCard(payload, archive)
      + renderArchiveDataQuality(payload)
      + renderArchiveInsightBlock('公司核验', companyProfile, '暂无可核验公司详情，建议补充公司主体、官网或企业库数据。')
      + renderArchiveInsightBlock('社媒账号核验', socialPresence, '暂无可归属该客户的社媒账号。')
      + renderArchiveInsightBlock('商品/交易证据', commerceSignals, '暂无可核验商品、店铺或交易侧证据。')
      + renderArchiveInsightBlock('联系方式核验', contactValidation, '暂无联系方式核验信息。')
      + contactHtml
      + renderArchiveListBlock('风险信号', risk.signals || [], '暂无明确风险信号，仍建议人工复核。')
      + renderArchiveListBlock('下一步建议', payload.next_actions || [], '暂无建议。')
      + renderArchiveFieldEvidence(fieldEvidence)
      + renderArchiveSources(sourceCatalog, usedSources)
      + renderArchiveListBlock('待人工核验', pending.items || [], '暂无待核验项。')
      + renderArchiveListBlock('调研缺口', researchGaps, '暂无调研缺口。')
      + evidenceHtml
      + jobHtml
      + '<div class="ali-actions" style="margin-top:0.75rem;"><button type="button" class="btn btn-primary btn-sm" data-ali-rerun-archive="' + esc(archive.id) + '">重新补全</button><button type="button" class="btn btn-ghost btn-sm" data-ali-edit-archive="' + esc(archive.id) + '">编辑档案</button><button type="button" class="btn btn-ghost btn-sm" data-ali-open-inquiry="' + esc(archive.inquiry_id) + '">查看原始询盘</button></div>';
  }

  function renderCustomerDetail(archive) {
    renderArchiveDetailPayload({ archive: archive });
  }

  function loadArchiveDetail(archiveId) {
    if (!archiveId) return Promise.resolve();
    state.selectedArchiveId = Number(archiveId || 0);
    var host = $('aliCustomerDetail');
    if (host) host.innerHTML = '<div class="ali-empty">正在加载档案详情…</div>';
    return apiJson('/api/alibaba-inquiries/customer-archives/' + encodeURIComponent(archiveId))
      .then(function(data) {
        renderArchiveDetailPayload(data);
        loadCustomers(false);
      }).catch(function(err) {
      setMsg(err.message || String(err), true);
      });
  }

  function rerunArchive(archiveId) {
    if (!archiveId) return;
    setBusy(true);
    setMsg('正在下发重新补全任务…');
    return apiJson('/api/alibaba-inquiries/customer-archives/' + encodeURIComponent(archiveId) + '/rerun', {
      method: 'POST',
      body: { force: true, max_results: 8 }
    }).then(function(data) {
      var archive = data.archive || {};
      var job = data.job || {};
      state.selectedArchiveId = Number(archive.id || archiveId);
      setMsg('重新补全任务已下发：' + (job.progress || '排队中'));
      return loadAccounts(true).then(function() {
        return loadArchiveDetail(state.selectedArchiveId);
      }).then(function() {
        if (job.id) watchArchiveJob(job.id, state.selectedArchiveId);
      });
    }).catch(function(err) {
      setMsg(err.message || String(err), true);
    }).finally(function() {
      setBusy(false);
    });
  }

  function showArchiveEditModal(show) {
    var modal = $('aliArchiveEditModal');
    if (!modal) return;
    modal.classList.toggle('is-visible', !!show);
    if (!show) return;
    var archive = state.selectedArchivePayload || {};
    var profile = archive.profile || {};
    var basics = profile.basics || {};
    var setVal = function(id, value) {
      var el = $(id);
      if (el) el.value = value || '';
    };
    setVal('aliArchiveEditName', archive.display_name || archive.company_name || archive.buyer_name || '');
    setVal('aliArchiveEditCompany', basics.company_name || archive.company_name || '');
    setVal('aliArchiveEditBuyer', basics.buyer_name || archive.buyer_name || '');
    setVal('aliArchiveEditCountry', basics.country || archive.country || '');
    setVal('aliArchiveEditDomain', basics.domain || archive.domain || '');
    setVal('aliArchiveEditEmail', basics.email || archive.email || '');
    setVal('aliArchiveEditPhone', basics.phone || archive.phone || '');
    setVal('aliArchiveEditGrade', archive.grade || '');
    setVal('aliArchiveEditScore', archive.score === null || archive.score === undefined ? '' : archive.score);
    setVal('aliArchiveEditNotes', '');
  }

  function saveArchiveEdit() {
    var archiveId = Number(state.selectedArchiveId || 0);
    if (!archiveId) return;
    var value = function(id) {
      return (($(id) || {}).value || '').trim();
    };
    var scoreText = value('aliArchiveEditScore');
    var body = {
      display_name: value('aliArchiveEditName'),
      grade: value('aliArchiveEditGrade'),
      score: scoreText ? Number(scoreText) : null,
      basics: {
        company_name: value('aliArchiveEditCompany'),
        buyer_name: value('aliArchiveEditBuyer'),
        country: value('aliArchiveEditCountry'),
        domain: value('aliArchiveEditDomain'),
        email: value('aliArchiveEditEmail'),
        phone: value('aliArchiveEditPhone')
      },
      notes: value('aliArchiveEditNotes')
    };
    setBusy(true);
    setMsg('正在保存客户档案修正…');
    apiJson('/api/alibaba-inquiries/customer-archives/' + encodeURIComponent(archiveId), {
      method: 'PATCH',
      body: body
    }).then(function(data) {
      showArchiveEditModal(false);
      state.selectedArchivePayload = data.archive || null;
      setMsg('客户档案已保存');
      return loadArchiveDetail(archiveId);
    }).catch(function(err) {
      setMsg(err.message || String(err), true);
    }).finally(function() {
      setBusy(false);
    });
  }

  function resumeArchiveJob(jobId) {
    if (!jobId) return;
    setBusy(true);
    setMsg('正在恢复客户档案补全任务…');
    apiJson('/api/alibaba-inquiries/customer-archive-jobs/' + encodeURIComponent(jobId) + '/resume', {
      method: 'POST',
      body: {}
    }).then(function(data) {
      var job = data.job || {};
      var archive = data.archive || {};
      if (archive.id) state.selectedArchiveId = Number(archive.id);
      setMsg('补全任务已恢复：' + (job.progress || '排队中'));
      if (job.id) watchArchiveJob(job.id, state.selectedArchiveId);
      return loadArchiveDetail(state.selectedArchiveId);
    }).catch(function(err) {
      setMsg(err.message || String(err), true);
    }).finally(function() {
      setBusy(false);
    });
  }

  function loadDocs(silent) {
    if (!state.activeAccountId) return Promise.resolve();
    return apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId) + '/training-docs')
      .then(function(data) {
        state.docs = data.items || [];
        renderDocs(state.docs);
        renderStrategyDocChoices(state.docs);
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

  function renderStrategyDocChoices(items) {
    var host = $('aliStrategyDocList');
    if (!host) return;
    items = items || [];
    if (!items.length) {
      host.innerHTML = '<div class="ali-empty">暂无可选资料。可以先在“话术资料”上传资料、FAQ 或话术文档。</div>';
      return;
    }
    host.innerHTML = items.map(function(item) {
      return '<label class="ali-item" style="display:flex;gap:0.55rem;align-items:flex-start;cursor:pointer;">'
        + '<input type="checkbox" data-ali-strategy-doc-id="' + esc(item.id) + '" style="width:auto;margin-top:0.18rem;">'
        + '<span style="min-width:0;">'
        + '<strong>' + esc(item.title || item.filename || '资料') + '</strong>'
        + '<span class="ali-item-meta" style="display:flex;"><span class="ali-badge">' + esc(item.kind || 'script') + '</span>'
        + '<span class="ali-badge">' + esc(fmtTime(item.created_at)) + '</span></span>'
        + '<span class="ali-preview" style="display:block;">' + esc(item.content_preview || '') + '</span>'
        + '</span>'
        + '</label>';
    }).join('');
  }

  function uploadDoc() {
    if (!state.activeAccountId) return;
    var fileInput = $('aliDocFile');
    var textContent = (($('aliDocContent') || {}).value || '').trim();
    var hasFile = !!(fileInput && fileInput.files && fileInput.files[0]);
    if (!hasFile && !textContent) {
      setMsg('请选择要上传的资料文件', true);
      return;
    }
    var form = new FormData();
    if (hasFile) form.append('file', fileInput.files[0]);
    form.append('content', textContent);
    form.append('kind', (($('aliDocKind') || {}).value || 'script'));
    form.append('title', (($('aliDocTitle') || {}).value || '').trim());
    setBusy(true);
    setMsg('正在上传资料…');
    apiUpload('/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId) + '/training-docs', form)
      .then(function() {
        if (fileInput) fileInput.value = '';
        if ($('aliDocTitle')) $('aliDocTitle').value = '';
        if ($('aliDocContent')) $('aliDocContent').value = '';
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
        state.summaries = data.items || [];
        renderSummaries(state.summaries);
        if (!silent) setMsg('回复策略已刷新');
      }).catch(function(err) {
        setMsg(err.message || String(err), true);
      });
  }

  function renderLegacySummaries(items) {
    var host = $('aliSummaryList');
    if (!host) return;
    if (!items.length) {
      host.innerHTML = '<div class="ali-empty">暂无回复策略。同步询盘或上传资料/FAQ/话术后，点“生成回复策略”。</div>';
      return;
    }
    host.innerHTML = items.map(function(item) {
      var meta = item.meta || {};
      var good = (meta.good_scripts || []).slice(0, 5).map(function(x) { return '<span class="ali-badge ok">' + esc(compact(x, 80)) + '</span>'; }).join('');
      var neg = (meta.negative_scripts || []).slice(0, 5).map(function(x) { return '<span class="ali-badge err">' + esc(compact(x, 80)) + '</span>'; }).join('');
      return '<article class="ali-item">'
        + '<div class="ali-card-head" style="margin-bottom:0.35rem;">'
        + '<strong>' + esc(meta.overview || '历史询盘回复策略') + '</strong>'
        + '<span class="ali-badge">' + esc(fmtTime(item.created_at)) + '</span>'
        + '</div>'
        + (good ? '<div class="ali-muted" style="margin-top:0.5rem;">好话术</div><div class="ali-item-meta">' + good + '</div>' : '')
        + (neg ? '<div class="ali-muted" style="margin-top:0.5rem;">负面话术</div><div class="ali-item-meta">' + neg + '</div>' : '')
        + '<details style="margin-top:0.6rem;"><summary class="ali-muted" style="cursor:pointer;">查看完整 JSON</summary>'
        + '<pre style="white-space:pre-wrap;font-size:0.76rem;line-height:1.5;max-height:320px;overflow:auto;">' + esc(item.content || '') + '</pre></details>'
        + '</article>';
    }).join('');
  }

  function renderSummaries(items) {
    var host = $('aliSummaryList');
    if (!host) return;
    if (!items.length) {
      host.innerHTML = '<div class="ali-empty">暂无回复策略。同步询盘或上传资料后，点击右上角“生成回复策略”。</div>';
      return;
    }
    var sorted = items.slice().sort(function(a, b) {
      return Number(!!b.enabled) - Number(!!a.enabled) || String(b.created_at || '').localeCompare(String(a.created_at || ''));
    });
    host.innerHTML = sorted.map(function(item) {
      var meta = item.meta || {};
      var source = meta.source || {};
      var good = (meta.good_scripts || []).slice(0, 5).map(function(x) { return '<span class="ali-badge ok">' + esc(compact(x, 80)) + '</span>'; }).join('');
      var neg = (meta.negative_scripts || []).slice(0, 5).map(function(x) { return '<span class="ali-badge err">' + esc(compact(x, 80)) + '</span>'; }).join('');
      var expanded = Number(state.expandedSummaryId || 0) === Number(item.id);
      return '<article class="ali-item' + (expanded ? ' is-active' : '') + '" data-ali-summary-id="' + esc(item.id) + '" style="cursor:pointer;">'
        + '<div class="ali-card-head" style="margin-bottom:0.35rem;">'
        + '<strong>' + esc(meta.overview || '阿里询盘回复策略') + '</strong>'
        + '<div class="ali-row-actions">'
        + (item.enabled ? '<span class="ali-badge ok">AI接管中</span><button type="button" class="btn btn-ghost btn-sm" data-ali-disable-summary="' + esc(item.id) + '">取消接管</button>' : '<button type="button" class="btn btn-primary btn-sm" data-ali-enable-summary="' + esc(item.id) + '">启用并接管</button>')
        + '<span class="ali-badge">' + esc(fmtTime(item.created_at)) + '</span>'
        + '</div>'
        + '</div>'
        + '<div class="ali-item-meta"><span class="ali-badge">询盘 ' + esc(source.inquiry_count || item.source_count || 0) + '</span>'
        + '<span class="ali-badge">消息 ' + esc(source.message_count || 0) + '</span>'
        + '<span class="ali-badge">资料 ' + esc(source.doc_count || 0) + '</span></div>'
        + '<div class="ali-summary-detail" style="' + (expanded ? '' : 'display:none;') + '">'
        + (meta.history_analysis && meta.history_analysis.length ? '<div class="ali-muted" style="margin-top:0.5rem;">历史数据分析</div><div class="ali-preview">' + esc(meta.history_analysis.slice(0, 3).join('；')) + '</div>' : '')
        + (meta.doc_strategy && meta.doc_strategy.length ? '<div class="ali-muted" style="margin-top:0.5rem;">资料/FAQ回复策略</div><div class="ali-preview">' + esc(meta.doc_strategy.slice(0, 3).join('；')) + '</div>' : '')
        + (meta.safety_rules && meta.safety_rules.length ? '<div class="ali-muted" style="margin-top:0.5rem;">安全边界</div><div class="ali-preview">' + esc(meta.safety_rules.slice(0, 4).join('；')) + '</div>' : '')
        + (good ? '<div class="ali-muted" style="margin-top:0.5rem;">好话术</div><div class="ali-item-meta">' + good + '</div>' : '')
        + (neg ? '<div class="ali-muted" style="margin-top:0.5rem;">负面话术</div><div class="ali-item-meta">' + neg + '</div>' : '')
        + '<details style="margin-top:0.6rem;"><summary class="ali-muted" style="cursor:pointer;">查看完整 JSON</summary>'
        + '<pre style="white-space:pre-wrap;font-size:0.76rem;line-height:1.5;max-height:320px;overflow:auto;">' + esc(item.content || '') + '</pre></details>'
        + '</div>'
        + '</article>';
    }).join('');
  }

  function analyzeHistory() {
    if (!state.activeAccountId) return;
    var docIds = Array.prototype.slice.call(document.querySelectorAll('#content-alibaba-inquiries [data-ali-strategy-doc-id]:checked'))
      .map(function(el) { return Number(el.getAttribute('data-ali-strategy-doc-id') || 0); })
      .filter(Boolean);
    setBusy(true);
    setMsg('AI 正在基于全量询盘和已选资料生成回复策略…');
    apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId) + '/analyze', {
      method: 'POST',
      body: { doc_ids: docIds }
    }).then(function() {
      setMsg('回复策略已生成，请在策略列表中选择启用');
      return loadSummaries(true);
    }).catch(function(err) {
      setMsg(err.message || String(err), true);
    }).finally(function() {
      setBusy(false);
    });
  }

  function syncAll() {
    if (!state.activeAccountId) return;
    setBusy(true);
    setMsg('正在同步全量询盘，并增量抓取每个会话的新消息/客户属性…');
    apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId) + '/sync', {
      method: 'POST',
      body: {
        max_scrolls: 180,
        max_pages: 200,
        stop_after_idle_rounds: 8,
        sync_details: true,
        detail_limit: 0
      }
    }).then(function(data) {
      var msg = data.message || '同步完成';
      if (Number(data.new_messages_count || 0) || Number(data.reply_candidate_count || 0)) {
        msg += '\n新增消息 ' + Number(data.new_messages_count || 0) + ' 条，待回复 ' + Number(data.reply_candidate_count || 0) + ' 条';
      }
      if (Number(data.details_skipped || 0)) {
        msg += '\n已跳过同步完成的会话 ' + Number(data.details_skipped || 0) + ' 个';
      }
      if (data.auto_reply && data.auto_reply.enabled) {
        msg += '\nAI接管：已回复 ' + Number(data.auto_reply.sent || 0)
          + ' 条，失败 ' + Number(data.auto_reply.failed || 0)
          + ' 条，跳过 ' + Number(data.auto_reply.skipped || 0) + ' 条';
      }
      setMsg(msg);
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
    setMsg('正在拉起阿里账号浏览器并检测登录状态…');
    apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId) + '/login', { method: 'POST', body: {} })
      .then(function(data) {
        setMsg(data.message || (data.logged_in ? '账号已登录' : '已打开浏览器，请完成登录'));
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
      return loadAccounts(true).then(function() {
        if (state.activeAccountId) openAccount(state.activeAccountId);
      });
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
      var enrichCurrentBtn = ev.target.closest('[data-ali-enrich-current]');
      if (enrichCurrentBtn) {
        ev.preventDefault();
        ev.stopPropagation();
        enrichSelectedArchive();
        return;
      }
      var account = ev.target.closest('.ali-account-card[data-account-id]');
      if (account) {
        openAccount(account.getAttribute('data-account-id'));
        return;
      }
      var avatar = ev.target.closest('[data-avatar-inquiry]');
      if (avatar) {
        ev.stopPropagation();
        loadDetail(avatar.getAttribute('data-avatar-inquiry'));
        return;
      }
      var inquiry = ev.target.closest('.ali-item[data-inquiry-id]');
      if (inquiry) {
        loadDetail(inquiry.getAttribute('data-inquiry-id'));
        return;
      }
      var archiveCard = ev.target.closest('.ali-item[data-customer-archive-id]');
      if (archiveCard) {
        loadArchiveDetail(archiveCard.getAttribute('data-customer-archive-id'));
        return;
      }
      var rerunArchiveBtn = ev.target.closest('[data-ali-rerun-archive]');
      if (rerunArchiveBtn) {
        ev.preventDefault();
        ev.stopPropagation();
        rerunArchive(rerunArchiveBtn.getAttribute('data-ali-rerun-archive'));
        return;
      }
      var resumeArchiveJobBtn = ev.target.closest('[data-ali-resume-archive-job]');
      if (resumeArchiveJobBtn) {
        ev.preventDefault();
        ev.stopPropagation();
        resumeArchiveJob(resumeArchiveJobBtn.getAttribute('data-ali-resume-archive-job'));
        return;
      }
      var editArchiveBtn = ev.target.closest('[data-ali-edit-archive]');
      if (editArchiveBtn) {
        ev.preventDefault();
        ev.stopPropagation();
        showArchiveEditModal(true);
        return;
      }
      var openInquiryBtn = ev.target.closest('[data-ali-open-inquiry]');
      if (openInquiryBtn) {
        ev.preventDefault();
        ev.stopPropagation();
        state.selectedInquiryId = openInquiryBtn.getAttribute('data-ali-open-inquiry') || '';
        switchTab('inquiries');
        loadDetail(state.selectedInquiryId);
        return;
      }
      var enableSummary = ev.target.closest('[data-ali-enable-summary]');
      if (enableSummary) {
        ev.preventDefault();
        ev.stopPropagation();
        var summaryId = enableSummary.getAttribute('data-ali-enable-summary');
        if (!state.activeAccountId || !summaryId) return;
        setBusy(true);
        setMsg('正在启用回复策略…');
        apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId) + '/summaries/' + encodeURIComponent(summaryId) + '/enable', {
          method: 'POST',
          body: {}
        }).then(function() {
          setMsg('回复策略已启用，后续自动接管会使用这条策略');
          return loadAccounts(true).then(function() { return loadSummaries(true); });
        }).catch(function(err) {
          setMsg(err.message || String(err), true);
        }).finally(function() {
          setBusy(false);
        });
        return;
      }
      var disableSummary = ev.target.closest('[data-ali-disable-summary]');
      if (disableSummary) {
        ev.preventDefault();
        ev.stopPropagation();
        if (!state.activeAccountId) return;
        setBusy(true);
        setMsg('正在取消回复策略接管…');
        apiJson('/api/alibaba-inquiries/accounts/' + encodeURIComponent(state.activeAccountId) + '/auto-reply/config', {
          method: 'POST',
          body: { enabled: false }
        }).then(function() {
          setMsg('已取消AI接管，后续同步不会自动回复');
          return loadAccounts(true).then(function() { return loadSummaries(true); });
        }).catch(function(err) {
          setMsg(err.message || String(err), true);
        }).finally(function() {
          setBusy(false);
        });
        return;
      }
      var summaryCard = ev.target.closest('[data-ali-summary-id]');
      if (summaryCard) {
        if (ev.target.closest('button,input,textarea,select,summary,details,pre')) return;
        var sid = Number(summaryCard.getAttribute('data-ali-summary-id') || 0);
        state.expandedSummaryId = Number(state.expandedSummaryId || 0) === sid ? 0 : sid;
        renderSummaries(state.summaries);
        return;
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
    bind('aliCloseArchiveEditModal', function() { showArchiveEditModal(false); });
    bind('aliSaveArchiveEditBtn', saveArchiveEdit);
    bind('aliCreateAccountBtn', createAccount);
    bind('aliBackAccountsBtn', backToAccounts);
    bind('aliOpenLoginBtn', openLogin);
    bind('aliSyncBtn', syncAll);
    bind('aliInquirySearchBtn', function() { loadInquiries(true); });
    bind('aliInquiryReloadBtn', function() { loadInquiries(true); });
    bind('aliSyncDetailBtn', syncSelectedDetail);
    bind('aliEnrichArchiveBtn', enrichSelectedArchive);
    bind('aliDraftReplyBtn', draftReply);
    bind('aliSendReplyBtn', sendReply);
    bind('aliCustomerSearchBtn', function() { loadCustomers(true); });
    bind('aliCustomerReloadBtn', function() { loadCustomers(true); });
    bind('aliUploadDocBtn', uploadDoc);
    bind('aliReloadDocsBtn', function() { loadDocs(false); });
    bind('aliStrategyReloadDocsBtn', function() { loadDocs(false); });
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
    state.mode = state.mode || 'accounts';
    loadAccounts(true).then(function() {
      if (state.mode === 'detail' && state.activeAccountId) loadInquiries(true);
    });
  };

  window._openAlibabaInquiriesView = function() {
    if (typeof window.registerLobsterView === 'function') {
      window.registerLobsterView('alibaba-inquiries', {
        html: '/static/views/alibaba-inquiries.html?v=20260724-strategy-disable',
        scripts: '/static/js/alibaba-inquiries.js?v=20260724-strategy-disable',
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
