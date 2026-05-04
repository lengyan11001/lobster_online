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
    references: [],
    results: [],
    activeResultIndex: 0,
    submitting: false,
    examples: [],
    exampleCatalog: [],
    examplesOffset: 0,
    examplesTotal: 0,
    examplesLoading: false,
    examplesLimit: 24,
    exampleCategory: 'all',
    exampleSearch: ''
  };

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
        '<p class="imglab-example-copy">' + escapeHtml(previewText ? (previewText.slice(0, 120) + (previewText.length > 120 ? '...' : '')) : '暂无提示词预览') + '</p>',
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
    showMessage('正在提交图片生成任务...', false);

    var form = new FormData();
    var finalPrompt = buildPromptWithReferenceHints(prompt);
    form.append('prompt', finalPrompt);
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
    var input = $('imglabReferenceInput');
    var list = $('imglabReferenceList');
    var gallery = $('imglabResultGallery');
    var examples = $('imglabExamplesGrid');
    var resetBtn = $('imglabResetBtn');
    var generateBtn = $('imglabGenerateBtn');
    var backBtn = $('imglabStudioBackBtn');
    var moreBtn = $('imglabExamplesMoreBtn');
    var searchInput = $('imglabExampleSearchInput');
    var purposeSelect = $('imglabReferencePurposeSelect');

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
