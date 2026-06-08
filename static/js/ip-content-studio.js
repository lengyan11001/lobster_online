(function() {
  var state = {
    tab: 'records',
    docs: [],
    selectedDocs: {},
    keywords: [],
    competitors: [],
    competitorCandidates: [],
    keywordSources: [],
    competitorSources: [],
    keywordSourceFilter: '',
    competitorSourceFilter: '',
    draftRecords: [],
    draftGroups: [],
    activeGroupId: '',
    activeMomentImageBatchId: '',
    latestDrafts: [],
    recordFilter: '',
    configTab: 'requirements',
    settingTemplates: []
  };

  var SETTINGS_STORAGE_KEY = 'ipContentStudio.generationSettings.v1';
  var TEMPLATES_STORAGE_KEY = 'ipContentStudio.requirementTemplates.v1';

  function $(id) {
    return document.getElementById(id);
  }

  function readStoredJson(key, fallback) {
    try {
      if (!window.localStorage) return fallback;
      var raw = window.localStorage.getItem(key);
      if (!raw) return fallback;
      var value = JSON.parse(raw);
      return value === undefined || value === null ? fallback : value;
    } catch (e) {
      return fallback;
    }
  }

  function writeStoredJson(key, value) {
    try {
      if (window.localStorage) window.localStorage.setItem(key, JSON.stringify(value));
    } catch (e) {}
  }

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

  function cssEscape(text) {
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(String(text || ''));
    return String(text || '').replace(/["\\\]]/g, '\\$&');
  }

  function cloudBase() {
    return (typeof API_BASE !== 'undefined' && API_BASE ? String(API_BASE) : '').replace(/\/$/, '');
  }

  function localBase() {
    return (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE ? String(LOCAL_API_BASE) : '').replace(/\/$/, '');
  }

  function headers(json) {
    var h = typeof authHeaders === 'function' ? Object.assign({}, authHeaders() || {}) : {};
    if (!h.Authorization && typeof token !== 'undefined' && token) h.Authorization = 'Bearer ' + token;
    if (typeof getOrCreateInstallationId === 'function') h['X-Installation-Id'] = getOrCreateInstallationId();
    if (json !== false) h['Content-Type'] = 'application/json';
    return h;
  }

  function setMsg(text, isErr) {
    var node = $('ipContentMsg');
    if (!node) return;
    node.textContent = text || '';
    node.className = 'ip-content-msg' + (isErr ? ' err' : '');
    node.style.display = text ? 'block' : 'none';
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

  function parseErr(data, fallback) {
    if (!data) return fallback || '请求失败';
    if (typeof data === 'string') return data;
    var detail = data.detail || data.error || data.message;
    if (typeof detail === 'string') return detail;
    try { return JSON.stringify(detail || data); } catch (e) { return fallback || '请求失败'; }
  }

  function cloudJson(path, opts) {
    opts = opts || {};
    var base = cloudBase();
    if (!base) return Promise.reject(new Error('未配置云端 API_BASE'));
    var req = { method: opts.method || 'GET', headers: headers(opts.json !== false) };
    if (opts.body !== undefined) req.body = JSON.stringify(opts.body || {});
    return fetch(base + path, req).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok || data.ok === false) throw new Error(parseErr(data, '请求失败'));
        return data;
      });
    });
  }

  function localJson(path, opts) {
    opts = opts || {};
    var base = localBase();
    var req = { method: opts.method || 'GET', headers: headers(opts.json !== false) };
    if (opts.body !== undefined) req.body = JSON.stringify(opts.body || {});
    return fetch(base + path, req).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok || data.ok === false) throw new Error(parseErr(data, '本机请求失败'));
        return data;
      });
    });
  }

  function switchTab(tab) {
    state.tab = tab || 'records';
    document.querySelectorAll('#content-ip-content-studio [data-ip-tab]').forEach(function(btn) {
      btn.classList.toggle('is-active', btn.getAttribute('data-ip-tab') === state.tab);
    });
    document.querySelectorAll('#content-ip-content-studio [data-ip-panel]').forEach(function(panel) {
      panel.classList.toggle('is-active', panel.getAttribute('data-ip-panel') === state.tab);
    });
  }

  function switchConfigTab(tab) {
    state.configTab = tab || 'keywords';
    document.querySelectorAll('#content-ip-content-studio [data-config-tab]').forEach(function(btn) {
      btn.classList.toggle('is-active', btn.getAttribute('data-config-tab') === state.configTab);
    });
    document.querySelectorAll('#content-ip-content-studio [data-config-panel]').forEach(function(panel) {
      panel.classList.toggle('is-active', panel.getAttribute('data-config-panel') === state.configTab);
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

  function fmtCount(value) {
    var num = Number(value || 0);
    if (!isFinite(num) || num <= 0) return '';
    if (num >= 100000000) return (num / 100000000).toFixed(num >= 1000000000 ? 1 : 2).replace(/\.0+$/, '') + '亿';
    if (num >= 10000) return (num / 10000).toFixed(num >= 100000 ? 1 : 2).replace(/\.0+$/, '') + '万';
    return String(Math.round(num));
  }

  function taskLabel(task) {
    if (task === 'industry_hot_oral') return '行业口播';
    if (task === 'professional_ip_oral') return 'IP口播';
    if (task === 'moments_candidate') return '朋友圈';
    return task || '记录';
  }

  function sourceTitle(item) {
    return item.title || item.description || item.item_key || '未命名数据';
  }

  function metricText(metrics) {
    metrics = metrics || {};
    var labels = {
      rank: '排名',
      score: '热度',
      hot_value: '热度',
      search_score: '搜索分',
      play_cnt: '播放',
      play_count: '播放',
      like_cnt: '点赞',
      digg_count: '点赞',
      comment_count: '评论',
      share_count: '分享',
      collect_count: '收藏'
    };
    var keys = ['rank', 'score', 'hot_value', 'search_score', 'play_cnt', 'play_count', 'like_cnt', 'digg_count', 'comment_count', 'share_count', 'collect_count'];
    var parts = [];
    keys.forEach(function(k) {
      if (metrics[k] !== undefined && metrics[k] !== null && metrics[k] !== '') parts.push((labels[k] || k) + ':' + metrics[k]);
    });
    return parts.join(' · ');
  }

  function sourceTypeLabel(type) {
    if (type === 'keyword_video') return '视频';
    if (type === 'billboard_search') return '热词';
    if (type === 'billboard_topic') return '话题';
    if (type === 'billboard_video') return '榜单视频';
    if (type === 'hot_search' || type === 'hot_total') return '热点';
    if (type === 'user_post') return '同行作品';
    return type || '数据';
  }

  function platformLabel(platform) {
    if (platform === 'wechat_channels') return '视频号';
    if (platform === 'douyin') return '抖音';
    return platform || '平台';
  }

  function selectedMemoryIds() {
    return Object.keys(state.selectedDocs).filter(function(id) { return !!state.selectedDocs[id]; });
  }

  function memoryDocId(doc) {
    return String(doc && (doc.id || doc.doc_id || doc.filename || doc.name) || '');
  }

  function memoryDocTitle(doc) {
    var id = memoryDocId(doc);
    return (doc && (doc.title || doc.name || doc.filename)) || id || '未命名记忆';
  }

  function memorySelectionLabel() {
    var docs = selectedMemoryDocs();
    if (!docs.length) return '选择记忆文件';
    if (docs.length === 1) return memoryDocTitle(docs[0]);
    return '已选 ' + docs.length + ' 个记忆文件';
  }

  function updateMemorySelectionLabel() {
    var label = $('ipMemoryDropdownLabel');
    if (label) label.textContent = memorySelectionLabel();
  }

  function selectedMemoryDocs() {
    var ids = selectedMemoryIds();
    return state.docs.filter(function(doc) {
      var id = memoryDocId(doc);
      return ids.indexOf(id) >= 0;
    });
  }

  function fetchMemoryContent(doc) {
    var id = doc.id || doc.doc_id || doc.filename || doc.name || '';
    if (!id || doc.content || doc.content_text || doc.text) return Promise.resolve(doc);
    return localJson('/api/openclaw/memory/' + encodeURIComponent(id) + '/content', { json: false })
      .then(function(data) {
        return Object.assign({}, doc, data.document || data.item || data.doc || {}, {
          content_text: data.content_text || data.content || ''
        });
      })
      .catch(function() { return doc; });
  }

  function selectedMemoryDocsWithContent() {
    var docs = selectedMemoryDocs();
    return Promise.all(docs.map(fetchMemoryContent));
  }

  function selectedMemoryIdsForRecord(rec) {
    var map = {};
    selectedMemoryIds().forEach(function(id) {
      if (id) map[String(id)] = true;
    });
    (Array.isArray(rec && rec.memory_doc_ids) ? rec.memory_doc_ids : []).forEach(function(id) {
      if (id) map[String(id)] = true;
    });
    return Object.keys(map);
  }

  function renderMemoryList() {
    var list = $('ipMemoryList');
    if (!list) return;
    if (!state.docs.length) {
      list.innerHTML = '<div class="ip-content-empty">暂无记忆文件。</div>';
      return;
    }
    list.innerHTML = '<button type="button" id="ipMemoryDropdownBtn" class="ip-memory-select-btn">' +
      '<span id="ipMemoryDropdownLabel">' + esc(memorySelectionLabel()) + '</span><span class="ip-memory-caret">v</span>' +
      '</button><div id="ipMemoryDropdownMenu" class="ip-memory-select-menu" hidden>' +
      state.docs.map(function(doc) {
      var id = memoryDocId(doc);
      var title = memoryDocTitle(doc);
      var summary = doc.summary || doc.description || doc.path || '';
      return '<label class="ip-memory-option">' +
        '<input type="checkbox" data-memory-id="' + escAttr(id) + '"' + (state.selectedDocs[id] ? ' checked' : '') + '>' +
        '<span><strong>' + esc(title) + '</strong><small>' + esc(summary) + '</small></span>' +
        '</label>';
    }).join('') + '</div>';
    var btn = $('ipMemoryDropdownBtn');
    var menu = $('ipMemoryDropdownMenu');
    if (btn && menu) {
      btn.addEventListener('click', function(ev) {
        ev.stopPropagation();
        menu.hidden = !menu.hidden;
      });
      menu.addEventListener('click', function(ev) {
        ev.stopPropagation();
      });
    }
    list.querySelectorAll('[data-memory-id]').forEach(function(input) {
      input.addEventListener('change', function() {
        state.selectedDocs[input.getAttribute('data-memory-id')] = input.checked;
        saveGenerationSettings();
        updateMemorySelectionLabel();
      });
    });
    updateMemorySelectionLabel();
  }

  function loadMemory() {
    var list = $('ipMemoryList');
    if (list) list.innerHTML = '<div class="ip-content-empty">正在加载记忆...</div>';
    return localJson('/api/openclaw/memory/list', { json: false })
      .then(function(data) {
        state.docs = Array.isArray(data.documents) ? data.documents : (Array.isArray(data.items) ? data.items : (Array.isArray(data.docs) ? data.docs : []));
        renderMemoryList();
      })
      .catch(function(err) {
        if (list) list.innerHTML = '<div class="ip-content-empty">' + esc(err.message || '记忆加载失败') + '</div>';
      });
  }

  function generationSettingSnapshot() {
    return {
      memory_doc_ids: selectedMemoryIds(),
      task1_extra: (($('ipTask1Extra') && $('ipTask1Extra').value) || '').trim(),
      task2_extra: (($('ipTask2Extra') && $('ipTask2Extra').value) || '').trim(),
      image_extra: (($('ipImageExtra') && $('ipImageExtra').value) || '').trim()
    };
  }

  function saveGenerationSettings() {
    writeStoredJson(SETTINGS_STORAGE_KEY, generationSettingSnapshot());
  }

  function normalizeTemplates(raw) {
    if (!Array.isArray(raw)) return [];
    return raw.filter(function(item) { return item && item.id && item.name; }).map(function(item) {
      return {
        id: String(item.id),
        name: String(item.name || ''),
        memory_doc_ids: Array.isArray(item.memory_doc_ids) ? item.memory_doc_ids.map(String) : (Array.isArray(item.memoryIds) ? item.memoryIds.map(String) : []),
        task1_extra: String(item.task1_extra || item.task1Extra || ''),
        task2_extra: String(item.task2_extra || item.task2Extra || ''),
        image_extra: String(item.image_extra || item.imageExtra || ''),
        updated_at: item.updated_at || item.updatedAt || ''
      };
    });
  }

  function renderTemplateOptions() {
    var select = $('ipTemplateSelect');
    if (!select) return;
    var current = select.value;
    state.settingTemplates = normalizeTemplates(state.settingTemplates);
    select.innerHTML = '<option value="">选择模板</option>' + state.settingTemplates.map(function(tpl) {
      return '<option value="' + escAttr(tpl.id) + '">' + esc(tpl.name) + '</option>';
    }).join('');
    if (current && state.settingTemplates.some(function(tpl) { return tpl.id === current; })) select.value = current;
    var hasSelected = !!select.value;
    if ($('ipApplyTemplateBtn')) $('ipApplyTemplateBtn').disabled = !hasSelected;
    if ($('ipDeleteTemplateBtn')) $('ipDeleteTemplateBtn').disabled = !hasSelected;
  }

  function restoreGenerationSettings() {
    var saved = readStoredJson(SETTINGS_STORAGE_KEY, {});
    var ids = Array.isArray(saved.memory_doc_ids) ? saved.memory_doc_ids : (Array.isArray(saved.selectedDocIds) ? saved.selectedDocIds : []);
    state.selectedDocs = {};
    ids.forEach(function(id) {
      if (id) state.selectedDocs[String(id)] = true;
    });
    if ($('ipTask1Extra') && typeof saved.task1_extra === 'string') $('ipTask1Extra').value = saved.task1_extra;
    if ($('ipTask2Extra') && typeof saved.task2_extra === 'string') $('ipTask2Extra').value = saved.task2_extra;
    if ($('ipImageExtra') && typeof saved.image_extra === 'string') $('ipImageExtra').value = saved.image_extra;
    state.settingTemplates = normalizeTemplates(readStoredJson(TEMPLATES_STORAGE_KEY, []));
    renderTemplateOptions();
  }

  function applyTemplate(tpl) {
    if (!tpl) return;
    if ($('ipTask1Extra')) $('ipTask1Extra').value = tpl.task1_extra || '';
    if ($('ipTask2Extra')) $('ipTask2Extra').value = tpl.task2_extra || '';
    if ($('ipImageExtra')) $('ipImageExtra').value = tpl.image_extra || '';
    saveGenerationSettings();
  }

  function applySelectedTemplate() {
    var select = $('ipTemplateSelect');
    var id = select && select.value;
    var tpl = state.settingTemplates.find(function(item) { return item.id === id; });
    if (!tpl) {
      setMsg('请选择要应用的模板。', true);
      return;
    }
    applyTemplate(tpl);
    setMsg('已应用模板：' + tpl.name);
  }

  function saveCurrentTemplate() {
    var name = window.prompt('模板名称', '');
    name = (name || '').trim();
    if (!name) return;
    var snapshot = generationSettingSnapshot();
    var existingIndex = state.settingTemplates.findIndex(function(tpl) { return tpl.name === name; });
    var tpl = {
      id: existingIndex >= 0 ? state.settingTemplates[existingIndex].id : ('tpl_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8)),
      name: name,
      task1_extra: snapshot.task1_extra,
      task2_extra: snapshot.task2_extra,
      image_extra: snapshot.image_extra,
      updated_at: new Date().toISOString()
    };
    if (existingIndex >= 0) state.settingTemplates.splice(existingIndex, 1, tpl);
    else state.settingTemplates.unshift(tpl);
    writeStoredJson(TEMPLATES_STORAGE_KEY, state.settingTemplates);
    saveGenerationSettings();
    renderTemplateOptions();
    if ($('ipTemplateSelect')) $('ipTemplateSelect').value = tpl.id;
    renderTemplateOptions();
    setMsg('模板已保存：' + name);
  }

  function deleteSelectedTemplate() {
    var select = $('ipTemplateSelect');
    var id = select && select.value;
    var tpl = state.settingTemplates.find(function(item) { return item.id === id; });
    if (!tpl) {
      setMsg('请选择要删除的模板。', true);
      return;
    }
    if (!window.confirm('删除模板“' + tpl.name + '”？')) return;
    state.settingTemplates = state.settingTemplates.filter(function(item) { return item.id !== id; });
    writeStoredJson(TEMPLATES_STORAGE_KEY, state.settingTemplates);
    renderTemplateOptions();
    setMsg('模板已删除。');
  }

  function renderKeywords() {
    var list = $('ipKeywordList');
    if (!list) return;
    if (!state.keywords.length) {
      list.innerHTML = '<div class="ip-content-empty">先添加行业关键词，行业热门口播会按这些关键词同步抖音榜单。</div>';
      return;
    }
    list.innerHTML = state.keywords.map(function(item) {
      return '<div class="ip-content-item">' +
        '<strong>' + esc(item.display_name || item.keyword) + '</strong>' +
        '<small>关键词：' + esc(item.keyword) + (item.last_fetch_at ? ' · 最近同步：' + esc(fmtTime(item.last_fetch_at)) : '') + '</small>' +
        '<div class="ip-content-item-actions">' +
        '<button type="button" class="btn btn-primary btn-sm" data-sync-keyword="' + escAttr(item.id) + '">同步榜单</button>' +
        '<button type="button" class="btn btn-ghost btn-sm" data-delete-keyword="' + escAttr(item.id) + '">删除</button>' +
        '</div></div>';
    }).join('');
    list.querySelectorAll('[data-sync-keyword]').forEach(function(btn) {
      btn.addEventListener('click', function() { syncKeyword(btn.getAttribute('data-sync-keyword'), btn); });
    });
    list.querySelectorAll('[data-delete-keyword]').forEach(function(btn) {
      btn.addEventListener('click', function() { deleteKeyword(btn.getAttribute('data-delete-keyword')); });
    });
  }

  function loadKeywords() {
    return cloudJson('/api/ip-content/keywords')
      .then(function(data) {
        state.keywords = Array.isArray(data.items) ? data.items : [];
        renderKeywords();
      })
      .catch(function(err) {
        var list = $('ipKeywordList');
        if (list) list.innerHTML = '<div class="ip-content-empty">' + esc(err.message || '关键词加载失败') + '</div>';
      });
  }

  function addKeyword() {
    var keyword = ($('ipKeywordInput') && $('ipKeywordInput').value || '').trim();
    var display = ($('ipKeywordDisplayName') && $('ipKeywordDisplayName').value || '').trim();
    if (!keyword) {
      setMsg('请填写关键词。', true);
      return;
    }
    var btn = $('ipAddKeywordBtn');
    setBusy(btn, true, '添加中...');
    cloudJson('/api/ip-content/keywords', { method: 'POST', body: { keyword: keyword, display_name: display } })
      .then(function() {
        if ($('ipKeywordInput')) $('ipKeywordInput').value = '';
        if ($('ipKeywordDisplayName')) $('ipKeywordDisplayName').value = '';
        setMsg('关键词已添加。');
        return loadKeywords();
      })
      .catch(function(err) { setMsg(err.message || '关键词添加失败', true); })
      .finally(function() { setBusy(btn, false); });
  }

  function deleteKeyword(id) {
    cloudJson('/api/ip-content/keywords/' + encodeURIComponent(id), { method: 'DELETE', json: false })
      .then(function() {
        setMsg('关键词已删除。');
        return loadKeywords();
      })
      .catch(function(err) { setMsg(err.message || '关键词删除失败', true); });
  }

  function syncKeyword(id, btn) {
    setBusy(btn, true, '同步中...');
    cloudJson('/api/ip-content/keywords/' + encodeURIComponent(id) + '/sync', {
      method: 'POST',
      body: { page_size: 20, date_window: 24 }
    })
      .then(function(data) {
        var videoStatus = data.video_detail_status || {};
        var sourceTip = videoStatus.error_message ? '视频详情暂未取到，已回退热词榜：' + videoStatus.error_message : '视频详情已同步';
        setMsg(sourceTip + '，入库 ' + ((data.items && data.items.length) || 0) + ' 条。');
        return Promise.all([loadKeywords(), loadSources()]);
      })
      .catch(function(err) { setMsg(err.message || '同步失败', true); })
      .finally(function() { setBusy(btn, false); });
  }

  function renderCompetitors() {
    var list = $('ipCompetitorList');
    if (!list) return;
    if (!state.competitors.length) {
      list.innerHTML = '<div class="ip-content-empty">添加同行账号后，可同步查看他的最新作品。</div>';
      return;
    }
    list.innerHTML = state.competitors.map(function(item) {
      return '<div class="ip-content-item">' +
        '<strong>' + esc(item.display_name || item.account_key) + '</strong>' +
        '<small>' + esc(platformLabel(item.platform || 'douyin')) + ' · ' + esc(item.account_key) + (item.last_fetch_at ? ' · 最近同步：' + esc(fmtTime(item.last_fetch_at)) : '') + '</small>' +
        (item.industry_tags ? '<small>标签：' + esc(item.industry_tags) + '</small>' : '') +
        '<div class="ip-content-item-actions">' +
        '<button type="button" class="btn btn-primary btn-sm" data-sync-competitor="' + escAttr(item.id) + '">同步作品</button>' +
        '<button type="button" class="btn btn-ghost btn-sm" data-view-competitor="' + escAttr(item.id) + '">查看作品</button>' +
        '<button type="button" class="btn btn-ghost btn-sm" data-delete-competitor="' + escAttr(item.id) + '">删除</button>' +
        '</div></div>';
    }).join('');
    list.querySelectorAll('[data-sync-competitor]').forEach(function(btn) {
      btn.addEventListener('click', function() { syncCompetitor(btn.getAttribute('data-sync-competitor'), btn); });
    });
    list.querySelectorAll('[data-view-competitor]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        switchTab('synced');
        loadSources({ competitorId: btn.getAttribute('data-view-competitor') });
      });
    });
    list.querySelectorAll('[data-delete-competitor]').forEach(function(btn) {
      btn.addEventListener('click', function() { deleteCompetitor(btn.getAttribute('data-delete-competitor')); });
    });
  }

  function loadCompetitors() {
    return cloudJson('/api/ip-content/competitors')
      .then(function(data) {
        state.competitors = Array.isArray(data.items) ? data.items : [];
        renderCompetitors();
      })
      .catch(function(err) {
        var list = $('ipCompetitorList');
        if (list) list.innerHTML = '<div class="ip-content-empty">' + esc(err.message || '同行账号加载失败') + '</div>';
      });
  }

  function recordGroupId(rec) {
    var meta = rec && rec.meta ? rec.meta : {};
    return String(meta.group_id || rec.group_id || rec.record_id || '');
  }

  function setRecordFilter(filter) {
    state.recordFilter = filter || '';
    document.querySelectorAll('#content-ip-content-studio [data-ip-record-filter]').forEach(function(btn) {
      btn.classList.toggle('is-active', (btn.getAttribute('data-ip-record-filter') || '') === state.recordFilter);
    });
    renderDraftRecords();
  }

  function buildDraftGroups(records) {
    var map = {};
    (records || []).forEach(function(rec) {
      var gid = recordGroupId(rec);
      if (!gid) return;
      if (!map[gid]) {
        map[gid] = {
          group_id: gid,
          task: rec.task || '',
          platform: rec.platform || '',
          created_at: rec.created_at || '',
          records: [],
          image_count: 0
        };
      }
      map[gid].records.push(rec);
      if (rec.image_url) map[gid].image_count += 1;
      if (rec.created_at && String(rec.created_at) > String(map[gid].created_at || '')) map[gid].created_at = rec.created_at;
    });
    return Object.keys(map).map(function(k) { return map[k]; }).sort(function(a, b) {
      return String(b.created_at || '').localeCompare(String(a.created_at || ''));
    });
  }

  function recordImages(rec) {
    if (Array.isArray(rec.images) && rec.images.length) return rec.images;
    var meta = rec && rec.meta ? rec.meta : {};
    if (Array.isArray(meta.images) && meta.images.length) return meta.images;
    if (rec && rec.image_url) return [{ image_url: rec.image_url, image_asset_id: rec.image_asset_id || '', image_prompt: rec.image_prompt || '', index: 1 }];
    return [];
  }

  function storedMomentImageBatchId(rec) {
    var meta = rec && rec.meta ? rec.meta : {};
    var imageUpdate = meta && meta.image_update ? meta.image_update : {};
    return String(meta.image_batch_id || imageUpdate.image_batch_id || rec.image_batch_id || '');
  }

  function momentRecordStatus(rec) {
    var meta = rec && rec.meta ? rec.meta : {};
    var imageUpdate = meta && meta.image_update ? meta.image_update : {};
    var status = rec._image_status || meta.image_status || imageUpdate.image_status || '';
    if (status) return String(status);
    var images = recordImages(rec);
    if (images.length >= 3) return '已完成';
    if (images.length > 0) return '生成中 ' + images.length + '/3';
    return storedMomentImageBatchId(rec) ? '等待生成' : '';
  }

  function momentRecordProgress(rec) {
    var meta = rec && rec.meta ? rec.meta : {};
    var imageUpdate = meta && meta.image_update ? meta.image_update : {};
    var progress = rec._image_progress || meta.image_progress || imageUpdate.image_progress || '';
    if (progress) return String(progress);
    var count = recordImages(rec).length;
    if (count) return count + '/3';
    return storedMomentImageBatchId(rec) ? '0/3' : '';
  }

  function momentRecordFailed(rec) {
    var status = momentRecordStatus(rec).toLowerCase();
    return status.indexOf('失败') >= 0 || status.indexOf('failed') >= 0 || status.indexOf('error') >= 0;
  }

  function momentRecordDone(rec) {
    return recordImages(rec).length >= 3 || momentRecordStatus(rec).indexOf('已完成') >= 0;
  }

  function attachMomentImageBatch(records, batchId, batchCreatedAt) {
    records.forEach(function(rec, idx) {
      rec.meta = Object.assign({}, rec.meta || {}, {
        image_batch_id: batchId,
        image_batch_created_at: batchCreatedAt,
        image_status: idx === 0 ? '准备生成' : '等待生成',
        image_progress: '0/3'
      });
      rec._image_status = idx === 0 ? '准备生成' : '等待生成';
      rec._image_progress = '0/3';
      rec.images = recordImages(rec);
    });
  }

  function syncMomentBatchRecords(records) {
    var byId = {};
    records.forEach(function(rec) {
      if (rec && rec.record_id) byId[String(rec.record_id)] = rec;
    });
    state.draftRecords = (state.draftRecords || []).map(function(rec) {
      return byId[String(rec.record_id)] || rec;
    });
    records.forEach(function(rec) {
      if (!(state.draftRecords || []).some(function(item) { return String(item.record_id) === String(rec.record_id); })) {
        state.draftRecords.unshift(rec);
      }
    });
    state.draftGroups = buildDraftGroups(state.draftRecords);
  }

  function refreshMomentBatchProgress(records) {
    syncMomentBatchRecords(records);
    renderDraftRecords();
    renderMomentImageRecords();
  }

  function persistMomentRecordProgress(rec, images, batchId, batchCreatedAt) {
    if (!rec || !rec.record_id) return Promise.resolve();
    images = Array.isArray(images) ? images : recordImages(rec);
    var first = images[0] || {};
    return cloudJson('/api/ip-content/draft-records/' + encodeURIComponent(rec.record_id) + '/image', {
      method: 'POST',
      body: {
        image_url: first.image_url || rec.image_url || '',
        image_asset_id: first.image_asset_id || rec.image_asset_id || '',
        image_prompt: rec.image_prompt || '',
        selected: true,
        meta: {
          source: 'creative-film-studio',
          image_batch_id: batchId,
          image_batch_created_at: batchCreatedAt,
          image_status: rec._image_status || momentRecordStatus(rec) || '',
          image_progress: rec._image_progress || momentRecordProgress(rec) || '0/3',
          images: images
        }
      }
    }).catch(function(err) {
      console.warn('[ip-content] persist moment image progress failed', err);
    });
  }

  function momentImageRecords() {
    return (state.draftRecords || []).filter(function(rec) {
      return rec.task === 'moments_candidate' && (recordImages(rec).length || storedMomentImageBatchId(rec));
    }).sort(function(a, b) {
      return String(b.updated_at || b.created_at || '').localeCompare(String(a.updated_at || a.created_at || ''));
    });
  }

  function momentImageBatchId(rec) {
    return storedMomentImageBatchId(rec) || String(rec && rec.record_id || '');
  }

  function momentImageBatchTime(rec) {
    var meta = rec && rec.meta ? rec.meta : {};
    var imageUpdate = meta && meta.image_update ? meta.image_update : {};
    return meta.image_batch_created_at || imageUpdate.image_batch_created_at || rec.updated_at || rec.created_at || '';
  }

  function momentImageBatches() {
    var map = {};
    momentImageRecords().forEach(function(rec) {
      var bid = momentImageBatchId(rec);
      if (!bid) return;
      var time = momentImageBatchTime(rec);
      if (!map[bid]) {
        map[bid] = {
          batch_id: bid,
          created_at: time,
          records: [],
          image_count: 0,
          done_count: 0,
          failed_count: 0
        };
      }
      map[bid].records.push(rec);
      map[bid].image_count += recordImages(rec).length;
      if (momentRecordDone(rec)) map[bid].done_count += 1;
      if (momentRecordFailed(rec)) map[bid].failed_count += 1;
      if (time && String(time) > String(map[bid].created_at || '')) map[bid].created_at = time;
    });
    return Object.keys(map).map(function(k) {
      map[k].records.sort(function(a, b) {
        return String(a.created_at || '').localeCompare(String(b.created_at || ''));
      });
      return map[k];
    }).sort(function(a, b) {
      return String(b.created_at || '').localeCompare(String(a.created_at || ''));
    });
  }

  function renderCompetitorCandidates() {
    var list = $('ipCompetitorSearchResults');
    if (!list) return;
    if (!state.competitorCandidates.length) {
      list.innerHTML = '';
      return;
    }
    var platform = (($('ipCompetitorPlatform') && $('ipCompetitorPlatform').value) || 'douyin');
    list.innerHTML = state.competitorCandidates.map(function(item, idx) {
      var fans = fmtCount(item.follower_count);
      var works = fmtCount(item.aweme_count);
      var likes = fmtCount(item.like_count);
      var bits = [];
      if (platform === 'wechat_channels') {
        if (item.username || item.finder_username) bits.push('username：' + (item.username || item.finder_username));
      } else if (item.unique_id) {
        bits.push('抖音号：' + item.unique_id);
      }
      if (fans) bits.push('粉丝：' + fans);
      if (works) bits.push('作品：' + works);
      if (likes) bits.push('获赞：' + likes);
      if (item.verify_info) bits.push(item.verify_info);
      var avatar = item.avatar_url
        ? '<img src="' + escAttr(item.avatar_url) + '" alt="">'
        : '<div class="ip-user-avatar">' + esc((item.display_name || item.nickname || platformLabel(platform)).slice(0, 1)) + '</div>';
      return '<div class="ip-user-card">' +
        avatar +
        '<div><strong>' + esc(item.display_name || item.nickname || item.unique_id || item.sec_user_id) + '</strong>' +
        (bits.length ? '<small>' + esc(bits.join(' · ')) + '</small>' : '') +
        (item.signature ? '<small>' + esc(item.signature) + '</small>' : '') +
        '</div>' +
        '<button type="button" class="btn btn-primary btn-sm" data-add-competitor-candidate="' + escAttr(idx) + '">添加</button>' +
        '</div>';
    }).join('');
    list.querySelectorAll('[data-add-competitor-candidate]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var idx = Number(btn.getAttribute('data-add-competitor-candidate'));
        addCompetitorFromCandidate(state.competitorCandidates[idx], btn);
      });
    });
  }

  function searchCompetitors() {
    var input = $('ipCompetitorSearchInput');
    var keyword = ((input && input.value) || '').trim();
    var platform = (($('ipCompetitorPlatform') && $('ipCompetitorPlatform').value) || 'douyin');
    var isWechatChannels = platform === 'wechat_channels';
    if (!keyword) {
      setMsg(isWechatChannels ? '请先输入视频号昵称或 username。' : '请先输入同行昵称或抖音号。', true);
      return;
    }
    var btn = $('ipSearchCompetitorBtn');
    setBusy(btn, true, '搜索中...');
    var resultList = $('ipCompetitorSearchResults');
    if (resultList) resultList.innerHTML = '<div class="ip-content-empty">正在搜索' + esc(platformLabel(platform)) + '账号...</div>';
    var url = isWechatChannels
      ? '/api/ip-content/wechat-channels/users/search?q=' + encodeURIComponent(keyword)
      : '/api/ip-content/douyin/users/search?q=' + encodeURIComponent(keyword);
    cloudJson(url)
      .then(function(data) {
        state.competitorCandidates = Array.isArray(data.items) ? data.items : [];
        if (!state.competitorCandidates.length) {
          if (resultList) resultList.innerHTML = '<div class="ip-content-empty">' + esc(isWechatChannels ? '没有搜到匹配账号，请换昵称或 username 再试。' : '没有搜到匹配账号，请换昵称或抖音号再试。') + '</div>';
          setMsg('没有搜到匹配账号。', true);
          return;
        }
        renderCompetitorCandidates();
        setMsg('搜到 ' + state.competitorCandidates.length + ' 个账号，请选择后添加。');
      })
      .catch(function(err) {
        state.competitorCandidates = [];
        if (resultList) resultList.innerHTML = '<div class="ip-content-empty">' + esc(err.message || '搜索失败') + '</div>';
        setMsg(err.message || '搜索同行失败', true);
      })
      .finally(function() { setBusy(btn, false); });
  }

  function updateCompetitorPlatformFields() {
    var platform = (($('ipCompetitorPlatform') && $('ipCompetitorPlatform').value) || 'douyin');
    var isWechatChannels = platform === 'wechat_channels';
    var label = document.querySelector('label[for="ipCompetitorSearchInput"]');
    var input = $('ipCompetitorSearchInput');
    var btn = $('ipSearchCompetitorBtn');
    if (label) label.textContent = isWechatChannels ? '昵称或 username' : '昵称或抖音号';
    if (input) input.placeholder = isWechatChannels ? '输入视频号昵称或 username' : '输入昵称或抖音号';
    if (btn) btn.textContent = '搜索账号';
    state.competitorCandidates = [];
    renderCompetitorCandidates();
  }

  function addCompetitorFromCandidate(candidate, btn) {
    var platform = (($('ipCompetitorPlatform') && $('ipCompetitorPlatform').value) || 'douyin');
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
      industry_tags: (($('ipCompetitorTags') && $('ipCompetitorTags').value) || '').trim(),
      meta: {
        source: platform === 'wechat_channels' ? 'wechat_channels_user_search' : 'douyin_user_search',
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
    if (!payload.account_key) {
      setMsg(platform === 'wechat_channels' ? '候选账号缺少 username，不能添加。' : '候选账号缺少 sec_user_id，不能添加。', true);
      return;
    }
    setBusy(btn, true, '添加中...');
    cloudJson('/api/ip-content/competitors', { method: 'POST', body: payload })
      .then(function() {
        ['ipCompetitorSearchInput', 'ipCompetitorTags'].forEach(function(id) { if ($(id)) $(id).value = ''; });
        state.competitorCandidates = [];
        renderCompetitorCandidates();
        setMsg('同行账号已添加。');
        return loadCompetitors();
      })
      .catch(function(err) { setMsg(err.message || '添加同行失败', true); })
      .finally(function() { setBusy(btn, false); });
  }

  function syncCompetitor(id, btn) {
    setBusy(btn, true, '同步中...');
    cloudJson('/api/ip-content/competitors/' + encodeURIComponent(id) + '/sync', {
      method: 'POST',
      body: { count: 20 }
    })
      .then(function(data) {
        setMsg('同行作品已同步，入库 ' + ((data.items && data.items.length) || 0) + ' 条。');
        return Promise.all([loadCompetitors(), loadSources({ competitorId: id })]);
      })
      .catch(function(err) { setMsg(err.message || '同步同行失败', true); })
      .finally(function() { setBusy(btn, false); });
  }

  function deleteCompetitor(id) {
    cloudJson('/api/ip-content/competitors/' + encodeURIComponent(id), { method: 'DELETE', json: false })
      .then(function() {
        setMsg('同行账号已删除。');
        return loadCompetitors();
      })
      .catch(function(err) { setMsg(err.message || '删除同行失败', true); });
  }

  function sourceKeywordValue(item) {
    var meta = item && item.source_meta ? item.source_meta : {};
    return String(meta.keyword || meta.display_name || '').trim();
  }

  function sourceCompetitorId(item) {
    var meta = item && item.source_meta ? item.source_meta : {};
    return String(meta.competitor_account_id || '').trim();
  }

  function sourceCompetitorName(item) {
    var meta = item && item.source_meta ? item.source_meta : {};
    return String(meta.competitor_name || item.author_name || item.author_key || '').trim();
  }

  function renderSourceFilter(selectId, options, current, allLabel) {
    var select = $(selectId);
    if (!select) return;
    var seen = {};
    var normalized = [];
    (options || []).forEach(function(opt) {
      var value = String(opt.value || '').trim();
      var label = String(opt.label || value).trim();
      if (!value || seen[value]) return;
      seen[value] = true;
      normalized.push({ value: value, label: label || value });
    });
    select.innerHTML = '<option value="">' + esc(allLabel) + '</option>' + normalized.map(function(opt) {
      return '<option value="' + escAttr(opt.value) + '">' + esc(opt.label) + '</option>';
    }).join('');
    if (current && seen[current]) select.value = current;
    else select.value = '';
  }

  function renderSourceFilters() {
    var keywordOptions = state.keywordSources.map(function(item) {
      var value = sourceKeywordValue(item);
      return value ? { value: value, label: value } : null;
    }).filter(Boolean);
    var competitorOptions = state.competitorSources.map(function(item) {
      var id = sourceCompetitorId(item);
      var name = sourceCompetitorName(item);
      return id ? { value: id, label: name || id } : null;
    }).filter(Boolean);
    renderSourceFilter('ipKeywordSourceFilter', keywordOptions, state.keywordSourceFilter, '全部关键词');
    renderSourceFilter('ipCompetitorSourceFilter', competitorOptions, state.competitorSourceFilter, '全部同行');
    state.keywordSourceFilter = ($('ipKeywordSourceFilter') && $('ipKeywordSourceFilter').value) || '';
    state.competitorSourceFilter = ($('ipCompetitorSourceFilter') && $('ipCompetitorSourceFilter').value) || '';
  }

  function currentSourceFilter(extra) {
    extra = extra || {};
    return {
      keyword: state.keywordSourceFilter || '',
      competitorId: extra.competitorId || state.competitorSourceFilter || ''
    };
  }

  function renderSourceList(listId, items, type, filter) {
    var list = $(listId);
    if (!list) return;
    var rows = items || [];
    if (type === 'keyword' && filter && filter.keyword) {
      rows = rows.filter(function(item) {
        return sourceKeywordValue(item) === String(filter.keyword);
      });
    }
    if (type === 'competitor' && filter && filter.competitorId) {
      rows = rows.filter(function(item) {
        return String((item.source_meta || {}).competitor_account_id || '') === String(filter.competitorId);
      });
    }
    if (!rows.length) {
      list.innerHTML = '<div class="ip-content-empty">' + (type === 'keyword' ? '暂无关键词榜单数据。' : '暂无同行作品数据。') + '</div>';
      return;
    }
    list.innerHTML = rows.map(function(item) {
      var meta = item.source_meta || {};
      var badges = '';
      if (item.is_new) badges += '<span class="ip-badge is-new">新</span>';
      if (item.is_used) badges += '<span class="ip-badge is-used">已用</span>';
      if (item.source_type) badges += '<span class="ip-badge">' + esc(sourceTypeLabel(item.source_type)) + '</span>';
      if (type === 'keyword' && meta.keyword) badges += '<span class="ip-badge">' + esc(meta.keyword) + '</span>';
      if (type === 'competitor' && meta.competitor_name) badges += '<span class="ip-badge">' + esc(meta.competitor_name) + '</span>';
      var metrics = metricText(item.metrics);
      var desc = item.description && item.description !== item.title ? item.description : '';
      return '<div class="ip-content-item">' +
        '<div class="ip-badge-row">' + badges + '</div>' +
        '<strong>' + esc(sourceTitle(item)) + '</strong>' +
        (desc ? '<small>' + esc(desc.slice(0, 180)) + '</small>' : '') +
        '<small>' + esc(item.author_name || item.author_key || '') + (item.publish_time ? ' · ' + esc(item.publish_time) : '') + '</small>' +
        (metrics ? '<small>' + esc(metrics) + '</small>' : '') +
        (item.used_for && item.used_for.length ? '<small>使用记录：' + esc(item.used_for.map(taskLabel).join('、')) + '</small>' : '') +
        '<div class="ip-content-item-actions">' +
        (item.public_url ? '<a class="btn btn-ghost btn-sm" href="' + escAttr(item.public_url) + '" target="_blank" rel="noopener">打开</a>' : '') +
        '</div></div>';
    }).join('');
  }

  function loadSources(filter) {
    filter = filter || {};
    if (filter.competitorId) state.competitorSourceFilter = String(filter.competitorId);
    if (filter.keyword) state.keywordSourceFilter = String(filter.keyword);
    var keywordUrl = '/api/ip-content/source-items?platform=douyin&source_type=keyword&limit=120';
    var competitorUrl = '/api/ip-content/source-items?source_type=user_post&limit=120';
    return Promise.all([
      cloudJson(keywordUrl).then(function(data) { state.keywordSources = data.items || []; }),
      cloudJson(competitorUrl).then(function(data) { state.competitorSources = data.items || []; })
    ]).then(function() {
      renderSourceFilters();
      var activeFilter = currentSourceFilter(filter);
      renderSourceList('ipKeywordSourceList', state.keywordSources, 'keyword', activeFilter);
      renderSourceList('ipCompetitorSourceList', state.competitorSources, 'competitor', activeFilter);
    }).catch(function(err) {
      renderSourceList('ipKeywordSourceList', [], 'keyword');
      renderSourceList('ipCompetitorSourceList', [], 'competitor');
      setMsg(err.message || '同步数据加载失败', true);
    });
  }

  function renderDraftCards(targetId, records, opts) {
    opts = opts || {};
    var box = $(targetId);
    if (!box) return;
    if (!records || !records.length) {
      box.innerHTML = '<div class="ip-content-empty">暂无内容。</div>';
      return;
    }
    var selectable = !!opts.selectable;
    box.innerHTML = records.map(function(rec) {
      var checked = selectable && rec._selected ? ' checked' : '';
      var images = recordImages(rec);
      var image = images.length
        ? '<div class="ip-image-grid">' + images.slice(0, 3).map(function(img, idx) {
            var url = img.image_url || img.url || '';
            return '<div class="ip-image-tile"><img src="' + escAttr(url) + '" alt="生成图片 ' + escAttr(idx + 1) + '">' +
              '<a class="btn btn-ghost btn-sm" href="' + escAttr(url) + '" target="_blank" rel="noopener">打开图片</a></div>';
          }).join('') + '</div>'
        : '<div class="ip-image-preview" data-image-preview="' + escAttr(rec.record_id || '') + '">' + (rec._image_status ? '<small>' + esc(rec._image_status) + '</small>' : '') + '</div>';
      return '<div class="ip-draft-card" data-record-id="' + escAttr(rec.record_id || '') + '">' +
        (selectable ? '<label class="ip-badge-row"><input type="checkbox" data-moment-select="' + escAttr(rec.record_id || '') + '"' + checked + '> <span class="ip-badge">选中出图</span></label>' : '') +
        '<div class="ip-badge-row"><span class="ip-badge">' + esc(taskLabel(rec.task)) + '</span>' + (rec.image_url ? '<span class="ip-badge is-image">已出图</span>' : '') + '</div>' +
        '<strong>' + esc(rec.title || '未命名文案') + '</strong>' +
        '<textarea data-record-copy="' + escAttr(rec.record_id || '') + '">' + esc(rec.body || rec.content || '') + '</textarea>' +
        (rec.image_prompt ? '<small>配图提示：' + esc(rec.image_prompt) + '</small>' : '') +
        image +
        '<div class="ip-content-item-actions">' +
        '<button type="button" class="btn btn-ghost btn-sm" data-copy-record="' + escAttr(rec.record_id || '') + '">复制</button>' +
        '</div></div>';
    }).join('');
    box.querySelectorAll('[data-copy-record]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var id = btn.getAttribute('data-copy-record');
        var ta = box.querySelector('[data-record-copy="' + cssEscape(id) + '"]');
        var text = ta ? ta.value : '';
        copyText(text, btn);
      });
    });
    box.querySelectorAll('[data-record-copy]').forEach(function(ta) {
      ta.style.height = 'auto';
      ta.style.height = Math.min(Math.max(ta.scrollHeight + 2, 42), 260) + 'px';
      ta.addEventListener('input', function() {
        ta.style.height = 'auto';
        ta.style.height = Math.min(Math.max(ta.scrollHeight + 2, 42), 260) + 'px';
      });
    });
    box.querySelectorAll('[data-moment-select]').forEach(function(input) {
      input.addEventListener('change', function() {
        var id = input.getAttribute('data-moment-select');
        state.latestDrafts.forEach(function(rec) {
          if (String(rec.record_id) === String(id)) rec._selected = input.checked;
        });
      });
    });
  }

  function renderDraftRecords() {
    var list = $('ipDraftGroupList');
    if (!list) return;
    var groups = state.draftGroups;
    if (state.recordFilter) groups = groups.filter(function(item) { return item.task === state.recordFilter; });
    if (!groups.length) {
      list.innerHTML = '<div class="ip-content-empty">暂无文案生成记录。</div>';
      state.latestDrafts = [];
      renderGroupDetail(null);
      return;
    }
    if (!state.activeGroupId || !groups.some(function(g) { return g.group_id === state.activeGroupId; })) {
      state.activeGroupId = groups[0].group_id;
    }
    list.innerHTML = groups.map(function(group) {
      var first = group.records[0] || {};
      var preview = (first.body || first.content || '').slice(0, 120);
      return '<div class="ip-content-item' + (group.group_id === state.activeGroupId ? ' is-active' : '') + '" data-show-group="' + escAttr(group.group_id) + '">' +
        '<div class="ip-badge-row"><span class="ip-badge">' + esc(taskLabel(group.task)) + '</span>' +
        (group.image_count ? '<span class="ip-badge is-image">图片 ' + esc(group.image_count) + '</span>' : '') +
        '</div>' +
        '<strong>' + esc(taskLabel(group.task)) + ' · ' + esc(group.records.length) + ' 条</strong>' +
        '<small>' + esc(fmtTime(group.created_at)) + '</small>' +
        (preview ? '<small>' + esc(preview) + (preview.length >= 120 ? '...' : '') + '</small>' : '') +
        '</div>';
    }).join('');
    list.querySelectorAll('[data-show-group]').forEach(function(item) {
      item.addEventListener('click', function() {
        state.activeGroupId = item.getAttribute('data-show-group') || '';
        renderDraftRecords();
      });
    });
    renderGroupDetail(groups.find(function(g) { return g.group_id === state.activeGroupId; }) || groups[0]);
  }

  function renderGroupDetail(group) {
    var title = $('ipRecordDetailTitle');
    var imageBtn = $('ipGenerateSelectedImagesBtn');
    if (!group) {
      if (title) title.textContent = '生成明细';
      if (imageBtn) imageBtn.style.display = 'none';
      renderDraftCards('ipLatestDraftList', []);
      return;
    }
    state.latestDrafts = group.records.map(function(rec) {
      if (rec._selected === undefined) rec._selected = false;
      return rec;
    });
    if (title) title.textContent = taskLabel(group.task) + ' · ' + group.records.length + ' 条';
    if (imageBtn) imageBtn.style.display = group.task === 'moments_candidate' ? '' : 'none';
    renderDraftCards('ipLatestDraftList', state.latestDrafts, { selectable: group.task === 'moments_candidate' });
  }

  function renderMomentImageRecords() {
    var list = $('ipMomentImageRecordList');
    if (!list) return;
    var batches = momentImageBatches();
    if (!batches.length) {
      list.innerHTML = '<div class="ip-content-empty">暂无朋友圈图片生成记录。</div>';
      state.activeMomentImageBatchId = '';
      renderMomentImageDetail(null);
      return;
    }
    if (!state.activeMomentImageBatchId || !batches.some(function(batch) { return batch.batch_id === state.activeMomentImageBatchId; })) {
      state.activeMomentImageBatchId = batches[0].batch_id;
    }
    list.innerHTML = batches.map(function(batch) {
      var first = batch.records[0] || {};
      var statusLabel = batch.failed_count ? '失败 ' + batch.failed_count : (batch.done_count >= batch.records.length ? '已完成' : '生成中');
      return '<div class="ip-content-item' + (batch.batch_id === state.activeMomentImageBatchId ? ' is-active' : '') + '" data-show-moment-image-batch="' + escAttr(batch.batch_id) + '">' +
        '<div class="ip-badge-row"><span class="ip-badge">朋友圈图片</span><span class="ip-badge is-image">图片 ' + esc(batch.image_count) + '</span><span class="ip-badge' + (batch.failed_count ? ' is-used' : '') + '">' + esc(statusLabel) + '</span></div>' +
        '<strong>' + esc('本轮 ' + batch.records.length + ' 条文案') + '</strong>' +
        '<small>' + esc('进度：' + batch.done_count + '/' + batch.records.length + ' 条完成') + '</small>' +
        '<small>' + esc(fmtTime(batch.created_at)) + '</small>' +
        '<small>' + esc((first.title || first.body || first.content || '').slice(0, 120)) + '</small>' +
        '</div>';
    }).join('');
    list.querySelectorAll('[data-show-moment-image-batch]').forEach(function(item) {
      item.addEventListener('click', function() {
        state.activeMomentImageBatchId = item.getAttribute('data-show-moment-image-batch') || '';
        renderMomentImageRecords();
      });
    });
    renderMomentImageDetail(batches.find(function(batch) { return batch.batch_id === state.activeMomentImageBatchId; }) || batches[0]);
  }

  function renderMomentImageDetail(batch) {
    var box = $('ipMomentImageDetail');
    if (!box) return;
    if (!batch || !batch.records || !batch.records.length) {
      box.innerHTML = '<div class="ip-content-empty">左侧选择一轮图片生成记录。</div>';
      return;
    }
    box.innerHTML = '<div class="ip-content-item">' +
      '<div class="ip-badge-row"><span class="ip-badge">本轮明细</span><span class="ip-badge">文案 ' + esc(batch.records.length) + '</span><span class="ip-badge is-image">图片 ' + esc(batch.image_count) + '</span><span class="ip-badge">完成 ' + esc(batch.done_count) + '/' + esc(batch.records.length) + '</span></div>' +
      '<small>' + esc(fmtTime(batch.created_at)) + '</small>' +
      '</div>' +
      batch.records.map(function(rec) {
        var images = recordImages(rec);
        var status = momentRecordStatus(rec);
        var progress = momentRecordProgress(rec);
        return '<div class="ip-draft-card">' +
          '<div class="ip-badge-row"><span class="ip-badge">朋友圈</span><span class="ip-badge is-image">图片 ' + esc(images.length) + '</span><span class="ip-badge' + (momentRecordFailed(rec) ? ' is-used' : '') + '">' + esc(status || '等待生成') + '</span>' + (progress ? '<span class="ip-badge">进度 ' + esc(progress) + '</span>' : '') + '</div>' +
          '<strong>' + esc(rec.title || '未命名文案') + '</strong>' +
          '<textarea readonly data-moment-image-copy="' + escAttr(rec.record_id || '') + '">' + esc(rec.body || rec.content || '') + '</textarea>' +
          (rec.image_prompt ? '<small>配图提示：' + esc(rec.image_prompt) + '</small>' : '') +
          (images.length ? '<div class="ip-image-grid">' + images.slice(0, 3).map(function(img, idx) {
            var url = img.image_url || img.url || '';
            return '<div class="ip-image-tile"><img src="' + escAttr(url) + '" alt="朋友圈图片 ' + escAttr(idx + 1) + '">' +
              '<a class="btn btn-ghost btn-sm" href="' + escAttr(url) + '" target="_blank" rel="noopener">打开图片</a></div>';
          }).join('') + '</div>' : '<div class="ip-content-empty">' + esc(status || '等待生成图片...') + '</div>') +
          '<div class="ip-content-item-actions"><button type="button" class="btn btn-ghost btn-sm" data-copy-moment-image-record="' + escAttr(rec.record_id) + '">复制文案</button></div>' +
          '</div>';
      }).join('');
    box.querySelectorAll('textarea').forEach(function(ta) {
      ta.style.height = 'auto';
      ta.style.height = Math.min(Math.max(ta.scrollHeight + 2, 42), 180) + 'px';
    });
    box.querySelectorAll('[data-copy-moment-image-record]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var id = btn.getAttribute('data-copy-moment-image-record');
        var rec = batch.records.find(function(item) { return String(item.record_id) === String(id); });
        copyText(rec ? (rec.body || rec.content || '') : '', btn);
      });
    });
  }

  function loadDraftRecords() {
    return cloudJson('/api/ip-content/draft-records?limit=120')
      .then(function(data) {
        state.draftRecords = Array.isArray(data.items) ? data.items : [];
        state.draftGroups = buildDraftGroups(state.draftRecords);
        renderDraftRecords();
        renderMomentImageRecords();
      })
      .catch(function(err) {
        var list = $('ipDraftGroupList');
        if (list) list.innerHTML = '<div class="ip-content-empty">' + esc(err.message || '文案生成记录加载失败') + '</div>';
      });
  }

  function copyText(text, btn) {
    text = text || '';
    var done = function() {
      if (!btn) return;
      var old = btn.textContent;
      btn.textContent = '已复制';
      setTimeout(function() { btn.textContent = old || '复制'; }, 1100);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(function() {
        fallbackCopy(text);
        done();
      });
    } else {
      fallbackCopy(text);
      done();
    }
  }

  function fallbackCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try { document.execCommand('copy'); } catch (e) {}
    document.body.removeChild(ta);
  }

  function generationPayload(extraId, count) {
    var extraNode = extraId ? $(extraId) : null;
    saveGenerationSettings();
    return selectedMemoryDocsWithContent().then(function(memoryDocs) {
      return {
        memory_docs: memoryDocs,
        extra_requirements: ((extraNode && extraNode.value) || '').trim(),
        count: count || 5,
        sync_before: false
      };
    });
  }

  function runGenerate(btn, endpoint, extraId, count, successTab) {
    setBusy(btn, true, '生成中...');
    setMsg('正在同步数据并生成，请稍候...');
    generationPayload(extraId, count)
      .then(function(payload) {
        return cloudJson(endpoint, { method: 'POST', body: payload });
      })
      .then(function(data) {
        var records = data.records || [];
        state.latestDrafts = records;
        if (records.length) {
          state.activeGroupId = recordGroupId(records[0]);
          state.recordFilter = records[0].task || '';
        }
        setMsg('已生成 ' + records.length + ' 条内容。');
        return Promise.all([loadDraftRecords(), loadSources()]).then(function() {
          setRecordFilter(state.recordFilter);
          switchTab(successTab || 'records');
        });
      })
      .catch(function(err) { setMsg(err.message || '生成失败', true); })
      .finally(function() { setBusy(btn, false); });
  }

  function confirmMomentsImages() {
    var selected = state.latestDrafts.filter(function(rec) { return rec.task === 'moments_candidate' && !!rec._selected && !rec.image_url; });
    if (!selected.length) {
      setMsg('请先在右侧明细勾选要出图的朋友圈文案。', true);
      return;
    }
    if (selected.length > 5) {
      setMsg('一次最多选择 5 条出图。', true);
      return;
    }
    var btn = $('ipGenerateSelectedImagesBtn');
    setBusy(btn, true, '出图中...');
    setMsg('正在为选中的朋友圈文案生成图片...');
    var batchId = 'moment_img_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8);
    var batchCreatedAt = new Date().toISOString();
    attachMomentImageBatch(selected, batchId, batchCreatedAt);
    syncMomentBatchRecords(selected);
    state.activeMomentImageBatchId = batchId;
    switchTab('moment-images');
    renderMomentImageRecords();
    Promise.all(selected.map(function(rec) {
      return persistMomentRecordProgress(rec, [], batchId, batchCreatedAt);
    })).then(function() {
      return loadDraftRecords();
    });
    var chain = Promise.resolve();
    selected.forEach(function(rec, recIdx) {
      chain = chain.then(function() {
        var preview = document.querySelector('[data-image-preview="' + cssEscape(String(rec.record_id || '')) + '"]');
        if (preview) preview.innerHTML = '<small>正在出图 0/3...</small>';
        rec._image_status = '正在出图 0/3...';
        rec._image_progress = '0/3';
        rec.meta = Object.assign({}, rec.meta || {}, { image_status: rec._image_status, image_progress: rec._image_progress });
        refreshMomentBatchProgress(selected);
        var copyTextValue = (rec.body || rec.content || '').trim();
        var originalImagePrompt = (rec.image_prompt || '').trim();
        var imageExtra = (($('ipImageExtra') && $('ipImageExtra').value) || '').trim();
        var memoryIds = selectedMemoryIdsForRecord(rec);
        saveGenerationSettings();
        var basePrompt = [
          originalImagePrompt ? '配图提示：' + originalImagePrompt : '',
          copyTextValue ? '朋友圈文案：' + copyTextValue : '',
          imageExtra ? '出图要求：' + imageExtra : '',
          '只根据以上已审核文案和配图提示生成朋友圈配图。文案里的选题可能来自行业热门或同行新内容，图片要贴合这个新内容；同时必须遵守记忆文件里的账号定位、行业事实、产品/服务特点和表达风格，不要另起主题，不要出现文字、水印、按钮或二维码。'
        ].filter(Boolean).join('\n\n');
        var images = [];
        var generateOne = function(index) {
          if (preview) preview.innerHTML = '<small>正在出图 ' + (index - 1) + '/3...</small>';
          rec._image_status = '正在出图 ' + (index - 1) + '/3...';
          rec._image_progress = (index - 1) + '/3';
          rec.meta = Object.assign({}, rec.meta || {}, { image_status: rec._image_status, image_progress: rec._image_progress, images: images });
          refreshMomentBatchProgress(selected);
          return localJson('/api/creative-film-studio/generate-image', {
            method: 'POST',
            body: {
              memory_doc_ids: memoryIds,
              title: rec.title || '朋友圈配图',
              goal: copyTextValue,
              direct_prompt: basePrompt + '\n\n图片序号：' + index + '。在主体和文案情绪一致的前提下，构图、场景或细节要和其他图片有所区别。',
              aspect_ratio: '1:1',
              image_model: 'gpt-image-2'
            }
          }).then(function(data) {
            var imageUrl = data.image_url || data.original_image_url || (data.asset && data.asset.source_url) || '';
            var assetId = data.asset_id || (data.asset && (data.asset.asset_id || data.asset.id)) || '';
            if (!imageUrl) throw new Error('图片生成完成但没有返回公网链接');
            images.push({
              image_url: imageUrl,
              image_asset_id: assetId,
              image_prompt: originalImagePrompt,
              generated_prompt: data.image_prompt || '',
              index: index,
              created_at: new Date().toISOString()
            });
            rec.images = images.slice();
            rec._image_status = index < 3 ? '正在出图 ' + index + '/3...' : '正在回写记录';
            rec._image_progress = index + '/3';
            rec.meta = Object.assign({}, rec.meta || {}, { image_status: rec._image_status, image_progress: rec._image_progress, images: rec.images });
            refreshMomentBatchProgress(selected);
            if (preview) preview.innerHTML = '<small>正在出图 ' + index + '/3...</small>';
          });
        };
        return generateOne(1).then(function() { return generateOne(2); }).then(function() { return generateOne(3); }).then(function() {
          rec.images = images;
          rec.image_url = images[0].image_url;
          rec.image_asset_id = images[0].image_asset_id;
          rec.image_prompt = originalImagePrompt;
          rec.selected = true;
          rec._selected = false;
          rec._image_status = '3 张图片已生成';
          rec._image_progress = '3/3';
          rec.meta = Object.assign({}, rec.meta || {}, {
            image_batch_id: batchId,
            image_batch_created_at: batchCreatedAt,
            image_status: rec._image_status,
            image_progress: rec._image_progress,
            images: images
          });
          refreshMomentBatchProgress(selected);
          return persistMomentRecordProgress(rec, images, batchId, batchCreatedAt).then(function() {
            return images;
          });
        }).then(function() {
          if (preview) {
            preview.innerHTML = '<div class="ip-image-grid">' + images.map(function(img, idx) {
              return '<div class="ip-image-tile"><img src="' + escAttr(img.image_url) + '" alt="朋友圈图片 ' + escAttr(idx + 1) + '">' +
                '<a class="btn btn-ghost btn-sm" href="' + escAttr(img.image_url) + '" target="_blank" rel="noopener">打开图片</a></div>';
            }).join('') + '</div>';
          }
            if (selected[recIdx + 1]) {
              selected[recIdx + 1]._image_status = '准备生成';
              selected[recIdx + 1]._image_progress = '0/3';
              selected[recIdx + 1].meta = Object.assign({}, selected[recIdx + 1].meta || {}, {
                image_status: selected[recIdx + 1]._image_status,
                image_progress: selected[recIdx + 1]._image_progress
              });
            }
            refreshMomentBatchProgress(selected);
        });
      });
    });
    chain.then(function() {
      setMsg('选中的朋友圈文案已各生成 3 张图片并回写生成记录。');
      state.activeMomentImageBatchId = batchId;
      return loadDraftRecords().then(function() { switchTab('moment-images'); });
    }).catch(function(err) {
      selected.forEach(function(rec) {
        if (!momentRecordDone(rec)) {
          rec._image_status = '生成失败：' + (err.message || '图片生成失败');
          rec.meta = Object.assign({}, rec.meta || {}, { image_status: rec._image_status, image_progress: rec._image_progress || '0/3' });
          persistMomentRecordProgress(rec, recordImages(rec), batchId, batchCreatedAt);
        }
      });
      refreshMomentBatchProgress(selected);
      setMsg(err.message || '朋友圈出图失败', true);
    }).finally(function() {
      setBusy(btn, false);
    });
  }

  function refreshAll() {
    return Promise.all([loadMemory(), loadKeywords(), loadCompetitors(), loadSources(), loadDraftRecords()]);
  }

  function bind() {
    var root = $('content-ip-content-studio');
    if (!root || root.dataset.ipContentBound === '1') return;
    root.dataset.ipContentBound = '1';
    document.querySelectorAll('#content-ip-content-studio [data-ip-tab]').forEach(function(btn) {
      btn.addEventListener('click', function() { switchTab(btn.getAttribute('data-ip-tab')); });
    });
    document.querySelectorAll('#content-ip-content-studio [data-config-tab]').forEach(function(btn) {
      btn.addEventListener('click', function() { switchConfigTab(btn.getAttribute('data-config-tab')); });
    });
    document.querySelectorAll('#content-ip-content-studio [data-ip-record-filter]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        setRecordFilter(btn.getAttribute('data-ip-record-filter') || '');
      });
    });
    ['ipTask1Extra', 'ipTask2Extra', 'ipImageExtra'].forEach(function(id) {
      if ($(id)) $(id).addEventListener('input', saveGenerationSettings);
    });
    if ($('ipTemplateSelect')) $('ipTemplateSelect').addEventListener('change', renderTemplateOptions);
    if ($('ipApplyTemplateBtn')) $('ipApplyTemplateBtn').addEventListener('click', applySelectedTemplate);
    if ($('ipSaveTemplateBtn')) $('ipSaveTemplateBtn').addEventListener('click', saveCurrentTemplate);
    if ($('ipDeleteTemplateBtn')) $('ipDeleteTemplateBtn').addEventListener('click', deleteSelectedTemplate);
    document.addEventListener('click', function() {
      var menu = $('ipMemoryDropdownMenu');
      if (menu) menu.hidden = true;
    });
    if ($('ipAddKeywordBtn')) $('ipAddKeywordBtn').addEventListener('click', addKeyword);
    if ($('ipCompetitorPlatform')) $('ipCompetitorPlatform').addEventListener('change', updateCompetitorPlatformFields);
    if ($('ipSearchCompetitorBtn')) $('ipSearchCompetitorBtn').addEventListener('click', searchCompetitors);
    if ($('ipCompetitorSearchInput')) {
      $('ipCompetitorSearchInput').addEventListener('keydown', function(ev) {
        if (ev.key === 'Enter') {
          ev.preventDefault();
          searchCompetitors();
        }
      });
    }
    if ($('ipContentRefreshBtn')) $('ipContentRefreshBtn').addEventListener('click', function() {
      setBusy($('ipContentRefreshBtn'), true, '刷新中...');
      refreshAll().then(function() { setMsg('数据已刷新。'); }).finally(function() { setBusy($('ipContentRefreshBtn'), false); });
    });
    if ($('ipContentBackBtn')) $('ipContentBackBtn').addEventListener('click', function() {
      if (typeof showView === 'function') showView('skill-store');
      else if (typeof window.showLobsterView === 'function') window.showLobsterView('skill-store');
      else history.back();
    });
    if ($('ipOpenRequirementConfigBtn')) $('ipOpenRequirementConfigBtn').addEventListener('click', function() {
      switchTab('config');
      switchConfigTab('requirements');
    });
    if ($('ipRefreshKeywordSourcesBtn')) $('ipRefreshKeywordSourcesBtn').addEventListener('click', function() { loadSources(); });
    if ($('ipRefreshCompetitorSourcesBtn')) $('ipRefreshCompetitorSourcesBtn').addEventListener('click', function() { loadSources(); });
    if ($('ipKeywordSourceFilter')) $('ipKeywordSourceFilter').addEventListener('change', function() {
      state.keywordSourceFilter = $('ipKeywordSourceFilter').value || '';
      renderSourceList('ipKeywordSourceList', state.keywordSources, 'keyword', currentSourceFilter());
    });
    if ($('ipCompetitorSourceFilter')) $('ipCompetitorSourceFilter').addEventListener('change', function() {
      state.competitorSourceFilter = $('ipCompetitorSourceFilter').value || '';
      renderSourceList('ipCompetitorSourceList', state.competitorSources, 'competitor', currentSourceFilter());
    });
    if ($('ipGenerateIndustryBtn')) $('ipGenerateIndustryBtn').addEventListener('click', function() {
      runGenerate($('ipGenerateIndustryBtn'), '/api/ip-content/generate/industry-hot-oral', 'ipTask1Extra', 5, 'records');
    });
    if ($('ipGenerateIpBtn')) $('ipGenerateIpBtn').addEventListener('click', function() {
      runGenerate($('ipGenerateIpBtn'), '/api/ip-content/generate/professional-ip-oral', 'ipTask1Extra', 5, 'records');
    });
    if ($('ipGenerateMomentsBtn')) $('ipGenerateMomentsBtn').addEventListener('click', function() {
      runGenerate($('ipGenerateMomentsBtn'), '/api/ip-content/generate/moments-candidates', 'ipTask2Extra', 20, 'records');
    });
    if ($('ipGenerateSelectedImagesBtn')) $('ipGenerateSelectedImagesBtn').addEventListener('click', confirmMomentsImages);
  }

  window.initIpContentStudioView = function() {
    bind();
    restoreGenerationSettings();
    renderDraftCards('ipLatestDraftList', []);
    switchConfigTab(state.configTab);
    updateCompetitorPlatformFields();
    refreshAll();
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() {
      if ($('content-ip-content-studio')) window.initIpContentStudioView();
    });
  } else {
    setTimeout(function() {
      if ($('content-ip-content-studio')) window.initIpContentStudioView();
    }, 0);
  }
})();
