(function() {
  var FALLBACK_EXAMPLES = [
    {
      id: 1050,
      title: '3D 彩墙人物',
      prompt: 'A stylized 3D animated young woman leaning against a textured abstract wall made of layered cracked paint panels in warm yellow, coral, pink and muted purple gradients. Soft cinematic lighting, warm pastel palette, painterly textures, dreamy atmosphere, Pixar-style 3D illustration, ultra detailed.',
      prompt_zh: '一位风格化 3D 动画年轻女性倚靠在抽象彩色墙面前，墙面由层叠龟裂的颜料板组成，带有暖黄色、珊瑚粉、粉色和柔和紫色渐变。柔和电影光，暖 pastel 色调，绘画质感，梦幻氛围，皮克斯风 3D 插画，细节丰富。',
      cover_image: 'https://raw.githubusercontent.com/songguoxs/gpt4o-image-prompts/master/images/1050.jpeg',
      model: 'gpt-image-2',
      tags: ['3D', '插画', '暖色']
    },
    {
      id: 1049,
      title: '角色设定草图',
      prompt: 'Character sheet sketch of a subject, featuring multiple angles and expressive facial variations, drawn in pencil and ballpoint pen on a clean white background. Soft pastel palette, sharp linework, hand-drawn manga style, clear design-sheet composition.',
      prompt_zh: '角色设定草图，同一个主体的多角度视图和丰富表情变化，铅笔与圆珠笔手绘质感，干净白底，柔和 pastel 色彩，清晰利落线条，手绘漫画风，明确的设定图构图。',
      cover_image: 'https://raw.githubusercontent.com/songguoxs/gpt4o-image-prompts/master/images/1049.jpeg',
      model: 'gpt-image-2',
      tags: ['角色', '线稿', '设定']
    }
  ];

  var state = {
    initialized: false,
    view: 'examples',
    references: [],
    results: [],
    activeResultIndex: 0,
    selectedJobId: '',
    submitting: false,
    examples: [],
    exampleCatalog: [],
    examplesOffset: 0,
    examplesTotal: 0,
    examplesLoading: false,
    examplesLimit: 24,
    exampleCategory: 'all',
    exampleSearch: '',
    currentJobId: '',
    currentJobStatus: '',
    currentJobPrompt: '',
    pollTimer: null,
    recentJobs: []
  };
  var imglabTaskToastTimer = null;
  var imglabTaskNotifiedJobs = {};
  var assetPickerState = {
    loading: false,
    items: [],
    selected: {},
    query: ''
  };
  var customSelectEventsBound = false;

  var JOB_RESTORE_WINDOW_MS = 6 * 60 * 60 * 1000;
  var JOB_HISTORY_WINDOW_MS = 3 * 24 * 60 * 60 * 1000;

  var PURPOSE_LABELS = {
    auto: '普通参考',
    person: '替换人物',
    product: '替换产品',
    style: '参考风格',
    background: '参考背景',
    local_edit: '局部修改'
  };

  var PURPOSE_HINTS = {
    person: '参考图{n}是唯一目标人物身份参考。生成结果中的主要人物必须替换为参考图{n}里的人物，优先保持脸型、五官比例、发型、气质、肤色和服装核心特征；不要沿用案例提示词里的默认人物长相。',
    product: '参考图{n}是目标产品，请将画面中的主体产品替换为参考图{n}的产品，保持外观、包装、颜色、材质、标签布局和品牌识别特征。',
    style: '参考图{n}只作为风格参考，请学习它的色彩、光线、构图和质感，不要复制其中的具体人物或产品。',
    background: '参考图{n}是背景参考，请使用类似场景、空间氛围、光线和环境结构。',
    local_edit: '参考图{n}用于局部修改，请优先保持原图主体一致，只修改提示词明确要求的区域。',
    auto: '参考图{n}是普通参考图，请结合它的主体、风格或构图进行生成。'
  };

  var EXAMPLE_CATEGORIES = {
    all: [],
    portrait: ['人物', '写真', '美女', '女', '男', '人像', '婚纱', '偶像', '网红', '肖像'],
    product: ['产品', '广告', '香水', '商品', '鞋', '包', '美妆', '护肤', '饮料', '海报', '品牌'],
    ecommerce: ['电商', '主图', '白底', '详情', '带货', '上架', 'SKU', '包装', '商品'],
    guofeng: ['古风', '国潮', '汉服', '西游', '唐僧', '白骨精', '宋朝', '中国', '东方', '水墨'],
    poster: ['海报', '字体', '排版', '明信片', '知识图谱', '信息图', '地图', '设计'],
    character: ['角色', '设定', '草图', '四视图', '表情', '漫画', '插画'],
    composite: ['拼接', '合成', '多图', '双生', '错觉', '参考图', '图像']
  };

  function $(id) {
    return document.getElementById(id);
  }

  function escapeHtml(text) {
    return String(text || '').replace(/[&<>"]/g, function(ch) {
      return {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;'
      }[ch];
    });
  }

  function formatFileSize(size) {
    if (!size) return '本地素材';
    if (size >= 1024 * 1024) return (size / (1024 * 1024)).toFixed(1) + ' MB';
    if (size >= 1024) return Math.round(size / 1024) + ' KB';
    return size + ' B';
  }

  function localBase() {
    return (typeof LOCAL_API_BASE !== 'undefined' ? (LOCAL_API_BASE || '') : '').replace(/\/$/, '');
  }

  function cloudBase() {
    return (typeof API_BASE !== 'undefined' ? (API_BASE || '') : '').replace(/\/$/, '');
  }

  function jobsStorageKey() {
    var uid = (window.__currentUserId || window.currentUserId || 'anon');
    return 'lobster_image_studio_jobs_' + String(uid || 'anon');
  }

  function loadRecentJobs() {
    try {
      var raw = window.localStorage ? window.localStorage.getItem(jobsStorageKey()) : '';
      var rows = JSON.parse(raw || '[]');
      var now = Date.now();
      state.recentJobs = (Array.isArray(rows) ? rows : []).filter(function(item) {
        if (!item || !item.jobId) return false;
        if (item.updatedAt && now - Number(item.updatedAt) > JOB_HISTORY_WINDOW_MS) return false;
        if (item.status === 'completed' && !item.image && !item.resultCount) return false;
        return true;
      }).slice(0, 12);
    } catch (e) {
      state.recentJobs = [];
    }
  }

  function saveRecentJobs() {
    try {
      if (window.localStorage) window.localStorage.setItem(jobsStorageKey(), JSON.stringify(state.recentJobs.slice(0, 12)));
    } catch (e) {}
  }

  function rememberJob(job) {
    if (!job || !job.jobId) return;
    var next = state.recentJobs.filter(function(item) { return item && item.jobId !== job.jobId; });
    next.unshift(Object.assign({}, job, { updatedAt: Date.now() }));
    state.recentJobs = next.slice(0, 12);
    saveRecentJobs();
  }

  function updateRememberedJob(jobId, patch) {
    if (!jobId) return;
    state.recentJobs = state.recentJobs.map(function(item) {
      if (!item || item.jobId !== jobId) return item;
      return Object.assign({}, item, patch || {}, { updatedAt: Date.now() });
    });
    saveRecentJobs();
  }

  function mergeRecentJobs(rows) {
    var byId = {};
    (state.recentJobs || []).concat(rows || []).forEach(function(item) {
      if (!item || !item.jobId) return;
      var old = byId[item.jobId] || {};
      byId[item.jobId] = Object.assign({}, old, item, {
        updatedAt: item.updatedAt || old.updatedAt || Date.now()
      });
    });
    state.recentJobs = Object.keys(byId).map(function(id) { return byId[id]; })
      .sort(function(a, b) { return Number(b.updatedAt || 0) - Number(a.updatedAt || 0); })
      .slice(0, 12);
    saveRecentJobs();
  }

  function stopPolling() {
    if (state.pollTimer) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function schedulePoll(delayMs) {
    stopPolling();
    state.pollTimer = setTimeout(function() {
      refreshJobStatus(false);
    }, delayMs || 3000);
  }

  function releaseItems(items) {
    (items || []).forEach(function(item) {
      if (item && item.objectUrl) {
        try {
          URL.revokeObjectURL(item.objectUrl);
        } catch (err) {}
      }
    });
  }

  function showMessage(text, isError) {
    var el = $('imglabStudioMsg');
    if (!el) return;
    if (!text) {
      el.style.display = 'none';
      el.textContent = '';
      el.className = 'msg';
      return;
    }
    el.style.display = 'block';
    el.textContent = text;
    el.className = 'msg ' + (isError ? 'err' : 'ok');
  }

  function openImageResultView() {
    if (typeof window._openImageComposerStudioView === 'function') {
      window._openImageComposerStudioView();
    } else if (typeof window._openHiddenWorkspaceView === 'function') {
      window._openHiddenWorkspaceView('image-composer-studio');
    } else if (typeof _switchToHiddenView === 'function') {
      _switchToHiddenView('image-composer-studio');
    } else {
      try { location.hash = 'image-composer-studio'; } catch (err) {}
    }
    setRightView('result');
    renderWorkspace();
  }

  function ensureTaskToast() {
    var toast = $('imglabTaskToast');
    if (toast) return toast;
    toast = document.createElement('div');
    toast.id = 'imglabTaskToast';
    toast.className = 'seedance-task-toast';
    toast.setAttribute('role', 'status');
    toast.setAttribute('aria-live', 'polite');
    toast.innerHTML = [
      '<div class="seedance-task-toast-main">',
      '<strong class="seedance-task-toast-title"></strong>',
      '<p class="seedance-task-toast-body"></p>',
      '</div>',
      '<button type="button" class="seedance-task-toast-action">查看</button>'
    ].join('');
    var action = toast.querySelector('.seedance-task-toast-action');
    if (action) {
      action.addEventListener('click', function() {
        openImageResultView();
        hideTaskToast();
      });
    }
    document.body.appendChild(toast);
    return toast;
  }

  function hideTaskToast() {
    var toast = $('imglabTaskToast');
    if (!toast) return;
    toast.classList.remove('is-visible');
  }

  function showTaskToast(kind, title, body, actionText) {
    var toast = ensureTaskToast();
    toast.classList.toggle('is-error', kind === 'error');
    toast.classList.toggle('is-success', kind !== 'error');
    var titleEl = toast.querySelector('.seedance-task-toast-title');
    var bodyEl = toast.querySelector('.seedance-task-toast-body');
    var actionEl = toast.querySelector('.seedance-task-toast-action');
    if (titleEl) titleEl.textContent = title || '';
    if (bodyEl) bodyEl.textContent = body || '';
    if (actionEl) actionEl.textContent = actionText || '查看';
    window.clearTimeout(imglabTaskToastTimer);
    window.requestAnimationFrame(function() {
      toast.classList.add('is-visible');
    });
    imglabTaskToastTimer = window.setTimeout(hideTaskToast, 9000);
  }

  function notifyTaskOnce(status, message) {
    if (!state.currentJobId || !status) return;
    var key = state.currentJobId + ':' + status;
    if (imglabTaskNotifiedJobs[key]) return;
    imglabTaskNotifiedJobs[key] = true;
    if (status === 'completed') {
      showTaskToast(
        'success',
        '图片已生成',
        '任务已完成，结果已同步到图片创作页面，可以回来查看或打开原图。',
        '查看结果'
      );
    } else if (status === 'failed') {
      showTaskToast(
        'error',
        '图片生成失败',
        message || '任务执行失败，请查看结果页原因后重新提交。',
        '查看原因'
      );
    }
  }

  function readFiles(fileList) {
    var purpose = (($('imglabReferencePurposeSelect') || {}).value || 'auto').trim() || 'auto';
    return Array.prototype.slice.call(fileList || []).map(function(file) {
      return {
        file: file,
        name: file.name,
        size: file.size,
        purpose: purpose,
        objectUrl: URL.createObjectURL(file)
      };
    });
  }

  function cleanRemoteUrl(url) {
    return String(url || '').trim().replace(/[\\\/]+$/, '');
  }

  function resolveAssetMediaUrl(asset) {
    if (!asset) return '';
    return cleanRemoteUrl(asset.preview_url || asset.open_url || asset.source_url || '');
  }

  function existingReferenceAssetIds() {
    var map = {};
    (state.references || []).forEach(function(item) {
      var aid = String((item && item.asset_id) || '').trim();
      if (aid) map[aid] = true;
    });
    return map;
  }

  function normalizeAssetPickerItem(asset) {
    var aid = String((asset && asset.asset_id) || '').trim();
    if (!aid) return null;
    var mediaType = String((asset && asset.media_type) || '').toLowerCase();
    if (mediaType && mediaType !== 'image') return null;
    return {
      asset_id: aid,
      name: String((asset && asset.filename) || aid).trim() || aid,
      size: Number((asset && asset.file_size) || 0),
      objectUrl: resolveAssetMediaUrl(asset),
      source_url: String((asset && (asset.open_url || asset.source_url || asset.preview_url)) || '').trim(),
      prompt: String((asset && asset.prompt) || '').trim(),
      purpose: (($('imglabReferencePurposeSelect') || {}).value || 'auto').trim() || 'auto'
    };
  }

  function selectedAssetPickerItems() {
    var selected = assetPickerState.selected || {};
    return (assetPickerState.items || []).filter(function(item) {
      return item && selected[item.asset_id];
    });
  }

  function appendReferenceItems(existing, incoming) {
    var next = (existing || []).slice();
    var existingAssets = {};
    next.forEach(function(item) {
      var aid = String((item && item.asset_id) || '').trim();
      if (aid) existingAssets[aid] = true;
    });
    (incoming || []).forEach(function(item) {
      var aid = String((item && item.asset_id) || '').trim();
      if (aid && existingAssets[aid]) return;
      if (aid) existingAssets[aid] = true;
      next.push(item);
    });
    return next;
  }

  function renderReferenceList() {
    var el = $('imglabReferenceList');
    if (!el) return;
    if (!state.references.length) {
      el.innerHTML = '<div class="imglab-empty-slot" style="grid-column:1 / -1;">还没有参考图，不上传就是纯文生图。</div>';
      return;
    }
    el.innerHTML = state.references.map(function(item, index) {
      return [
        '<div class="imglab-upload-card">',
        '<button type="button" class="imglab-upload-remove" data-ref-index="' + index + '" title="移除">×</button>',
        '<img src="' + escapeHtml(item.objectUrl) + '" alt="' + escapeHtml(item.name) + '">',
        '<div class="imglab-upload-card-body">',
        '<div class="imglab-upload-card-title">' + escapeHtml(item.name) + '</div>',
        '<div class="imglab-upload-card-meta">' + escapeHtml(formatFileSize(item.size)) + '</div>',
        '<select class="imglab-ref-purpose" data-ref-purpose-index="' + index + '">',
        Object.keys(PURPOSE_LABELS).map(function(key) {
          return '<option value="' + key + '"' + ((item.purpose || 'auto') === key ? ' selected' : '') + '>' + escapeHtml(PURPOSE_LABELS[key]) + '</option>';
        }).join(''),
        '</select>',
        '</div>',
        '</div>'
      ].join('');
    }).join('');
  }

  function ensureAssetPickerModal() {
    var modal = $('imglabAssetPickerModal');
    if (modal) return modal;
    modal = document.createElement('div');
    modal.id = 'imglabAssetPickerModal';
    modal.className = 'imglab-asset-picker-modal';
    modal.setAttribute('aria-hidden', 'true');
    modal.innerHTML = [
      '<div class="imglab-asset-picker-card" role="dialog" aria-modal="true" aria-labelledby="imglabAssetPickerTitle">',
      '<div class="imglab-asset-picker-head">',
      '<div>',
      '<h4 id="imglabAssetPickerTitle">选择素材库图片</h4>',
      '<p>可以选择一张或多张图片，确认后直接作为参考图使用。</p>',
      '</div>',
      '<button type="button" class="imglab-asset-picker-close" aria-label="关闭">×</button>',
      '</div>',
      '<div class="imglab-asset-picker-tools">',
      '<input type="search" id="imglabAssetPickerSearch" placeholder="搜索文件名、提示词或标签">',
      '<button type="button" class="btn btn-ghost btn-sm" id="imglabAssetPickerReload">刷新</button>',
      '</div>',
      '<div id="imglabAssetPickerStatus" class="imglab-asset-picker-status"></div>',
      '<div id="imglabAssetPickerGrid" class="imglab-asset-picker-grid"></div>',
      '<div class="imglab-asset-picker-foot">',
      '<span id="imglabAssetPickerCount">已选择 0 张</span>',
      '<div class="imglab-asset-picker-actions">',
      '<button type="button" class="btn btn-ghost" id="imglabAssetPickerCancel">取消</button>',
      '<button type="button" class="btn btn-primary" id="imglabAssetPickerConfirm">确认使用</button>',
      '</div>',
      '</div>',
      '</div>'
    ].join('');
    document.body.appendChild(modal);

    modal.addEventListener('click', function(event) {
      if (event.target === modal) closeAssetPicker();
      var closeBtn = event.target && event.target.closest ? event.target.closest('.imglab-asset-picker-close, #imglabAssetPickerCancel') : null;
      if (closeBtn) {
        event.preventDefault();
        closeAssetPicker();
      }
      var card = event.target && event.target.closest ? event.target.closest('[data-imglab-asset-id]') : null;
      if (card) {
        event.preventDefault();
        toggleAssetPickerSelection(card.getAttribute('data-imglab-asset-id'));
      }
    });

    var search = modal.querySelector('#imglabAssetPickerSearch');
    if (search) {
      search.addEventListener('input', function(event) {
        assetPickerState.query = event.target.value || '';
        renderAssetPicker();
      });
    }
    var reload = modal.querySelector('#imglabAssetPickerReload');
    if (reload) reload.addEventListener('click', function() { loadAssetPickerItems(true); });
    var confirm = modal.querySelector('#imglabAssetPickerConfirm');
    if (confirm) confirm.addEventListener('click', confirmAssetPicker);
    document.addEventListener('keydown', function(event) {
      if (event.key === 'Escape' && modal.classList.contains('is-visible')) closeAssetPicker();
    });
    return modal;
  }

  function filteredAssetPickerItems() {
    var query = String(assetPickerState.query || '').trim().toLowerCase();
    if (!query) return assetPickerState.items || [];
    return (assetPickerState.items || []).filter(function(item) {
      var haystack = [item.name || '', item.asset_id || '', item.prompt || ''].join(' ').toLowerCase();
      return haystack.indexOf(query) >= 0;
    });
  }

  function renderAssetPicker() {
    var modal = ensureAssetPickerModal();
    var grid = modal.querySelector('#imglabAssetPickerGrid');
    var status = modal.querySelector('#imglabAssetPickerStatus');
    var count = modal.querySelector('#imglabAssetPickerCount');
    var confirm = modal.querySelector('#imglabAssetPickerConfirm');
    if (!grid || !status) return;
    var selected = selectedAssetPickerItems();
    if (count) count.textContent = '已选择 ' + selected.length + ' 张';
    if (confirm) confirm.disabled = selected.length < 1;

    if (assetPickerState.loading) {
      status.textContent = '正在加载素材库图片...';
      grid.innerHTML = '<div class="imglab-asset-picker-empty">正在加载...</div>';
      return;
    }
    var items = filteredAssetPickerItems();
    if (!assetPickerState.items.length) {
      status.textContent = '素材库暂无图片';
      grid.innerHTML = '<div class="imglab-asset-picker-empty">暂无图片素材。你也可以继续使用“上传图片”。</div>';
      return;
    }
    if (!items.length) {
      status.textContent = '没有匹配的图片';
      grid.innerHTML = '<div class="imglab-asset-picker-empty">换个关键词试试。</div>';
      return;
    }
    status.textContent = '共 ' + assetPickerState.items.length + ' 张图片，当前显示 ' + items.length + ' 张';
    var existing = existingReferenceAssetIds();
    grid.innerHTML = items.map(function(item) {
      var selectedClass = assetPickerState.selected[item.asset_id] ? ' is-selected' : '';
      var already = existing[item.asset_id] ? ' is-existing' : '';
      var thumb = item.objectUrl
        ? '<img src="' + escapeHtml(item.objectUrl) + '" alt="' + escapeHtml(item.name) + '">'
        : '<div class="imglab-asset-picker-no-thumb">无预览</div>';
      return [
        '<button type="button" class="imglab-asset-picker-item' + selectedClass + already + '" data-imglab-asset-id="' + escapeHtml(item.asset_id) + '">',
        '<span class="imglab-asset-picker-thumb">' + thumb + '</span>',
        '<span class="imglab-asset-picker-name" title="' + escapeHtml(item.name) + '">' + escapeHtml(item.name) + '</span>',
        '<span class="imglab-asset-picker-meta">' + escapeHtml(formatFileSize(item.size)) + (already ? ' · 已添加' : '') + '</span>',
        '<span class="imglab-asset-picker-check">✓</span>',
        '</button>'
      ].join('');
    }).join('');
  }

  function toggleAssetPickerSelection(assetId) {
    assetId = String(assetId || '').trim();
    if (!assetId) return;
    if (assetPickerState.selected[assetId]) delete assetPickerState.selected[assetId];
    else assetPickerState.selected[assetId] = true;
    renderAssetPicker();
  }

  function loadAssetPickerItems(force) {
    var modal = ensureAssetPickerModal();
    if (!force && assetPickerState.items.length) {
      renderAssetPicker();
      return Promise.resolve(assetPickerState.items);
    }
    var base = localBase();
    if (!base) {
      assetPickerState.items = [];
      renderAssetPicker();
      return Promise.reject(new Error('当前未检测到可用的后端地址，无法读取素材库'));
    }
    assetPickerState.loading = true;
    renderAssetPicker();
    return fetch(base + '/api/assets?media_type=image&limit=100', { headers: authHeadersSafe() })
      .then(function(response) {
        return response.json().then(function(data) {
          return { ok: response.ok, data: data || {} };
        });
      })
      .then(function(result) {
        if (!result.ok) throw new Error(responseErrorText(result.data, '素材库图片加载失败'));
        var rows = Array.isArray(result.data.assets) ? result.data.assets : [];
        assetPickerState.items = rows.map(normalizeAssetPickerItem).filter(Boolean);
        assetPickerState.loading = false;
        renderAssetPicker();
        return assetPickerState.items;
      })
      .catch(function(err) {
        assetPickerState.loading = false;
        var status = modal.querySelector('#imglabAssetPickerStatus');
        if (status) status.textContent = (err && err.message) || '素材库图片加载失败';
        renderAssetPicker();
        throw err;
      });
  }

  function openAssetPicker() {
    var modal = ensureAssetPickerModal();
    assetPickerState.selected = {};
    assetPickerState.query = '';
    var search = modal.querySelector('#imglabAssetPickerSearch');
    if (search) search.value = '';
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
    loadAssetPickerItems(false).catch(function(err) {
      showMessage((err && err.message) || '素材库图片加载失败', true);
    });
  }

  function closeAssetPicker() {
    var modal = $('imglabAssetPickerModal');
    if (!modal) return;
    modal.classList.remove('is-visible');
    modal.setAttribute('aria-hidden', 'true');
  }

  function confirmAssetPicker() {
    var items = selectedAssetPickerItems();
    if (!items.length) return;
    var purpose = (($('imglabReferencePurposeSelect') || {}).value || 'auto').trim() || 'auto';
    var incoming = items.map(function(item) {
      return {
        name: item.name,
        size: item.size,
        purpose: purpose,
        objectUrl: item.objectUrl || item.source_url,
        source_url: item.source_url || item.objectUrl,
        asset_id: item.asset_id
      };
    }).filter(function(item) {
      return item.asset_id && item.objectUrl;
    });
    var before = state.references.length;
    state.references = appendReferenceItems(state.references, incoming);
    closeAssetPicker();
    renderReferenceList();
    var added = state.references.length - before;
    showMessage(added > 0 ? ('已从素材库添加 ' + added + ' 张参考图。') : '选择的图片已在参考图列表中。', false);
  }

  function currentResult() {
    if (!state.results.length) return null;
    return state.results[state.activeResultIndex] || state.results[0] || null;
  }

  function resultPreviewUrl(item) {
    if (!item) return '';
    return item.sourceUrl || item.data_url || item.url || '';
  }

  function shortenText(text, maxChars) {
    var clean = String(text || '').trim().replace(/\s+/g, ' ');
    if (!clean) return '';
    return clean.length > maxChars ? (clean.slice(0, maxChars) + '...') : clean;
  }

  function cleanPromptText(text) {
    var raw = String(text || '').trim();
    if (!raw) return '';
    var marker = '用户提示词：';
    var idx = raw.lastIndexOf(marker);
    if (idx >= 0) {
      var userText = raw.slice(idx + marker.length).trim();
      if (userText) return userText;
    }
    return raw;
  }

  function promptTitleText(prompt, fallback) {
    return shortenText(cleanPromptText(prompt), 30) || fallback || '图片任务';
  }

  function displayModelName(model) {
    var value = String(model || '').trim().toLowerCase();
    if (!value) return '';
    if (value.indexOf('gpt-image-2') >= 0) return '\u5f71\u68a6lite';
    return '';
  }

  function customSelectLabel(select) {
    if (!select) return '';
    var option = select.options && select.options[select.selectedIndex];
    return option ? option.textContent : '';
  }

  function closeImglabCustomSelects(exceptWrap) {
    document.querySelectorAll('#content-image-composer-studio .imglab-custom-select.is-open').forEach(function(wrap) {
      if (exceptWrap && wrap === exceptWrap) return;
      wrap.classList.remove('is-open');
      var button = wrap.querySelector('.imglab-custom-select-button');
      if (button) button.setAttribute('aria-expanded', 'false');
    });
  }

  function syncImglabCustomSelect(select) {
    if (!select || !select.dataset.imglabCustomSelect) return;
    var wrap = select.nextElementSibling;
    if (!wrap || !wrap.classList || !wrap.classList.contains('imglab-custom-select')) return;
    var value = select.value;
    var label = customSelectLabel(select);
    var labelEl = wrap.querySelector('.imglab-custom-select-label');
    if (labelEl) labelEl.textContent = label;
    wrap.querySelectorAll('[data-custom-select-value]').forEach(function(item) {
      var active = item.getAttribute('data-custom-select-value') === value;
      item.classList.toggle('is-selected', active);
      item.setAttribute('aria-selected', active ? 'true' : 'false');
    });
  }

  function buildImglabCustomSelect(select) {
    if (!select || select.dataset.imglabCustomSelect === '1') return;
    select.dataset.imglabCustomSelect = '1';
    select.classList.add('imglab-native-select-hidden');

    if (!select.dataset.imglabCustomSelectSyncBound) {
      select.dataset.imglabCustomSelectSyncBound = '1';
      select.addEventListener('change', function() {
        syncImglabCustomSelect(select);
      });
    }

    var wrap = document.createElement('div');
    wrap.className = 'imglab-custom-select';
    wrap.setAttribute('data-custom-select-for', select.id || '');

    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'imglab-custom-select-button';
    button.setAttribute('aria-haspopup', 'listbox');
    button.setAttribute('aria-expanded', 'false');
    button.innerHTML = '<span class="imglab-custom-select-label"></span><span class="imglab-custom-select-arrow" aria-hidden="true"></span>';

    var menu = document.createElement('div');
    menu.className = 'imglab-custom-select-menu';
    menu.setAttribute('role', 'listbox');

    Array.prototype.forEach.call(select.options || [], function(option) {
      var item = document.createElement('button');
      item.type = 'button';
      item.className = 'imglab-custom-select-option';
      item.setAttribute('role', 'option');
      item.setAttribute('data-custom-select-value', option.value);
      item.textContent = option.textContent || option.value;
      item.addEventListener('click', function(event) {
        event.preventDefault();
        event.stopPropagation();
        select.value = option.value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
        syncImglabCustomSelect(select);
        closeImglabCustomSelects();
      });
      menu.appendChild(item);
    });

    button.addEventListener('click', function(event) {
      event.preventDefault();
      event.stopPropagation();
      var willOpen = !wrap.classList.contains('is-open');
      closeImglabCustomSelects(wrap);
      wrap.classList.toggle('is-open', willOpen);
      button.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    });

    wrap.appendChild(button);
    wrap.appendChild(menu);
    select.parentNode.insertBefore(wrap, select.nextSibling);
    syncImglabCustomSelect(select);
  }

  function initImglabCustomSelects() {
    [
      'imglabAspectRatioSelect',
      'imglabModelSelect',
      'imglabQualitySelect',
      'imglabBackgroundSelect'
    ].forEach(function(id) {
      buildImglabCustomSelect($(id));
    });
    if (!customSelectEventsBound) {
      customSelectEventsBound = true;
      document.addEventListener('click', function(event) {
        if (event.target && event.target.closest && event.target.closest('#content-image-composer-studio .imglab-custom-select')) return;
        closeImglabCustomSelects();
      });
      document.addEventListener('keydown', function(event) {
        if (event.key === 'Escape') closeImglabCustomSelects();
      });
    }
  }

  function syncImglabCustomSelects() {
    [
      'imglabAspectRatioSelect',
      'imglabModelSelect',
      'imglabQualitySelect',
      'imglabBackgroundSelect'
    ].forEach(function(id) {
      syncImglabCustomSelect($(id));
    });
  }

  function promptFromJob(job) {
    if (!job) return '';
    return cleanPromptText(
      job.prompt
      || (job.requestPayload && job.requestPayload.prompt)
      || (job.cloudJob && (job.cloudJob.prompt || (job.cloudJob.request_payload && job.cloudJob.request_payload.prompt)))
      || ''
    );
  }

  function promptFromResult(item) {
    if (!item) return '';
    var remembered = state.currentJobId
      ? state.recentJobs.find(function(job) { return job && job.jobId === state.currentJobId; })
      : null;
    return cleanPromptText(item.prompt || state.currentJobPrompt || promptFromJob(remembered) || '');
  }

  function selectedHistoryJob() {
    if (!state.selectedJobId) return null;
    return state.recentJobs.find(function(job) {
      return job && job.jobId === state.selectedJobId;
    }) || null;
  }

  function activeResultDetail() {
    var selectedJob = selectedHistoryJob();
    if (selectedJob) {
      return {
        type: 'job',
        status: selectedJob.status || 'running',
        title: promptTitleText(promptFromJob(selectedJob), selectedJob.title || '图片任务'),
        prompt: promptFromJob(selectedJob),
        previewUrl: selectedJob.image || '',
        downloadUrl: selectedJob.image || '',
        assetId: selectedJob.assetId || selectedJob.asset_id || '',
        model: selectedJob.model || '',
        aspectRatio: selectedJob.aspectRatio || '',
        size: selectedJob.size || '',
        jobId: selectedJob.jobId || '',
        resultCount: selectedJob.resultCount || 0
      };
    }

    var active = currentResult();
    if (active) {
      return {
        type: 'result',
        status: 'completed',
        title: promptTitleText(promptFromResult(active), '生成结果 ' + (state.activeResultIndex + 1)),
        prompt: promptFromResult(active),
        previewUrl: resultPreviewUrl(active),
        downloadUrl: active.sourceUrl || active.url || active.data_url || '',
        assetId: active.assetId || '',
        model: active.model || '',
        aspectRatio: active.aspectRatio || '',
        size: active.size || '',
        jobId: state.currentJobId || '',
        resultCount: state.results.length
      };
    }

    if (state.currentJobId && state.currentJobStatus) {
      var remembered = state.recentJobs.find(function(job) {
        return job && job.jobId === state.currentJobId;
      }) || null;
      return {
        type: 'job',
        status: state.currentJobStatus,
        title: promptTitleText(state.currentJobPrompt || promptFromJob(remembered), '当前图片任务'),
        prompt: cleanPromptText(state.currentJobPrompt || promptFromJob(remembered)),
        previewUrl: remembered && remembered.image ? remembered.image : '',
        downloadUrl: remembered && remembered.image ? remembered.image : '',
        assetId: remembered ? (remembered.assetId || remembered.asset_id || '') : '',
        model: remembered ? (remembered.model || '') : '',
        aspectRatio: remembered ? (remembered.aspectRatio || '') : '',
        size: remembered ? (remembered.size || '') : '',
        jobId: state.currentJobId,
        resultCount: remembered ? (remembered.resultCount || 0) : 0
      };
    }
    return null;
  }

  function renderResultPreview(detail) {
    if (detail && detail.previewUrl) {
      return '<img src="' + escapeHtml(detail.previewUrl) + '" alt="' + escapeHtml(detail.title || '图片结果') + '">';
    }
    if (detail && detail.status === 'failed') {
      return [
        '<div class="imglab-result-preview-placeholder is-error">',
        '<strong>任务失败</strong>',
        '<span>这条任务没有可展示的图片结果，你可以复制右侧提示词继续修改后重试。</span>',
        '</div>'
      ].join('');
    }
    if (detail && (detail.status === 'running' || detail.status === 'stale')) {
      return [
        '<div class="imglab-result-preview-placeholder">',
        '<strong>图片正在生成中</strong>',
        '<span>高质量模型通常需要 5-10 分钟。你可以先继续使用其他功能，完成后会在右下角提醒。</span>',
        '</div>'
      ].join('');
    }
    return [
      '<div class="imglab-result-preview-placeholder">',
      '<strong>暂无图片预览</strong>',
      '<span>这条历史任务暂时还没有可加载的图片，你可以先查看提示词或刷新任务状态。</span>',
      '</div>'
    ].join('');
  }

  function renderDetailPills(detail) {
    if (!detail) return '';
    var pills = [
      detail.type === 'result' ? ('第 ' + (state.activeResultIndex + 1) + ' 张结果') : ('任务 ' + String(detail.jobId || '').slice(0, 8)),
      jobStatusText(detail.status)
    ];
    var modelLabel = displayModelName(detail.model);
    if (modelLabel) pills.push(modelLabel);
    if (detail.aspectRatio) pills.push(detail.aspectRatio);
    if (detail.size) pills.push(detail.size);
    if (detail.resultCount) pills.push(detail.resultCount + ' 张');
    if (detail.assetId) pills.push('素材 ' + detail.assetId);
    return pills.map(function(text) {
      return '<span class="imglab-result-pill">' + escapeHtml(text) + '</span>';
    }).join('');
  }

  function fillPromptInput(prompt) {
    var input = $('imglabPromptInput');
    if (!input) return;
    input.value = prompt || '';
    input.focus();
    if (typeof input.setSelectionRange === 'function') {
      var pos = input.value.length;
      input.setSelectionRange(pos, pos);
    }
  }

  function writeTextToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function(resolve, reject) {
      try {
        var textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.setAttribute('readonly', 'readonly');
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        var ok = document.execCommand('copy');
        document.body.removeChild(textarea);
        if (ok) resolve();
        else reject(new Error('copy failed'));
      } catch (err) {
        reject(err);
      }
    });
  }

  function copyActivePrompt() {
    var detail = activeResultDetail();
    var prompt = detail ? cleanPromptText(detail.prompt) : '';
    if (!prompt) {
      showMessage('当前结果还没有可复制的提示词。', true);
      return;
    }
    fillPromptInput(prompt);
    writeTextToClipboard(prompt)
      .then(function() {
        showMessage('提示词已复制，并回填到左侧输入框。', false);
      })
      .catch(function() {
        showMessage('提示词已回填到左侧输入框，但复制到剪贴板失败了。', true);
      });
  }

  function downloadActiveImage() {
    var detail = activeResultDetail();
    var url = detail ? (detail.downloadUrl || detail.previewUrl || '') : '';
    if (!url) {
      showMessage('当前结果还没有可下载的图片。', true);
      return;
    }
    var link = document.createElement('a');
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener';
    link.download = (detail && detail.jobId ? ('image-studio-' + detail.jobId) : 'image-studio-result') + '.png';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    showMessage('已开始下载图片。', false);
  }

  function ensureResultPreviewModal() {
    var existing = $('imglabPreviewModal');
    if (existing) return existing;

    var wrapper = document.createElement('div');
    wrapper.innerHTML = [
      '<div id="imglabPreviewModal" class="imglab-preview-modal" hidden>',
      '<div class="imglab-preview-backdrop" data-imglab-preview-close></div>',
      '<div class="imglab-preview-dialog" role="dialog" aria-modal="true" aria-label="Image preview">',
      '<button type="button" class="imglab-preview-close" data-imglab-preview-close aria-label="Close">&times;</button>',
      '<div class="imglab-preview-stage">',
      '<img id="imglabPreviewModalImage" alt="Image preview">',
      '</div>',
      '</div>',
      '</div>'
    ].join('');
    document.body.appendChild(wrapper.firstChild);
    var modal = $('imglabPreviewModal');
    if (!modal) return null;

    modal.addEventListener('click', function(event) {
      if (event.target.closest('[data-imglab-preview-close]')) {
        closeResultPreviewModal();
      }
    });
    document.addEventListener('keydown', function(event) {
      if (event.key === 'Escape') {
        closeResultPreviewModal();
      }
    });
    return modal;
  }

  function openResultPreviewModal(url, title) {
    if (!url) {
      showMessage('Current result has no preview image yet.', true);
      return;
    }
    var modal = ensureResultPreviewModal();
    if (!modal) return;
    var image = $('imglabPreviewModalImage');
    if (!image) return;
    image.src = url;
    image.alt = title ? (title + ' preview') : 'Image preview';
    modal.hidden = false;
    document.body.classList.add('imglab-preview-open');
  }

  function closeResultPreviewModal() {
    var modal = $('imglabPreviewModal');
    if (!modal) return;
    var image = $('imglabPreviewModalImage');
    if (image) {
      image.removeAttribute('src');
      image.alt = 'Image preview';
    }
    modal.hidden = true;
    document.body.classList.remove('imglab-preview-open');
  }

  function cloudImageUrlFromJob(job) {
    var assets = (job && job.assets) || {};
    var ids = Array.isArray(job && job.asset_ids) ? job.asset_ids : [];
    for (var i = 0; i < ids.length; i += 1) {
      var asset = assets[ids[i]] || {};
      var src = String(asset.source_url || asset.preview_url || '').trim();
      if (src) return src;
    }
    var saved = Array.isArray(job && job.saved_assets) ? job.saved_assets : [];
    for (var j = 0; j < saved.length; j += 1) {
      var item = saved[j] || {};
      var row = item.asset || item.cloud_asset || {};
      var url = String(row.source_url || row.preview_url || item.source_url || '').trim();
      if (url) return url;
    }
    var payload = (job && job.result_payload) || {};
    var images = Array.isArray(payload.images) ? payload.images : [];
    return images[0] ? String(images[0].source_url || images[0].url || images[0].data_url || '').trim() : '';
  }

  function normalizeCloudJob(job) {
    if (!job || !job.job_id) return null;
    var payload = job.result_payload || {};
    var images = Array.isArray(payload.images) ? payload.images : [];
    var ids = Array.isArray(job.asset_ids) ? job.asset_ids : [];
    var prompt = cleanPromptText(job.prompt || (job.request_payload && job.request_payload.prompt) || '');
    return {
      jobId: String(job.job_id || ''),
      status: String(job.status || 'running'),
      title: promptTitleText(prompt, job.title || '图片任务'),
      prompt: prompt,
      image: cloudImageUrlFromJob(job),
      assetId: ids[0] || '',
      resultCount: ids.length || images.length || 0,
      model: (payload.meta && payload.meta.model) || '',
      aspectRatio: (payload.meta && payload.meta.aspect_ratio) || '',
      size: (payload.meta && payload.meta.size) || '',
      updatedAt: Date.parse(job.updated_at || job.completed_at || job.created_at || '') || Date.now(),
      cloud: true,
      cloudJob: job
    };
  }

  function applyCloudJobResult(job) {
    var payload = (job && job.result_payload) || {};
    var assets = (job && job.assets) || {};
    var saved = Array.isArray(job && job.saved_assets) ? job.saved_assets : [];
    state.results = (Array.isArray(payload.images) ? payload.images : []).map(function(item, index) {
      var savedItem = saved[index] || {};
      var row = savedItem.asset || savedItem.cloud_asset || {};
      var aid = item.asset_id || savedItem.asset_id || row.asset_id || '';
      var asset = aid ? (assets[aid] || {}) : {};
      return {
        url: item.url || '',
        data_url: item.data_url || '',
        assetId: aid,
        sourceUrl: asset.source_url || row.source_url || item.source_url || savedItem.source_url || '',
        prompt: cleanPromptText((job && job.prompt) || (job && job.request_payload && job.request_payload.prompt) || ''),
        model: payload.meta && payload.meta.model,
        aspectRatio: payload.meta && payload.meta.aspect_ratio,
        size: payload.meta && payload.meta.size
      };
    });
    if (!state.results.length) {
      var fallback = cloudImageUrlFromJob(job);
      if (fallback) state.results = [{ url: fallback, sourceUrl: fallback, assetId: (job.asset_ids || [])[0] || '' }];
    }
    state.activeResultIndex = 0;
    state.selectedJobId = '';
    state.currentJobPrompt = cleanPromptText((job && job.prompt) || (job && job.request_payload && job.request_payload.prompt) || '');
    state.currentJobStatus = String((job && job.status) || 'completed');
    renderResultSurface();
    setRightView('result');
  }

  function jobStatusText(status) {
    if (status === 'completed') return '已完成';
    if (status === 'failed') return '失败';
    if (status === 'running') return '生成中';
    if (status === 'stale') return '待刷新';
    return '等待中';
  }

  function friendlyImageTaskFailureMessage() {
    return '可能网络波动导致任务提交失败，请尝试重新提交。';
  }

  function imageTaskFailureMessage(data, fallback) {
    var detail = data && (data.error || data.detail || data.message);
    if (typeof detail === 'string' && detail.trim()) return detail.trim();
    if (detail && typeof detail === 'object') {
      try { return JSON.stringify(detail); } catch (err) {}
    }
    return fallback || friendlyImageTaskFailureMessage();
  }

  function resultCardsHtml() {
    var cards = [];
    var activeResultAssetIds = {};
    state.results.forEach(function(item) {
      var aid = String((item && item.assetId) || '').trim();
      if (aid) activeResultAssetIds[aid] = true;
    });
    if (state.currentJobId && state.currentJobStatus === 'running') {
      cards.push({
        type: 'job',
        jobId: state.currentJobId,
        title: promptTitleText(state.currentJobPrompt, '当前图片任务'),
        status: 'running'
      });
    }
    state.results.forEach(function(item, index) {
      cards.push({
        type: 'result',
        index: index,
        title: promptTitleText(promptFromResult(item), '生成结果 ' + (index + 1)),
        status: 'completed',
        image: resultPreviewUrl(item),
        assetId: item.assetId || ''
      });
    });
    state.recentJobs.forEach(function(job) {
      if (!job || !job.jobId) return;
      if (job.jobId === state.currentJobId && (state.currentJobStatus === 'running' || state.results.length)) return;
      if (job.status === 'completed' && !job.image && !job.resultCount) return;
      var jobAssetId = String(job.assetId || job.asset_id || '').trim();
      if (job.status === 'completed' && jobAssetId && activeResultAssetIds[jobAssetId]) return;
      var displayStatus = job.status || 'running';
      if (displayStatus === 'running') displayStatus = 'stale';
      cards.push({
        type: 'job',
        jobId: job.jobId,
        title: promptTitleText(promptFromJob(job), job.title || '图片任务'),
        status: displayStatus,
        image: job.image || '',
        resultCount: job.resultCount || 0
      });
    });
    if (!cards.length) {
      return '<div class="imglab-empty-slot" style="grid-column:1 / -1;">还没有图片任务，提交一次后这里会出现任务卡片。</div>';
    }
    return cards.slice(0, 12).map(function(card) {
      var isActive = card.type === 'result'
        ? (!state.selectedJobId && card.index === state.activeResultIndex)
        : (!!state.selectedJobId && card.jobId === state.selectedJobId);
      var attrs = card.type === 'result'
        ? 'data-result-index="' + card.index + '"'
        : 'data-imglab-job="' + escapeHtml(card.jobId) + '"';
      var media = card.status === 'completed' && card.image
        ? '<img src="' + escapeHtml(card.image) + '" alt="' + escapeHtml(card.title) + '">'
        : '<div class="imglab-task-card-pending">' + (card.status === 'running' ? '<span class="imglab-task-spinner" aria-hidden="true"></span>' : '<span class="imglab-task-done-mark">' + (card.status === 'stale' ? '刷新' : '完成') + '</span>') + '</div>';
      if (card.status === 'failed') media = '<div class="imglab-task-card-failed">失败</div>';
      return [
        '<button type="button" class="imglab-task-card' + (isActive ? ' is-active' : '') + ' is-' + escapeHtml(card.status || 'running') + '" ' + attrs + ' title="' + escapeHtml(card.title || '图片任务') + '">',
        '<div class="imglab-task-card-media">' + media + '</div>',
        '<div class="imglab-task-card-body">',
        '<span class="imglab-task-card-title">' + escapeHtml(card.title || '图片任务') + '</span>',
        '<span class="imglab-task-card-meta">' + escapeHtml(jobStatusText(card.status)) + (card.resultCount ? ' · ' + escapeHtml(card.resultCount) + ' 张' : '') + (card.assetId ? ' · 素材 ' + escapeHtml(card.assetId) : '') + '</span>',
        '</div>',
        '</button>'
      ].join('');
    }).join('');
  }

  function renderResultSurface() {
    var surface = $('imglabResultSurface');
    var meta = $('imglabResultMeta');
    var gallery = $('imglabResultGallery');
    if (!surface || !meta || !gallery) return;

    var detail = activeResultDetail();
    if (!detail) {
      surface.innerHTML = [
        '<div class="imglab-result-placeholder">',
        '<strong>结果展示区</strong>',
        '<span>左侧填好提示词后直接生成。上传参考图时会合成，不上传时就是纯文生图。</span>',
        '</div>'
      ].join('');
      meta.innerHTML = '';
      gallery.innerHTML = resultCardsHtml();
      bindRecentJobButtons();
      return;
    }

    surface.innerHTML = [
      '<div class="imglab-result-layout">',
      '<div class="imglab-result-preview-pane">',
      renderResultPreview(detail),
      '</div>',
      '<div class="imglab-result-side">',
      '<div class="imglab-result-side-top">',
      '<span class="imglab-result-kicker">' + escapeHtml(detail.type === 'result' ? '当前结果' : '历史任务') + '</span>',
      '<h5 class="imglab-result-heading">' + escapeHtml(detail.title || '图片任务') + '</h5>',
      '</div>',
      '<div class="imglab-result-pills">' + renderDetailPills(detail) + '</div>',
      '<div class="imglab-result-actions">',
      '<button type="button" class="btn btn-sm imglab-action-btn is-copy" data-imglab-copy-prompt>' + (detail.prompt ? '复制提示词并回填' : '暂无提示词') + '</button>',
      '<button type="button" class="btn btn-sm imglab-action-btn is-download" data-imglab-download-image' + (detail.previewUrl ? '' : ' disabled') + '>下载图片</button>',
      '<button type="button" class="btn btn-sm imglab-action-btn is-preview" data-imglab-open-preview' + (detail.previewUrl ? '' : ' disabled') + '>查看原图</button>',
      '</div>',
      '<div class="imglab-result-prompt-panel">',
      '<div class="imglab-result-prompt-head">提示词</div>',
      '<div class="imglab-result-prompt-body">' + escapeHtml(detail.prompt || '这条任务暂时没有可展示的提示词。') + '</div>',
      '</div>',
      '</div>',
      '</div>'
    ].join('');
    meta.innerHTML = [
      '<div class="imglab-result-gallery-head">',
      '<strong>任务记录</strong>',
      '<span>点击下方卡片切换当前结果和历史任务详情。</span>',
      '</div>'
    ].join('');

    gallery.innerHTML = resultCardsHtml();
    bindRecentJobButtons();
  }

  function bindRecentJobButtons() {
    document.querySelectorAll('[data-result-index]').forEach(function(btn) {
      if (btn.dataset.bound) return;
      btn.dataset.bound = '1';
      btn.addEventListener('click', function() {
        state.selectedJobId = '';
        state.activeResultIndex = Number(btn.getAttribute('data-result-index')) || 0;
        renderResultSurface();
      });
    });
    document.querySelectorAll('[data-imglab-job]').forEach(function(btn) {
      if (btn.dataset.bound) return;
      btn.dataset.bound = '1';
      btn.addEventListener('click', function() {
        var jobId = btn.getAttribute('data-imglab-job') || '';
        if (!jobId) return;
        state.currentJobId = jobId;
        var hit = state.recentJobs.find(function(item) { return item && item.jobId === jobId; });
        state.currentJobStatus = (hit && hit.status) || 'running';
        state.currentJobPrompt = promptFromJob(hit);
        if (state.currentJobStatus === 'stale') state.currentJobStatus = 'running';
        setRightView('result');
        if (hit && hit.cloud && hit.cloudJob && hit.status === 'completed') {
          state.selectedJobId = '';
          applyCloudJobResult(hit.cloudJob);
          showMessage('已加载服务器保存的历史结果。', false);
          return;
        }
        state.selectedJobId = jobId;
        renderResultSurface();
        if (state.currentJobStatus === 'running' || (!hit || (hit.status === 'completed' && !hit.image))) {
          refreshJobStatus(true);
        }
      });
    });
  }

  function setRightView(view) {
    state.view = view === 'result' ? 'result' : 'examples';
    var resultPanel = $('imglabResultPanel');
    var examplesPanel = $('imglabExamplesPanel');
    if (resultPanel) resultPanel.hidden = state.view !== 'result';
    if (examplesPanel) examplesPanel.hidden = state.view !== 'examples';
  }

  function exampleImage(item) {
    return item.cover_image || item.image || '';
  }

  function updateExampleStatus() {
    var status = $('imglabExamplesStatus');
    var moreBtn = $('imglabExamplesMoreBtn');
    if (status) {
      if (state.examplesLoading && !state.examples.length) {
        status.textContent = '正在加载示例...';
      } else if (state.examples.length) {
        var total = state.examplesTotal || state.examples.length;
        status.textContent = '已加载 ' + state.examples.length + ' / ' + total + ' 条示例';
      } else {
        status.textContent = '暂时没有示例数据';
      }
    }
    if (moreBtn) {
      moreBtn.style.display = (state.examples.length && state.examples.length < state.examplesTotal) ? '' : 'none';
      moreBtn.disabled = !!state.examplesLoading;
      moreBtn.textContent = state.examplesLoading ? '加载中...' : '加载更多示例';
    }
  }

  function renderExamples() {
    var el = $('imglabExamplesGrid');
    if (!el) return;
    if (!state.examples.length) {
      el.innerHTML = '<div class="imglab-empty-slot" style="grid-column:1 / -1;">还没有示例数据。</div>';
      updateExampleStatus();
      return;
    }
    el.innerHTML = state.examples.map(function(item, index) {
      var tags = Array.isArray(item.tags) ? item.tags : [];
      var previewText = String(item.preview_text || item.prompt || '').trim();
      return [
        '<button type="button" class="imglab-example-card" data-example-index="' + index + '">',
        exampleImage(item) ? '<img src="' + escapeHtml(exampleImage(item)) + '" alt="' + escapeHtml(item.title) + '">' : '<div style="aspect-ratio:1 / 1;background:#eef3fb;"></div>',
        '<div class="imglab-example-body">',
        '<h4 class="imglab-example-title">' + escapeHtml(item.title || ('示例 ' + (index + 1))) + '</h4>',
        '<p class="imglab-example-copy">' + escapeHtml(previewText ? (previewText.slice(0, 120) + (previewText.length > 120 ? '...' : '')) : '暂无提示词预览') + '</p>',
        '<div class="imglab-example-tag-row">',
        tags.slice(0, 4).map(function(tag) {
          return '<span class="imglab-example-tag">' + escapeHtml(tag) + '</span>';
        }).join(''),
        (displayModelName(item.model) ? '<span class="imglab-example-tag">' + escapeHtml(displayModelName(item.model)) + '</span>' : ''),
        '</div>',
        '</div>',
        '</button>'
      ].join('');
    }).join('');
    updateExampleStatus();
  }

  function removeReference(index) {
    var next = state.references.slice();
    var removed = next.splice(index, 1);
    releaseItems(removed);
    state.references = next;
    renderReferenceList();
  }

  function updateReferencePurpose(index, purpose) {
    if (!state.references[index]) return;
    state.references[index].purpose = purpose || 'auto';
    renderReferenceList();
  }

  function applyDefaultReferencePurposeToExisting(purpose) {
    var nextPurpose = purpose || 'auto';
    if (!state.references.length) return;
    state.references = state.references.map(function(item) {
      return Object.assign({}, item, { purpose: nextPurpose });
    });
    renderReferenceList();
    showMessage('已将已上传参考图标记为“' + (PURPOSE_LABELS[nextPurpose] || '普通参考') + '”。', false);
  }

  function resetWorkspace() {
    stopPolling();
    releaseItems(state.references);
    state.references = [];
    state.results = [];
    state.activeResultIndex = 0;
    state.selectedJobId = '';
    state.view = 'examples';
    state.submitting = false;
    state.currentJobId = '';
    state.currentJobStatus = '';
    state.currentJobPrompt = '';
    if ($('imglabPromptInput')) $('imglabPromptInput').value = '';
    if ($('imglabAspectRatioSelect')) $('imglabAspectRatioSelect').value = '9:16';
    if ($('imglabModelSelect')) $('imglabModelSelect').value = 'gpt-image-2';
    if ($('imglabQualitySelect')) $('imglabQualitySelect').value = 'high';
    if ($('imglabBackgroundSelect')) $('imglabBackgroundSelect').value = 'auto';
    syncImglabCustomSelects();
    if ($('imglabReferenceInput')) $('imglabReferenceInput').value = '';
    showMessage('');
    renderReferenceList();
    renderResultSurface();
    setRightView('examples');
  }

  function setSubmitting(submitting) {
    state.submitting = !!submitting;
    var btn = $('imglabGenerateBtn');
    if (!btn) return;
    btn.disabled = !!submitting;
    btn.textContent = submitting ? '提交中...' : '开始生成图片';
  }

  function authHeadersSafe() {
    if (typeof authHeaders === 'function') {
      var headers = authHeaders() || {};
      delete headers['Content-Type'];
      delete headers['content-type'];
      return headers;
    }
    return {};
  }

  function applyJobResult(payload) {
    state.results = (payload.images || []).map(function(item) {
      return {
        url: item.url || '',
        data_url: item.data_url || '',
        assetId: item.asset_id || '',
        sourceUrl: item.source_url || '',
        prompt: cleanPromptText(state.currentJobPrompt),
        model: payload.meta && payload.meta.model,
        aspectRatio: payload.meta && payload.meta.aspect_ratio,
        size: payload.meta && payload.meta.size
      };
    });
    state.activeResultIndex = 0;
    state.selectedJobId = '';
    renderResultSurface();
    setRightView('result');
  }

  function loadCloudJobHistory() {
    var base = cloudBase();
    if (!base) return Promise.resolve([]);
    return fetch(base + '/api/creative-jobs?feature_type=image_studio&limit=12', {
      headers: authHeadersSafe()
    })
      .then(function(response) {
        return response.json().then(function(data) {
          return { ok: response.ok, data: data || {} };
        });
      })
      .then(function(result) {
        if (!result.ok) throw new Error('cloud history failed');
        var rows = (Array.isArray(result.data.items) ? result.data.items : [])
          .map(normalizeCloudJob)
          .filter(Boolean);
        mergeRecentJobs(rows);
        renderWorkspace();
        return rows;
      })
      .catch(function(err) {
        console.warn('图片云端历史加载失败', err);
        return [];
      });
  }

  function refreshJobStatus(showToast) {
    if (!state.currentJobId) return;
    fetch(localBase() + '/api/comfly-image-studio/jobs/' + encodeURIComponent(state.currentJobId), {
      headers: authHeadersSafe()
    })
      .then(function(response) {
        return response.json().then(function(data) {
          return { ok: response.ok, data: data || {} };
        });
      })
      .then(function(result) {
        if (!result.ok) {
          throw new Error(imageTaskFailureMessage(result.data));
        }
        var status = String(result.data.status || '').trim();
        state.currentJobStatus = status;
        updateRememberedJob(state.currentJobId, { status: status });
        if (status === 'running') {
          renderResultSurface();
          schedulePoll(3000);
          return;
        }
        stopPolling();
        if (status === 'completed') {
          applyJobResult(result.data);
          var savedCount = state.results.filter(function(item) { return item.assetId; }).length;
          var first = state.results[0] || {};
          updateRememberedJob(state.currentJobId, {
            status: 'completed',
            title: '图片任务',
            resultCount: state.results.length,
            image: resultPreviewUrl(first),
            assetId: first.assetId || ''
          });
          showMessage(savedCount ? '图片任务已完成，并已保存到素材库。' : '图片任务已完成，素材库保存失败时会记录到日志。', false);
          notifyTaskOnce('completed');
        } else if (status === 'failed') {
          renderResultSurface();
          updateRememberedJob(state.currentJobId, { status: 'failed' });
          var failMessage = imageTaskFailureMessage(result.data);
          showMessage(failMessage, true);
          notifyTaskOnce('failed', failMessage);
        } else if (showToast) {
          showMessage('任务状态已刷新。', false);
        }
      })
      .catch(function(err) {
        console.warn('图片任务状态刷新失败', err);
        stopPolling();
        state.currentJobStatus = 'failed';
        updateRememberedJob(state.currentJobId, { status: 'failed' });
        renderResultSurface();
        var failMessage = imageTaskFailureMessage({ error: err && err.message });
        showMessage(failMessage, true);
        notifyTaskOnce('failed', failMessage);
      });
  }

  function translateKnownMissingPrompt(item, promptEn) {
    var id = item && String(item.id || '');
    if (id === '13621') {
      return '综合医学信息图，临床白底，高精度 3D 医学插画风。主题为“心脏病发作的因果链”：从风险因素、斑块形成、斑块破裂、血栓、心肌缺血到永久损伤。中央是透明解剖人体，显示心脏、冠状动脉、肺部和血管系统，用发光红色高亮冠状动脉阻塞与胸部、左臂放射痛。左右两侧和底部用 12 个清晰中文模块呈现：主要风险因素、动脉粥样硬化、冠脉血流减少、易损斑块、斑块破裂与血栓、心肌缺血、细胞损伤级联、急性心梗、永久心肌损伤、并发症、全身影响、疼痛是最终信号。所有标题、注释、引导线标注必须是清晰简体中文，信息密集但排版干净。';
    }
    if (id === '13491') {
      return '以《金瓶梅》人物“潘金莲”为主题，创作一张复古科学百科风知识图谱。中心主体为极具真实 3D 弹出感的潘金莲形象，像从泛黄百科纸面中跃出。周围布置 6-8 个结构化知识模块，包含人物关系、时代背景、关键情节、性格与心理、服饰器物、文学地位、文化影响、争议解读等。使用精细线稿、复古米色纸张、复杂引导线、箭头、括号和节点，把中心人物与各模块连接成完整知识网络。主标题使用中文书法体，所有模块标题、注释和手写说明必须为清晰简体中文，信息密集、专业、像百科全书插图，不要品牌 logo，比例 3:4。';
    }
    if (id === '13433') {
      return '35mm 胶片摄影，温暖复古的日式温泉旅馆氛围，木质灯笼柔光与自然窗光混合，轻微胶片颗粒，编辑大片质感。画面为亲密中景：一位二十岁出头的中国女性，精致自然五官，瓷白暖调皮肤，柔和自然妆容，深棕长发低低挽起，几缕碎发落在脸侧和颈部。她穿宽松白色浴衣，坐在传统木质缘侧，身体微微转向镜头，姿态放松优雅，手轻扶浴衣领口，另一只手撑在身后。背景是温暖木质室内、纸拉门和远处虚化的温泉蒸汽，轮廓光突出皮肤和布料质感，真实布料褶皱、自然发丝、复古暖色调，无水印无文字。';
    }
    return promptEn;
  }

  function normalizeExample(item) {
    var tags = Array.isArray(item && item.tags) ? item.tags : [];
    var prompt = String((item && item.prompt) || '').trim();
    var promptEn = String((item && item.prompt_en) || '').trim();
    var promptZh = String((item && item.prompt_zh) || '').trim();
    if (!promptZh && promptEn) promptZh = translateKnownMissingPrompt(item, promptEn);
    return {
      id: item && item.id,
      title: String((item && (item.title || item.name)) || '未命名示例').trim(),
      prompt: promptZh || prompt || promptEn,
      prompt_en: promptEn,
      prompt_zh: promptZh,
      preview_text: promptZh || prompt || promptEn,
      input_prompt: promptZh || prompt || promptEn,
      cover_image: String((item && (item.cover_image || item.image)) || '').trim(),
      model: String((item && item.model) || 'gpt-image-2').trim() || 'gpt-image-2',
      tags: tags.map(function(tag) { return String(tag || '').trim(); }).filter(Boolean).slice(0, 6)
    };
  }

  async function ensureExampleCatalog() {
    if (state.exampleCatalog.length) {
      return state.exampleCatalog;
    }
    var staticResp = await fetch('/static/data/comfly-image-studio-examples.json', { cache: 'no-store' });
    if (!staticResp.ok) {
      throw new Error('项目内示例文件加载失败');
    }
    var staticPayload = await staticResp.json();
    if (!Array.isArray(staticPayload) || !staticPayload.length) {
      throw new Error('项目内示例文件为空');
    }
    state.exampleCatalog = staticPayload.map(normalizeExample).filter(function(item) {
      return item.prompt && item.title;
    });
    return state.exampleCatalog;
  }

  function exampleMatchesCategory(item, category) {
    if (!category || category === 'all') return true;
    var words = EXAMPLE_CATEGORIES[category] || [];
    if (!words.length) return true;
    var haystack = [
      item.title || '',
      item.prompt || '',
      item.prompt_zh || '',
      (item.tags || []).join(' ')
    ].join(' ');
    return words.some(function(word) {
      return haystack.indexOf(word) >= 0;
    });
  }

  function exampleMatchesSearch(item, query) {
    var q = String(query || '').trim().toLowerCase();
    if (!q) return true;
    var haystack = [
      item.title || '',
      item.prompt || '',
      item.prompt_zh || '',
      item.prompt_en || '',
      (item.tags || []).join(' ')
    ].join(' ').toLowerCase();
    return haystack.indexOf(q) >= 0;
  }

  function filteredExamples(catalog) {
    return (catalog || []).filter(function(item) {
      return exampleMatchesCategory(item, state.exampleCategory) && exampleMatchesSearch(item, state.exampleSearch);
    });
  }

  async function loadExamples(reset) {
    if (state.examplesLoading) return;
    if (reset) {
      state.examples = [];
      state.examplesOffset = 0;
      state.examplesTotal = 0;
      renderExamples();
    }
    state.examplesLoading = true;
    updateExampleStatus();
    try {
      var catalog = filteredExamples(await ensureExampleCatalog());
      var items = catalog.slice(state.examplesOffset, state.examplesOffset + state.examplesLimit);
      state.examples = reset ? items : state.examples.concat(items);
      state.examplesOffset = state.examples.length;
      state.examplesTotal = catalog.length;
      renderExamples();
    } catch (err) {
      if (!state.examples.length) {
        state.examples = FALLBACK_EXAMPLES.slice();
        state.examplesOffset = state.examples.length;
        state.examplesTotal = state.examples.length;
        renderExamples();
      }
      showMessage(err && err.message ? err.message : '加载示例失败，已使用本地备选示例。', false);
    } finally {
      state.examplesLoading = false;
      updateExampleStatus();
    }
  }

  async function generateImage() {
    if (state.submitting) return;
    var prompt = (($('imglabPromptInput') || {}).value || '').trim();
    if (!prompt) {
      showMessage('请先输入提示词', true);
      return;
    }

    // 算力预检查
    var model = $('imglabModelSelect').value;
    var estimatedCredits = 30;
    var userCredits = estimatedCredits * 2;

    try {
      var meResp = await fetch((typeof API_BASE !== 'undefined' ? API_BASE : '') + '/auth/me', {
        headers: (typeof authHeaders === 'function' ? authHeaders() : {})
      });
      if (meResp.ok) {
        var meData = await meResp.json();
        var balance = meData.credits != null ? meData.credits : null;
        if (balance !== null && balance < userCredits) {
          showMessage('算力不足：生成一张图片需要 ' + userCredits + ' 算力，当前余额 ' + balance + ' 算力。请先充值。', true);
          return;
        }
      }
    } catch (err) {
      // 算力检查失败不阻断，继续执行
      console.warn('算力预检查失败:', err);
    }

    setSubmitting(true);
    setRightView('result');
    state.currentJobId = '';
    state.currentJobStatus = '';
    showMessage('正在提交图片生成任务...', false);

    var form = new FormData();
    var finalPrompt = buildPromptWithReferenceHints(prompt);
    form.append('prompt', finalPrompt);
    form.append('model', $('imglabModelSelect').value);
    form.append('aspect_ratio', $('imglabAspectRatioSelect').value);
    form.append('quality', $('imglabQualitySelect').value);
    form.append('background', $('imglabBackgroundSelect').value);
    var referenceImageUrls = [];
    state.references.forEach(function(item) {
      if (item && item.file) form.append('images', item.file);
      else if (item && (item.source_url || item.objectUrl)) referenceImageUrls.push(item.source_url || item.objectUrl);
    });
    if (referenceImageUrls.length) form.append('reference_image_urls', referenceImageUrls.join(','));

    try {
      var resp = await fetch(localBase() + '/api/comfly-image-studio/generate/start', {
        method: 'POST',
        headers: authHeadersSafe(),
        body: form
      });
      var payload = await resp.json().catch(function() { return {}; });
      if (!resp.ok) {
        throw new Error(imageTaskFailureMessage(payload));
      }

      state.currentJobId = payload.job_id || '';
      if (!state.currentJobId) {
        throw new Error(friendlyImageTaskFailureMessage());
      }
      state.currentJobStatus = 'running';
      state.results = [];
      rememberJob({
        jobId: state.currentJobId,
        status: 'running',
        title: prompt.slice(0, 18) || '图片任务'
      });
      renderResultSurface();
      setRightView('result');
      showMessage('图片任务已提交，可以切换页面或继续提交新任务。', false);
      refreshJobStatus(false);
    } catch (err) {
      console.warn('图片任务提交失败', err);
      state.currentJobId = '';
      state.currentJobStatus = '';
      state.currentJobPrompt = '';
      renderResultSurface();
      showMessage(imageTaskFailureMessage({ error: err && err.message }), true);
    } finally {
      setSubmitting(false);
    }
  }

  function buildPromptWithReferenceHints(prompt) {
    var hints = [];
    state.references.forEach(function(item, index) {
      var purpose = item.purpose || 'auto';
      var tpl = PURPOSE_HINTS[purpose] || PURPOSE_HINTS.auto;
      hints.push(tpl.replace(/\{n\}/g, String(index + 1)));
    });
    if (!hints.length) return prompt;
    return hints.join('\n') + '\n\n用户提示词：' + prompt;
  }

  function bindEvents() {
    var uploadBtn = $('imglabReferenceUploadBtn');
    var assetPickBtn = $('imglabAssetPickBtn');
    var input = $('imglabReferenceInput');
    var list = $('imglabReferenceList');
    var gallery = $('imglabResultGallery');
    var surface = $('imglabResultSurface');
    var examples = $('imglabExamplesGrid');
    var resetBtn = $('imglabResetBtn');
    var generateBtn = $('imglabGenerateBtn');
    var backBtn = $('imglabStudioBackBtn');
    var showExamplesBtn = $('imglabShowExamplesBtn');
    var moreBtn = $('imglabExamplesMoreBtn');
    var searchInput = $('imglabExampleSearchInput');
    var purposeSelect = $('imglabReferencePurposeSelect');

    if (uploadBtn && input && !uploadBtn.dataset.bound) {
      uploadBtn.dataset.bound = '1';
      uploadBtn.addEventListener('click', function() {
        input.click();
      });
    }
    if (assetPickBtn && !assetPickBtn.dataset.bound) {
      assetPickBtn.dataset.bound = '1';
      assetPickBtn.addEventListener('click', function() {
        openAssetPicker();
      });
    }
    if (input && !input.dataset.bound) {
      input.dataset.bound = '1';
      input.addEventListener('change', function(event) {
        var files = readFiles(event.target.files);
        state.references = appendReferenceItems(state.references, files);
        input.value = '';
        renderReferenceList();
      });
    }
    if (purposeSelect && !purposeSelect.dataset.bound) {
      purposeSelect.dataset.bound = '1';
      purposeSelect.addEventListener('change', function() {
        applyDefaultReferencePurposeToExisting(purposeSelect.value || 'auto');
      });
    }
    if (list && !list.dataset.bound) {
      list.dataset.bound = '1';
      list.addEventListener('click', function(event) {
        var btn = event.target.closest('.imglab-upload-remove');
        if (btn) {
          removeReference(Number(btn.getAttribute('data-ref-index')) || 0);
          return;
        }
      });
      list.addEventListener('change', function(event) {
        var sel = event.target.closest('.imglab-ref-purpose');
        if (!sel) return;
        updateReferencePurpose(Number(sel.getAttribute('data-ref-purpose-index')) || 0, sel.value);
      });
    }
    if (gallery && !gallery.dataset.bound) {
      gallery.dataset.bound = '1';
      gallery.addEventListener('click', function(event) {
        var btn = event.target.closest('.imglab-result-thumb');
        if (!btn) return;
        state.selectedJobId = '';
        state.activeResultIndex = Number(btn.getAttribute('data-result-index')) || 0;
        renderResultSurface();
      });
    }
    if (surface && !surface.dataset.bound) {
      surface.dataset.bound = '1';
      surface.addEventListener('click', function(event) {
        var copyBtn = event.target.closest('[data-imglab-copy-prompt]');
        if (copyBtn) {
          copyActivePrompt();
          return;
        }
        var downloadBtn = event.target.closest('[data-imglab-download-image]');
        if (downloadBtn) {
          if (downloadBtn.disabled) return;
          downloadActiveImage();
          return;
        }
        var previewBtn = event.target.closest('[data-imglab-open-preview]');
        if (previewBtn) {
          if (previewBtn.disabled) return;
          var detail = activeResultDetail();
          openResultPreviewModal(detail ? (detail.downloadUrl || detail.previewUrl || '') : '', detail ? detail.title : '');
        }
      });
    }
    if (examples && !examples.dataset.bound) {
      examples.dataset.bound = '1';
      examples.addEventListener('click', function(event) {
        var card = event.target.closest('.imglab-example-card');
        if (!card) return;
        var example = state.examples[Number(card.getAttribute('data-example-index')) || 0];
        if (!example || !$('imglabPromptInput')) return;
        $('imglabPromptInput').value = example.input_prompt || example.prompt || '';
        $('imglabPromptInput').focus();
        showMessage('已带入示例提示词，可以继续修改后再生成。', false);
      });
    }
    if (resetBtn && !resetBtn.dataset.bound) {
      resetBtn.dataset.bound = '1';
      resetBtn.addEventListener('click', resetWorkspace);
    }
    if (generateBtn && !generateBtn.dataset.bound) {
      generateBtn.dataset.bound = '1';
      generateBtn.addEventListener('click', generateImage);
    }
    if (backBtn && !backBtn.dataset.bound) {
      backBtn.dataset.bound = '1';
      backBtn.addEventListener('click', function() {
        var btn = document.querySelector('.nav-left-item[data-view="skill-store"]');
        if (btn) btn.click();
      });
    }
    if (showExamplesBtn && !showExamplesBtn.dataset.bound) {
      showExamplesBtn.dataset.bound = '1';
      showExamplesBtn.addEventListener('click', function() {
        setRightView('examples');
        loadExamples(false);
      });
    }
    document.querySelectorAll('[data-imglab-show-result]').forEach(function(btn) {
      if (btn.dataset.bound) return;
      btn.dataset.bound = '1';
      btn.addEventListener('click', function() {
        state.view = 'result';
        setRightView('result');
        renderResultSurface();
      });
    });
    if (moreBtn && !moreBtn.dataset.bound) {
      moreBtn.dataset.bound = '1';
      moreBtn.addEventListener('click', function() {
        loadExamples(false);
      });
    }
    document.querySelectorAll('[data-imglab-category]').forEach(function(btn) {
      if (btn.dataset.bound) return;
      btn.dataset.bound = '1';
      btn.addEventListener('click', function() {
        state.exampleCategory = btn.getAttribute('data-imglab-category') || 'all';
        document.querySelectorAll('[data-imglab-category]').forEach(function(item) {
          item.classList.toggle('active', item === btn);
        });
        loadExamples(true);
      });
    });
    if (searchInput && !searchInput.dataset.bound) {
      searchInput.dataset.bound = '1';
      searchInput.addEventListener('input', function() {
        state.exampleSearch = searchInput.value || '';
        loadExamples(true);
      });
    }
  }

  function renderWorkspace() {
    renderReferenceList();
    renderResultSurface();
    renderExamples();
    setRightView((state.results.length || state.submitting || state.currentJobStatus === 'running') ? state.view : 'examples');
  }

  function init() {
    initImglabCustomSelects();
    bindEvents();
    if (!state.initialized) {
      state.initialized = true;
      loadRecentJobs();
      loadCloudJobHistory();
      loadExamples(true);
      var now = Date.now();
      var active = state.recentJobs.find(function(item) {
        if (!item || item.status !== 'running' || !item.jobId) return false;
        return !item.updatedAt || now - Number(item.updatedAt) <= JOB_RESTORE_WINDOW_MS;
      });
      if (active) {
        state.currentJobId = active.jobId;
        state.currentJobStatus = 'running';
        state.view = 'result';
        refreshJobStatus(false);
      }
    }
    syncImglabCustomSelects();
    renderWorkspace();
  }

  window.initImageComposerStudioView = init;
})();
