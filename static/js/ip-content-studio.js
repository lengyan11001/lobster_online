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
    configTab: 'templates',
    settingTemplates: [],
    activeTemplateId: '',
    templateKeywordIds: [],
    templateCompetitorIds: []
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
    return fetch(base + path, req).catch(function(err) {
      var raw = err && err.message ? String(err.message) : '';
      if (raw === 'Failed to fetch' || /Failed to fetch|NetworkError|Load failed/i.test(raw)) {
        throw new Error('网络请求中断：云端接口响应太久或连接被浏览器断开，请稍后重试。');
      }
      throw err;
    }).then(function(resp) {
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
    state.configTab = tab || 'templates';
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
        renderTemplateSummary();
      });
    });
    updateMemorySelectionLabel();
    renderTemplateSummary();
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
      keyword_ids: cleanTemplateIds(state.templateKeywordIds, false),
      competitor_ids: cleanTemplateIds(state.templateCompetitorIds, false),
      task1_extra: (($('ipTask1Extra') && $('ipTask1Extra').value) || '').trim(),
      task2_extra: (($('ipTask2Extra') && $('ipTask2Extra').value) || '').trim(),
      image_extra: (($('ipImageExtra') && $('ipImageExtra').value) || '').trim()
    };
  }

  function saveGenerationSettings() {
    writeStoredJson(SETTINGS_STORAGE_KEY, generationSettingSnapshot());
  }

  function cleanTemplateIds(values, asString) {
    if (!Array.isArray(values)) return [];
    var seen = {};
    var out = [];
    values.forEach(function(value) {
      var normalized = asString ? String(value || '').trim() : parseInt(value, 10);
      if (!normalized || seen[String(normalized)]) return;
      seen[String(normalized)] = true;
      out.push(normalized);
    });
    return out;
  }

  function templateRequirements(item) {
    var req = item && item.requirements && typeof item.requirements === 'object' ? item.requirements : {};
    return {
      oral: String(item.task1_extra || item.task1Extra || req.oral || req.industry_oral || req.ip_oral || ''),
      moments: String(item.task2_extra || item.task2Extra || req.moments || req.moments_copy || ''),
      image: String(item.image_extra || item.imageExtra || req.image || '')
    };
  }

  function templateMemoryDocs(item) {
    var docs = Array.isArray(item && item.memory_docs) ? item.memory_docs : [];
    return docs.map(function(doc) {
      if (typeof doc === 'string') return { id: doc, title: doc };
      return Object.assign({}, doc || {});
    }).filter(function(doc) {
      return doc && (doc.id || doc.doc_id || doc.filename || doc.name || doc.title);
    });
  }

  function normalizeTemplates(raw) {
    if (!Array.isArray(raw)) return [];
    return raw.filter(function(item) { return item && item.id && item.name; }).map(function(item) {
      var req = templateRequirements(item);
      var memoryDocs = templateMemoryDocs(item);
      var memoryIds = cleanTemplateIds(item.memory_doc_ids || item.memoryIds || memoryDocs.map(memoryDocId), true);
      var source = item.source || (item.requirements || item.keyword_ids || item.competitor_ids || item.memory_docs ? 'server' : 'local');
      return {
        id: String(item.id),
        server_id: item.server_id || (source === 'server' ? item.id : ''),
        source: source,
        name: String(item.name || ''),
        keyword_ids: cleanTemplateIds(item.keyword_ids, false),
        competitor_ids: cleanTemplateIds(item.competitor_ids, false),
        memory_docs: memoryDocs,
        memory_doc_ids: memoryIds,
        task1_extra: req.oral,
        task2_extra: req.moments,
        image_extra: req.image,
        requirements: item.requirements || {},
        meta: item.meta || {},
        updated_at: item.updated_at || item.updatedAt || ''
      };
    });
  }

  function activeTemplate() {
    return state.settingTemplates.find(function(item) { return item.id === state.activeTemplateId; }) || null;
  }

  function localTemplateBackup() {
    return state.settingTemplates.map(function(tpl) {
      return Object.assign({}, tpl, { source: tpl.source || 'local' });
    });
  }

  function writeTemplateBackup() {
    writeStoredJson(TEMPLATES_STORAGE_KEY, localTemplateBackup());
  }

  function upsertTemplate(tpl) {
    if (!tpl || !tpl.id) return;
    var idx = state.settingTemplates.findIndex(function(item) {
      return item.id === tpl.id ||
        (tpl.server_id && String(item.server_id || '') === String(tpl.server_id)) ||
        (tpl.source === 'server' && item.source !== 'server' && item.name === tpl.name);
    });
    if (idx >= 0) state.settingTemplates.splice(idx, 1, tpl);
    else state.settingTemplates.unshift(tpl);
    var keptIndex = state.settingTemplates.indexOf(tpl);
    if (tpl.source === 'server') {
      state.settingTemplates = state.settingTemplates.filter(function(item, index) {
        if (index === keptIndex) return true;
        if (tpl.server_id && String(item.server_id || '') === String(tpl.server_id)) return false;
        if (item.source !== 'server' && item.name === tpl.name) return false;
        return true;
      });
    }
    state.activeTemplateId = tpl.id;
    writeTemplateBackup();
  }

  function templateMemoryLabelFromRef(ref) {
    if (!ref) return '';
    var id = memoryDocId(ref);
    var doc = state.docs.find(function(item) { return memoryDocId(item) === id; });
    return memoryDocTitle(doc || ref);
  }

  function keywordLabelById(id) {
    var row = state.keywords.find(function(item) { return String(item.id || '') === String(id || ''); });
    return row ? (row.display_name || row.keyword || String(id)) : String(id || '');
  }

  function competitorLabelById(id) {
    var row = state.competitors.find(function(item) { return String(item.id || '') === String(id || ''); });
    return row ? (row.display_name || row.account_key || String(id)) : String(id || '');
  }

  function templateIdSelected(list, id) {
    return cleanTemplateIds(list, false).some(function(item) { return String(item) === String(id); });
  }

  function setTemplateIdSelected(key, id, checked) {
    var list = cleanTemplateIds(state[key], false);
    var value = parseInt(id, 10);
    if (!value) return;
    if (checked && !templateIdSelected(list, value)) list.push(value);
    if (!checked) list = list.filter(function(item) { return String(item) !== String(value); });
    state[key] = list;
    saveGenerationSettings();
    renderTemplateSummary();
  }

  function renderTemplatePickers() {
    var keywordBox = $('ipTemplateKeywordPicker');
    var competitorBox = $('ipTemplateCompetitorPicker');
    if (keywordBox) {
      if (!state.keywords.length) {
        keywordBox.innerHTML = '<div class="ip-content-empty">暂无关键词，先到关键词页添加。</div>';
      } else {
        keywordBox.innerHTML = state.keywords.map(function(item) {
          var id = item.id;
          return '<label class="ip-template-choice">' +
            '<input type="checkbox" data-template-keyword="' + escAttr(id) + '"' + (templateIdSelected(state.templateKeywordIds, id) ? ' checked' : '') + '>' +
            '<span><strong>' + esc(item.display_name || item.keyword || ('关键词 #' + id)) + '</strong><small>' + esc(item.keyword || '') + '</small></span>' +
            '</label>';
        }).join('');
        keywordBox.querySelectorAll('[data-template-keyword]').forEach(function(input) {
          input.addEventListener('change', function() {
            setTemplateIdSelected('templateKeywordIds', input.getAttribute('data-template-keyword'), input.checked);
          });
        });
      }
    }
    if (competitorBox) {
      if (!state.competitors.length) {
        competitorBox.innerHTML = '<div class="ip-content-empty">暂无同行账号，先到同行账号页添加。</div>';
      } else {
        competitorBox.innerHTML = state.competitors.map(function(item) {
          var id = item.id;
          return '<label class="ip-template-choice">' +
            '<input type="checkbox" data-template-competitor="' + escAttr(id) + '"' + (templateIdSelected(state.templateCompetitorIds, id) ? ' checked' : '') + '>' +
            '<span><strong>' + esc(item.display_name || item.account_key || ('同行 #' + id)) + '</strong><small>' + esc(platformLabel(item.platform || 'douyin')) + ' · ' + esc(item.account_key || '') + '</small></span>' +
            '</label>';
        }).join('');
        competitorBox.querySelectorAll('[data-template-competitor]').forEach(function(input) {
          input.addEventListener('change', function() {
            setTemplateIdSelected('templateCompetitorIds', input.getAttribute('data-template-competitor'), input.checked);
          });
        });
      }
    }
  }

  function chipHtml(items, emptyText) {
    var rows = (items || []).filter(Boolean);
    if (!rows.length) return '<small>' + esc(emptyText || '未选择') + '</small>';
    return '<div class="ip-template-chip-row">' + rows.map(function(text) {
      return '<span class="ip-template-chip">' + esc(text) + '</span>';
    }).join('') + '</div>';
  }

  function requirementPreview(label, text) {
    return '<div class="ip-template-snapshot-section"><strong>' + esc(label) + '</strong>' +
      '<div class="ip-template-text-preview">' + esc(text || '未填写') + '</div></div>';
  }

  function templateSummaryHtml(title, data) {
    data = data || {};
    var memories = (data.memory_docs || []).map(templateMemoryLabelFromRef);
    if (!memories.length && data.memory_doc_ids) {
      memories = data.memory_doc_ids.map(function(id) {
        var doc = state.docs.find(function(item) { return memoryDocId(item) === String(id); });
        return doc ? memoryDocTitle(doc) : String(id);
      });
    }
    var keywords = (data.keyword_ids || []).map(keywordLabelById);
    var competitors = (data.competitor_ids || []).map(competitorLabelById);
    return '<div class="ip-template-snapshot-section"><strong>' + esc(title) + '</strong>' +
      '<small>记忆文件</small>' + chipHtml(memories, '未选择记忆文件') +
      '<small>关键词</small>' + chipHtml(keywords, '未配置关键词') +
      '<small>同行账号</small>' + chipHtml(competitors, '未配置同行账号') +
      '</div>' +
      requirementPreview('口播要求', data.task1_extra || '') +
      requirementPreview('朋友圈文案要求', data.task2_extra || '') +
      requirementPreview('出图要求', data.image_extra || '');
  }

  function currentTemplateSnapshot() {
    var snapshot = generationSettingSnapshot();
    return {
      memory_doc_ids: snapshot.memory_doc_ids,
      memory_docs: selectedMemoryDocs(),
      keyword_ids: cleanTemplateIds(state.templateKeywordIds, false),
      competitor_ids: cleanTemplateIds(state.templateCompetitorIds, false),
      task1_extra: snapshot.task1_extra,
      task2_extra: snapshot.task2_extra,
      image_extra: snapshot.image_extra
    };
  }

  function renderTemplateList() {
    var list = $('ipTemplateRecordList');
    state.settingTemplates = normalizeTemplates(state.settingTemplates);
    if (state.activeTemplateId && !state.settingTemplates.some(function(tpl) { return tpl.id === state.activeTemplateId; })) {
      state.activeTemplateId = '';
    }
    var generateSelect = $('ipGenerateTemplateSelect');
    if (generateSelect) {
      var current = generateSelect.value || state.activeTemplateId || '';
      generateSelect.innerHTML = '<option value="">请选择模板</option>' + state.settingTemplates.map(function(tpl) {
        return '<option value="' + escAttr(tpl.id) + '">' + esc(tpl.name) + '</option>';
      }).join('');
      if (current && state.settingTemplates.some(function(tpl) { return tpl.id === current; })) generateSelect.value = current;
      else generateSelect.value = '';
    }
    if (!list) return;
    if (!state.settingTemplates.length) {
      list.innerHTML = '<div class="ip-content-empty">暂无模板。点击添加模板，填写记忆、关键词、同行和要求后保存。</div>';
    } else {
      list.innerHTML = state.settingTemplates.map(function(tpl) {
        var meta = [];
        meta.push(tpl.source === 'server' ? '服务器' : '本地');
        if (tpl.keyword_ids.length) meta.push('关键词 ' + tpl.keyword_ids.length);
        if (tpl.competitor_ids.length) meta.push('同行 ' + tpl.competitor_ids.length);
        if (tpl.memory_doc_ids.length || tpl.memory_docs.length) meta.push('记忆 ' + Math.max(tpl.memory_doc_ids.length, tpl.memory_docs.length));
        return '<button type="button" class="ip-template-record' + (tpl.id === state.activeTemplateId ? ' is-active' : '') + '" data-template-id="' + escAttr(tpl.id) + '">' +
          '<strong>' + esc(tpl.name) + '</strong>' +
          '<small>' + esc(meta.join(' · ') || '模板') + '</small>' +
          (tpl.updated_at ? '<small>' + esc(fmtTime(tpl.updated_at)) + '</small>' : '') +
          '</button>';
      }).join('');
      list.querySelectorAll('[data-template-id]').forEach(function(btn) {
        btn.addEventListener('click', function() {
          state.activeTemplateId = btn.getAttribute('data-template-id') || '';
          var tpl = activeTemplate();
          if ($('ipTemplateNameInput') && tpl) $('ipTemplateNameInput').value = tpl.name || '';
          if (tpl) applyTemplate(tpl);
          renderTemplateOptions();
        });
      });
    }
  }

  function renderTemplateSummary() {
    var box = $('ipTemplateSnapshot');
    var tpl = activeTemplate();
    if ($('ipDeleteTemplateBtn')) $('ipDeleteTemplateBtn').disabled = !tpl;
    if (!box) return;
    if (tpl) {
      box.innerHTML = templateSummaryHtml('选中模板记录', tpl) +
        '<div class="ip-content-empty">左侧点击模板后，会直接回填到右侧表单，可修改后保存。</div>';
      return;
    }
    box.innerHTML = templateSummaryHtml('当前将保存的内容', currentTemplateSnapshot()) +
      '<div class="ip-content-empty">左侧选择模板后，可查看模板记录内容并应用或删除。</div>';
  }

  function renderTemplateOptions() {
    renderTemplateList();
    renderTemplatePickers();
    renderTemplateSummary();
  }

  function newTemplateDraft() {
    state.activeTemplateId = '';
    state.templateKeywordIds = [];
    state.templateCompetitorIds = [];
    state.selectedDocs = {};
    if ($('ipTemplateNameInput')) $('ipTemplateNameInput').value = '';
    if ($('ipTask1Extra')) $('ipTask1Extra').value = '';
    if ($('ipTask2Extra')) $('ipTask2Extra').value = '';
    if ($('ipImageExtra')) $('ipImageExtra').value = '';
    saveGenerationSettings();
    renderMemoryList();
    renderTemplateOptions();
    setMsg('请填写模板内容后保存。');
  }

  function selectTemplateById(id, opts) {
    opts = opts || {};
    var tpl = state.settingTemplates.find(function(item) { return item.id === String(id || ''); });
    if (!tpl) {
      if (opts.required) setMsg('请选择模板后再生成。', true);
      return null;
    }
    state.activeTemplateId = tpl.id;
    if ($('ipGenerateTemplateSelect')) $('ipGenerateTemplateSelect').value = tpl.id;
    if ($('ipTemplateNameInput')) $('ipTemplateNameInput').value = tpl.name || '';
    applyTemplate(tpl);
    renderTemplateOptions();
    return tpl;
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
    state.templateKeywordIds = cleanTemplateIds(saved.keyword_ids, false);
    state.templateCompetitorIds = cleanTemplateIds(saved.competitor_ids, false);
    state.settingTemplates = normalizeTemplates(readStoredJson(TEMPLATES_STORAGE_KEY, []));
    renderTemplateOptions();
  }

  function applyTemplate(tpl) {
    if (!tpl) return;
    if ($('ipTask1Extra')) $('ipTask1Extra').value = tpl.task1_extra || '';
    if ($('ipTask2Extra')) $('ipTask2Extra').value = tpl.task2_extra || '';
    if ($('ipImageExtra')) $('ipImageExtra').value = tpl.image_extra || '';
    state.templateKeywordIds = cleanTemplateIds(tpl.keyword_ids, false);
    state.templateCompetitorIds = cleanTemplateIds(tpl.competitor_ids, false);
    state.selectedDocs = {};
    var markMemoryId = function(rawId) {
      var id = String(rawId || '').trim();
      if (!id) return false;
      var doc = state.docs.find(function(item) {
        return memoryDocId(item) === id ||
          String(item.name || '').trim() === id ||
          String(item.title || '').trim() === id ||
          String(item.filename || '').trim() === id;
      });
      state.selectedDocs[doc ? memoryDocId(doc) : id] = true;
      return true;
    };
    (tpl.memory_doc_ids || []).forEach(function(id) {
      markMemoryId(id);
    });
    if (!Object.keys(state.selectedDocs).length && Array.isArray(tpl.memory_docs)) {
      tpl.memory_docs.forEach(function(doc) {
        markMemoryId(memoryDocId(doc) || doc.title || doc.name || doc.filename);
      });
    }
    renderMemoryList();
    renderTemplatePickers();
    saveGenerationSettings();
    if ($('ipTemplateNameInput')) $('ipTemplateNameInput').value = tpl.name || '';
    renderTemplateSummary();
  }

  function applySelectedTemplate() {
    var tpl = activeTemplate();
    if (!tpl) {
      setMsg('请选择要应用的模板。', true);
      return;
    }
    applyTemplate(tpl);
    setMsg('已应用模板：' + tpl.name);
  }

  function templateRequestBody(name, snapshot, memoryDocs) {
    return {
      name: name,
      keyword_ids: cleanTemplateIds(snapshot.keyword_ids, false),
      competitor_ids: cleanTemplateIds(snapshot.competitor_ids, false),
      memory_docs: memoryDocs || [],
      requirements: {
        oral: snapshot.task1_extra || '',
        industry_oral: snapshot.task1_extra || '',
        ip_oral: snapshot.task1_extra || '',
        moments: snapshot.task2_extra || '',
        image: snapshot.image_extra || '',
        common: ''
      },
      meta: { source: 'ip_content_studio' }
    };
  }

  function serverTemplateFromItem(item) {
    item = Object.assign({}, item || {}, { source: 'server', server_id: item && item.id });
    return normalizeTemplates([item])[0] || null;
  }

  function localTemplateFromSnapshot(id, name, snapshot) {
    return normalizeTemplates([{
      id: id || ('tpl_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8)),
      source: 'local',
      name: name,
      keyword_ids: snapshot.keyword_ids,
      competitor_ids: snapshot.competitor_ids,
      memory_doc_ids: snapshot.memory_doc_ids,
      task1_extra: snapshot.task1_extra,
      task2_extra: snapshot.task2_extra,
      image_extra: snapshot.image_extra,
      updated_at: new Date().toISOString()
    }])[0];
  }

  function loadServerTemplates() {
    return cloudJson('/api/ip-content/schedule-templates')
      .then(function(data) {
        var server = normalizeTemplates((Array.isArray(data.items) ? data.items : []).map(function(item) {
          return Object.assign({}, item || {}, { source: 'server', server_id: item && item.id });
        }));
        var localOnly = normalizeTemplates(readStoredJson(TEMPLATES_STORAGE_KEY, [])).filter(function(tpl) {
          if (tpl.source === 'server') return false;
          return !server.some(function(row) { return row.name === tpl.name; });
        });
        state.settingTemplates = server.concat(localOnly);
        if (state.activeTemplateId && !state.settingTemplates.some(function(tpl) { return tpl.id === state.activeTemplateId; })) {
          state.activeTemplateId = '';
        }
        renderTemplateOptions();
      })
      .catch(function(err) {
        state.settingTemplates = normalizeTemplates(readStoredJson(TEMPLATES_STORAGE_KEY, []));
        renderTemplateOptions();
        setMsg('模板记录加载失败，已显示本地备份：' + (err.message || '未知错误'), true);
      });
  }

  function saveCurrentTemplate() {
    var active = activeTemplate();
    var name = (($('ipTemplateNameInput') && $('ipTemplateNameInput').value) || (active && active.name) || '').trim();
    if (!name) {
      setMsg('请先填写模板名称。', true);
      if ($('ipTemplateNameInput')) $('ipTemplateNameInput').focus();
      return;
    }
    var snapshot = currentTemplateSnapshot();
    var localId = active && active.source !== 'server' ? active.id : '';
    var localTpl = localTemplateFromSnapshot(localId, name, snapshot);
    upsertTemplate(localTpl);
    saveGenerationSettings();
    renderTemplateOptions();
    setMsg('模板本地备份已保存，正在同步服务器...');
    setBusy($('ipSaveTemplateBtn'), true, '保存中...');
    selectedMemoryDocsWithContent().then(function(memoryDocs) {
      var body = templateRequestBody(name, snapshot, memoryDocs);
      var activeServerId = active && active.source === 'server' ? (active.server_id || active.id) : '';
      return cloudJson('/api/ip-content/schedule-templates')
        .then(function(data) {
          var existing = (Array.isArray(data.items) ? data.items : []).find(function(item) {
            return item && (String(item.id) === String(activeServerId) || item.name === name);
          });
          return cloudJson('/api/ip-content/schedule-templates' + (existing ? '/' + encodeURIComponent(existing.id) : ''), {
            method: existing ? 'PATCH' : 'POST',
            body: body
          });
        });
    }).then(function(data) {
      var serverTpl = serverTemplateFromItem(data.item || data);
      if (serverTpl) {
        upsertTemplate(serverTpl);
        if ($('ipTemplateNameInput')) $('ipTemplateNameInput').value = serverTpl.name || name;
        renderTemplateOptions();
      }
      setMsg('模板已保存到服务器：' + name);
    }).catch(function(err) {
      setMsg('本地模板已保存，服务器模板保存失败：' + (err.message || '未知错误'), true);
    }).finally(function() {
      setBusy($('ipSaveTemplateBtn'), false);
    });
  }

  function deleteSelectedTemplate() {
    var tpl = activeTemplate();
    if (!tpl) {
      setMsg('请选择要删除的模板。', true);
      return;
    }
    if (!window.confirm('删除模板“' + tpl.name + '”？')) return;
    var removeLocal = function() {
      state.settingTemplates = state.settingTemplates.filter(function(item) { return item.id !== tpl.id; });
      state.activeTemplateId = '';
      writeTemplateBackup();
      if ($('ipTemplateNameInput')) $('ipTemplateNameInput').value = '';
      renderTemplateOptions();
    };
    setBusy($('ipDeleteTemplateBtn'), true, '删除中...');
    var serverId = tpl.source === 'server' ? (tpl.server_id || tpl.id) : '';
    var task = serverId
      ? cloudJson('/api/ip-content/schedule-templates/' + encodeURIComponent(serverId), { method: 'DELETE', json: false })
      : Promise.resolve();
    task.then(function() {
      removeLocal();
      setMsg('模板已删除。');
    }).catch(function(err) {
      setMsg(err.message || '模板删除失败', true);
    }).finally(function() {
      setBusy($('ipDeleteTemplateBtn'), false);
    });
  }

  function renderKeywords() {
    var list = $('ipKeywordList');
    renderTemplatePickers();
    renderTemplateSummary();
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
    renderTemplatePickers();
    renderTemplateSummary();
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

  function recordImagePrompts(rec) {
    var prompts = [];
    function add(value) {
      value = String(value || '').trim();
      if (value && prompts.indexOf(value) < 0) prompts.push(value);
    }
    if (rec && Array.isArray(rec.image_prompts)) rec.image_prompts.forEach(add);
    var meta = rec && rec.meta ? rec.meta : {};
    if (Array.isArray(meta.image_prompts)) meta.image_prompts.forEach(add);
    add(rec && rec.image_prompt);
    return prompts.slice(0, 3);
  }

  function renderImagePrompts(rec) {
    var prompts = recordImagePrompts(rec);
    if (!prompts.length) return '';
    return '<div class="ip-image-prompt-list">' + prompts.map(function(prompt, idx) {
      return '<small>配图 ' + esc(idx + 1) + '：' + esc(prompt) + '</small>';
    }).join('') + '</div>';
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
          image_prompts: recordImagePrompts(rec),
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
      var isMoments = rec.task === 'moments_candidate';
      var bodyText = rec.body || rec.content || '';
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
        '<textarea class="' + (isMoments ? 'ip-moments-copy-editor' : '') + '" data-record-copy="' + escAttr(rec.record_id || '') + '">' + esc(bodyText) + '</textarea>' +
        renderImagePrompts(rec) +
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
      var maxH = ta.classList.contains('ip-moments-copy-editor') ? 520 : 260;
      var minH = ta.classList.contains('ip-moments-copy-editor') ? 220 : 42;
      ta.style.height = Math.min(Math.max(ta.scrollHeight + 2, minH), maxH) + 'px';
      ta.addEventListener('input', function() {
        ta.style.height = 'auto';
        ta.style.height = Math.min(Math.max(ta.scrollHeight + 2, minH), maxH) + 'px';
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
        var bodyText = rec.body || rec.content || '';
        return '<div class="ip-draft-card">' +
          '<div class="ip-badge-row"><span class="ip-badge">朋友圈</span><span class="ip-badge is-image">图片 ' + esc(images.length) + '</span><span class="ip-badge' + (momentRecordFailed(rec) ? ' is-used' : '') + '">' + esc(status || '等待生成') + '</span>' + (progress ? '<span class="ip-badge">进度 ' + esc(progress) + '</span>' : '') + '</div>' +
          '<strong>' + esc(rec.title || '未命名文案') + '</strong>' +
          '<div class="ip-moments-copy-preview" data-moment-image-copy="' + escAttr(rec.record_id || '') + '">' + esc(bodyText) + '</div>' +
          renderImagePrompts(rec) +
          (images.length ? '<div class="ip-image-grid">' + images.slice(0, 3).map(function(img, idx) {
            var url = img.image_url || img.url || '';
            return '<div class="ip-image-tile"><img src="' + escAttr(url) + '" alt="朋友圈图片 ' + escAttr(idx + 1) + '">' +
              '<a class="btn btn-ghost btn-sm" href="' + escAttr(url) + '" target="_blank" rel="noopener">打开图片</a></div>';
          }).join('') + '</div>' : '<div class="ip-content-empty">' + esc(status || '等待生成图片...') + '</div>') +
          '<div class="ip-content-item-actions"><button type="button" class="btn btn-ghost btn-sm" data-copy-moment-image-record="' + escAttr(rec.record_id) + '">复制文案</button></div>' +
          '</div>';
      }).join('');
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

  function generationPayload(extraId, count, opts) {
    opts = opts || {};
    var selectedTemplateId = ($('ipGenerateTemplateSelect') && $('ipGenerateTemplateSelect').value) || state.activeTemplateId || '';
    if (!selectTemplateById(selectedTemplateId, { required: true })) {
      return Promise.reject(new Error('请选择模板后再生成。'));
    }
    var extraNode = extraId ? $(extraId) : null;
    saveGenerationSettings();
    var keywordIds = cleanTemplateIds(state.templateKeywordIds, false);
    var competitorIds = cleanTemplateIds(state.templateCompetitorIds, false);
    if (opts.requireKeywords && !keywordIds.length) return Promise.reject(new Error('请选择模板里的关键词后再生成。'));
    if (opts.requireCompetitors && !competitorIds.length) return Promise.reject(new Error('请选择模板里的同行账号后再生成。'));
    return selectedMemoryDocsWithContent().then(function(memoryDocs) {
      if (!memoryDocs.length) {
        var tpl = activeTemplate();
        var savedMemoryDocs = templateMemoryDocs(tpl);
        if (savedMemoryDocs.length) memoryDocs = savedMemoryDocs;
      }
      return {
        memory_docs: memoryDocs,
        keyword_ids: keywordIds,
        competitor_ids: competitorIds,
        extra_requirements: ((extraNode && extraNode.value) || '').trim(),
        count: count || 5,
        sync_before: false
      };
    });
  }

  function clonePayload(payload) {
    var copy = {};
    Object.keys(payload || {}).forEach(function(key) {
      var value = payload[key];
      if (Array.isArray(value)) copy[key] = value.slice();
      else if (value && typeof value === 'object') copy[key] = JSON.parse(JSON.stringify(value));
      else copy[key] = value;
    });
    return copy;
  }

  function delay(ms) {
    return new Promise(function(resolve) { setTimeout(resolve, ms); });
  }

  function postWithRetry(endpoint, payload, attempts, label) {
    attempts = Math.max(1, attempts || 1);
    var tried = 0;
    function once() {
      return cloudJson(endpoint, { method: 'POST', body: payload }).catch(function(err) {
        tried += 1;
        var message = err && err.message ? err.message : '请求失败';
        if (tried >= attempts) throw new Error((label ? label + '：' : '') + message);
        return delay(1200 * tried).then(once);
      });
    }
    return once();
  }

  function runMomentsGenerate(btn, successTab) {
    setBusy(btn, true, '生成中...');
    setMsg('正在分批生成朋友圈文案，请稍等...');
    generationPayload('ipTask2Extra', 20, {})
      .then(function(payload) {
        var total = 20;
        var batchSize = 5;
        var batchCount = Math.ceil(total / batchSize);
        var groupId = 'moments_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8);
        var allRecords = [];
        var chain = Promise.resolve();
        for (var i = 0; i < batchCount; i += 1) {
          (function(batchIndex) {
            chain = chain.then(function() {
              var batchPayload = clonePayload(payload);
              batchPayload.count = Math.min(batchSize, total - batchIndex * batchSize);
              batchPayload.group_id = groupId;
              batchPayload.sync_before = false;
              setMsg('正在生成朋友圈文案：第 ' + (batchIndex + 1) + '/' + batchCount + ' 批...');
              return postWithRetry('/api/ip-content/generate/moments-candidates', batchPayload, 2, '第 ' + (batchIndex + 1) + ' 批生成失败')
                .then(function(data) {
                  var records = Array.isArray(data.records) ? data.records : [];
                  records.forEach(function(rec) {
                    rec.group_id = rec.group_id || groupId;
                    rec.meta = Object.assign({}, rec.meta || {}, { group_id: groupId });
                  });
                  allRecords = allRecords.concat(records);
                  state.latestDrafts = allRecords;
                  if (records.length && !state.activeGroupId) {
                    state.activeGroupId = recordGroupId(records[0]);
                    state.recordFilter = records[0].task || 'moments_candidate';
                  }
                  setMsg('已生成 ' + allRecords.length + '/' + total + ' 条朋友圈文案...');
                });
            });
          })(i);
        }
        return chain.then(function() { return allRecords; });
      })
      .then(function(records) {
        if (records.length) {
          state.activeGroupId = recordGroupId(records[0]);
          state.recordFilter = records[0].task || 'moments_candidate';
        }
        setMsg('已生成 ' + records.length + ' 条朋友圈文案。');
        return Promise.all([loadDraftRecords(), loadSources()]).then(function() {
          setRecordFilter(state.recordFilter || 'moments_candidate');
          switchTab(successTab || 'records');
        });
      })
      .catch(function(err) { setMsg(err.message || '生成失败', true); })
      .finally(function() { setBusy(btn, false); });
  }

  function runGenerate(btn, endpoint, extraId, count, successTab, opts) {
    setBusy(btn, true, '生成中...');
    setMsg('正在同步数据并生成，请稍候...');
    generationPayload(extraId, count, opts)
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
        var promptList = recordImagePrompts(rec);
        var originalImagePrompt = (promptList[0] || rec.image_prompt || '').trim();
        var imageExtra = (($('ipImageExtra') && $('ipImageExtra').value) || '').trim();
        var memoryIds = selectedMemoryIdsForRecord(rec);
        saveGenerationSettings();
        var basePrompt = [
          copyTextValue ? '朋友圈文案：' + copyTextValue : '',
          imageExtra ? '出图要求：' + imageExtra : '',
          '只根据以上已审核文案和当前配图文案生成朋友圈配图。文案里的选题可能来自行业热门或同行新内容，图片要贴合这个新内容；同时必须遵守记忆文件里的账号定位、行业事实、产品/服务特点和表达风格，不要另起主题，不要出现文字、水印、按钮或二维码。'
        ].filter(Boolean).join('\n\n');
        var images = [];
        var imageVariants = [
          {
            name: '真实场景纪实',
            prompt: '画面方向：真实场景纪实。用自然光、真实空间、人物动作或工作现场表现文案里的场景，强调可信和生活感。'
          },
          {
            name: '细节特写隐喻',
            prompt: '画面方向：细节特写隐喻。选择一个能代表文案观点的物件、手部动作、桌面资料、工具或局部空间做主体，强调情绪和专业细节。'
          },
          {
            name: '关系与结果场景',
            prompt: '画面方向：关系与结果场景。表现人与人沟通、客户反馈、团队讨论、成果交付或前后对比的瞬间，强调业务结果和案例感。'
          }
        ];
        var generateOne = function(index) {
          if (preview) preview.innerHTML = '<small>正在出图 ' + (index - 1) + '/3...</small>';
          rec._image_status = '正在出图 ' + (index - 1) + '/3...';
          rec._image_progress = (index - 1) + '/3';
          rec.meta = Object.assign({}, rec.meta || {}, { image_status: rec._image_status, image_progress: rec._image_progress, images: images });
          refreshMomentBatchProgress(selected);
          var variant = imageVariants[(index - 1) % imageVariants.length];
          var promptForImage = (promptList[index - 1] || originalImagePrompt || variant.prompt).trim();
          return localJson('/api/creative-film-studio/generate-image', {
            method: 'POST',
            body: {
              memory_doc_ids: memoryIds,
              title: rec.title || '朋友圈配图',
              goal: copyTextValue,
              direct_prompt: [
                promptForImage ? '当前配图文案：' + promptForImage : '',
                basePrompt,
                variant.prompt,
                '这是同一条朋友圈文案的一组 3 张备选图中的第 ' + index + ' 张。优先执行“当前配图文案”，并保持和朋友圈正文同一个主题。三张图必须明显不同：主体、景别、构图、环境和关键道具至少改变三项；不要只改变色调或角度。',
                '不要把朋友圈文案或配图提示写成画面里的文字；不要出现文字、水印、按钮、二维码。'
              ].filter(Boolean).join('\n\n'),
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
              image_prompt: promptForImage,
              generated_prompt: data.image_prompt || '',
              variant: variant.name,
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
    return Promise.all([loadMemory(), loadKeywords(), loadCompetitors()])
      .then(function() {
        return Promise.all([loadServerTemplates(), loadSources(), loadDraftRecords()]);
      });
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
      if ($(id)) $(id).addEventListener('input', function() {
        saveGenerationSettings();
        renderTemplateSummary();
      });
    });
    if ($('ipRefreshTemplateBtn')) $('ipRefreshTemplateBtn').addEventListener('click', function() {
      setBusy($('ipRefreshTemplateBtn'), true, '刷新中...');
      loadServerTemplates().then(function() { setMsg('模板记录已刷新。'); }).finally(function() { setBusy($('ipRefreshTemplateBtn'), false); });
    });
    if ($('ipGenerateTemplateSelect')) $('ipGenerateTemplateSelect').addEventListener('change', function() {
      selectTemplateById($('ipGenerateTemplateSelect').value);
    });
    if ($('ipNewTemplateBtn')) $('ipNewTemplateBtn').addEventListener('click', newTemplateDraft);
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
      switchConfigTab('templates');
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
      runGenerate($('ipGenerateIndustryBtn'), '/api/ip-content/generate/industry-hot-oral', 'ipTask1Extra', 5, 'records', { requireKeywords: true });
    });
    if ($('ipGenerateIpBtn')) $('ipGenerateIpBtn').addEventListener('click', function() {
      runGenerate($('ipGenerateIpBtn'), '/api/ip-content/generate/professional-ip-oral', 'ipTask1Extra', 5, 'records', { requireCompetitors: true });
    });
    if ($('ipGenerateMomentsBtn')) $('ipGenerateMomentsBtn').addEventListener('click', function() {
      runMomentsGenerate($('ipGenerateMomentsBtn'), 'records');
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
