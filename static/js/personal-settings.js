(function() {
  var state = {
    tab: 'template',
    keywords: [],
    competitors: [],
    competitorCandidates: [],
    memories: [],
    selectedKeywords: {},
    selectedCompetitors: {},
    selectedMemories: {},
    selectedReferenceMemories: {},
    defaultItem: null
  };

  function $(id) { return document.getElementById(id); }

  function esc(text) {
    if (typeof escapeHtml === 'function') return escapeHtml(String(text || ''));
    return String(text || '').replace(/[&<>"']/g, function(ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
  }

  function escAttr(text) {
    if (typeof escapeAttr === 'function') return escapeAttr(String(text || ''));
    return esc(text);
  }

  function cloudBase() {
    return String((typeof API_BASE !== 'undefined' && API_BASE) ? API_BASE : '').replace(/\/$/, '');
  }

  function localBase() {
    return String((typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? LOCAL_API_BASE : '').replace(/\/$/, '');
  }

  function headers(json) {
    var h = typeof authHeaders === 'function' ? Object.assign({}, authHeaders() || {}) : {};
    if (!h.Authorization && typeof token !== 'undefined' && token) h.Authorization = 'Bearer ' + token;
    if (typeof getOrCreateInstallationId === 'function') h['X-Installation-Id'] = getOrCreateInstallationId();
    if (json !== false) h['Content-Type'] = 'application/json';
    return h;
  }

  function parseErr(data, fallback) {
    if (!data) return fallback || '请求失败';
    if (typeof data === 'string') return data;
    var detail = data.detail || data.error || data.message;
    if (typeof detail === 'string') return detail;
    try { return JSON.stringify(detail || data); } catch (e) { return fallback || '请求失败'; }
  }

  function requestJson(base, path, opts) {
    opts = opts || {};
    var req = { method: opts.method || 'GET', headers: headers(opts.json !== false) };
    if (opts.body !== undefined) req.body = JSON.stringify(opts.body || {});
    return fetch(base + path, req).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok || data.ok === false) throw new Error(parseErr(data, '请求失败'));
        return data;
      });
    });
  }

  function cloudJson(path, opts) {
    var base = cloudBase();
    if (!base) return Promise.reject(new Error('未配置云端 API_BASE'));
    return requestJson(base, path, opts);
  }

  function localJson(path, opts) {
    return requestJson(localBase(), path, opts);
  }

  function setMsg(text, isErr) {
    var el = $('psMsg');
    if (!el) return;
    el.textContent = text || '';
    el.className = 'ps-msg' + (isErr ? ' err' : '');
    el.style.display = text ? 'block' : 'none';
  }

  function setBusy(btn, busy, label) {
    if (!btn) return;
    if (busy) {
      if (!btn.dataset.oldText) btn.dataset.oldText = btn.textContent || '';
      btn.textContent = label || '处理中...';
      btn.disabled = true;
    } else {
      btn.textContent = btn.dataset.oldText || btn.textContent || '';
      btn.disabled = false;
      delete btn.dataset.oldText;
    }
  }

  function switchTab(tab) {
    state.tab = tab || 'template';
    document.querySelectorAll('#content-personal-settings [data-ps-tab]').forEach(function(btn) {
      btn.classList.toggle('is-active', btn.getAttribute('data-ps-tab') === state.tab);
    });
    document.querySelectorAll('#content-personal-settings [data-ps-panel]').forEach(function(panel) {
      panel.classList.toggle('is-active', panel.getAttribute('data-ps-panel') === state.tab);
    });
  }

  function memoryId(doc) {
    return String(doc && (doc.id || doc.doc_id || doc.filename || doc.name || doc.title) || '');
  }

  function memoryTitle(doc) {
    return String(doc && (doc.title || doc.name || doc.filename || doc.id) || '未命名记忆');
  }

  function memoryFormTitle() {
    var el = $('psMemoryTitle');
    return el ? String(el.value || '').trim() : '';
  }

  function platformLabel(platform) {
    if (platform === 'wechat_channels') return '视频号';
    if (platform === 'douyin') return '抖音';
    return platform || '平台';
  }

  function fmtCount(value) {
    var n = Number(value || 0);
    if (!isFinite(n) || n <= 0) return '';
    if (n >= 10000) return (n / 10000).toFixed(n >= 100000 ? 0 : 1).replace(/\.0$/, '') + '万';
    return String(Math.round(n));
  }

  function cleanIntIds(map) {
    return Object.keys(map || {}).filter(function(id) { return !!map[id]; }).map(function(id) { return parseInt(id, 10); }).filter(Boolean);
  }

  function cleanStringIds(map) {
    return Object.keys(map || {}).filter(function(id) { return !!map[id]; }).map(function(id) { return String(id || '').trim(); }).filter(Boolean);
  }

  function selectedMemoryDocs() {
    var ids = cleanStringIds(state.selectedMemories);
    return state.memories.filter(function(doc) { return ids.indexOf(memoryId(doc)) >= 0; });
  }

  function selectedReferenceMemoryIds() {
    return cleanStringIds(state.selectedReferenceMemories);
  }

  function fetchMemoryContent(doc) {
    var id = memoryId(doc);
    if (!id) return Promise.resolve(doc);
    return localJson('/api/openclaw/memory/' + encodeURIComponent(id) + '/content', { json: false })
      .then(function(data) {
        return Object.assign({}, doc, data.document || {}, { content_text: data.content_text || '' });
      })
      .catch(function() { return doc; });
  }

  function bindOptionChecks(el, kind, selected) {
    if (!el) return;
    el.querySelectorAll('[data-ps-option="' + kind + '"]').forEach(function(input) {
      input.addEventListener('change', function() {
        selected[input.value] = input.checked;
      });
    });
  }

  function renderTemplateOptions(elId, rows, opts) {
    var el = $(elId);
    if (!el) return;
    opts = opts || {};
    if (!rows.length) {
      el.innerHTML = '<div class="ps-empty">' + esc(opts.empty || '暂无可选项') + '</div>';
      return;
    }
    el.innerHTML = rows.map(function(row) {
      var id = String(opts.id(row));
      return '<label class="ps-option">' +
        '<input type="checkbox" data-ps-option="' + escAttr(opts.kind) + '" value="' + escAttr(id) + '"' + (opts.selected[id] ? ' checked' : '') + '>' +
        '<span><strong>' + esc(opts.title(row)) + '</strong><small>' + esc(opts.subtitle(row) || '') + '</small></span>' +
      '</label>';
    }).join('');
    bindOptionChecks(el, opts.kind, opts.selected);
  }

  function renderTemplateLists() {
    renderTemplateOptions('psTemplateKeywordList', state.keywords, {
      kind: 'keyword',
      selected: state.selectedKeywords,
      empty: '暂无关键词，请到“关键词”tab 添加。',
      id: function(row) { return row.id; },
      title: function(row) { return row.display_name || row.keyword || ('关键词 #' + row.id); },
      subtitle: function(row) { return row.keyword || ''; }
    });
    renderTemplateOptions('psTemplateCompetitorList', state.competitors, {
      kind: 'competitor',
      selected: state.selectedCompetitors,
      empty: '暂无同行账号，请到“同行账号”tab 添加。',
      id: function(row) { return row.id; },
      title: function(row) { return row.display_name || row.account_key || ('同行 #' + row.id); },
      subtitle: function(row) { return platformLabel(row.platform) + ' · ' + (row.account_key || ''); }
    });
    renderTemplateOptions('psTemplateMemoryList', state.memories, {
      kind: 'memory',
      selected: state.selectedMemories,
      empty: '暂无记忆文件，请到“记忆文件”tab 上传或保存。',
      id: memoryId,
      title: memoryTitle,
      subtitle: function(row) { return row.notes || row.filename || row.id || ''; }
    });
  }

  function renderKeywords() {
    var el = $('psKeywordList');
    if (!el) return;
    if (!state.keywords.length) {
      el.innerHTML = '<div class="ps-empty">还没有关键词。</div>';
      return;
    }
    el.innerHTML = state.keywords.map(function(row) {
      var id = String(row.id || '');
      return '<article class="ps-option is-action">' +
        '<div><strong>' + esc(row.display_name || row.keyword || ('关键词 #' + id)) + '</strong>' +
        '<small>关键词：' + esc(row.keyword || '') + (row.last_fetch_at ? ' · 最近同步：' + esc(row.last_fetch_at) : '') + '</small></div>' +
        '<div class="ps-item-actions">' +
          '<button type="button" class="btn btn-primary btn-sm" data-sync-keyword="' + escAttr(id) + '">同步榜单</button>' +
          '<button type="button" class="btn btn-ghost btn-sm" data-delete-keyword="' + escAttr(id) + '">删除</button>' +
        '</div>' +
      '</article>';
    }).join('');
    el.querySelectorAll('[data-sync-keyword]').forEach(function(btn) {
      btn.addEventListener('click', function() { syncKeyword(btn.getAttribute('data-sync-keyword') || '', btn); });
    });
    el.querySelectorAll('[data-delete-keyword]').forEach(function(btn) {
      btn.addEventListener('click', function() { deleteKeyword(btn.getAttribute('data-delete-keyword') || ''); });
    });
  }

  function renderCompetitors() {
    var el = $('psCompetitorList');
    if (!el) return;
    if (!state.competitors.length) {
      el.innerHTML = '<div class="ps-empty">还没有同行账号。</div>';
      return;
    }
    el.innerHTML = state.competitors.map(function(row) {
      var id = String(row.id || '');
      return '<article class="ps-option is-action">' +
        '<div><strong>' + esc(row.display_name || row.account_key || ('同行 #' + id)) + '</strong>' +
        '<small>' + esc(platformLabel(row.platform)) + ' · ' + esc(row.account_key || '') + (row.last_fetch_at ? ' · 最近同步：' + esc(row.last_fetch_at) : '') + '</small>' +
        (row.industry_tags ? '<small>标签：' + esc(row.industry_tags) + '</small>' : '') + '</div>' +
        '<div class="ps-item-actions">' +
          '<button type="button" class="btn btn-primary btn-sm" data-sync-competitor="' + escAttr(id) + '">同步作品</button>' +
          '<button type="button" class="btn btn-ghost btn-sm" data-delete-competitor="' + escAttr(id) + '">删除</button>' +
        '</div>' +
      '</article>';
    }).join('');
    el.querySelectorAll('[data-sync-competitor]').forEach(function(btn) {
      btn.addEventListener('click', function() { syncCompetitor(btn.getAttribute('data-sync-competitor') || '', btn); });
    });
    el.querySelectorAll('[data-delete-competitor]').forEach(function(btn) {
      btn.addEventListener('click', function() { deleteCompetitor(btn.getAttribute('data-delete-competitor') || ''); });
    });
  }

  function renderMemories() {
    var el = $('psMemoryList');
    renderMemorySelectOptions();
    renderReferenceMemoryOptions();
    if (!el) return;
    if (!state.memories.length) {
      el.innerHTML = '<div class="ps-empty">还没有保存的记忆文件。</div>';
      return;
    }
    el.innerHTML = state.memories.map(function(doc) {
      var id = memoryId(doc);
      return '<article class="ps-memory-item">' +
        '<div><strong>' + esc(memoryTitle(doc)) + '</strong>' +
        '<small>' + esc((doc.notes || doc.filename || '') + (doc.created_at ? ' · ' + doc.created_at : '')) + '</small></div>' +
        '<div class="ps-actions">' +
          '<button type="button" class="btn btn-ghost btn-sm" data-preview-memory="' + escAttr(id) + '">预览</button>' +
          '<button type="button" class="btn btn-ghost btn-sm" data-delete-memory="' + escAttr(id) + '">删除</button>' +
        '</div>' +
      '</article>';
    }).join('');
    el.querySelectorAll('[data-preview-memory]').forEach(function(btn) {
      btn.addEventListener('click', function() { previewMemory(btn.getAttribute('data-preview-memory') || ''); });
    });
    el.querySelectorAll('[data-delete-memory]').forEach(function(btn) {
      btn.addEventListener('click', function() { deleteMemory(btn.getAttribute('data-delete-memory') || ''); });
    });
  }

  function renderMemorySelectOptions() {
    var select = $('psTargetMemorySelect');
    if (!select) return;
    var current = select.value || '';
    select.innerHTML = '<option value="">请选择已有文档</option>' + state.memories.map(function(doc) {
      var id = memoryId(doc);
      return '<option value="' + escAttr(id) + '">' + esc(memoryTitle(doc)) + '</option>';
    }).join('');
    if (current && state.memories.some(function(doc) { return memoryId(doc) === current; })) {
      select.value = current;
    }
    syncSaveModeState();
  }

  function renderReferenceMemoryOptions() {
    renderTemplateOptions('psReferenceMemoryList', state.memories, {
      kind: 'reference-memory',
      selected: state.selectedReferenceMemories,
      empty: '暂无记忆文件，可先在右侧保存一个记忆文件。',
      id: memoryId,
      title: memoryTitle,
      subtitle: function(row) { return row.notes || row.filename || row.id || ''; }
    });
  }

  function syncSaveModeState() {
    var mode = (($('psSaveMode') || {}).value || 'new');
    var targetSelect = $('psTargetMemorySelect');
    var titleInput = $('psMemoryTitle');
    if (targetSelect) {
      targetSelect.disabled = mode !== 'overwrite';
      if (mode !== 'overwrite') targetSelect.value = '';
    }
    if (titleInput) {
      titleInput.disabled = mode === 'overwrite';
      if (mode === 'overwrite') titleInput.value = '';
    }
    if (mode === 'overwrite' && targetSelect && !targetSelect.value) {
      setMsg('覆盖已有文档需要先选择一个文档。', true);
    }
  }

  function renderAllLists() {
    renderTemplateLists();
    renderKeywords();
    renderCompetitors();
    renderMemories();
  }

  function applyDefaultItem(item) {
    state.defaultItem = item || {};
    state.selectedKeywords = {};
    state.selectedCompetitors = {};
    state.selectedMemories = {};
    (item.keyword_ids || []).forEach(function(id) { if (id) state.selectedKeywords[String(id)] = true; });
    (item.competitor_ids || []).forEach(function(id) { if (id) state.selectedCompetitors[String(id)] = true; });
    (item.memory_doc_ids || []).forEach(function(id) { if (id) state.selectedMemories[String(id)] = true; });
    var req = item.requirements || {};
    if ($('psOralReq')) $('psOralReq').value = req.oral || req.industry_oral || req.ip_oral || '';
    if ($('psMomentsReq')) $('psMomentsReq').value = req.moments || req.moments_copy || '';
    if ($('psImageReq')) $('psImageReq').value = req.image || '';
    renderTemplateLists();
  }

  function loadKeywords() {
    return cloudJson('/api/ip-content/keywords').then(function(data) {
      state.keywords = Array.isArray(data.items) ? data.items : [];
      renderTemplateLists();
      renderKeywords();
    });
  }

  function loadCompetitors() {
    return cloudJson('/api/ip-content/competitors').then(function(data) {
      state.competitors = Array.isArray(data.items) ? data.items : [];
      renderTemplateLists();
      renderCompetitors();
    });
  }

  function loadMemories() {
    return localJson('/api/openclaw/memory/list', { json: false }).then(function(data) {
      state.memories = Array.isArray(data.documents) ? data.documents : [];
      renderTemplateLists();
      renderMemories();
    });
  }

  function loadAll() {
    setMsg('正在加载个人设置...');
    return Promise.all([
      cloudJson('/api/ip-content/keywords').then(function(data) { state.keywords = Array.isArray(data.items) ? data.items : []; }),
      cloudJson('/api/ip-content/competitors').then(function(data) { state.competitors = Array.isArray(data.items) ? data.items : []; }),
      localJson('/api/openclaw/memory/list', { json: false }).then(function(data) { state.memories = Array.isArray(data.documents) ? data.documents : []; }),
      cloudJson('/api/ip-content/personal-default').then(function(data) { state.defaultItem = data.item || {}; })
    ]).then(function() {
      applyDefaultItem(state.defaultItem || {});
      renderAllLists();
      setMsg('');
    }).catch(function(err) {
      renderAllLists();
      setMsg(err.message || '个人设置加载失败', true);
    });
  }

  function saveConfig() {
    var btn = $('psSaveConfigBtn');
    setBusy(btn, true, '保存中...');
    setMsg('正在保存模板...');
    Promise.all(selectedMemoryDocs().map(fetchMemoryContent)).then(function(memoryDocs) {
      return cloudJson('/api/ip-content/personal-default', {
        method: 'PUT',
        body: {
          name: '个人默认模板',
          keyword_ids: cleanIntIds(state.selectedKeywords),
          competitor_ids: cleanIntIds(state.selectedCompetitors),
          memory_doc_ids: cleanStringIds(state.selectedMemories),
          memory_docs: memoryDocs,
          requirements: {
            oral: (($('psOralReq') || {}).value || '').trim(),
            industry_oral: (($('psOralReq') || {}).value || '').trim(),
            ip_oral: (($('psOralReq') || {}).value || '').trim(),
            moments: (($('psMomentsReq') || {}).value || '').trim(),
            image: (($('psImageReq') || {}).value || '').trim()
          },
          meta: { source: 'personal_settings' }
        }
      });
    }).then(function(data) {
      applyDefaultItem(data.item || {});
      setMsg('模板已保存。');
    }).catch(function(err) {
      setMsg(err.message || '保存失败', true);
    }).finally(function() {
      setBusy(btn, false);
    });
  }

  function saveConfigSilently() {
    return Promise.all(selectedMemoryDocs().map(fetchMemoryContent)).then(function(memoryDocs) {
      return cloudJson('/api/ip-content/personal-default', {
        method: 'PUT',
        body: {
          name: '个人默认模板',
          keyword_ids: cleanIntIds(state.selectedKeywords),
          competitor_ids: cleanIntIds(state.selectedCompetitors),
          memory_doc_ids: cleanStringIds(state.selectedMemories),
          memory_docs: memoryDocs,
          requirements: {
            oral: (($('psOralReq') || {}).value || '').trim(),
            industry_oral: (($('psOralReq') || {}).value || '').trim(),
            ip_oral: (($('psOralReq') || {}).value || '').trim(),
            moments: (($('psMomentsReq') || {}).value || '').trim(),
            image: (($('psImageReq') || {}).value || '').trim()
          },
          meta: { source: 'personal_settings' }
        }
      });
    }).then(function(data) {
      applyDefaultItem(data.item || {});
      return data;
    });
  }

  function addKeyword() {
    var keyword = (($('psKeywordInput') || {}).value || '').trim();
    var display = (($('psKeywordDisplayName') || {}).value || '').trim();
    if (!keyword) {
      setMsg('请填写关键词。', true);
      return;
    }
    var btn = $('psAddKeywordBtn');
    setBusy(btn, true, '添加中...');
    cloudJson('/api/ip-content/keywords', {
      method: 'POST',
      body: { keyword: keyword, display_name: display, meta: { source: 'personal_settings' } }
    }).then(function(data) {
      var item = data.item || {};
      if (item.id) state.selectedKeywords[String(item.id)] = true;
      if ($('psKeywordInput')) $('psKeywordInput').value = '';
      if ($('psKeywordDisplayName')) $('psKeywordDisplayName').value = '';
      setMsg('关键词已添加。');
      return loadKeywords();
    }).catch(function(err) {
      setMsg(err.message || '关键词添加失败', true);
    }).finally(function() {
      setBusy(btn, false);
    });
  }

  function deleteKeyword(id) {
    if (!id) return;
    cloudJson('/api/ip-content/keywords/' + encodeURIComponent(id), { method: 'DELETE', json: false })
      .then(function() {
        delete state.selectedKeywords[String(id)];
        setMsg('关键词已删除。');
        return loadKeywords();
      })
      .catch(function(err) { setMsg(err.message || '关键词删除失败', true); });
  }

  function syncKeyword(id, btn) {
    if (!id) return;
    setBusy(btn, true, '同步中...');
    cloudJson('/api/ip-content/keywords/' + encodeURIComponent(id) + '/sync', {
      method: 'POST',
      body: { page_size: 20, date_window: 24 }
    }).then(function(data) {
      setMsg('关键词榜单已同步，入库 ' + ((data.items && data.items.length) || 0) + ' 条。');
      return loadKeywords();
    }).catch(function(err) {
      setMsg(err.message || '同步关键词失败', true);
    }).finally(function() {
      setBusy(btn, false);
    });
  }

  function renderCompetitorCandidates() {
    var el = $('psCompetitorSearchResults');
    if (!el) return;
    if (!state.competitorCandidates.length) {
      el.innerHTML = '';
      return;
    }
    var platform = (($('psCompetitorPlatform') || {}).value || 'douyin');
    el.innerHTML = state.competitorCandidates.map(function(item, idx) {
      var bits = [];
      if (platform === 'wechat_channels') {
        if (item.username || item.finder_username) bits.push('username：' + (item.username || item.finder_username));
      } else if (item.unique_id) {
        bits.push('抖音号：' + item.unique_id);
      }
      var fans = fmtCount(item.follower_count);
      var works = fmtCount(item.aweme_count);
      var likes = fmtCount(item.like_count);
      if (fans) bits.push('粉丝：' + fans);
      if (works) bits.push('作品：' + works);
      if (likes) bits.push('获赞：' + likes);
      if (item.verify_info) bits.push(item.verify_info);
      var title = item.display_name || item.nickname || item.unique_id || item.username || item.sec_user_id || platformLabel(platform);
      var avatar = item.avatar_url
        ? '<img src="' + escAttr(item.avatar_url) + '" alt="">'
        : '<div class="ps-user-avatar">' + esc(String(title || platformLabel(platform)).slice(0, 1)) + '</div>';
      return '<article class="ps-user-card">' +
        avatar +
        '<div><strong>' + esc(title) + '</strong>' +
        (bits.length ? '<small>' + esc(bits.join(' · ')) + '</small>' : '') +
        (item.signature ? '<small>' + esc(item.signature) + '</small>' : '') +
        '</div>' +
        '<button type="button" class="btn btn-primary btn-sm" data-add-competitor-candidate="' + escAttr(idx) + '">添加</button>' +
      '</article>';
    }).join('');
    el.querySelectorAll('[data-add-competitor-candidate]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var idx = Number(btn.getAttribute('data-add-competitor-candidate'));
        addCompetitorFromCandidate(state.competitorCandidates[idx], btn);
      });
    });
  }

  function updateCompetitorPlatformFields() {
    var platform = (($('psCompetitorPlatform') || {}).value || 'douyin');
    var isWechatChannels = platform === 'wechat_channels';
    var label = document.querySelector('#content-personal-settings label[for="psCompetitorSearchInput"]');
    var input = $('psCompetitorSearchInput');
    if (label) label.textContent = isWechatChannels ? '昵称或 username' : '昵称或抖音号';
    if (input) input.placeholder = isWechatChannels ? '输入视频号昵称或 username' : '输入昵称或抖音号';
    state.competitorCandidates = [];
    renderCompetitorCandidates();
  }

  function searchCompetitors() {
    var keyword = (($('psCompetitorSearchInput') || {}).value || '').trim();
    var platform = (($('psCompetitorPlatform') || {}).value || 'douyin');
    var isWechatChannels = platform === 'wechat_channels';
    if (!keyword) {
      setMsg(isWechatChannels ? '请先输入视频号昵称或 username。' : '请先输入同行昵称或抖音号。', true);
      return;
    }
    var btn = $('psSearchCompetitorBtn');
    var resultBox = $('psCompetitorSearchResults');
    setBusy(btn, true, '搜索中...');
    if (resultBox) resultBox.innerHTML = '<div class="ps-empty">正在搜索' + esc(platformLabel(platform)) + '账号...</div>';
    var url = isWechatChannels
      ? '/api/ip-content/wechat-channels/users/search?q=' + encodeURIComponent(keyword)
      : '/api/ip-content/douyin/users/search?q=' + encodeURIComponent(keyword);
    cloudJson(url)
      .then(function(data) {
        state.competitorCandidates = Array.isArray(data.items) ? data.items : [];
        if (!state.competitorCandidates.length) {
          if (resultBox) resultBox.innerHTML = '<div class="ps-empty">没有搜到匹配账号，请换昵称或账号再试。</div>';
          setMsg('没有搜到匹配账号。', true);
          return;
        }
        renderCompetitorCandidates();
        setMsg('搜到 ' + state.competitorCandidates.length + ' 个账号，请选择后添加。');
      })
      .catch(function(err) {
        state.competitorCandidates = [];
        if (resultBox) resultBox.innerHTML = '<div class="ps-empty">' + esc(err.message || '搜索失败') + '</div>';
        setMsg(err.message || '搜索同行失败', true);
      })
      .finally(function() { setBusy(btn, false); });
  }

  function addCompetitorFromCandidate(candidate, btn) {
    var platform = (($('psCompetitorPlatform') || {}).value || 'douyin');
    var accountKey = platform === 'wechat_channels'
      ? String(candidate && (candidate.username || candidate.finder_username || candidate.id) || '').trim()
      : String(candidate && (candidate.sec_user_id || candidate.sec_uid || candidate.id) || '').trim();
    if (!candidate || !accountKey) {
      setMsg(platform === 'wechat_channels' ? '候选账号缺少 username，不能添加。' : '候选账号缺少 sec_user_id，不能添加。', true);
      return;
    }
    var payload = {
      platform: platform,
      account_key: accountKey,
      display_name: String(candidate.display_name || candidate.nickname || candidate.unique_id || '').trim(),
      homepage_url: String(candidate.homepage_url || '').trim(),
      industry_tags: (($('psCompetitorTags') || {}).value || '').trim(),
      meta: {
        source: platform === 'wechat_channels' ? 'personal_settings_wechat_channels_search' : 'personal_settings_douyin_search',
        unique_id: candidate.unique_id || '',
        username: candidate.username || candidate.finder_username || '',
        uid: candidate.uid || '',
        nickname: candidate.nickname || candidate.display_name || '',
        follower_count: candidate.follower_count || 0,
        aweme_count: candidate.aweme_count || 0,
        like_count: candidate.like_count || 0,
        signature: candidate.signature || '',
        avatar_url: candidate.avatar_url || '',
        verify_info: candidate.verify_info || ''
      }
    };
    setBusy(btn, true, '添加中...');
    cloudJson('/api/ip-content/competitors', { method: 'POST', body: payload })
      .then(function() {
        if ($('psCompetitorSearchInput')) $('psCompetitorSearchInput').value = '';
        if ($('psCompetitorTags')) $('psCompetitorTags').value = '';
        state.competitorCandidates = [];
        renderCompetitorCandidates();
        setMsg('同行账号已添加。');
        return loadCompetitors();
      })
      .catch(function(err) { setMsg(err.message || '添加同行失败', true); })
      .finally(function() { setBusy(btn, false); });
  }

  function syncCompetitor(id, btn) {
    if (!id) return;
    setBusy(btn, true, '同步中...');
    cloudJson('/api/ip-content/competitors/' + encodeURIComponent(id) + '/sync', {
      method: 'POST',
      body: { count: 20 }
    }).then(function(data) {
      setMsg('同行作品已同步，入库 ' + ((data.items && data.items.length) || 0) + ' 条。');
      return loadCompetitors();
    }).catch(function(err) {
      setMsg(err.message || '同步同行失败', true);
    }).finally(function() {
      setBusy(btn, false);
    });
  }

  function deleteCompetitor(id) {
    if (!id) return;
    cloudJson('/api/ip-content/competitors/' + encodeURIComponent(id), { method: 'DELETE', json: false })
      .then(function() {
        delete state.selectedCompetitors[String(id)];
        setMsg('同行账号已删除。');
        return loadCompetitors();
      })
      .catch(function(err) { setMsg(err.message || '删除同行失败', true); });
  }

  function memoryInputText() {
    var parts = [];
    var raw = (($('psRawMemoryText') || {}).value || '').trim();
    var urls = (($('psMemoryUrls') || {}).value || '').trim();
    if (raw) parts.push(raw);
    if (urls) parts.push('资料链接：\n' + urls);
    var files = $('psMemoryFiles') && $('psMemoryFiles').files ? $('psMemoryFiles').files : [];
    if (files.length) {
      parts.push('已上传文件：\n' + Array.prototype.map.call(files, function(file) { return '- ' + file.name; }).join('\n'));
    }
    return parts.join('\n\n').trim();
  }

  function generateMemoryDocs() {
    var btn = $('psGenerateMemoryBtn');
    var fd = new FormData();
    var files = $('psMemoryFiles') && $('psMemoryFiles').files ? $('psMemoryFiles').files : [];
    var raw = (($('psRawMemoryText') || {}).value || '').trim();
    Array.prototype.forEach.call(files, function(file) { fd.append('files', file); });
    fd.append('urls', (($('psMemoryUrls') || {}).value || '').trim());
    fd.append('direct_intro', raw);
    fd.append('direct_faq', '');
    fd.append('direct_scripts', '');
    fd.append('doc_type', (($('psGenerateDocType') || {}).value || 'brand_product_intro'));
    fd.append('reference_doc_ids', selectedReferenceMemoryIds().join(','));
    setBusy(btn, true, '理解中...');
    setMsg('正在理解资料并生成记忆内容...');
    fetch(localBase() + '/api/personal-settings/memory-documents/generate', {
      method: 'POST',
      headers: headers(false),
      body: fd
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok || data.ok === false) throw new Error(parseErr(data, '生成失败'));
        return data;
      });
    }).then(function(data) {
      var text = data.raw_text || '';
      if (!text && data.documents) {
        text = [
          data.documents.brand_product_intro || '',
          data.documents.product_service_faq || '',
          data.documents.short_video_scripts || ''
        ].filter(Boolean).join('\n\n---\n\n');
      }
      if ($('psMemoryReviewText')) $('psMemoryReviewText').value = text;
      setMsg('AI 理解完成，请审核后存入记忆。');
    }).catch(function(err) {
      setMsg(err.message || 'AI 理解失败', true);
    }).finally(function() {
      setBusy(btn, false);
    });
  }

  function saveRawMemory() {
    var files = $('psMemoryFiles') && $('psMemoryFiles').files ? $('psMemoryFiles').files : [];
    var raw = (($('psRawMemoryText') || {}).value || '').trim();
    var urls = (($('psMemoryUrls') || {}).value || '').trim();
    var mode = (($('psSaveMode') || {}).value || 'new');
    var title = mode === 'new' ? memoryFormTitle() : '';
    var targetDocId = (($('psTargetMemorySelect') || {}).value || '');
    if (!raw && !urls && !files.length) {
      setMsg('请上传资料、填写链接或粘贴资料后再保存。', true);
      return;
    }
    if (mode === 'new' && !title) {
      setMsg('新建文档需要填写文档名字。', true);
      return;
    }
    if (mode === 'overwrite' && !targetDocId) {
      setMsg('覆盖已有文档需要先选择一个文档。', true);
      return;
    }
    var fd = new FormData();
    Array.prototype.forEach.call(files, function(file) { fd.append('files', file); });
    fd.append('title', title);
    fd.append('notes', '个人设置直接保存的原始资料');
    fd.append('raw_text', raw);
    fd.append('urls', urls);
    fd.append('mode', mode);
    fd.append('target_doc_id', targetDocId);
    saveUploadedMemory($('psSaveRawMemoryBtn'), fd);
  }

  function saveMemory() {
    var content = (($('psMemoryReviewText') || {}).value || '').trim();
    var mode = (($('psSaveMode') || {}).value || 'new');
    var title = mode === 'new' ? memoryFormTitle() : '';
    var targetDocId = (($('psTargetMemorySelect') || {}).value || '');
    if (!content) {
      content = memoryInputText();
      if ($('psMemoryReviewText')) $('psMemoryReviewText').value = content;
    }
    if (!content) {
      setMsg('没有可保存的记忆内容。', true);
      return;
    }
    if (mode === 'new' && !title) {
      setMsg('新建文档需要填写文档名字。', true);
      return;
    }
    if (mode === 'overwrite' && !targetDocId) {
      setMsg('覆盖已有文档需要先选择一个文档。', true);
      return;
    }
    saveMemoryContent($('psSaveMemoryBtn'), title, content, '个人设置审核后保存的记忆', mode, targetDocId);
  }

  function saveMemoryContent(btn, title, content, notes, mode, targetDocId) {
    setBusy(btn, true, '保存中...');
    setMsg('正在保存到记忆...');
    localJson('/api/personal-settings/memory-documents/save-raw', {
      method: 'POST',
      body: { title: title, notes: notes, content: content, mode: mode || 'new', target_doc_id: targetDocId || '' }
    })
      .then(function(data) {
        var docs = Array.isArray(data.documents) ? data.documents : [];
        if (!docs.length && data.document) docs = [data.document];
        docs.forEach(function(doc) {
          if (doc && doc.id) state.selectedMemories[String(doc.id)] = true;
        });
        return loadMemories();
      })
      .then(saveConfigSilently)
      .then(function() {
        setMsg('已存入记忆，并写入模板选择。');
        renderTemplateLists();
      })
      .catch(function(err) {
        setMsg(err.message || '保存记忆失败', true);
      })
      .finally(function() { setBusy(btn, false); });
  }

  function saveUploadedMemory(btn, formData) {
    setBusy(btn, true, '保存中...');
    setMsg('正在保存上传资料到记忆...');
    fetch(localBase() + '/api/personal-settings/memory-documents/save-upload', {
      method: 'POST',
      headers: headers(false),
      body: formData
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok || data.ok === false) throw new Error(parseErr(data, '保存失败'));
        return data;
      });
    }).then(function(data) {
      var docs = Array.isArray(data.documents) ? data.documents : [];
      if (!docs.length && data.document) docs = [data.document];
      docs.forEach(function(doc) {
        if (doc && doc.id) state.selectedMemories[String(doc.id)] = true;
      });
      if ($('psMemoryReviewText')) $('psMemoryReviewText').value = data.content_text || memoryInputText();
      return loadMemories();
    }).then(saveConfigSilently)
      .then(function() {
        setMsg('已存入记忆，并写入模板选择。');
        renderTemplateLists();
      })
      .catch(function(err) {
        setMsg(err.message || '保存记忆失败', true);
      })
      .finally(function() { setBusy(btn, false); });
  }

  function previewMemory(id) {
    if (!id) return;
    var box = $('psMemoryPreview');
    if (box) box.textContent = '正在读取...';
    localJson('/api/personal-settings/memory-documents/' + encodeURIComponent(id) + '/preview', { json: false })
      .then(function(data) {
        if (box) box.textContent = data.content_text || '没有内容。';
      })
      .catch(function(err) {
        if (box) box.textContent = err.message || '读取失败';
      });
  }

  function deleteMemory(id) {
    if (!id) return;
    if (!window.confirm('删除这个记忆文件？')) return;
    localJson('/api/openclaw/memory/' + encodeURIComponent(id), { method: 'DELETE', json: false })
      .then(function() {
        delete state.selectedMemories[String(id)];
        delete state.selectedReferenceMemories[String(id)];
        return loadMemories();
      })
      .then(saveConfigSilently)
      .then(function() { setMsg('记忆文件已删除。'); })
      .catch(function(err) { setMsg(err.message || '删除失败', true); });
  }

  function bind() {
    document.querySelectorAll('#content-personal-settings [data-ps-tab]').forEach(function(btn) {
      btn.addEventListener('click', function() { switchTab(btn.getAttribute('data-ps-tab') || 'template'); });
    });
    if ($('psRefreshBtn')) $('psRefreshBtn').addEventListener('click', loadAll);
    if ($('psBackBtn')) $('psBackBtn').addEventListener('click', function() {
      if (typeof window.showLobsterView === 'function') window.showLobsterView('chat');
    });
    if ($('psSaveConfigBtn')) $('psSaveConfigBtn').addEventListener('click', saveConfig);
    if ($('psAddKeywordBtn')) $('psAddKeywordBtn').addEventListener('click', addKeyword);
    if ($('psCompetitorPlatform')) $('psCompetitorPlatform').addEventListener('change', updateCompetitorPlatformFields);
    if ($('psSearchCompetitorBtn')) $('psSearchCompetitorBtn').addEventListener('click', searchCompetitors);
    if ($('psCompetitorSearchInput')) {
      $('psCompetitorSearchInput').addEventListener('keydown', function(ev) {
        if (ev.key === 'Enter') {
          ev.preventDefault();
          searchCompetitors();
        }
      });
    }
    if ($('psGenerateMemoryBtn')) $('psGenerateMemoryBtn').addEventListener('click', generateMemoryDocs);
    if ($('psSaveMemoryBtn')) $('psSaveMemoryBtn').addEventListener('click', saveMemory);
    if ($('psSaveRawMemoryBtn')) $('psSaveRawMemoryBtn').addEventListener('click', saveRawMemory);
    if ($('psSaveMode')) $('psSaveMode').addEventListener('change', syncSaveModeState);
    if ($('psTargetMemorySelect')) $('psTargetMemorySelect').addEventListener('change', function() {
      syncSaveModeState();
      var id = $('psTargetMemorySelect').value || '';
      if (id) previewMemory(id);
    });
  }

  window.initPersonalSettingsView = function() {
    var root = $('content-personal-settings');
    if (!root) return;
    if (!root.dataset.bound) {
      root.dataset.bound = '1';
      bind();
    }
    updateCompetitorPlatformFields();
    loadAll();
  };
})();
