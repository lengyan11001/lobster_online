(function() {
  var state = {
    tab: 'keywords',
    keywords: [],
    competitors: [],
    competitorCandidates: [],
    memories: [],
    templates: [],
    templateLoadError: '',
    editingTemplateId: '',
    selectedKeywords: {},
    selectedCompetitors: {},
    selectedMemories: {},
    selectedReferenceMemories: {},
    memoryUseProfile: true,
    memorySourceKeywords: {},
    memorySourceCompetitors: {},
    memorySourceDocs: {},
    memorySourceFiles: {},
    memorySourceRecordings: {},
    recorderRecords: [],
    recorderRecordsLoading: false,
    recorderRecordsLoaded: false,
    recorderRecordsPromise: null,
    generatedDocuments: {},
    generatedDocOrder: [],
    uploadFiles: [],
    customReferenceFile: null,
    defaultItem: null,
    personalTemplateLanguage: 'zh-CN',
    profilePhotoPreview: '',
    profilePhotoName: '',
    profilePhotoResolvedValue: '',
    profilePhotoResolvingValue: '',
    profilePhotoAssets: [],
    profilePhotoPickerOpen: false,
    profilePhotoPickerLoading: false,
    profilePhotoPickerQuery: '',
    profilePhotoUploadBusy: false,
    personalDigitalHumanTemplates: [],
    personalDigitalHumanTemplatesLoaded: false,
    personalDigitalHumanTemplatesLoading: false,
    personalDigitalHumanTemplatesError: '',
    personalDigitalHumanTemplateSearch: '',
    personalDigitalHumanTemplatePage: 1,
    personalDigitalHumanTemplateDraft: null,
    personalSelectedDigitalHumanTemplate: null,
    personalDigitalHumanResources: { avatars: [], voices: [] },
    personalDigitalHumanAvatarOptions: [],
    personalDigitalHumanVoiceOptions: [],
    personalDigitalHumanResourcesLoading: false,
    personalDigitalHumanResourcePickerKind: 'avatar',
    personalDigitalHumanResourceQuery: '',
    personalDigitalHumanResourcePage: 1,
    personalDigitalHumanResourceDraft: null,
    personalDigitalHumanTemplateExplicitlyCleared: false
  };

  var DOC_TYPES = [
    { key: 'brand_product_intro', label: '产品介绍' },
    { key: 'product_service_faq', label: '百问百答' },
    { key: 'short_video_scripts', label: '短视频口播稿' }
  ];

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
    if (state.tab === 'upload') renderUploadedDocuments();
    if (state.tab === 'memory') {
      renderMemorySourceSelectors();
      loadRecorderSources().catch(function(err) {
        setMsg(err.message || '录音转写记录加载失败', true);
      });
    }
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

  function existingIntIdSet(rows) {
    var allowed = {};
    (rows || []).forEach(function(row) {
      var id = Number(row && row.id);
      if (isFinite(id) && id > 0) allowed[String(id)] = true;
    });
    return allowed;
  }

  function cleanExistingIntIds(values, rows) {
    var allowed = existingIntIdSet(rows);
    return uniqueIds(values || []).map(function(id) { return Number(id); }).filter(function(id) {
      return isFinite(id) && id > 0 && allowed[String(id)];
    });
  }

  function pruneSelectedIntMap(map, rows) {
    var allowed = existingIntIdSet(rows);
    Object.keys(map || {}).forEach(function(id) {
      if (!allowed[String(id)]) delete map[id];
    });
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

  function templateLanguageFromParts(requirements, meta, fallback) {
    var req = requirements && typeof requirements === 'object' ? requirements : {};
    var m = meta && typeof meta === 'object' ? meta : {};
    return normalizeIpTemplateLanguage(req.language || req.target_language || m.language || m.target_language || m.profile_language || fallback || '');
  }

  function currentPersonalTemplateLanguage() {
    var sel = $('psTemplateLanguage');
    return normalizeIpTemplateLanguage((sel && sel.value) || state.personalTemplateLanguage || '');
  }

  function setPersonalTemplateLanguage(value) {
    state.personalTemplateLanguage = normalizeIpTemplateLanguage(value);
    var sel = $('psTemplateLanguage');
    if (sel) sel.value = state.personalTemplateLanguage;
  }

  function templateRequirementsWithLanguage(requirements, language) {
    var req = requirements && typeof requirements === 'object' ? Object.assign({}, requirements) : {};
    var lang = normalizeIpTemplateLanguage(language);
    req.language = lang;
    req.target_language = ipTemplateLanguageLabel(lang);
    req.common = ipTemplateLanguageInstruction(lang);
    return req;
  }

  function profileQuestions() {
    return [
      { field: 'psProfileName', label: '你的名字', type: 'input', hint: '填写希望在内容中使用的姓名或称呼', placeholder: '例如：张老师、阿杰、李总' },
      { field: 'psGender', label: '你的性别', type: 'select', hint: '用于匹配口播称谓和表达方式', placeholder: '请选择性别', options: [{ value: 'female', label: '女' }, { value: 'male', label: '男' }] },
      { field: 'psProfilePhoto', label: '人物照片', type: 'photo', hint: '选择一张清晰正面照，便于后续形象和内容生成', placeholder: '支持从电脑上传或从素材库选择' },
      { field: 'psBirthEra', label: '你是哪个年代出生的？', type: 'input', hint: '年龄阶段会影响语言习惯和内容表达', placeholder: '例如：80后、90后、00后' },
      { field: 'psCurrentProvince', label: '你现在居住在哪个省份？', type: 'input', hint: '填写当前常住地所在省份', placeholder: '例如：广东省' },
      { field: 'psCurrentCity', label: '你现在居住在哪个城市？', type: 'input', hint: '用于生成同城和地域相关内容', placeholder: '例如：深圳市' },
      { field: 'psHometown', label: '你的籍贯是哪里？', type: 'input', hint: '填写家乡所在城市或地区', placeholder: '例如：湖南长沙' },
      { field: 'psRole', label: '你是做什么的？', type: 'input', hint: '说明你的职业、岗位或对外身份', placeholder: '例如：餐饮老板、家装设计师、企业培训师' },
      { field: 'psShareTopic', label: '你主要想分享什么内容？', type: 'input', hint: '填写账号长期输出的内容方向', placeholder: '例如：门店经营、育儿知识、行业经验' },
      { field: 'psVideoStyle', label: '你希望呈现什么视频风格？', type: 'input', hint: '描述画面、口播语气或内容节奏', placeholder: '例如：专业口播、轻松聊天、剧情演绎' },
      { field: 'psAfterViewAction', label: '希望用户看完后做什么？', type: 'input', hint: '填写你希望用户采取的下一步动作', placeholder: '例如：关注账号、私信咨询、到店体验' },
      { field: 'psBusinessProduct', label: '你在做什么产品或服务？', type: 'textarea', hint: '写清产品、服务、主要卖点、价格带和交付方式', placeholder: '例如：为本地餐饮门店提供短视频获客服务，包含拍摄、运营和线索转化' },
      { field: 'psTargetCustomer', label: '你最想服务哪类客户？', type: 'textarea', hint: '描述客户身份、年龄、地区、需求和主要痛点', placeholder: '例如：25-45 岁的餐饮门店老板，希望稳定获取同城客流' },
      { field: 'psAdvantages', label: '你比同行好在哪里？', type: 'textarea', hint: '说明资历、案例、产品、服务或价格方面的差异化优势', placeholder: '例如：10 年行业经验，服务过 300 家门店，提供从内容到成交的完整方案' }
    ];
  }

  function profilePhotoAssetPreview(row) {
    row = row || {};
    return String(row.preview_url || row.local_preview_url || row.open_url || row.source_url || row.url || '').trim();
  }

  function profilePhotoDisplayUrl(value) {
    var url = String(value || '').trim();
    return /^(https?:\/\/|blob:|data:image\/|\/)/i.test(url) ? url : '';
  }

  function profilePhotoSize(value) {
    var bytes = Number(value || 0);
    if (!isFinite(bytes) || bytes <= 0) return '';
    if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(bytes >= 10 * 1024 * 1024 ? 0 : 1) + ' MB';
    return Math.max(1, Math.round(bytes / 1024)) + ' KB';
  }

  function setProfilePhoto(value, previewUrl, name) {
    var nextValue = String(value || '').trim();
    setFieldValue('psProfilePhoto', nextValue);
    state.profilePhotoPreview = profilePhotoDisplayUrl(previewUrl || (nextValue.indexOf('http') === 0 ? nextValue : ''));
    state.profilePhotoName = String(name || '').trim();
    state.profilePhotoResolvedValue = state.profilePhotoPreview ? nextValue : '';
    state.profilePhotoResolvingValue = '';
  }

  function resolveProfilePhotoPreview(value) {
    var selected = String(value || '').trim();
    if (!selected || state.profilePhotoResolvedValue === selected || state.profilePhotoResolvingValue === selected) return;
    var directUrl = profilePhotoDisplayUrl(selected);
    if (directUrl) {
      state.profilePhotoPreview = directUrl;
      state.profilePhotoName = state.profilePhotoName || '已选择人物照片';
      state.profilePhotoResolvedValue = selected;
      return;
    }
    if (!localBase()) return;
    state.profilePhotoResolvingValue = selected;
    localJson('/api/assets/' + encodeURIComponent(selected), { json: false })
      .then(function(item) {
        if (fieldValue('psProfilePhoto') !== selected) return;
        state.profilePhotoPreview = profilePhotoDisplayUrl(profilePhotoAssetPreview(item));
        state.profilePhotoName = String(item.filename || item.name || '素材库图片');
        state.profilePhotoResolvedValue = selected;
      })
      .catch(function() {
        if (fieldValue('psProfilePhoto') === selected) state.profilePhotoResolvedValue = selected;
      })
      .finally(function() {
        if (state.profilePhotoResolvingValue === selected) state.profilePhotoResolvingValue = '';
        if (state.tab === 'profile' && fieldValue('psProfilePhoto') === selected) renderProfileWizard();
      });
  }

  function renderProfilePhotoSelector() {
    var value = fieldValue('psProfilePhoto');
    resolveProfilePhotoPreview(value);
    var preview = profilePhotoDisplayUrl(state.profilePhotoPreview || value);
    var title = state.profilePhotoName || (value ? '已选择人物照片' : '还没有选择照片');
    var meta = value ? (/^https?:\/\//i.test(value) ? '图片链接' : '素材库图片') : '支持 JPG、PNG、WEBP 等图片';
    var uploadLabel = state.profilePhotoUploadBusy ? '上传中...' : '上传电脑图片';
    return '<div class="ps-photo-select">' +
      '<input id="psProfilePhotoFile" type="file" accept="image/*" hidden>' +
      '<div class="ps-photo-preview' + (preview ? ' has-image' : '') + '">' +
        (preview ? '<img src="' + escAttr(preview) + '" alt="人物照片预览">' : '<span>人像</span>') +
      '</div>' +
      '<div class="ps-photo-copy"><strong>' + esc(title) + '</strong><span>' + esc(meta) + '</span></div>' +
      '<div class="ps-photo-actions">' +
        '<button type="button" class="btn btn-primary btn-sm" id="psProfilePhotoUploadBtn"' + (state.profilePhotoUploadBusy ? ' disabled' : '') + '>' + esc(uploadLabel) + '</button>' +
        '<button type="button" class="btn btn-ghost btn-sm" id="psProfilePhotoLibraryBtn"' + (state.profilePhotoUploadBusy ? ' disabled' : '') + '>从素材库选择</button>' +
        (value ? '<button type="button" class="btn btn-ghost btn-sm ps-photo-clear" id="psProfilePhotoClearBtn">清除</button>' : '') +
      '</div>' +
    '</div>';
  }

  function bindProfilePhotoSelector() {
    var fileInput = $('psProfilePhotoFile');
    var uploadBtn = $('psProfilePhotoUploadBtn');
    var libraryBtn = $('psProfilePhotoLibraryBtn');
    var clearBtn = $('psProfilePhotoClearBtn');
    if (uploadBtn && fileInput) uploadBtn.addEventListener('click', function() { fileInput.click(); });
    if (fileInput) fileInput.addEventListener('change', function() {
      var file = fileInput.files && fileInput.files[0];
      if (file) uploadProfilePhoto(file);
    });
    if (libraryBtn) libraryBtn.addEventListener('click', openProfilePhotoPicker);
    if (clearBtn) clearBtn.addEventListener('click', function() {
      setProfilePhoto('', '', '');
      renderProfileWizard();
      setMsg('已清除人物照片。');
    });
  }

  function uploadProfilePhoto(file) {
    if (!file || state.profilePhotoUploadBusy) return;
    var imageFile = /^image\//i.test(String(file.type || '')) || /\.(jpe?g|png|webp|gif|bmp|heic|heif)$/i.test(String(file.name || ''));
    if (!imageFile) {
      setMsg('请选择图片文件。', true);
      return;
    }
    if (!localBase()) {
      setMsg('当前未检测到本机后端地址。', true);
      return;
    }
    state.profilePhotoUploadBusy = true;
    renderProfileWizard();
    setMsg('正在上传人物照片...');
    var fd = new FormData();
    fd.append('file', file, file.name || 'profile-photo');
    fetch(localBase() + '/api/assets/upload', { method: 'POST', headers: headers(false), body: fd })
      .then(function(resp) {
        return resp.json().catch(function() { return {}; }).then(function(data) {
          if (!resp.ok || !data.asset_id) throw new Error(parseErr(data, '人物照片上传失败'));
          return data;
        });
      })
      .then(function(item) {
        setProfilePhoto(item.asset_id, profilePhotoAssetPreview(item), item.filename || file.name || '人物照片');
        setMsg('人物照片已上传并存入素材库。');
      })
      .catch(function(err) {
        setMsg(err.message || '人物照片上传失败', true);
      })
      .finally(function() {
        state.profilePhotoUploadBusy = false;
        renderProfileWizard();
      });
  }

  function renderProfilePhotoPicker(error) {
    var modal = $('psProfilePhotoPicker');
    var status = $('psProfilePhotoPickerStatus');
    var grid = $('psProfilePhotoPickerGrid');
    if (!modal || !status || !grid) return;
    modal.classList.toggle('is-visible', !!state.profilePhotoPickerOpen);
    modal.setAttribute('aria-hidden', state.profilePhotoPickerOpen ? 'false' : 'true');
    if (!state.profilePhotoPickerOpen) return;
    if (state.profilePhotoPickerLoading) {
      status.textContent = '正在加载素材库图片...';
      grid.innerHTML = '<div class="ps-photo-picker-empty">正在加载...</div>';
      return;
    }
    if (error) {
      status.textContent = error;
      grid.innerHTML = '<div class="ps-photo-picker-empty">素材库加载失败</div>';
      return;
    }
    var query = String(state.profilePhotoPickerQuery || '').trim().toLowerCase();
    var rows = (state.profilePhotoAssets || []).filter(function(item) {
      return !query || String(item.filename || item.name || item.asset_id || '').toLowerCase().indexOf(query) >= 0;
    });
    status.textContent = rows.length ? '共 ' + rows.length + ' 张图片' : '没有匹配的图片';
    if (!rows.length) {
      grid.innerHTML = '<div class="ps-photo-picker-empty">暂无可选图片</div>';
      return;
    }
    grid.innerHTML = rows.map(function(item) {
      var preview = profilePhotoAssetPreview(item);
      var name = item.filename || item.name || item.asset_id || '人物照片';
      var selected = String(fieldValue('psProfilePhoto')) === String(item.asset_id || '');
      return '<button type="button" class="ps-photo-picker-item' + (selected ? ' is-selected' : '') + '" data-ps-photo-asset="' + escAttr(item.asset_id || '') + '">' +
        '<span class="ps-photo-picker-thumb">' + (preview ? '<img src="' + escAttr(preview) + '" alt="" loading="lazy" decoding="async">' : '<span>无预览</span>') + '</span>' +
        '<span class="ps-photo-picker-name" title="' + escAttr(name) + '">' + esc(name) + '</span>' +
        '<small>' + esc(profilePhotoSize(item.file_size) || '图片素材') + '</small>' +
      '</button>';
    }).join('');
  }

  function loadProfilePhotoAssets() {
    if (!localBase()) {
      renderProfilePhotoPicker('当前未检测到本机后端地址');
      return;
    }
    state.profilePhotoPickerLoading = true;
    renderProfilePhotoPicker();
    localJson('/api/assets?media_type=image&limit=200', { json: false })
      .then(function(data) {
        state.profilePhotoAssets = Array.isArray(data.assets) ? data.assets : [];
        state.profilePhotoPickerLoading = false;
        renderProfilePhotoPicker();
      })
      .catch(function(err) {
        state.profilePhotoPickerLoading = false;
        renderProfilePhotoPicker(err.message || '素材库加载失败');
      });
  }

  function openProfilePhotoPicker() {
    state.profilePhotoPickerOpen = true;
    state.profilePhotoPickerQuery = '';
    var search = $('psProfilePhotoPickerSearch');
    if (search) search.value = '';
    renderProfilePhotoPicker();
    loadProfilePhotoAssets();
  }

  function closeProfilePhotoPicker() {
    state.profilePhotoPickerOpen = false;
    renderProfilePhotoPicker();
  }

  function pickProfilePhotoAsset(assetId) {
    var item = (state.profilePhotoAssets || []).find(function(row) { return String(row.asset_id || '') === String(assetId || ''); });
    if (!item) return;
    setProfilePhoto(item.asset_id, profilePhotoAssetPreview(item), item.filename || item.name || '素材库图片');
    closeProfilePhotoPicker();
    renderProfileWizard();
    setMsg('已选择素材库人物照片。');
  }

  function syncProfileAnswerToField(event) {
    var changed = event && event.target;
    if (changed && changed.getAttribute) {
      var changedField = changed.getAttribute('data-ps-profile-answer');
      if (changedField) setFieldValue(changedField, changed.value || '');
    } else {
      document.querySelectorAll('#psProfileQuestionList [data-ps-profile-answer]').forEach(function(answer) {
        var field = answer.getAttribute('data-ps-profile-answer');
        if (field) setFieldValue(field, answer.value || '');
      });
    }
    updateProfileCompletion();
  }

  function updateProfileCompletion() {
    var questions = profileQuestions();
    var completed = questions.filter(function(question) {
      return String(fieldValue(question.field) || '').trim();
    }).length;
    var text = $('psProfileCompletionText');
    if (text) text.textContent = '已填写 ' + completed + '/' + questions.length;
  }

  function renderProfileWizard() {
    var host = $('psProfileQuestionList');
    if (!host) return;
    var questions = profileQuestions();
    host.innerHTML = questions.map(function(question, idx) {
      var value = fieldValue(question.field);
      var control = '';
      if (question.type === 'photo') {
        control = renderProfilePhotoSelector();
      } else if (question.type === 'select') {
        var options = Array.isArray(question.options) ? question.options : [];
        control = '<select data-ps-profile-answer="' + escAttr(question.field) + '">' +
          '<option value="">' + esc(question.placeholder || '请选择') + '</option>' +
          options.map(function(item) { return '<option value="' + escAttr(item.value) + '">' + esc(item.label) + '</option>'; }).join('') +
          '</select>';
      } else if (question.type === 'textarea') {
        control = '<textarea rows="3" data-ps-profile-answer="' + escAttr(question.field) + '" placeholder="' + escAttr(question.placeholder || '') + '">' + esc(value) + '</textarea>';
      } else {
        control = '<input type="text" data-ps-profile-answer="' + escAttr(question.field) + '" value="' + escAttr(value) + '" placeholder="' + escAttr(question.placeholder || '') + '">';
      }
      return '<article class="ps-survey-item">' +
        '<div class="ps-survey-question"><span>' + (idx + 1) + '</span><div>' +
          '<strong>' + esc(question.label) + '</strong>' +
          '<small>' + esc(question.hint || question.placeholder || '') + '</small>' +
        '</div></div>' +
        '<div class="ps-survey-answer">' + control + '</div>' +
      '</article>';
    }).join('');
    bindProfilePhotoSelector();
    host.querySelectorAll('[data-ps-profile-answer]').forEach(function(answer) {
      if (answer.tagName === 'SELECT') answer.value = fieldValue(answer.getAttribute('data-ps-profile-answer'));
      answer.addEventListener('input', syncProfileAnswerToField);
      answer.addEventListener('change', syncProfileAnswerToField);
    });
    updateProfileCompletion();
  }

  function profileRequirements() {
    var profilePhoto = fieldValue('psProfilePhoto');
    var basic = {
      name: fieldValue('psProfileName'),
      gender: fieldValue('psGender'),
      profile_photo_asset_id: /^https?:\/\//i.test(profilePhoto) ? '' : profilePhoto,
      profile_photo_url: /^https?:\/\//i.test(profilePhoto) ? profilePhoto : '',
      birth_era: fieldValue('psBirthEra'),
      current_province: fieldValue('psCurrentProvince'),
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
      ['性别', basic.gender],
      ['人物照片', basic.profile_photo_asset_id || basic.profile_photo_url],
      ['出生年代', basic.birth_era],
      ['现居省份', basic.current_province],
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
      gender: basic.gender,
      profile_photo_asset_id: basic.profile_photo_asset_id,
      profile_photo_url: basic.profile_photo_url,
      birth_era: basic.birth_era,
      current_province: basic.current_province,
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

  var PERSONAL_PROFILE_REQUIREMENT_KEYS = [
    'basic_profile',
    'business_description',
    'profile',
    'business',
    'profile_name',
    'name',
    'gender',
    'profile_photo_asset_id',
    'profile_photo_url',
    'birth_era',
    'current_province',
    'province',
    'current_city',
    'city',
    'hometown',
    'role',
    'identity',
    'share_topic',
    'video_style',
    'after_view_action',
    'product',
    'target_customer',
    'advantages'
  ];

  function stripPersonalProfileRequirements(requirements) {
    var req = requirements && typeof requirements === 'object' ? Object.assign({}, requirements) : {};
    PERSONAL_PROFILE_REQUIREMENT_KEYS.forEach(function(key) { delete req[key]; });
    return req;
  }

  function fillProfileFields(item) {
    var req = (item && item.requirements) || {};
    var profile = req.basic_profile && typeof req.basic_profile === 'object' ? req.basic_profile : (req.profile || {});
    var business = req.business_description && typeof req.business_description === 'object' ? req.business_description : (req.business || {});
    var profilePhoto = req.profile_photo_asset_id || profile.profile_photo_asset_id || req.profile_photo_url || profile.profile_photo_url || '';
    setFieldValue('psProfileName', req.profile_name || profile.name || '');
    setFieldValue('psGender', req.gender || profile.gender || '');
    if (fieldValue('psProfilePhoto') !== String(profilePhoto || '')) {
      state.profilePhotoPreview = profilePhotoDisplayUrl(profilePhoto);
      state.profilePhotoName = '';
      state.profilePhotoResolvedValue = state.profilePhotoPreview ? String(profilePhoto || '') : '';
      state.profilePhotoResolvingValue = '';
    }
    setFieldValue('psProfilePhoto', profilePhoto);
    setFieldValue('psBirthEra', req.birth_era || profile.birth_era || '');
    setFieldValue('psCurrentProvince', req.current_province || profile.current_province || req.province || profile.province || '');
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

  function fileNeedsOnlineDocumentParse(file) {
    var name = String(file && file.name || '').toLowerCase();
    return /\.(txt|md|markdown|csv|tsv|json|jsonl|ya?ml|html?|log|pdf|docx|xlsx|xlsm|xls|pptx)$/.test(name);
  }

  function requireOnlineMemoryParser(files) {
    if (!(files || []).some(fileNeedsOnlineDocumentParse)) return Promise.resolve();
    return cloudJson('/api/h5-chat/devices/status', { json: false }).then(function(data) {
      var devices = (Array.isArray(data.devices) ? data.devices : []).filter(function(item) {
        return item && item.online;
      });
      if (!devices.length) throw new Error('资料解析需要先启动并登录 Online。');
      var capable = devices.some(function(item) {
        return Array.isArray(item.capabilities) && item.capabilities.indexOf('memory_document_parse_v1') >= 0;
      });
      if (!capable) throw new Error('当前 Online 版本不支持本机资料解析，请升级最新 OTA 后重试。');
    });
  }

  function waitForOnlineMemoryGeneration(messageId) {
    var attempts = 0;
    function poll() {
      if (attempts >= 600) return Promise.reject(new Error('资料仍在 Online 处理中，请稍后重试。'));
      attempts += 1;
      return new Promise(function(resolve) { window.setTimeout(resolve, 3000); }).then(function() {
        return cloudJson('/api/h5-chat/messages/' + encodeURIComponent(messageId), { json: false });
      }).then(function(data) {
        var message = data && data.message ? data.message : {};
        var events = Array.isArray(data && data.events) ? data.events : [];
        var resultEvent = events.slice().reverse().find(function(event) {
          var payload = event && event.payload && typeof event.payload === 'object' ? event.payload : {};
          return payload.documents && typeof payload.documents === 'object';
        });
        if (resultEvent) return resultEvent.payload;
        if (message.status === 'failed' || message.status === 'cancelled') {
          throw new Error(message.error || 'Online 资料理解失败。');
        }
        if (message.status === 'completed') {
          throw new Error('Online 已完成解析，但没有返回可审核内容，请重试。');
        }
        var progressEvent = events.slice().reverse().find(function(event) {
          return event && event.type === 'progress';
        });
        var progressText = progressEvent && progressEvent.payload ? progressEvent.payload.text : '';
        if (progressText) setMsg(progressText);
        return poll();
      });
    }
    return poll();
  }

  function monitorOnlineMemoryParse(messageId, filename) {
    if (!messageId) return;
    state.onlineMemoryParseMonitors = state.onlineMemoryParseMonitors || {};
    if (state.onlineMemoryParseMonitors[messageId]) return;
    state.onlineMemoryParseMonitors[messageId] = true;
    var attempts = 0;
    function finish() {
      delete state.onlineMemoryParseMonitors[messageId];
    }
    function poll() {
      if (attempts >= 600) {
        setMsg('“' + (filename || '资料') + '”仍在 Online 解析，可稍后刷新资料列表查看。');
        finish();
        return;
      }
      attempts += 1;
      window.setTimeout(function() {
        cloudJson('/api/h5-chat/messages/' + encodeURIComponent(messageId), { json: false }).then(function(data) {
          var message = data && data.message ? data.message : {};
          if (message.status === 'completed') {
            return loadMemories().then(saveConfigSilently).then(function() {
              setMsg('“' + (filename || '资料') + '”已解析并存入记忆。');
              finish();
            });
          }
          if (message.status === 'failed' || message.status === 'cancelled') {
            setMsg(message.error || ('“' + (filename || '资料') + '”解析失败。'), true);
            finish();
            return;
          }
          poll();
        }).catch(function(err) {
          setMsg((err && err.message) || '资料解析状态查询失败，可稍后刷新资料列表查看。', true);
          finish();
        });
      }, 3000);
    }
    poll();
  }

  function appendUploadFiles(fileList) {
    var picked = fileList ? Array.prototype.slice.call(fileList) : [];
    if (!picked.length) {
      renderSelectedFiles();
      return;
    }
    var seen = {};
    var existing = state.uploadFiles && state.uploadFiles.length ? state.uploadFiles : [];
    state.uploadFiles = existing.concat(picked).filter(function(file) {
      var key = uploadFileKey(file);
      if (!key || seen[key]) return false;
      seen[key] = true;
      return true;
    });
    renderSelectedFiles();
    renderMemorySourceSelectors();
  }

  function handleUploadFileChange() {
    var input = $('psMemoryFiles');
    appendUploadFiles(input && input.files ? input.files : []);
    if (input) input.value = '';
  }

  function bindUploadDropzone() {
    var zone = $('psUploadDropzone');
    if (!zone || zone.dataset.bound) return;
    zone.dataset.bound = '1';
    var dragDepth = 0;
    zone.addEventListener('dragenter', function(ev) {
      ev.preventDefault();
      dragDepth += 1;
      zone.classList.add('is-dragover');
    });
    zone.addEventListener('dragover', function(ev) {
      ev.preventDefault();
      if (ev.dataTransfer) ev.dataTransfer.dropEffect = 'copy';
    });
    zone.addEventListener('dragleave', function(ev) {
      ev.preventDefault();
      dragDepth = Math.max(0, dragDepth - 1);
      if (!dragDepth) zone.classList.remove('is-dragover');
    });
    zone.addEventListener('drop', function(ev) {
      ev.preventDefault();
      dragDepth = 0;
      zone.classList.remove('is-dragover');
      appendUploadFiles(ev.dataTransfer && ev.dataTransfer.files ? ev.dataTransfer.files : []);
    });
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
          '<button type="button" class="btn btn-ghost btn-sm" data-download-generated-doc="' + escAttr(key) + '">下载</button>' +
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

  var PS_MULTI_SELECT_PAGE_SIZE = 8;
  var PS_LIST_PAGE_SIZE = 8;
  var psMultiSelectPages = {};
  var psListPages = {};
  var psListPagingBound = false;

  function psPageInfo(elId, rows, size, store) {
    var totalPages = Math.max(1, Math.ceil(rows.length / size));
    var page = parseInt(store[elId] || 1, 10);
    if (!Number.isFinite(page) || page < 1) page = 1;
    if (page > totalPages) page = totalPages;
    store[elId] = page;
    return { page: page, totalPages: totalPages, start: (page - 1) * size };
  }

  function psPagerMarkup(attr, id, page, totalPages) {
    if (totalPages <= 1) return '';
    return '<div class="ps-list-pager">' +
      '<button type="button" class="btn btn-ghost btn-sm" ' + attr + '="' + escAttr(id) + '" data-page-delta="-1"' + (page <= 1 ? ' disabled' : '') + '>上一页</button>' +
      '<span>' + page + ' / ' + totalPages + '</span>' +
      '<button type="button" class="btn btn-ghost btn-sm" ' + attr + '="' + escAttr(id) + '" data-page-delta="1"' + (page >= totalPages ? ' disabled' : '') + '>下一页</button>' +
    '</div>';
  }

  function updatePsMultiSelectSummary(el) {
    if (!el) return;
    var summary = el.querySelector('[data-ps-multi-summary]');
    if (!summary) return;
    var checked = Array.prototype.slice.call(el.querySelectorAll('input[type="checkbox"]:checked'));
    var names = checked.slice(0, 2).map(function(input) {
      var strong = input.closest('label') && input.closest('label').querySelector('strong');
      return strong ? strong.textContent : input.value;
    });
    summary.textContent = checked.length ? ('已选 ' + checked.length + ' 项' + (names.length ? ' · ' + names.join('、') : '')) : '未选择';
  }

  function renderPsMultiSelect(elId, rows, opts) {
    var el = $(elId);
    if (!el) return;
    opts = opts || {};
    rows = Array.isArray(rows) ? rows : [];
    if (!rows.length) {
      el.innerHTML = '<div class="ps-empty">' + esc(opts.empty || '暂无') + '</div>';
      return;
    }
    var info = psPageInfo(elId, rows, PS_MULTI_SELECT_PAGE_SIZE, psMultiSelectPages);
    var pageRows = rows.slice(info.start, info.start + PS_MULTI_SELECT_PAGE_SIZE);
    var selected = opts.selected || {};
    var idFn = opts.id || function(row) { return row.id; };
    var titleFn = opts.title || function(row) { return row.name || row.title || row.id; };
    var subtitleFn = opts.subtitle || function() { return ''; };
    var kindFn = opts.kindFn || function() { return opts.kind || ''; };
    var attrName = opts.attributeName || 'data-ps-option';
    var selectedRows = rows.filter(function(row) { return !!selected[String(idFn(row))]; });
    var selectedNames = selectedRows.slice(0, 2).map(function(row) { return titleFn(row); });
    var label = selectedRows.length ? ('已选 ' + selectedRows.length + ' 项' + (selectedNames.length ? ' · ' + selectedNames.join('、') : '')) : '未选择';
    el.innerHTML = '<details class="ps-multi-select">' +
      '<summary><span>' + esc(opts.label || '选择') + '</span><strong data-ps-multi-summary>' + esc(label) + '</strong><i>⌄</i></summary>' +
      '<div class="ps-multi-select-menu"><div class="ps-multi-select-options">' + pageRows.map(function(row) {
        var id = String(idFn(row) || '');
        var kind = String(kindFn(row) || '');
        var subtitle = String(subtitleFn(row) || '');
        return '<label class="ps-option ps-multi-select-option">' +
          '<input type="checkbox" ' + attrName + '="' + escAttr(kind) + '" value="' + escAttr(id) + '"' + (selected[id] ? ' checked' : '') + '>' +
          '<span><strong>' + esc(titleFn(row)) + '</strong>' + (subtitle ? '<small>' + esc(subtitle) + '</small>' : '') + '</span>' +
        '</label>';
      }).join('') + '</div><div class="ps-multi-select-footer">' + psPagerMarkup('data-ps-multi-page', elId, info.page, info.totalPages) + '</div></div></details>';
    bindOptionChecks(el, opts.kind, selected);
    el.querySelectorAll('input[' + attrName + ']').forEach(function(input) {
      input.addEventListener('change', function() { updatePsMultiSelectSummary(el); });
    });
    el.querySelectorAll('[data-ps-multi-page]').forEach(function(btn) {
      btn.addEventListener('click', function(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        var next = (psMultiSelectPages[elId] || 1) + Number(btn.getAttribute('data-page-delta') || 0);
        psMultiSelectPages[elId] = Math.max(1, Math.min(info.totalPages, next));
        renderPsMultiSelect(elId, rows, opts);
        var details = $(elId).querySelector('details');
        if (details) details.open = true;
      });
    });
  }

  function ensurePsListPagingHandlers() {
    if (psListPagingBound) return;
    psListPagingBound = true;
    document.addEventListener('click', function(ev) {
      var btn = ev.target && ev.target.closest ? ev.target.closest('[data-ps-list-page]') : null;
      if (!btn) return;
      ev.preventDefault();
      var id = btn.getAttribute('data-ps-list-page') || '';
      psListPages[id] = Math.max(1, (psListPages[id] || 1) + Number(btn.getAttribute('data-page-delta') || 0));
      renderAllLists();
    });
  }

  function psListPageRows(elId, rows) {
    var info = psPageInfo(elId, rows, PS_LIST_PAGE_SIZE, psListPages);
    return {
      rows: rows.slice(info.start, info.start + PS_LIST_PAGE_SIZE),
      pager: psPagerMarkup('data-ps-list-page', elId, info.page, info.totalPages)
    };
  }

  function renderTemplateOptions(elId, rows, opts) {
    var el = $(elId);
    if (!el) return;
    opts = opts || {};
    renderPsMultiSelect(elId, rows, Object.assign({}, opts, {
      label: opts.label || '选择资源',
      attributeName: 'data-ps-option'
    }));
    return;
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

  function personalDigitalHumanMediaUrl(value) {
    var url = String(value || '').trim();
    return /^(https?:)?\/\//i.test(url) || url.startsWith('/') ? url : '';
  }

  function normalizePersonalDigitalHumanTemplate(item) {
    if (!item || typeof item !== 'object') return null;
    var styleId = String(item.style_id || item.styleId || item.id || '').trim();
    if (!styleId) return null;
    return {
      scene: String(item.scene || item.template_scene || 'realMan').trim() || 'realMan',
      style_id: styleId,
      name: String(item.name || item.title || '未命名模板').trim() || '未命名模板',
      cover_url: personalDigitalHumanMediaUrl(item.cover_url || item.coverUrl || item.imageUrl),
      demo_url: personalDigitalHumanMediaUrl(item.demo_url || item.demoUrl || item.videoUrl),
      materials: Array.isArray(item.materials) ? item.materials.map(function(row) { return Object.assign({}, row || {}); }) : [],
      material_sound_switch: !!(item.material_sound_switch ?? item.materialSoundSwitch),
      introduce_name: String(item.introduce_name || item.introduceName || '').trim(),
      introduce_description: String(item.introduce_description || item.introduceDescription || '').trim(),
      pack_rules: (item.pack_rules || item.packRules) && typeof (item.pack_rules || item.packRules) === 'object' ? Object.assign({}, item.pack_rules || item.packRules) : {},
      process_rules: (item.process_rules || item.processRules) && typeof (item.process_rules || item.processRules) === 'object' ? Object.assign({}, item.process_rules || item.processRules) : {}
    };
  }

  function clonePersonalDigitalHumanTemplate(item) {
    var normalized = normalizePersonalDigitalHumanTemplate(item);
    return normalized ? {
      scene: normalized.scene,
      style_id: normalized.style_id,
      name: normalized.name,
      cover_url: normalized.cover_url,
      demo_url: normalized.demo_url,
      materials: (normalized.materials || []).map(function(row) { return Object.assign({}, row || {}); }),
      material_sound_switch: !!normalized.material_sound_switch,
      introduce_name: normalized.introduce_name,
      introduce_description: normalized.introduce_description,
      pack_rules: Object.assign({}, normalized.pack_rules || {}),
      process_rules: Object.assign({}, normalized.process_rules || {})
    } : null;
  }

  function personalDigitalHumanTemplateLabel(item) {
    return String((item && (item.name || item.style_id)) || '').trim() || '未选择';
  }

  function renderPersonalDigitalHumanTemplateSummary() {
    var box = $('psDigitalHumanTemplateSummary');
    if (!box) return;
    var item = state.personalSelectedDigitalHumanTemplate;
    if (!item) {
      box.className = 'ps-dh-summary is-empty';
      box.innerHTML = '<div class="ps-dh-summary-note">未选择数字人剪辑模板。保存后，含数字人口播的工作流会读取这里的模板信息。</div>';
      return;
    }
    box.className = 'ps-dh-summary';
    box.innerHTML = '<div class="ps-dh-summary-cover">' +
      (item.cover_url ? '<img src="' + escAttr(item.cover_url) + '" alt="" loading="lazy" referrerpolicy="no-referrer">' : '<span>' + esc(String(personalDigitalHumanTemplateLabel(item)).slice(0, 2)) + '</span>') +
    '</div>' +
    '<div class="ps-dh-summary-copy">' +
      '<strong>' + esc(personalDigitalHumanTemplateLabel(item)) + '</strong>' +
      '<small>样式ID ' + esc(item.style_id || '') + '</small>' +
      (item.demo_url ? '<small>有样片，可在模板列表里预览</small>' : '<small>暂无样片</small>') +
    '</div>' +
    '<div class="ps-dh-summary-note">已写入模板 meta.digital_human_template</div>';
  }

  function filteredPersonalDigitalHumanTemplates() {
    var query = String(state.personalDigitalHumanTemplateSearch || '').trim().toLowerCase();
    var rows = Array.isArray(state.personalDigitalHumanTemplates) ? state.personalDigitalHumanTemplates : [];
    if (!query) return rows;
    return rows.filter(function(item) {
      return String(item.name || '').toLowerCase().indexOf(query) >= 0
        || String(item.style_id || '').toLowerCase().indexOf(query) >= 0;
    });
  }

  var PERSONAL_DH_TEMPLATE_PAGE_SIZE = 12;

  function clampPersonalDigitalHumanTemplatePage(totalRows) {
    var totalPages = Math.max(1, Math.ceil(Math.max(0, Number(totalRows) || 0) / PERSONAL_DH_TEMPLATE_PAGE_SIZE));
    var page = parseInt(state.personalDigitalHumanTemplatePage, 10);
    if (!Number.isFinite(page) || page < 1) page = 1;
    if (page > totalPages) page = totalPages;
    state.personalDigitalHumanTemplatePage = page;
    return { page: page, totalPages: totalPages };
  }

  function renderPersonalDigitalHumanTemplatePicker() {
    var modal = $('psDigitalHumanTemplateModal');
    var grid = $('psDigitalHumanTemplateGrid');
    if (!grid) return;
    var rows = filteredPersonalDigitalHumanTemplates();
    var total = (state.personalDigitalHumanTemplates || []).length;
    var pageInfo = clampPersonalDigitalHumanTemplatePage(rows.length);
    var start = (pageInfo.page - 1) * PERSONAL_DH_TEMPLATE_PAGE_SIZE;
    var pageRows = rows.slice(start, start + PERSONAL_DH_TEMPLATE_PAGE_SIZE);
    var count = $('psDigitalHumanTemplateCount');
    if (count) {
      var visibleRange = rows.length ? ((start + 1) + '-' + Math.min(start + PERSONAL_DH_TEMPLATE_PAGE_SIZE, rows.length)) : '0';
      count.textContent = (state.personalDigitalHumanTemplateSearch ? (rows.length + ' / ' + total) : String(total)) + ' 个模板 · 当前 ' + visibleRange;
    }
    var status = $('psDigitalHumanTemplateStatus');
    if (status) {
      status.textContent = state.personalDigitalHumanTemplatesLoading
        ? '正在加载数字人模板...'
        : (state.personalDigitalHumanTemplatesError || (rows.length ? '' : (state.personalDigitalHumanTemplateSearch ? '没有匹配的模板' : '暂无可用模板')));
      status.className = 'ps-dh-modal-status' + (state.personalDigitalHumanTemplatesError ? ' error' : '');
    }
    var pageText = $('psDigitalHumanTemplatePageText');
    var prevBtn = $('psDigitalHumanTemplatePrev');
    var nextBtn = $('psDigitalHumanTemplateNext');
    if (pageText) pageText.textContent = pageInfo.page + ' / ' + pageInfo.totalPages;
    if (prevBtn) prevBtn.disabled = pageInfo.page <= 1 || state.personalDigitalHumanTemplatesLoading;
    if (nextBtn) nextBtn.disabled = pageInfo.page >= pageInfo.totalPages || state.personalDigitalHumanTemplatesLoading;
    if (!modal || !modal.classList.contains('is-visible')) return;
    if (state.personalDigitalHumanTemplatesLoading && !rows.length) {
      grid.innerHTML = Array.from({ length: 6 }, function() { return '<div class="ps-dh-card skeleton"></div>'; }).join('');
      return;
    }
    var selectedId = String((state.personalDigitalHumanTemplateDraft || {}).style_id || '');
    grid.innerHTML = pageRows.map(function(item) {
      var selected = String(item.style_id || '') === selectedId;
      return '<article class="ps-dh-card' + (selected ? ' is-selected' : '') + '" data-ps-dh-template="' + escAttr(item.style_id) + '" tabindex="0" role="radio" aria-checked="' + (selected ? 'true' : 'false') + '">' +
        '<div class="ps-dh-card-cover">' +
          (item.cover_url ? '<img src="' + escAttr(item.cover_url) + '" alt="" loading="lazy" referrerpolicy="no-referrer">' : '<span>' + esc(String(item.name || '模板').slice(0, 2)) + '</span>') +
        '</div>' +
        '<div class="ps-dh-card-body">' +
          '<strong title="' + escAttr(item.name) + '">' + esc(item.name) + '</strong>' +
          '<small>' + esc(item.style_id) + '</small>' +
        '</div>' +
      '</article>';
    }).join('');
  }

  function loadPersonalDigitalHumanTemplates(force) {
    if (state.personalDigitalHumanTemplatesLoading) return Promise.resolve();
    if (!force && state.personalDigitalHumanTemplatesLoaded) {
      renderPersonalDigitalHumanTemplatePicker();
      return Promise.resolve();
    }
    state.personalDigitalHumanTemplatesLoading = true;
    state.personalDigitalHumanTemplatesError = '';
    if (force) state.personalDigitalHumanTemplates = [];
    renderPersonalDigitalHumanTemplatePicker();
    var rows = [];
    var seen = {};
    var sid = '';
    var chain = Promise.resolve();
    var page = 0;
    function loadNextPage() {
      if (page >= 20) return Promise.resolve();
      page += 1;
      return cloudJson('/api/shanjian-smart-clip/templates', {
        method: 'POST',
        body: { page_size: 60, sid: sid, scene: 'realMan', sort_by: 'desc' }
      }).then(function(data) {
        (Array.isArray(data.results) ? data.results : []).forEach(function(raw) {
          var item = normalizePersonalDigitalHumanTemplate(raw);
          if (!item || seen[item.style_id]) return;
          seen[item.style_id] = true;
          rows.push(item);
        });
        var nextSid = String(data.sid || '').trim();
        if (!nextSid || nextSid === sid) return;
        sid = nextSid;
        return loadNextPage();
      });
    }
    return loadNextPage().then(function() {
      state.personalDigitalHumanTemplates = rows;
      state.personalDigitalHumanTemplatesLoaded = true;
      var selectedId = String((state.personalDigitalHumanTemplateDraft || {}).style_id || '');
      var selected = selectedId ? rows.find(function(item) { return item.style_id === selectedId; }) : null;
      if (selected) state.personalDigitalHumanTemplateDraft = clonePersonalDigitalHumanTemplate(selected);
    }).catch(function(err) {
      state.personalDigitalHumanTemplatesError = err && err.message ? err.message : '模板加载失败，请重试';
    }).finally(function() {
      state.personalDigitalHumanTemplatesLoading = false;
      renderPersonalDigitalHumanTemplatePicker();
    });
  }

  function openPersonalDigitalHumanTemplatePicker() {
    state.personalDigitalHumanTemplateDraft = clonePersonalDigitalHumanTemplate(state.personalSelectedDigitalHumanTemplate);
    state.personalDigitalHumanTemplateSearch = '';
    state.personalDigitalHumanTemplatePage = 1;
    if ($('psDigitalHumanTemplateSearch')) $('psDigitalHumanTemplateSearch').value = '';
    var modal = $('psDigitalHumanTemplateModal');
    if (modal) {
      modal.hidden = false;
      modal.setAttribute('aria-hidden', 'false');
      modal.classList.add('is-visible');
      document.documentElement.classList.add('ps-dh-modal-open');
    }
    renderPersonalDigitalHumanTemplatePicker();
    loadPersonalDigitalHumanTemplates(false);
  }

  function closePersonalDigitalHumanTemplatePicker() {
    var modal = $('psDigitalHumanTemplateModal');
    if (modal) {
      modal.classList.remove('is-visible');
      modal.setAttribute('aria-hidden', 'true');
      modal.hidden = true;
    }
    document.documentElement.classList.remove('ps-dh-modal-open');
    state.personalDigitalHumanTemplateDraft = null;
  }

  function confirmPersonalDigitalHumanTemplate() {
    state.personalSelectedDigitalHumanTemplate = clonePersonalDigitalHumanTemplate(state.personalDigitalHumanTemplateDraft);
    if (state.personalSelectedDigitalHumanTemplate) state.personalDigitalHumanTemplateExplicitlyCleared = false;
    renderPersonalDigitalHumanTemplateSummary();
    closePersonalDigitalHumanTemplatePicker();
  }

  function renderCurrentTemplate() {
    var box = $('psCurrentTemplateBox');
    if (!box) return;
    var current = state.defaultItem || {};
    var keywordCount = Array.isArray(current.keyword_ids) ? current.keyword_ids.length : 0;
    var competitorCount = Array.isArray(current.competitor_ids) ? current.competitor_ids.length : 0;
    var memoryCount = Array.isArray(current.memory_doc_ids) ? current.memory_doc_ids.length : 0;
    var meta = current.meta && typeof current.meta === 'object' ? current.meta : {};
    var languageLabel = ipTemplateLanguageLabel(templateLanguageFromParts(current.requirements, meta, ''));
    var sourceId = String(meta.current_template_id || '').trim();
    var sourceTemplate = sourceId ? (state.templates || []).find(function(row) { return String(row.id || '') === sourceId; }) : null;
    var title = sourceTemplate ? templateName(sourceTemplate) : (current.name && !isPersonalDefaultTemplate(current) ? templateName(current) : '未指定模板');
    var digitalHumanLabel = meta.digital_human_template && typeof meta.digital_human_template === 'object'
      ? String(meta.digital_human_template.name || meta.digital_human_template.style_id || '已选择数字人模板')
      : '未选择数字人模板';
    box.innerHTML = '<article class="ps-template-card">' +
      '<div><strong>' + esc(title) + '</strong><div class="ps-template-meta">语种 ' + esc(languageLabel) + ' · 关键词 ' + keywordCount + ' · 同行 ' + competitorCount + ' · 记忆 ' + memoryCount + ' · 数字人 ' + esc(digitalHumanLabel) + '</div></div>' +
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
    var page = psListPageRows('psSavedTemplateList', rows);
    list.innerHTML = page.rows.map(function(row) {
      var id = String(row.id || '');
      var k = Array.isArray(row.keyword_ids) ? row.keyword_ids.length : 0;
      var c = Array.isArray(row.competitor_ids) ? row.competitor_ids.length : 0;
      var m = Array.isArray(row.memory_doc_ids) ? row.memory_doc_ids.length : 0;
      var dh = row.meta && typeof row.meta === 'object' && row.meta.digital_human_template && typeof row.meta.digital_human_template === 'object'
        ? String(row.meta.digital_human_template.name || row.meta.digital_human_template.style_id || '已选数字人')
        : '未选数字人';
      var languageLabel = ipTemplateLanguageLabel(templateLanguageFromParts(row.requirements, row.meta, row.language || row.target_language || ''));
      return '<article class="ps-template-card">' +
        '<div><strong>' + esc(templateName(row)) + '</strong><div class="ps-template-meta">语种 ' + esc(languageLabel) + ' · 关键词 ' + k + ' · 同行 ' + c + ' · 记忆 ' + m + ' · 数字人 ' + esc(dh) + '</div></div>' +
        '<div class="ps-item-actions">' +
          '<button type="button" class="btn btn-primary btn-sm" data-use-template="' + escAttr(id) + '">' + (row.source === 'agent' ? '套用' : '设为当前') + '</button>' +
          (row.source === 'agent'
            ? ''
            : '<button type="button" class="btn btn-ghost btn-sm" data-edit-template="' + escAttr(id) + '">编辑</button>' +
              '<button type="button" class="btn btn-ghost btn-sm" data-delete-template="' + escAttr(id) + '">删除</button>') +
        '</div>' +
      '</article>';
    }).join('') + page.pager;
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
    box.querySelectorAll('[data-download-generated-doc]').forEach(function(button) {
      button.addEventListener('click', function() {
        var key = button.getAttribute('data-download-generated-doc') || '';
        var text = state.generatedDocuments && state.generatedDocuments[key] || '';
        downloadTextFile(docTypeLabel(key) + '.md', '# ' + docTypeLabel(key) + '\n\n' + text)
          .then(function(result) {
            if (!result || !result.cancelled) setMsg(result && result.path ? '已保存至：' + result.path : '下载已开始');
          })
          .catch(function(err) { setMsg(err.message || '下载失败', true); });
      });
    });
    list.querySelectorAll('[data-delete-template]').forEach(function(btn) {
      btn.addEventListener('click', function() { deleteTemplate(btn.getAttribute('data-delete-template') || '', btn); });
    });
  }

  function parseHostSaveResult(value) {
    if (value && typeof value === 'object') return value;
    if (typeof value !== 'string') return {};
    try { return JSON.parse(value); } catch (e) { return { ok: value === 'ok' }; }
  }

  function saveTextDownload(filename, text) {
    filename = filename || '个人记忆资料.md';
    text = String(text || '');
    if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.save_text_file === 'function') {
      return Promise.resolve(window.pywebview.api.save_text_file(filename, text)).then(function(value) {
        var result = parseHostSaveResult(value);
        if (!result.ok && !result.cancelled) throw new Error(result.error || '保存失败');
        return result;
      });
    }
    if (window.LobsterAndroid && typeof window.LobsterAndroid.saveTextFile === 'function') {
      try {
        var androidResult = parseHostSaveResult(window.LobsterAndroid.saveTextFile(filename, 'text/markdown', text));
        if (!androidResult.ok && !androidResult.cancelled) throw new Error(androidResult.error || '保存失败');
        return Promise.resolve(androidResult);
      } catch (err) {
        return Promise.reject(err);
      }
    }
    var blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
    return Promise.resolve({ ok: true, browser_download: true });
  }

  function downloadTextFile(filename, text) {
    return saveTextDownload(filename, text);
  }

  function downloadMemoryDocument(id, fallbackName) {
    if (!id) return Promise.reject(new Error('资料不存在'));
    return fetch(cloudBase() + '/api/personal-settings/memory-documents/' + encodeURIComponent(id) + '/download', {
      headers: headers(false)
    }).then(function(resp) {
      if (!resp.ok) return resp.text().then(function(text) { throw new Error(text || '下载失败'); });
      return resp.text();
    }).then(function(text) {
      return saveTextDownload(fallbackName || '个人记忆资料.md', text);
    });
  }

  function digitalHumanResourceKey(item, kind) {
    item = item && typeof item === 'object' ? item : {};
    var provider = String(item.provider || item.source || '').trim().toLowerCase();
    var id = kind === 'voice'
      ? String(item.voice || item.voice_id || item.speaker_id || item.speakerId || item.id || '').trim()
      : String(item.virtualman_id || item.virtualmanId || item.avatar || item.avatar_id || item.id || '').trim();
    return provider + ':' + id;
  }

  function normalizePersonalDigitalHumanResources(value) {
    value = value && typeof value === 'object' ? value : {};
    function normalizeList(list, kind) {
      var seen = {};
      return (Array.isArray(list) ? list : []).map(function(item) {
        if (!item || typeof item !== 'object') return null;
        var row = Object.assign({}, item);
        var key = digitalHumanResourceKey(row, kind);
        if (!key || key.endsWith(':')) return null;
        if (seen[key]) return null;
        seen[key] = true;
        row.provider = String(row.provider || row.source || (kind === 'avatar' && (row.virtualman_id || row.virtualmanId) ? 'shanjian' : 'hifly')).trim().toLowerCase();
        if (kind === 'avatar') {
          row.virtualman_id = String(row.virtualman_id || row.virtualmanId || ((row.provider === 'shanjian' || row.provider === 'shanjian_v2' || row.provider === 'digital_human') ? (row.avatar || '') : '')).trim();
          row.avatar = String(row.avatar || row.avatar_id || row.avatarId || '').trim();
        } else {
          row.voice = String(row.voice || row.voice_id || row.speaker_id || row.speakerId || '').trim();
        }
        row.title = String(row.title || row.name || '未命名资源').trim() || '未命名资源';
        return row;
      }).filter(Boolean);
    }
    return { avatars: normalizeList(value.avatars, 'avatar'), voices: normalizeList(value.voices, 'voice') };
  }

  function clonePersonalDigitalHumanResources(value) {
    var normalized = normalizePersonalDigitalHumanResources(value);
    return {
      avatars: normalized.avatars.map(function(row) { return Object.assign({}, row); }),
      voices: normalized.voices.map(function(row) { return Object.assign({}, row); })
    };
  }

  function resourceTitle(row) {
    return String(row && (row.title || row.name || row.virtualman_id || row.avatar || row.voice) || '未命名资源');
  }

  function resourceSubtitle(row, kind) {
    if (kind === 'voice') return String(row && (row.voice || row.voice_id || row.speaker_id) || '');
    return String(row && (row.virtualman_id || row.avatar || row.avatar_id) || '');
  }

  function personalDigitalHumanResourceOptions(kind, value) {
    var resources = normalizePersonalDigitalHumanResources(value || state.personalDigitalHumanResources);
    var selectedRows = resources[kind === 'avatar' ? 'avatars' : 'voices'];
    var loadedRows = kind === 'avatar' ? state.personalDigitalHumanAvatarOptions : state.personalDigitalHumanVoiceOptions;
    var rows = [];
    var seen = {};
    loadedRows.concat(selectedRows).forEach(function(row) {
      var key = digitalHumanResourceKey(row, kind);
      if (!key || key.endsWith(':') || seen[key]) return;
      seen[key] = true;
      rows.push(row);
    });
    return rows;
  }

  function renderPersonalDigitalHumanResources() {
    var resources = normalizePersonalDigitalHumanResources(state.personalDigitalHumanResources);
    state.personalDigitalHumanResources = resources;
    ['avatar', 'voice'].forEach(function(kind) {
      var el = $(kind === 'avatar' ? 'psDigitalHumanAvatarList' : 'psDigitalHumanVoiceList');
      var count = $(kind === 'avatar' ? 'psDigitalHumanAvatarCount' : 'psDigitalHumanVoiceCount');
      if (!el) return;
      var selectedRows = resources[kind === 'avatar' ? 'avatars' : 'voices'];
      if (count) count.textContent = String(selectedRows.length);
      if (state.personalDigitalHumanResourcesLoading && !selectedRows.length) {
        el.innerHTML = '<div class="ps-empty">正在加载资源...</div>';
        return;
      }
      if (!selectedRows.length) {
        el.innerHTML = '<button type="button" class="ps-resource-empty" data-open-ps-resource="' + escAttr(kind) + '">尚未选择，点击添加</button>';
        return;
      }
      var visible = selectedRows.slice(0, 4);
      el.innerHTML = visible.map(function(row) {
        return '<div class="ps-option ps-resource-summary-item"><span><strong>' + esc(resourceTitle(row)) + '</strong><small>' +
          esc(resourceSubtitle(row, kind) + (row.provider ? ' · ' + row.provider : '')) + '</small></span></div>';
      }).join('') + (selectedRows.length > visible.length
        ? '<button type="button" class="ps-resource-more" data-open-ps-resource="' + escAttr(kind) + '">另有 ' + (selectedRows.length - visible.length) + ' 个已选择</button>'
        : '');
    });
  }

  var PERSONAL_DH_RESOURCE_PAGE_SIZE = 20;

  function filteredPersonalDigitalHumanResourceRows() {
    var kind = state.personalDigitalHumanResourcePickerKind === 'voice' ? 'voice' : 'avatar';
    var rows = personalDigitalHumanResourceOptions(kind, state.personalDigitalHumanResourceDraft);
    var query = String(state.personalDigitalHumanResourceQuery || '').trim().toLowerCase();
    if (!query) return rows;
    return rows.filter(function(row) {
      return [resourceTitle(row), resourceSubtitle(row, kind), row && (row.provider || row.source), row && row.status].some(function(value) {
        return String(value || '').toLowerCase().indexOf(query) >= 0;
      });
    });
  }

  function renderPersonalDigitalHumanResourcePicker() {
    var modal = $('psDigitalHumanResourceModal');
    if (!modal || !modal.classList.contains('is-visible')) return;
    var kind = state.personalDigitalHumanResourcePickerKind === 'voice' ? 'voice' : 'avatar';
    var listKey = kind === 'avatar' ? 'avatars' : 'voices';
    var draft = normalizePersonalDigitalHumanResources(state.personalDigitalHumanResourceDraft);
    state.personalDigitalHumanResourceDraft = draft;
    var selectedRows = draft[listKey];
    var selected = {};
    selectedRows.forEach(function(row) { selected[digitalHumanResourceKey(row, kind)] = true; });
    var rows = filteredPersonalDigitalHumanResourceRows();
    var totalPages = Math.max(1, Math.ceil(rows.length / PERSONAL_DH_RESOURCE_PAGE_SIZE));
    var page = parseInt(state.personalDigitalHumanResourcePage, 10);
    if (!Number.isFinite(page) || page < 1) page = 1;
    if (page > totalPages) page = totalPages;
    state.personalDigitalHumanResourcePage = page;
    var start = (page - 1) * PERSONAL_DH_RESOURCE_PAGE_SIZE;
    var pageRows = rows.slice(start, start + PERSONAL_DH_RESOURCE_PAGE_SIZE);
    if ($('psDigitalHumanResourceTitle')) $('psDigitalHumanResourceTitle').textContent = kind === 'avatar' ? '选择数字人形象 / 分身' : '选择声音';
    if ($('psDigitalHumanResourceStats')) $('psDigitalHumanResourceStats').textContent = '搜索结果 ' + rows.length + ' 个，已选 ' + selectedRows.length + ' 个';
    var tabs = $('psDigitalHumanResourceTabs');
    if (tabs) Array.from(tabs.querySelectorAll('[data-ps-resource-kind]')).forEach(function(button) {
      var buttonKind = button.getAttribute('data-ps-resource-kind') === 'voice' ? 'voice' : 'avatar';
      var buttonCount = draft[buttonKind === 'avatar' ? 'avatars' : 'voices'].length;
      button.textContent = (buttonKind === 'avatar' ? '形象 / 分身' : '声音') + ' (' + buttonCount + ')';
      button.classList.toggle('active', buttonKind === kind);
    });
    var list = $('psDigitalHumanResourceList');
    if (list) {
      list.innerHTML = pageRows.length ? pageRows.map(function(row) {
        var key = digitalHumanResourceKey(row, kind);
        var checked = !!selected[key];
        var cover = kind === 'avatar' ? personalDigitalHumanMediaUrl(row.cover_url || row.image_url) : '';
        var marker = kind === 'avatar' ? resourceTitle(row).slice(0, 1) : '声';
        return '<label class="ps-resource-option' + (checked ? ' selected' : '') + '">' +
          '<input type="checkbox" data-ps-resource-key="' + escAttr(key) + '"' + (checked ? ' checked' : '') + '>' +
          '<span class="ps-resource-thumb">' + (cover ? '<img src="' + escAttr(cover) + '" alt="" loading="lazy" referrerpolicy="no-referrer">' : esc(marker)) + '</span>' +
          '<span class="ps-resource-copy"><strong>' + esc(resourceTitle(row)) + '</strong><small>' + esc(resourceSubtitle(row, kind) + (row.provider ? ' · ' + row.provider : '')) + '</small></span>' +
        '</label>';
      }).join('') : '<div class="ps-resource-empty-state">没有匹配的资源</div>';
    }
    var allSelected = !!rows.length && rows.every(function(row) { return !!selected[digitalHumanResourceKey(row, kind)]; });
    if ($('psDigitalHumanResourceSelectAll')) {
      $('psDigitalHumanResourceSelectAll').textContent = allSelected ? '取消全选搜索结果' : '全选搜索结果';
      $('psDigitalHumanResourceSelectAll').disabled = !rows.length;
    }
    if ($('psDigitalHumanResourcePageText')) $('psDigitalHumanResourcePageText').textContent = page + ' / ' + totalPages;
    if ($('psDigitalHumanResourcePrev')) $('psDigitalHumanResourcePrev').disabled = page <= 1;
    if ($('psDigitalHumanResourceNext')) $('psDigitalHumanResourceNext').disabled = page >= totalPages;
  }

  function openPersonalDigitalHumanResourcePicker(kind) {
    state.personalDigitalHumanResourcePickerKind = kind === 'voice' ? 'voice' : 'avatar';
    state.personalDigitalHumanResourceQuery = '';
    state.personalDigitalHumanResourcePage = 1;
    state.personalDigitalHumanResourceDraft = clonePersonalDigitalHumanResources(state.personalDigitalHumanResources);
    if ($('psDigitalHumanResourceSearch')) $('psDigitalHumanResourceSearch').value = '';
    var modal = $('psDigitalHumanResourceModal');
    if (modal) {
      modal.hidden = false;
      modal.setAttribute('aria-hidden', 'false');
      modal.classList.add('is-visible');
      document.documentElement.classList.add('ps-dh-modal-open');
    }
    renderPersonalDigitalHumanResourcePicker();
    loadPersonalDigitalHumanResources().then(renderPersonalDigitalHumanResourcePicker);
  }

  function closePersonalDigitalHumanResourcePicker() {
    var modal = $('psDigitalHumanResourceModal');
    if (modal) {
      modal.classList.remove('is-visible');
      modal.setAttribute('aria-hidden', 'true');
      modal.hidden = true;
    }
    if (!$('psDigitalHumanTemplateModal') || $('psDigitalHumanTemplateModal').hidden) document.documentElement.classList.remove('ps-dh-modal-open');
    state.personalDigitalHumanResourceDraft = null;
  }

  function confirmPersonalDigitalHumanResources() {
    state.personalDigitalHumanResources = clonePersonalDigitalHumanResources(state.personalDigitalHumanResourceDraft);
    renderPersonalDigitalHumanResources();
    closePersonalDigitalHumanResourcePicker();
  }

  function loadPersonalDigitalHumanResources() {
    if (state.personalDigitalHumanResourcesLoading) return Promise.resolve();
    state.personalDigitalHumanResourcesLoading = true;
    renderPersonalDigitalHumanResources();
    function loadKind(kind, page, rows) {
      page = page || 1;
      rows = rows || [];
      if (page > 20) return Promise.resolve(rows);
      return cloudJson('/api/h5/assets/digital-library?kind=' + encodeURIComponent(kind) + '&page=' + page + '&size=100').then(function(data) {
        var items = Array.isArray(data.items) ? data.items : [];
        rows = rows.concat(items);
        var total = Number(data.total || 0);
        if (!items.length || items.length < 100 || (total > 0 && rows.length >= total)) return rows;
        return loadKind(kind, page + 1, rows);
      });
    }
    return Promise.all([
      loadKind('avatar').catch(function() { return []; }),
      loadKind('voice').catch(function() { return []; })
    ]).then(function(results) {
      state.personalDigitalHumanAvatarOptions = results[0];
      state.personalDigitalHumanVoiceOptions = results[1];
    }).finally(function() {
      state.personalDigitalHumanResourcesLoading = false;
      renderPersonalDigitalHumanResources();
      renderPersonalDigitalHumanResourcePicker();
    });
  }

  function renderKeywords() {
    var el = $('psKeywordList');
    if (!el) return;
    if (!state.keywords.length) {
      el.innerHTML = '<div class="ps-empty">还没有关键词。</div>';
      return;
    }
    var page = psListPageRows('psKeywordList', state.keywords);
    el.innerHTML = page.rows.map(function(row) {
      var id = String(row.id || '');
      return '<article class="ps-option is-action">' +
        '<div><strong>' + esc(row.display_name || row.keyword || ('关键词 #' + id)) + '</strong>' +
        '<small>关键词：' + esc(row.keyword || '') + (row.last_fetch_at ? ' · 最近同步：' + esc(row.last_fetch_at) : '') + '</small></div>' +
        '<div class="ps-item-actions">' +
          '<button type="button" class="btn btn-ghost btn-sm" data-delete-keyword="' + escAttr(id) + '">删除</button>' +
        '</div>' +
      '</article>';
    }).join('') + page.pager;
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
    var page = psListPageRows('psCompetitorList', state.competitors);
    el.innerHTML = page.rows.map(function(row) {
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
    }).join('') + page.pager;
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
    renderUploadedDocuments();
    if (!el) return;
    if (!state.memories.length) {
      el.innerHTML = '<div class="ps-empty">还没有保存的记忆文件。</div>';
      return;
    }
    var page = psListPageRows('psMemoryList', state.memories);
    el.innerHTML = page.rows.map(function(doc) {
      var id = memoryId(doc);
      var readOnly = !!(doc && (doc.read_only || doc.source === 'agent'));
      var tag = readOnly ? '代理商' : '个人';
      return '<article class="ps-memory-item">' +
        '<div><strong>' + esc(memoryTitle(doc)) + '</strong>' +
        '<small>' + esc(tag + (doc.notes || doc.filename ? ' · ' + (doc.notes || doc.filename) : '') + (doc.created_at ? ' · ' + doc.created_at : '')) + '</small></div>' +
        '<div class="ps-actions">' +
          '<button type="button" class="btn btn-ghost btn-sm" data-preview-memory="' + escAttr(id) + '">预览</button>' +
          '<button type="button" class="btn btn-ghost btn-sm" data-download-memory="' + escAttr(id) + '">下载</button>' +
          (readOnly ? '' : '<button type="button" class="btn btn-ghost btn-sm" data-delete-memory="' + escAttr(id) + '">删除</button>') +
        '</div>' +
      '</article>';
    }).join('') + page.pager;
    el.querySelectorAll('[data-preview-memory]').forEach(function(btn) {
      btn.addEventListener('click', function() { previewMemory(btn.getAttribute('data-preview-memory') || ''); });
    });
    el.querySelectorAll('[data-download-memory]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var id = btn.getAttribute('data-download-memory') || '';
        var doc = state.memories.filter(function(row) { return memoryId(row) === id; })[0] || {};
        downloadMemoryDocument(id, (memoryTitle(doc) || '个人记忆资料') + '.md')
          .then(function(result) {
            if (!result || !result.cancelled) setMsg(result && result.path ? '已保存至：' + result.path : '下载已开始');
          })
          .catch(function(err) { setMsg(err.message || '下载失败', true); });
      });
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

  function uploadedMemoryDocRows() {
    return (state.memories || []).filter(function(doc) {
      return !(doc && (doc.read_only || doc.source === 'agent')) && isUploadedMemoryDoc(doc);
    });
  }

  function renderUploadedDocuments() {
    var el = $('psUploadDocList');
    var count = $('psUploadHistoryCount');
    if (!el) return;
    var rows = uploadedMemoryDocRows();
    if (count) count.textContent = rows.length + ' 条';
    if (!rows.length) {
      el.innerHTML = '<div class="ps-empty">暂无上传资料。</div>';
      return;
    }
    var page = psListPageRows('psUploadDocList', rows);
    el.innerHTML = page.rows.map(function(doc) {
      var id = memoryId(doc);
      var metaText = doc.notes || doc.filename || '已存入共享记忆';
      if (doc.created_at) metaText += ' · ' + doc.created_at;
      return '<article class="ps-memory-item">' +
        '<div><strong>' + esc(memoryTitle(doc)) + '</strong><small>' + esc(metaText) + '</small></div>' +
        '<div class="ps-actions">' +
          '<button type="button" class="btn btn-ghost btn-sm" data-preview-upload-memory="' + escAttr(id) + '">预览</button>' +
          '<button type="button" class="btn btn-ghost btn-sm" data-delete-upload-memory="' + escAttr(id) + '">删除</button>' +
        '</div>' +
      '</article>';
    }).join('') + page.pager;
    el.querySelectorAll('[data-preview-upload-memory]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        switchTab('memory');
        previewMemory(btn.getAttribute('data-preview-upload-memory') || '');
      });
    });
    el.querySelectorAll('[data-delete-upload-memory]').forEach(function(btn) {
      btn.addEventListener('click', function() { deleteMemory(btn.getAttribute('data-delete-upload-memory') || ''); });
    });
  }

  function memorySourceDocRows() {
    var rows = (state.memories || []).slice();
    var uploaded = rows.filter(isUploadedMemoryDoc);
    return uploaded.length ? uploaded : rows;
  }

  function ensureMemorySourceSelections() {
    if (state.memoryUseProfile !== false) state.memoryUseProfile = true;
    state.memorySourceKeywords = syncSelectionMap(state.memorySourceKeywords || {}, (state.keywords || []).map(function(row) { return row.id; }));
    state.memorySourceCompetitors = syncSelectionMap(state.memorySourceCompetitors || {}, (state.competitors || []).map(function(row) { return row.id; }));
    state.memorySourceDocs = syncSelectionMap(state.memorySourceDocs || {}, memorySourceDocRows().map(memoryId));
    state.memorySourceFiles = syncSelectionMap(state.memorySourceFiles || {}, selectedUploadFiles().map(uploadFileKey));
    state.memorySourceRecordings = syncSelectionMap(
      state.memorySourceRecordings || {},
      (state.recorderRecords || []).map(function(row) { return row.id; }),
      false
    );
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

  function selectedRecorderRecords() {
    ensureMemorySourceSelections();
    return (state.recorderRecords || []).filter(function(row) {
      return state.memorySourceRecordings[String(row.id || '')];
    });
  }

  function loadRecorderSources(force) {
    if (state.recorderRecordsLoading && state.recorderRecordsPromise) return state.recorderRecordsPromise;
    if (state.recorderRecordsLoaded && !force) {
      renderMemorySourceSelectors();
      return Promise.resolve(state.recorderRecords);
    }
    state.recorderRecordsLoading = true;
    renderMemorySourceSelectors();
    var request = cloudJson('/api/h5/recorder/files?page=1&page_size=50', { json: false })
      .then(function(data) {
        state.recorderRecords = (Array.isArray(data.items) ? data.items : []).filter(function(row) {
          return row && row.status === 'completed';
        });
        state.recorderRecordsLoaded = true;
        return state.recorderRecords;
      })
      .finally(function() {
        state.recorderRecordsLoading = false;
        state.recorderRecordsPromise = null;
        renderMemorySourceSelectors();
      });
    state.recorderRecordsPromise = request;
    return request;
  }

  function renderSourceOptions(elId, rows, selected, kind, titleFn, subtitleFn) {
    var el = $(elId);
    if (!el) return;
    var entries = (Array.isArray(rows) ? rows : []).map(function(row) {
      return { row: row, sourceKind: kind };
    });
    if (elId === 'psMemoryUploadSourceList') {
      selectedUploadFiles().forEach(function(file) {
        entries.push({ row: file, sourceKind: 'source_file' });
      });
    }
    renderPsMultiSelect(elId, entries, {
      label: '选择资料',
      selected: selected,
      empty: '暂无',
      attributeName: 'data-ps-memory-source',
      kind: kind,
      kindFn: function(entry) { return entry.sourceKind; },
      id: function(entry) {
        return entry.sourceKind === 'source_file' ? uploadFileKey(entry.row) : (entry.sourceKind === 'source_doc' ? memoryId(entry.row) : String(entry.row.id || ''));
      },
      title: function(entry) {
        return entry.sourceKind === 'source_file' ? (entry.row.name || '未命名文件') : titleFn(entry.row);
      },
      subtitle: function(entry) {
        if (entry.sourceKind === 'source_file') return entry.row.size ? (Math.ceil(entry.row.size / 1024) + 'KB') : '当前选择';
        return subtitleFn ? String(subtitleFn(entry.row) || '') : '';
      }
    });
    return;
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
    var currentFiles = [];
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
    var recorderBox = $('psMemoryRecorderSourceList');
    if (state.recorderRecordsLoading && recorderBox) {
      recorderBox.innerHTML = '<div class="ps-empty">正在读取转写记录...</div>';
    } else {
      renderSourceOptions('psMemoryRecorderSourceList', state.recorderRecords || [], state.memorySourceRecordings, 'recording',
        function(row) { return row.display_name || row.file_name || ('转写 #' + row.id); },
        function(row) {
          var created = String(row.recorded_at || row.created_at || '').replace('T', ' ').slice(0, 16);
          return created + (row.source_label ? ' · ' + row.source_label : '');
        });
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
    var review = $('psMemoryReviewText');
    if (targetSelect) {
      targetSelect.disabled = mode !== 'overwrite';
      if (mode !== 'overwrite') targetSelect.value = '';
    }
    if (titleInput) {
      titleInput.disabled = mode === 'overwrite';
      if (mode === 'overwrite') titleInput.value = '';
    }
    if (review) {
      var editing = mode === 'overwrite';
      review.style.display = editing ? '' : 'none';
      review.setAttribute('aria-hidden', editing ? 'false' : 'true');
    }
    if (mode === 'overwrite' && targetSelect && !targetSelect.value) {
      setMsg('覆盖已有文档需要先选择一个文档。', true);
    }
  }

  function renderAllLists() {
    ensurePsListPagingHandlers();
    renderTemplateLists();
    renderCurrentTemplate();
    renderSavedTemplates();
    renderPersonalDigitalHumanTemplateSummary();
    renderPersonalDigitalHumanResources();
    renderKeywords();
    renderCompetitors();
    renderMemories();
    renderMemorySourceSelectors();
    renderProfileWizard();
  }

  function applyDefaultItem(item) {
    state.defaultItem = item || {};
    setPersonalTemplateLanguage(templateLanguageFromParts(state.defaultItem.requirements, state.defaultItem.meta, state.personalTemplateLanguage));
    state.personalSelectedDigitalHumanTemplate = normalizePersonalDigitalHumanTemplate((state.defaultItem.meta || {}).digital_human_template);
    state.personalDigitalHumanResources = clonePersonalDigitalHumanResources((state.defaultItem.meta || {}).digital_human_resources);
    state.personalDigitalHumanTemplateExplicitlyCleared = false;
    state.selectedKeywords = {};
    state.selectedCompetitors = {};
    state.selectedMemories = {};
    (item.keyword_ids || []).forEach(function(id) { if (id) state.selectedKeywords[String(id)] = true; });
    (item.competitor_ids || []).forEach(function(id) { if (id) state.selectedCompetitors[String(id)] = true; });
    (item.memory_doc_ids || []).forEach(function(id) { if (id) state.selectedMemories[String(id)] = true; });
    fillProfileFields(state.defaultItem);
    renderTemplateLists();
    renderCurrentTemplate();
    renderPersonalDigitalHumanTemplateSummary();
    renderPersonalDigitalHumanResources();
  }

  function loadKeywords() {
    return cloudJson('/api/ip-content/keywords').then(function(data) {
      state.keywords = Array.isArray(data.items) ? data.items : [];
      pruneSelectedIntMap(state.selectedKeywords, state.keywords);
      renderTemplateLists();
      renderMemorySourceSelectors();
      renderKeywords();
    });
  }

  function loadCompetitors() {
    return cloudJson('/api/ip-content/competitors').then(function(data) {
      state.competitors = Array.isArray(data.items) ? data.items : [];
      pruneSelectedIntMap(state.selectedCompetitors, state.competitors);
      renderTemplateLists();
      renderMemorySourceSelectors();
      renderCompetitors();
    });
  }

  function loadMemories() {
    return syncOpenClawMemoryFromCloud().then(function() {
      return cloudJson('/api/personal-settings/memory-documents/list', { json: false }).catch(function(primaryErr) {
        return cloudJson('/api/openclaw/memory/list', { json: false }).catch(function() {
          throw primaryErr;
        });
      });
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
      state.templateLoadError = '';
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
      loadTemplates().catch(function(err) {
        state.templateLoadError = err && err.message ? err.message : '模板加载失败';
      }),
      loadPersonalDigitalHumanResources().catch(function() { state.personalDigitalHumanAvatarOptions = []; state.personalDigitalHumanVoiceOptions = []; }),
      cloudJson('/api/ip-content/personal-default').then(function(data) { state.defaultItem = data.item || {}; })
    ]).then(function() {
      applyDefaultItem(state.defaultItem || {});
      pruneSelectedIntMap(state.selectedKeywords, state.keywords);
      pruneSelectedIntMap(state.selectedCompetitors, state.competitors);
      renderAllLists();
      setMsg(state.templateLoadError ? ('IP 人设模板加载失败：' + state.templateLoadError) : '', !!state.templateLoadError);
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
      var language = currentPersonalTemplateLanguage();
      var digitalHumanTemplate = state.personalDigitalHumanTemplateExplicitlyCleared
        ? null
        : clonePersonalDigitalHumanTemplate(state.personalSelectedDigitalHumanTemplate);
      var body = {
        name: name,
        keyword_ids: cleanExistingIntIds(cleanIntIds(state.selectedKeywords), state.keywords),
        competitor_ids: cleanExistingIntIds(cleanIntIds(state.selectedCompetitors), state.competitors),
        memory_doc_ids: cleanStringIds(state.selectedMemories),
        memory_docs: memoryDocs,
        requirements: templateRequirementsWithLanguage({}, language),
        meta: (function() {
          var currentMeta = state.defaultItem && state.defaultItem.meta && typeof state.defaultItem.meta === 'object' ? state.defaultItem.meta : {};
          var meta = { source: 'personal_settings_template', language: language, target_language: ipTemplateLanguageLabel(language), digital_human_template: digitalHumanTemplate, digital_human_resources: clonePersonalDigitalHumanResources(state.personalDigitalHumanResources) };
          if (currentMeta.current_template_id) meta.current_template_id = currentMeta.current_template_id;
          return meta;
        })()
      };
      return cloudJson(state.editingTemplateId ? '/api/ip-content/schedule-templates/' + encodeURIComponent(state.editingTemplateId) : '/api/ip-content/schedule-templates', {
        method: state.editingTemplateId ? 'PATCH' : 'POST',
        body: body
      });
    }).then(function(data) {
      if (data.item && data.item.id) state.editingTemplateId = String(data.item.id);
      setMsg('模板已保存。');
      // Applying an agent template creates user-owned resource rows. Refresh
      // those lists before the next edit so the saved IDs can be rendered.
      return Promise.all([loadKeywords(), loadCompetitors(), loadMemories(), loadTemplates()]);
    }).catch(function(err) {
      setMsg(err.message || '保存失败', true);
    }).finally(function() {
      setBusy(btn, false);
    });
  }

  function saveCurrentDefault(options) {
    options = options || {};
    var existing = state.defaultItem || {};
    var source = options.source || 'personal_settings';
    var language = normalizeIpTemplateLanguage(options.language || currentPersonalTemplateLanguage() || templateLanguageFromParts(existing.requirements, existing.meta, ''));
    var baseRequirements = stripPersonalProfileRequirements((existing.requirements && typeof existing.requirements === 'object') ? existing.requirements : {});
    var incomingRequirements = Object.assign({}, baseRequirements, stripPersonalProfileRequirements(options.requirements || {}));
    if (options.includeProfile) incomingRequirements = Object.assign({}, incomingRequirements, profileRequirements());
    incomingRequirements = templateRequirementsWithLanguage(incomingRequirements, language);
    var keywordSource = options.replaceSelection ? cleanIntIds(state.selectedKeywords) : [].concat(Array.isArray(existing.keyword_ids) ? existing.keyword_ids : [], cleanIntIds(state.selectedKeywords));
    var competitorSource = options.replaceSelection ? cleanIntIds(state.selectedCompetitors) : [].concat(Array.isArray(existing.competitor_ids) ? existing.competitor_ids : [], cleanIntIds(state.selectedCompetitors));
    var memorySource = options.replaceSelection ? cleanStringIds(state.selectedMemories) : [].concat(Array.isArray(existing.memory_doc_ids) ? existing.memory_doc_ids : [], cleanStringIds(state.selectedMemories));
    var keywordIds = cleanExistingIntIds(keywordSource, state.keywords);
    var competitorIds = cleanExistingIntIds(competitorSource, state.competitors);
    var memoryIds = uniqueIds(memorySource);
    var digitalHumanTemplate = options.digital_human_template !== undefined
      ? clonePersonalDigitalHumanTemplate(options.digital_human_template)
      : (state.personalDigitalHumanTemplateExplicitlyCleared
        ? null
        : clonePersonalDigitalHumanTemplate(state.personalSelectedDigitalHumanTemplate || (existing.meta || {}).digital_human_template));
    var digitalHumanResources = options.digital_human_resources !== undefined
      ? clonePersonalDigitalHumanResources(options.digital_human_resources)
      : clonePersonalDigitalHumanResources(state.personalDigitalHumanResources || (existing.meta || {}).digital_human_resources);
    return Promise.all(selectedMemoryPayload(memoryIds).map(fetchMemoryContent)).then(function(memoryDocs) {
      return cloudJson('/api/ip-content/personal-default', {
        method: 'PUT',
        body: {
          name: options.name || existing.name || '个人默认模板',
          keyword_ids: keywordIds,
          competitor_ids: competitorIds,
          memory_doc_ids: memoryIds,
          memory_docs: memoryDocs,
          requirements: incomingRequirements,
          meta: Object.assign({}, (existing.meta && typeof existing.meta === 'object') ? existing.meta : {}, options.meta || {}, { source: source, language: language, target_language: ipTemplateLanguageLabel(language), digital_human_template: digitalHumanTemplate, digital_human_resources: digitalHumanResources })
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
    saveCurrentDefault({ source: 'online_personal_profile', includeProfile: true })
      .then(function() { setMsg('资料调查已保存。'); })
      .catch(function(err) { setMsg(err.message || '保存失败', true); })
      .finally(function() { setBusy(btn, false); });
  }

  function applyTemplate(row, editing) {
    row = row || {};
    state.editingTemplateId = editing && row.id ? String(row.id) : '';
    state.personalSelectedDigitalHumanTemplate = normalizePersonalDigitalHumanTemplate((row.meta || {}).digital_human_template);
    state.personalDigitalHumanResources = clonePersonalDigitalHumanResources((row.meta || {}).digital_human_resources);
    state.personalDigitalHumanTemplateExplicitlyCleared = false;
    state.selectedKeywords = {};
    state.selectedCompetitors = {};
    state.selectedMemories = {};
    (row.keyword_ids || []).forEach(function(id) { if (id) state.selectedKeywords[String(id)] = true; });
    (row.competitor_ids || []).forEach(function(id) { if (id) state.selectedCompetitors[String(id)] = true; });
    (row.memory_doc_ids || []).forEach(function(id) { if (id) state.selectedMemories[String(id)] = true; });
    if ($('psTemplateName')) $('psTemplateName').value = row.name || '';
    setPersonalTemplateLanguage(templateLanguageFromParts(row.requirements, row.meta, row.language || row.target_language || state.personalTemplateLanguage));
    renderPersonalDigitalHumanTemplateSummary();
    renderPersonalDigitalHumanResources();
    renderAllLists();
    switchTab('template');
  }

  function resetTemplateForm() {
    state.editingTemplateId = '';
    state.selectedKeywords = {};
    state.selectedCompetitors = {};
    state.selectedMemories = {};
    state.personalSelectedDigitalHumanTemplate = clonePersonalDigitalHumanTemplate((state.defaultItem || {}).meta && state.defaultItem.meta.digital_human_template);
    state.personalDigitalHumanResources = clonePersonalDigitalHumanResources((state.defaultItem || {}).meta && state.defaultItem.meta.digital_human_resources);
    state.personalDigitalHumanTemplateDraft = null;
    state.personalDigitalHumanTemplateExplicitlyCleared = false;
    if ($('psTemplateName')) $('psTemplateName').value = '';
    setPersonalTemplateLanguage(templateLanguageFromParts((state.defaultItem || {}).requirements, (state.defaultItem || {}).meta, state.personalTemplateLanguage));
    renderTemplateLists();
    renderSavedTemplates();
    renderPersonalDigitalHumanTemplateSummary();
    renderPersonalDigitalHumanResources();
  }

  function useTemplate(id, btn) {
    var row = (state.templates || []).find(function(item) { return String(item.id || '') === String(id || ''); });
    if (row && row.source === 'agent') {
      state.editingTemplateId = '';
      state.selectedKeywords = {};
      state.selectedCompetitors = {};
      state.selectedMemories = {};
      state.personalSelectedDigitalHumanTemplate = normalizePersonalDigitalHumanTemplate((row.meta || {}).digital_human_template);
      state.personalDigitalHumanResources = clonePersonalDigitalHumanResources((row.meta || {}).digital_human_resources);
      state.personalDigitalHumanTemplateExplicitlyCleared = false;
      (row.keyword_ids || []).forEach(function(value) { if (value) state.selectedKeywords[String(value)] = true; });
      (row.competitor_ids || []).forEach(function(value) { if (value) state.selectedCompetitors[String(value)] = true; });
      (row.memory_doc_ids || []).forEach(function(value) { if (value) state.selectedMemories[String(value)] = true; });
      (Array.isArray(row.keywords) ? row.keywords : []).forEach(function(resource) {
        var resourceId = String(resource && resource.id || '');
        if (resourceId && !state.keywords.some(function(item) { return String(item && item.id || '') === resourceId; })) state.keywords.push(resource);
      });
      (Array.isArray(row.competitors) ? row.competitors : []).forEach(function(resource) {
        var resourceId = String(resource && resource.id || '');
        if (resourceId && !state.competitors.some(function(item) { return String(item && item.id || '') === resourceId; })) state.competitors.push(resource);
      });
      (Array.isArray(row.memory_docs) ? row.memory_docs : []).forEach(function(resource) {
        var resourceId = memoryId(resource);
        if (resourceId && !state.memories.some(function(item) { return memoryId(item) === resourceId; })) state.memories.push(resource);
      });
      state.defaultItem = Object.assign({}, state.defaultItem || {}, { meta: Object.assign({}, (state.defaultItem && state.defaultItem.meta) || {}, { current_template_id: row.id }) });
      if ($('psTemplateName')) $('psTemplateName').value = templateName(row);
      setPersonalTemplateLanguage(templateLanguageFromParts(row.requirements, row.meta, row.language || row.target_language || state.personalTemplateLanguage));
      renderAllLists();
      setMsg('已填充代理商模板内容，请修改名称后保存为个人模板。');
      return;
    }
    if (!row) {
      setMsg('模板不存在。', true);
      return;
    }
    setBusy(btn, true, '保存中...');
    state.selectedKeywords = {};
    state.selectedCompetitors = {};
    state.selectedMemories = {};
      state.personalSelectedDigitalHumanTemplate = normalizePersonalDigitalHumanTemplate((row.meta || {}).digital_human_template);
      state.personalDigitalHumanResources = clonePersonalDigitalHumanResources((row.meta || {}).digital_human_resources);
    state.personalDigitalHumanTemplateExplicitlyCleared = false;
    (row.keyword_ids || []).forEach(function(value) { if (value) state.selectedKeywords[String(value)] = true; });
    (row.competitor_ids || []).forEach(function(value) { if (value) state.selectedCompetitors[String(value)] = true; });
    (row.memory_doc_ids || []).forEach(function(value) { if (value) state.selectedMemories[String(value)] = true; });
    var language = templateLanguageFromParts(row.requirements, row.meta, row.language || row.target_language || state.personalTemplateLanguage);
    var requirements = templateRequirementsWithLanguage(Object.assign(
      {},
      stripPersonalProfileRequirements((state.defaultItem && state.defaultItem.requirements && typeof state.defaultItem.requirements === 'object') ? state.defaultItem.requirements : {}),
      stripPersonalProfileRequirements((row.requirements && typeof row.requirements === 'object') ? row.requirements : {})
    ), language);
    saveCurrentDefault({
      name: templateName(row),
      requirements: requirements,
      meta: Object.assign({}, row.meta || {}, { current_template_id: row.id, language: language, target_language: ipTemplateLanguageLabel(language), digital_human_template: state.personalSelectedDigitalHumanTemplate, digital_human_resources: clonePersonalDigitalHumanResources(state.personalDigitalHumanResources) }),
      source: 'personal_settings_current_template',
      language: language,
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

  function copyTemplate(id, btn) {
    var row = (state.templates || []).find(function(item) { return String(item.id || '') === String(id || ''); });
    if (!row) {
      setMsg('模板不存在。', true);
      return;
    }
    setBusy(btn, true, '复制中的...');
    cloudJson('/api/ip-content/schedule-templates/' + encodeURIComponent(id) + '/copy', {
      method: 'POST',
      body: {}
    }).then(function(data) {
      return Promise.all([loadKeywords(), loadCompetitors(), loadMemories(), loadTemplates()]).then(function() {
        var copied = data.item || (state.templates || []).find(function(item) {
          return String(item.meta && item.meta.copied_from_template_id || '') === String(id);
        });
        if (copied) applyTemplate(copied, true);
        setMsg('已复制为个人模板，可继续编辑。');
      });
    }).catch(function(err) {
      setMsg(err.message || '复制失败', true);
    }).finally(function() {
      setBusy(btn, false);
    });
  }

  function deleteTemplate(id, btn) {
    var row = (state.templates || []).find(function(item) { return String(item.id || '') === String(id || ''); });
    if (!row || row.source === 'agent') {
      setMsg('只能删除自己创建的模板。', true);
      return;
    }
    if (!window.confirm('删除模板“' + templateName(row) + '”？')) return;
    setBusy(btn, true, '删除中...');
    cloudJson('/api/ip-content/schedule-templates/' + encodeURIComponent(id), { method: 'DELETE', json: false })
      .then(function() {
        if (String(state.editingTemplateId || '') === String(id || '')) resetTemplateForm();
        return loadAll();
      })
      .then(function() { setMsg('模板已删除。'); })
      .catch(function(err) { setMsg(err.message || '删除失败', true); })
      .finally(function() { setBusy(btn, false); });
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
    var recorderRows = selectedRecorderRecords();
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
    requireOnlineMemoryParser(files).then(function() {
      return competitorSourceText(competitorRows.map(function(row) { return row.id; }));
    }).then(function(competitorText) {
      if (!files.length && !contextText && !competitorText && !recorderRows.length) {
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
      fd.append('source_doc_ids', sourceDocs.map(memoryId).filter(Boolean).join(','));
      fd.append('recorder_record_ids', recorderRows.map(function(row) { return row.id; }).filter(Boolean).join(','));
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
      if (data.processing === 'online' && data.message_id) {
        setMsg('已下发 Online，正在本机解析资料...');
        return waitForOnlineMemoryGeneration(data.message_id);
      }
      return data;
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
    var savedKeys = {};
    var queued = [];
    var savedImmediately = 0;
    var failed = [];
    requireOnlineMemoryParser(files).then(function() {
      return files.reduce(function(chain, file, index) {
        return chain.then(function() {
          setBusy(btn, true, '保存 ' + (index + 1) + '/' + files.length + '...');
          setMsg('正在上传并解析 ' + (index + 1) + '/' + files.length + '：' + (file.name || '未命名文件'));
          var fd = new FormData();
          fd.append('files', file, file.name || 'upload');
          fd.append('title', file.name || '上传资料');
          fd.append('notes', 'IP人设定位上传资料');
          fd.append('raw_text', '');
          fd.append('urls', '');
          fd.append('mode', 'new');
          fd.append('target_doc_id', '');
          return saveUploadedMemory(null, fd, { refresh: false, status: false }).then(function(result) {
            savedKeys[uploadFileKey(file)] = true;
            if (result && result.processing === 'online' && result.message_id) {
              queued.push({ messageId: result.message_id, filename: file.name || '资料' });
            } else {
              savedImmediately += 1;
            }
          }).catch(function(err) {
            failed.push((file.name || '未命名文件') + '：' + ((err && err.message) || '读取失败'));
          });
        });
      }, Promise.resolve());
    }).then(function() {
      state.uploadFiles = files.filter(function(file) { return !savedKeys[uploadFileKey(file)]; });
      renderSelectedFiles();
      renderMemorySourceSelectors();
      var refresh = savedImmediately ? loadMemories().then(saveConfigSilently) : Promise.resolve();
      return refresh.then(function() {
        queued.forEach(function(item) { monitorOnlineMemoryParse(item.messageId, item.filename); });
        if (failed.length) {
          setMsg('已处理 ' + Object.keys(savedKeys).length + ' 个文件；未读取 ' + failed.join('；'), true);
        } else {
          setMsg(queued.length
            ? '已提交 ' + queued.length + ' 个文件到 Online 解析，完成后自动存入记忆。'
            : '已存入 ' + Object.keys(savedKeys).length + ' 个文件。');
        }
      });
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
    var mode = (($('psSaveMode') || {}).value || 'new');
    var content = mode === 'overwrite'
      ? (($('psMemoryReviewText') || {}).value || '').trim()
      : (generatedContent || (!hasGeneratedPreview ? (($('psMemoryReviewText') || {}).value || '').trim() : ''));
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

  function saveUploadedMemory(btn, formData, options) {
    options = options || {};
    setBusy(btn, true, '保存中...');
    if (options.status !== false) setMsg('正在保存上传资料到记忆...');
    return fetch(cloudBase() + '/api/personal-settings/memory-documents/save-upload', {
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
      if ($('psMemoryReviewText') && data.content_text) $('psMemoryReviewText').value = data.content_text;
      if (options.refresh === false) return data;
      return loadMemories().then(saveConfigSilently).then(function() { return data; });
    }).then(function(data) {
        if (options.status !== false) setMsg('已存入记忆，并写入模板选择。');
        renderTemplateLists();
        return data;
      })
      .catch(function(err) {
        if (options.status !== false) setMsg(err.message || '保存记忆失败', true);
        throw err;
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
    if ($('psSaveProfileBtn')) $('psSaveProfileBtn').addEventListener('click', saveProfile);
    if ($('psProfilePhotoPickerClose')) $('psProfilePhotoPickerClose').addEventListener('click', closeProfilePhotoPicker);
    if ($('psProfilePhotoPickerSearch')) $('psProfilePhotoPickerSearch').addEventListener('input', function(ev) {
      state.profilePhotoPickerQuery = String(ev.target.value || '');
      renderProfilePhotoPicker();
    });
    if ($('psProfilePhotoPicker')) $('psProfilePhotoPicker').addEventListener('click', function(ev) {
      if (ev.target === $('psProfilePhotoPicker')) {
        closeProfilePhotoPicker();
        return;
      }
      var item = ev.target && ev.target.closest ? ev.target.closest('[data-ps-photo-asset]') : null;
      if (item) pickProfilePhotoAsset(item.getAttribute('data-ps-photo-asset') || '');
    });
    document.addEventListener('keydown', function(ev) {
      if (ev.key === 'Escape' && state.profilePhotoPickerOpen) closeProfilePhotoPicker();
      if (ev.key === 'Escape' && $('psDigitalHumanResourceModal') && !$('psDigitalHumanResourceModal').hidden) {
        closePersonalDigitalHumanResourcePicker();
      }
      if (ev.key === 'Escape' && $('psDigitalHumanTemplateModal') && !$('psDigitalHumanTemplateModal').hidden) {
        closePersonalDigitalHumanTemplatePicker();
      }
    });
    if ($('psSaveTemplateBtn')) $('psSaveTemplateBtn').addEventListener('click', saveTemplate);
    if ($('psNewTemplateBtn')) $('psNewTemplateBtn').addEventListener('click', resetTemplateForm);
    if ($('psTemplateLanguage')) $('psTemplateLanguage').addEventListener('change', function(ev) {
      setPersonalTemplateLanguage(ev.target.value || 'zh-CN');
      renderCurrentTemplate();
      renderSavedTemplates();
    });
    if ($('psDigitalHumanTemplateChooseBtn')) $('psDigitalHumanTemplateChooseBtn').addEventListener('click', function() {
      openPersonalDigitalHumanTemplatePicker();
    });
    if ($('psDigitalHumanAvatarChooseBtn')) $('psDigitalHumanAvatarChooseBtn').addEventListener('click', function() {
      openPersonalDigitalHumanResourcePicker('avatar');
    });
    if ($('psDigitalHumanVoiceChooseBtn')) $('psDigitalHumanVoiceChooseBtn').addEventListener('click', function() {
      openPersonalDigitalHumanResourcePicker('voice');
    });
    ['psDigitalHumanAvatarList', 'psDigitalHumanVoiceList'].forEach(function(id) {
      if (!$(id)) return;
      $(id).addEventListener('click', function(ev) {
        var button = ev.target && ev.target.closest ? ev.target.closest('[data-open-ps-resource]') : null;
        if (button) openPersonalDigitalHumanResourcePicker(button.getAttribute('data-open-ps-resource') || 'avatar');
      });
    });
    if ($('psDigitalHumanResourceClose')) $('psDigitalHumanResourceClose').addEventListener('click', closePersonalDigitalHumanResourcePicker);
    if ($('psDigitalHumanResourceCancel')) $('psDigitalHumanResourceCancel').addEventListener('click', closePersonalDigitalHumanResourcePicker);
    if ($('psDigitalHumanResourceConfirm')) $('psDigitalHumanResourceConfirm').addEventListener('click', confirmPersonalDigitalHumanResources);
    if ($('psDigitalHumanResourceModal')) $('psDigitalHumanResourceModal').addEventListener('click', function(ev) {
      if (ev.target === $('psDigitalHumanResourceModal')) closePersonalDigitalHumanResourcePicker();
    });
    if ($('psDigitalHumanResourceTabs')) $('psDigitalHumanResourceTabs').addEventListener('click', function(ev) {
      var button = ev.target && ev.target.closest ? ev.target.closest('[data-ps-resource-kind]') : null;
      if (!button) return;
      state.personalDigitalHumanResourcePickerKind = button.getAttribute('data-ps-resource-kind') === 'voice' ? 'voice' : 'avatar';
      state.personalDigitalHumanResourceQuery = '';
      state.personalDigitalHumanResourcePage = 1;
      if ($('psDigitalHumanResourceSearch')) $('psDigitalHumanResourceSearch').value = '';
      renderPersonalDigitalHumanResourcePicker();
    });
    if ($('psDigitalHumanResourceSearch')) $('psDigitalHumanResourceSearch').addEventListener('input', function(ev) {
      state.personalDigitalHumanResourceQuery = String(ev.target.value || '');
      state.personalDigitalHumanResourcePage = 1;
      renderPersonalDigitalHumanResourcePicker();
    });
    if ($('psDigitalHumanResourceList')) $('psDigitalHumanResourceList').addEventListener('change', function(ev) {
      var input = ev.target && ev.target.closest ? ev.target.closest('[data-ps-resource-key]') : null;
      if (!input) return;
      var kind = state.personalDigitalHumanResourcePickerKind === 'voice' ? 'voice' : 'avatar';
      var listKey = kind === 'avatar' ? 'avatars' : 'voices';
      var key = input.getAttribute('data-ps-resource-key') || '';
      var draft = normalizePersonalDigitalHumanResources(state.personalDigitalHumanResourceDraft);
      var current = draft[listKey].filter(function(row) { return digitalHumanResourceKey(row, kind) !== key; });
      if (input.checked) {
        var picked = personalDigitalHumanResourceOptions(kind, draft).find(function(row) { return digitalHumanResourceKey(row, kind) === key; });
        if (picked) current.push(Object.assign({}, picked));
      }
      draft[listKey] = current;
      state.personalDigitalHumanResourceDraft = draft;
      renderPersonalDigitalHumanResourcePicker();
    });
    if ($('psDigitalHumanResourceSelectAll')) $('psDigitalHumanResourceSelectAll').addEventListener('click', function() {
      var kind = state.personalDigitalHumanResourcePickerKind === 'voice' ? 'voice' : 'avatar';
      var listKey = kind === 'avatar' ? 'avatars' : 'voices';
      var rows = filteredPersonalDigitalHumanResourceRows();
      var rowKeys = {};
      rows.forEach(function(row) { rowKeys[digitalHumanResourceKey(row, kind)] = true; });
      var draft = normalizePersonalDigitalHumanResources(state.personalDigitalHumanResourceDraft);
      var selected = {};
      draft[listKey].forEach(function(row) { selected[digitalHumanResourceKey(row, kind)] = true; });
      var allSelected = !!rows.length && rows.every(function(row) { return !!selected[digitalHumanResourceKey(row, kind)]; });
      var kept = draft[listKey].filter(function(row) { return !rowKeys[digitalHumanResourceKey(row, kind)]; });
      draft[listKey] = allSelected ? kept : kept.concat(rows.map(function(row) { return Object.assign({}, row); }));
      state.personalDigitalHumanResourceDraft = draft;
      renderPersonalDigitalHumanResourcePicker();
    });
    if ($('psDigitalHumanResourceClear')) $('psDigitalHumanResourceClear').addEventListener('click', function() {
      var kind = state.personalDigitalHumanResourcePickerKind === 'voice' ? 'voice' : 'avatar';
      var draft = normalizePersonalDigitalHumanResources(state.personalDigitalHumanResourceDraft);
      draft[kind === 'avatar' ? 'avatars' : 'voices'] = [];
      state.personalDigitalHumanResourceDraft = draft;
      renderPersonalDigitalHumanResourcePicker();
    });
    if ($('psDigitalHumanResourcePrev')) $('psDigitalHumanResourcePrev').addEventListener('click', function() {
      state.personalDigitalHumanResourcePage = Math.max(1, Number(state.personalDigitalHumanResourcePage || 1) - 1);
      renderPersonalDigitalHumanResourcePicker();
    });
    if ($('psDigitalHumanResourceNext')) $('psDigitalHumanResourceNext').addEventListener('click', function() {
      state.personalDigitalHumanResourcePage = Number(state.personalDigitalHumanResourcePage || 1) + 1;
      renderPersonalDigitalHumanResourcePicker();
    });
    if ($('psDigitalHumanTemplateClearBtn')) $('psDigitalHumanTemplateClearBtn').addEventListener('click', function() {
      state.personalSelectedDigitalHumanTemplate = null;
      state.personalDigitalHumanTemplateExplicitlyCleared = true;
      renderPersonalDigitalHumanTemplateSummary();
    });
    if ($('psDigitalHumanTemplateClose')) $('psDigitalHumanTemplateClose').addEventListener('click', closePersonalDigitalHumanTemplatePicker);
    if ($('psDigitalHumanTemplateModal')) $('psDigitalHumanTemplateModal').addEventListener('click', function(ev) {
      if (ev.target === $('psDigitalHumanTemplateModal')) closePersonalDigitalHumanTemplatePicker();
    });
    if ($('psDigitalHumanTemplateSearch')) $('psDigitalHumanTemplateSearch').addEventListener('input', function(ev) {
      state.personalDigitalHumanTemplateSearch = String(ev.target.value || '');
      state.personalDigitalHumanTemplatePage = 1;
      renderPersonalDigitalHumanTemplatePicker();
    });
    if ($('psDigitalHumanTemplatePrev')) $('psDigitalHumanTemplatePrev').addEventListener('click', function() {
      state.personalDigitalHumanTemplatePage = Math.max(1, Number(state.personalDigitalHumanTemplatePage || 1) - 1);
      renderPersonalDigitalHumanTemplatePicker();
    });
    if ($('psDigitalHumanTemplateNext')) $('psDigitalHumanTemplateNext').addEventListener('click', function() {
      state.personalDigitalHumanTemplatePage = Number(state.personalDigitalHumanTemplatePage || 1) + 1;
      renderPersonalDigitalHumanTemplatePicker();
    });
    if ($('psDigitalHumanTemplateGrid')) $('psDigitalHumanTemplateGrid').addEventListener('click', function(ev) {
      var card = ev.target && ev.target.closest ? ev.target.closest('[data-ps-dh-template]') : null;
      if (!card) return;
      var item = (state.personalDigitalHumanTemplates || []).find(function(row) { return String(row.style_id || '') === String(card.getAttribute('data-ps-dh-template') || ''); });
      if (!item) return;
      state.personalDigitalHumanTemplateDraft = clonePersonalDigitalHumanTemplate(item);
      renderPersonalDigitalHumanTemplatePicker();
    });
    if ($('psDigitalHumanTemplateGrid')) $('psDigitalHumanTemplateGrid').addEventListener('keydown', function(ev) {
      if (ev.key !== 'Enter' && ev.key !== ' ') return;
      var card = ev.target && ev.target.closest ? ev.target.closest('[data-ps-dh-template]') : null;
      if (!card) return;
      ev.preventDefault();
      var item = (state.personalDigitalHumanTemplates || []).find(function(row) { return String(row.style_id || '') === String(card.getAttribute('data-ps-dh-template') || ''); });
      if (!item) return;
      state.personalDigitalHumanTemplateDraft = clonePersonalDigitalHumanTemplate(item);
      renderPersonalDigitalHumanTemplatePicker();
    });
    if ($('psDigitalHumanTemplateCancel')) $('psDigitalHumanTemplateCancel').addEventListener('click', closePersonalDigitalHumanTemplatePicker);
    if ($('psDigitalHumanTemplateConfirm')) $('psDigitalHumanTemplateConfirm').addEventListener('click', confirmPersonalDigitalHumanTemplate);
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
    bindUploadDropzone();
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
            : (kind === 'source_doc'
              ? state.memorySourceDocs
              : (kind === 'recording' ? state.memorySourceRecordings : state.memorySourceFiles)));
        if (input.value) map[String(input.value)] = !!input.checked;
      });
    }
    if ($('psSaveMemoryBtn')) $('psSaveMemoryBtn').addEventListener('click', saveMemory);
    if ($('psSaveRawMemoryBtn')) $('psSaveRawMemoryBtn').addEventListener('click', saveRawMemory);
    if ($('psSaveMode')) $('psSaveMode').addEventListener('change', syncSaveModeState);
    if ($('psTargetMemorySelect')) $('psTargetMemorySelect').addEventListener('change', function() {
      syncSaveModeState();
      var id = $('psTargetMemorySelect').value || '';
      if (id) {
        previewMemory(id);
        cloudJson('/api/personal-settings/memory-documents/' + encodeURIComponent(id) + '/preview', { json: false })
          .then(function(data) {
            if ($('psMemoryReviewText')) $('psMemoryReviewText').value = data.content_text || '';
          })
          .catch(function(err) { setMsg(err.message || '读取失败', true); });
      }
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
  window.closePersonalSettingsOverlays = function() {
    closeProfilePhotoPicker();
    closePersonalDigitalHumanTemplatePicker();
  };
})();
