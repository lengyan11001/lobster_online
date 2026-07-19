(function() {
  var state = {
    tab: 'keywords',
    keywords: [],
    competitors: [],
    competitorCandidates: [],
    memories: [],
    templates: [],
    editingTemplateId: '',
    profileIndex: 0,
    selectedKeywords: {},
    selectedCompetitors: {},
    selectedMemories: {},
    selectedReferenceMemories: {},
    memoryUseProfile: true,
    memorySourceKeywords: {},
    memorySourceCompetitors: {},
    memorySourceDocs: {},
    memorySourceFiles: {},
    generatedDocuments: {},
    generatedDocOrder: [],
    uploadFiles: [],
    customReferenceFile: null,
    defaultItem: null
  };

  var DOC_TYPES = [
    { key: 'brand_product_intro', label: '产品介绍' },
    { key: 'product_service_faq', label: '百问百答' },
    { key: 'short_video_scripts', label: '短视频口播稿' }
  ];

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
    if (/^Bearer\s*$/i.test(String(h.Authorization || h.authorization || '').trim())) {
      delete h.Authorization;
      delete h.authorization;
    }
    if (!h.Authorization && !h.authorization && typeof token !== 'undefined' && token) h.Authorization = 'Bearer ' + token;
    if (typeof getOrCreateInstallationId === 'function') h['X-Installation-Id'] = getOrCreateInstallationId();
    if (json === false) {
      delete h['Content-Type'];
      delete h['content-type'];
    } else {
      h['Content-Type'] = 'application/json';
    }
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

  function syncOpenClawMemoryFromCloud() {
    if (!localBase()) return Promise.resolve({ ok: false, skipped: 'LOCAL_API_BASE not configured' });
    return localJson('/api/openclaw/memory/sync-cloud', { method: 'POST', json: false }).catch(function(err) {
      console.warn('[personal-settings] sync OpenClaw memory failed', err);
      return { ok: false, error: err && err.message ? err.message : String(err || '') };
    });
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
    var allowed = ['keywords', 'competitors', 'profile', 'upload', 'memory', 'template'];
    state.tab = allowed.indexOf(String(tab || '')) >= 0 ? String(tab || '') : 'keywords';
    document.querySelectorAll('#content-personal-settings [data-ps-tab]').forEach(function(btn) {
      btn.classList.toggle('is-active', btn.getAttribute('data-ps-tab') === state.tab);
    });
    document.querySelectorAll('#content-personal-settings [data-ps-panel]').forEach(function(panel) {
      panel.classList.toggle('is-active', panel.getAttribute('data-ps-panel') === state.tab);
    });
    if (state.tab === 'profile') renderProfileWizard();
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

  function uniqueIds(ids) {
    var seen = {};
    return (ids || []).map(function(id) { return String(id || '').trim(); }).filter(function(id) {
      if (!id || seen[id]) return false;
      seen[id] = true;
      return true;
    });
  }

  function fieldValue(id) {
    var el = $(id);
    return el ? String(el.value || '').trim() : '';
  }

  function setFieldValue(id, value) {
    var el = $(id);
    if (el) el.value = value || '';
  }

  function profileQuestions() {
    return [
      { field: 'psProfileName', label: '你的名字', type: 'input' },
      { field: 'psBirthEra', label: '哪个年代出生', type: 'input' },
      { field: 'psCurrentCity', label: '现居城市', type: 'input' },
      { field: 'psHometown', label: '籍贯', type: 'input' },
      { field: 'psRole', label: '你是做什么的', type: 'input' },
      { field: 'psShareTopic', label: '你主要分享什么', type: 'input' },
      { field: 'psVideoStyle', label: '你希望视频是什么风格', type: 'input' },
      { field: 'psAfterViewAction', label: '希望大家看完后做什么', type: 'input' },
      { field: 'psBusinessProduct', label: '你在做什么/什么产品', type: 'textarea' },
      { field: 'psTargetCustomer', label: '你想卖给谁/哪些年代的人', type: 'textarea' },
      { field: 'psAdvantages', label: '你的优势/比同行好在哪', type: 'textarea' }
    ];
  }

  function syncProfileAnswerToField() {
    var questions = profileQuestions();
    var idx = Math.max(0, Math.min(Number(state.profileIndex || 0), questions.length - 1));
    var question = questions[idx];
    var answer = $('psProfileAnswer');
    if (question && answer) setFieldValue(question.field, answer.value || '');
  }

  function renderProfileWizard() {
    var host = $('psProfileAnswerHost');
    var title = $('psProfileQuestionTitle');
    if (!host || !title) return;
    var questions = profileQuestions();
    var maxIdx = Math.max(0, questions.length - 1);
    var idx = Math.max(0, Math.min(Number(state.profileIndex || 0), maxIdx));
    state.profileIndex = idx;
    var question = questions[idx];
    title.textContent = question.label;
    var step = $('psProfileStepText');
    var progress = $('psProfileProgress');
    if (step) step.textContent = (idx + 1) + '/' + questions.length;
    if (progress) progress.style.width = Math.round(((idx + 1) / questions.length) * 100) + '%';
    host.innerHTML = question.type === 'textarea'
      ? '<textarea id="psProfileAnswer" rows="5"></textarea>'
      : '<input id="psProfileAnswer" type="text">';
    var answer = $('psProfileAnswer');
    if (answer) {
      answer.value = fieldValue(question.field);
      answer.addEventListener('input', syncProfileAnswerToField);
      setTimeout(function() { answer.focus(); }, 0);
    }
    if ($('psProfilePrevBtn')) $('psProfilePrevBtn').disabled = idx <= 0;
    if ($('psProfileNextBtn')) $('psProfileNextBtn').hidden = idx >= maxIdx;
    if ($('psSaveProfileBtn')) $('psSaveProfileBtn').hidden = idx < maxIdx;
  }

  function moveProfile(delta) {
    syncProfileAnswerToField();
    var questions = profileQuestions();
    var maxIdx = Math.max(0, questions.length - 1);
    state.profileIndex = Math.max(0, Math.min(Number(state.profileIndex || 0) + delta, maxIdx));
    renderProfileWizard();
  }

  function profileRequirements() {
    var basic = {
      name: fieldValue('psProfileName'),
      birth_era: fieldValue('psBirthEra'),
      current_city: fieldValue('psCurrentCity'),
      hometown: fieldValue('psHometown'),
      role: fieldValue('psRole'),
      share_topic: fieldValue('psShareTopic'),
      video_style: fieldValue('psVideoStyle'),
      after_view_action: fieldValue('psAfterViewAction')
    };
    var business = {
      product: fieldValue('psBusinessProduct'),
      target_customer: fieldValue('psTargetCustomer'),
      advantages: fieldValue('psAdvantages')
    };
    var lines = [
      ['名字', basic.name],
      ['出生年代', basic.birth_era],
      ['现居城市', basic.current_city],
      ['籍贯', basic.hometown],
      ['职业/身份', basic.role],
      ['主要分享', basic.share_topic],
      ['视频风格', basic.video_style],
      ['看完后动作', basic.after_view_action],
      ['产品/业务', business.product],
      ['目标客户', business.target_customer],
      ['优势', business.advantages]
    ].filter(function(item) { return String(item[1] || '').trim(); }).map(function(item) { return item[0] + '：' + item[1]; });
    var text = lines.join('\n');
    return {
      basic_profile: basic,
      business_description: business,
      profile_name: basic.name,
      birth_era: basic.birth_era,
      current_city: basic.current_city,
      hometown: basic.hometown,
      role: basic.role,
      share_topic: basic.share_topic,
      video_style: basic.video_style,
      after_view_action: basic.after_view_action,
      product: business.product,
      target_customer: business.target_customer,
      advantages: business.advantages,
      common: text,
      oral: text,
      industry_oral: text,
      ip_oral: text,
      moments: text,
      moments_copy: text,
      image: text
    };
  }

  function fillProfileFields(item) {
    var req = (item && item.requirements) || {};
    var profile = req.basic_profile && typeof req.basic_profile === 'object' ? req.basic_profile : (req.profile || {});
    var business = req.business_description && typeof req.business_description === 'object' ? req.business_description : (req.business || {});
    setFieldValue('psProfileName', req.profile_name || profile.name || '');
    setFieldValue('psBirthEra', req.birth_era || profile.birth_era || '');
    setFieldValue('psCurrentCity', req.current_city || profile.current_city || '');
    setFieldValue('psHometown', req.hometown || profile.hometown || '');
    setFieldValue('psRole', req.role || profile.role || '');
    setFieldValue('psShareTopic', req.share_topic || profile.share_topic || '');
    setFieldValue('psVideoStyle', req.video_style || profile.video_style || '');
    setFieldValue('psAfterViewAction', req.after_view_action || profile.after_view_action || '');
    setFieldValue('psBusinessProduct', req.product || business.product || '');
    setFieldValue('psTargetCustomer', req.target_customer || business.target_customer || '');
    setFieldValue('psAdvantages', req.advantages || business.advantages || '');
    renderProfileWizard();
  }

  function profileContextText(options) {
    options = options || {};
    var includeProfile = options.includeProfile !== false;
    var keywordRows = Array.isArray(options.keywordRows) ? options.keywordRows : selectedMemoryKeywordRows();
    var competitorRows = Array.isArray(options.competitorRows) ? options.competitorRows : selectedMemoryCompetitorRows();
    var sourceDocs = Array.isArray(options.sourceDocs) ? options.sourceDocs : selectedMemorySourceDocs();
    var req = profileRequirements();
    var keywordLines = keywordRows.map(function(row) { return row.display_name || row.keyword; }).filter(Boolean);
    var competitorLines = competitorRows.map(function(row) {
      return [platformLabel(row.platform), row.display_name || row.account_key || ''].filter(Boolean).join(' ');
    }).filter(Boolean);
    var docLines = sourceDocs.map(function(doc) {
      var title = memoryTitle(doc);
      var text = String(doc.content_text || doc.content || doc.text || doc.content_preview || '').trim();
      return text ? '【' + title + '】\n' + text : '';
    }).filter(Boolean);
    var sections = [];
    if (includeProfile && req.common) sections.push('资料调查：\n' + req.common);
    if (keywordLines.length) sections.push('关键词：\n' + keywordLines.join('\n'));
    if (competitorLines.length) sections.push('同行账号：\n' + competitorLines.join('\n'));
    if (docLines.length) sections.push('上传资料：\n' + docLines.join('\n\n'));
    return sections.join('\n\n').trim();
  }

  function metricText(metrics) {
    if (!metrics || typeof metrics !== 'object') return '';
    return [
      ['点赞', metrics.like_count || metrics.digg_count || metrics.likes],
      ['评论', metrics.comment_count || metrics.comments],
      ['分享', metrics.share_count || metrics.shares],
      ['收藏', metrics.collect_count || metrics.favorite_count || metrics.favorites],
      ['播放', metrics.play_count || metrics.view_count || metrics.views]
    ].filter(function(item) { return item[1] !== undefined && item[1] !== null && String(item[1]) !== ''; })
      .map(function(item) { return item[0] + item[1]; })
      .join('，');
  }

  function competitorSourceText(selectedIds) {
    var selected = (Array.isArray(selectedIds) ? selectedIds : cleanIntIds(state.memorySourceCompetitors)).map(function(id) { return String(id); });
    if (!selected.length) return Promise.resolve('');
    var wanted = {};
    selected.forEach(function(id) { wanted[id] = true; });
    return cloudJson('/api/ip-content/source-items?source_type=competitor&limit=80')
      .then(function(data) {
        var rows = (Array.isArray(data.items) ? data.items : []).filter(function(row) {
          var meta = row && row.source_meta && typeof row.source_meta === 'object' ? row.source_meta : {};
          var cid = String(meta.competitor_account_id || '');
          return !!wanted[cid];
        }).slice(0, 40);
        if (!rows.length) return '';
        return '同行同步数据：\n' + rows.map(function(row, idx) {
          var metrics = metricText(row.metrics || {});
          return [
            (idx + 1) + '. ' + [row.author_name || '', row.title ? '《' + row.title + '》' : ''].filter(Boolean).join(' '),
            row.description ? '内容：' + row.description : '',
            row.publish_time ? '时间：' + row.publish_time : '',
            metrics ? '数据：' + metrics : '',
            row.public_url ? '链接：' + row.public_url : ''
          ].filter(Boolean).join('\n');
        }).join('\n\n');
      })
      .catch(function() { return ''; });
  }

  function selectedMemoryDocs() {
    var ids = cleanStringIds(state.selectedMemories);
    return state.memories.filter(function(doc) { return ids.indexOf(memoryId(doc)) >= 0; });
  }

  function selectedReferenceMemoryIds() {
    return cleanStringIds(state.selectedReferenceMemories);
  }

  var localPreviewUrls = typeof WeakMap !== 'undefined' ? new WeakMap() : null;

  function filePreviewUrl(file) {
    if (!file || !file.type || !/^(image|video)\//i.test(file.type) || !window.URL || !URL.createObjectURL) return '';
    if (localPreviewUrls && localPreviewUrls.has(file)) return localPreviewUrls.get(file);
    var url = URL.createObjectURL(file);
    if (localPreviewUrls) localPreviewUrls.set(file, url);
    return url;
  }

  function filePreviewHtml(file) {
    var type = String(file && file.type || '');
    var url = filePreviewUrl(file);
    if (url && /^image\//i.test(type)) return '<img src="' + esc(url) + '" alt="">';
    if (url && /^video\//i.test(type)) return '<video src="' + esc(url) + '" muted playsinline preload="metadata"></video>';
    var suffix = String((file && file.name || 'FILE').split('.').pop() || 'FILE').slice(0, 5).toUpperCase();
    return '<span>' + esc(suffix) + '</span>';
  }

  function hasVisualPreview(file) {
    return !!(file && file.type && /^(image|video)\//i.test(file.type));
  }

  function fileChipHtml(file, removeAttr, fallbackName) {
    var size = file.size ? ' · ' + Math.ceil(file.size / 1024) + 'KB' : '';
    var metaHtml = hasVisualPreview(file)
      ? ''
      : '<div class="ps-file-meta"><span>' + esc(file.name || fallbackName || '未命名文件') + esc(size) + '</span></div>';
    return '<div class="ps-file-chip">' +
      '<div class="ps-file-thumb">' + filePreviewHtml(file) + '</div>' +
      metaHtml +
      '<button type="button" ' + removeAttr + '>移除</button>' +
    '</div>';
  }

  function selectedUploadFiles() {
    var input = $('psMemoryFiles');
    var files = state.uploadFiles && state.uploadFiles.length ? state.uploadFiles : (input && input.files ? input.files : []);
    return Array.prototype.filter.call(files, function(file) {
      return file && (file.name || file.size > 0);
    });
  }

  function uploadFileKey(file) {
    return [
      file && file.name || '',
      file && file.size || 0,
      file && file.lastModified || 0,
      file && file.type || ''
    ].join('|');
  }

  function handleUploadFileChange() {
    var input = $('psMemoryFiles');
    var picked = input && input.files ? Array.prototype.slice.call(input.files) : [];
    if (!picked.length) {
      renderSelectedFiles();
      return;
    }
    var seen = {};
    state.uploadFiles = selectedUploadFiles().concat(picked).filter(function(file) {
      var key = uploadFileKey(file);
      if (!key || seen[key]) return false;
      seen[key] = true;
      return true;
    });
    if (input) input.value = '';
    renderSelectedFiles();
    renderMemorySourceSelectors();
  }

  function removeUploadFile(index) {
    state.uploadFiles = selectedUploadFiles().filter(function(_file, idx) {
      return idx !== index;
    });
    renderSelectedFiles();
    renderMemorySourceSelectors();
  }

  function docTypeLabel(key) {
    var row = DOC_TYPES.find(function(item) { return item.key === key; });
    if (key === 'custom_memory') return '自定义参考文档';
    return row ? row.label : key;
  }

  function recommendMemoryTitle(docTypes, hasCustomReference) {
    var keys = Array.isArray(docTypes) ? docTypes.filter(Boolean) : [];
    if (keys.length === 1 && keys[0] === 'custom_memory') return '自定义记忆';
    if (keys.length === 1) return docTypeLabel(keys[0]);
    if (!keys.length && hasCustomReference) return '自定义记忆';
    return 'IP人设记忆';
  }

  function selectedCustomReferenceFile() {
    return state.customReferenceFile || null;
  }

  function handleCustomReferenceFileChange() {
    var input = $('psCustomReferenceFile');
    var file = input && input.files && input.files[0] ? input.files[0] : null;
    state.customReferenceFile = file && (file.name || file.size > 0) ? file : null;
    if (input) input.value = '';
    renderCustomReferenceFile();
  }

  function removeCustomReferenceFile() {
    state.customReferenceFile = null;
    renderCustomReferenceFile();
  }

  function renderCustomReferenceFile() {
    var box = $('psCustomReferenceFileInfo');
    if (!box) return;
    var file = selectedCustomReferenceFile();
    if (!file) {
      box.innerHTML = '';
      return;
    }
    box.innerHTML = fileChipHtml(file, 'data-remove-custom-reference', '参考文档');
    var btn = box.querySelector('[data-remove-custom-reference]');
    if (btn) btn.addEventListener('click', removeCustomReferenceFile);
  }

  function selectedGenerateDocTypes() {
    var values = [];
    document.querySelectorAll('#psGenerateDocTypes [data-ps-doc-type]').forEach(function(input) {
      if (input.checked) values.push(input.value);
    });
    return values;
  }

  function renderSelectedFiles() {
    var box = $('psSelectedFiles');
    if (!box) return;
    var files = selectedUploadFiles();
    if (!files.length) {
      box.innerHTML = '';
      return;
    }
    box.innerHTML = files.map(function(file, idx) {
      return fileChipHtml(file, 'data-remove-upload-file="' + idx + '"', '未命名文件');
    }).join('');
    box.querySelectorAll('[data-remove-upload-file]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        removeUploadFile(parseInt(btn.getAttribute('data-remove-upload-file') || '-1', 10));
      });
    });
  }

  function formatGeneratedDocs(docs, order) {
    docs = docs || {};
    order = order && order.length ? order : DOC_TYPES.map(function(item) { return item.key; }).concat(['custom_memory']);
    return order.map(function(key) {
      var text = String(docs[key] || '').trim();
      return text ? '# ' + docTypeLabel(key) + '\n\n' + text : '';
    }).filter(Boolean).join('\n\n---\n\n').trim();
  }

  function generatedDocsFromUi() {
    var docs = {};
    var order = [];
    document.querySelectorAll('[data-ps-generated-text]').forEach(function(textarea) {
      var key = textarea.getAttribute('data-ps-generated-text') || '';
      var keep = document.querySelector('[data-ps-save-doc="' + key + '"]');
      var text = String(textarea.value || '').trim();
      if (key && text && (!keep || keep.checked)) {
        docs[key] = text;
        order.push(key);
      }
    });
    return { documents: docs, order: order };
  }

  function renderGeneratedDocs() {
    var box = $('psGeneratedDocList');
    if (!box) return;
    var docs = state.generatedDocuments || {};
    var order = state.generatedDocOrder && state.generatedDocOrder.length
      ? state.generatedDocOrder
      : Object.keys(docs);
    order = order.filter(function(key) { return docs[key]; });
    if (!order.length) {
      box.innerHTML = '<div class="ps-empty">选择资料和生成类型后，点击“AI 理解”生成预览。</div>';
      if ($('psMemoryReviewText')) $('psMemoryReviewText').value = '';
      return;
    }
    box.innerHTML = order.map(function(key) {
      return '<article class="ps-generated-doc">' +
        '<div class="ps-generated-head">' +
          '<strong>' + esc(docTypeLabel(key)) + '</strong>' +
          '<label class="ps-choice"><input type="checkbox" data-ps-save-doc="' + escAttr(key) + '" checked><span>保存这个结果</span></label>' +
        '</div>' +
        '<textarea data-ps-generated-text="' + escAttr(key) + '">' + esc(docs[key]) + '</textarea>' +
      '</article>';
    }).join('');
    box.querySelectorAll('[data-ps-generated-text]').forEach(function(textarea) {
      textarea.addEventListener('input', function() {
        var key = textarea.getAttribute('data-ps-generated-text') || '';
        if (key) state.generatedDocuments[key] = textarea.value || '';
        if ($('psMemoryReviewText')) $('psMemoryReviewText').value = formatGeneratedDocs(state.generatedDocuments, state.generatedDocOrder);
      });
    });
    if ($('psMemoryReviewText')) $('psMemoryReviewText').value = formatGeneratedDocs(docs, order);
  }

  function fetchMemoryContent(doc) {
    var id = memoryId(doc);
    if (!id) return Promise.resolve(doc);
    if (doc.content_text || doc.content || doc.text) return Promise.resolve(doc);
    return cloudJson('/api/personal-settings/memory-documents/' + encodeURIComponent(id) + '/preview', { json: false })
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

  function isPersonalDefaultTemplate(row) {
    var meta = row && row.meta && typeof row.meta === 'object' ? row.meta : {};
    return !!meta.is_personal_default || String((row && row.name) || '') === '个人默认配置';
  }

  function templateName(row) {
    return String((row && row.name) || '').trim() || '未命名模板';
  }

  function renderCurrentTemplate() {
    var box = $('psCurrentTemplateBox');
    if (!box) return;
    var current = state.defaultItem || {};
    var keywordCount = Array.isArray(current.keyword_ids) ? current.keyword_ids.length : 0;
    var competitorCount = Array.isArray(current.competitor_ids) ? current.competitor_ids.length : 0;
    var memoryCount = Array.isArray(current.memory_doc_ids) ? current.memory_doc_ids.length : 0;
    var meta = current.meta && typeof current.meta === 'object' ? current.meta : {};
    var sourceId = String(meta.current_template_id || '').trim();
    var sourceTemplate = sourceId ? (state.templates || []).find(function(row) { return String(row.id || '') === sourceId; }) : null;
    var title = sourceTemplate ? templateName(sourceTemplate) : (current.name && !isPersonalDefaultTemplate(current) ? templateName(current) : '未指定模板');
    box.innerHTML = '<article class="ps-template-card">' +
      '<div><strong>' + esc(title) + '</strong><div class="ps-template-meta">关键词 ' + keywordCount + ' · 同行 ' + competitorCount + ' · 记忆 ' + memoryCount + '</div></div>' +
    '</article>';
  }

  function renderSavedTemplates() {
    var list = $('psSavedTemplateList');
    if (!list) return;
    var rows = Array.isArray(state.templates) ? state.templates : [];
    if (!rows.length) {
      list.innerHTML = '<div class="ps-empty">暂无模板</div>';
      return;
    }
    list.innerHTML = rows.map(function(row) {
      var id = String(row.id || '');
      var k = Array.isArray(row.keyword_ids) ? row.keyword_ids.length : 0;
      var c = Array.isArray(row.competitor_ids) ? row.competitor_ids.length : 0;
      var m = Array.isArray(row.memory_doc_ids) ? row.memory_doc_ids.length : 0;
      return '<article class="ps-template-card">' +
        '<div><strong>' + esc(templateName(row)) + '</strong><div class="ps-template-meta">关键词 ' + k + ' · 同行 ' + c + ' · 记忆 ' + m + '</div></div>' +
        '<div class="ps-item-actions">' +
          '<button type="button" class="btn btn-primary btn-sm" data-use-template="' + escAttr(id) + '">设为当前</button>' +
          '<button type="button" class="btn btn-ghost btn-sm" data-edit-template="' + escAttr(id) + '">编辑</button>' +
        '</div>' +
      '</article>';
    }).join('');
    list.querySelectorAll('[data-use-template]').forEach(function(btn) {
      btn.addEventListener('click', function() { useTemplate(btn.getAttribute('data-use-template') || '', btn); });
    });
    list.querySelectorAll('[data-edit-template]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var id = btn.getAttribute('data-edit-template') || '';
        var row = (state.templates || []).find(function(item) { return String(item.id || '') === id; });
        if (row) applyTemplate(row, true);
      });
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
          '<button type="button" class="btn btn-ghost btn-sm" data-delete-keyword="' + escAttr(id) + '">删除</button>' +
        '</div>' +
      '</article>';
    }).join('');
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
      var readOnly = !!(doc && (doc.read_only || doc.source === 'agent'));
      var tag = readOnly ? '代理商' : '个人';
      return '<article class="ps-memory-item">' +
        '<div><strong>' + esc(memoryTitle(doc)) + '</strong>' +
        '<small>' + esc(tag + (doc.notes || doc.filename ? ' · ' + (doc.notes || doc.filename) : '') + (doc.created_at ? ' · ' + doc.created_at : '')) + '</small></div>' +
        '<div class="ps-actions">' +
          '<button type="button" class="btn btn-ghost btn-sm" data-preview-memory="' + escAttr(id) + '">预览</button>' +
          (readOnly ? '' : '<button type="button" class="btn btn-ghost btn-sm" data-delete-memory="' + escAttr(id) + '">删除</button>') +
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
    var editableDocs = state.memories.filter(function(doc) { return !(doc && (doc.read_only || doc.source === 'agent')); });
    select.innerHTML = '<option value="">请选择已有文档</option>' + editableDocs.map(function(doc) {
      var id = memoryId(doc);
      return '<option value="' + escAttr(id) + '">' + esc(memoryTitle(doc)) + '</option>';
    }).join('');
    if (current && editableDocs.some(function(doc) { return memoryId(doc) === current; })) {
      select.value = current;
    }
    syncSaveModeState();
  }

  function syncSelectionMap(map, ids, defaultSelected) {
    ids = (ids || []).map(function(id) { return String(id || '').trim(); }).filter(Boolean);
    var allowed = {};
    ids.forEach(function(id) { allowed[id] = true; });
    Object.keys(map || {}).forEach(function(id) {
      if (!allowed[String(id)]) delete map[id];
    });
    ids.forEach(function(id) {
      if (!(id in map)) map[id] = defaultSelected !== false;
    });
    return map;
  }

  function isUploadedMemoryDoc(doc) {
    var notes = String((doc && doc.notes) || '');
    var meta = doc && doc.meta && typeof doc.meta === 'object' ? doc.meta : {};
    return notes.indexOf('上传资料') >= 0 || meta.save_mode === 'new' || meta.uploaded === true;
  }

  function memorySourceDocRows() {
    var rows = (state.memories || []).filter(function(doc) { return !(doc && (doc.read_only || doc.source === 'agent')); });
    var uploaded = rows.filter(isUploadedMemoryDoc);
    return uploaded.length ? uploaded : rows;
  }

  function ensureMemorySourceSelections() {
    if (state.memoryUseProfile !== false) state.memoryUseProfile = true;
    state.memorySourceKeywords = syncSelectionMap(state.memorySourceKeywords || {}, (state.keywords || []).map(function(row) { return row.id; }));
    state.memorySourceCompetitors = syncSelectionMap(state.memorySourceCompetitors || {}, (state.competitors || []).map(function(row) { return row.id; }));
    state.memorySourceDocs = syncSelectionMap(state.memorySourceDocs || {}, memorySourceDocRows().map(memoryId));
    state.memorySourceFiles = syncSelectionMap(state.memorySourceFiles || {}, selectedUploadFiles().map(uploadFileKey));
  }

  function selectedMemoryKeywordRows() {
    ensureMemorySourceSelections();
    return (state.keywords || []).filter(function(row) { return state.memorySourceKeywords[String(row.id || '')]; });
  }

  function selectedMemoryCompetitorRows() {
    ensureMemorySourceSelections();
    return (state.competitors || []).filter(function(row) { return state.memorySourceCompetitors[String(row.id || '')]; });
  }

  function selectedMemorySourceDocs() {
    ensureMemorySourceSelections();
    return memorySourceDocRows().filter(function(doc) { return state.memorySourceDocs[memoryId(doc)]; });
  }

  function selectedMemoryUploadFiles() {
    ensureMemorySourceSelections();
    return selectedUploadFiles().filter(function(file) { return state.memorySourceFiles[uploadFileKey(file)]; });
  }

  function renderSourceOptions(elId, rows, selected, kind, titleFn, subtitleFn) {
    var el = $(elId);
    if (!el) return;
    if (!rows.length) {
      el.innerHTML = '<div class="ps-empty">暂无</div>';
      return;
    }
    el.innerHTML = rows.map(function(row) {
      var id = kind === 'source_file' ? uploadFileKey(row) : (kind === 'source_doc' ? memoryId(row) : String(row.id || ''));
      var subtitle = subtitleFn ? String(subtitleFn(row) || '') : '';
      return '<label class="ps-source-option">' +
        '<input type="checkbox" data-ps-memory-source="' + escAttr(kind) + '" value="' + escAttr(id) + '"' + (selected[id] ? ' checked' : '') + '>' +
        '<span><strong>' + esc(titleFn(row)) + '</strong>' + (subtitle ? '<small>' + esc(subtitle) + '</small>' : '') + '</span>' +
      '</label>';
    }).join('');
  }

  function renderMemorySourceSelectors() {
    ensureMemorySourceSelections();
    if ($('psMemoryUseProfile')) $('psMemoryUseProfile').checked = state.memoryUseProfile !== false;
    renderSourceOptions('psMemoryKeywordSourceList', state.keywords || [], state.memorySourceKeywords, 'keyword',
      function(row) { return row.display_name || row.keyword || ('关键词 #' + row.id); },
      function(row) { return row.keyword || ''; });
    renderSourceOptions('psMemoryCompetitorSourceList', state.competitors || [], state.memorySourceCompetitors, 'competitor',
      function(row) { return row.display_name || row.account_key || ('同行 #' + row.id); },
      function(row) { return platformLabel(row.platform) + (row.account_key ? ' · ' + row.account_key : ''); });
    renderSourceOptions('psMemoryUploadSourceList', memorySourceDocRows(), state.memorySourceDocs, 'source_doc',
      memoryTitle,
      function(row) { return row.notes || row.filename || ''; });
    var currentFiles = selectedUploadFiles();
    if (currentFiles.length) {
      var box = $('psMemoryUploadSourceList');
      var fileHtml = currentFiles.map(function(file) {
        var id = uploadFileKey(file);
        var size = file && file.size ? ' · ' + Math.ceil(file.size / 1024) + 'KB' : '';
        return '<label class="ps-source-option">' +
          '<input type="checkbox" data-ps-memory-source="source_file" value="' + escAttr(id) + '"' + (state.memorySourceFiles[id] ? ' checked' : '') + '>' +
          '<span><strong>' + esc(file.name || '未命名文件') + '</strong><small>当前选择' + esc(size) + '</small></span>' +
        '</label>';
      }).join('');
      if (box) box.innerHTML = (box.innerHTML && box.innerHTML.indexOf('ps-empty') < 0 ? box.innerHTML : '') + fileHtml;
    }
  }

  function renderReferenceMemoryOptions() {
    renderTemplateOptions('psReferenceMemoryList', state.memories, {
      kind: 'reference-memory',
      selected: state.selectedReferenceMemories,
      empty: '暂无记忆文件，可先上传资料并存入记忆。',
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
    renderCurrentTemplate();
    renderSavedTemplates();
    renderKeywords();
    renderCompetitors();
    renderMemories();
    renderMemorySourceSelectors();
    renderProfileWizard();
  }

  function applyDefaultItem(item) {
    state.defaultItem = item || {};
    state.selectedKeywords = {};
    state.selectedCompetitors = {};
    state.selectedMemories = {};
    (item.keyword_ids || []).forEach(function(id) { if (id) state.selectedKeywords[String(id)] = true; });
    (item.competitor_ids || []).forEach(function(id) { if (id) state.selectedCompetitors[String(id)] = true; });
    (item.memory_doc_ids || []).forEach(function(id) { if (id) state.selectedMemories[String(id)] = true; });
    fillProfileFields(state.defaultItem);
    renderTemplateLists();
    renderCurrentTemplate();
  }

  function loadKeywords() {
    return cloudJson('/api/ip-content/keywords').then(function(data) {
      state.keywords = Array.isArray(data.items) ? data.items : [];
      renderTemplateLists();
      renderMemorySourceSelectors();
      renderKeywords();
    });
  }

  function loadCompetitors() {
    return cloudJson('/api/ip-content/competitors').then(function(data) {
      state.competitors = Array.isArray(data.items) ? data.items : [];
      renderTemplateLists();
      renderMemorySourceSelectors();
      renderCompetitors();
    });
  }

  function loadMemories() {
    return syncOpenClawMemoryFromCloud().then(function() {
      return cloudJson('/api/personal-settings/memory-documents/list', { json: false });
    }).then(function(data) {
      state.memories = Array.isArray(data.documents) ? data.documents : [];
      renderTemplateLists();
      renderMemorySourceSelectors();
      renderMemories();
    });
  }

  function loadTemplates() {
    return cloudJson('/api/ip-content/schedule-templates').then(function(data) {
      state.templates = (Array.isArray(data.items) ? data.items : []).filter(function(row) { return !isPersonalDefaultTemplate(row); });
      renderCurrentTemplate();
      renderSavedTemplates();
    });
  }

  function loadAll() {
    setMsg('正在加载个人设置...');
    return Promise.all([
      cloudJson('/api/ip-content/keywords').then(function(data) { state.keywords = Array.isArray(data.items) ? data.items : []; }),
      cloudJson('/api/ip-content/competitors').then(function(data) { state.competitors = Array.isArray(data.items) ? data.items : []; }),
      loadMemories().catch(function() { state.memories = []; }),
      loadTemplates().catch(function() { state.templates = []; }),
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

  function selectedMemoryPayload(ids) {
    ids = ids || cleanStringIds(state.selectedMemories);
    return state.memories.filter(function(doc) { return ids.indexOf(memoryId(doc)) >= 0; });
  }

  function removeDefaultId(kind, id) {
    var item = state.defaultItem || {};
    var key = kind === 'keyword' ? 'keyword_ids' : (kind === 'competitor' ? 'competitor_ids' : 'memory_doc_ids');
    var strId = String(id || '');
    item[key] = (Array.isArray(item[key]) ? item[key] : []).filter(function(value) { return String(value || '') !== strId; });
    if (kind === 'memory') {
      item.memory_docs = (Array.isArray(item.memory_docs) ? item.memory_docs : []).filter(function(doc) {
        return String((doc && (doc.doc_id || doc.id)) || '') !== strId;
      });
    }
    state.defaultItem = item;
  }

  function saveTemplate() {
    var btn = $('psSaveTemplateBtn');
    var name = fieldValue('psTemplateName');
    if (!name) {
      setMsg('请填写模板名称。', true);
      return;
    }
    setBusy(btn, true, '保存中...');
    setMsg('正在保存模板...');
    Promise.all(selectedMemoryPayload().map(fetchMemoryContent)).then(function(memoryDocs) {
      var body = {
        name: name,
        keyword_ids: cleanIntIds(state.selectedKeywords),
        competitor_ids: cleanIntIds(state.selectedCompetitors),
        memory_doc_ids: cleanStringIds(state.selectedMemories),
        memory_docs: memoryDocs,
        requirements: profileRequirements(),
        meta: { source: 'personal_settings' }
      };
      return cloudJson(state.editingTemplateId ? '/api/ip-content/schedule-templates/' + encodeURIComponent(state.editingTemplateId) : '/api/ip-content/schedule-templates', {
        method: state.editingTemplateId ? 'PATCH' : 'POST',
        body: body
      });
    }).then(function(data) {
      if (data.item && data.item.id) state.editingTemplateId = String(data.item.id);
      setMsg('模板已保存。');
      return loadTemplates();
    }).catch(function(err) {
      setMsg(err.message || '保存失败', true);
    }).finally(function() {
      setBusy(btn, false);
    });
  }

  function saveCurrentDefault(options) {
    options = options || {};
    var existing = state.defaultItem || {};
    var keywordSource = options.replaceSelection ? cleanIntIds(state.selectedKeywords) : [].concat(Array.isArray(existing.keyword_ids) ? existing.keyword_ids : [], cleanIntIds(state.selectedKeywords));
    var competitorSource = options.replaceSelection ? cleanIntIds(state.selectedCompetitors) : [].concat(Array.isArray(existing.competitor_ids) ? existing.competitor_ids : [], cleanIntIds(state.selectedCompetitors));
    var memorySource = options.replaceSelection ? cleanStringIds(state.selectedMemories) : [].concat(Array.isArray(existing.memory_doc_ids) ? existing.memory_doc_ids : [], cleanStringIds(state.selectedMemories));
    var keywordIds = uniqueIds(keywordSource).map(function(id) { return Number(id); }).filter(function(id) { return isFinite(id) && id > 0; });
    var competitorIds = uniqueIds(competitorSource).map(function(id) { return Number(id); }).filter(function(id) { return isFinite(id) && id > 0; });
    var memoryIds = uniqueIds(memorySource);
    return Promise.all(selectedMemoryPayload(memoryIds).map(fetchMemoryContent)).then(function(memoryDocs) {
      return cloudJson('/api/ip-content/personal-default', {
        method: 'PUT',
        body: {
          name: options.name || existing.name || '个人默认模板',
          keyword_ids: keywordIds,
          competitor_ids: competitorIds,
          memory_doc_ids: memoryIds,
          memory_docs: memoryDocs,
          requirements: Object.assign({}, (existing.requirements && typeof existing.requirements === 'object') ? existing.requirements : {}, profileRequirements(), options.requirements || {}),
          meta: Object.assign({}, (existing.meta && typeof existing.meta === 'object') ? existing.meta : {}, options.meta || {}, { source: options.source || 'personal_settings' })
        }
      });
    }).then(function(data) {
      applyDefaultItem(data.item || {});
      return syncOpenClawMemoryFromCloud().then(function() { return data; });
    });
  }

  function saveConfigSilently() {
    return saveCurrentDefault();
  }

  function saveProfile() {
    syncProfileAnswerToField();
    var btn = $('psSaveProfileBtn');
    setBusy(btn, true, '保存中...');
    setMsg('正在保存资料调查...');
    saveCurrentDefault({ source: 'personal_settings_profile' })
      .then(function() { setMsg('资料调查已保存。'); })
      .catch(function(err) { setMsg(err.message || '保存失败', true); })
      .finally(function() { setBusy(btn, false); });
  }

  function applyTemplate(row, editing) {
    row = row || {};
    state.editingTemplateId = editing && row.id ? String(row.id) : '';
    state.selectedKeywords = {};
    state.selectedCompetitors = {};
    state.selectedMemories = {};
    (row.keyword_ids || []).forEach(function(id) { if (id) state.selectedKeywords[String(id)] = true; });
    (row.competitor_ids || []).forEach(function(id) { if (id) state.selectedCompetitors[String(id)] = true; });
    (row.memory_doc_ids || []).forEach(function(id) { if (id) state.selectedMemories[String(id)] = true; });
    if ($('psTemplateName')) $('psTemplateName').value = row.name || '';
    if (row.requirements && typeof row.requirements === 'object') fillProfileFields(row);
    renderAllLists();
    switchTab('template');
  }

  function resetTemplateForm() {
    state.editingTemplateId = '';
    state.selectedKeywords = {};
    state.selectedCompetitors = {};
    state.selectedMemories = {};
    if ($('psTemplateName')) $('psTemplateName').value = '';
    renderTemplateLists();
    renderSavedTemplates();
  }

  function useTemplate(id, btn) {
    var row = (state.templates || []).find(function(item) { return String(item.id || '') === String(id || ''); });
    if (!row) {
      setMsg('模板不存在。', true);
      return;
    }
    setBusy(btn, true, '保存中...');
    state.selectedKeywords = {};
    state.selectedCompetitors = {};
    state.selectedMemories = {};
    (row.keyword_ids || []).forEach(function(value) { if (value) state.selectedKeywords[String(value)] = true; });
    (row.competitor_ids || []).forEach(function(value) { if (value) state.selectedCompetitors[String(value)] = true; });
    (row.memory_doc_ids || []).forEach(function(value) { if (value) state.selectedMemories[String(value)] = true; });
    var requirements = Object.assign({}, (state.defaultItem && state.defaultItem.requirements && typeof state.defaultItem.requirements === 'object') ? state.defaultItem.requirements : profileRequirements(), (row.requirements && typeof row.requirements === 'object') ? row.requirements : {});
    saveCurrentDefault({
      name: templateName(row),
      requirements: requirements,
      meta: Object.assign({}, row.meta || {}, { current_template_id: row.id }),
      source: 'personal_settings_current_template',
      replaceSelection: true
    }).then(function() {
      fillProfileFields(state.defaultItem || {});
      renderAllLists();
      setMsg('当前使用模板已更新。');
    }).catch(function(err) {
      setMsg(err.message || '设置当前模板失败', true);
    }).finally(function() {
      setBusy(btn, false);
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
        removeDefaultId('keyword', id);
        setMsg('关键词已删除。');
        return loadKeywords().then(saveConfigSilently);
      })
      .catch(function(err) { setMsg(err.message || '关键词删除失败', true); });
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
      .then(function(data) {
        if ($('psCompetitorSearchInput')) $('psCompetitorSearchInput').value = '';
        if ($('psCompetitorTags')) $('psCompetitorTags').value = '';
        state.competitorCandidates = [];
        renderCompetitorCandidates();
        setMsg('同行账号已添加。');
        return loadCompetitors().then(function() {
          if (data.item && data.item.id) return syncCompetitor(data.item.id);
          return null;
        });
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
        removeDefaultId('competitor', id);
        setMsg('同行账号已删除。');
        return loadCompetitors().then(saveConfigSilently);
      })
      .catch(function(err) { setMsg(err.message || '删除同行失败', true); });
  }

  function memoryInputText() {
    var parts = [];
    var context = profileContextText({
      includeProfile: state.memoryUseProfile !== false,
      keywordRows: selectedMemoryKeywordRows(),
      competitorRows: selectedMemoryCompetitorRows(),
      sourceDocs: selectedMemorySourceDocs()
    });
    if (context) parts.push(context);
    var files = selectedMemoryUploadFiles();
    if (files.length) {
      parts.push('当前选择文件：\n' + files.map(function(file) { return '- ' + file.name; }).join('\n'));
    }
    return parts.join('\n\n').trim();
  }

  function generateMemoryDocs() {
    var btn = $('psGenerateMemoryBtn');
    syncProfileAnswerToField();
    ensureMemorySourceSelections();
    var files = selectedMemoryUploadFiles();
    var keywordRows = selectedMemoryKeywordRows();
    var competitorRows = selectedMemoryCompetitorRows();
    var sourceDocs = selectedMemorySourceDocs();
    var docTypes = selectedGenerateDocTypes();
    var customReferenceFile = selectedCustomReferenceFile();
    var contextText = profileContextText({
      includeProfile: state.memoryUseProfile !== false,
      keywordRows: keywordRows,
      competitorRows: competitorRows,
      sourceDocs: sourceDocs
    });
    if (!docTypes.length && !customReferenceFile) {
      setMsg('请选择一个预置生成类型，或上传一份自定义参考文档。', true);
      return;
    }
    setBusy(btn, true, '理解中...');
    setMsg('正在理解资料并生成记忆内容...');
    competitorSourceText(competitorRows.map(function(row) { return row.id; })).then(function(competitorText) {
      if (!files.length && !contextText && !competitorText) {
        throw new Error('请选择要生成的资料来源。');
      }
      var fd = new FormData();
      files.forEach(function(file) { fd.append('files', file, file.name || 'upload'); });
      fd.append('urls', '');
      fd.append('direct_intro', [contextText, competitorText].filter(Boolean).join('\n\n'));
      fd.append('direct_faq', '');
      fd.append('direct_scripts', '');
      fd.append('doc_type', docTypes[0] || '');
      fd.append('doc_types', JSON.stringify(docTypes));
      if (customReferenceFile) fd.append('custom_reference_file', customReferenceFile, customReferenceFile.name || 'custom-reference');
      fd.append('reference_doc_ids', '');
      return fetch(cloudBase() + '/api/personal-settings/memory-documents/generate', {
        method: 'POST',
        headers: headers(false),
        body: fd
      });
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok || data.ok === false) throw new Error(parseErr(data, '生成失败'));
        return data;
      });
    }).then(function(data) {
      state.generatedDocuments = data.documents || {};
      state.generatedDocOrder = Array.isArray(data.doc_types) && data.doc_types.length ? data.doc_types : docTypes;
      if ($('psMemoryTitle') && (($('psSaveMode') || {}).value || 'new') === 'new') {
        $('psMemoryTitle').value = recommendMemoryTitle(state.generatedDocOrder, !!customReferenceFile);
      }
      renderGeneratedDocs();
      setMsg('AI 理解完成，请审核右侧结果后存入记忆。');
    }).catch(function(err) {
      setMsg(err.message || 'AI 理解失败', true);
    }).finally(function() {
      setBusy(btn, false);
    });
  }

  function saveRawMemory() {
    var files = selectedUploadFiles();
    if (!files.length) {
      setMsg('请先上传文件。', true);
      return;
    }
    var btn = $('psSaveRawMemoryBtn');
    setBusy(btn, true, '保存中...');
    setMsg('正在保存上传文件...');
    files.reduce(function(chain, file) {
      return chain.then(function() {
        var fd = new FormData();
        fd.append('files', file, file.name || 'upload');
        fd.append('title', file.name || '上传资料');
        fd.append('notes', 'IP人设定位上传资料');
        fd.append('raw_text', '');
        fd.append('urls', '');
        fd.append('mode', 'new');
        fd.append('target_doc_id', '');
        return saveUploadedMemory(null, fd);
      });
    }, Promise.resolve()).then(function() {
      state.uploadFiles = [];
      renderSelectedFiles();
      renderMemorySourceSelectors();
      setMsg('已存入记忆。');
    }).catch(function(err) {
      setMsg(err.message || '保存记忆失败', true);
    }).finally(function() {
      setBusy(btn, false);
    });
  }

  function saveMemory() {
    var generated = generatedDocsFromUi();
    var generatedContent = formatGeneratedDocs(generated.documents, generated.order);
    var hasGeneratedPreview = document.querySelectorAll('[data-ps-generated-text]').length > 0;
    if (hasGeneratedPreview && !Object.keys(generated.documents || {}).length) {
      setMsg('请至少勾选一个要保存的 AI 理解结果。', true);
      return;
    }
    var content = generatedContent || (!hasGeneratedPreview ? (($('psMemoryReviewText') || {}).value || '').trim() : '');
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
    if (mode === 'new' && Object.keys(generated.documents || {}).length) {
      saveGeneratedDocuments($('psSaveMemoryBtn'), title, generated.documents);
      return;
    }
    saveMemoryContent($('psSaveMemoryBtn'), title, content, '个人设置审核后保存的记忆', mode, targetDocId);
  }

  function saveGeneratedDocuments(btn, title, documents) {
    setBusy(btn, true, '保存中...');
    setMsg('正在按生成类型保存到记忆...');
    cloudJson('/api/personal-settings/memory-documents/save', {
      method: 'POST',
      body: {
        title: title,
        notes: '个人设置 AI 理解后保存的记忆',
        documents: documents || {}
      }
    })
      .then(function(data) {
        var docs = Array.isArray(data.documents) ? data.documents : [];
        if (!docs.length && data.document) docs = [data.document];
        docs.forEach(function(doc) {
          if (doc && doc.id) state.selectedMemories[String(doc.id)] = true;
        });
        if ($('psMemoryReviewText')) $('psMemoryReviewText').value = data.content_text || formatGeneratedDocs(documents, state.generatedDocOrder);
        return loadMemories();
      })
      .then(saveConfigSilently)
      .then(function() {
        setMsg('已按生成类型存入记忆，并写入模板选择。');
        renderTemplateLists();
      })
      .catch(function(err) {
        setMsg(err.message || '保存记忆失败', true);
      })
      .finally(function() { setBusy(btn, false); });
  }

  function saveMemoryContent(btn, title, content, notes, mode, targetDocId) {
    setBusy(btn, true, '保存中...');
    setMsg('正在保存到记忆...');
    cloudJson('/api/personal-settings/memory-documents/save-raw', {
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
    fetch(cloudBase() + '/api/personal-settings/memory-documents/save-upload', {
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
      state.generatedDocuments = {};
      state.generatedDocOrder = [];
      renderGeneratedDocs();
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
    cloudJson('/api/personal-settings/memory-documents/' + encodeURIComponent(id) + '/preview', { json: false })
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
    cloudJson('/api/personal-settings/memory-documents/' + encodeURIComponent(id), { method: 'DELETE', json: false })
      .then(function() {
        delete state.selectedMemories[String(id)];
        delete state.selectedReferenceMemories[String(id)];
        removeDefaultId('memory', id);
        return loadMemories();
      })
      .then(saveConfigSilently)
      .then(function() { setMsg('记忆文件已删除。'); })
      .catch(function(err) { setMsg(err.message || '删除失败', true); });
  }

  function bind() {
    document.querySelectorAll('#content-personal-settings [data-ps-tab]').forEach(function(btn) {
      btn.addEventListener('click', function() { switchTab(btn.getAttribute('data-ps-tab') || 'keywords'); });
    });
    if ($('psRefreshBtn')) $('psRefreshBtn').addEventListener('click', loadAll);
    if ($('psBackBtn')) $('psBackBtn').addEventListener('click', function() {
      if (typeof window.showLobsterView === 'function') window.showLobsterView('chat');
    });
    if ($('psProfilePrevBtn')) $('psProfilePrevBtn').addEventListener('click', function() { moveProfile(-1); });
    if ($('psProfileNextBtn')) $('psProfileNextBtn').addEventListener('click', function() { moveProfile(1); });
    if ($('psSaveProfileBtn')) $('psSaveProfileBtn').addEventListener('click', saveProfile);
    if ($('psSaveTemplateBtn')) $('psSaveTemplateBtn').addEventListener('click', saveTemplate);
    if ($('psNewTemplateBtn')) $('psNewTemplateBtn').addEventListener('click', resetTemplateForm);
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
    if ($('psMemoryFiles')) $('psMemoryFiles').addEventListener('change', handleUploadFileChange);
    if ($('psCustomReferenceFile')) $('psCustomReferenceFile').addEventListener('change', handleCustomReferenceFileChange);
    if ($('psMemoryUseProfile')) $('psMemoryUseProfile').addEventListener('change', function(ev) {
      state.memoryUseProfile = !!ev.target.checked;
    });
    var root = $('content-personal-settings');
    if (root) {
      root.addEventListener('change', function(ev) {
        var input = ev.target && ev.target.closest ? ev.target.closest('[data-ps-memory-source]') : null;
        if (!input) return;
        var kind = input.getAttribute('data-ps-memory-source') || '';
        var map = kind === 'keyword'
          ? state.memorySourceKeywords
          : (kind === 'competitor'
            ? state.memorySourceCompetitors
            : (kind === 'source_doc' ? state.memorySourceDocs : state.memorySourceFiles));
        if (input.value) map[String(input.value)] = !!input.checked;
      });
    }
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
    renderSelectedFiles();
    renderMemorySourceSelectors();
    renderCustomReferenceFile();
    renderGeneratedDocs();
    loadAll();
  };
})();
