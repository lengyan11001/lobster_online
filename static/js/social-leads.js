(function() {
  var state = {
    platform: 'reddit',
    tab: '',
    jobs: [],
    activeJobId: '',
    activeOutputId: '',
    activeLeadKey: '',
    activeStepKey: '',
    activeSourceId: '',
    pollTimer: null,
    autoResumeJobs: {},
    templates: [],
    templatesLoading: false,
    tiktokKeywords: [],
    tiktokSelectedKeywords: {},
    tiktokMonitorTargets: [],
    tiktokDiscoveredAccounts: [],
    tiktokVideoResults: [],
    tiktokSelectedVideos: [],
    tiktokVideoKeyword: ''
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

  function tiktokMonitorStorageKey() {
    return 'tiktok_monitor_targets_v1';
  }

  function loadTikTokMonitorTargets() {
    try {
      var rows = JSON.parse(localStorage.getItem(tiktokMonitorStorageKey()) || '[]');
      state.tiktokMonitorTargets = Array.isArray(rows) ? rows.filter(function(row) { return row && row.handle; }) : [];
    } catch (e) {
      state.tiktokMonitorTargets = [];
    }
    renderTikTokMonitorTargets();
  }

  function saveTikTokMonitorTargets() {
    try { localStorage.setItem(tiktokMonitorStorageKey(), JSON.stringify(state.tiktokMonitorTargets || [])); } catch (e) {}
  }

  function syncTikTokAccountsInput() {
    var input = $('slAccountsInput');
    if (!input) return;
    var handles = (state.tiktokMonitorTargets || []).filter(function(row) { return row.selected; }).map(function(row) { return row.handle; });
    input.value = handles.join('\n');
  }

  function renderTikTokMonitorTargets() {
    var host = $('slMonitorList');
    if (!host) return;
    var rows = state.tiktokMonitorTargets || [];
    if (!rows.length) {
      host.innerHTML = '<div class="sl-meta">还没有监控同行。先用上面的拓词搜索，把同行加入监控。</div>';
      return;
    }
    host.innerHTML = rows.map(function(row) {
      var followers = Number(row.followers || 0);
      var meta = ['@' + row.handle, followers ? (Math.round(followers / 100) / 10) + 'K 粉丝' : '', row.matched_keywords && row.matched_keywords.length ? '来自 ' + row.matched_keywords.slice(0, 2).join('、') : ''].filter(Boolean).join(' · ');
      return '<div class="sl-monitor-row"><input type="checkbox" data-monitor-toggle="' + esc(row.handle) + '" ' + (row.selected ? 'checked' : '') + '><div class="sl-row-main"><strong>' + esc(row.name || row.handle) + '</strong><span class="sl-meta">' + esc(meta) + '</span></div><button type="button" class="btn btn-ghost btn-sm" data-remove-monitor="' + esc(row.handle) + '">移除</button></div>';
    }).join('');
    host.querySelectorAll('[data-monitor-toggle]').forEach(function(input) {
      input.addEventListener('change', function() {
        var handle = input.getAttribute('data-monitor-toggle');
        state.tiktokMonitorTargets = state.tiktokMonitorTargets.map(function(row) {
          if (row.handle === handle) row.selected = !!input.checked;
          return row;
        });
        saveTikTokMonitorTargets();
        syncTikTokAccountsInput();
      });
    });
    host.querySelectorAll('[data-remove-monitor]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var handle = btn.getAttribute('data-remove-monitor');
        state.tiktokMonitorTargets = state.tiktokMonitorTargets.filter(function(row) { return row.handle !== handle; });
        saveTikTokMonitorTargets();
        renderTikTokMonitorTargets();
        syncTikTokAccountsInput();
      });
    });
  }

  function addTikTokMonitorTarget(account) {
    account = account || {};
    var handle = String(account.handle || account.unique_id || '').replace(/^@/, '').trim();
    if (!handle) return;
    var exists = (state.tiktokMonitorTargets || []).some(function(row) { return row.handle === handle; });
    if (!exists) {
      state.tiktokMonitorTargets.unshift({
        handle: handle,
        name: account.name || handle,
        followers: Number(account.followers || 0),
        avatar_url: account.avatar_url || '',
        matched_keywords: Array.isArray(account.matched_keywords) ? account.matched_keywords : [],
        selected: true,
        added_at: new Date().toISOString()
      });
      saveTikTokMonitorTargets();
    }
    renderTikTokMonitorTargets();
    syncTikTokAccountsInput();
    setMsg('已加入监控：@' + handle, false);
  }

  function renderTikTokKeywordChips() {
    var host = $('slKeywordChips');
    if (!host) return;
    var words = state.tiktokKeywords || [];
    if (!words.length) {
      host.innerHTML = '<span class="sl-meta">输入种子词后点「AI 拓词」，会生成可勾选的关键词。</span>';
      return;
    }
    host.innerHTML = words.map(function(word) {
      return '<button type="button" class="sl-keyword-chip' + (state.tiktokSelectedKeywords[word] ? ' is-active' : '') + '" data-keyword-chip="' + esc(word) + '">' + esc(word) + '</button>';
    }).join('');
    host.querySelectorAll('[data-keyword-chip]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var word = btn.getAttribute('data-keyword-chip');
        if (state.tiktokSelectedKeywords[word]) delete state.tiktokSelectedKeywords[word];
        else state.tiktokSelectedKeywords[word] = true;
        renderTikTokKeywordChips();
      });
    });
  }

  function renderTikTokDiscoverList(accounts) {
    var host = $('slDiscoverList');
    if (!host) return;
    state.tiktokDiscoveredAccounts = Array.isArray(accounts) ? accounts : [];
    if (!state.tiktokDiscoveredAccounts.length) {
      host.innerHTML = '<div class="sl-meta">还没有发现同行。先勾选关键词，再点「搜索同行」。</div>';
      return;
    }
    host.innerHTML = state.tiktokDiscoveredAccounts.map(function(account) {
      var handle = String(account.handle || account.unique_id || '').replace(/^@/, '');
      var watched = (state.tiktokMonitorTargets || []).some(function(row) { return row.handle === handle; });
      var followers = Number(account.followers || 0);
      return '<div class="sl-discover-row"><div class="sl-row-main"><strong>' + esc(account.name || handle) + '</strong><span class="sl-meta">@' + esc(handle) + (followers ? ' · ' + (Math.round(followers / 100) / 10) + 'K 粉丝' : '') + (account.matched_keywords && account.matched_keywords.length ? ' · ' + esc(account.matched_keywords.slice(0, 2).join('、')) : '') + '</span></div><button type="button" class="btn btn-ghost btn-sm" data-add-competitor="' + esc(handle) + '" ' + (watched ? 'disabled' : '') + '>' + (watched ? '已监控' : '加入监控') + '</button></div>';
    }).join('');
    host.querySelectorAll('[data-add-competitor]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var handle = btn.getAttribute('data-add-competitor');
        var account = state.tiktokDiscoveredAccounts.filter(function(row) { return String(row.handle || row.unique_id || '').replace(/^@/, '') === handle; })[0];
        addTikTokMonitorTarget(account);
      });
    });
  }
  function expandTikTokKeywords() {
    var seed = ($('slSeedKeyword') || {}).value || '';
    seed = String(seed).trim();
    if (!seed) { setMsg('请输入种子关键词。', true); return; }
    var btn = $('slExpandKeywordsBtn');
    if (btn) { btn.disabled = true; btn.textContent = '拓词中…'; }
    var status = $('slDiscoverStatus');
    if (status) status.textContent = '正在生成相关关键词…';
    setMsg('正在拓词...', false);
    apiJson('/api/social-leads/tiktok/expand-keywords', { method: 'POST', body: { seed_keyword: seed, count: 10 } })
      .then(function(data) {
        state.tiktokKeywords = Array.isArray(data.keywords) ? data.keywords : [];
        state.tiktokSelectedKeywords = {};
        state.tiktokKeywords.forEach(function(word) { state.tiktokSelectedKeywords[word] = true; });
        renderTikTokKeywordChips();
        setMsg('拓词完成，共 ' + state.tiktokKeywords.length + ' 个关键词。', false);
      })
      .catch(function(err) { setMsg(err.message || '拓词失败', true); })
      .finally(function() { if (btn) { btn.disabled = false; btn.textContent = 'AI 拓词'; } });
  }

  function discoverTikTokCompetitors() {
    var words = Object.keys(state.tiktokSelectedKeywords || {});
    if (!words.length) words = (state.tiktokKeywords || []).slice(0, 5);
    if (!words.length) { setMsg('请先拓词并选择至少一个关键词。', true); return; }
    var btn = $('slDiscoverBtn');
    if (btn) { btn.disabled = true; btn.textContent = '搜索中…'; }
    var status = $('slDiscoverStatus');
    if (status) status.textContent = '正在按关键词搜索同行，通常需要几秒…';
    setMsg('正在按关键词搜索同行...', false);
    apiJson('/api/social-leads/tiktok/discover-competitors', { method: 'POST', body: { keywords: words, limit: 20 } })
      .then(function(data) {
        var accounts = data.accounts || [];
        renderTikTokDiscoverList(accounts);
        if (status) status.textContent = accounts.length ? ('找到 ' + accounts.length + ' 个同行，点「加入监控」后可在任务设置里批量采集。') : '没有找到同行，换个关键词再试。';
        setMsg(accounts.length ? ('发现 ' + accounts.length + ' 个同行账号。') : '没有找到同行，换个关键词再试。', !accounts.length);
      })
      .catch(function(err) { if (status) status.textContent = '搜索失败：' + (err.message || '请稍后重试'); setMsg(err.message || '搜索同行失败', true); })
      .finally(function() { if (btn) { btn.disabled = false; btn.textContent = '搜索同行'; } });
  }
  function isVideoSelected(id) {
    id = String(id || '');
    return (state.tiktokSelectedVideos || []).some(function(item) { return String(item.aweme_id || '') === id; });
  }

  function videoSelectedIds() {
    return (state.tiktokSelectedVideos || []).map(function(item) { return String(item.aweme_id || ''); }).filter(Boolean);
  }

  function renderTikTokSelectedVideos() {
    var rows = state.tiktokSelectedVideos || [];
    var count = $('slVideoSelectedCount');
    if (count) count.textContent = '已选 ' + rows.length + ' 条视频';
    var useBtn = $('slUseVideosBtn');
    if (useBtn) {
      useBtn.disabled = !rows.length;
      useBtn.textContent = rows.length ? ('用所选 ' + rows.length + ' 条视频建任务') : '用所选视频建任务';
    }
    var hint = $('slSelectedVideosHint');
    if (hint) hint.style.display = rows.length ? '' : 'none';
    var list = $('slSelectedVideoList');
    if (list) {
      list.innerHTML = rows.map(function(item) {
        return '<span class="sl-selected-video-chip">' + esc(compactText(item.title || item.aweme_id, 26))
          + '<button type="button" data-remove-video="' + esc(item.aweme_id) + '" aria-label="移除">×</button></span>';
      }).join('');
      list.querySelectorAll('[data-remove-video]').forEach(function(btn) {
        btn.addEventListener('click', function() { removeSelectedVideo(btn.getAttribute('data-remove-video')); });
      });
    }
    document.querySelectorAll('#content-social-leads [data-video-pick]').forEach(function(card) {
      var selected = isVideoSelected(card.getAttribute('data-video-pick'));
      card.classList.toggle('is-selected', selected);
      var box = card.querySelector('input[type="checkbox"]');
      if (box) box.checked = selected;
    });
  }

  function addSelectedVideo(video) {
    video = video || {};
    var id = String(video.aweme_id || '').trim();
    if (!id || isVideoSelected(id)) return;
    if ((state.tiktokSelectedVideos || []).length >= 30) {
      setMsg('一次最多选择 30 条视频。', true);
      return;
    }
    state.tiktokSelectedVideos.push(video);
    renderTikTokSelectedVideos();
  }

  function removeSelectedVideo(id) {
    id = String(id || '');
    state.tiktokSelectedVideos = (state.tiktokSelectedVideos || []).filter(function(item) { return String(item.aweme_id || '') !== id; });
    renderTikTokSelectedVideos();
  }

  function renderTikTokVideoResults(videos) {
    var host = $('slVideoResults');
    if (!host) return;
    videos = Array.isArray(videos) ? videos : [];
    if (!videos.length) {
      host.innerHTML = state.tiktokVideoKeyword
        ? '<div class="sl-meta">没有搜到视频。换个关键词再试，或去「拓词找同行」按同行采集。</div>'
        : '<div class="sl-meta">还没有搜索结果。输入关键词后点「搜索视频」。</div>';
      return;
    }
    host.innerHTML = videos.map(function(video) {
      var id = String(video.aweme_id || '');
      var cover = video.cover_url
        ? '<img src="' + esc(video.cover_url) + '" alt="" loading="lazy" referrerpolicy="no-referrer">'
        : '<div class="sl-video-noimg">无封面</div>';
      var stats = [['播放', video.play_count], ['点赞', video.digg_count], ['评论', video.comment_count]].filter(function(pair) {
        return Number(pair[1]) > 0;
      }).map(function(pair) { return pair[0] + ' ' + Number(pair[1]); }).join(' · ');
      return '<div class="sl-video-card' + (isVideoSelected(id) ? ' is-selected' : '') + '" data-video-pick="' + esc(id) + '">'
        + '<div class="sl-video-cover">' + cover
        + '<label class="sl-video-check"><input type="checkbox" ' + (isVideoSelected(id) ? 'checked' : '') + '></label></div>'
        + '<div class="sl-video-body"><strong>' + esc(compactText(video.title || '(无标题)', 70)) + '</strong>'
        + '<span class="sl-meta">@' + esc(video.author_handle || '') + (video.author_name ? ' · ' + esc(video.author_name) : '') + '</span>'
        + '<span class="sl-video-stats">' + esc(stats || '暂无互动数据') + '</span>'
        + (video.url ? '<a href="' + esc(video.url) + '" target="_blank" rel="noopener">打开视频</a>' : '')
        + '</div></div>';
    }).join('');
    host.querySelectorAll('[data-video-pick]').forEach(function(card) {
      var id = card.getAttribute('data-video-pick');
      var video = videos.filter(function(item) { return String(item.aweme_id || '') === id; })[0];
      var box = card.querySelector('input[type="checkbox"]');
      if (box) {
        box.addEventListener('change', function(evt) {
          evt.stopPropagation();
          if (box.checked) addSelectedVideo(video);
          else removeSelectedVideo(id);
        });
      }
      card.addEventListener('click', function(evt) {
        if (evt.target && evt.target.closest && (evt.target.closest('a') || evt.target.closest('label') || evt.target.tagName === 'INPUT')) return;
        if (isVideoSelected(id)) removeSelectedVideo(id);
        else addSelectedVideo(video);
      });
    });
  }

  function searchTikTokVideos() {
    var keyword = String(($('slVideoKeyword') || {}).value || '').trim();
    if (!keyword) { setMsg('请输入要搜索的视频关键词。', true); return; }
    var btn = $('slSearchVideosBtn');
    var status = $('slVideoSearchStatus');
    if (btn) { btn.disabled = true; btn.textContent = '搜索中…'; }
    if (status) status.textContent = '正在搜索 TikTok 视频，请稍候…';
    apiJson('/api/social-leads/tiktok/search-videos', { method: 'POST', body: { keyword: keyword, count: 20 } })
      .then(function(data) {
        var videos = Array.isArray(data.videos) ? data.videos : [];
        state.tiktokVideoKeyword = keyword;
        state.tiktokVideoResults = videos;
        renderTikTokVideoResults(videos);
        if (status) status.textContent = videos.length
          ? ('关键词「' + keyword + '」找到 ' + videos.length + ' 条视频，勾选后点下面按钮建任务。')
          : ('关键词「' + keyword + '」没有搜到视频，换个词再试。');
        setMsg(videos.length ? ('找到 ' + videos.length + ' 条视频。') : '没有搜到视频，换个关键词试试。', !videos.length);
      })
      .catch(function(err) {
        if (status) status.textContent = '搜索失败：' + (err.message || '请稍后重试');
        setMsg(err.message || '搜索视频失败', true);
      })
      .finally(function() {
        if (btn) { btn.disabled = false; btn.textContent = '搜索视频'; }
      });
  }

  function useSelectedTikTokVideos() {
    var rows = state.tiktokSelectedVideos || [];
    if (!rows.length) { setMsg('请先勾选要分析的视频。', true); return; }
    switchTab('new');
    renderTikTokSelectedVideos();
    setMsg('已选 ' + rows.length + ' 条视频。填写客户标准和采集数量后开始分析。', false);
    var kw = $('slKeywordsInput');
    if (kw && !String(kw.value || '').trim()) kw.focus();
  }

  function clearSelectedTikTokVideos() {
    state.tiktokSelectedVideos = [];
    renderTikTokSelectedVideos();
    setMsg('已清空所选视频。', false);
  }

  function syncPlatform(value) {
    var next = normalizePlatform(value || window.__socialLeadsPlatform || state.platform || 'reddit') || 'reddit';
    state.platform = next;
    window.__socialLeadsPlatform = next;
    var page = document.querySelector('#content-social-leads .social-leads-page');
    if (page) {
      page.classList.toggle('is-x', state.platform === 'x');
      page.classList.toggle('is-tiktok', state.platform === 'tiktok');
    }
    var isTikTok = state.platform === 'tiktok';
    if ($('slTikTokHero')) $('slTikTokHero').style.display = isTikTok ? '' : 'none';
    document.querySelectorAll('#content-social-leads [data-tiktok-only]').forEach(function(el) {
      el.style.display = isTikTok ? '' : 'none';
    });
    if (isTikTok) {
      loadTikTokMonitorTargets();
      renderTikTokKeywordChips();
      renderTikTokDiscoverList(state.tiktokDiscoveredAccounts);
      renderTikTokVideoResults(state.tiktokVideoResults);
      renderTikTokSelectedVideos();
    } else if (state.tab === 'keyword-search' || state.tab === 'discover') {
      switchTab('new');
    }
    if ($('slTikTokLimits')) $('slTikTokLimits').style.display = isTikTok ? '' : 'none';
    if ($('slTemplateCard')) $('slTemplateCard').style.display = isTikTok ? 'none' : '';
    if ($('slCreateTemplateBtn')) $('slCreateTemplateBtn').style.display = isTikTok ? 'none' : '';
    if ($('slRuleHint')) $('slRuleHint').style.display = isTikTok ? 'none' : '';
    var sourceToggle = document.querySelector('#content-social-leads .sl-source-toggle');
    if (sourceToggle) sourceToggle.style.display = isTikTok ? 'none' : '';
    if ($('socialLeadsKicker')) $('socialLeadsKicker').textContent = isTikTok ? 'TikTok 精准获客' : (platformLabel() + ' 线索采集');
    if ($('socialLeadsTitle')) $('socialLeadsTitle').textContent = isTikTok ? '从同行评论区找精准客户' : (platformLabel() + ' 公开信息采集工作台');
    if ($('socialLeadsSubtitle')) $('socialLeadsSubtitle').textContent = isTikTok ? '任务是任务，结果是结果：先创建采集，再在任务记录里看客户' : '只采集公开信息，不执行评论、发布或私信动作';
    if ($('slKeywordsLabel')) $('slKeywordsLabel').textContent = isTikTok ? '什么算精准客户（必填）' : '精准用户方向关键词（必填）';
    if ($('slKeywordsInput')) $('slKeywordsInput').placeholder = isTikTok ? '用一句话描述理想客户，例如：想采购定制笔记本的企业采购，问价格、起订量、交期，想印 logo' : '用户想抓哪类精准用户。每行一个，例如：AI automation\\nlead generation';
    if ($('slAccountsLabel')) $('slAccountsLabel').textContent = isTikTok ? '同行账号（必填）' : '账号';
    if ($('slAccountsInput')) $('slAccountsInput').placeholder = isTikTok ? '每行一个 TikTok 同行主页或 @unique_id，例如：@brand' : '每行一个。Reddit 可填 u/xxx；TikTok/X 可填 @xxx 或主页链接';
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
    if (state.platform === 'tiktok') return 'account';
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
    var keywords = lines(($('slKeywordsInput') || {}).value);
    var isTikTok = state.platform === 'tiktok';
    return {
      platform: state.platform,
      title: ($('slJobTitleInput') || {}).value || '',
      keywords: keywords,
      intent_prompt: isTikTok ? keywords.join('\n') : '',
      source_keywords: isTikTok ? Object.keys(state.tiktokSelectedKeywords || {}) : ((state.platform === 'x') && mode === 'community' ? sourceValues : []),
      accounts: isTikTok ? Array.from(new Set((state.tiktokMonitorTargets || []).filter(function(row) { return row.selected; }).map(function(row) { return row.handle; }).concat(lines(($('slAccountsInput') || {}).value)))) : (mode === 'account' ? lines(($('slAccountsInput') || {}).value) : []),
      post_ids: isTikTok ? videoSelectedIds() : [],
      communities: state.platform === 'reddit' && mode === 'community' ? sourceValues : [],
      country: '',
      search_type: '',
      sort: 'NEW',
      time_range: 'day',
      max_items: isTikTok ? Number(($('slMaxLeadsInput') || {}).value || 50) : 100,
      max_videos: isTikTok ? Number(($('slMaxVideosInput') || {}).value || 12) : 20,
      max_comments_per_video: isTikTok ? Number(($('slMaxCommentsInput') || {}).value || 100) : 100,
      time_range_days: isTikTok ? Number(($('slDaysInput') || {}).value || 30) : 30,
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
        if (state.activeJobId) loadJobDetail(state.activeJobId);
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
    if (state.platform === 'tiktok' && !body.accounts.length && !body.source_keywords.length && !body.post_ids.length) {
      setMsg('请选择要分析的视频、监控同行，或先拓词并勾选关键词。', true);
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
        + '<div class="sl-badges">' + statusBadge(job.status) + (isLegacyTikTokJob(job) ? '<span class="sl-badge">旧版数据</span>' : '') + '<span class="sl-badge">' + esc(buildLeads(job).length) + ' 客户</span><span class="sl-badge">' + esc((job.steps || []).length) + ' 步</span></div>'
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
        loadJobDetail(state.activeJobId);
        switchTab('run-detail');
      });
    });
    renderActiveJob();
  }

  function loadJobDetail(jobId) {
    if (!jobId) return Promise.resolve();
    return apiJson('/api/social-leads/jobs/' + encodeURIComponent(jobId))
      .then(function(data) {
        if (!data || !data.job) return;
        replaceJob(data.job);
        renderJobs();
      })
      .catch(function() {});
  }

  function renderActiveJob() {
    var job = activeJob();
    if (job && jobPlatform(job) !== state.platform) job = null;
    var bar = $('slProgressBar');
    if (bar) bar.style.width = Math.max(0, Math.min(100, Number(job && job.progress || 0))) + '%';
    var summaryHtml = '';
    if (!job) {
      summaryHtml = '<div class="sl-meta">请选择左侧任务。</div>';
    } else {
      var leads = buildLeads(job);
      summaryHtml = '<div class="sl-box"><h4>' + esc(job.title || (platformLabel() + '线索采集')) + '</h4>'
        + '<div class="sl-badges">' + statusBadge(job.status) + '<span class="sl-badge">' + esc(Number(job.progress || 0)) + '%</span><span class="sl-badge">' + esc(leads.length) + ' 客户</span><span class="sl-badge">' + esc((job.steps || []).length) + ' 步</span></div>'
        + (job.error ? '<div class="sl-source-desc" style="color:#be123c;">' + esc(job.error) + '</div>' : '')
        + '<div class="sl-meta">创建于 ' + esc(fmtTime(job.created_at)) + '</div></div>';
    }
    var summary = $('slRunSummary');
    if (summary) summary.innerHTML = summaryHtml;
    var runHead = $('slRunHead');
    if (runHead) runHead.innerHTML = summaryHtml;
    var openBtn = $('slOpenRunDetailBtn');
    if (openBtn) openBtn.disabled = !job;
    renderSteps(job);
    renderLeads(job);
    renderOutputs(job);
  }
  function renderSteps(job) {
    var host = $('slStepList');
    if (!host) return;
    if (!job) {
      host.innerHTML = '<div class="sl-meta">请选择一个任务。</div>';
      state.stepsDigest = '';
      return;
    }
    var steps = Array.isArray(job.steps) ? job.steps : [];
    if (steps.length && !steps.some(function(step) { return step.key === state.activeStepKey; })) {
      state.activeStepKey = steps[0].key || '';
    }
    var digest = JSON.stringify({ active: state.activeStepKey, steps: steps.map(function(step) {
      return [step.key, step.status, step.error, stepSources(job, step.key).length];
    }) });
    if (state.stepsDigest !== digest) {
      state.stepsDigest = digest;
      host.innerHTML = steps.map(function(step, idx) {
        var st = step.status || 'pending';
        var active = step.key === state.activeStepKey ? ' is-active' : '';
        var sourceCount = stepSources(job, step.key).length;
        return '<div class="sl-step is-' + esc(st) + active + '" data-sl-step-key="' + esc(step.key || '') + '">'
          + '<span class="sl-step-dot">' + esc(idx + 1) + '</span>'
          + '<div><strong>' + esc(step.label || step.key) + '</strong>'
          + '<span class="sl-meta" data-step-detail>' + esc(step.detail || '') + '</span>'
          + (step.error ? '<span class="sl-meta" style="color:#be123c;">' + esc(step.error) + '</span>' : '')
          + '</div><div class="sl-row-actions">' + statusBadge(st)
          + '<button type="button" class="btn btn-ghost btn-sm" data-open-step="' + esc(step.key || '') + '">查看明细' + (sourceCount ? ' (' + esc(sourceCount) + ')' : '') + '</button>'
          + '</div></div>';
      }).join('');
    }
    // 进度文案单独更新，避免轮询时整段列表重绘造成闪烁和点击丢失
    host.querySelectorAll('[data-sl-step-key]').forEach(function(node) {
      var key = node.getAttribute('data-sl-step-key');
      var step = steps.filter(function(item) { return item.key === key; })[0];
      var detail = node.querySelector('[data-step-detail]');
      if (step && detail) detail.textContent = step.detail || '';
    });
  }

  function openStepDetail(stepKey) {
    var job = activeJob();
    if (!job) return;
    state.activeStepKey = stepKey || state.activeStepKey || '';
    var steps = Array.isArray(job.steps) ? job.steps : [];
    var selected = steps.filter(function(step) { return step.key === state.activeStepKey; })[0] || null;
    var rows = stepSources(job, selected && selected.key);
    var modal = $('slStepModal');
    if (!modal) return;
    var title = $('slStepModalTitle');
    var meta = $('slStepModalMeta');
    var body = $('slStepModalBody');
    if (title) title.textContent = selected ? (selected.label || selected.key || '步骤明细') : '步骤明细';
    if (meta) meta.innerHTML = selected
      ? '<span class="sl-badge">' + esc(rows.length) + ' 条明细</span> ' + esc(selected.detail || '')
      : '请选择一个步骤。';
    if (body) body.innerHTML = renderSourceCards(rows, '这一步还没有保存采集明细。任务执行中会持续写入，稍后刷新再看。');
    modal.hidden = false;
    document.body.classList.add('sl-modal-open');
  }

  function closeStepDetail() {
    var modal = $('slStepModal');
    if (modal) modal.hidden = true;
    document.body.classList.remove('sl-modal-open');
  }

  function renderStepDetail(job) {
    var detail = $('slStepDetail');
    if (!detail) return;
    var steps = job && Array.isArray(job.steps) ? job.steps : [];
    var selected = steps.filter(function(step) { return step.key === state.activeStepKey; })[0] || null;
    var rows = stepSources(job, selected && selected.key);
    detail.innerHTML = selected
      ? '<div class="sl-box"><h4>' + esc(selected.label || selected.key) + '</h4><span class="sl-meta">' + esc(selected.detail || '') + '</span><div class="sl-badges">' + statusBadge(selected.status || 'pending') + '<span class="sl-badge">' + esc(rows.length) + ' 条明细</span></div></div>' + renderSourceCards(rows, '这一步还没有保存采集明细。')
      : '<div class="sl-meta">请选择左侧步骤。</div>';
  }
  function leadKey(item) {
    item = item || {};
    return String(item.candidate_key || item.handle || item.url || item.name || 'unknown-lead').slice(0, 240);
  }

  function leadComments(lead) {
    return lead && Array.isArray(lead.comments) ? lead.comments : [];
  }

  function leadCommentText(lead) {
    if (!lead) return '';
    if (lead.comment_text) return String(lead.comment_text);
    var comments = leadComments(lead);
    return comments.length ? String(comments[0].text || '') : '';
  }

  function epochText(value) {
    var n = Number(value || 0);
    if (!n) return '';
    var ms = n > 1000000000000 ? n : n * 1000;
    return fmtTime(new Date(ms).toISOString());
  }

  function buildLeads(job) {
    var payload = job && job.result_payload && typeof job.result_payload === 'object' ? job.result_payload : {};
    var rows = Array.isArray(payload.leads) ? payload.leads : payload.candidates;
    if (!Array.isArray(rows)) rows = [];
    return rows.map(function(item) {
      return Object.assign({}, item, { key: leadKey(item) });
    });
  }

  function isLegacyTikTokJob(job) {
    if (!job || String(job.platform || '').toLowerCase() !== 'tiktok') return false;
    var payload = job.result_payload && typeof job.result_payload === 'object' ? job.result_payload : {};
    return !Array.isArray(payload.leads);
  }
  function renderLeads(job) {
    var list = $('slLeadList');
    var detail = $('slLeadDetail');
    if (!list || !detail) return;
    var leads = buildLeads(job);
    if (!job) {
      list.innerHTML = '<div class="sl-meta">请选择一个任务。</div>';
      detail.innerHTML = '<div class="sl-meta">请选择左侧客户。</div>';
      return;
    }
    if (!leads.length) {
      list.innerHTML = '<div class="sl-meta">这个任务还没有筛出精准客户。可以回到任务记录查看采集步骤，或放宽客户描述后重新创建任务。</div>';
      detail.innerHTML = '<div class="sl-box"><h4>还没有精准客户</h4><span class="sl-meta">先确认评论是否采集成功，再看平台高意向和客户描述是否过窄。</span></div>';
      return;
    }
    if (!state.activeLeadKey || !leads.some(function(x) { return x.key === state.activeLeadKey; })) {
      state.activeLeadKey = leads[0].key;
    }
    var legacyBanner = isLegacyTikTokJob(job)
      ? '<div class="sl-legacy"><b>这是旧版本任务</b><span>旧任务只保存了少量候选，没有评论原文；请新建任务重新采集。</span><button type="button" data-sl-go-new="1">新建任务</button></div>'
      : '';
    list.innerHTML = legacyBanner + leads.map(function(lead) {
      var active = lead.key === state.activeLeadKey ? ' is-active' : '';
      var title = lead.name || lead.handle || lead.url || lead.candidate_key || '-';
      var region = lead.region || '';
      var language = lead.language || lead.comment_language || '';
      var followers = Number(lead.followers || 0);
      var sub = [lead.handle ? '@' + lead.handle : '', region, language, followers ? (followers >= 1000 ? (Math.round(followers / 100) / 10) + 'K 粉丝' : followers + ' 粉丝') : ''].filter(Boolean).join(' · ');
      var comment = leadCommentText(lead);
      var comments = leadComments(lead);
      var matched = (lead.keyword_relevance && lead.keyword_relevance.matched_keywords) || [];
      return '<div class="sl-lead' + active + '" data-sl-lead-key="' + esc(lead.key) + '">'
        + '<strong>' + esc(title) + '</strong>'
        + '<span class="sl-meta">' + esc(sub || 'TikTok 用户') + '</span>'
        + (comment ? '<div class="sl-lead-comment">“' + esc(compactText(comment, 180)) + '”</div>' : '')
        + '<div class="sl-badges"><span class="sl-badge">意向 ' + esc(lead.score || lead.intent_score || '-') + '</span>'
        + (lead.platform_high_intent ? '<span class="sl-badge done">平台高意向</span>' : '')
        + (comments.length ? '<span class="sl-badge">' + esc(comments.length) + ' 条评论证据</span>' : '')
        + (matched.length ? '<span class="sl-badge">命中 ' + esc(matched.join('、')) + '</span>' : '')
        + '</div></div>';
    }).join('');
    list.querySelectorAll('[data-sl-go-new]').forEach(function(node) {
      node.addEventListener('click', function() { switchTab('new'); });
    });
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
      host.innerHTML = '<div class="sl-meta">请选择左侧客户。</div>';
      return;
    }
    var comments = leadComments(lead);
    var reasons = Array.isArray(lead.intent_reasons) ? lead.intent_reasons : [];
    var relevance = lead.keyword_relevance || {};
    var matched = Array.isArray(relevance.matched_keywords) ? relevance.matched_keywords : [];
    var profileUrl = lead.profile_url || lead.url || '';
    var commentHtml = comments.length ? comments.slice(0, 8).map(function(item) {
      var meta = [item.video_title || '', epochText(item.comment_time), item.likes ? ('点赞 ' + item.likes) : '', item.language || ''].filter(Boolean).join(' · ');
      return '<div class="sl-comment-item"><p>“' + esc(item.text || '') + '”</p><span class="sl-meta">' + esc(meta) + '</span>'
        + (item.video_url ? '<a class="sl-meta" href="' + esc(item.video_url) + '" target="_blank" rel="noopener">看来源视频</a>' : '')
        + '</div>';
    }).join('') : (lead.comment_text ? '<div class="sl-comment-item"><p>“' + esc(lead.comment_text) + '”</p></div>' : '<div class="sl-meta">这条客户没有可展示的评论原文。</div>');
    var evidence = Array.isArray(lead.evidence) ? lead.evidence : [];
    var evidenceHtml = evidence.length ? evidence.slice(0, 6).map(function(ev, idx) {
      return '<div class="sl-evidence"><strong>来源 ' + esc(idx + 1) + ' · ' + esc(typeLabel(ev.source_type)) + '</strong>'
        + (ev.title ? '<span class="sl-meta">' + esc(ev.title) + '</span>' : '')
        + (ev.description ? '<div class="sl-source-desc">' + esc(compactText(ev.description, 280)) + '</div>' : '')
        + (ev.url ? '<span class="sl-meta"><a href="' + esc(ev.url) + '" target="_blank" rel="noopener">打开来源</a></span>' : '')
        + '</div>';
    }).join('') : '<div class="sl-meta">暂无补充证据。</div>';
    var meta = [lead.region || '', lead.language || lead.comment_language || '', lead.followers ? (lead.followers + ' 粉丝') : ''].filter(Boolean).join(' · ');
    host.innerHTML = '<div class="sl-box"><h4>' + esc(lead.name || lead.handle || 'TikTok 客户') + '</h4>'
      + (lead.handle ? '<div class="sl-meta">@' + esc(lead.handle) + '</div>' : '')
      + (meta ? '<div class="sl-meta">' + esc(meta) + '</div>' : '')
      + (profileUrl ? '<div class="sl-meta"><a href="' + esc(profileUrl) + '" target="_blank" rel="noopener">打开 TikTok 主页</a></div>' : '')
      + '<div class="sl-badges"><span class="sl-badge">意向 ' + esc(lead.score || lead.intent_score || '-') + '</span>'
      + (lead.platform_high_intent ? '<span class="sl-badge done">平台高意向</span>' : '')
      + '<span class="sl-badge">' + esc(comments.length || (lead.comment_text ? 1 : 0)) + ' 条评论</span></div></div>'
      + '<div class="sl-box"><h4>他咨询了什么</h4>' + commentHtml + '</div>'
      + '<div class="sl-box"><h4>为什么是精准客户</h4>'
      + '<div class="sl-badges">' + (matched.length ? '<span class="sl-badge">命中 ' + esc(matched.join('、')) + '</span>' : '<span class="sl-badge">补充判断</span>') + '</div>'
      + '<div class="sl-source-desc">' + esc(reasons.length ? reasons.join('；') : '平台高意向评论或客户描述命中') + '</div></div>'
      + '<div class="sl-box"><h4>客户资料</h4><div class="sl-meta">' + esc(lead.bio || '暂无主页简介。') + '</div>'
      + '<div class="sl-badges">' + metricChips(lead.metrics || {}) + '</div></div>'
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
    var searchVideosBtn = $('slSearchVideosBtn');
    if (searchVideosBtn && searchVideosBtn.dataset.bound !== '1') { searchVideosBtn.dataset.bound = '1'; searchVideosBtn.addEventListener('click', searchTikTokVideos); }
    var videoKeyword = $('slVideoKeyword');
    if (videoKeyword && videoKeyword.dataset.bound !== '1') {
      videoKeyword.dataset.bound = '1';
      videoKeyword.addEventListener('keydown', function(evt) { if (evt.key === 'Enter') { evt.preventDefault(); searchTikTokVideos(); } });
    }
    var useVideosBtn = $('slUseVideosBtn');
    if (useVideosBtn && useVideosBtn.dataset.bound !== '1') { useVideosBtn.dataset.bound = '1'; useVideosBtn.addEventListener('click', useSelectedTikTokVideos); }
    var clearVideosBtn = $('slClearVideosBtn');
    if (clearVideosBtn && clearVideosBtn.dataset.bound !== '1') { clearVideosBtn.dataset.bound = '1'; clearVideosBtn.addEventListener('click', clearSelectedTikTokVideos); }
    var clearSelectedBtn = $('slClearSelectedVideosBtn');
    if (clearSelectedBtn && clearSelectedBtn.dataset.bound !== '1') { clearSelectedBtn.dataset.bound = '1'; clearSelectedBtn.addEventListener('click', clearSelectedTikTokVideos); }
    var stepList = $('slStepList');
    if (stepList && stepList.dataset.bound !== '1') {
      stepList.dataset.bound = '1';
      stepList.addEventListener('click', function(evt) {
        var btn = evt.target && evt.target.closest ? evt.target.closest('[data-open-step]') : null;
        if (btn) { openStepDetail(btn.getAttribute('data-open-step')); return; }
        var row = evt.target && evt.target.closest ? evt.target.closest('[data-sl-step-key]') : null;
        if (row) openStepDetail(row.getAttribute('data-sl-step-key'));
      });
    }
    var stepModalClose = $('slStepModalClose');
    if (stepModalClose && stepModalClose.dataset.bound !== '1') { stepModalClose.dataset.bound = '1'; stepModalClose.addEventListener('click', closeStepDetail); }
    var stepModal = $('slStepModal');
    if (stepModal && stepModal.dataset.bound !== '1') {
      stepModal.dataset.bound = '1';
      stepModal.addEventListener('click', function(evt) { if (evt.target === stepModal) closeStepDetail(); });
    }
    if (!document.body.dataset.slEscBound) {
      document.body.dataset.slEscBound = '1';
      document.addEventListener('keydown', function(evt) { if (evt.key === 'Escape') closeStepDetail(); });
    }
    var runRefreshBtn = $('slRunRefreshBtn');
    if (runRefreshBtn && runRefreshBtn.dataset.bound !== '1') {
      runRefreshBtn.dataset.bound = '1';
      runRefreshBtn.addEventListener('click', function() { if (state.activeJobId) loadJobDetail(state.activeJobId); });
    }
    var expandBtn = $('slExpandKeywordsBtn');
    if (expandBtn && expandBtn.dataset.bound !== '1') { expandBtn.dataset.bound = '1'; expandBtn.addEventListener('click', expandTikTokKeywords); }
    var discoverBtn = $('slDiscoverBtn');
    if (discoverBtn && discoverBtn.dataset.bound !== '1') { discoverBtn.dataset.bound = '1'; discoverBtn.addEventListener('click', discoverTikTokCompetitors); }
    var clearMonitorBtn = $('slClearMonitorBtn');
    if (clearMonitorBtn && clearMonitorBtn.dataset.bound !== '1') {
      clearMonitorBtn.dataset.bound = '1';
      clearMonitorBtn.addEventListener('click', function() {
        state.tiktokMonitorTargets = [];
        saveTikTokMonitorTargets();
        renderTikTokMonitorTargets();
        syncTikTokAccountsInput();
        setMsg('监控同行已清空。', false);
      });
    }
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
    var openRunDetailBtn = $('slOpenRunDetailBtn');
    if (openRunDetailBtn && openRunDetailBtn.dataset.bound !== '1') { openRunDetailBtn.dataset.bound = '1'; openRunDetailBtn.addEventListener('click', function() { if (state.activeJobId) switchTab('run-detail'); }); }
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
        switchTab(state.platform === 'tiktok' ? 'keyword-search' : 'new');
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
    switchTab(state.tab || (state.platform === 'tiktok' ? 'keyword-search' : 'new'));
    loadJobs(true);
  };
})();
