(function() {
  var state = {
    platform: 'reddit',
    tab: 'new',
    jobs: [],
    activeJobId: '',
    activeOutputId: '',
    activeLeadKey: '',
    activeStepKey: '',
    activeSourceId: '',
    pollTimer: null,
    autoResumeJobs: {},
    templates: [],
    templatesLoading: false
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

  function localBase() {
    var base = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE ? String(LOCAL_API_BASE) : '').replace(/\/$/, '');
    return base || window.location.origin;
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

  function localJson(path, body) {
    return fetch(localBase() + path, {
      method: 'POST',
      headers: authHeaderJson(),
      body: JSON.stringify(body || {})
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok || data.ok === false) throw new Error(parseErr(data, '本机处理失败'));
        return data;
      });
    });
  }

  function setMsg(text, isErr) {
    var node = $('socialLeadsMsg');
    if (!node) return;
    node.textContent = text || '';
    node.className = 'sl-msg' + (isErr ? ' err' : '');
    node.style.display = text ? 'block' : 'none';
  }

  function draftKey() {
    return 'socialLeads.inputDraft.' + state.platform + '.v1';
  }

  function lines(value) {
    return String(value || '').split(/\r?\n|[,，]/).map(function(x) { return x.trim(); }).filter(Boolean);
  }

  function platformLabel() {
    if (state.platform === 'x') return 'X';
    if (state.platform === 'tiktok') return 'TikTok';
    return 'Reddit';
  }

  function normalizePlatform(value) {
    var next = String(value || '').toLowerCase();
    if (next === 'twitter' || next === 'x_leads') next = 'x';
    if (next === 'reddit_leads') next = 'reddit';
    if (next === 'tiktok_leads' || next === 'tik_tok') next = 'tiktok';
    if (['reddit', 'x', 'tiktok'].indexOf(next) < 0) return '';
    return next;
  }

  function resetRunState(clearJobs) {
    if (clearJobs) state.jobs = [];
    state.activeJobId = '';
    state.activeOutputId = '';
    state.activeLeadKey = '';
    state.activeStepKey = '';
    state.activeSourceId = '';
    if (state.pollTimer) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
    state.autoResumeJobs = {};
  }

  function syncPlatform(value) {
    var next = normalizePlatform(value || window.__socialLeadsPlatform || state.platform || 'reddit') || 'reddit';
    state.platform = next;
    window.__socialLeadsPlatform = next;
    var page = document.querySelector('#content-social-leads .social-leads-page');
    if (page) page.classList.toggle('is-x', state.platform === 'x');
    if ($('socialLeadsKicker')) $('socialLeadsKicker').textContent = platformLabel() + ' 线索采集';
    if ($('socialLeadsTitle')) $('socialLeadsTitle').textContent = platformLabel() + ' 公开信息采集工作台';
    if ($('socialLeadsSubtitle')) $('socialLeadsSubtitle').textContent = '只采集公开信息，不执行评论、发布或私信动作';
    document.querySelectorAll('#content-social-leads [data-reddit-only]').forEach(function(el) {
      el.style.display = state.platform === 'reddit' ? '' : 'none';
    });
    document.querySelectorAll('#content-social-leads [data-x-only]').forEach(function(el) {
      el.style.display = state.platform === 'x' ? '' : 'none';
    });
    if ($('slSourceModeCommunityLabel')) $('slSourceModeCommunityLabel').textContent = state.platform === 'tiktok' ? '按来源词采集' : (state.platform === 'x' ? '按关键词采集' : '按社区采集');
    if ($('slCommunitiesLabel')) $('slCommunitiesLabel').textContent = state.platform === 'tiktok' ? 'TikTok 来源词/话题' : (state.platform === 'x' ? 'X 搜索关键词' : 'Reddit 社区');
    if ($('slCommunitiesInput')) $('slCommunitiesInput').placeholder = state.platform === 'tiktok'
      ? '每行一个来源词，例如：AI tool\\nsmall business'
      : (state.platform === 'x' ? '每行一个搜索词，例如：AI automation\\nlead generation' : '每行一个社区，例如：Entrepreneur\\nSaaS');
    syncSourceMode();
    loadDraft();
    loadTemplates(true);
  }

  function switchTab(tab) {
    state.tab = tab || 'new';
    document.querySelectorAll('#content-social-leads [data-sl-tab]').forEach(function(btn) {
      btn.classList.toggle('is-active', btn.getAttribute('data-sl-tab') === state.tab);
    });
    document.querySelectorAll('#content-social-leads [data-sl-panel]').forEach(function(panel) {
      panel.classList.toggle('is-active', panel.getAttribute('data-sl-panel') === state.tab);
    });
  }

  function sourceMode() {
    var checked = document.querySelector('#content-social-leads input[name="slSourceMode"]:checked');
    return checked ? checked.value : 'account';
  }

  function syncSourceMode() {
    var mode = sourceMode();
    document.querySelectorAll('#content-social-leads [data-sl-source-field]').forEach(function(el) {
      el.style.display = el.getAttribute('data-sl-source-field') === mode ? '' : 'none';
    });
  }

  function readInputs() {
    var mode = sourceMode();
    var sourceValues = lines(($('slCommunitiesInput') || {}).value);
    return {
      platform: state.platform,
      title: ($('slJobTitleInput') || {}).value || '',
      keywords: lines(($('slKeywordsInput') || {}).value),
      source_keywords: (state.platform === 'tiktok' || state.platform === 'x') && mode === 'community' ? sourceValues : [],
      accounts: mode === 'account' ? lines(($('slAccountsInput') || {}).value) : [],
      post_ids: [],
      communities: state.platform === 'reddit' && mode === 'community' ? sourceValues : [],
      country: '',
      search_type: '',
      sort: 'NEW',
      time_range: 'day',
      max_items: 100,
      include_comments: true,
      include_account_posts: true,
      auto_run: true
    };
  }

  function templateSummary(payload) {
    payload = payload || {};
    var parts = [];
    var keywords = payload.keywords || [];
    var accounts = payload.accounts || [];
    var sources = (payload.communities && payload.communities.length ? payload.communities : payload.source_keywords) || [];
    if (keywords.length) parts.push('关键词 ' + keywords.length);
    if (accounts.length) parts.push('账号 ' + accounts.length);
    if (sources.length) parts.push((state.platform === 'reddit' ? '社区 ' : '来源 ') + sources.length);
    return parts.join(' · ') || '未配置采集条件';
  }

  function fillInputsFromTemplate(row) {
    var data = (row && row.request_payload) || {};
    if ($('slJobTitleInput')) $('slJobTitleInput').value = data.title || row.name || '';
    if ($('slKeywordsInput')) $('slKeywordsInput').value = (data.keywords || []).join('\n');
    if ($('slAccountsInput')) $('slAccountsInput').value = (data.accounts || []).join('\n');
    if ($('slCommunitiesInput')) $('slCommunitiesInput').value = ((data.source_keywords && data.source_keywords.length ? data.source_keywords : data.communities) || []).join('\n');
    var mode = (data.accounts || []).length ? 'account' : 'community';
    var radio = document.querySelector('#content-social-leads input[name="slSourceMode"][value="' + mode + '"]');
    if (radio) radio.checked = true;
    syncSourceMode();
    setMsg('已填入模板：' + (row.name || ''), false);
  }

  function renderTemplates() {
    var host = $('slTemplateList');
    if (!host) return;
    if (state.templatesLoading) {
      host.innerHTML = '<div class="sl-meta">加载中...</div>';
      return;
    }
    if (!state.templates.length) {
      host.innerHTML = '<div class="sl-meta">暂无模板</div>';
      return;
    }
    host.innerHTML = state.templates.map(function(row) {
      return '<article class="sl-template-item" data-template-id="' + esc(row.id) + '">'
        + '<strong>' + esc(row.name || ('模板 #' + row.id)) + '</strong>'
        + '<span class="sl-meta">' + esc(templateSummary(row.request_payload || {})) + '</span>'
        + '<div class="sl-template-actions">'
        + '<button type="button" class="btn btn-primary btn-sm" data-template-action="use">使用</button>'
        + '<button type="button" class="btn btn-ghost btn-sm" data-template-action="delete">删除</button>'
        + '</div>'
        + '</article>';
    }).join('');
  }

  function loadTemplates(silent) {
    state.templatesLoading = true;
    renderTemplates();
    return apiJson('/api/lead-collection/templates?platform=' + encodeURIComponent(state.platform))
      .then(function(data) {
        state.templates = data.items || [];
        renderTemplates();
      })
      .catch(function(err) {
        state.templates = [];
        renderTemplates();
        if (!silent) setMsg(err.message || '模板加载失败', true);
      })
      .then(function() {
        state.templatesLoading = false;
        renderTemplates();
      });
  }

  function createTemplate() {
    var body = readInputs();
    var name = (body.title || '').trim() || window.prompt('模板名称', platformLabel() + '采集模板');
    if (!name) return;
    apiJson('/api/lead-collection/templates', {
      method: 'POST',
      body: {
        platform: state.platform,
        name: name,
        title: body.title || name,
        request_payload: body
      }
    }).then(function(data) {
      setMsg('模板已创建', false);
      if (data.template) {
        state.templates = [data.template].concat((state.templates || []).filter(function(row) { return String(row.id) !== String(data.template.id); }));
        renderTemplates();
      } else {
        loadTemplates(true);
      }
    }).catch(function(err) {
      setMsg(err.message || '模板创建失败', true);
    });
  }

  function deleteTemplate(templateId) {
    if (!templateId) return;
    apiJson('/api/lead-collection/templates/' + encodeURIComponent(templateId), { method: 'DELETE', body: {} })
      .then(function() {
        state.templates = (state.templates || []).filter(function(row) { return String(row.id) !== String(templateId); });
        renderTemplates();
        setMsg('模板已删除', false);
      })
      .catch(function(err) { setMsg(err.message || '模板删除失败', true); });
  }

  function saveDraft() {
    var data = readInputs();
    data.source_mode = sourceMode();
    try { localStorage.setItem(draftKey(), JSON.stringify(data)); } catch (e) {}
    setMsg('已保存输入。', false);
  }

  function loadDraft() {
    var data = {};
    try { data = JSON.parse(localStorage.getItem(draftKey()) || '{}') || {}; } catch (e) { data = {}; }
    if ($('slJobTitleInput')) $('slJobTitleInput').value = data.title || '';
    if ($('slKeywordsInput')) $('slKeywordsInput').value = (data.keywords || []).join('\n');
    if ($('slAccountsInput')) $('slAccountsInput').value = (data.accounts || []).join('\n');
    if ($('slCommunitiesInput')) $('slCommunitiesInput').value = ((data.source_keywords && data.source_keywords.length ? data.source_keywords : data.communities) || []).join('\n');
    var mode = data.source_mode || (data.accounts && data.accounts.length ? 'account' : 'community');
    var radio = document.querySelector('#content-social-leads input[name="slSourceMode"][value="' + mode + '"]');
    if (radio) radio.checked = true;
    syncSourceMode();
  }

  function activeJob() {
    return state.jobs.filter(function(j) { return j.job_id === state.activeJobId; })[0] || null;
  }

  function jobPlatform(job) {
    var value = normalizePlatform(job && (job.platform || (job.request_payload && job.request_payload.platform)));
    if (!value && job && job.feature_type) {
      value = normalizePlatform(job.feature_type);
    }
    if (!value && job && job.job_id) {
      if (/^x_/.test(job.job_id)) value = 'x';
      else if (/^tt_/.test(job.job_id)) value = 'tiktok';
      else if (/^rd_/.test(job.job_id)) value = 'reddit';
    }
    return value;
  }

  function statusBadge(status) {
    var cls = status === 'completed' ? 'done' : (status === 'failed' ? 'fail' : (status === 'running' || status === 'queued' ? 'run' : ''));
    var label = {
      queued: '排队',
      running: '执行中',
      pending: '待执行',
      completed: '完成',
      failed: '失败',
      skipped: '跳过',
      canceled: '已取消',
      stale: '已过期'
    }[status] || '未知';
    return '<span class="sl-badge ' + cls + '">' + esc(label) + '</span>';
  }

  function isTerminalStatus(status) {
    return ['completed', 'failed', 'canceled', 'stale'].indexOf(String(status || '')) >= 0;
  }

  function jobAgeMs(job) {
    var value = job && (job.updated_at || job.created_at);
    if (!value) return 0;
    try {
      var ts = new Date(value).getTime();
      return isNaN(ts) ? 0 : Date.now() - ts;
    } catch (e) {
      return 0;
    }
  }

  function scheduleAutoRefresh() {
    if (state.pollTimer) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
    var hasRunning = state.jobs.some(function(job) {
      return job && jobPlatform(job) === state.platform && !isTerminalStatus(job.status);
    });
    if (!hasRunning) return;
    state.pollTimer = setTimeout(function() {
      state.pollTimer = null;
      loadJobs(true);
    }, 2600);
  }

  function autoResumeQueuedJobs() {
    state.jobs.forEach(function(job) {
      if (!job || !job.job_id || isTerminalStatus(job.status)) return;
      if (jobPlatform(job) !== state.platform) return;
      var hasPendingStep = (job.steps || []).some(function(step) {
        return ['completed', 'skipped'].indexOf(String(step.status || '')) < 0;
      });
      var hasRunningStep = (job.steps || []).some(function(step) {
        return String(step.status || '') === 'running';
      });
      var idleRunning = job.status === 'running'
        && hasPendingStep
        && !hasRunningStep
        && !(job.current_step || (job.meta || {}).current_step)
        && jobAgeMs(job) >= 8000;
      var shouldResume = !!job.needs_resume || idleRunning || (job.status === 'queued' && jobAgeMs(job) >= 8000);
      if (!shouldResume) return;
      var lastResumeAt = Number(state.autoResumeJobs[job.job_id] || 0);
      if (lastResumeAt && Date.now() - lastResumeAt < 12000) return;
      state.autoResumeJobs[job.job_id] = Date.now();
      apiJson('/api/social-leads/jobs/' + encodeURIComponent(job.job_id) + '/resume', { method: 'POST', body: {} })
        .then(function(data) {
          replaceJob(data.job);
          renderJobs();
          setMsg('任务正在自动执行，页面会持续刷新进度。', false);
        })
        .catch(function(err) {
          setMsg(err.message || '自动续跑失败，请刷新后查看。', true);
        });
    });
  }

  function fmtTime(value) {
    if (!value) return '';
    try {
      var d = new Date(value);
      if (!isNaN(d.getTime())) return d.toLocaleString();
    } catch (e) {}
    return String(value);
  }

  function typeLabel(type) {
    return {
      search_result: '搜索结果',
      typeahead: '联想结果',
      post_detail: '帖子详情',
      post_comment: '评论',
      user_profile: '账号资料',
      user_post: '账号发帖',
      user_comment: '账号评论',
      subreddit_post: '社区帖子',
      trend: '趋势',
      user_follower: '粉丝',
      user_following: '关注'
    }[type] || type || '采集项';
  }

  function stepSources(job, stepKey) {
    var rows = job && Array.isArray(job.source_items) ? job.source_items : [];
    if (!stepKey) return rows;
    return rows.filter(function(item) { return item.step_key === stepKey; });
  }

  function metricChips(metrics) {
    metrics = metrics || {};
    var labels = {
      score: '分数',
      ups: '点赞',
      upvote_ratio: '赞同率',
      num_comments: '评论',
      total_karma: 'Karma',
      post_karma: '发帖Karma',
      comment_karma: '评论Karma',
      subscribers: '订阅',
      followers: '粉丝',
      followers_count: '粉丝',
      friends_count: '关注',
      reply_count: '回复',
      retweet_count: '转发',
      favorite_count: '喜欢',
      view_count: '浏览'
    };
    var html = Object.keys(metrics).filter(function(k) {
      return metrics[k] !== null && metrics[k] !== undefined && metrics[k] !== '' && typeof metrics[k] !== 'object';
    }).slice(0, 10).map(function(k) {
      return '<span class="sl-badge">' + esc(labels[k] || k) + ' ' + esc(metrics[k]) + '</span>';
    }).join('');
    return html || '<span class="sl-meta">暂无指标</span>';
  }

  function compactText(text, len) {
    text = String(text || '').replace(/\s+/g, ' ').trim();
    len = len || 160;
    return text.length > len ? text.slice(0, len - 1) + '…' : text;
  }

  function sourceTitle(item) {
    item = item || {};
    return item.display_name || item.handle || item.title || item.item_key || typeLabel(item.source_type);
  }

  function renderSourceCards(items, emptyText) {
    if (!items || !items.length) return '<div class="sl-meta">' + esc(emptyText || '暂无采集明细') + '</div>';
    return items.map(function(item) {
      var url = item.url ? '<a href="' + esc(item.url) + '" target="_blank" rel="noopener">打开链接</a>' : '';
      var desc = item.description || item.title || '';
      return '<div class="sl-source-card" data-sl-source-id="' + esc(item.id || '') + '">'
        + '<div class="sl-source-head"><strong>' + esc(sourceTitle(item)) + '</strong><span class="sl-badge">' + esc(typeLabel(item.source_type)) + '</span></div>'
        + '<span class="sl-meta">' + esc(item.source_reason || '') + '</span>'
        + (desc ? '<div class="sl-source-desc">' + esc(compactText(desc, 260)) + '</div>' : '')
        + '<div class="sl-badges">' + metricChips(item.metrics || {}) + '</div>'
        + (url ? '<div class="sl-meta">' + url + '</div>' : '')
        + '</div>';
    }).join('');
  }

  function detailPairs(obj) {
    obj = obj || {};
    var labels = {
      author: '作者',
      username: '用户名',
      name: '名称',
      display_name: '展示名',
      title: '标题',
      subreddit: '社区',
      subreddit_name_prefixed: '社区',
      public_description: '公开简介',
      description: '描述',
      selftext: '正文',
      body: '正文',
      full_text: '正文',
      text: '正文',
      permalink: '链接',
      url: '链接',
      score: '分数',
      ups: '点赞',
      num_comments: '评论数',
      total_karma: 'Karma',
      subscribers: '订阅数',
      followers_count: '粉丝数',
      created_utc: '发布时间',
      created_at: '发布时间'
    };
    var keys = Object.keys(obj).filter(function(k) { return obj[k] !== null && obj[k] !== undefined && obj[k] !== ''; });
    if (!keys.length) return '<div class="sl-meta">暂无更多字段</div>';
    return '<div class="sl-kv-list">' + keys.map(function(k) {
      var value = obj[k];
      if (typeof value === 'object') {
        value = Array.isArray(value) ? ('包含 ' + value.length + ' 条数据') : ('包含 ' + Object.keys(value || {}).length + ' 个字段');
      }
      return '<div class="sl-kv"><span>' + esc(labels[k] || k) + '</span><strong>' + esc(compactText(value, 300)) + '</strong></div>';
    }).join('') + '</div>';
  }

  function loadJobs(silent) {
    var requestedPlatform = state.platform;
    return apiJson('/api/social-leads/jobs?platform=' + encodeURIComponent(requestedPlatform) + '&limit=50')
      .then(function(data) {
        if (state.platform !== requestedPlatform) return;
        var prevActiveJobId = state.activeJobId;
        state.jobs = (data.items || []).filter(function(job) {
          return jobPlatform(job) === requestedPlatform;
        });
        if (!state.activeJobId && state.jobs.length) state.activeJobId = state.jobs[0].job_id;
        if (state.activeJobId && !state.jobs.some(function(j) { return j.job_id === state.activeJobId; })) {
          state.activeJobId = state.jobs.length ? state.jobs[0].job_id : '';
        }
        if (prevActiveJobId !== state.activeJobId) {
          state.activeLeadKey = '';
          state.activeOutputId = '';
          state.activeStepKey = '';
          state.activeSourceId = '';
        }
        renderJobs();
        autoResumeQueuedJobs();
        scheduleAutoRefresh();
        if (!silent) setMsg('', false);
      })
      .catch(function(err) {
        if (state.platform !== requestedPlatform) return;
        setMsg(err.message || '任务加载失败', true);
      });
  }

  function startJob() {
    var body = readInputs();
    if (!body.keywords.length) {
      setMsg('关键词不能为空，请填写要筛选的精准用户方向。', true);
      return;
    }
    if (state.platform === 'reddit' && !body.accounts.length && !body.communities.length) {
      setMsg('Reddit 账号和社区请选择一种并填写。', true);
      return;
    }
    if (state.platform === 'tiktok' && !body.accounts.length && !body.source_keywords.length) {
      setMsg('TikTok 账号和来源词请选择一种并填写。', true);
      return;
    }
    if (state.platform === 'x' && !body.accounts.length && !body.source_keywords.length) {
      setMsg('X 账号和搜索词请选择一种并填写。', true);
      return;
    }
    setMsg('正在创建采集任务...', false);
    setBusy(true);
    apiJson('/api/social-leads/jobs', { method: 'POST', body: body })
      .then(function(data) {
        var job = data.job || {};
        state.activeJobId = job.job_id || '';
        saveDraft();
        setMsg('采集任务已创建，正在自动执行。', false);
        switchTab('runs');
        return loadJobs(true);
      })
      .catch(function(err) { setMsg(err.message || '创建任务失败', true); })
      .finally(function() { setBusy(false); });
  }

  function setBusy(busy) {
    ['slStartJobBtn'].forEach(function(id) {
      var el = $(id);
      if (el) el.disabled = !!busy;
    });
  }

  function replaceJob(job) {
    if (!job || !job.job_id) return;
    if (jobPlatform(job) !== state.platform) return;
    var found = false;
    state.jobs = state.jobs.map(function(item) {
      if (item.job_id === job.job_id) {
        found = true;
        return job;
      }
      return item;
    });
    if (!found) state.jobs.unshift(job);
    state.activeJobId = job.job_id;
    scheduleAutoRefresh();
  }

  function renderJobs() {
    var host = $('slJobList');
    if (!host) return;
    if (!state.jobs.length) {
      host.innerHTML = '<div class="sl-meta">暂无任务。</div>';
      renderActiveJob();
      return;
    }
    host.innerHTML = state.jobs.map(function(job) {
      var active = job.job_id === state.activeJobId ? ' is-active' : '';
      return '<div class="sl-item' + active + '" data-sl-job-id="' + esc(job.job_id) + '">'
        + '<strong>' + esc(job.title || (platformLabel() + '线索采集')) + '</strong>'
        + '<span class="sl-meta">' + esc(fmtTime(job.created_at)) + ' · ' + esc(job.progress || 0) + '%</span>'
        + '<div class="sl-badges">' + statusBadge(job.status) + '<span class="sl-badge">' + esc((job.steps || []).length) + ' 步</span></div>'
        + (job.error ? '<span class="sl-meta" style="color:#be123c;">' + esc(job.error) + '</span>' : '')
        + '</div>';
    }).join('');
    host.querySelectorAll('[data-sl-job-id]').forEach(function(node) {
      node.addEventListener('click', function() {
        state.activeJobId = node.getAttribute('data-sl-job-id') || '';
        state.activeLeadKey = '';
        state.activeOutputId = '';
        state.activeStepKey = '';
        state.activeSourceId = '';
        renderJobs();
        renderActiveJob();
        switchTab('runs');
      });
    });
    renderActiveJob();
  }

  function renderActiveJob() {
    var job = activeJob();
    if (job && jobPlatform(job) !== state.platform) job = null;
    var bar = $('slProgressBar');
    if (bar) bar.style.width = Math.max(0, Math.min(100, Number(job && job.progress || 0))) + '%';
    renderSteps(job);
    renderLeads(job);
    renderOutputs(job);
  }

  function renderSteps(job) {
    var host = $('slStepList');
    if (!host) return;
    if (!job) {
      host.innerHTML = '<div class="sl-meta">请选择一个任务。</div>';
      return;
    }
    host.innerHTML = (job.steps || []).map(function(step, idx) {
      var st = step.status || 'pending';
      var active = step.key === state.activeStepKey ? ' is-active' : '';
      var sourceCount = stepSources(job, step.key).length;
      return '<div class="sl-step is-' + esc(st) + active + '" data-sl-step-key="' + esc(step.key || '') + '">'
        + '<span class="sl-step-dot">' + esc(idx + 1) + '</span>'
        + '<div><strong>' + esc(step.label || step.key) + '</strong>'
        + '<span class="sl-meta">' + esc(step.detail || '') + '</span>'
        + (sourceCount ? '<span class="sl-meta">已保存 ' + esc(sourceCount) + ' 条采集明细，点击查看</span>' : '')
        + (step.error ? '<span class="sl-meta" style="color:#be123c;">' + esc(step.error) + '</span>' : '')
        + '</div><div class="sl-row-actions">' + statusBadge(st)
        + '</div></div>';
    }).join('');
    var detail = $('slStepDetail');
    if (detail) {
      if ((job.steps || []).length && !job.steps.some(function(step) { return step.key === state.activeStepKey; })) {
        state.activeStepKey = (job.steps || [])[0].key || '';
      }
      var selected = (job.steps || []).filter(function(step) { return step.key === state.activeStepKey; })[0] || null;
      var rows = stepSources(job, selected && selected.key);
      detail.innerHTML = selected
        ? '<div class="sl-box"><h4>' + esc(selected.label || selected.key) + '</h4><span class="sl-meta">' + esc(selected.detail || '') + '</span><div class="sl-badges">' + statusBadge(selected.status || 'pending') + '<span class="sl-badge">' + esc(rows.length) + ' 条明细</span></div></div>' + renderSourceCards(rows, '这一步还没有保存采集明细。')
        : '<div class="sl-meta">请选择左侧步骤。</div>';
    }
    host.querySelectorAll('[data-sl-step-key]').forEach(function(node) {
      node.addEventListener('click', function(e) {
        state.activeStepKey = node.getAttribute('data-sl-step-key') || '';
        renderSteps(activeJob());
      });
    });
  }

  function leadKey(item) {
    item = item || {};
    return String(item.candidate_key || item.handle || item.url || item.name || 'unknown-lead').slice(0, 240);
  }

  function buildLeads(job) {
    var rows = job && job.result_payload && job.result_payload.candidates;
    if (!Array.isArray(rows)) rows = [];
    return rows.map(function(item) {
      return Object.assign({}, item, { key: leadKey(item) });
    });
  }

  function renderLeads(job) {
    var list = $('slLeadList');
    var detail = $('slLeadDetail');
    if (!list || !detail) return;
    var leads = buildLeads(job);
    if (!job) {
      list.innerHTML = '<div class="sl-meta">请选择一个任务。</div>';
      detail.innerHTML = '<div class="sl-meta">请选择左侧线索。</div>';
      return;
    }
    if (!leads.length) {
      var sources = job && Array.isArray(job.source_items) ? job.source_items : [];
      list.innerHTML = '<div class="sl-meta">暂无归并线索。</div>';
      detail.innerHTML = '<div class="sl-box"><h4>已采集到的内容</h4><span class="sl-meta">当前没有可归并成账号的线索，但可以先查看采集明细。</span></div>' + renderSourceCards(sources, '还没有采集明细。');
      return;
    }
    if (!state.activeLeadKey || !leads.some(function(x) { return x.key === state.activeLeadKey; })) {
      state.activeLeadKey = leads[0].key;
    }
    list.innerHTML = leads.map(function(lead) {
      var active = lead.key === state.activeLeadKey ? ' is-active' : '';
      var title = lead.name || lead.handle || lead.url || lead.source_reason || lead.candidate_key || '-';
      var sub = [lead.handle ? '@' + lead.handle : '', lead.url || '', lead.source_reason || lead.source_type || ''].filter(Boolean).join(' · ');
      var relevance = lead.keyword_relevance || {};
      var matched = Array.isArray(relevance.matched_keywords) ? relevance.matched_keywords.join('、') : '';
      return '<div class="sl-lead' + active + '" data-sl-lead-key="' + esc(lead.key) + '">'
        + '<strong>' + esc(title) + '</strong>'
        + '<span class="sl-meta">' + esc(sub) + '</span>'
        + (lead.bio ? '<span class="sl-meta">' + esc(compactText(lead.bio, 120)) + '</span>' : '')
        + '<div class="sl-badges"><span class="sl-badge">评分 ' + esc(lead.score || '-') + '</span><span class="sl-badge">' + esc((lead.evidence || []).length) + ' 条证据</span>' + (matched ? '<span class="sl-badge">命中 ' + esc(matched) + '</span>' : '') + '</div>'
        + '</div>';
    }).join('');
    list.querySelectorAll('[data-sl-lead-key]').forEach(function(node) {
      node.addEventListener('click', function() {
        state.activeLeadKey = node.getAttribute('data-sl-lead-key') || '';
        renderLeads(activeJob());
      });
    });
    renderLeadDetail(leads.filter(function(x) { return x.key === state.activeLeadKey; })[0] || leads[0]);
  }

  function renderLeadDetail(lead) {
    var host = $('slLeadDetail');
    if (!host) return;
    if (!lead) {
      host.innerHTML = '<div class="sl-meta">请选择左侧线索。</div>';
      return;
    }
    var evidence = Array.isArray(lead.evidence) ? lead.evidence : [];
    var relevance = lead.keyword_relevance || {};
    var matched = Array.isArray(relevance.matched_keywords) ? relevance.matched_keywords.join('、') : '';
    var reasons = Array.isArray(lead.intent_reasons) ? lead.intent_reasons : [];
    var evidenceHtml = evidence.length ? evidence.map(function(ev, idx) {
      return '<div class="sl-evidence"><strong>证据 ' + esc(idx + 1) + ' · ' + esc(typeLabel(ev.source_type)) + '</strong>'
        + (ev.title ? '<span class="sl-meta">' + esc(ev.title) + '</span>' : '')
        + (ev.description ? '<div class="sl-source-desc">' + esc(compactText(ev.description, 360)) + '</div>' : '')
        + (ev.url ? '<span class="sl-meta"><a href="' + esc(ev.url) + '" target="_blank" rel="noopener">打开来源</a></span>' : '')
        + '</div>';
    }).join('') : '<div class="sl-meta">暂无证据。</div>';
    host.innerHTML = '<div class="sl-box"><h4>' + esc(lead.name || lead.handle || lead.url || '-') + '</h4>'
      + (lead.handle ? '<div class="sl-meta">@' + esc(lead.handle) + '</div>' : '')
      + (lead.url ? '<div class="sl-meta"><a href="' + esc(lead.url) + '" target="_blank" rel="noopener">' + esc(lead.url) + '</a></div>' : '')
      + '<div class="sl-badges"><span class="sl-badge">评分 ' + esc(lead.score || '-') + '</span><span class="sl-badge">' + esc(evidence.length) + ' 条证据</span></div></div>'
      + '<div class="sl-box"><h4>精准判断</h4>'
      + '<div class="sl-badges">' + (matched ? '<span class="sl-badge">命中关键词 ' + esc(matched) + '</span>' : '<span class="sl-badge">未直接命中关键词</span>') + '</div>'
      + '<div class="sl-source-desc">' + esc(reasons.length ? reasons.join('；') : '暂无判断理由') + '</div></div>'
      + '<div class="sl-box"><h4>公开信息</h4><div class="sl-meta">' + esc(lead.bio || '暂无简介/正文。') + '</div></div>'
      + '<div class="sl-box"><h4>指标</h4><div class="sl-badges">' + metricChips(lead.metrics || {}) + '</div></div>'
      + '<div class="sl-box"><h4>来源依据</h4>' + evidenceHtml + '</div>';
  }

  function renderOutputs(job) {
    var list = $('slOutputList');
    var detail = $('slOutputDetail');
    if (!list || !detail) return;
    var sources = job && Array.isArray(job.source_items) ? job.source_items : [];
    if (!sources.length) {
      list.innerHTML = '<div class="sl-meta">暂无采集明细。</div>';
      detail.innerHTML = '<div class="sl-meta">任务执行后，这里会按账号、帖子、评论、社区帖子展示采集结果。</div>';
      return;
    }
    if (!state.activeSourceId || !sources.some(function(x) { return String(x.id) === String(state.activeSourceId); })) {
      state.activeSourceId = String(sources[0].id || '');
    }
    list.innerHTML = sources.map(function(item) {
      var active = String(item.id) === String(state.activeSourceId) ? ' is-active' : '';
      return '<div class="sl-item' + active + '" data-sl-source-id="' + esc(item.id || '') + '">'
        + '<strong>' + esc(sourceTitle(item)) + '</strong>'
        + '<span class="sl-meta">' + esc(typeLabel(item.source_type)) + ' · ' + esc(item.source_reason || '') + '</span>'
        + (item.description ? '<span class="sl-meta">' + esc(compactText(item.description, 100)) + '</span>' : '')
        + '</div>';
    }).join('');
    list.querySelectorAll('[data-sl-source-id]').forEach(function(node) {
      node.addEventListener('click', function() {
        state.activeSourceId = node.getAttribute('data-sl-source-id') || '';
        renderOutputs(activeJob());
      });
    });
    var item = sources.filter(function(x) { return String(x.id) === String(state.activeSourceId); })[0] || sources[0];
    detail.innerHTML = '<div class="sl-box"><h4>' + esc(sourceTitle(item)) + '</h4>'
      + '<span class="sl-meta">' + esc(typeLabel(item.source_type)) + ' · ' + esc(item.source_reason || '') + '</span>'
      + (item.url ? '<span class="sl-meta"><a href="' + esc(item.url) + '" target="_blank" rel="noopener">打开来源链接</a></span>' : '')
      + '</div>'
      + '<div class="sl-box"><h4>正文/简介</h4><div class="sl-meta">' + esc(item.description || item.title || '暂无正文。') + '</div></div>'
      + '<div class="sl-box"><h4>指标</h4><div class="sl-badges">' + metricChips(item.metrics || {}) + '</div></div>'
      + '<div class="sl-box"><h4>采集字段</h4>' + detailPairs(item.raw_preview || {}) + '</div>';
  }

  function leadsMarkdown() {
    var job = activeJob();
    var leads = buildLeads(job);
    if (!job || !leads.length) return '';
    var linesOut = ['# ' + (job.title || platformLabel() + '线索名单'), ''];
    leads.forEach(function(lead, idx) {
      linesOut.push((idx + 1) + '. ' + (lead.name || lead.handle || lead.candidate_key || '-'));
      if (lead.handle) linesOut.push('   - 账号：@' + lead.handle);
      if (lead.url) linesOut.push('   - 链接：' + lead.url);
      if (lead.source_reason) linesOut.push('   - 来源：' + lead.source_reason);
      if (lead.bio) linesOut.push('   - 信息：' + lead.bio);
      linesOut.push('');
    });
    return linesOut.join('\n');
  }

  function joinList(values) {
    return Array.isArray(values) ? values.filter(Boolean).map(function(x) { return String(x); }).join('、') : '';
  }

  function exportRowsFromJob(job) {
    var req = job && job.request_payload || {};
    var leads = buildLeads(job);
    return leads.map(function(lead, idx) {
      var evidence = Array.isArray(lead.evidence) ? lead.evidence : [];
      var relevance = lead.keyword_relevance || {};
      var evidenceText = evidence.slice(0, 5).map(function(ev) {
        return [ev.title || ev.source_reason || '', ev.description || ev.body || ''].filter(Boolean).join(' - ');
      }).filter(Boolean).join('\n');
      var evidenceLinks = evidence.slice(0, 5).map(function(ev) { return ev.url || ''; }).filter(Boolean).join('\n');
      return {
        '序号': idx + 1,
        '平台': job.platform || state.platform || '',
        '任务': job.title || '',
        '目标关键词': joinList(req.keywords || []),
        '账号': lead.handle || lead.candidate_key || '',
        '名称': lead.name || '',
        '主页': lead.url || '',
        '精准分': lead.score || lead.intent_score || '',
        '意向等级': lead.intent_level || '',
        '命中关键词': joinList(relevance.matched_keywords || []),
        '判断理由': joinList(lead.intent_reasons || []),
        '公开资料': lead.bio || '',
        '证据数量': evidence.length,
        '证据摘要': evidenceText,
        '证据链接': evidenceLinks
      };
    });
  }

  function exportLeadsExcel() {
    var job = activeJob();
    if (!job || !job.job_id) {
      setMsg('请先选择一个任务。', true);
      return;
    }
    if (!buildLeads(job).length) {
      setMsg('当前任务还没有可导出的线索。', true);
      return;
    }
    var btn = $('slExportLeadsBtn');
    if (btn) btn.disabled = true;
    setMsg('正在生成 Excel...', false);
    Promise.resolve(exportRowsFromJob(job))
      .then(function(rows) {
        if (!rows.length) throw new Error('当前任务没有可导出的线索');
        return localJson('/api/social-leads/export', {
          filename: (job.platform || state.platform || 'social') + '-leads-' + job.job_id,
          rows: rows
        });
      })
      .then(function(data) {
        var path = data.path || data.filename || '';
        setMsg((data.opened_folder ? 'Excel 已导出并打开所在目录：' : 'Excel 已导出：') + path, false);
      })
      .catch(function(err) {
        setMsg((err && err.message) || '导出失败', true);
      })
      .finally(function() {
        if (btn) btn.disabled = false;
      });
  }

  function copyText(text, okMsg) {
    text = String(text || '');
    if (!text) { setMsg('没有可复制内容。', true); return; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function() { setMsg(okMsg || '已复制。', false); }).catch(function() { fallbackCopy(text, okMsg); });
    } else {
      fallbackCopy(text, okMsg);
    }
  }

  function fallbackCopy(text, okMsg) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); setMsg(okMsg || '已复制。', false); } catch (e) { setMsg('复制失败。', true); }
    ta.remove();
  }

  function bindEvents() {
    document.querySelectorAll('#content-social-leads [data-sl-tab]').forEach(function(btn) {
      if (btn.dataset.socialLeadsTabBound === '1') return;
      btn.dataset.socialLeadsTabBound = '1';
      btn.addEventListener('click', function() { switchTab(btn.getAttribute('data-sl-tab') || 'new'); });
    });
    var startBtn = $('slStartJobBtn');
    if (startBtn && startBtn.dataset.bound !== '1') { startBtn.dataset.bound = '1'; startBtn.addEventListener('click', startJob); }
    var templateBtn = $('slCreateTemplateBtn');
    if (templateBtn && templateBtn.dataset.bound !== '1') { templateBtn.dataset.bound = '1'; templateBtn.addEventListener('click', createTemplate); }
    var refreshTemplatesBtn = $('slRefreshTemplatesBtn');
    if (refreshTemplatesBtn && refreshTemplatesBtn.dataset.bound !== '1') { refreshTemplatesBtn.dataset.bound = '1'; refreshTemplatesBtn.addEventListener('click', function() { loadTemplates(false); }); }
    var templateList = $('slTemplateList');
    if (templateList && templateList.dataset.bound !== '1') {
      templateList.dataset.bound = '1';
      templateList.addEventListener('click', function(evt) {
        var btn = evt.target && evt.target.closest ? evt.target.closest('[data-template-action]') : null;
        if (!btn) return;
        var card = btn.closest('[data-template-id]');
        var id = card ? card.getAttribute('data-template-id') : '';
        var row = (state.templates || []).filter(function(item) { return String(item.id) === String(id); })[0];
        if (btn.getAttribute('data-template-action') === 'use') fillInputsFromTemplate(row);
        else if (btn.getAttribute('data-template-action') === 'delete') deleteTemplate(id);
      });
    }
    var saveBtn = $('slSaveDraftBtn');
    if (saveBtn && saveBtn.dataset.bound !== '1') { saveBtn.dataset.bound = '1'; saveBtn.addEventListener('click', saveDraft); }
    var loadBtn = $('slLoadDraftBtn');
    if (loadBtn && loadBtn.dataset.bound !== '1') { loadBtn.dataset.bound = '1'; loadBtn.addEventListener('click', loadDraft); }
    var refreshBtn = $('socialLeadsRefreshBtn');
    if (refreshBtn && refreshBtn.dataset.bound !== '1') { refreshBtn.dataset.bound = '1'; refreshBtn.addEventListener('click', function() { loadJobs(false); }); }
    var backBtn = $('socialLeadsBackBtn');
    if (backBtn && backBtn.dataset.bound !== '1') {
      backBtn.dataset.bound = '1';
      backBtn.addEventListener('click', function() {
        if (typeof window.showLobsterView === 'function') window.showLobsterView('skill-store');
        else location.hash = 'skill-store';
      });
    }
    var switchBtn = $('socialLeadsSwitchBtn');
    if (switchBtn && switchBtn.dataset.bound !== '1') {
      switchBtn.dataset.bound = '1';
      switchBtn.addEventListener('click', function() {
        var order = ['reddit', 'x', 'tiktok'];
        var idx = order.indexOf(state.platform);
        window.__socialLeadsPlatform = order[(idx + 1) % order.length];
        resetRunState(true);
        syncPlatform(window.__socialLeadsPlatform);
        renderJobs();
        loadJobs(true);
      });
    }
    var copyBtn = $('slCopyLeadsBtn');
    if (copyBtn && copyBtn.dataset.bound !== '1') { copyBtn.dataset.bound = '1'; copyBtn.addEventListener('click', function() { copyText(leadsMarkdown(), '线索名单已复制。'); }); }
    var exportBtn = $('slExportLeadsBtn');
    if (exportBtn && exportBtn.dataset.bound !== '1') { exportBtn.dataset.bound = '1'; exportBtn.addEventListener('click', exportLeadsExcel); }
    document.querySelectorAll('#content-social-leads input[name="slSourceMode"]').forEach(function(input) {
      if (input.dataset.bound === '1') return;
      input.dataset.bound = '1';
      input.addEventListener('change', syncSourceMode);
    });
  }

  window.initSocialLeadsView = function(platform) {
    var nextPlatform = normalizePlatform(platform || window.__socialLeadsPlatform || 'reddit') || 'reddit';
    var prevPlatform = state.platform;
    syncPlatform(nextPlatform);
    if (state.platform !== prevPlatform) {
      resetRunState(true);
      renderJobs();
    }
    bindEvents();
    switchTab(state.tab || 'new');
    loadJobs(true);
  };
})();
