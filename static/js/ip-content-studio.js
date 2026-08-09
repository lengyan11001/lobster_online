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
    momentBatchJobs: [],
    latestDrafts: [],
    selectedRecordIds: {},
    recordFilter: '',
    configTab: 'templates',
    settingTemplates: [],
    activeTemplateId: '',
    templateKeywordIds: [],
    templateCompetitorIds: []
  };

  var SETTINGS_STORAGE_KEY = 'ipContentStudio.generationSettings.v1';
  var TEMPLATES_STORAGE_KEY = 'ipContentStudio.requirementTemplates.v1';
  var MOMENT_BATCH_JOBS_STORAGE_KEY = 'ipContentStudio.momentBatchJobs.v1';
  var IP_TEMPLATE_LANGUAGES = [
    ['zh-CN', '简体中文'],
    ['en', 'English'],
    ['ja', '日本語'],
    ['ko', '한국어'],
    ['th', 'ไทย'],
    ['vi', 'Tiếng Việt'],
    ['id', 'Bahasa Indonesia'],
    ['ms', 'Bahasa Melayu'],
    ['es', 'Español'],
    ['pt', 'Português'],
    ['fr', 'Français'],
    ['de', 'Deutsch'],
    ['ru', 'Русский'],
    ['ar', 'العربية']
  ];

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

  function normalizeIpTemplateLanguage(value) {
    var raw = String(value || '').trim();
    var lower = raw.toLowerCase();
    var aliases = {
      zh: 'zh-CN',
      'zh-cn': 'zh-CN',
      chinese: 'zh-CN',
      '简体中文': 'zh-CN',
      english: 'en',
      japanese: 'ja',
      korean: 'ko',
      thai: 'th',
      vietnamese: 'vi',
      indonesian: 'id',
      malay: 'ms',
      spanish: 'es',
      portuguese: 'pt',
      french: 'fr',
      german: 'de',
      russian: 'ru',
      arabic: 'ar'
    };
    var normalized = aliases[lower] || raw;
    return IP_TEMPLATE_LANGUAGES.some(function(row) { return row[0] === normalized; }) ? normalized : 'zh-CN';
  }

  function ipTemplateLanguageLabel(value) {
    var lang = normalizeIpTemplateLanguage(value);
    var row = IP_TEMPLATE_LANGUAGES.find(function(item) { return item[0] === lang; });
    return row ? row[1] : '简体中文';
  }

  function ipTemplateLanguageInstruction(value) {
    var label = ipTemplateLanguageLabel(value);
    return '目标语种：' + label + '。所有生成内容必须使用' + label + '输出；标题、口播正文、朋友圈正文、图片提示词中的可见文字都要使用' + label + '，不要混用其他语言。';
  }

  function stripIpTemplateLanguageInstruction(text) {
    return String(text || '')
      .split(/\r?\n/)
      .filter(function(line) { return !/^目标语种[:：]/.test(line.trim()); })
      .join('\n')
      .trim();
  }

  function textWithTemplateLanguage(text, language) {
    var clean = stripIpTemplateLanguageInstruction(text);
    return [ipTemplateLanguageInstruction(language), clean].filter(Boolean).join('\n');
  }

  function templateLanguageFromParts(requirements, meta, fallback) {
    var req = requirements && typeof requirements === 'object' ? requirements : {};
    var m = meta && typeof meta === 'object' ? meta : {};
    return normalizeIpTemplateLanguage(
      req.language || req.target_language ||
      m.language || m.target_language || m.profile_language ||
      fallback || ''
    );
  }

  function currentTemplateLanguage() {
    var tpl = activeTemplate() || state.settingTemplates[0] || {};
    return templateLanguageFromParts(tpl.requirements, tpl.meta, tpl.language || tpl.target_language || '');
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

  function syncOpenClawMemoryFromCloud() {
    if (!localBase()) return Promise.resolve({ ok: false, skipped: 'LOCAL_API_BASE not configured' });
    return localJson('/api/openclaw/memory/sync-cloud', { method: 'POST', json: false }).catch(function(err) {
      console.warn('[ip-content-studio] sync OpenClaw memory failed', err);
      return { ok: false, error: err && err.message ? err.message : String(err || '') };
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

  function isOralTask(task) {
    return task === 'industry_hot_oral' || task === 'professional_ip_oral';
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
    return cloudJson('/api/personal-settings/memory-documents/' + encodeURIComponent(id) + '/preview', { json: false })
      .then(function(data) {
        return Object.assign({}, doc, data.document || data.item || data.doc || {}, {
          content_text: data.content_text || data.content || ''
        });
      })
      .catch(function() {
        return localJson('/api/openclaw/memory/' + encodeURIComponent(id) + '/content', { json: false })
          .then(function(data) {
            return Object.assign({}, doc, data.document || data.item || data.doc || {}, {
              content_text: data.content_text || data.content || ''
            });
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
    return syncOpenClawMemoryFromCloud()
      .then(function() {
        return cloudJson('/api/personal-settings/memory-documents/list', { json: false });
      })
      .catch(function(cloudErr) {
        return localJson('/api/openclaw/memory/list', { json: false }).catch(function() {
          throw cloudErr;
        });
      })
      .then(function(data) {
        state.docs = Array.isArray(data.documents) ? data.documents : (Array.isArray(data.items) ? data.items : (Array.isArray(data.docs) ? data.docs : []));
        renderMemoryList();
      })
      .catch(function(err) {
        if (list) list.innerHTML = '<div class="ip-content-empty">' + esc(err.message || '记忆加载失败') + '</div>';
      });
  }

  function generationSettingSnapshot() {
    var language = currentTemplateLanguage();
    return {
      memory_doc_ids: selectedMemoryIds(),
      keyword_ids: cleanTemplateIds(state.templateKeywordIds, false),
      competitor_ids: cleanTemplateIds(state.templateCompetitorIds, false),
      language: language,
      target_language: ipTemplateLanguageLabel(language),
      task1_extra: (($('ipTask1Extra') && $('ipTask1Extra').value) || '').trim(),
      task2_extra: (($('ipTask2Extra') && $('ipTask2Extra').value) || '').trim(),
      image_extra: (($('ipImageExtra') && $('ipImageExtra').value) || '').trim()
    };
  }

  function saveGenerationSettings() {
    writeStoredJson(SETTINGS_STORAGE_KEY, generationSettingSnapshot());
  }

  function normalizeMomentBatchJobs(items) {
    if (!Array.isArray(items)) return [];
    return items.map(function(item, idx) {
      item = item && typeof item === 'object' ? item : {};
      var batchIndex = parseInt(item.batch_index, 10);
      if (!batchIndex || batchIndex < 1) batchIndex = idx + 1;
      var batchCount = parseInt(item.batch_count, 10);
      if (!batchCount || batchCount < batchIndex) batchCount = Math.max(batchIndex, 1);
      var batchId = String(item.batch_id || item.id || item.group_id || ('moment_batch_' + batchIndex + '_' + batchCount));
      var groupId = String(item.group_id || batchId);
      var status = String(item.status || 'queued').toLowerCase();
      if (['queued', 'running', 'done', 'failed'].indexOf(status) < 0) status = 'queued';
      return {
        batch_id: batchId,
        batch_index: batchIndex,
        batch_count: batchCount,
        label: String(item.label || ('第' + batchIndex + '批')),
        group_id: groupId,
        count: Math.max(1, parseInt(item.count, 10) || 5),
        status: status,
        error: String(item.error || ''),
        records: Array.isArray(item.records) ? item.records : [],
        payload: item.payload && typeof item.payload === 'object' ? item.payload : null,
        created_at: String(item.created_at || ''),
        updated_at: String(item.updated_at || ''),
        retry_count: Math.max(0, parseInt(item.retry_count, 10) || 0),
        summary: String(item.summary || '')
      };
    });
  }

  function saveMomentBatchJobs() {
    writeStoredJson(MOMENT_BATCH_JOBS_STORAGE_KEY, normalizeMomentBatchJobs(state.momentBatchJobs));
  }

  function restoreMomentBatchJobs() {
    state.momentBatchJobs = normalizeMomentBatchJobs(readStoredJson(MOMENT_BATCH_JOBS_STORAGE_KEY, []));
    renderMomentBatchQueue();
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
    var meta = item && item.meta && typeof item.meta === 'object' ? item.meta : {};
    var language = templateLanguageFromParts(req, meta, item && (item.language || item.target_language));
    return {
      language: language,
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
        language: req.language,
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
        competitorBox.innerHTML = '<div class="ip-content-empty">暂无同行账号。同行是可选输入，没有同行也可以保存模板和生成。</div>';
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
      '<small>同行账号（可选）</small>' + chipHtml(competitors, '未配置同行账号') +
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
      language: snapshot.language,
      target_language: snapshot.target_language,
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
    renderCurrentProfileTemplateBox();
    if (!list) return;
    if (!state.settingTemplates.length) {
      list.innerHTML = '<div class="ip-content-empty">未读取到当前IP人设。请到 IP人设定位 保存并启用模板。</div>';
    } else {
      list.innerHTML = state.settingTemplates.map(function(tpl) {
        var meta = [];
        meta.push(tpl.source === 'server' ? '服务器' : '本地');
        meta.push(ipTemplateLanguageLabel(tpl.language || 'zh-CN'));
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

  function renderCurrentProfileTemplateBox() {
    var box = $('ipCurrentProfileTemplateBox');
    if (!box) return;
    var tpl = activeTemplate() || state.settingTemplates[0] || null;
    if (!tpl) {
      box.innerHTML = '未读取到当前IP人设，请先到 IP人设定位 保存并启用模板。';
      return;
    }
    var keywordCount = Array.isArray(tpl.keyword_ids) ? tpl.keyword_ids.length : 0;
    var competitorCount = Array.isArray(tpl.competitor_ids) ? tpl.competitor_ids.length : 0;
    var memoryCount = Math.max(Array.isArray(tpl.memory_doc_ids) ? tpl.memory_doc_ids.length : 0, Array.isArray(tpl.memory_docs) ? tpl.memory_docs.length : 0);
    box.innerHTML = '<strong>' + esc(tpl.name || '当前IP人设') + '</strong>' +
      '<small>关键词 ' + keywordCount + ' · 同行 ' + competitorCount + ' · 记忆 ' + memoryCount + '</small>';
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
    state.settingTemplates = [];
    state.activeTemplateId = '';
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
    var language = normalizeIpTemplateLanguage(snapshot.language || snapshot.target_language || '');
    var targetLanguage = ipTemplateLanguageLabel(language);
    return {
      name: name,
      keyword_ids: cleanTemplateIds(snapshot.keyword_ids, false),
      competitor_ids: cleanTemplateIds(snapshot.competitor_ids, false),
      memory_doc_ids: cleanTemplateIds(snapshot.memory_doc_ids, true),
      memory_docs: memoryDocs || [],
      requirements: {
        language: language,
        target_language: targetLanguage,
        oral: snapshot.task1_extra || '',
        industry_oral: snapshot.task1_extra || '',
        ip_oral: snapshot.task1_extra || '',
        moments: snapshot.task2_extra || '',
        image: snapshot.image_extra || '',
        common: ipTemplateLanguageInstruction(language)
      },
      meta: { source: 'ip_content_studio', language: language, target_language: targetLanguage }
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
      language: snapshot.language,
      task1_extra: snapshot.task1_extra,
      task2_extra: snapshot.task2_extra,
      image_extra: snapshot.image_extra,
      requirements: {
        language: normalizeIpTemplateLanguage(snapshot.language || snapshot.target_language || ''),
        target_language: ipTemplateLanguageLabel(snapshot.language || snapshot.target_language || ''),
        oral: snapshot.task1_extra || '',
        industry_oral: snapshot.task1_extra || '',
        ip_oral: snapshot.task1_extra || '',
        moments: snapshot.task2_extra || '',
        image: snapshot.image_extra || '',
        common: ipTemplateLanguageInstruction(snapshot.language || snapshot.target_language || '')
      },
      meta: {
        source: 'ip_content_studio_local',
        language: normalizeIpTemplateLanguage(snapshot.language || snapshot.target_language || ''),
        target_language: ipTemplateLanguageLabel(snapshot.language || snapshot.target_language || '')
      },
      updated_at: new Date().toISOString()
    }])[0];
  }

  function loadServerTemplates() {
    return cloudJson('/api/ip-content/personal-default')
      .then(function(data) {
        var item = data.item || {};
        var meta = item.meta && typeof item.meta === 'object' ? item.meta : {};
        var language = templateLanguageFromParts(item.requirements, meta, item.language || item.target_language || '');
        var tpl = normalizeTemplates([Object.assign({}, item, {
          id: 'personal-default',
          server_id: item.id || '',
          source: 'personal_default',
          name: meta.current_template_name || item.display_name || '当前IP人设',
          language: language,
          target_language: ipTemplateLanguageLabel(language)
        })])[0];
        state.settingTemplates = tpl ? [tpl] : [];
        state.activeTemplateId = tpl ? tpl.id : '';
        renderTemplateOptions();
      })
      .catch(function(err) {
        state.settingTemplates = [];
        state.activeTemplateId = '';
        renderTemplateOptions();
        setMsg('当前IP人设读取失败：' + (err.message || '未知错误'), true);
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
      map[gid].image_count += recordImages(rec).length;
      if (rec.created_at && String(rec.created_at) > String(map[gid].created_at || '')) map[gid].created_at = rec.created_at;
    });
    return Object.keys(map).map(function(k) { return map[k]; }).sort(function(a, b) {
      return String(b.created_at || '').localeCompare(String(a.created_at || ''));
    });
  }

  function recordImages(rec) {
    var rows = [];
    var seen = {};
    function add(value, fallbackIndex) {
      if (typeof value === 'string') value = { image_url: value };
      if (!value || typeof value !== 'object') return;
      var url = String(value.image_url || value.url || value.source_url || '').trim();
      var assetId = String(value.image_asset_id || value.asset_id || '').trim();
      if (!url && !assetId) return;
      var existing = rows.find(function(item) {
        return (url && item.image_url === url) || (assetId && item.image_asset_id === assetId);
      });
      if (existing) {
        if (url && !existing.image_url) existing.image_url = url;
        if (assetId && !existing.image_asset_id) existing.image_asset_id = assetId;
        return;
      }
      var key = url || ('asset:' + assetId);
      if (seen[key]) return;
      seen[key] = true;
      rows.push(Object.assign({}, value, {
        image_url: url,
        image_asset_id: assetId,
        index: Number(value.index || fallbackIndex || rows.length + 1)
      }));
    }
    function addList(value) {
      if (Array.isArray(value)) value.forEach(function(item, idx) { add(item, idx + 1); });
      else if (value) add(value, rows.length + 1);
    }
    function addParallel(urls, assetIds) {
      var urlList = Array.isArray(urls) ? urls : [];
      var assetIdList = Array.isArray(assetIds) ? assetIds : [];
      for (var index = 0; index < Math.max(urlList.length, assetIdList.length); index += 1) {
        add({ image_url: urlList[index] || '', image_asset_id: assetIdList[index] || '' }, index + 1);
      }
    }
    addList(rec && rec.images);
    var meta = rec && rec.meta ? rec.meta : {};
    var imageUpdate = meta && meta.image_update ? meta.image_update : {};
    var directImageUpdate = rec && rec.image_update ? rec.image_update : {};
    addParallel(rec && rec.image_urls, rec && rec.image_asset_ids);
    addList(rec && rec.image_results);
    addList(directImageUpdate.images);
    addParallel(directImageUpdate.image_urls, directImageUpdate.image_asset_ids);
    addList(directImageUpdate.image_results);
    add(directImageUpdate, rows.length + 1);
    addList(meta.images);
    addParallel(meta.image_urls, meta.image_asset_ids);
    addList(meta.image_results);
    addList(imageUpdate.images);
    addParallel(imageUpdate.image_urls, imageUpdate.image_asset_ids);
    addList(imageUpdate.image_results);
    add({ image_url: rec && rec.image_url, image_asset_id: rec && rec.image_asset_id, image_prompt: rec && rec.image_prompt }, 1);
    add({ image_url: imageUpdate.image_url || imageUpdate.url, image_asset_id: imageUpdate.image_asset_id || imageUpdate.asset_id }, rows.length + 1);
    return rows.slice(0, 30);
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
    var images = recordImages(rec);
    var status = rec._image_status || meta.image_status || imageUpdate.image_status || '';
    if (images.length >= 3 && /失败|failed|error/i.test(String(status))) return '已完成';
    if (status) return String(status);
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
    if (recordImages(rec).length >= 3) return false;
    var status = momentRecordStatus(rec).toLowerCase();
    return status.indexOf('失败') >= 0 || status.indexOf('failed') >= 0 || status.indexOf('error') >= 0;
  }

  function momentRecordError(rec) {
    var meta = rec && rec.meta ? rec.meta : {};
    var imageUpdate = meta && meta.image_update ? meta.image_update : {};
    var error = rec && rec._image_error || meta.image_error || imageUpdate.image_error || '';
    if (error) return String(error).trim();
    return momentRecordStatus(rec).replace(/^生成失败\s*[：:]?\s*/, '').trim();
  }

  function momentRecordFailedIndex(rec) {
    var meta = rec && rec.meta ? rec.meta : {};
    var imageUpdate = meta && meta.image_update ? meta.image_update : {};
    return Number(rec && rec._image_failed_index || meta.image_failed_index || imageUpdate.image_failed_index || 0);
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
        image_progress: '0/3',
        image_error: '',
        image_failed_index: 0,
        image_complete: false
      });
      rec._image_status = idx === 0 ? '准备生成' : '等待生成';
      rec._image_progress = '0/3';
      rec._image_error = '';
      rec._image_failed_index = 0;
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
          image_error: rec._image_error || '',
          image_failed_index: rec._image_failed_index || 0,
          image_complete: momentRecordDone(rec),
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

  function setFieldValue(id, value) {
    var field = $(id);
    if (!field) return false;
    field.value = String(value || '');
    field.dispatchEvent(new Event('input', { bubbles: true }));
    field.dispatchEvent(new Event('change', { bubbles: true }));
    try {
      field.focus();
      if (typeof field.setSelectionRange === 'function') field.setSelectionRange(field.value.length, field.value.length);
    } catch (e) {}
    return true;
  }

  function fillFieldWhenReady(id, value, callback, attempts) {
    attempts = attempts === undefined ? 40 : attempts;
    if (setFieldValue(id, value)) {
      if (typeof callback === 'function') callback();
      return;
    }
    if (attempts <= 0) {
      setMsg('目标工作台加载失败，请重新操作。', true);
      return;
    }
    setTimeout(function() { fillFieldWhenReady(id, value, callback, attempts - 1); }, 50);
  }

  function ensureDraftActionAllowed(view) {
    if (typeof window.isLobsterViewAllowed === 'function' && !window.isLobsterViewAllowed(view)) {
      setMsg('当前账号没有该创作功能权限。', true);
      return false;
    }
    return true;
  }

  function openDraftMomentsPublish(rec, currentText) {
    rec = rec || {};
    var images = recordImages(rec).map(function(img) {
      return {
        image_url: String((img && (img.image_url || img.url || img.source_url)) || '').trim(),
        image_asset_id: String((img && (img.image_asset_id || img.asset_id)) || '').trim()
      };
    }).filter(function(img) { return img.image_url || img.image_asset_id; });
    if (!images.length) {
      setMsg('请先生成图片，再发布朋友圈。', true);
      return;
    }
    if (typeof window._openJuheWechatView !== 'function') {
      setMsg('朋友圈发布功能暂时无法打开。', true);
      return;
    }
    window._openJuheWechatView();
    var attempts = 50;
    (function waitForMoments() {
      if (typeof window.prefillNativeWechatMoments === 'function' && $('nativeWechatMomentsContent')) {
        window.prefillNativeWechatMoments({
          content: String(currentText || rec.body || rec.content || '').trim(),
          title: String(rec.title || '').trim(),
          image_urls: images.map(function(img) { return img.image_url; }),
          image_asset_ids: images.map(function(img) { return img.image_asset_id; }),
          images: images,
          source: 'ip_daily',
          source_id: String(rec.record_id || '').trim(),
          media_type: 'image_text'
        });
        return;
      }
      if (attempts <= 0) {
        setMsg('朋友圈发布页面加载失败，请重试。', true);
        return;
      }
      attempts -= 1;
      setTimeout(waitForMoments, 80);
    }());
  }

  function openDraftContentAction(rec, action, currentText) {
    rec = rec || {};
    var text = String(currentText || rec.body || rec.content || '').trim();
    var title = String(rec.title || taskLabel(rec.task) || '口播文案').trim();
    if (!text) {
      setMsg('当前记录没有可带入的文案。', true);
      return;
    }
    if (action === 'image') {
      if (!ensureDraftActionAllowed('image-composer-studio')) return;
      if (typeof window._openImageComposerStudioView === 'function') window._openImageComposerStudioView();
      else if (typeof window._openHiddenWorkspaceView === 'function') window._openHiddenWorkspaceView('image-composer-studio');
      fillFieldWhenReady('imglabPromptInput', text);
      return;
    }
    if (action === 'video') {
      if (!ensureDraftActionAllowed('seedance-tvc-studio')) return;
      if (typeof window._openSeedanceTvcStudioView === 'function') window._openSeedanceTvcStudioView();
      else if (typeof window._openHiddenWorkspaceView === 'function') window._openHiddenWorkspaceView('seedance-tvc-studio');
      fillFieldWhenReady('seedanceTaskPromptInput', text);
      return;
    }
    if (action === 'digital-human') {
      if (!ensureDraftActionAllowed('shanjian-digital-human')) return;
      if (typeof window._openShanjianDigitalHumanView === 'function') window._openShanjianDigitalHumanView();
      else if (typeof window._openHiddenWorkspaceView === 'function') window._openHiddenWorkspaceView('shanjian-digital-human');
      fillFieldWhenReady('shanjianScriptInput', text, function() {
        setFieldValue('shanjianTitleInput', title.slice(0, 20));
      });
    }
  }

  function deleteDraftRecord(recordId) {
    recordId = String(recordId || '');
    if (!recordId || !confirm('删除这条生成记录？删除后不可恢复。')) return;
    cloudJson('/api/ip-content/draft-records/' + encodeURIComponent(recordId), { method: 'DELETE', json: false })
      .then(function() {
        delete state.selectedRecordIds[recordId];
        setMsg('生成记录已删除。');
        return loadDraftRecords();
      })
      .catch(function(err) { setMsg(err.message || '生成记录删除失败', true); });
  }

  function deleteDraftGroup(groupId) {
    groupId = String(groupId || '');
    if (!groupId || !confirm('删除这一批全部生成记录？删除后不可恢复。')) return;
    cloudJson('/api/ip-content/draft-record-groups/' + encodeURIComponent(groupId), { method: 'DELETE', json: false })
      .then(function() {
        state.activeGroupId = '';
        state.selectedRecordIds = {};
        setMsg('这一批生成记录已删除。');
        return loadDraftRecords();
      })
      .catch(function(err) { setMsg(err.message || '生成批次删除失败', true); });
  }

  function updateRecordBulkToolbar() {
    var toolbar = $('ipRecordBulkToolbar');
    var selectAll = $('ipRecordSelectAll');
    var countNode = $('ipRecordSelectedCount');
    var copyBtn = $('ipCopySelectedRecordsBtn');
    var records = (state.latestDrafts || []).filter(function(rec) { return isOralTask(rec.task); });
    var selected = records.filter(function(rec) { return !!state.selectedRecordIds[String(rec.record_id || '')]; });
    if (toolbar) toolbar.hidden = !records.length;
    if (selectAll) {
      selectAll.checked = !!records.length && selected.length === records.length;
      selectAll.indeterminate = selected.length > 0 && selected.length < records.length;
    }
    if (countNode) countNode.textContent = '已选 ' + selected.length + ' 条';
    if (copyBtn) copyBtn.disabled = !selected.length;
  }

  function copySelectedDraftRecords() {
    var selected = (state.latestDrafts || []).filter(function(rec) {
      return isOralTask(rec.task) && !!state.selectedRecordIds[String(rec.record_id || '')];
    });
    if (!selected.length) {
      setMsg('请先选择要复制的文案。', true);
      return;
    }
    var text = selected.map(function(rec) {
      return [String(rec.title || '').trim(), String(rec.body || rec.content || '').trim()].filter(Boolean).join('\n');
    }).filter(Boolean).join('\n\n');
    copyText(text, $('ipCopySelectedRecordsBtn'));
    setMsg('已复制 ' + selected.length + ' 条文案。');
  }

  function draftActionMenuHtml(rec) {
    var id = escAttr(rec.record_id || '');
    var creationActions = isOralTask(rec.task)
      ? '<button type="button" data-record-action="image" data-record-id="' + id + '">生成图片</button>' +
        '<button type="button" data-record-action="video" data-record-id="' + id + '">生成视频</button>' +
        '<button type="button" data-record-action="digital-human" data-record-id="' + id + '">数字人口播</button>'
      : '';
    var publishAction = rec.task === 'moments_candidate' && recordImages(rec).length
      ? '<button type="button" data-record-action="publish-moments" data-record-id="' + id + '">发布到朋友圈</button>'
      : '';
    return '<details class="ip-draft-action-menu"><summary>操作</summary><div class="ip-draft-action-list">' +
      creationActions +
      publishAction +
      '<button type="button" class="is-danger" data-record-action="delete" data-record-id="' + id + '">删除</button>' +
      '</div></details>';
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
      var oral = isOralTask(rec.task);
      var oralChecked = oral && state.selectedRecordIds[String(rec.record_id || '')] ? ' checked' : '';
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
        '<div class="ip-draft-card-head"><div class="ip-badge-row">' +
        (oral ? '<label class="ip-draft-select"><input type="checkbox" data-record-select="' + escAttr(rec.record_id || '') + '"' + oralChecked + '>选择</label>' : '') +
        '<span class="ip-badge">' + esc(taskLabel(rec.task)) + '</span>' + (rec.image_url ? '<span class="ip-badge is-image">已出图</span>' : '') + '</div>' +
        '<div class="ip-draft-top-actions">' +
        (oral ? '<button type="button" class="btn btn-ghost btn-sm" data-copy-record="' + escAttr(rec.record_id || '') + '">复制</button>' : '') +
        draftActionMenuHtml(rec) + '</div></div>' +
        '<strong>' + esc(rec.title || '未命名文案') + '</strong>' +
        '<textarea class="' + (isMoments ? 'ip-moments-copy-editor' : '') + '" data-record-copy="' + escAttr(rec.record_id || '') + '">' + esc(bodyText) + '</textarea>' +
        renderImagePrompts(rec) +
        image +
        (!oral ? '<div class="ip-content-item-actions"><button type="button" class="btn btn-ghost btn-sm" data-copy-record="' + escAttr(rec.record_id || '') + '">复制</button></div>' : '') +
        '</div>';
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
        var id = ta.getAttribute('data-record-copy') || '';
        var rec = records.find(function(item) { return String(item.record_id || '') === String(id); });
        if (rec) {
          rec.body = ta.value;
          rec.content = ta.value;
        }
        ta.style.height = 'auto';
        ta.style.height = Math.min(Math.max(ta.scrollHeight + 2, minH), maxH) + 'px';
      });
    });
    box.querySelectorAll('[data-moment-select]').forEach(function(input) {
      input.addEventListener('change', function() {
        var id = input.getAttribute('data-moment-select');
        records.forEach(function(rec) {
          if (String(rec.record_id) === String(id)) rec._selected = input.checked;
        });
      });
    });
    box.querySelectorAll('[data-record-select]').forEach(function(input) {
      input.addEventListener('change', function() {
        var id = input.getAttribute('data-record-select') || '';
        if (id) state.selectedRecordIds[id] = !!input.checked;
        updateRecordBulkToolbar();
      });
    });
    box.querySelectorAll('[data-record-action]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var id = btn.getAttribute('data-record-id') || '';
        var action = btn.getAttribute('data-record-action') || '';
        var rec = records.find(function(item) { return String(item.record_id || '') === String(id); });
        var details = btn.closest('details');
        if (details) details.removeAttribute('open');
        if (action === 'delete') {
          deleteDraftRecord(id);
          return;
        }
        var ta = box.querySelector('[data-record-copy="' + cssEscape(id) + '"]');
        if (rec && action === 'publish-moments') {
          openDraftMomentsPublish(rec, ta ? ta.value : '');
          return;
        }
        if (rec && rec.task === 'moments_candidate' && action === 'image') {
          if (ta) {
            rec.body = ta.value;
            rec.content = ta.value;
          }
          rec._selected = true;
          confirmMomentsImages([rec], btn, !!btn.closest('#ipMomentBatchResultModal'));
          return;
        }
        openDraftContentAction(rec, action, ta ? ta.value : '');
      });
    });
    updateRecordBulkToolbar();
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
        '<div class="ip-content-item-actions"><button type="button" class="btn btn-ghost btn-sm" data-delete-group="' + escAttr(group.group_id) + '">删除记录</button></div>' +
        '</div>';
    }).join('');
    list.querySelectorAll('[data-show-group]').forEach(function(item) {
      item.addEventListener('click', function(ev) {
        if (ev.target && ev.target.closest && ev.target.closest('[data-delete-group]')) return;
        state.activeGroupId = item.getAttribute('data-show-group') || '';
        renderDraftRecords();
      });
    });
    list.querySelectorAll('[data-delete-group]').forEach(function(btn) {
      btn.addEventListener('click', function(ev) {
        ev.stopPropagation();
        deleteDraftGroup(btn.getAttribute('data-delete-group') || '');
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
      updateRecordBulkToolbar();
      return;
    }
    state.latestDrafts = group.records.map(function(rec) {
      if (rec._selected === undefined) rec._selected = false;
      return rec;
    });
    if (title) title.textContent = taskLabel(group.task) + ' · ' + group.records.length + ' 条';
    if (imageBtn) imageBtn.style.display = group.task === 'moments_candidate' ? '' : 'none';
    renderDraftCards('ipLatestDraftList', state.latestDrafts, { selectable: group.task === 'moments_candidate' });
    updateRecordBulkToolbar();
  }

  function momentBatchStatusLabel(status) {
    if (status === 'running') return '执行中';
    if (status === 'done') return '已完成';
    if (status === 'failed') return '失败';
    return '待执行';
  }

  function momentBatchStatusClass(status) {
    if (status === 'running') return ' is-running';
    if (status === 'done') return ' is-done';
    if (status === 'failed') return ' is-failed';
    return '';
  }

  function momentBatchRecords(job) {
    if (!job) return [];
    if (Array.isArray(job.records) && job.records.length) return job.records;
    var group = (state.draftGroups || []).find(function(item) {
      return String(item.group_id || '') === String(job.group_id || '');
    });
    return group && Array.isArray(group.records) ? group.records : [];
  }

  function closeMomentBatchResult() {
    var modal = $('ipMomentBatchResultModal');
    if (modal) modal.hidden = true;
  }

  function openMomentBatchResult(batchId) {
    var job = findMomentBatchJob(batchId);
    if (!job) return;
    var records = momentBatchRecords(job);
    if (!records.length) {
      setMsg('这一批暂时没有可展示的文案，请刷新后重试。', true);
      return;
    }
    var title = $('ipMomentBatchResultTitle');
    var meta = $('ipMomentBatchResultMeta');
    if (title) title.textContent = '朋友圈文案 · ' + (job.label || '批次结果');
    if (meta) meta.textContent = records.length + ' 条 · ' + fmtTime(job.updated_at || job.created_at);
    records.forEach(function(rec) {
      if (rec._selected === undefined) rec._selected = false;
    });
    renderDraftCards('ipMomentBatchResultList', records, { selectable: true });
    var generateBtn = $('ipMomentBatchGenerateImagesBtn');
    if (generateBtn) generateBtn.dataset.batchId = String(batchId || '');
    var modal = $('ipMomentBatchResultModal');
    if (modal) modal.hidden = false;
  }

  function renderMomentBatchQueue() {
    var box = $('ipMomentBatchQueue');
    if (!box) return;
    var jobs = normalizeMomentBatchJobs(state.momentBatchJobs);
    state.momentBatchJobs = jobs;
    if (!jobs.length) {
      box.style.display = 'none';
      box.innerHTML = '';
      return;
    }
    box.style.display = 'grid';
    box.innerHTML = jobs.map(function(job) {
      var status = String(job.status || 'queued');
      var records = momentBatchRecords(job);
      var doneCount = status === 'done' ? (records.length || job.count || 0) : 0;
      var previews = records.slice(0, 2).map(function(rec, idx) {
        var title = String(rec.title || ('朋友圈文案 ' + (idx + 1))).trim();
        var body = String(rec.body || rec.content || '').replace(/\s+/g, ' ').trim();
        return '<div class="ip-moment-batch-preview"><strong>' + esc(title) + '</strong><span>' + esc(body.slice(0, 100)) + (body.length > 100 ? '...' : '') + '</span></div>';
      }).join('');
      var action = '';
      if (status === 'failed') {
        action = '<button type="button" class="btn btn-primary btn-sm" data-retry-moment-batch="' + escAttr(job.batch_id) + '">重试该批</button>';
      } else if (status === 'done') {
        action = '<button type="button" class="btn btn-ghost btn-sm" data-show-moment-batch="' + escAttr(job.batch_id) + '">查看完整结果</button>';
      } else if (status === 'running') {
        action = '<button type="button" class="btn btn-ghost btn-sm" disabled>执行中</button>';
      } else {
        action = '<button type="button" class="btn btn-ghost btn-sm" disabled>待执行</button>';
      }
      action += '<button type="button" class="btn btn-ghost btn-sm" data-delete-moment-batch="' + escAttr(job.batch_id) + '">删除记录</button>';
      return '<div class="ip-moment-batch-card' + momentBatchStatusClass(status) + '">' +
        '<div class="ip-badge-row"><span class="ip-badge">朋友圈文案</span><span class="ip-badge">' + esc(job.label) + '</span><span class="ip-badge is-image">' + esc(job.count) + '条</span><span class="ip-badge' + (status === 'failed' ? ' is-used' : (status === 'done' ? ' is-new' : '')) + '">' + esc(momentBatchStatusLabel(status)) + '</span></div>' +
        '<strong>' + esc(job.label) + ' / 共 ' + esc(job.batch_count) + ' 批</strong>' +
        (status === 'done' ? '<small>已生成 ' + esc(doneCount || job.count) + ' 条，可查看这一批结果。</small>' : '') +
        (previews ? '<div class="ip-moment-batch-previews">' + previews + '</div>' : '') +
        (status === 'failed' ? '<small class="ip-moment-batch-error">' + esc(job.error || '这一批生成失败，可以单独重试。') + '</small>' : '') +
        (status === 'running' ? '<small>当前批次正在云端生成，其它批次互不影响。</small>' : '') +
        '<div class="ip-moment-batch-actions">' + action + '</div>' +
        '</div>';
    }).join('');
    box.querySelectorAll('[data-retry-moment-batch]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        retryMomentBatchJob(btn.getAttribute('data-retry-moment-batch'));
      });
    });
    box.querySelectorAll('[data-show-moment-batch]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        openMomentBatchResult(btn.getAttribute('data-show-moment-batch'));
      });
    });
    box.querySelectorAll('[data-delete-moment-batch]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        deleteMomentBatchJob(btn.getAttribute('data-delete-moment-batch') || '');
      });
    });
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
        var failed = momentRecordFailed(rec);
        var errorText = failed ? momentRecordError(rec) : '';
        var failedIndex = failed ? momentRecordFailedIndex(rec) : 0;
        var bodyText = rec.body || rec.content || '';
        return '<div class="ip-draft-card">' +
          '<div class="ip-badge-row"><span class="ip-badge">朋友圈</span><span class="ip-badge is-image">图片 ' + esc(images.length) + '</span><span class="ip-badge' + (failed ? ' is-used' : '') + '">' + esc(failed ? '生成失败' : (status || '等待生成')) + '</span>' + (progress ? '<span class="ip-badge">进度 ' + esc(progress) + '</span>' : '') + '</div>' +
          '<strong>' + esc(rec.title || '未命名文案') + '</strong>' +
          '<div class="ip-moments-copy-preview" data-moment-image-copy="' + escAttr(rec.record_id || '') + '">' + esc(bodyText) + '</div>' +
          renderImagePrompts(rec) +
          (errorText ? '<div class="ip-moment-image-error"><strong>' + esc(failedIndex ? ('第 ' + failedIndex + ' 张图片生成失败') : '本条出图失败') + '</strong><span>' + esc(errorText) + '</span></div>' : '') +
          (images.length ? '<div class="ip-image-grid">' + images.slice(0, 3).map(function(img, idx) {
            var url = img.image_url || img.url || '';
            return '<div class="ip-image-tile"><img src="' + escAttr(url) + '" alt="朋友圈图片 ' + escAttr(idx + 1) + '">' +
              '<a class="btn btn-ghost btn-sm" href="' + escAttr(url) + '" target="_blank" rel="noopener">打开图片</a></div>';
          }).join('') + '</div>' : '<div class="ip-content-empty">' + esc(failed ? '本条文案未生成图片' : (status || '等待生成图片...')) + '</div>') +
          '<div class="ip-content-item-actions"><button type="button" class="btn btn-ghost btn-sm" data-copy-moment-image-record="' + escAttr(rec.record_id) + '">复制文案</button>' +
          (images.length ? '<button type="button" class="btn btn-primary btn-sm" data-publish-moment-image-record="' + escAttr(rec.record_id) + '">发布到朋友圈</button>' : '') +
          (failed ? '<button type="button" class="btn btn-primary btn-sm" data-retry-moment-image-record="' + escAttr(rec.record_id) + '">重新出图</button>' : '') + '</div>' +
          '</div>';
      }).join('');
    box.querySelectorAll('[data-copy-moment-image-record]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var id = btn.getAttribute('data-copy-moment-image-record');
        var rec = batch.records.find(function(item) { return String(item.record_id) === String(id); });
        copyText(rec ? (rec.body || rec.content || '') : '', btn);
      });
    });
    box.querySelectorAll('[data-retry-moment-image-record]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var id = btn.getAttribute('data-retry-moment-image-record');
        var rec = batch.records.find(function(item) { return String(item.record_id) === String(id); });
        if (!rec) return;
        rec._selected = true;
        confirmMomentsImages([rec], btn, false);
      });
    });
    box.querySelectorAll('[data-publish-moment-image-record]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var id = btn.getAttribute('data-publish-moment-image-record');
        var rec = batch.records.find(function(item) { return String(item.record_id) === String(id); });
        if (rec) openDraftMomentsPublish(rec, rec.body || rec.content || '');
      });
    });
  }

  function loadDraftRecords() {
    return cloudJson('/api/ip-content/draft-records?limit=120')
      .then(function(data) {
        state.draftRecords = Array.isArray(data.items) ? data.items : [];
        state.draftGroups = buildDraftGroups(state.draftRecords);
        renderMomentBatchQueue();
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

  function ensureActiveProfileTemplate() {
    var tpl = activeTemplate() || state.settingTemplates[0] || null;
    if (tpl) {
      if (!state.activeTemplateId) state.activeTemplateId = tpl.id;
      if ($('ipGenerateTemplateSelect')) $('ipGenerateTemplateSelect').value = tpl.id;
      renderCurrentProfileTemplateBox();
      return Promise.resolve(tpl);
    }
    return loadServerTemplates().then(function() {
      tpl = activeTemplate() || state.settingTemplates[0] || null;
      if (!tpl) throw new Error('请先到 IP人设定位 保存并启用模板。');
      return tpl;
    });
  }

  function generationPayload(extraId, count, opts) {
    opts = opts || {};
    return ensureActiveProfileTemplate().then(function(tpl) {
      state.activeTemplateId = tpl.id;
      applyTemplate(tpl);
      var language = currentTemplateLanguage();
      var targetLanguage = ipTemplateLanguageLabel(language);
      var extraNode = extraId ? $(extraId) : null;
      saveGenerationSettings();
      var keywordIds = cleanTemplateIds(state.templateKeywordIds, false);
      var competitorIds = cleanTemplateIds(state.templateCompetitorIds, false);
      if (opts.requireKeywords && !keywordIds.length) return Promise.reject(new Error('请先到 IP人设定位 给当前模板选择关键词。'));
      return selectedMemoryDocsWithContent().then(function(memoryDocs) {
        if (!memoryDocs.length) {
          var savedMemoryDocs = templateMemoryDocs(tpl);
          if (savedMemoryDocs.length) memoryDocs = savedMemoryDocs;
        }
        return {
          memory_docs: memoryDocs,
          keyword_ids: keywordIds,
          competitor_ids: competitorIds,
          language: language,
          target_language: targetLanguage,
          requirements: {
            language: language,
            target_language: targetLanguage,
            common: ipTemplateLanguageInstruction(language)
          },
          extra_requirements: textWithTemplateLanguage(((extraNode && extraNode.value) || '').trim(), language),
          count: count || 5,
          sync_before: false
        };
      });
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

  function createMomentBatchJobs(payload, total, batchSize) {
    total = Math.max(1, parseInt(total, 10) || 20);
    batchSize = Math.max(1, parseInt(batchSize, 10) || 5);
    var batchCount = Math.ceil(total / batchSize);
    var groupBase = 'moments_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8);
    var createdAt = new Date().toISOString();
    var jobs = [];
    for (var i = 0; i < batchCount; i += 1) {
      var count = Math.min(batchSize, total - i * batchSize);
      var groupId = groupBase + '_b' + (i + 1);
      var batchPayload = clonePayload(payload);
      batchPayload.count = count;
      batchPayload.group_id = groupId;
      batchPayload.sync_before = false;
      batchPayload.batch_id = groupId;
      batchPayload.batch_index = i + 1;
      batchPayload.batch_count = batchCount;
      jobs.push({
        batch_id: groupId,
        batch_index: i + 1,
        batch_count: batchCount,
        label: '第' + (i + 1) + '批',
        group_id: groupId,
        count: count,
        status: 'queued',
        error: '',
        records: [],
        payload: batchPayload,
        created_at: createdAt,
        updated_at: createdAt,
        retry_count: 0
      });
    }
    return jobs;
  }

  function findMomentBatchJob(batchId) {
    batchId = String(batchId || '');
    return (state.momentBatchJobs || []).find(function(job) {
      return String(job.batch_id || '') === batchId;
    }) || null;
  }

  function updateMomentBatchJob(batchId, patch) {
    var job = findMomentBatchJob(batchId);
    if (!job) return null;
    Object.assign(job, patch || {}, { updated_at: new Date().toISOString() });
    state.momentBatchJobs = normalizeMomentBatchJobs(state.momentBatchJobs);
    saveMomentBatchJobs();
    renderMomentBatchQueue();
    return findMomentBatchJob(batchId);
  }

  function runMomentBatchJob(job) {
    job = job ? findMomentBatchJob(job.batch_id) || job : null;
    if (!job) return Promise.reject(new Error('未找到要重试的批次'));
    var payload = clonePayload(job.payload || {});
    payload.count = job.count || payload.count || 5;
    payload.group_id = job.group_id || payload.group_id || job.batch_id;
    payload.sync_before = false;
    payload.batch_id = job.batch_id;
    payload.batch_index = job.batch_index;
    payload.batch_count = job.batch_count;
    updateMomentBatchJob(job.batch_id, { status: 'running', error: '', payload: payload, records: [] });
    setMsg('正在生成朋友圈文案：' + (job.label || '当前批次') + '...');
    return postWithRetry('/api/ip-content/generate/moments-candidates', payload, 1, (job.label || '当前批次') + '生成失败')
      .then(function(data) {
        var records = Array.isArray(data.records) ? data.records : [];
        records.forEach(function(rec) {
          rec.group_id = rec.group_id || data.group_id || payload.group_id;
          rec.meta = Object.assign({}, rec.meta || {}, { group_id: rec.group_id });
        });
        updateMomentBatchJob(job.batch_id, {
          status: 'done',
          error: '',
          group_id: data.group_id || payload.group_id,
          records: records
        });
        if (records.length) {
          syncMomentBatchRecords(records);
          renderDraftRecords();
        }
        return Object.assign({}, data || {}, { records: records });
      })
      .catch(function(err) {
        updateMomentBatchJob(job.batch_id, {
          status: 'failed',
          error: err && err.message ? err.message : '这一批生成失败'
        });
        throw err;
      });
  }

  function deleteMomentBatchJob(batchId) {
    var job = findMomentBatchJob(batchId);
    if (!job || !confirm('删除这条批次记录？已生成的文案也会一起删除。')) return;
    var groupId = String(job.group_id || '');
    var removeRemote = groupId
      ? cloudJson('/api/ip-content/draft-record-groups/' + encodeURIComponent(groupId), { method: 'DELETE', json: false })
          .catch(function(err) {
            if (/生成批次不存在|404/.test(String(err && err.message || ''))) return { ok: true, deleted: 0 };
            throw err;
          })
      : Promise.resolve({ ok: true, deleted: 0 });
    removeRemote.then(function() {
      (job.records || []).forEach(function(rec) {
        if (rec && rec.record_id) delete state.selectedRecordIds[String(rec.record_id)];
      });
      state.momentBatchJobs = (state.momentBatchJobs || []).filter(function(item) {
        return String(item.batch_id || '') !== String(batchId || '');
      });
      saveMomentBatchJobs();
      renderMomentBatchQueue();
      setMsg('批次记录已删除。');
      return loadDraftRecords();
    }).catch(function(err) {
      setMsg(err.message || '批次记录删除失败', true);
    });
  }

  function retryMomentBatchJob(batchId) {
    var job = findMomentBatchJob(batchId);
    if (!job) {
      setMsg('未找到要重试的批次。', true);
      return;
    }
    job.retry_count = (parseInt(job.retry_count, 10) || 0) + 1;
    saveMomentBatchJobs();
    runMomentBatchJob(job)
      .then(function() {
        state.recordFilter = 'moments_candidate';
        state.activeGroupId = job.group_id;
        return loadDraftRecords().then(function() {
          setRecordFilter('moments_candidate');
          selectMomentBatchGroup(job.group_id);
          setMsg((job.label || '当前批次') + '已重试成功。');
        });
      })
      .catch(function(err) {
        setMsg((err && err.message) || '这一批重试失败', true);
      });
  }

  function runMomentsGenerate(btn, successTab) {
    setBusy(btn, true, '生成中...');
    setMsg('正在拆成 4 个独立批次生成朋友圈文案，单批失败不会影响其它批次。');
    generationPayload('ipTask2Extra', 20, {})
      .then(function(payload) {
        var total = 20;
        var batchSize = 5;
        var jobs = createMomentBatchJobs(payload, total, batchSize);
        state.momentBatchJobs = jobs;
        saveMomentBatchJobs();
        renderMomentBatchQueue();
        switchTab(successTab || 'records');
        var runners = jobs.map(function(job, index) {
          return delay(index * 1200).then(function() {
            return runMomentBatchJob(job);
          });
        });
        return Promise.allSettled(runners).then(function(results) {
          return { results: results, jobs: normalizeMomentBatchJobs(state.momentBatchJobs) };
        });
      })
      .then(function(ctx) {
        var jobs = ctx.jobs || [];
        var done = jobs.filter(function(job) { return job.status === 'done'; });
        var failed = jobs.filter(function(job) { return job.status === 'failed'; });
        var batchCount = jobs.length || 4;
        if (done.length) {
          state.activeGroupId = done[0].group_id;
          state.recordFilter = 'moments_candidate';
        }
        return Promise.all([loadDraftRecords(), loadSources()]).then(function() {
          if (done.length) {
            setRecordFilter('moments_candidate');
            switchTab(successTab || 'records');
          }
          if (failed.length) {
            if (done.length) {
              setMsg('朋友圈文案已完成 ' + done.length + '/' + batchCount + ' 批，失败 ' + failed.length + ' 批，可在批次卡片里单独重试。', true);
            } else {
              setMsg('朋友圈文案 ' + batchCount + ' 批全部失败，可在批次卡片里单独重试。', true);
            }
          } else {
            setMsg('朋友圈文案 ' + batchCount + ' 个批次已全部生成完成。');
          }
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

  function confirmMomentsImages(records, triggerButton, closeResultModal) {
    var sourceRecords = Array.isArray(records) ? records : state.latestDrafts;
    var selected = sourceRecords.filter(function(rec) { return rec.task === 'moments_candidate' && !!rec._selected && !momentRecordDone(rec); });
    if (!selected.length) {
      setMsg('请先勾选要出图的朋友圈文案。', true);
      return;
    }
    if (selected.length > 5) {
      setMsg('一次最多选择 5 条出图。', true);
      return;
    }
    var btn = triggerButton || $('ipGenerateSelectedImagesBtn');
    if (closeResultModal) closeMomentBatchResult();
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
    saveGenerationSettings();
    localJson('/api/ip-content/moments/images/generate', {
      method: 'POST',
      body: {
        batch_id: batchId,
        batch_created_at: batchCreatedAt,
        image_extra: textWithTemplateLanguage((($('ipImageExtra') && $('ipImageExtra').value) || '').trim(), currentTemplateLanguage()),
        records: selected.map(function(rec) {
          return {
            record_id: rec.record_id,
            title: rec.title || '朋友圈配图',
            body: rec.body || rec.content || '',
            image_prompt: rec.image_prompt || '',
            image_prompts: recordImagePrompts(rec),
            memory_doc_ids: selectedMemoryIdsForRecord(rec)
          };
        })
      }
    }).then(function(data) {
      var responseRecords = Array.isArray(data.records) ? data.records : [];
      selected.forEach(function(rec) {
        var item = responseRecords.find(function(row) { return String(row.record_id || '') === String(rec.record_id || ''); });
        if (!item) item = { status: 'failed', error: '图片服务未返回当前文案的生成结果', images: recordImages(rec) };
        rec.images = item.images || [];
        rec.image_url = rec.images[0] && rec.images[0].image_url || rec.image_url || '';
        rec.image_asset_id = rec.images[0] && rec.images[0].image_asset_id || rec.image_asset_id || '';
        rec._selected = false;
        rec._image_error = String(item.error || '').trim();
        rec._image_failed_index = Number(item.failed_index || 0);
        rec._image_status = item.status === 'failed' || rec._image_error
          ? '生成失败：' + (rec._image_error || '图片生成失败')
          : rec.images.length + ' 张图片已生成';
        rec._image_progress = item.image_progress || (rec.images.length + '/3');
        rec.meta = Object.assign({}, rec.meta || {}, {
          image_batch_id: batchId,
          image_batch_created_at: batchCreatedAt,
          image_status: rec._image_status,
          image_progress: rec._image_progress,
          image_error: rec._image_error,
          image_failed_index: rec._image_failed_index,
          images: rec.images
        });
      });
      refreshMomentBatchProgress(selected);
      var failedCount = Number(data.failed_count);
      if (!Number.isFinite(failedCount)) failedCount = selected.filter(momentRecordFailed).length;
      var completedCount = Number(data.completed_count);
      if (!Number.isFinite(completedCount)) completedCount = selected.length - failedCount;
      if (failedCount) {
        setMsg('本轮出图结束：成功 ' + completedCount + ' 条，失败 ' + failedCount + ' 条。失败原因已标在对应文案，可单独重试。');
      } else {
        setMsg('选中的朋友圈文案已各生成 3 张图片并回写生成记录。');
      }
      state.activeMomentImageBatchId = batchId;
      return loadDraftRecords().then(function() { switchTab('moment-images'); });
    }).catch(function(err) {
      selected.forEach(function(rec) {
        if (!momentRecordDone(rec)) {
          rec._image_status = '生成失败：' + (err.message || '图片生成失败');
          rec._image_error = err.message || '图片生成失败';
          rec.meta = Object.assign({}, rec.meta || {}, { image_status: rec._image_status, image_progress: rec._image_progress || '0/3', image_error: rec._image_error });
          persistMomentRecordProgress(rec, recordImages(rec), batchId, batchCreatedAt);
        }
      });
      refreshMomentBatchProgress(selected);
      setMsg('本轮出图请求中断，失败原因已标在对应文案，可单独重试。');
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
    if ($('ipOpenPersonalSettingsBtn')) $('ipOpenPersonalSettingsBtn').addEventListener('click', function() {
      if (typeof showView === 'function') showView('personal-settings');
      else if (typeof window.showLobsterView === 'function') window.showLobsterView('personal-settings');
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
      runGenerate($('ipGenerateIpBtn'), '/api/ip-content/generate/professional-ip-oral', 'ipTask1Extra', 5, 'records', {});
    });
    if ($('ipGenerateMomentsBtn')) $('ipGenerateMomentsBtn').addEventListener('click', function() {
      runMomentsGenerate($('ipGenerateMomentsBtn'), 'records');
    });
    if ($('ipGenerateSelectedImagesBtn')) $('ipGenerateSelectedImagesBtn').addEventListener('click', confirmMomentsImages);
    if ($('ipMomentBatchGenerateImagesBtn')) $('ipMomentBatchGenerateImagesBtn').addEventListener('click', function() {
      var batchId = $('ipMomentBatchGenerateImagesBtn').dataset.batchId || '';
      var job = findMomentBatchJob(batchId);
      confirmMomentsImages(momentBatchRecords(job), $('ipMomentBatchGenerateImagesBtn'), true);
    });
    if ($('ipRecordSelectAll')) $('ipRecordSelectAll').addEventListener('change', function() {
      var checked = !!$('ipRecordSelectAll').checked;
      (state.latestDrafts || []).forEach(function(rec) {
        if (isOralTask(rec.task) && rec.record_id) state.selectedRecordIds[String(rec.record_id)] = checked;
      });
      document.querySelectorAll('#ipLatestDraftList [data-record-select]').forEach(function(input) {
        input.checked = checked;
      });
      updateRecordBulkToolbar();
    });
    if ($('ipCopySelectedRecordsBtn')) $('ipCopySelectedRecordsBtn').addEventListener('click', copySelectedDraftRecords);
    if ($('ipMomentBatchResultCloseBtn')) $('ipMomentBatchResultCloseBtn').addEventListener('click', closeMomentBatchResult);
    if ($('ipMomentBatchResultModal')) $('ipMomentBatchResultModal').addEventListener('click', function(ev) {
      if (ev.target === $('ipMomentBatchResultModal')) closeMomentBatchResult();
    });
  }

  window.initIpContentStudioView = function() {
    bind();
    restoreGenerationSettings();
    restoreMomentBatchJobs();
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
