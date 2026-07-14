(function() {
  var PAGE_SIZE = 20;
  var DEFAULT_SOURCES = ['google', 'bing', 'linkedin', 'x', 'tiktok', 'facebook', 'crunchbase', 'zoominfo', 'tradeatlas', '10times', 'glassdoor'];
  var SOURCE_LOGOS = {
    google: 'G',
    bing: 'b',
    yandex: 'Y',
    yahoo: 'Y!',
    linkedin: 'in',
    x: 'X',
    tiktok: '♪',
    reddit: 'r/',
    facebook: 'f',
    whatsapp: '☎',
    crunchbase: 'cb',
    zoominfo: 'Z',
    apollo: 'A',
    tradeatlas: 'TA',
    '10times': '10',
    glassdoor: 'gd',
    query: 'Q'
  };
  var SOURCE_LABELS = {};

  var state = {
    tab: 'create',
    busy: false,
    sources: [],
    jobs: [],
    crm: [],
    activeJobId: '',
    activeContactId: '',
    crmPage: 1,
    crmTotal: 0,
    pollTimer: null
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
    if (typeof getOrCreateInstallationId === 'function') h['X-Installation-Id'] = getOrCreateInstallationId();
    h['Content-Type'] = 'application/json';
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
    if (!base) return Promise.reject(new Error('未配置服务器 API_BASE'));
    var req = { method: opts.method || 'GET', headers: authHeaderJson() };
    if (opts.body !== undefined) req.body = JSON.stringify(opts.body || {});
    return fetch(base + path, req).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok || data.ok === false) throw new Error(parseErr(data, '请求失败'));
        return data;
      });
    });
  }

  function setMsg(text, isErr) {
    var node = $('globalLeadsMsg');
    if (!node) return;
    node.textContent = text || '';
    node.className = 'gl-msg' + (isErr ? ' err' : '');
    node.style.display = text ? 'block' : 'none';
  }

  function setBusy(busy) {
    state.busy = !!busy;
    ['glStartBtn', 'glResetBtn'].forEach(function(id) {
      var el = $(id);
      if (el) el.disabled = state.busy;
    });
  }

  function fmtTime(value) {
    if (!value) return '';
    try {
      var d = new Date(value);
      if (!isNaN(d.getTime())) return d.toLocaleString();
    } catch (e) {}
    return String(value || '');
  }

  function lines(value) {
    return String(value || '')
      .split(/\r?\n|[,，;；]+/)
      .map(function(x) { return x.trim(); })
      .filter(Boolean);
  }

  function unique(values) {
    var seen = {};
    var out = [];
    (values || []).forEach(function(value) {
      var text = String(value || '').trim();
      var key = text.toLowerCase();
      if (text && !seen[key]) {
        seen[key] = true;
        out.push(text);
      }
    });
    return out;
  }

  function compact(value, len) {
    var text = String(value || '').replace(/\s+/g, ' ').trim();
    len = len || 160;
    return text.length > len ? text.slice(0, len - 1) + '…' : text;
  }

  function sourceLabel(id) {
    return SOURCE_LABELS[id] || id || '-';
  }

  function statusLabel(status) {
    status = String(status || '').toLowerCase();
    return {
      queued: '排队中',
      running: '执行中',
      collecting: '采集中',
      completed: '已完成',
      failed: '失败',
      ready: '搜索入口',
      needs_connector: '待接入',
      needs_input: '需补条件',
      active: '启用',
      new: '新线索'
    }[status] || status || '-';
  }

  function statusBadge(status) {
    var st = String(status || '').toLowerCase();
    var tone = st === 'completed' || st === 'active' || st === 'new' ? ' ok' : (st === 'failed' ? ' err' : ' warn');
    return '<span class="gl-badge' + tone + '">' + esc(statusLabel(st)) + '</span>';
  }

  function selectedSources() {
    return Array.prototype.slice.call(document.querySelectorAll('#content-global-leads input[name="glSource"]:checked'))
      .map(function(input) { return input.value; })
      .filter(Boolean);
  }

  function readForm() {
    return {
      title: '',
      company_name: (($('glCompanyInput') || {}).value || '').trim(),
      domain: (($('glDomainInput') || {}).value || '').trim(),
      region: (($('glRegionInput') || {}).value || '').trim(),
      target_profile: (($('glTargetInput') || {}).value || '').trim(),
      keywords: lines(($('glKeywordsInput') || {}).value),
      reddit_communities: lines(($('glRedditInput') || {}).value),
      max_items: Math.max(10, Math.min(300, Number(($('glMaxItemsInput') || {}).value || 80))),
      sources: selectedSources(),
      auto_run: true
    };
  }

  function searchTerms(data) {
    return unique([data.company_name, data.domain, data.region].concat(data.keywords || [])).filter(Boolean);
  }

  function switchTab(tab) {
    state.tab = tab || 'create';
    document.querySelectorAll('#content-global-leads [data-gl-tab]').forEach(function(btn) {
      btn.classList.toggle('is-active', btn.getAttribute('data-gl-tab') === state.tab);
    });
    document.querySelectorAll('#content-global-leads [data-gl-panel]').forEach(function(panel) {
      panel.classList.toggle('is-active', panel.getAttribute('data-gl-panel') === state.tab);
    });
    if (state.tab === 'crm') loadCrm(true);
    if (state.tab === 'jobs') loadJobs(true);
    if (state.tab === 'dashboard') renderDashboard();
  }

  function renderSourceGrid() {
    var host = $('glSourceGrid');
    if (!host) return;
    var rows = state.sources.length ? state.sources : DEFAULT_SOURCES.map(function(id) { return { id: id, name: sourceLabel(id), status: 'search_link' }; });
    host.innerHTML = rows.map(function(row) {
      var checked = DEFAULT_SOURCES.indexOf(row.id) >= 0 ? ' checked' : '';
      return '<label class="gl-source-card" title="' + esc(sourceStatusText(row)) + '">'
        + '<input type="checkbox" name="glSource" value="' + esc(row.id) + '"' + checked + '>'
        + '<span class="gl-logo">' + esc(SOURCE_LOGOS[row.id] || row.name.slice(0, 2)) + '</span>'
        + '<span>' + esc(row.name) + '</span>'
        + '</label>';
    }).join('');
    host.querySelectorAll('input[name="glSource"]').forEach(function(input) {
      input.addEventListener('change', function() {
        toggleRedditField();
        renderPlanPreview();
      });
    });
    fillSourceFilter();
    toggleRedditField();
  }

  function sourceStatusText(row) {
    if (!row) return '';
    if (row.status === 'connected') return '已接入自动采集';
    if (row.status === 'conditional') return '需要补充社区或账号条件';
    if (row.status === 'search_link') return '先生成搜索入口';
    return '待接入采集器';
  }

  function fillSourceFilter() {
    var sel = $('glCrmSourceFilter');
    if (!sel || sel.dataset.ready === '1') return;
    sel.innerHTML = '<option value="">全部来源</option>' + [{ id: 'query', name: '用户输入' }].concat(state.sources || []).map(function(row) {
      return '<option value="' + esc(row.id) + '">' + esc(row.name) + '</option>';
    }).join('');
    sel.dataset.ready = '1';
  }

  function toggleRedditField() {
    var field = $('glRedditField');
    if (!field) return;
    field.style.display = selectedSources().indexOf('reddit') >= 0 ? 'grid' : 'none';
  }

  function renderPlanPreview() {
    var host = $('glPlanPreview');
    var count = $('glPlanCount');
    if (!host) return;
    var data = readForm();
    var sources = data.sources || [];
    var terms = searchTerms(data);
    if (count) count.textContent = sources.length + ' 个来源';
    if (!sources.length) {
      host.innerHTML = '<div class="gl-empty">请选择数据来源</div>';
      return;
    }
    if (!terms.length) {
      host.innerHTML = '<div class="gl-empty">填写企业、域名或目标方向后生成采集路径</div>';
      return;
    }
    host.innerHTML = sources.map(function(id) {
      var connected = ['google', 'bing', 'linkedin', 'x', 'tiktok'].indexOf(id) >= 0;
      var redditReady = id !== 'reddit' || data.reddit_communities.length > 0;
      var badge = connected || (id === 'reddit' && redditReady) ? statusBadge('queued') : statusBadge(id === 'reddit' ? 'needs_input' : 'ready');
      return '<div class="gl-source-row">'
        + '<span class="gl-logo">' + esc(SOURCE_LOGOS[id] || id.slice(0, 2)) + '</span>'
        + '<div><strong>' + esc(sourceLabel(id)) + '</strong><div class="gl-meta">' + esc(terms.slice(0, 4).join(' / ')) + '</div></div>'
        + '<div>' + badge + '</div>'
        + '</div>';
    }).join('');
  }

  function createJob(e) {
    if (e) e.preventDefault();
    var data = readForm();
    if (!data.company_name && !data.domain && !data.keywords.length) {
      setMsg('请填写企业名称、域名或目标方向', true);
      return;
    }
    if (!data.sources.length) {
      setMsg('请选择至少一个数据来源', true);
      return;
    }
    setBusy(true);
    setMsg('正在创建获客任务...', false);
    apiJson('/api/global-leads/jobs', { method: 'POST', body: data })
      .then(function(resp) {
        var job = resp.job || {};
        upsertJob(job);
        state.activeJobId = job.job_id || state.activeJobId;
        setMsg('任务已创建，CRM 会自动展示采集到的线索。', false);
        switchTab('jobs');
        renderJobs();
        loadJobDetail(job.job_id);
        loadCrm(true);
        schedulePoll();
      })
      .catch(function(err) { setMsg(err.message || '创建失败', true); })
      .finally(function() { setBusy(false); });
  }

  function loadSources() {
    return apiJson('/api/global-leads/source-catalog')
      .then(function(data) {
        state.sources = data.items || [];
        state.sources.forEach(function(row) { SOURCE_LABELS[row.id] = row.name; });
        SOURCE_LABELS.query = '用户输入';
        renderSourceGrid();
        renderPlanPreview();
      })
      .catch(function() {
        DEFAULT_SOURCES.forEach(function(id) { SOURCE_LABELS[id] = id; });
        SOURCE_LABELS.query = '用户输入';
        renderSourceGrid();
        renderPlanPreview();
      });
  }

  function upsertJob(job) {
    if (!job || !job.job_id) return;
    var found = false;
    state.jobs = (state.jobs || []).map(function(item) {
      if (item.job_id === job.job_id) {
        found = true;
        return Object.assign({}, item, job);
      }
      return item;
    });
    if (!found) state.jobs.unshift(job);
    state.jobs.sort(function(a, b) {
      return String(b.created_at || '').localeCompare(String(a.created_at || ''));
    });
  }

  function loadJobs(silent) {
    var q = encodeURIComponent((($('glJobSearchInput') || {}).value || '').trim());
    return apiJson('/api/global-leads/jobs?limit=20&q=' + q)
      .then(function(data) {
        state.jobs = data.items || [];
        if (!state.activeJobId && state.jobs.length) state.activeJobId = state.jobs[0].job_id;
        renderJobs();
        if (state.activeJobId) loadJobDetail(state.activeJobId);
        renderStats();
        schedulePoll();
      })
      .catch(function(err) {
        if (!silent) setMsg(err.message || '任务加载失败', true);
      });
  }

  function renderJobs() {
    var host = $('glJobList');
    if (!host) return;
    if ($('glJobCountText')) $('glJobCountText').textContent = state.jobs.length + ' 条';
    if (!state.jobs.length) {
      host.innerHTML = '<div class="gl-empty">暂无任务</div>';
      renderJobDetail(null, []);
      return;
    }
    host.innerHTML = state.jobs.map(function(job) {
      var active = job.job_id === state.activeJobId ? ' is-active' : '';
      return '<article class="gl-item' + active + '" data-job-id="' + esc(job.job_id) + '">'
        + '<div class="gl-item-head"><strong>' + esc(job.title || job.company_name || job.domain || job.job_id) + '</strong>' + statusBadge(job.status) + '</div>'
        + '<div class="gl-meta">' + esc([job.company_name, job.domain, job.region].filter(Boolean).join(' · ') || job.job_id) + '</div>'
        + '<div class="gl-badges"><span class="gl-badge">' + esc(job.crm_count || 0) + ' CRM</span><span class="gl-badge">' + esc((job.source_plan || []).length) + ' 来源</span><span class="gl-badge">' + esc(fmtTime(job.created_at)) + '</span></div>'
        + '<div class="gl-progress"><i style="width:' + Math.max(0, Math.min(100, Number(job.progress || 0))) + '%"></i></div>'
        + '</article>';
    }).join('');
    host.querySelectorAll('[data-job-id]').forEach(function(card) {
      card.addEventListener('click', function() {
        state.activeJobId = card.getAttribute('data-job-id') || '';
        renderJobs();
        loadJobDetail(state.activeJobId);
      });
    });
  }

  function loadJobDetail(jobId) {
    if (!jobId) {
      renderJobDetail(null, []);
      return Promise.resolve();
    }
    return apiJson('/api/global-leads/jobs/' + encodeURIComponent(jobId))
      .then(function(data) {
        upsertJob(data.job || {});
        renderJobs();
        renderJobDetail(data.job || null, data.contacts || []);
        renderStats();
      })
      .catch(function(err) { setMsg(err.message || '任务详情加载失败', true); });
  }

  function renderJobDetail(job, contacts) {
    var host = $('glJobDetail');
    if (!host) return;
    if (!job) {
      if ($('glActiveJobText')) $('glActiveJobText').textContent = '未选择';
      host.innerHTML = '<div class="gl-empty">选择左侧任务查看采集来源和入库线索</div>';
      return;
    }
    if ($('glActiveJobText')) $('glActiveJobText').textContent = job.job_id || '';
    var req = job.request_payload || {};
    var sources = job.source_plan || [];
    host.innerHTML =
      '<div class="gl-box"><h4>创建参数</h4>' + renderPairs({
        企业: job.company_name || '-',
        域名: job.domain || '-',
        区域: job.region || '-',
        目标: compact(job.target_profile || (req.keywords || []).join(' / '), 220) || '-',
        规模: req.max_items || '-'
      }) + '</div>'
      + '<div class="gl-box"><h4>来源进度</h4>' + renderSources(sources) + '</div>'
      + '<div class="gl-box"><h4>本任务 CRM 线索</h4>' + renderLeadCards(contacts || []) + '</div>';
  }

  function renderPairs(obj) {
    return '<div class="gl-kv">' + Object.keys(obj || {}).map(function(k) {
      return '<span>' + esc(k) + '</span><span>' + esc(obj[k]) + '</span>';
    }).join('') + '</div>';
  }

  function renderSources(sources) {
    if (!sources || !sources.length) return '<div class="gl-empty">暂无来源</div>';
    return '<div class="gl-detail-list">' + sources.map(function(item) {
      var link = item.search_url && ['google', 'bing'].indexOf(String(item.id || '').toLowerCase()) < 0 ? '<a class="btn btn-ghost btn-sm" href="' + esc(item.search_url) + '" target="_blank" rel="noopener">打开</a>' : '';
      return '<div class="gl-source-row">'
        + '<span class="gl-logo">' + esc(SOURCE_LOGOS[item.id] || String(item.name || '').slice(0, 2)) + '</span>'
        + '<div><strong>' + esc(item.name || sourceLabel(item.id)) + '</strong><div class="gl-meta">' + esc(item.message || (item.job_id ? ('子任务 ' + item.job_id) : '')) + '</div></div>'
        + '<div class="gl-row-actions">' + statusBadge(item.status) + (item.lead_count ? '<span class="gl-badge ok">' + esc(item.lead_count) + '线索</span>' : '') + link + '</div>'
        + '</div>';
    }).join('') + '</div>';
  }

  function renderLeadCards(leads) {
    if (!leads || !leads.length) return '<div class="gl-empty">任务执行完成后，这里会显示入库线索</div>';
    return '<div class="gl-detail-list">' + leads.map(function(lead) {
      return '<article class="gl-lead-card">'
        + '<div class="gl-item-head"><h4>' + esc(lead.name || lead.company || '-') + '</h4><span class="gl-badge ok">' + esc(lead.score || 0) + '分</span></div>'
        + '<div class="gl-meta">' + esc([lead.role, lead.company, lead.social_handle ? '@' + lead.social_handle : ''].filter(Boolean).join(' · ')) + '</div>'
        + (lead.profile_url ? '<div class="gl-meta"><a href="' + esc(lead.profile_url) + '" target="_blank" rel="noopener">' + esc(compact(lead.profile_url, 110)) + '</a></div>' : '')
        + '<div class="gl-badges"><span class="gl-badge">' + esc(sourceLabel(lead.source_platform)) + '</span>' + statusBadge(lead.status) + '</div>'
        + '</article>';
    }).join('') + '</div>';
  }

  function loadCrm(silent) {
    var q = encodeURIComponent((($('glCrmSearchInput') || {}).value || '').trim());
    var source = encodeURIComponent((($('glCrmSourceFilter') || {}).value || '').trim());
    var offset = (state.crmPage - 1) * PAGE_SIZE;
    return apiJson('/api/global-leads/crm?limit=' + PAGE_SIZE + '&offset=' + offset + '&q=' + q + '&source=' + source)
      .then(function(data) {
        state.crm = data.items || [];
        state.crmTotal = Number(data.total || 0);
        if (!state.activeContactId && state.crm.length) state.activeContactId = String(state.crm[0].id);
        renderCrm();
        renderStats();
      })
      .catch(function(err) {
        if (!silent) setMsg(err.message || 'CRM 加载失败', true);
      });
  }

  function renderCrm() {
    var host = $('glCrmList');
    if (!host) return;
    if ($('glCrmCountText')) $('glCrmCountText').textContent = state.crmTotal + ' 条';
    var maxPage = Math.max(1, Math.ceil(state.crmTotal / PAGE_SIZE));
    state.crmPage = Math.max(1, Math.min(state.crmPage, maxPage));
    if ($('glCrmPageText')) $('glCrmPageText').textContent = state.crmPage + ' / ' + maxPage;
    if (!state.crm.length) {
      host.innerHTML = '<div class="gl-empty">暂无 CRM 线索</div>';
      renderCrmDetail(null);
      return;
    }
    host.innerHTML = state.crm.map(function(row) {
      var active = String(row.id) === String(state.activeContactId) ? ' is-active' : '';
      return '<article class="gl-item' + active + '" data-contact-id="' + esc(row.id) + '">'
        + '<div class="gl-item-head"><strong>' + esc(row.name || row.company || '-') + '</strong><span class="gl-badge">' + esc(sourceLabel(row.source_platform)) + '</span></div>'
        + '<div class="gl-meta">' + esc([row.role, row.company, row.domain, row.region].filter(Boolean).join(' · ')) + '</div>'
        + '<div class="gl-badges"><span class="gl-badge ok">' + esc(row.score || 0) + '分</span>' + statusBadge(row.status) + '<span class="gl-badge">' + esc(fmtTime(row.created_at)) + '</span></div>'
        + '</article>';
    }).join('');
    host.querySelectorAll('[data-contact-id]').forEach(function(card) {
      card.addEventListener('click', function() {
        state.activeContactId = card.getAttribute('data-contact-id') || '';
        renderCrm();
      });
    });
    renderCrmDetail(activeContact());
  }

  function activeContact() {
    return (state.crm || []).filter(function(row) { return String(row.id) === String(state.activeContactId); })[0] || null;
  }

  function renderCrmDetail(row) {
    var host = $('glCrmDetail');
    if (!host) return;
    if (!row) {
      if ($('glActiveCrmText')) $('glActiveCrmText').textContent = '未选择';
      host.innerHTML = '<div class="gl-empty">选择左侧线索查看详情</div>';
      return;
    }
    if ($('glActiveCrmText')) $('glActiveCrmText').textContent = row.name || row.company || ('#' + row.id);
    var evidence = Array.isArray(row.evidence) ? row.evidence : [];
    host.innerHTML =
      '<div class="gl-box"><h4>基础信息</h4>' + renderPairs({
        类型: row.entity_type === 'company' ? '企业' : '联系人',
        名称: row.name || '-',
        公司: row.company || '-',
        职位: row.role || '-',
        域名: row.domain || '-',
        区域: row.region || '-',
        来源: sourceLabel(row.source_platform),
        状态: statusLabel(row.status)
      }) + '</div>'
      + '<div class="gl-box"><h4>联系方式</h4>' + renderPairs({
        邮箱: row.email || '-',
        电话: row.phone || '-',
        账号: row.social_handle || '-',
        链接: row.profile_url || row.source_url || '-'
      }) + '</div>'
      + '<div class="gl-box"><h4>来源依据</h4>' + (evidence.length ? evidence.map(function(item) {
        return '<div class="gl-meta">' + esc(compact([item.title, item.text, item.description, item.source_reason].filter(Boolean).join('：'), 260)) + '</div>';
      }).join('') : '<div class="gl-meta">暂无依据</div>') + '</div>';
  }

  function exportCrm() {
    if (!state.crm.length) {
      setMsg('当前页没有可导出的线索', true);
      return;
    }
    var rows = state.crm.map(function(row, idx) {
      return {
        序号: (state.crmPage - 1) * PAGE_SIZE + idx + 1,
        类型: row.entity_type === 'company' ? '企业' : '联系人',
        名称: row.name || '',
        公司: row.company || '',
        职位: row.role || '',
        域名: row.domain || '',
        区域: row.region || '',
        来源: sourceLabel(row.source_platform),
        评分: row.score || '',
        邮箱: row.email || '',
        电话: row.phone || '',
        账号: row.social_handle || '',
        链接: row.profile_url || row.source_url || '',
        状态: statusLabel(row.status),
        创建时间: fmtTime(row.created_at)
      };
    });
    downloadCsv('global-leads-crm.csv', rows);
  }

  function downloadCsv(filename, rows) {
    var headers = [];
    rows.forEach(function(row) {
      Object.keys(row).forEach(function(k) {
        if (headers.indexOf(k) < 0) headers.push(k);
      });
    });
    var csv = '\ufeff' + headers.join(',') + '\n' + rows.map(function(row) {
      return headers.map(function(k) {
        var value = String(row[k] === undefined || row[k] === null ? '' : row[k]);
        return '"' + value.replace(/"/g, '""') + '"';
      }).join(',');
    }).join('\n');
    var blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename.replace(/[\\/:*?"<>|]+/g, '_');
    document.body.appendChild(a);
    a.click();
    setTimeout(function() {
      URL.revokeObjectURL(url);
      a.remove();
    }, 600);
  }

  function renderDashboard() {
    var sourceMap = {};
    state.crm.forEach(function(row) {
      var key = sourceLabel(row.source_platform || 'unknown');
      sourceMap[key] = (sourceMap[key] || 0) + 1;
    });
    var statusMap = {};
    state.jobs.forEach(function(job) {
      var key = statusLabel(job.status);
      statusMap[key] = (statusMap[key] || 0) + 1;
    });
    if ($('glSourceChart')) $('glSourceChart').innerHTML = renderBars(sourceMap, '暂无 CRM 数据');
    if ($('glStatusChart')) $('glStatusChart').innerHTML = renderBars(statusMap, '暂无任务数据');
    renderStats();
  }

  function renderBars(map, emptyText) {
    var keys = Object.keys(map || {}).filter(function(k) { return Number(map[k] || 0) > 0; });
    if (!keys.length) return '<div class="gl-empty">' + esc(emptyText || '暂无数据') + '</div>';
    var max = Math.max.apply(Math, keys.map(function(k) { return Number(map[k] || 0); }));
    return keys.map(function(k) {
      var value = Number(map[k] || 0);
      var width = max ? Math.max(6, Math.round(value / max * 100)) : 0;
      return '<div class="gl-bar-row"><span>' + esc(k) + '</span><div class="gl-bar-track"><div class="gl-bar" style="width:' + width + '%;"></div></div><strong>' + esc(value) + '</strong></div>';
    }).join('');
  }

  function renderStats() {
    var running = state.jobs.filter(function(job) {
      return ['queued', 'running'].indexOf(String(job.status || '').toLowerCase()) >= 0;
    }).length;
    if ($('glStatJobs')) $('glStatJobs').textContent = state.jobs.length;
    if ($('glStatCrm')) $('glStatCrm').textContent = state.crmTotal || state.crm.length;
    if ($('glStatRunning')) $('glStatRunning').textContent = running;
  }

  function schedulePoll() {
    if (state.pollTimer) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
    var hasRunning = state.jobs.some(function(job) {
      return ['queued', 'running'].indexOf(String(job.status || '').toLowerCase()) >= 0;
    });
    if (!hasRunning) return;
    state.pollTimer = setTimeout(function() {
      Promise.all([loadJobs(true), loadCrm(true)]).catch(function() {});
    }, 8000);
  }

  function resetForm() {
    ['glCompanyInput', 'glDomainInput', 'glRegionInput', 'glKeywordsInput', 'glTargetInput', 'glRedditInput'].forEach(function(id) {
      var el = $(id);
      if (el) el.value = '';
    });
    if ($('glMaxItemsInput')) $('glMaxItemsInput').value = 80;
    document.querySelectorAll('#content-global-leads input[name="glSource"]').forEach(function(input) {
      input.checked = DEFAULT_SOURCES.indexOf(input.value) >= 0;
    });
    toggleRedditField();
    renderPlanPreview();
  }

  function bind() {
    var root = $('content-global-leads');
    if (!root || root.dataset.globalLeadsBound === '1') return;
    root.dataset.globalLeadsBound = '1';
    document.querySelectorAll('#content-global-leads [data-gl-tab]').forEach(function(btn) {
      btn.addEventListener('click', function() { switchTab(btn.getAttribute('data-gl-tab') || 'create'); });
    });
    var form = $('globalLeadsForm');
    if (form) form.addEventListener('submit', createJob);
    var resetBtn = $('glResetBtn');
    if (resetBtn) resetBtn.addEventListener('click', resetForm);
    var refreshJobs = $('glRefreshJobsBtn');
    if (refreshJobs) refreshJobs.addEventListener('click', function() { loadJobs(false); });
    var refreshCrm = $('glRefreshCrmBtn');
    if (refreshCrm) refreshCrm.addEventListener('click', function() { loadCrm(false); });
    var exportBtn = $('glExportCrmBtn');
    if (exportBtn) exportBtn.addEventListener('click', exportCrm);
    var crmPrev = $('glCrmPrevBtn');
    if (crmPrev) crmPrev.addEventListener('click', function() {
      state.crmPage = Math.max(1, state.crmPage - 1);
      loadCrm(false);
    });
    var crmNext = $('glCrmNextBtn');
    if (crmNext) crmNext.addEventListener('click', function() {
      var maxPage = Math.max(1, Math.ceil(state.crmTotal / PAGE_SIZE));
      state.crmPage = Math.min(maxPage, state.crmPage + 1);
      loadCrm(false);
    });
    ['glCompanyInput', 'glDomainInput', 'glRegionInput', 'glKeywordsInput', 'glTargetInput', 'glMaxItemsInput', 'glRedditInput'].forEach(function(id) {
      var el = $(id);
      if (el) el.addEventListener('input', renderPlanPreview);
      if (el) el.addEventListener('change', renderPlanPreview);
    });
    ['glCrmSearchInput', 'glCrmSourceFilter'].forEach(function(id) {
      var el = $(id);
      if (!el) return;
      el.addEventListener('input', function() { state.crmPage = 1; loadCrm(true); });
      el.addEventListener('change', function() { state.crmPage = 1; loadCrm(true); });
    });
    var jobSearch = $('glJobSearchInput');
    if (jobSearch) jobSearch.addEventListener('input', function() { loadJobs(true); });
  }

  window.initGlobalLeadsView = function initGlobalLeadsView() {
    bind();
    loadSources()
      .then(function() {
        return Promise.all([loadJobs(true), loadCrm(true)]);
      })
      .then(function() {
        renderDashboard();
      })
      .catch(function() {});
  };
})();
