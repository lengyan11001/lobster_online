(function() {
  var state = {
    accounts: [],
    selectedAccount: null,
    videos: [],
    selectedKeys: new Set(),
    jobs: [],
    activeJobId: '',
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
  function headers() {
    var h = typeof authHeaders === 'function' ? Object.assign({}, authHeaders() || {}) : {};
    if (!h.Authorization && typeof token !== 'undefined' && token) h.Authorization = 'Bearer ' + token;
    h['Content-Type'] = 'application/json';
    return h;
  }
  function parseErr(data, fallback) {
    if (!data) return fallback || '请求失败';
    if (typeof data === 'string') return data;
    var detail = data.detail || data.error || data.message || data.msg;
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail.message === 'string') return detail.message;
    try { return JSON.stringify(detail || data); } catch (e) { return fallback || '请求失败'; }
  }
  function apiJson(path, opts) {
    opts = opts || {};
    var base = apiBase();
    if (!base) return Promise.reject(new Error('未配置服务器 API_BASE'));
    var req = { method: opts.method || 'GET', headers: headers() };
    if (opts.body !== undefined) req.body = JSON.stringify(opts.body || {});
    return fetch(base + path, req).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok) throw new Error(parseErr(data, '请求失败'));
        return data;
      });
    });
  }
  function setMsg(text, isErr) {
    var node = $('wctMsg');
    if (!node) return;
    node.textContent = text || '';
    node.className = text ? 'wct-msg ' + (isErr ? 'err' : 'ok') : 'wct-msg';
  }
  function accountKey(account) {
    return String(account && (account.username || account.finder_username || account.id) || '');
  }
  function videoKey(video) {
    return String(video && (video.item_key || video.public_url || video.video_url) || '');
  }
  function statusLabel(status) {
    var map = { queued: '排队中', running: '执行中', completed: '完成', failed: '失败', pending: '待处理' };
    return map[status] || status || '-';
  }
  function activeJob() {
    return state.jobs.filter(function(job) { return job.job_id === state.activeJobId; })[0] || null;
  }

  function renderAccounts() {
    var list = $('wctAccountList');
    if (!list) return;
    if (!state.accounts.length) {
      list.className = 'wct-empty';
      list.innerHTML = '暂无账号候选';
      return;
    }
    list.className = 'wct-list';
    list.innerHTML = state.accounts.map(function(account) {
      var key = accountKey(account);
      var active = state.selectedAccount && accountKey(state.selectedAccount) === key ? ' is-active' : '';
      return '<div class="wct-item' + active + '" data-wct-account="' + esc(key) + '" style="cursor:pointer;">' +
        (account.avatar_url ? '<img src="' + esc(account.avatar_url) + '" style="width:36px;height:36px;border-radius:50%;object-fit:cover;">' : '<span></span>') +
        '<div><div class="wct-item-title">' + esc(account.display_name || account.nickname || key) + '</div>' +
        '<div class="wct-meta">' + esc(key) + '</div>' +
        '<div class="wct-meta">' + esc(account.signature || account.verify_info || '') + '</div></div>' +
        '<button type="button" class="btn btn-primary btn-sm">选择</button>' +
      '</div>';
    }).join('');
  }

  function renderSelectedAccount() {
    var node = $('wctSelectedAccount');
    if (!node) return;
    if (!state.selectedAccount) {
      node.textContent = '请先选择账号';
      return;
    }
    node.textContent = (state.selectedAccount.display_name || state.selectedAccount.nickname || accountKey(state.selectedAccount)) + ' · ' + accountKey(state.selectedAccount);
  }

  function renderVideos() {
    var list = $('wctVideoList');
    if (!list) return;
    if (!state.videos.length) {
      list.className = 'wct-empty';
      list.innerHTML = state.selectedAccount ? '暂无作品，或作品里没有可下载视频链接。' : '选择账号后点击“拉取作品”。';
      return;
    }
    list.className = 'wct-list';
    list.innerHTML = state.videos.map(function(video, idx) {
      var key = videoKey(video);
      var checked = state.selectedKeys.has(key);
      return '<label class="wct-item wct-video-row" style="cursor:pointer;">' +
        '<input type="checkbox" data-wct-video="' + esc(key) + '"' + (checked ? ' checked' : '') + '>' +
        (video.cover_url ? '<img class="wct-cover" src="' + esc(video.cover_url) + '">' : '<div class="wct-cover"></div>') +
        '<div><div class="wct-item-title">' + esc(video.title || ('视频 ' + (idx + 1))) + '</div>' +
        '<div class="wct-meta">' + esc(video.publish_time || '') + '</div>' +
        '<div class="wct-meta">' + esc(video.video_url || video.public_url || '') + '</div></div>' +
      '</label>';
    }).join('');
  }

  function renderJobs() {
    var list = $('wctJobList');
    if (!list) return;
    if (!state.jobs.length) {
      list.className = 'wct-empty';
      list.innerHTML = '暂无任务';
      renderResults();
      return;
    }
    list.className = 'wct-list';
    list.innerHTML = state.jobs.map(function(job) {
      var active = job.job_id === state.activeJobId ? ' is-active' : '';
      var count = (job.items || []).length || (job.result_payload && job.result_payload.count) || 0;
      return '<div class="wct-item' + active + '" data-wct-job="' + esc(job.job_id) + '" style="grid-template-columns:minmax(0,1fr) auto;cursor:pointer;">' +
        '<div><div class="wct-item-title">' + esc(job.title || job.job_id) + '</div>' +
        '<div class="wct-meta">' + esc(job.created_at || '') + '</div>' +
        '<div class="wct-meta">' + esc(count) + ' 条 · ' + esc(job.progress || 0) + '%</div></div>' +
        '<span class="wct-status ' + esc(job.status || '') + '">' + esc(statusLabel(job.status)) + '</span>' +
      '</div>';
    }).join('');
    renderResults();
  }

  function renderResults() {
    var host = $('wctResultList');
    var summary = $('wctJobSummary');
    var job = activeJob();
    if (!host) return;
    if (!job) {
      host.className = 'wct-empty';
      host.innerHTML = '点击左侧记录查看结果。';
      if (summary) summary.textContent = '点击左侧记录查看结果。';
      return;
    }
    var items = job.items || [];
    if (summary) summary.textContent = statusLabel(job.status) + ' · ' + (job.progress || 0) + '% · ' + items.length + ' 条';
    if (!items.length) {
      host.className = 'wct-empty';
      host.innerHTML = '任务暂无明细';
      return;
    }
    host.className = '';
    host.innerHTML = items.map(function(item, idx) {
      return '<div class="wct-output">' +
        '<div class="wct-row" style="justify-content:space-between;margin-bottom:8px;">' +
        '<div><strong>' + esc(idx + 1) + '. ' + esc(item.title || item.item_key || '') + '</strong><div class="wct-meta">' + esc(item.publish_time || '') + '</div></div>' +
        '<span class="wct-status ' + esc(item.status || '') + '">' + esc(statusLabel(item.status)) + '</span></div>' +
        (item.error ? '<div class="wct-msg err" style="display:block;margin-bottom:8px;">' + esc(item.error) + '</div>' : '') +
        '<textarea readonly>' + esc(item.transcript || '') + '</textarea>' +
        '<div class="wct-actions" style="margin-top:8px;"><button type="button" class="btn btn-ghost btn-sm" data-wct-copy-one="' + esc(idx) + '">复制</button></div>' +
      '</div>';
    }).join('');
  }

  function selectedVideos() {
    var keys = [];
    document.querySelectorAll('[data-wct-video]').forEach(function(input) {
      if (input.checked) keys.push(String(input.getAttribute('data-wct-video') || ''));
    });
    state.selectedKeys = new Set(keys);
    var wanted = new Set(keys);
    return state.videos.filter(function(video) { return wanted.has(videoKey(video)); });
  }

  function searchAccounts() {
    var q = (($('wctKeywordInput') || {}).value || '').trim();
    if (!q) return setMsg('请输入视频号昵称或 username', true);
    setMsg('正在查询账号...', false);
    apiJson('/api/wechat-channels-transcript/users/search?q=' + encodeURIComponent(q)).then(function(data) {
      state.accounts = data.items || [];
      state.selectedAccount = null;
      state.videos = [];
      state.selectedKeys.clear();
      renderAccounts();
      renderSelectedAccount();
      renderVideos();
      setMsg(state.accounts.length ? '请选择要分析的视频号账号' : '没有搜到匹配账号', !state.accounts.length);
    }).catch(function(err) {
      setMsg(err.message || '账号查询失败', true);
    });
  }

  function fetchVideos() {
    if (!state.selectedAccount) return setMsg('请先选择账号', true);
    var username = accountKey(state.selectedAccount);
    setMsg('正在拉取作品...', false);
    apiJson('/api/wechat-channels-transcript/videos', {
      method: 'POST',
      body: { username: username, max_pages: 8, page_size: 20 }
    }).then(function(data) {
      state.videos = data.items || [];
      state.selectedKeys = new Set(state.videos.map(videoKey).filter(Boolean));
      renderVideos();
      setMsg('作品拉取完成：' + state.videos.length + ' 条', false);
    }).catch(function(err) {
      setMsg(err.message || '作品拉取失败', true);
    });
  }

  function createJob() {
    if (!state.selectedAccount) return setMsg('请先选择账号', true);
    var videos = selectedVideos();
    if (!videos.length) return setMsg('请至少选择一个作品', true);
    setMsg('正在创建转写任务...', false);
    apiJson('/api/wechat-channels-transcript/jobs', {
      method: 'POST',
      body: { username: accountKey(state.selectedAccount), videos: videos }
    }).then(function(data) {
      upsertJob(data.job);
      setMsg('转写任务已创建，正在后台处理。', false);
      startPolling();
    }).catch(function(err) {
      setMsg(err.message || '创建任务失败', true);
    });
  }

  function upsertJob(job) {
    if (!job || !job.job_id) return;
    var found = false;
    state.jobs = state.jobs.map(function(item) {
      if (item.job_id === job.job_id) { found = true; return job; }
      return item;
    });
    if (!found) state.jobs.unshift(job);
    state.activeJobId = job.job_id;
    renderJobs();
  }

  function loadJobs() {
    return apiJson('/api/wechat-channels-transcript/jobs?limit=50').then(function(data) {
      state.jobs = data.items || [];
      if (!state.activeJobId && state.jobs.length) state.activeJobId = state.jobs[0].job_id;
      renderJobs();
      if (state.jobs.some(function(job) { return job.status === 'running' || job.status === 'queued'; })) startPolling();
    }).catch(function(err) {
      setMsg(err.message || '任务记录加载失败', true);
    });
  }

  function refreshActiveJob() {
    var job = activeJob();
    if (!job) return Promise.resolve();
    return apiJson('/api/wechat-channels-transcript/jobs/' + encodeURIComponent(job.job_id)).then(function(data) {
      upsertJob(data.job);
      var next = activeJob();
      if (!next || (next.status !== 'running' && next.status !== 'queued')) stopPolling();
    }).catch(function(err) {
      stopPolling();
      setMsg(err.message || '任务刷新失败', true);
    });
  }

  function startPolling() {
    stopPolling();
    state.pollTimer = setInterval(refreshActiveJob, 2500);
  }
  function stopPolling() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = null;
  }

  function resumeJob() {
    var job = activeJob();
    if (!job) return setMsg('请先选择一个任务', true);
    apiJson('/api/wechat-channels-transcript/jobs/' + encodeURIComponent(job.job_id) + '/resume', { method: 'POST', body: {} }).then(function(data) {
      upsertJob(data.job);
      startPolling();
      setMsg('已继续任务', false);
    }).catch(function(err) {
      setMsg(err.message || '继续任务失败', true);
    });
  }

  function allText(job) {
    job = job || activeJob();
    if (!job) return '';
    return (job.items || []).map(function(item, idx) {
      return '### ' + (idx + 1) + '. ' + (item.title || item.item_key || '') + '\n' + (item.transcript || '') + '\n';
    }).join('\n');
  }
  function downloadText(filename, text, type) {
    var blob = new Blob([text], { type: type || 'text/plain;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(function() { URL.revokeObjectURL(url); a.remove(); }, 1000);
  }
  function exportCsv() {
    var job = activeJob();
    if (!job) return setMsg('请先选择一个任务', true);
    var rows = [['标题', '发布时间', '状态', '原链接', '视频链接', '转写文本']];
    (job.items || []).forEach(function(item) {
      rows.push([item.title || '', item.publish_time || '', statusLabel(item.status), item.public_url || '', item.video_url || '', item.transcript || item.error || '']);
    });
    var csv = rows.map(function(row) {
      return row.map(function(cell) { return '"' + String(cell || '').replace(/"/g, '""') + '"'; }).join(',');
    }).join('\r\n');
    downloadText('wechat-channels-transcripts.csv', '\ufeff' + csv, 'text/csv;charset=utf-8');
  }

  function bind(root) {
    var back = $('wctBackBtn');
    if (back) back.addEventListener('click', function() {
      var nav = document.querySelector('.nav-left-item[data-view="skill-store"]');
      if (nav) nav.click();
    });
    var search = $('wctSearchBtn');
    if (search) search.addEventListener('click', searchAccounts);
    var input = $('wctKeywordInput');
    if (input) input.addEventListener('keydown', function(evt) { if (evt.key === 'Enter') searchAccounts(); });
    var fetchBtn = $('wctFetchVideosBtn');
    if (fetchBtn) fetchBtn.addEventListener('click', fetchVideos);
    var allBtn = $('wctSelectAllBtn');
    if (allBtn) allBtn.addEventListener('click', function() {
      state.selectedKeys = new Set(state.videos.map(videoKey).filter(Boolean));
      renderVideos();
    });
    var clearBtn = $('wctClearSelectBtn');
    if (clearBtn) clearBtn.addEventListener('click', function() {
      state.selectedKeys.clear();
      renderVideos();
    });
    var createBtn = $('wctCreateJobBtn');
    if (createBtn) createBtn.addEventListener('click', createJob);
    var refreshBtn = $('wctRefreshJobsBtn');
    if (refreshBtn) refreshBtn.addEventListener('click', loadJobs);
    var resumeBtn = $('wctResumeJobBtn');
    if (resumeBtn) resumeBtn.addEventListener('click', resumeJob);
    var copyAll = $('wctCopyAllBtn');
    if (copyAll) copyAll.addEventListener('click', function() {
      var text = allText();
      if (!text) return setMsg('暂无可复制文本', true);
      navigator.clipboard.writeText(text).then(function() { setMsg('已复制全部文案', false); }).catch(function() { setMsg('复制失败', true); });
    });
    var exportTxt = $('wctExportTxtBtn');
    if (exportTxt) exportTxt.addEventListener('click', function() {
      var text = allText();
      if (!text) return setMsg('暂无可导出文本', true);
      downloadText('wechat-channels-transcripts.txt', text, 'text/plain;charset=utf-8');
    });
    var exportCsvBtn = $('wctExportCsvBtn');
    if (exportCsvBtn) exportCsvBtn.addEventListener('click', exportCsv);

    root.addEventListener('change', function(evt) {
      var video = evt.target.closest('[data-wct-video]');
      if (!video) return;
      var key = String(video.getAttribute('data-wct-video') || '');
      if (video.checked) state.selectedKeys.add(key);
      else state.selectedKeys.delete(key);
    });
    root.addEventListener('click', function(evt) {
      var account = evt.target.closest('[data-wct-account]');
      var job = evt.target.closest('[data-wct-job]');
      var copyOne = evt.target.closest('[data-wct-copy-one]');
      if (copyOne) {
        var active = activeJob();
        var idx = Number(copyOne.getAttribute('data-wct-copy-one'));
        var item = active && active.items && active.items[idx];
        var text = item && item.transcript || '';
        if (!text) return setMsg('这条暂无可复制文本', true);
        navigator.clipboard.writeText(text).then(function() { setMsg('已复制', false); }).catch(function() { setMsg('复制失败', true); });
        return;
      }
      if (job) {
        state.activeJobId = job.getAttribute('data-wct-job') || '';
        renderJobs();
        refreshActiveJob();
        return;
      }
      if (account) {
        var key = account.getAttribute('data-wct-account') || '';
        state.selectedAccount = state.accounts.filter(function(item) { return accountKey(item) === key; })[0] || null;
        state.videos = [];
        state.selectedKeys.clear();
        renderAccounts();
        renderSelectedAccount();
        renderVideos();
      }
    });
  }

  window.initWechatChannelsTranscriptView = function() {
    var root = $('content-wechat-channels-transcript');
    if (!root || root.dataset.bound === '1') return;
    root.dataset.bound = '1';
    bind(root);
    renderAccounts();
    renderSelectedAccount();
    renderVideos();
    loadJobs();
  };
})();
