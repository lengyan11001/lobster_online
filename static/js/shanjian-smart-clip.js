(function() {
  var SCENES = [
    { value: 'virtualman', label: '数字人' },
    { value: 'realMan', label: '真人口播' },
    { value: 'oralMixCutting', label: '素材混剪' },
    { value: 'newsMixCutting', label: '新闻体' }
  ];

  var PRICE_BY_SCENE = {
    virtualman: '约 60 算力/分钟',
    realMan: '约 15 算力/分钟',
    oralMixCutting: '约 15 算力/分钟',
    newsMixCutting: '约 6 算力/分钟'
  };

  var state = {
    templates: [],
    templateSid: '',
    templateScene: 'realMan',
    selectedTemplate: null,
    templateDetail: null,
    realManVideoUrl: '',
    uploadedMaterials: [],
    virtualmans: [],
    virtualmanSid: '',
    selectedVirtualman: null,
    voices: [],
    selectedVoice: null,
    taskId: '',
    pollTimer: null,
    busy: false
  };

  function $(id) { return document.getElementById(id); }

  function apiBase() {
    return (typeof LOCAL_API_BASE !== 'undefined' ? (LOCAL_API_BASE || '') : '').replace(/\/$/, '');
  }

  function authHeadersSafe() {
    if (typeof authHeaders === 'function') return Object.assign({}, authHeaders() || {});
    return {};
  }

  function escapeHtml(text) {
    return String(text || '').replace(/[&<>"]/g, function(ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch];
    });
  }

  function request(path, body) {
    return fetch(apiBase() + path, {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, authHeadersSafe()),
      body: JSON.stringify(body || {})
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok || data.ok === false) {
          var msg = data.detail || data.error || data.message || '请求失败';
          throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
        }
        return data;
      });
    });
  }

  function showMessage(text, isError) {
    var el = $('sjClipMsg');
    if (!el) return;
    el.textContent = text || '';
    el.style.display = text ? 'block' : 'none';
    el.classList.toggle('err', !!isError);
  }

  function setBusy(flag) {
    state.busy = !!flag;
    ['sjClipSubmitBtn', 'sjClipRefreshTemplatesBtn', 'sjClipLoadMoreTemplatesBtn', 'sjClipRefreshAssetsBtn'].forEach(function(id) {
      var btn = $(id);
      if (btn) btn.disabled = !!flag;
    });
  }

  function placeholder(title) {
    var label = String(title || '模板').slice(0, 6);
    return 'data:image/svg+xml;charset=UTF-8,' + encodeURIComponent(
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 960"><rect width="720" height="960" fill="#eef2ff"/><rect x="72" y="90" width="576" height="780" rx="28" fill="#fff"/><text x="360" y="480" text-anchor="middle" font-size="54" font-family="Arial" fill="#4f46e5">' + escapeHtml(label) + '</text></svg>'
    );
  }

  function currentScene() {
    return SCENES.find(function(item) { return item.value === state.templateScene; }) || SCENES[0];
  }

  function renderSceneTabs() {
    var el = $('sjClipSceneTabs');
    if (!el) return;
    el.innerHTML = SCENES.map(function(item) {
      var active = item.value === state.templateScene;
      return '<button type="button" class="sj-tab' + (active ? ' is-active' : '') + '" data-scene="' + escapeHtml(item.value) + '">' + escapeHtml(item.label) + '</button>';
    }).join('');
    Array.prototype.forEach.call(el.querySelectorAll('[data-scene]'), function(btn) {
      btn.addEventListener('click', function() {
        var scene = btn.getAttribute('data-scene') || 'virtualman';
        if (scene === state.templateScene || state.busy) return;
        state.templateScene = scene;
        state.templateSid = '';
        state.templates = [];
        state.selectedTemplate = null;
        state.templateDetail = null;
        renderSceneTabs();
        renderTemplateDetail();
        loadTemplates(true).catch(function(err) {
          showMessage(err.message || '模板加载失败', true);
        });
      });
    });
    updatePriceText();
  }

  function updatePriceText() {
    var el = $('sjClipSceneCost');
    if (!el) return;
    var scene = currentScene();
    el.textContent = scene.label + '参考消耗：' + (PRICE_BY_SCENE[scene.value] || '以任务返回为准');
  }

  function assetUploadHeaders() {
    var headers = authHeadersSafe();
    delete headers['Content-Type'];
    delete headers['content-type'];
    return headers;
  }

  function uploadAssetFile(file) {
    var fd = new FormData();
    fd.append('file', file);
    return fetch(apiBase() + '/api/assets/upload', {
      method: 'POST',
      headers: assetUploadHeaders(),
      body: fd
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok) {
          var msg = data.detail || data.error || data.message || ('上传失败: HTTP ' + resp.status);
          throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
        }
        if (!data.source_url) throw new Error('上传成功但没有返回可用链接');
        return data;
      });
    });
  }

  function renderUploadState() {
    var real = $('sjClipRealManUploadState');
    if (real) {
      real.textContent = state.realManVideoUrl ? '已上传真人视频' : '未上传';
      real.classList.toggle('is-ok', !!state.realManVideoUrl);
    }
    var list = $('sjClipMaterialUploadList');
    if (list) {
      if (!state.uploadedMaterials.length) {
        list.innerHTML = '<div class="sj-upload-empty">还没有上传素材</div>';
      } else {
        list.innerHTML = state.uploadedMaterials.map(function(item, index) {
          return '<div class="sj-upload-item"><span>' + escapeHtml(item.name || ('素材 ' + (index + 1))) + '</span><button type="button" data-remove-material="' + index + '">移除</button></div>';
        }).join('');
        Array.prototype.forEach.call(list.querySelectorAll('[data-remove-material]'), function(btn) {
          btn.addEventListener('click', function() {
            var idx = Number(btn.getAttribute('data-remove-material'));
            if (!Number.isNaN(idx)) state.uploadedMaterials.splice(idx, 1);
            renderUploadState();
          });
        });
      }
    }
  }

  function setUploadBusy(kind, busy, text) {
    var id = kind === 'realman' ? 'sjClipRealManUploadBtn' : 'sjClipMaterialUploadBtn';
    var btn = $(id);
    if (btn) {
      btn.disabled = !!busy;
      btn.textContent = text || (kind === 'realman' ? '上传真人视频' : '上传素材');
    }
  }

  function templateCard(item) {
    var selected = state.selectedTemplate && state.selectedTemplate.id === item.id;
    return ''
      + '<button type="button" class="sj-card sj-template-card' + (selected ? ' is-selected' : '') + '" data-template-id="' + escapeHtml(item.id) + '" title="双击预览样片">'
      + '<span class="sj-cover"><img src="' + escapeHtml(item.coverUrl || placeholder(item.name)) + '" alt="' + escapeHtml(item.name || '模板') + '" loading="lazy" referrerpolicy="no-referrer"></span>'
      + '<span class="sj-card-body"><strong>' + escapeHtml(item.name || '未命名模板') + '</strong></span>'
      + '</button>';
  }

  function renderTemplates() {
    var grid = $('sjClipTemplateGrid');
    var empty = $('sjClipTemplateEmpty');
    if (!grid) return;
    grid.innerHTML = (state.templates || []).map(templateCard).join('');
    if (empty) empty.style.display = state.templates.length ? 'none' : 'block';
    var more = $('sjClipLoadMoreTemplatesBtn');
    if (more) more.style.display = state.templateSid ? '' : 'none';
    Array.prototype.forEach.call(grid.querySelectorAll('[data-template-id]'), function(btn) {
      btn.addEventListener('click', function() {
        var id = btn.getAttribute('data-template-id') || '';
        var item = state.templates.find(function(row) { return row.id === id; });
        selectTemplate(item);
      });
      btn.addEventListener('dblclick', function(ev) {
        ev.preventDefault();
        var id = btn.getAttribute('data-template-id') || '';
        var item = state.templates.find(function(row) { return row.id === id; });
        openTemplateDemo(item);
      });
    });
  }

  function renderTemplateDetail() {
    var el = $('sjClipSelectedTemplate');
    if (!el) return;
    var item = state.selectedTemplate;
    if (!item) {
      el.innerHTML = '<div class="sj-empty-inline">先选择一个智能剪辑模板</div>';
      return;
    }
    var edit = state.templateDetail && state.templateDetail.videoStructInfo ? state.templateDetail.videoStructInfo.editInfo || {} : {};
    var canvas = edit.canvas || {};
    el.innerHTML = ''
      + '<div class="sj-selected">'
      + '<img src="' + escapeHtml(item.coverUrl || placeholder(item.name)) + '" alt="">'
      + '<div><strong>' + escapeHtml(item.name || '未命名模板') + '</strong>'
      + '<span>' + escapeHtml(currentScene().label) + '</span>'
      + '<span>' + escapeHtml(canvas.width && canvas.height ? canvas.width + ' x ' + canvas.height : '已选择') + '</span>'
      + '</div></div>';
  }

  function assetCard(item, kind) {
    var id = item.id || '';
    var selected = kind === 'virtualman'
      ? (state.selectedVirtualman && state.selectedVirtualman.id === id)
      : (state.selectedVoice && state.selectedVoice.id === id);
    var attrs = kind === 'virtualman' ? 'data-virtualman-id' : 'data-voice-id';
    var media = '<img src="' + escapeHtml(item.coverUrl || placeholder(item.name)) + '" alt="' + escapeHtml(item.name || '') + '" loading="lazy" referrerpolicy="no-referrer">';
    var demo = kind === 'voice' && item.demoUrl ? '<audio controls preload="none" src="' + escapeHtml(item.demoUrl) + '"></audio>' : '';
    var note = [item.gender, item.isGreenBg ? '绿幕' : ''].filter(Boolean).join(' / ');
    return ''
      + '<button type="button" class="sj-card sj-asset-card' + (selected ? ' is-selected' : '') + '" ' + attrs + '="' + escapeHtml(id) + '">'
      + '<span class="sj-cover">' + media + '</span>'
      + '<span class="sj-card-body"><strong>' + escapeHtml(item.name || id) + '</strong><small>' + escapeHtml(note) + '</small>' + demo + '</span>'
      + '</button>';
  }

  function renderAssets() {
    var vmGrid = $('sjClipVirtualmanGrid');
    var voiceGrid = $('sjClipVoiceGrid');
    if (vmGrid) {
      vmGrid.innerHTML = state.virtualmans.map(function(item) { return assetCard(item, 'virtualman'); }).join('');
      Array.prototype.forEach.call(vmGrid.querySelectorAll('[data-virtualman-id]'), function(btn) {
        btn.addEventListener('click', function() {
          var id = btn.getAttribute('data-virtualman-id') || '';
          state.selectedVirtualman = state.virtualmans.find(function(row) { return row.id === id; }) || null;
          renderAssets();
          renderPicked();
        });
      });
    }
    if (voiceGrid) {
      voiceGrid.innerHTML = state.voices.map(function(item) { return assetCard(item, 'voice'); }).join('');
      Array.prototype.forEach.call(voiceGrid.querySelectorAll('[data-voice-id]'), function(btn) {
        btn.addEventListener('click', function() {
          var id = btn.getAttribute('data-voice-id') || '';
          state.selectedVoice = state.voices.find(function(row) { return row.id === id; }) || null;
          renderAssets();
          renderPicked();
        });
      });
    }
    var more = $('sjClipLoadMoreVirtualmansBtn');
    if (more) more.style.display = state.virtualmanSid ? '' : 'none';
  }

  function updateSceneForm() {
    var isVirtualman = state.templateScene === 'virtualman';
    var isRealMan = state.templateScene === 'realMan';
    var isNews = state.templateScene === 'newsMixCutting';
    if ($('sjClipVirtualmanAssetsPanel')) $('sjClipVirtualmanAssetsPanel').style.display = isVirtualman ? '' : 'none';
    if ($('sjClipVoiceAssetsPanel')) $('sjClipVoiceAssetsPanel').style.display = (isRealMan || isNews) ? 'none' : '';
    if ($('sjClipRealManFields')) $('sjClipRealManFields').style.display = isRealMan ? '' : 'none';
    if ($('sjClipTextFields')) $('sjClipTextFields').style.display = (!isRealMan && !isNews && (($('sjClipModeSelect') || {}).value || 'text') === 'text') ? '' : 'none';
    if ($('sjClipAudioFields')) $('sjClipAudioFields').style.display = (!isRealMan && !isNews && (($('sjClipModeSelect') || {}).value || 'text') === 'audio') ? '' : 'none';
    if ($('sjClipModeField')) $('sjClipModeField').style.display = (isRealMan || isNews) ? 'none' : '';
    if ($('sjClipVirtualmanPicked')) $('sjClipVirtualmanPicked').style.display = isVirtualman ? '' : 'none';
    if ($('sjClipVoicePicked')) $('sjClipVoicePicked').style.display = (!isRealMan && !isNews) ? '' : 'none';
  }

  function renderPicked() {
    var el = $('sjClipPickedAssets');
    if (!el) return;
    el.innerHTML = ''
      + '<div id="sjClipVirtualmanPicked"><b>数字人</b><span>' + escapeHtml(state.selectedVirtualman ? state.selectedVirtualman.name + ' / ' + state.selectedVirtualman.id : '未选择') + '</span></div>'
      + '<div id="sjClipVoicePicked"><b>声音</b><span>' + escapeHtml(state.selectedVoice ? state.selectedVoice.name + ' / ' + state.selectedVoice.id : '未选择') + '</span></div>';
    updateSceneForm();
  }

  function loadTemplates(reset) {
    var search = (($('sjClipTemplateSearch') || {}).value || '').trim();
    return request('/api/shanjian-smart-clip/templates', {
      page_size: 30,
      sid: reset ? '' : state.templateSid,
      scene: state.templateScene,
      search_key: search ? 'name' : '',
      search_value: search,
      sort_by: 'desc'
    }).then(function(data) {
      state.templateSid = data.sid || '';
      state.templates = reset ? (data.results || []) : state.templates.concat(data.results || []);
      if (reset) {
        state.selectedTemplate = null;
        state.templateDetail = null;
      }
      if (!state.selectedTemplate && state.templates.length) selectTemplate(state.templates[0]);
      renderTemplates();
      updatePriceText();
      updateSceneForm();
    });
  }

  function selectTemplate(item) {
    state.selectedTemplate = item || null;
    state.templateDetail = null;
    renderTemplates();
    renderTemplateDetail();
    if (!item || !item.id) return;
    request('/api/shanjian-smart-clip/template-detail', { template_id: item.id }).then(function(data) {
      state.templateDetail = data.item || null;
      renderTemplateDetail();
    }).catch(function(err) {
      showMessage(err && err.message ? err.message : '模板详情读取失败', true);
    });
  }

  function loadAssets(resetVirtualman) {
    var vmReq = request('/api/shanjian-smart-clip/virtualmans', {
      page_size: 24,
      sid: resetVirtualman ? '' : state.virtualmanSid
    }).then(function(data) {
      state.virtualmanSid = data.sid || '';
      state.virtualmans = resetVirtualman ? (data.results || []) : state.virtualmans.concat(data.results || []);
      if (!state.selectedVirtualman && state.virtualmans.length) state.selectedVirtualman = state.virtualmans[0];
    });
    var voiceReq = state.voices.length ? Promise.resolve() : request('/api/shanjian-smart-clip/voices', {}).then(function(data) {
      state.voices = data.results || [];
      if (!state.selectedVoice && state.voices.length) state.selectedVoice = state.voices[0];
    });
    return Promise.all([vmReq, voiceReq]).then(function() {
      renderAssets();
      renderPicked();
    });
  }

  function materialRows() {
    return state.uploadedMaterials.map(function(item) {
      return {
        type: item.type || (/\.(mp4|mov|webm|avi|mkv)(\?|$)/i.test(item.url || '') ? 'video' : 'image'),
        fileUrl: item.url
      };
    }).filter(function(item) {
      return item.fileUrl && item.type;
    });
  }

  function submitClip() {
    if (!state.selectedTemplate) return showMessage('请先选择模板。', true);
    var isVirtualman = state.templateScene === 'virtualman';
    var isRealMan = state.templateScene === 'realMan';
    var isNews = state.templateScene === 'newsMixCutting';
    if (isVirtualman && !state.selectedVirtualman) return showMessage('请先选择数字人。', true);
    var mode = (($('sjClipModeSelect') || {}).value || 'text');
    var content = (($('sjClipContentInput') || {}).value || '').trim();
    var audioUrl = (($('sjClipAudioUrlInput') || {}).value || '').trim();
    var videoUrl = state.realManVideoUrl;
    var materials = materialRows();
    if (isRealMan && !videoUrl) return showMessage('请先上传真人视频。', true);
    if (isNews && !materials.length) return showMessage('请先上传素材。', true);
    if (!isRealMan && !isNews && mode === 'text' && !state.selectedVoice) return showMessage('文本生成需要选择声音。', true);
    if (!isRealMan && !isNews && mode === 'text' && !content) return showMessage('请填写剪辑文案。', true);
    if (!isRealMan && !isNews && mode === 'audio' && !audioUrl) return showMessage('请填写音频 URL。', true);

    setBusy(true);
    showMessage('正在提交智能剪辑任务...', false);
    renderResult('任务已提交', '正在创建视频，请稍等。', true);
    request('/api/shanjian-smart-clip/submit', {
      title: (($('sjClipTitleInput') || {}).value || '智能剪辑').trim(),
      scene: state.templateScene,
      style_id: state.selectedTemplate.id,
      virtualman_id: state.selectedVirtualman && state.selectedVirtualman.id,
      video_url: videoUrl,
      speaker_id: state.selectedVoice && state.selectedVoice.id,
      content: !isRealMan && !isNews && mode === 'text' ? content : '',
      audio_url: !isRealMan && !isNews && mode === 'audio' ? audioUrl : '',
      language: (($('sjClipLanguageSelect') || {}).value || 'zh-CN'),
      speed_ratio: Number((($('sjClipSpeedInput') || {}).value || 1)),
      materials: materials,
      header_switch: !!(($('sjClipHeaderSwitch') || {}).checked),
      material_switch: !!(($('sjClipMaterialSwitch') || {}).checked),
      subtitle_switch: !!(($('sjClipSubtitleSwitch') || {}).checked),
      keyword_switch: !!(($('sjClipKeywordSwitch') || {}).checked),
      watermark_show: !!(($('sjClipWatermarkSwitch') || {}).checked)
    }).then(function(data) {
      state.taskId = data.task_id || '';
      showMessage('任务已提交，费用以最终任务返回为准。', false);
      pollTask(true);
    }).catch(function(err) {
      renderResult('提交失败', err && err.message ? err.message : '任务提交失败', false);
      showMessage(err && err.message ? err.message : '任务提交失败', true);
    }).finally(function() {
      setBusy(false);
    });
  }

  function renderResult(title, text, busy) {
    var el = $('sjClipResultSurface');
    if (!el) return;
    el.innerHTML = '<div class="sj-result-placeholder' + (busy ? ' is-busy' : '') + '"><strong>' + escapeHtml(title) + '</strong><span>' + escapeHtml(text || '') + '</span></div>';
  }

  function pollTask(immediate) {
    if (state.pollTimer) clearTimeout(state.pollTimer);
    if (!state.taskId) return;
    state.pollTimer = setTimeout(function() {
      request('/api/shanjian-smart-clip/task', { task_id: state.taskId }).then(function(data) {
        if (data.status === 'succeed') {
          if (data.video_url) {
            $('sjClipResultSurface').innerHTML = '<video controls src="' + escapeHtml(data.video_url) + '"></video>';
          } else {
            renderResult('任务已完成', '没有返回视频地址，请查看任务结果。', false);
          }
          var cost = data.cost_rights && data.cost_rights.credits != null ? '，实际消耗 ' + data.cost_rights.credits + ' 算力' : '';
          showMessage('智能剪辑已完成' + cost + '。', false);
          return;
        }
        if (data.status === 'failed') {
          renderResult('任务失败', data.message || '任务失败', false);
          showMessage(data.message || '任务失败', true);
          return;
        }
        renderResult(data.status_text || '处理中', '任务正在生成，页面会自动刷新结果。', true);
        pollTask(false);
      }).catch(function(err) {
        renderResult('查询失败', err && err.message ? err.message : '任务查询失败', false);
        showMessage(err && err.message ? err.message : '任务查询失败', true);
      });
    }, immediate ? 0 : 8000);
  }

  function syncMode() {
    updateSceneForm();
  }

  function bindUploadEvents() {
    if ($('sjClipRealManUploadBtn')) $('sjClipRealManUploadBtn').addEventListener('click', function() {
      var input = $('sjClipRealManVideoInput');
      if (input) input.click();
    });
    if ($('sjClipRealManVideoInput')) $('sjClipRealManVideoInput').addEventListener('change', function() {
      var file = this.files && this.files[0];
      this.value = '';
      if (!file) return;
      setUploadBusy('realman', true, '上传中...');
      showMessage('正在上传真人视频...', false);
      uploadAssetFile(file).then(function(data) {
        state.realManVideoUrl = data.source_url || '';
        renderUploadState();
        showMessage('真人视频已上传。', false);
      }).catch(function(err) {
        showMessage(err && err.message ? err.message : '真人视频上传失败', true);
      }).finally(function() {
        setUploadBusy('realman', false);
      });
    });
    if ($('sjClipMaterialUploadBtn')) $('sjClipMaterialUploadBtn').addEventListener('click', function() {
      var input = $('sjClipMaterialFilesInput');
      if (input) input.click();
    });
    if ($('sjClipMaterialFilesInput')) $('sjClipMaterialFilesInput').addEventListener('change', function() {
      var files = Array.prototype.slice.call(this.files || []);
      this.value = '';
      if (!files.length) return;
      setUploadBusy('material', true, '上传中...');
      showMessage('正在上传素材...', false);
      files.reduce(function(chain, file) {
        return chain.then(function() {
          return uploadAssetFile(file).then(function(data) {
            state.uploadedMaterials.push({
              name: file.name,
              type: (data.media_type === 'video' || data.media_type === 'image') ? data.media_type : (file.type.indexOf('video/') === 0 ? 'video' : 'image'),
              url: data.source_url
            });
            renderUploadState();
          });
        });
      }, Promise.resolve()).then(function() {
        showMessage('素材已上传。', false);
      }).catch(function(err) {
        showMessage(err && err.message ? err.message : '素材上传失败', true);
      }).finally(function() {
        setUploadBusy('material', false);
      });
    });
  }

  function ensureDemoModal() {
    var modal = $('sjClipDemoModal');
    if (modal) return modal;
    modal = document.createElement('div');
    modal.id = 'sjClipDemoModal';
    modal.className = 'sj-demo-modal';
    modal.innerHTML = ''
      + '<div class="sj-demo-backdrop" data-close-demo="1"></div>'
      + '<div class="sj-demo-card" role="dialog" aria-modal="true">'
      + '<div class="sj-demo-head"><strong id="sjClipDemoTitle">样片预览</strong><button type="button" class="sj-demo-close" data-close-demo="1" aria-label="关闭">×</button></div>'
      + '<video id="sjClipDemoVideo" class="sj-demo-video" controls playsinline></video>'
      + '</div>';
    document.body.appendChild(modal);
    Array.prototype.forEach.call(modal.querySelectorAll('[data-close-demo]'), function(el) {
      el.addEventListener('click', closeTemplateDemo);
    });
    document.addEventListener('keydown', function(ev) {
      if (ev.key === 'Escape') closeTemplateDemo();
    });
    return modal;
  }

  function openTemplateDemo(item) {
    if (!item || !item.demoUrl) {
      showMessage('这个模板暂时没有样片。', false);
      return;
    }
    var modal = ensureDemoModal();
    var video = $('sjClipDemoVideo');
    var title = $('sjClipDemoTitle');
    if (title) title.textContent = item.name || '样片预览';
    if (video) {
      video.pause();
      video.removeAttribute('src');
      video.load();
      video.src = item.demoUrl;
      video.play().catch(function() {});
    }
    modal.classList.add('is-open');
  }

  function closeTemplateDemo() {
    var modal = $('sjClipDemoModal');
    var video = $('sjClipDemoVideo');
    if (video) {
      video.pause();
      video.removeAttribute('src');
      video.load();
    }
    if (modal) modal.classList.remove('is-open');
  }

  function bindEvents() {
    if ($('sjClipBackBtn')) $('sjClipBackBtn').addEventListener('click', function() {
      if (typeof window._ensureSkillStoreVisible === 'function') window._ensureSkillStoreVisible();
      try { location.hash = 'skill-store'; } catch (e) {}
    });
    if ($('sjClipRefreshTemplatesBtn')) $('sjClipRefreshTemplatesBtn').addEventListener('click', function() {
      loadTemplates(true).catch(function(err) { showMessage(err.message || '模板加载失败', true); });
    });
    if ($('sjClipLoadMoreTemplatesBtn')) $('sjClipLoadMoreTemplatesBtn').addEventListener('click', function() {
      loadTemplates(false).catch(function(err) { showMessage(err.message || '模板加载失败', true); });
    });
    if ($('sjClipTemplateSearch')) $('sjClipTemplateSearch').addEventListener('keydown', function(ev) {
      if (ev.key === 'Enter') {
        loadTemplates(true).catch(function(err) { showMessage(err.message || '模板加载失败', true); });
      }
    });
    if ($('sjClipRefreshAssetsBtn')) $('sjClipRefreshAssetsBtn').addEventListener('click', function() {
      state.voices = [];
      loadAssets(true).catch(function(err) { showMessage(err.message || '资源加载失败', true); });
    });
    if ($('sjClipLoadMoreVirtualmansBtn')) $('sjClipLoadMoreVirtualmansBtn').addEventListener('click', function() {
      loadAssets(false).catch(function(err) { showMessage(err.message || '数字人加载失败', true); });
    });
    if ($('sjClipSubmitBtn')) $('sjClipSubmitBtn').addEventListener('click', submitClip);
    if ($('sjClipModeSelect')) $('sjClipModeSelect').addEventListener('change', syncMode);
    bindUploadEvents();
  }

  function buildTemplate() {
    return ''
      + '<div class="sj-clip-page">'
      + '<div class="sj-hero"><div><h3>智能剪辑</h3><p>选择模板、人物和声音，生成剪辑视频。</p><div id="sjClipSceneCost" class="sj-price"></div></div><button id="sjClipBackBtn" class="btn btn-ghost btn-sm" type="button">返回技能商店</button></div>'
      + '<div class="sj-layout">'
      + '<aside class="sj-side">'
      + '<div class="sj-panel"><h4>提交参数</h4><div class="sj-field"><label>标题</label><input id="sjClipTitleInput" type="text" maxlength="80" placeholder="智能剪辑"></div><div id="sjClipModeField" class="sj-field"><label>生成方式</label><select id="sjClipModeSelect"><option value="text">文本 + 声音</option><option value="audio">音频 URL</option></select></div><div id="sjClipTextFields"><div class="sj-field"><label>文案</label><textarea id="sjClipContentInput" placeholder="输入 3 到 1800 字剪辑文案"></textarea></div><div class="sj-inline"><div class="sj-field"><label>语种</label><select id="sjClipLanguageSelect"><option value="zh-CN">中文</option><option value="en-US">英语</option></select></div><div class="sj-field"><label>语速</label><input id="sjClipSpeedInput" type="number" min="0.5" max="2" step="0.1" value="1"></div></div></div><div id="sjClipAudioFields" style="display:none;"><div class="sj-field"><label>音频 URL</label><input id="sjClipAudioUrlInput" type="url" placeholder="https://...mp3"></div></div><div id="sjClipRealManFields" style="display:none;"><div class="sj-field"><label>真人视频</label><input id="sjClipRealManVideoInput" type="file" accept="video/*" hidden><div class="sj-upload-row"><button id="sjClipRealManUploadBtn" class="btn btn-ghost btn-sm" type="button">上传真人视频</button><span id="sjClipRealManUploadState" class="sj-upload-state">未上传</span></div></div></div><div class="sj-field"><label>素材</label><input id="sjClipMaterialFilesInput" type="file" accept="image/*,video/*" multiple hidden><div class="sj-upload-row"><button id="sjClipMaterialUploadBtn" class="btn btn-ghost btn-sm" type="button">上传素材</button><span class="sj-upload-state">图片或视频，可多选</span></div><div id="sjClipMaterialUploadList" class="sj-upload-list"></div></div><div class="sj-switches"><label><input id="sjClipHeaderSwitch" type="checkbox" checked>标题包装</label><label><input id="sjClipMaterialSwitch" type="checkbox" checked>素材包装</label><label><input id="sjClipSubtitleSwitch" type="checkbox" checked>字幕包装</label><label><input id="sjClipKeywordSwitch" type="checkbox" checked>关键词包装</label><label><input id="sjClipWatermarkSwitch" type="checkbox" checked>AI 水印</label></div><div id="sjClipPickedAssets" class="sj-picked"></div><div id="sjClipMsg" class="msg" style="display:none;margin-top:0.75rem;"></div><button id="sjClipSubmitBtn" type="button" class="btn btn-primary">提交智能剪辑</button></div>'
      + '</aside>'
      + '<main class="sj-main">'
      + '<section class="sj-panel"><div class="sj-toolbar"><div><h4>模板列表</h4><p>双击模板卡片可预览样片。</p></div><div><input id="sjClipTemplateSearch" type="text" placeholder="搜索模板名称"><button id="sjClipRefreshTemplatesBtn" class="btn btn-ghost btn-sm" type="button">刷新模板</button></div></div><div id="sjClipSceneTabs" class="sj-tabs"></div><div id="sjClipSelectedTemplate"></div><div id="sjClipTemplateGrid" class="sj-grid sj-template-grid"></div><div id="sjClipTemplateEmpty" class="sj-empty" style="display:none;">暂无模板</div><button id="sjClipLoadMoreTemplatesBtn" class="btn btn-ghost btn-sm" type="button" style="display:none;">加载更多模板</button></section>'
      + '<section class="sj-two"><div id="sjClipVirtualmanAssetsPanel" class="sj-panel"><div class="sj-toolbar"><div><h4>数字人</h4><p>选择用于出镜的数字人。</p></div><button id="sjClipRefreshAssetsBtn" class="btn btn-ghost btn-sm" type="button">刷新资源</button></div><div id="sjClipVirtualmanGrid" class="sj-grid"></div><button id="sjClipLoadMoreVirtualmansBtn" class="btn btn-ghost btn-sm" type="button" style="display:none;">加载更多数字人</button></div><div id="sjClipVoiceAssetsPanel" class="sj-panel"><h4>声音</h4><p>选择用于文本生成的声音。</p><div id="sjClipVoiceGrid" class="sj-grid"></div></div></section>'
      + '<section class="sj-panel"><h4>任务结果</h4><div id="sjClipResultSurface" class="sj-result"></div></section>'
      + '</main></div></div>';
  }

  function ensureStyles() {
    if ($('sjClipStyle')) return;
    var style = document.createElement('style');
    style.id = 'sjClipStyle';
    style.textContent =
      '#content-shanjian-smart-clip{height:100%;overflow-y:auto;}'
      + '#content-shanjian-smart-clip .sj-clip-page{display:flex;flex-direction:column;gap:1rem;padding:1rem;}'
      + '#content-shanjian-smart-clip .sj-hero{display:flex;justify-content:space-between;gap:1rem;align-items:flex-start;padding:1.1rem 1.25rem;border:1px solid rgba(15,23,42,0.08);border-radius:8px;background:#fff;}'
      + '#content-shanjian-smart-clip .sj-hero h3{margin:0;font-size:1.45rem;color:#111827;}'
      + '#content-shanjian-smart-clip .sj-hero p{margin:0.3rem 0;color:#475569;}'
      + '#content-shanjian-smart-clip .sj-price{display:inline-flex;margin-top:0.25rem;font-size:0.84rem;color:#2563eb;background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:0.5rem 0.65rem;}'
      + '#content-shanjian-smart-clip .sj-layout{display:grid;grid-template-columns:minmax(300px,360px) minmax(0,1fr);gap:1rem;align-items:start;}'
      + '#content-shanjian-smart-clip .sj-side{position:sticky;top:0.8rem;}'
      + '#content-shanjian-smart-clip .sj-panel{background:#fff;border:1px solid rgba(15,23,42,0.08);border-radius:8px;padding:1rem;box-shadow:0 10px 28px rgba(15,23,42,0.05);}'
      + '#content-shanjian-smart-clip .sj-panel h4{margin:0 0 0.35rem;color:#111827;}'
      + '#content-shanjian-smart-clip .sj-panel p{margin:0 0 0.7rem;color:#64748b;font-size:0.86rem;line-height:1.55;}'
      + '#content-shanjian-smart-clip .sj-field{display:flex;flex-direction:column;gap:0.35rem;margin-bottom:0.75rem;}'
      + '#content-shanjian-smart-clip label{font-size:0.82rem;font-weight:700;color:#334155;}'
      + '#content-shanjian-smart-clip input,#content-shanjian-smart-clip select,#content-shanjian-smart-clip textarea{width:100%;border:1px solid rgba(15,23,42,0.12);border-radius:8px;padding:0.62rem 0.7rem;background:#fff;color:#111827;font:inherit;}'
      + '#content-shanjian-smart-clip textarea{min-height:120px;resize:vertical;}'
      + '#content-shanjian-smart-clip .sj-inline{display:grid;grid-template-columns:1fr 100px;gap:0.7rem;}'
      + '#content-shanjian-smart-clip .sj-switches{display:grid;grid-template-columns:1fr 1fr;gap:0.45rem;margin:0.4rem 0 0.8rem;}'
      + '#content-shanjian-smart-clip .sj-switches label{display:flex;align-items:center;gap:0.4rem;font-weight:600;}'
      + '#content-shanjian-smart-clip .sj-switches input{width:auto;}'
      + '#content-shanjian-smart-clip .sj-picked{display:grid;gap:0.45rem;margin:0.65rem 0;padding:0.65rem;border-radius:8px;background:#f8fafc;}'
      + '#content-shanjian-smart-clip .sj-picked div{display:flex;justify-content:space-between;gap:0.75rem;font-size:0.82rem;color:#475569;}'
      + '#content-shanjian-smart-clip .sj-picked span{word-break:break-all;text-align:right;}'
      + '#content-shanjian-smart-clip .sj-upload-row{display:flex;align-items:center;gap:0.65rem;flex-wrap:wrap;}'
      + '#content-shanjian-smart-clip .sj-upload-state{font-size:0.82rem;color:#64748b;}'
      + '#content-shanjian-smart-clip .sj-upload-state.is-ok{color:#047857;font-weight:700;}'
      + '#content-shanjian-smart-clip .sj-upload-list{display:grid;gap:0.4rem;margin-top:0.55rem;}'
      + '#content-shanjian-smart-clip .sj-upload-empty{font-size:0.82rem;color:#94a3b8;background:#f8fafc;border-radius:8px;padding:0.55rem;}'
      + '#content-shanjian-smart-clip .sj-upload-item{display:flex;align-items:center;justify-content:space-between;gap:0.55rem;background:#f8fafc;border-radius:8px;padding:0.5rem 0.55rem;font-size:0.82rem;color:#334155;}'
      + '#content-shanjian-smart-clip .sj-upload-item span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}'
      + '#content-shanjian-smart-clip .sj-upload-item button{border:0;background:transparent;color:#dc2626;cursor:pointer;font:inherit;}'
      + '#content-shanjian-smart-clip .sj-main{display:flex;flex-direction:column;gap:1rem;min-width:0;}'
      + '#content-shanjian-smart-clip .sj-toolbar{display:flex;justify-content:space-between;gap:1rem;align-items:flex-start;margin-bottom:0.75rem;}'
      + '#content-shanjian-smart-clip .sj-toolbar>div:last-child{display:flex;gap:0.5rem;align-items:center;}'
      + '#content-shanjian-smart-clip .sj-tabs{display:flex;flex-wrap:wrap;gap:0.5rem;margin:0 0 0.75rem;}'
      + '#content-shanjian-smart-clip .sj-tab{border:1px solid rgba(15,23,42,0.12);background:#fff;color:#334155;border-radius:8px;padding:0.5rem 0.75rem;cursor:pointer;font-weight:700;}'
      + '#content-shanjian-smart-clip .sj-tab:hover,#content-shanjian-smart-clip .sj-tab.is-active{border-color:#2563eb;background:#eff6ff;color:#1d4ed8;}'
      + '#content-shanjian-smart-clip .sj-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:0.75rem;}'
      + '#content-shanjian-smart-clip .sj-template-grid{grid-template-columns:repeat(auto-fill,minmax(170px,1fr));}'
      + '#content-shanjian-smart-clip .sj-card{display:flex;flex-direction:column;text-align:left;border:1px solid rgba(15,23,42,0.1);border-radius:8px;background:#fff;padding:0;overflow:hidden;cursor:pointer;min-width:0;}'
      + '#content-shanjian-smart-clip .sj-card:hover,#content-shanjian-smart-clip .sj-card.is-selected{border-color:#2563eb;box-shadow:0 10px 24px rgba(37,99,235,0.12);}'
      + '#content-shanjian-smart-clip .sj-cover{display:block;aspect-ratio:3/4;background:#eef2ff;overflow:hidden;}'
      + '#content-shanjian-smart-clip .sj-cover img{width:100%;height:100%;object-fit:cover;display:block;}'
      + '#content-shanjian-smart-clip .sj-card-body{display:flex;flex-direction:column;gap:0.25rem;padding:0.65rem;min-width:0;}'
      + '#content-shanjian-smart-clip .sj-card-body strong{color:#111827;font-size:0.9rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}'
      + '#content-shanjian-smart-clip .sj-card-body small{color:#64748b;min-height:1em;}'
      + '#content-shanjian-smart-clip .sj-card audio{width:100%;height:32px;}'
      + '#content-shanjian-smart-clip .sj-selected{display:grid;grid-template-columns:72px minmax(0,1fr);gap:0.7rem;align-items:center;padding:0.65rem;background:#f8fafc;border-radius:8px;margin-bottom:0.75rem;}'
      + '#content-shanjian-smart-clip .sj-selected img{width:72px;height:96px;border-radius:8px;object-fit:cover;}'
      + '#content-shanjian-smart-clip .sj-selected div{display:flex;flex-direction:column;gap:0.24rem;min-width:0;}'
      + '#content-shanjian-smart-clip .sj-selected span{font-size:0.8rem;color:#64748b;word-break:break-all;}'
      + '#content-shanjian-smart-clip .sj-two{display:grid;grid-template-columns:1fr 1fr;gap:1rem;}'
      + '#content-shanjian-smart-clip .sj-result{min-height:360px;border-radius:8px;background:#0f172a;display:flex;align-items:center;justify-content:center;overflow:hidden;}'
      + '#content-shanjian-smart-clip .sj-result video{width:100%;height:100%;max-height:680px;object-fit:contain;background:#000;}'
      + '#content-shanjian-smart-clip .sj-result-placeholder{display:flex;flex-direction:column;gap:0.45rem;text-align:center;color:#cbd5e1;padding:1rem;}'
      + '#content-shanjian-smart-clip .sj-result-placeholder strong{color:#fff;font-size:1.05rem;}'
      + '#content-shanjian-smart-clip .sj-empty,#content-shanjian-smart-clip .sj-empty-inline{padding:1rem;color:#64748b;background:#f8fafc;border-radius:8px;}'
      + '.sj-demo-modal{position:fixed;inset:0;z-index:9999;display:none;align-items:center;justify-content:center;padding:1rem;}'
      + '.sj-demo-modal.is-open{display:flex;}'
      + '.sj-demo-backdrop{position:absolute;inset:0;background:rgba(15,23,42,0.58);}'
      + '.sj-demo-card{position:relative;width:min(920px,92vw);background:#0f172a;border-radius:8px;overflow:hidden;box-shadow:0 24px 80px rgba(15,23,42,0.38);}'
      + '.sj-demo-head{display:flex;justify-content:space-between;align-items:center;gap:1rem;padding:0.75rem 0.9rem;color:#fff;background:#111827;}'
      + '.sj-demo-close{width:32px;height:32px;border:0;border-radius:8px;background:rgba(255,255,255,0.12);color:#fff;font-size:24px;line-height:1;cursor:pointer;}'
      + '.sj-demo-video{display:block;width:100%;max-height:78vh;background:#000;}'
      + '@media (max-width:1100px){#content-shanjian-smart-clip .sj-layout,#content-shanjian-smart-clip .sj-two{grid-template-columns:1fr;}#content-shanjian-smart-clip .sj-side{position:static;}#content-shanjian-smart-clip .sj-toolbar{flex-direction:column;}#content-shanjian-smart-clip .sj-toolbar>div:last-child{width:100%;}#content-shanjian-smart-clip .sj-toolbar input{min-width:0;}}';
    document.head.appendChild(style);
  }

  window.initShanjianSmartClipView = function() {
    var root = $('content-shanjian-smart-clip');
    if (!root) return;
    ensureStyles();
    if (root.getAttribute('data-sj-init') !== '1') {
      root.innerHTML = buildTemplate();
      root.setAttribute('data-sj-init', '1');
      bindEvents();
      renderSceneTabs();
      renderTemplateDetail();
      renderPicked();
      renderUploadState();
      renderResult('等待提交', '选择模板、数字人和声音后提交任务。', false);
      syncMode();
    } else {
      renderSceneTabs();
    }
    Promise.allSettled([loadTemplates(true), loadAssets(true)]).then(function() {
      showMessage('资源已加载。', false);
    });
  };

  if (location.hash === '#shanjian-smart-clip') {
    setTimeout(function() {
      if (typeof window.initShanjianSmartClipView === 'function') window.initShanjianSmartClipView();
    }, 0);
  }
})();
