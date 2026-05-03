(function() {
  var FALLBACK_EXAMPLES = [
    {
      id: 1050,
      title: '3D 彩墙人物',
      prompt: 'A stylized 3D animated young woman leaning against a textured abstract wall made of layered cracked paint panels in warm yellow, coral, pink and muted purple gradients. Soft cinematic lighting, warm pastel palette, painterly textures, dreamy atmosphere, Pixar-style 3D illustration, ultra detailed.',
      cover_image: 'https://raw.githubusercontent.com/songguoxs/gpt4o-image-prompts/master/images/1050.jpeg',
      model: 'gpt-image-2',
      tags: ['3D', '插画', '暖色']
    },
    {
      id: 1049,
      title: '角色设定草图',
      prompt: 'Character sheet sketch of a subject, featuring multiple angles and expressive facial variations, drawn in pencil and ballpoint pen on a clean white background. Soft pastel palette, sharp linework, hand-drawn manga style, clear design-sheet composition.',
      cover_image: 'https://raw.githubusercontent.com/songguoxs/gpt4o-image-prompts/master/images/1049.jpeg',
      model: 'gpt-image-2',
      tags: ['角色', '线稿', '设定']
    }
  ];

  var state = {
    initialized: false,
    references: [],
    results: [],
    activeResultIndex: 0,
    submitting: false,
    examples: [],
    exampleCatalog: [],
    examplesOffset: 0,
    examplesTotal: 0,
    examplesLoading: false,
    examplesLimit: 24
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

  function readFiles(fileList) {
    return Array.prototype.slice.call(fileList || []).map(function(file) {
      return {
        file: file,
        name: file.name,
        size: file.size,
        objectUrl: URL.createObjectURL(file)
      };
    });
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
        '</div>',
        '</div>'
      ].join('');
    }).join('');
  }

  function currentResult() {
    if (!state.results.length) return null;
    return state.results[state.activeResultIndex] || state.results[0] || null;
  }

  function resultPreviewUrl(item) {
    if (!item) return '';
    return item.data_url || item.url || '';
  }

  function renderResultSurface() {
    var surface = $('imglabResultSurface');
    var meta = $('imglabResultMeta');
    var gallery = $('imglabResultGallery');
    if (!surface || !meta || !gallery) return;

    var active = currentResult();
    if (!active) {
      surface.innerHTML = [
        '<div class="imglab-result-placeholder">',
        '<strong>结果展示区</strong>',
        '<span>左侧填好提示词后直接生成。上传参考图时会合成，不上传时就是纯文生图。</span>',
        '</div>'
      ].join('');
      meta.innerHTML = '';
      gallery.innerHTML = '';
      return;
    }

    surface.innerHTML = '<img src="' + escapeHtml(resultPreviewUrl(active)) + '" alt="生成结果">';
    meta.innerHTML = [
      '<div class="imglab-result-pills">',
      '<span class="imglab-result-pill">第 ' + (state.activeResultIndex + 1) + ' 张结果</span>',
      active.model ? '<span class="imglab-result-pill">' + escapeHtml(active.model) + '</span>' : '',
      active.aspectRatio ? '<span class="imglab-result-pill">' + escapeHtml(active.aspectRatio) + '</span>' : '',
      active.size ? '<span class="imglab-result-pill">' + escapeHtml(active.size) + '</span>' : '',
      '</div>',
      active.url ? '<a class="btn btn-ghost btn-sm" href="' + escapeHtml(active.url) + '" target="_blank" rel="noopener">打开原图</a>' : '<span></span>'
    ].join('');

    gallery.innerHTML = state.results.map(function(item, index) {
      return [
        '<button type="button" class="imglab-result-thumb' + (index === state.activeResultIndex ? ' is-active' : '') + '" data-result-index="' + index + '">',
        '<img src="' + escapeHtml(resultPreviewUrl(item)) + '" alt="结果缩略图 ' + (index + 1) + '">',
        '</button>'
      ].join('');
    }).join('');
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
        '<p class="imglab-example-copy">' + escapeHtml(previewText ? (previewText.slice(0, 120) + (previewText.length > 120 ? '...' : '')) : 'No prompt preview') + '</p>',
        '<div class="imglab-example-tag-row">',
        tags.slice(0, 4).map(function(tag) {
          return '<span class="imglab-example-tag">' + escapeHtml(tag) + '</span>';
        }).join(''),
        (item.model ? '<span class="imglab-example-tag">' + escapeHtml(item.model) + '</span>' : ''),
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

  function resetWorkspace() {
    releaseItems(state.references);
    state.references = [];
    state.results = [];
    state.activeResultIndex = 0;
    state.submitting = false;
    if ($('imglabPromptInput')) $('imglabPromptInput').value = '';
    if ($('imglabAspectRatioSelect')) $('imglabAspectRatioSelect').value = '1:1';
    if ($('imglabModelSelect')) $('imglabModelSelect').value = 'gpt-image-2';
    if ($('imglabQualitySelect')) $('imglabQualitySelect').value = 'high';
    if ($('imglabBackgroundSelect')) $('imglabBackgroundSelect').value = 'auto';
    if ($('imglabReferenceInput')) $('imglabReferenceInput').value = '';
    showMessage('');
    renderReferenceList();
    renderResultSurface();
  }

  function setSubmitting(submitting) {
    state.submitting = !!submitting;
    var btn = $('imglabGenerateBtn');
    if (!btn) return;
    btn.disabled = !!submitting;
    btn.textContent = submitting ? '生成中...' : '开始生成图片';
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

  function normalizeExample(item) {
    var tags = Array.isArray(item && item.tags) ? item.tags : [];
    var prompt = String((item && item.prompt) || '').trim();
    var promptEn = String((item && item.prompt_en) || '').trim();
    var promptZh = String((item && item.prompt_zh) || '').trim();
    return {
      id: item && item.id,
      title: String((item && (item.title || item.name)) || '未命名示例').trim(),
      prompt: prompt || promptEn || promptZh,
      prompt_en: promptEn,
      prompt_zh: promptZh,
      preview_text: promptZh || promptEn || prompt,
      input_prompt: promptEn || promptZh || prompt,
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
      var catalog = await ensureExampleCatalog();
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
    showMessage('正在提交图片生成任务...', false);

    var form = new FormData();
    form.append('prompt', prompt);
    form.append('model', $('imglabModelSelect').value);
    form.append('aspect_ratio', $('imglabAspectRatioSelect').value);
    form.append('quality', $('imglabQualitySelect').value);
    form.append('background', $('imglabBackgroundSelect').value);
    state.references.forEach(function(item) {
      if (item && item.file) form.append('images', item.file);
    });

    try {
      var resp = await fetch((typeof LOCAL_API_BASE !== 'undefined' ? (LOCAL_API_BASE || '') : '') + '/api/comfly-image-studio/generate', {
        method: 'POST',
        headers: authHeadersSafe(),
        body: form
      });
      var payload = await resp.json().catch(function() { return {}; });
      if (!resp.ok) {
        throw new Error(payload.detail || payload.error || '图片生成失败');
      }

      state.results = (payload.images || []).map(function(item) {
        return {
          url: item.url || '',
          data_url: item.data_url || '',
          model: payload.meta && payload.meta.model,
          aspectRatio: payload.meta && payload.meta.aspect_ratio,
          size: payload.meta && payload.meta.size
        };
      });
      state.activeResultIndex = 0;
      renderResultSurface();
      showMessage('图片已生成，右侧可以直接查看结果。', false);
    } catch (err) {
      showMessage(err && err.message ? err.message : '图片生成失败，请稍后重试', true);
    } finally {
      setSubmitting(false);
    }
  }

  function bindEvents() {
    var uploadBtn = $('imglabReferenceUploadBtn');
    var input = $('imglabReferenceInput');
    var list = $('imglabReferenceList');
    var gallery = $('imglabResultGallery');
    var examples = $('imglabExamplesGrid');
    var resetBtn = $('imglabResetBtn');
    var generateBtn = $('imglabGenerateBtn');
    var backBtn = $('imglabStudioBackBtn');
    var moreBtn = $('imglabExamplesMoreBtn');

    if (uploadBtn && input && !uploadBtn.dataset.bound) {
      uploadBtn.dataset.bound = '1';
      uploadBtn.addEventListener('click', function() {
        input.click();
      });
    }
    if (input && !input.dataset.bound) {
      input.dataset.bound = '1';
      input.addEventListener('change', function(event) {
        var files = readFiles(event.target.files);
        state.references = state.references.concat(files);
        input.value = '';
        renderReferenceList();
      });
    }
    if (list && !list.dataset.bound) {
      list.dataset.bound = '1';
      list.addEventListener('click', function(event) {
        var btn = event.target.closest('.imglab-upload-remove');
        if (!btn) return;
        removeReference(Number(btn.getAttribute('data-ref-index')) || 0);
      });
    }
    if (gallery && !gallery.dataset.bound) {
      gallery.dataset.bound = '1';
      gallery.addEventListener('click', function(event) {
        var btn = event.target.closest('.imglab-result-thumb');
        if (!btn) return;
        state.activeResultIndex = Number(btn.getAttribute('data-result-index')) || 0;
        renderResultSurface();
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
    if (moreBtn && !moreBtn.dataset.bound) {
      moreBtn.dataset.bound = '1';
      moreBtn.addEventListener('click', function() {
        loadExamples(false);
      });
    }
  }

  function renderWorkspace() {
    renderReferenceList();
    renderResultSurface();
    renderExamples();
  }

  function init() {
    bindEvents();
    if (!state.initialized) {
      state.initialized = true;
      loadExamples(true);
    }
    renderWorkspace();
  }

  window.initImageComposerStudioView = init;
})();
