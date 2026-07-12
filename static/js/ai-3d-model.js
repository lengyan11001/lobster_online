(function() {
  var state = {
    jobId: '',
    pollTimer: null,
    configured: false,
    jobs: [],
    jobsLoadSeq: 0,
    jobPage: 1,
    jobPageSize: 10,
    previewModelByJob: {},
    infoBubblePinned: false,
    createFiles: []
  };
  var LAST_JOB_KEY = 'lobster.ai3d.lastJobId';

  function base() {
    return (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE ? String(LOCAL_API_BASE) : '').replace(/\/$/, '');
  }

  function api(path) {
    return base() + path;
  }

  function jobAssetCacheKey(job, file) {
    var outputs = job && job.outputs ? job.outputs : {};
    var assembly = outputs.assembly && typeof outputs.assembly === 'object' ? outputs.assembly : {};
    var plan = assembly.plan && typeof assembly.plan === 'object' ? assembly.plan : {};
    return [
      job && (job.updated_at || job.stage || job.status || ''),
      plan.version || '',
      plan.part_count || '',
      plan.skipped_part_count || '',
      assembly.status || '',
      file && (file.size || file.filename || '')
    ].join('|');
  }

  function assetUrl(path, job, file) {
    if (!path) return '';
    var full = api(path);
    var key = jobAssetCacheKey(job || {}, file || {});
    if (!key.replace(/\|/g, '')) return full;
    return full + (full.indexOf('?') >= 0 ? '&' : '?') + 'ai3d_cache=' + encodeURIComponent(key);
  }

  function headers(extra) {
    return Object.assign({}, typeof authHeaders === 'function' ? authHeaders() : {}, extra || {});
  }

  function formHeaders(extra) {
    var h = Object.assign({}, typeof authHeaders === 'function' ? authHeaders() : {}, extra || {});
    delete h['Content-Type'];
    delete h['content-type'];
    return h;
  }

  function el(id) {
    return document.getElementById(id);
  }

  function ensureCss() {
    if (document.getElementById('ai3dModelCss')) return;
    var link = document.createElement('link');
    link.id = 'ai3dModelCss';
    link.rel = 'stylesheet';
    link.href = '/static/css/ai-3d-model.css?v=20260710-resumable-component-records-v1';
    document.head.appendChild(link);
  }

  function ensureModelViewer() {
    if (window.customElements && customElements.get('model-viewer')) return;
    if (document.getElementById('ai3dModelViewerScript')) return;
    var script = document.createElement('script');
    script.id = 'ai3dModelViewerScript';
    script.src = '/static/vendor/model-viewer/model-viewer-umd.min.js?v=20260625-ai3d-viewer';
    document.head.appendChild(script);
  }

  function refreshStaticCopy() {
    var root = el('content-ai-3d-model');
    if (!root) return;
    var subtitle = root.querySelector('.ai3d-subtitle');
    if (subtitle) {
      subtitle.textContent = '按任务类型上传参考图、实物多角度图或已有多视角图；也可只填资产提示词；确认后生成 3D 模型。';
    }
    var imageLabel = root.querySelector('label[for="ai3dImageModel"]');
    if (imageLabel) imageLabel.textContent = '前置图片模型';
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

  function setMsg(text, isErr) {
    var node = el('ai3dMsg');
    if (!node) return;
    node.textContent = text || '';
    node.className = 'msg' + (isErr ? ' err' : '');
    node.style.display = text ? 'block' : 'none';
  }

  function setMsgHtml(html, isErr) {
    var node = el('ai3dMsg');
    if (!node) return;
    node.innerHTML = html || '';
    node.className = 'msg' + (isErr ? ' err' : '');
    node.style.display = html ? 'block' : 'none';
  }

  function rememberJob(jobId) {
    state.jobId = jobId || '';
    try {
      if (state.jobId) localStorage.setItem(LAST_JOB_KEY, state.jobId);
    } catch (e) {}
  }

  function rememberedJob() {
    try {
      var params = new URLSearchParams(window.location.search || '');
      var directJob = (params.get('job') || params.get('job_id') || '').trim();
      if (directJob) return directJob;
    } catch (e) {}
    try { return localStorage.getItem(LAST_JOB_KEY) || ''; } catch (e) { return ''; }
  }

  function setBusy(btn, busy, text) {
    if (!btn) return;
    if (busy) {
      btn.dataset.oldText = btn.textContent || '';
      btn.textContent = text || '处理中...';
      btn.disabled = true;
    } else {
      btn.textContent = btn.dataset.oldText || btn.textContent || '';
      btn.disabled = false;
    }
  }

  function formatSize(size) {
    var n = Number(size || 0);
    if (!n) return '';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1024 / 1024).toFixed(2) + ' MB';
  }

  var localPreviewUrls = typeof WeakMap !== 'undefined' ? new WeakMap() : null;

  function localFileFromItem(item) {
    return item && item.file ? item.file : item;
  }

  function localFilePreviewUrl(item) {
    var file = localFileFromItem(item);
    if (!file || !file.type || !/^(image|video)\//i.test(file.type) || !window.URL || !URL.createObjectURL) return '';
    if (localPreviewUrls && localPreviewUrls.has(file)) return localPreviewUrls.get(file);
    var url = URL.createObjectURL(file);
    if (localPreviewUrls) localPreviewUrls.set(file, url);
    return url;
  }

  function localFileThumbHtml(item) {
    var file = localFileFromItem(item);
    var type = String(file && file.type || '');
    var url = localFilePreviewUrl(item);
    if (url && /^image\//i.test(type)) {
      return '<img src="' + escAttr(url) + '" alt="">';
    }
    if (url && /^video\//i.test(type)) {
      return '<video src="' + escAttr(url) + '" muted playsinline preload="metadata"></video>';
    }
    var suffix = String((file && file.name || 'FILE').split('.').pop() || 'FILE').slice(0, 5).toUpperCase();
    return '<span>' + esc(suffix) + '</span>';
  }

  function hasVisualFilePreview(item) {
    var file = localFileFromItem(item);
    return !!(file && file.type && /^(image|video)\//i.test(file.type));
  }

  function setFileInputFiles(input, files) {
    if (!input) return;
    files = files || [];
    if (!files.length) {
      input.value = '';
      return;
    }
    if (typeof DataTransfer === 'undefined') {
      input.value = '';
      return;
    }
    var dt = new DataTransfer();
    files.forEach(function(file) {
      if (file) dt.items.add(file);
    });
    input.files = dt.files;
  }

  function fileKey(file) {
    if (!file) return '';
    return [file.name || '', file.size || 0, file.lastModified || 0, file.type || ''].join('|');
  }

  function mergeCreateFiles(incoming) {
    incoming = incoming || [];
    var merged = Array.isArray(state.createFiles) ? state.createFiles.slice() : [];
    var seen = {};
    merged.forEach(function(file) {
      seen[fileKey(file)] = true;
    });
    incoming.forEach(function(file) {
      var key = fileKey(file);
      if (!key || seen[key]) return;
      seen[key] = true;
      merged.push(file);
    });
    state.createFiles = merged;
    setFileInputFiles(el('ai3dFiles'), state.createFiles);
  }

  function handleGeneralFileChange() {
    var input = el('ai3dFiles');
    if (!input) return;
    mergeCreateFiles(Array.prototype.slice.call(input.files || []));
    renderFiles();
  }

  function parseError(data, fallback) {
    if (!data) return fallback || '请求失败';
    var detail = data.detail || data.error || data.message;
    if (typeof detail === 'string') return detail;
    try { return JSON.stringify(detail || data); } catch (e) { return fallback || '请求失败'; }
  }

  function loadConfig() {
    var balance = el('ai3dBalanceText');
    if (balance) balance.textContent = '正在检查 3D 引擎配置';
    return fetch(api('/api/ai-3d-model/config'), { headers: headers() })
      .then(function(resp) { return resp.json().then(function(data) { return { ok: resp.ok, data: data }; }); })
      .then(function(x) {
        if (!x.ok) throw new Error(parseError(x.data, '配置检查失败'));
        state.configured = !!x.data.configured;
        var badge = el('ai3dProviderBadge');
        if (badge) {
          badge.textContent = state.configured ? 'Meshy 3D 已连接' : 'Meshy 3D 未配置';
          badge.className = 'ai3d-badge ' + (state.configured ? 'ok' : 'bad');
        }
        if (balance) {
          if (!state.configured) balance.textContent = '最终 3D 需在本机 .env 配置 MESHY_API_KEY；拆件可单独选择，不走底模拼接。';
          else if (x.data.balance_error) balance.textContent = '余额读取失败：' + x.data.balance_error;
          else balance.textContent = 'Meshy 3D 余额：' + (x.data.balance == null ? '未知' : x.data.balance + ' credits') + '；拆件流程会逐部件生成三视图再送 3D';
        }
      })
      .catch(function(err) {
        state.configured = false;
        var badge = el('ai3dProviderBadge');
        if (badge) {
          badge.textContent = 'Meshy 3D 未连接';
          badge.className = 'ai3d-badge bad';
        }
        if (balance) balance.textContent = err && err.message ? err.message : '配置检查失败';
      });
  }

  function renderFiles() {
    var input = el('ai3dFiles');
    var list = el('ai3dFileList');
    if (!input || !list) return;
    var slotFiles = realObjectSlotFiles();
    var files = workflowMode() === 'real_object'
      ? slotFiles.map(function(item) {
          return { name: (item.role || 'view') + ' - ' + item.file.name, size: item.file.size, type: item.file.type, file: item.file, role: item.role, slot: true };
        })
      : (Array.isArray(state.createFiles) && state.createFiles.length ? state.createFiles : Array.prototype.slice.call(input.files || []));
    if (!files.length) {
      list.innerHTML = '';
      return;
    }
    list.innerHTML = files.map(function(file, index) {
      var hasVisual = hasVisualFilePreview(file);
      var metaHtml = hasVisual
        ? (formatSize(file.size) ? '<div class="ai3d-file-main compact"><small>' + esc(formatSize(file.size)) + '</small></div>' : '')
        : '<div class="ai3d-file-main"><span>' + esc(file.name) + '</span><small>' + esc(formatSize(file.size)) + '</small></div>';
      var removeAttr = file.slot
        ? 'data-ai3d-remove-slot="' + escAttr(file.role || '') + '"'
        : 'data-ai3d-remove-file="' + index + '"';
      return '<div class="ai3d-file-item">' +
        '<div class="ai3d-file-thumb">' + localFileThumbHtml(file) + '</div>' +
        metaHtml +
        '<button type="button" class="ai3d-file-remove" ' + removeAttr + ' aria-label="删除素材">删除</button>' +
      '</div>';
    }).join('');
  }

  function selectedFormats() {
    return Array.prototype.slice.call(document.querySelectorAll('input[name="format"]:checked'))
      .map(function(input) { return input.value; });
  }

  function selectedText(id) {
    var node = el(id);
    if (!node || !node.options || node.selectedIndex < 0) return node && node.value ? node.value : '';
    return node.options[node.selectedIndex].textContent || node.value || '';
  }

  function workflowMode() {
    var node = el('ai3dWorkflowMode');
    return node && node.value ? node.value : 'custom';
  }

  function realObjectSlotFiles() {
    return Array.prototype.slice.call(document.querySelectorAll('#ai3dRealObjectSlots input[type="file"][data-ai3d-role]'))
      .filter(function(input) { return input.files && input.files[0]; })
      .map(function(input) {
        return { role: input.getAttribute('data-ai3d-role') || '', file: input.files[0] };
      });
  }

  function removeUploadedFile(index) {
    var input = el('ai3dFiles');
    if (!input) return;
    var files = Array.isArray(state.createFiles) && state.createFiles.length
      ? state.createFiles.slice()
      : Array.prototype.slice.call(input.files || []);
    files.splice(Number(index), 1);
    state.createFiles = files;
    setFileInputFiles(input, files);
    renderFiles();
  }

  function clearRealObjectSlot(role) {
    if (!role) return;
    var input = document.querySelector('#ai3dRealObjectSlots input[type="file"][data-ai3d-role="' + role.replace(/"/g, '\\"') + '"]');
    if (input) input.value = '';
    renderFiles();
  }

  function resetCreateForm() {
    var form = el('ai3dForm');
    if (form) form.reset();
    var files = el('ai3dFiles');
    state.createFiles = [];
    if (files) files.value = '';
    Array.prototype.slice.call(document.querySelectorAll('#ai3dRealObjectSlots input[type="file"]')).forEach(function(input) {
      input.value = '';
    });
    renderFiles();
    applyWorkflowModeDefaults();
    setMsg('', false);
  }

  function applyWorkflowModeDefaults() {
    var mode = workflowMode();
    var slots = el('ai3dRealObjectSlots');
    if (slots) slots.hidden = mode !== 'real_object';
    var hint = el('ai3dWorkflowHint');
    var strategy = el('ai3dStrategy');
    var preprocess = el('ai3dPreprocessOnly');
    var template = el('ai3dTemplate');
    var autoDecompose = el('ai3dAutoDecompose');
    var files = el('ai3dFiles');
    var dropzone = files && files.closest ? files.closest('.ai3d-dropzone') : null;
    var dropTitle = dropzone ? dropzone.querySelector('span') : null;
    var dropHint = dropzone ? dropzone.querySelector('small') : null;
    if (files) files.setAttribute('accept', 'image/*');
    if (files) files.disabled = mode === 'real_object';
    if (mode === 'real_object') {
      if (strategy) strategy.value = 'multi_view';
      if (preprocess) preprocess.checked = false;
      if (autoDecompose) autoDecompose.checked = false;
      if (hint) hint.textContent = '实物：按固定角度上传真实照片，优先用正面、45°、侧面、背面 4 张直接生成 3D。';
      if (dropTitle) dropTitle.textContent = '使用下方固定角度槽位';
      if (dropHint) dropHint.textContent = '建议 4 个角度；至少正面和一个非正面角度。';
    } else if (mode === 'game_prop') {
      if (strategy) strategy.value = 'multi_view';
      if (preprocess) preprocess.checked = true;
      if (autoDecompose) autoDecompose.checked = true;
      if (template && template.value === 'auto') template.value = 'ornament_prop';
      if (hint) hint.textContent = '游戏道具：可上传多张参考图，第一张作为主图；AI 先理解主体并生成可编辑多视角提示词。';
      if (dropTitle) dropTitle.textContent = '上传道具参考图（可多张）';
      if (dropHint) dropHint.textContent = '第一张作为主图，其他图作为造型/材质/细节参考；也可以只填写提示词。';
    } else if (mode === 'component_split') {
      if (strategy) strategy.value = 'part_batch';
      if (preprocess) preprocess.checked = true;
      if (autoDecompose) autoDecompose.checked = false;
      if (hint) hint.textContent = '拆件：上传 1 张主图，GPT 先规划部件提示词，GPT Image 2 生成部件图和部件三视图，再逐件生成 3D。';
      if (dropTitle) dropTitle.textContent = '上传 1 张拆件参考图';
      if (dropHint) dropHint.textContent = '系统自动决定拆几个部件；不生成底模，也不做低质量拼接。';
    } else if (mode === 'direct_multiview') {
      if (strategy) strategy.value = 'multi_view';
      if (preprocess) preprocess.checked = false;
      if (autoDecompose) autoDecompose.checked = false;
      if (hint) hint.textContent = '多视图：可上传 1 张多视图参考板自动识别裁切，或上传最多 4 张独立视角图直接送入 3D。';
      if (dropTitle) dropTitle.textContent = '上传多视图参考板或独立视角图';
      if (dropHint) dropHint.textContent = '1 张参考板会自动裁切视角；多张图片按上传顺序映射为正面、45°、侧面、背面。';
    } else {
      if (files) files.disabled = false;
      if (hint) hint.textContent = '自定义：保留现有自动判断、裁切、多视角和拆件流程。';
      if (dropTitle) dropTitle.textContent = '上传参考图片（可多张）';
      if (dropHint) dropHint.textContent = '系统按资产模板自动判断预处理、多视角或拆件流程。';
    }
    renderFiles();
    updateParamSummary();
  }

  function updateParamSummary() {
    var host = el('ai3dParamSummary');
    if (!host) return;
    var model = selectedText('ai3dImageModel').replace(/（.*?）/g, '').trim() || 'GPT Image 2';
    var template = selectedText('ai3dTemplate') || '写实角色/人物';
    var strategy = selectedText('ai3dStrategy') || '自动判断';
    var workflow = selectedText('ai3dWorkflowMode') || '自定义';
    var formats = selectedFormats().map(function(item) { return item.toUpperCase(); }).join('/');
    host.textContent = [workflow, model, '4K', 'high', 'PNG', template, strategy, formats || '未选格式'].filter(Boolean).join(' · ');
  }

  function openCreateModal() {
    var modal = el('ai3dCreateModal');
    if (!modal) return;
    resetCreateForm();
    modal.hidden = false;
    document.body.classList.add('ai3d-modal-open');
  }

  function closeCreateModal() {
    var modal = el('ai3dCreateModal');
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove('ai3d-modal-open');
    applyWorkflowModeDefaults();
  }

  function openHistoryModal() {
    var modal = el('ai3dHistoryModal');
    if (!modal) return;
    renderJobList();
    modal.hidden = false;
    document.body.classList.add('ai3d-modal-open');
    loadJobs(false);
  }

  function closeHistoryModal() {
    var modal = el('ai3dHistoryModal');
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove('ai3d-modal-open');
  }

  function openParamModal() {
    openCreateModal();
  }

  function closeParamModal() {
    closeCreateModal();
  }

  function openTextEditModal(opts) {
    opts = opts || {};
    var modal = el('ai3dTextEditModal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'ai3dTextEditModal';
      modal.className = 'ai3d-modal';
      modal.hidden = true;
      modal.innerHTML =
        '<div class="ai3d-modal-backdrop" data-ai3d-text-close></div>' +
        '<div class="ai3d-modal-panel ai3d-text-edit-panel" role="dialog" aria-modal="true">' +
        '<div class="ai3d-modal-head"><div><strong id="ai3dTextEditTitle">编辑</strong><span id="ai3dTextEditSub"></span></div>' +
        '<button type="button" class="ai3d-modal-close" data-ai3d-text-close aria-label="关闭">×</button></div>' +
        '<div class="ai3d-modal-body"><div class="ai3d-field"><textarea id="ai3dTextEditValue" rows="12"></textarea></div></div>' +
        '<div class="ai3d-modal-actions"><button type="button" class="btn btn-ghost btn-sm" data-ai3d-text-close>取消</button>' +
        '<button type="button" id="ai3dTextEditSubmit" class="btn btn-primary btn-sm">确认</button></div></div>';
      document.body.appendChild(modal);
      modal.addEventListener('click', function(evt) {
        if (evt.target && evt.target.hasAttribute('data-ai3d-text-close')) {
          modal.hidden = true;
          document.body.classList.remove('ai3d-modal-open');
        }
      });
    }
    var title = el('ai3dTextEditTitle');
    var sub = el('ai3dTextEditSub');
    var textarea = el('ai3dTextEditValue');
    var submit = el('ai3dTextEditSubmit');
    if (title) title.textContent = opts.title || '编辑';
    if (sub) sub.textContent = opts.subtitle || '';
    if (textarea) {
      textarea.value = opts.value || '';
      textarea.placeholder = opts.placeholder || '';
    }
    if (submit) {
      submit.onclick = function() {
        if (typeof opts.onSubmit === 'function') opts.onSubmit(textarea ? textarea.value : '', submit, modal);
      };
    }
    modal.hidden = false;
    document.body.classList.add('ai3d-modal-open');
    setTimeout(function() { if (textarea) textarea.focus(); }, 30);
  }

  function currentTriviewPrompt(job) {
    var preprocessing = job && job.preprocessing ? job.preprocessing : {};
    if (preprocessing.custom_triview_prompt) return String(preprocessing.custom_triview_prompt || '');
    var plan = job && job.view_generation_plan ? job.view_generation_plan : {};
    if (plan.custom_triview_prompt) return String(plan.custom_triview_prompt || '');
    var views = Array.isArray(plan.views) ? plan.views : [];
    var sheet = views.filter(function(item) { return item && item.view === 'triview_sheet'; })[0];
    return sheet && sheet.prompt ? String(sheet.prompt) : '';
  }

  function editTriviewPrompt(jobId) {
    var job = state.currentJob || {};
    openTextEditModal({
      title: '编辑多视角提示词',
      subtitle: '保存后再生成多视角，会按这段提示词走 4K/high/png。',
      value: currentTriviewPrompt(job),
      placeholder: '写清楚主体、材质、关键细节、禁止变化，以及正面/45°/侧面/背面要求。',
      onSubmit: function(value, submitBtn, modal) {
        var fd = new FormData();
        fd.append('prompt', String(value || '').trim());
        setBusy(submitBtn, true, '保存中...');
        fetch(api('/api/ai-3d-model/jobs/' + encodeURIComponent(jobId) + '/triview-prompt'), {
          method: 'POST',
          headers: formHeaders(),
          body: fd
        })
          .then(function(resp) { return resp.json().then(function(data) { return { ok: resp.ok, data: data }; }); })
          .then(function(x) {
            if (!x.ok || !x.data || x.data.ok === false) throw new Error(parseError(x.data, '提示词保存失败'));
            modal.hidden = true;
            document.body.classList.remove('ai3d-modal-open');
            renderJob(x.data.job || {});
            setMsg('多视角提示词已保存，可以点击生成多视角。', false);
          })
          .catch(function(err) {
            setMsg(err && err.message ? err.message : '提示词保存失败', true);
          })
          .finally(function() { setBusy(submitBtn, false); });
      }
    });
  }

  function regenerateTriviewView(jobId, role) {
    var labelMap = {
      front: '正视图',
      front_left_45: '左前45°视图',
      front_right_45: '右前45°视图',
      side: '侧视图',
      back: '背视图'
    };
    openTextEditModal({
      title: '重生' + (labelMap[role] || role),
      subtitle: '只重生这一张，原提示词和当前图片会一起传给 AI。',
      value: '',
      placeholder: '例如：角度再明显一点；不要拉窄主体；保留顶部旗子和右侧管线；不要改变材质。',
      onSubmit: function(value, submitBtn, modal) {
        var fd = new FormData();
        fd.append('role', role);
        fd.append('edit_prompt', String(value || '').trim());
        fd.append('model', el('ai3dImageModel') ? el('ai3dImageModel').value : 'openai/gpt-image-2');
        setBusy(submitBtn, true, '提交中...');
        fetch(api('/api/ai-3d-model/jobs/' + encodeURIComponent(jobId) + '/triview/regenerate-view'), {
          method: 'POST',
          headers: formHeaders(),
          body: fd
        })
          .then(function(resp) { return resp.json().then(function(data) { return { ok: resp.ok, data: data }; }); })
          .then(function(x) {
            if (!x.ok || !x.data || x.data.ok === false) throw new Error(parseError(x.data, '视角重生启动失败'));
            modal.hidden = true;
            document.body.classList.remove('ai3d-modal-open');
            renderJob(x.data.job || {});
            startPolling();
            setMsg('已开始重生单个视角，完成后会自动刷新并复核。', false);
          })
          .catch(function(err) {
            setMsg(err && err.message ? err.message : '视角重生启动失败', true);
          })
          .finally(function() { setBusy(submitBtn, false); });
      }
    });
  }

  function actionContext(trigger, fallbackId, explicitJobId) {
    var btn = null;
    if (trigger && trigger.currentTarget) btn = trigger.currentTarget;
    else if (trigger && trigger.nodeType === 1) btn = trigger;
    else btn = el(fallbackId);
    var jobId = explicitJobId || (btn && btn.dataset ? btn.dataset.jobId : '') || state.jobId;
    if (jobId) rememberJob(jobId);
    return { btn: btn, jobId: jobId };
  }

  function submitJob(evt) {
    if (evt) evt.preventDefault();
    var input = el('ai3dFiles');
    var mode = workflowMode();
    var slotFiles = realObjectSlotFiles();
    var generalFiles = Array.isArray(state.createFiles) && state.createFiles.length
      ? state.createFiles.slice()
      : Array.prototype.slice.call(input && input.files ? input.files : []);
    var description = el('ai3dDescription') ? el('ai3dDescription').value.trim() : '';
    var hasFiles = mode === 'real_object' ? !!slotFiles.length : !!generalFiles.length;
    if (!hasFiles && !description) {
      setMsg('请上传图片，或填写资产提示词。', true);
      return;
    }
    if (mode === 'component_split' && generalFiles.length !== 1) {
      setMsg('拆件流程必须且只需要上传 1 张参考图；多图请用“多视图直接生成 3D”或实物流程。', true);
      return;
    }
    if (mode === 'real_object' && slotFiles.length < 2) {
      setMsg('实物生 3D 至少上传正面和一个非正面角度；建议补齐 45°、侧面、背面。', true);
      return;
    }
    if (mode === 'real_object') {
      var slotRoles = slotFiles.map(function(item) { return item.role; });
      if (slotRoles.indexOf('front') < 0 || slotRoles.length < 2) {
        setMsg('实物生 3D 必须至少包含正面和一个非正面角度。', true);
        return;
      }
    }
    if ((mode === 'real_object' || mode === 'direct_multiview') && generalFiles.length > 4) {
      setMsg('Meshy 多图生成最多支持 4 张独立视角图；如果是一张参考板，请只上传那一张。', true);
      return;
    }
    var formats = selectedFormats();
    if (!formats.length) {
      setMsg('请至少选择一种导出格式。', true);
      return;
    }
    var fd = new FormData();
    var inputRoles = [];
    if (mode === 'real_object') {
      slotFiles.forEach(function(item) {
        fd.append('files', item.file, item.role + '-' + item.file.name);
        inputRoles.push(item.role);
      });
    } else if (hasFiles) {
      generalFiles.forEach(function(file) {
        fd.append('files', file, file.name);
      });
    }
    fd.append('workflow_mode', mode);
    fd.append('input_roles', JSON.stringify(inputRoles));
    fd.append('strategy', el('ai3dStrategy') ? el('ai3dStrategy').value : 'auto');
    fd.append('quality', el('ai3dQuality') ? el('ai3dQuality').value : 'production');
    fd.append('formats', formats.join(','));
    fd.append('title', el('ai3dTitle') ? el('ai3dTitle').value.trim() : '');
    fd.append('auto_decompose', el('ai3dAutoDecompose') && el('ai3dAutoDecompose').checked ? 'true' : 'false');
    fd.append('max_parts', el('ai3dMaxParts') ? el('ai3dMaxParts').value : '24');
    var preprocessOnly = el('ai3dPreprocessOnly') && el('ai3dPreprocessOnly').checked;
    if (mode === 'real_object' || mode === 'direct_multiview') preprocessOnly = false;
    if (mode === 'game_prop' || mode === 'component_split') preprocessOnly = true;
    fd.append('preprocess_only', preprocessOnly ? 'true' : 'false');
    fd.append('asset_template', el('ai3dTemplate') ? el('ai3dTemplate').value : 'auto');
    fd.append('reference_strength', el('ai3dReferenceStrength') ? el('ai3dReferenceStrength').value : 'high');
    fd.append('description', description);
    fd.append('image_model', el('ai3dImageModel') ? el('ai3dImageModel').value : 'openai/gpt-image-2');
    var btn = el('ai3dSubmitBtn');
    setBusy(btn, true, '提交中...');
    setMsg('任务已提交，正在创建任务记录...', false);
    closeCreateModal();
    fetch(api('/api/ai-3d-model/jobs'), {
      method: 'POST',
      headers: formHeaders(),
      body: fd
    })
      .then(function(resp) { return resp.json().then(function(data) { return { ok: resp.ok, data: data }; }); })
      .then(function(x) {
        if (!x.ok || !x.data || x.data.ok === false) throw new Error(parseError(x.data, '任务提交失败'));
        rememberJob(x.data.job && x.data.job.job_id);
        renderJob(x.data.job || {});
        upsertJob(x.data.job || {});
        loadJobs(false);
        if (x.data.job && x.data.job.status === 'preprocessed') {
          if (x.data.job.workflow_mode === 'component_split' || (x.data.job.preprocessing && x.data.job.preprocessing.workflow_mode === 'component_split')) {
            setMsg('拆件任务已创建：先点“生成拆件部件图”；后续会为每个部件生成三视图再生成 3D。', false);
          } else if (x.data.job.workflow_mode === 'game_prop' || (x.data.job.preprocessing && x.data.job.preprocessing.workflow_mode === 'game_prop')) {
            setMsg('游戏道具任务已创建：先在第一步检查/编辑提示词，再生成多视图。', false);
          } else if (x.data.job.preprocessing && x.data.job.preprocessing.text_prompt_only) {
            setMsg('纯文本任务已创建：先检查/编辑提示词，再生成多视图；这一步不调用 Meshy。', false);
          } else {
            setMsg('已完成预处理，请检查主体裁切和区域候选；下一步先生成多视图，确认生成 3D 时才调用 Meshy。', false);
          }
        } else if (x.data.job && x.data.job.status === 'preprocessing') {
          setMsg('任务已创建，AI 正在后台理解图片；进度会显示在步骤列表里。', false);
          startPolling();
        } else {
          setMsg('任务已提交，正在调用 Meshy 生成 3D。复杂资产可能需要数分钟。', false);
          startPolling();
        }
      })
      .catch(function(err) {
        setMsg(err && err.message ? err.message : '任务提交失败', true);
      })
      .finally(function() {
        setBusy(btn, false);
      });
  }

  function startPolling() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    if (!state.jobId) return;
    pollJob();
    state.pollTimer = setInterval(pollJob, 6000);
  }

  function pollJob() {
    if (!state.jobId) return;
    fetch(api('/api/ai-3d-model/jobs/' + encodeURIComponent(state.jobId)), { headers: headers() })
      .then(function(resp) { return resp.json().then(function(data) { return { ok: resp.ok, data: data }; }); })
      .then(function(x) {
        if (!x.ok || !x.data || x.data.ok === false) throw new Error(parseError(x.data, '任务状态读取失败'));
        var job = x.data.job || {};
        rememberJob(job.job_id || state.jobId);
        renderJob(job);
        upsertJob(job);
        if (job.status === 'succeeded' || job.status === 'failed' || job.status === 'preprocessed') {
          if (state.pollTimer) clearInterval(state.pollTimer);
          state.pollTimer = null;
          if (job.status === 'succeeded') {
            setMsg('3D 模型已生成，可以下载模型文件。', false);
            loadConfig();
          } else if (job.status === 'preprocessed') {
            if (job.stage === 'triview_completed') {
              setMsg('多视图已由图片模型生成，请检查后确认生成 3D。', false);
            } else if (job.stage === 'base_model_ready') {
              setMsg('完整 3D 模型已生成。满意就到这里结束；不满意再生成部件输入图做局部增强。', false);
            } else if (job.stage === 'component_split_completed') {
              setMsg('2D 部件输入图已完成；这还不是 3D 部件。完整 3D 模型就绪后可单独生成 3D 部件。', false);
            } else if (job.stage === 'parts_3d_ready') {
              setMsg('3D 部件已生成/复用完成。现在可以点击“合成最终模型”。', false);
            } else if (job.stage === 'triview_failed') {
              setMsg('多视图生成失败：' + (job.error || '图片模型暂时没有返回结果') + '。任务进度已保留；为保证一致性，系统不会自动切换模型。', true);
            } else if (job.stage === 'component_split_failed') {
              setMsg('AI 部件分离失败：' + (job.error || '图片模型暂时没有返回结果') + '。任务进度已保留；为保证一致性，系统不会自动切换模型。', true);
            } else {
              setMsg('预处理已完成，请检查主体裁切和区域候选图；下一步可用图片模型生成多视图或独立部件板。', false);
            }
          } else {
            setMsg(job.error || '3D 生成失败', true);
          }
        }
      })
      .catch(function(err) {
        setMsg(err && err.message ? err.message : '任务状态读取失败', true);
      });
  }

  function statusText(job) {
    var s = job.status || 'idle';
    var stage = job.stage ? ' / ' + job.stage : '';
    return s + stage;
  }

  function renderJob(job) {
    job = job || {};
    state.currentJob = job;
    var badge = el('ai3dJobBadge');
    var meta = el('ai3dJobMeta');
    var bar = el('ai3dProgressBar');
    if (badge) {
      badge.textContent = job.status || 'idle';
      badge.className = 'ai3d-badge ' + (job.status === 'succeeded' ? 'ok' : job.status === 'failed' ? 'bad' : 'muted');
    }
    if (meta) {
      var pieces = [statusText(job)];
      if (job.mode) pieces.push(job.mode);
      if (job.quality) pieces.push(job.quality);
      if (job.consumed_credits) pieces.push(job.consumed_credits + ' credits');
      meta.textContent = pieces.filter(Boolean).join(' · ');
    }
    if (bar) bar.style.width = Math.max(0, Math.min(100, Number(job.progress || 0))) + '%';
    renderActions(job);
    renderPreview(job);
    renderSteps(job);
    renderMetrics(job);
    renderOutputs(job);
    updateCurrentDownload(job);
  }

  function upsertJob(job) {
    if (!job || !job.job_id) return;
    var found = false;
    state.jobs = (state.jobs || []).map(function(item) {
      if (item.job_id === job.job_id) {
        found = true;
        return job;
      }
      return item;
    });
    if (!found) state.jobs.unshift(job);
    state.jobs.sort(function(a, b) {
      return String(b.updated_at || b.created_at || '').localeCompare(String(a.updated_at || a.created_at || ''));
    });
    state.jobs = state.jobs.slice(0, 100);
    setJobPageForJob(job.job_id);
    renderJobList();
  }

  function isActiveJob(job) {
    var s = job && job.status;
    return s === 'preprocessing' || s === 'queued' || s === 'running' || s === 'generating_views' || s === 'splitting_parts';
  }

  function loadJobs(restoreLatest) {
    var seq = ++state.jobsLoadSeq;
    var host = el('ai3dJobList');
    if (host && !(state.jobs && state.jobs.length)) {
      host.innerHTML = '<div class="ai3d-empty slim">正在恢复历史任务...</div>';
    }
    return fetch(api('/api/ai-3d-model/jobs?limit=100'), { headers: headers() })
      .then(function(resp) { return resp.json().then(function(data) { return { ok: resp.ok, data: data }; }); })
      .then(function(x) {
        if (seq !== state.jobsLoadSeq) return;
        if (!x.ok || !x.data || x.data.ok === false) throw new Error(parseError(x.data, '任务列表读取失败'));
        state.jobs = Array.isArray(x.data.jobs) ? x.data.jobs : [];
        renderJobList();
        if (restoreLatest !== false && !state.jobId) restoreJobFromList();
      })
      .catch(function(err) {
        if (seq !== state.jobsLoadSeq) return;
        if (host) host.innerHTML = '<div class="ai3d-empty slim">' + esc(err && err.message ? err.message : '任务列表读取失败') + '</div>';
      });
  }

  function restoreJobFromList() {
    var jobs = state.jobs || [];
    if (!jobs.length) {
      renderSteps({});
      return;
    }
    var last = rememberedJob();
    var job = jobs.filter(function(item) { return item.job_id === last; })[0] || jobs[0];
    rememberJob(job.job_id);
    setJobPageForJob(job.job_id);
    renderJob(job);
    renderJobList();
    if (isActiveJob(job)) startPolling();
    else setMsg('已恢复最近任务：' + displayJobTitle(job), false);
  }

  function selectJob(jobId) {
    if (!jobId) return;
    rememberJob(jobId);
    fetch(api('/api/ai-3d-model/jobs/' + encodeURIComponent(jobId)), { headers: headers() })
      .then(function(resp) { return resp.json().then(function(data) { return { ok: resp.ok, data: data }; }); })
      .then(function(x) {
        if (!x.ok || !x.data || x.data.ok === false) throw new Error(parseError(x.data, '任务读取失败'));
        var job = x.data.job || {};
        setJobPageForJob(job.job_id);
        renderJob(job);
        upsertJob(job);
        closeHistoryModal();
        if (isActiveJob(job)) startPolling();
        else if (state.pollTimer) {
          clearInterval(state.pollTimer);
          state.pollTimer = null;
        }
      })
      .catch(function(err) {
        setMsg(err && err.message ? err.message : '任务读取失败', true);
      });
  }

  function jobDownloadHref(job) {
    if (!job || !job.job_id) return '';
    return api('/api/ai-3d-model/jobs/' + encodeURIComponent(job.job_id) + '/download');
  }

  function downloadHref(href, filename) {
    if (!href) return;
    var a = document.createElement('a');
    a.href = /^https?:\/\//i.test(href) ? href : api(href);
    a.target = '_blank';
    a.rel = 'noopener';
    if (filename) a.download = filename;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    setTimeout(function() {
      if (a.parentNode) a.parentNode.removeChild(a);
    }, 1000);
  }

  function triggerJobDownload(job) {
    var current = job && job.job_id ? job : (state.currentJob || {});
    if ((!current || !current.job_id) && state.jobId) current = { job_id: state.jobId };
    var href = jobDownloadHref(current);
    if (!href) {
      setMsg('Please select a task before downloading its resources.', true);
      return;
    }
    setMsg('Preparing download package. Large 3D tasks may take a while on the first download.', false);
    downloadHref(href, String(current.job_id || 'ai3d') + '-ai3d-outputs.zip');
  }

  function totalJobPages() {
    return Math.max(1, Math.ceil((state.jobs || []).length / state.jobPageSize));
  }

  function clampJobPage() {
    state.jobPage = Math.max(1, Math.min(totalJobPages(), Number(state.jobPage || 1)));
  }

  function setJobPageForJob(jobId) {
    if (!jobId) {
      clampJobPage();
      return;
    }
    var idx = -1;
    (state.jobs || []).some(function(job, i) {
      if (job && job.job_id === jobId) {
        idx = i;
        return true;
      }
      return false;
    });
    if (idx >= 0) state.jobPage = Math.floor(idx / state.jobPageSize) + 1;
    clampJobPage();
  }

  function renderJobPager() {
    var pager = el('ai3dJobPager');
    if (!pager) return;
    var jobs = state.jobs || [];
    var pages = totalJobPages();
    if (jobs.length <= state.jobPageSize) {
      pager.innerHTML = '';
      return;
    }
    clampJobPage();
    var start = (state.jobPage - 1) * state.jobPageSize + 1;
    var end = Math.min(jobs.length, state.jobPage * state.jobPageSize);
    pager.innerHTML = '<button type="button" class="ai3d-page-btn" data-ai3d-page="prev"' + (state.jobPage <= 1 ? ' disabled' : '') + '>上一页</button>' +
      '<span>' + esc(start + '-' + end + ' / ' + jobs.length + '，第 ' + state.jobPage + '/' + pages + ' 页') + '</span>' +
      '<button type="button" class="ai3d-page-btn" data-ai3d-page="next"' + (state.jobPage >= pages ? ' disabled' : '') + '>下一页</button>';
  }

  function updateCurrentDownload(job) {
    var link = el('ai3dDownloadCurrentBtn');
    if (!link) return;
    var current = job && job.job_id ? job : (state.currentJob || {});
    var href = jobDownloadHref(current);
    if (!href) {
      link.setAttribute('href', '#');
      link.setAttribute('aria-disabled', 'true');
      link.classList.add('disabled');
      link.removeAttribute('target');
      link.removeAttribute('download');
      return;
    }
    link.setAttribute('href', href);
    link.setAttribute('target', '_blank');
    link.setAttribute('rel', 'noopener');
    link.setAttribute('download', String(current.job_id || 'ai3d') + '-ai3d-outputs.zip');
    link.setAttribute('aria-disabled', 'false');
    link.classList.remove('disabled');
  }

  function renderJobList() {
    var host = el('ai3dJobList');
    if (!host) return;
    var jobs = state.jobs || [];
    if (!jobs.length) {
      host.innerHTML = '<div class="ai3d-empty slim">暂无历史任务。</div>';
      renderJobPager();
      updateCurrentDownload({});
      return;
    }
    clampJobPage();
    var startIndex = (state.jobPage - 1) * state.jobPageSize;
    var pageJobs = jobs.slice(startIndex, startIndex + state.jobPageSize);
    host.innerHTML = pageJobs.map(function(job) {
      var active = job.job_id === state.jobId ? ' active' : '';
      var title = displayJobTitle(job);
      var meta = [statusText(job), job.mode, job.updated_at ? formatTime(job.updated_at) : ''].filter(Boolean).join(' · ');
      var download = jobDownloadHref(job);
      return '<article class="ai3d-job-card' + active + '">' +
        '<button type="button" class="ai3d-job-item" data-ai3d-job-id="' + escAttr(job.job_id || '') + '">' +
        '<strong>' + esc(title) + '</strong><span>' + esc(meta) + '</span></button>' +
        '<div class="ai3d-job-actions">' +
        (download ? '<a class="ai3d-job-download" href="' + escAttr(download) + '" target="_blank" rel="noopener">批量下载</a>' : '') +
        '</div></article>';
    }).join('');
    renderJobPager();
    updateCurrentDownload();
  }

  function formatTime(value) {
    if (!value) return '';
    try {
      var d = new Date(value);
      if (!isNaN(d.getTime())) return d.toLocaleString();
    } catch (e) {}
    return String(value).slice(0, 19);
  }

  function previewImg(url, title, cls) {
    if (!url) return '';
    var full = api(url);
    return '<button type="button" class="' + escAttr(cls || 'ai3d-previewable') + '" data-ai3d-preview-src="' + escAttr(full) +
      '" data-ai3d-preview-title="' + escAttr(title || '预览图') + '"><img src="' + escAttr(full) + '" alt="' + escAttr(title || '预览图') + '"></button>';
  }

  function openLightbox(src, title) {
    if (!src) return;
    closeLightbox();
    var box = document.createElement('div');
    box.className = 'ai3d-lightbox';
    box.innerHTML = '<button type="button" class="ai3d-lightbox-close ai3d-lightbox-floating-close" aria-label="关闭">×</button>' +
      '<div class="ai3d-lightbox-panel" role="dialog" aria-modal="true">' +
      '<div class="ai3d-lightbox-head"><strong>' + esc(title || '预览图') + '</strong><button type="button" class="ai3d-lightbox-close" aria-label="关闭">×</button></div>' +
      '<div class="ai3d-lightbox-body"><img src="' + escAttr(src) + '" alt="' + escAttr(title || '预览图') + '"></div></div>';
    document.body.appendChild(box);
  }

  function openModelLightbox(src, title, poster, downloadUrl) {
    if (!src) return;
    ensureModelViewer();
    closeLightbox();
    var box = document.createElement('div');
    box.className = 'ai3d-lightbox ai3d-model-lightbox';
    box.innerHTML = '<button type="button" class="ai3d-lightbox-close ai3d-lightbox-floating-close" aria-label="关闭">×</button>' +
      '<div class="ai3d-lightbox-panel ai3d-model-lightbox-panel" role="dialog" aria-modal="true">' +
      '<div class="ai3d-lightbox-head"><strong>' + esc(title || '3D 预览') + '</strong><button type="button" class="ai3d-lightbox-close" aria-label="关闭">×</button></div>' +
      '<div class="ai3d-lightbox-body ai3d-model-lightbox-body">' +
      '<model-viewer class="ai3d-model-viewer ai3d-model-viewer-modal" src="' + escAttr(src) + '"' +
      (poster ? ' poster="' + escAttr(poster) + '"' : '') +
      ' camera-controls touch-action="pan-y" auto-rotate rotation-per-second="18deg" shadow-intensity="0.75" exposure="1" environment-image="neutral" ar>' +
      '<div class="ai3d-empty" slot="poster">正在加载 3D 模型...</div>' +
      '</model-viewer></div>' +
      '<div class="ai3d-model-viewer-bar"><span>' + esc(title || '3D 模型') + ' · 可拖动旋转，滚轮缩放</span>' +
      '<a href="' + escAttr(downloadUrl || src) + '" target="_blank" rel="noopener">下载 GLB</a></div>' +
      '</div>';
    document.body.appendChild(box);
  }

  function closeLightbox() {
    document.querySelectorAll('.ai3d-lightbox').forEach(function(node) { node.remove(); });
  }

  function closeInfoBubble() {
    state.infoBubblePinned = false;
    document.querySelectorAll('.ai3d-info-bubble').forEach(function(node) { node.remove(); });
    document.querySelectorAll('.ai3d-info-dot.active').forEach(function(node) { node.classList.remove('active'); });
  }

  function openInfoBubble(btn, pinned) {
    if (!btn) return;
    var text = btn.getAttribute('data-ai3d-info') || '';
    if (!text) return;
    closeInfoBubble();
    state.infoBubblePinned = !!pinned;
    btn.classList.add('active');
    var bubble = document.createElement('div');
    bubble.className = 'ai3d-info-bubble';
    bubble.setAttribute('role', 'tooltip');
    bubble.textContent = text;
    document.body.appendChild(bubble);
    var rect = btn.getBoundingClientRect();
    var bubbleRect = bubble.getBoundingClientRect();
    var margin = 12;
    var left = rect.left + rect.width / 2 - bubbleRect.width / 2;
    left = Math.max(margin, Math.min(left, window.innerWidth - bubbleRect.width - margin));
    var top = rect.bottom + 8;
    if (top + bubbleRect.height + margin > window.innerHeight) {
      top = Math.max(margin, rect.top - bubbleRect.height - 8);
    }
    bubble.style.left = left + 'px';
    bubble.style.top = top + 'px';
  }

  function displayJobTitle(job) {
    var title = String((job && job.title) || '').trim();
    if (!title || /^\?+$/.test(title)) return '3D 任务 ' + String((job && job.job_id) || '').slice(0, 8);
    return title;
  }

  function stepStatusText(status) {
    return ({
      done: '完成',
      running: '进行中',
      failed: '失败',
      skipped: '跳过',
      blocked: '需真实拆件',
      pending: '待处理'
    })[status || 'pending'] || status || '待处理';
  }

  function stepStatusClass(status) {
    return status === 'done' ? 'ok' : status === 'failed' ? 'failed' : status === 'running' ? 'running' : status === 'skipped' ? 'skip' : status === 'blocked' ? 'blocked' : 'muted';
  }

  function renderStepThumbs(items, limit, stepKey, job) {
    if (!Array.isArray(items) || !items.length) return '';
    return '<div class="ai3d-step-thumbs">' + items.slice(0, limit || 12).map(function(item) {
      var url = item.preview_url || item.url || '';
      var isImage = item.preview_url || /\.(png|jpe?g|webp)$/i.test(url);
      var title = item.label || item.filename || item.format || '??';
      if (item && item.kind === 'prompt') {
        var promptText = String(item.prompt || '');
        var promptAction = (stepKey === 'triview' || stepKey === 'prompt') && job && job.job_id
          ? '<button type="button" class="ai3d-mini-action" data-ai3d-action="triview_prompt" data-ai3d-job-id="' + escAttr(job.job_id) + '">编辑提示词</button>'
          : '';
        return '<figure class="ai3d-prompt-thumb ai3d-text-only-prompt">' +
          '<figcaption><strong>' + esc(title) + '</strong><span class="ai3d-card-meta">' +
          esc(promptText.length > 180 ? promptText.slice(0, 180) + '...' : promptText) +
          '</span>' + promptAction + '</figcaption></figure>';
      }
      if (item && (item.kind === 'component_prompt' || item.kind === 'component_triview_prompt')) {
        var roleKey = item.role || '';
        var imagePrompt = String(item.image_prompt || '');
        var triviewPrompt = String(item.triview_prompt || '');
        if (item.kind === 'component_triview_prompt') triviewPrompt = String(item.triview_prompt || item.prompt || '');
        var reasonText = item.reason ? '<small>' + esc(item.reason) + '</small>' : '';
        var sourcePreview = item.source_preview_url ? previewImg(item.source_preview_url, item.label || roleKey, 'ai3d-previewable ai3d-step-preview') : '';
        var html = '<figure class="ai3d-prompt-thumb ai3d-component-prompt" data-ai3d-part-role="' + escAttr(roleKey) + '">' +
          sourcePreview +
          '<figcaption><strong>' + esc(item.label || roleKey || '部件') + '</strong>' + reasonText;
        if (item.kind === 'component_prompt') {
          html += '<label class="ai3d-prompt-label">部件图提示词</label>' +
            '<textarea class="ai3d-inline-prompt" data-ai3d-component-field="image_prompt" data-ai3d-role="' + escAttr(roleKey) + '">' + esc(imagePrompt) + '</textarea>';
          if (job && job.job_id) {
            html += '<div class="ai3d-card-actions">' +
              '<button type="button" class="ai3d-mini-action" data-ai3d-action="save_component_prompt" data-ai3d-job-id="' + escAttr(job.job_id) + '" data-ai3d-role="' + escAttr(roleKey) + '">保存</button>' +
              '<button type="button" class="ai3d-mini-action primary" data-ai3d-action="component_images" data-ai3d-job-id="' + escAttr(job.job_id) + '" data-ai3d-role="' + escAttr(roleKey) + '">生成图片</button>' +
              '</div>';
          }
        } else {
          html += '<label class="ai3d-prompt-label">三视图提示词</label>' +
            '<textarea class="ai3d-inline-prompt" data-ai3d-component-field="triview_prompt" data-ai3d-role="' + escAttr(roleKey) + '">' + esc(triviewPrompt) + '</textarea>';
          if (job && job.job_id) {
            html += '<div class="ai3d-card-actions">' +
              '<button type="button" class="ai3d-mini-action" data-ai3d-action="save_component_prompt" data-ai3d-job-id="' + escAttr(job.job_id) + '" data-ai3d-role="' + escAttr(roleKey) + '">保存</button>' +
              '<button type="button" class="ai3d-mini-action primary" data-ai3d-action="component_triviews" data-ai3d-job-id="' + escAttr(job.job_id) + '" data-ai3d-role="' + escAttr(roleKey) + '">生成三视图</button>' +
              '<button type="button" class="ai3d-mini-action danger" data-ai3d-action="delete_component_record" data-ai3d-scope="component_triview_prompt" data-ai3d-job-id="' + escAttr(job.job_id) + '" data-ai3d-role="' + escAttr(roleKey) + '">删除记录</button>' +
              '</div>';
          }
        }
        html += '</figcaption></figure>';
        return html;
      }
      var meta = '';
      var detailText = [item.subject_reason || '', item.subject_risk ? ('Risk: ' + item.subject_risk) : ''].filter(Boolean).join('\n');
      if (item.ai_recommended) meta += '<em>AI</em>';
      if (item.suitability_score) meta += '<small>score ' + esc(item.suitability_score) + '</small>';
      if (detailText) meta += '<button type="button" class="ai3d-info-dot" data-ai3d-info="' + escAttr(detailText) + '" aria-label="Show AI analysis">i</button>';
      var role = String(item.role || '');
      var regenAction = stepKey === 'triview' && job && job.job_id && ['front', 'front_left_45', 'front_right_45', 'side', 'back'].indexOf(role) >= 0
        ? '<button type="button" class="ai3d-mini-action" data-ai3d-action="regen_view" data-ai3d-job-id="' + escAttr(job.job_id) + '" data-ai3d-role="' + escAttr(role) + '">重生此图</button>'
        : '';
      var componentImageAction = '';
      if (stepKey === 'component_images' && job && job.job_id && role && role !== 'component_sheet') {
        componentImageAction = '<div class="ai3d-card-actions">' +
          '<button type="button" class="ai3d-mini-action" data-ai3d-action="component_images" data-ai3d-job-id="' + escAttr(job.job_id) + '" data-ai3d-role="' + escAttr(role) + '">重新生成</button>' +
          '<button type="button" class="ai3d-mini-action primary" data-ai3d-action="component_triview_prompts" data-ai3d-job-id="' + escAttr(job.job_id) + '" data-ai3d-role="' + escAttr(role) + '">生成三视图提示词</button>' +
          '<button type="button" class="ai3d-mini-action danger" data-ai3d-action="delete_component_record" data-ai3d-scope="component_image" data-ai3d-job-id="' + escAttr(job.job_id) + '" data-ai3d-role="' + escAttr(role) + '">删除记录</button>' +
          '</div>';
      }
      if (stepKey === 'component_triviews' && job && job.job_id) {
        var componentRole = String(item.part_role || role || '').replace(/_triview_sheet$/, '');
        var partIndex = String(item.part_index || '');
        if (componentRole || partIndex) {
          componentImageAction = '<div class="ai3d-card-actions">' +
            '<button type="button" class="ai3d-mini-action danger" data-ai3d-action="delete_component_record" data-ai3d-scope="component_triview" data-ai3d-job-id="' + escAttr(job.job_id) + '" data-ai3d-role="' + escAttr(componentRole) + '" data-ai3d-part-index="' + escAttr(partIndex) + '">删除记录</button>' +
            '</div>';
        }
      }
      return '<figure>' + (isImage && url ? previewImg(url, title, 'ai3d-previewable ai3d-step-preview') : '<div class="ai3d-step-file-icon">3D</div>') +
        '<figcaption><strong>' + esc(item.label || item.filename || item.format || '??') + '</strong><span class="ai3d-card-meta">' + meta + '</span>' + regenAction + componentImageAction + '</figcaption></figure>';
    }).join('') + '</div>';
  }

  function renderStepItems(step, job) {
    var groups = Array.isArray(step.groups) ? step.groups : [];
    if (groups.length) {
      var groupHtml = '<div class="ai3d-step-groups">' + groups.map(function(group) {
        var groupItems = Array.isArray(group.items) ? group.items : [];
        return '<div class="ai3d-step-group">' +
          '<div class="ai3d-step-group-head"><strong>' + esc(group.title || '结果分组') + '</strong>' +
          '<span>' + esc(group.summary || '') + '</span></div>' +
          renderStepThumbs(groupItems, 12, step.key || '', job) + '</div>';
      }).join('') + '</div>';
      if (Array.isArray(step.parts) && step.parts.length) {
        groupHtml += '<div class="ai3d-step-files">' + step.parts.map(function(part) {
          var files = Array.isArray(part.files) ? part.files : [];
          var partRole = String(part.role || '');
          var partIndex = String(part.part_index || '');
          var partDelete = job && job.job_id
            ? '<button type="button" class="ai3d-mini-action danger" data-ai3d-action="delete_component_record" data-ai3d-scope="part_3d" data-ai3d-job-id="' + escAttr(job.job_id) + '" data-ai3d-role="' + escAttr(partRole) + '" data-ai3d-part-index="' + escAttr(partIndex) + '">删除记录</button>'
            : '';
          return '<div class="ai3d-step-file"><strong>部件 ' + esc(part.part_index || '') + '</strong><span>' + esc(files.length + ' 个文件') + '</span>' + partDelete + '</div>';
        }).join('') + '</div>';
      }
      return groupHtml;
    }
    var items = Array.isArray(step.items) ? step.items : [];
    if (!items.length && Array.isArray(step.parts)) {
      return '<div class="ai3d-step-files">' + step.parts.map(function(part) {
        var files = Array.isArray(part.files) ? part.files : [];
        var partRole = String(part.role || '');
        var partIndex = String(part.part_index || '');
        var partDelete = job && job.job_id
          ? '<button type="button" class="ai3d-mini-action danger" data-ai3d-action="delete_component_record" data-ai3d-scope="part_3d" data-ai3d-job-id="' + escAttr(job.job_id) + '" data-ai3d-role="' + escAttr(partRole) + '" data-ai3d-part-index="' + escAttr(partIndex) + '">删除记录</button>'
          : '';
        return '<div class="ai3d-step-file"><strong>部件 ' + esc(part.part_index || '') + '</strong><span>' + esc(files.length + ' 个文件') + '</span>' + partDelete + '</div>';
      }).join('') + '</div>';
    }
    return renderStepThumbs(items, 12, step.key || '', job);
  }

  function stepActionButtonHtml(item, jobId) {
    return '<button type="button" class="ai3d-step-action' + (item.primary ? ' primary' : '') + '"' +
      ' data-ai3d-action="' + escAttr(item.action || '') + '"' +
      ' data-ai3d-job-id="' + escAttr(jobId || '') + '"' +
      (item.role ? ' data-ai3d-role="' + escAttr(item.role) + '"' : '') +
      (item.disabled ? ' disabled' : '') + '>' + esc(item.text || '执行') + '</button>';
  }

  function stepActionItems(step, job) {
    if (!step || !job || !job.job_id) return [];
    var f = actionFacts(job);
    var key = step.key || '';
    if (key === 'prompt') {
      return [{
        action: 'triview_prompt',
        text: '编辑提示词',
        disabled: !currentTriviewPrompt(job),
        primary: false
      }];
    }
    if (key === 'triview') {
      if (f.triviewFromReferenceSheet) {
        return [{ action: 'triview', text: '已提取参考板视角', disabled: true, primary: false }];
      }
      if (f.canRegenerateTriview) {
        var mode = (job.workflow_mode || (job.preprocessing && job.preprocessing.workflow_mode) || 'custom');
        if (mode === 'game_prop' && !f.hasTriview) {
          return [{
            action: 'triview_prompt',
            text: '编辑多视角提示词',
            disabled: false,
            primary: false
          }, {
            action: 'triview',
            text: '生成多视角',
            disabled: false,
            primary: true
          }];
        }
        if (mode === 'direct_multiview' && !f.hasTriview) {
          return [{
            action: 'triview',
            text: '识别裁切视角',
            disabled: false,
            primary: true
          }];
        }
        return [{
          action: 'triview',
          text: job.stage === 'triview_completed' ? '重新生成多视图' : '生成多视图',
          disabled: false,
          primary: !f.hasTriview
        }];
      }
      if (!f.hasTriview) return [{ action: 'triview', text: '等待预处理完成', disabled: true, primary: false }];
      return [];
    }
    if (key === 'base_model') {
      if (f.baseReady) return [{
        action: '3mf_base',
        text: '下载 3MF',
        disabled: false,
        primary: false
      }, {
        action: 'base',
        text: '重新生成 3D 模型',
        disabled: !f.canPreprocessed,
        primary: false
      }];
      if (!f.hasTriview) return [{ action: 'base', text: '先生成多视图', disabled: true, primary: false }];
      return [{
        action: 'base',
        text: '生成 3D 模型',
        disabled: !f.canPreprocessed,
        primary: true
      }];
    }
    if (key === 'components') {
      if (f.componentSplitMode) {
        return [{
          action: 'components',
          text: job.stage === 'component_split_completed' ? '重新生成拆件部件图' : '生成拆件部件图',
          disabled: !f.canRegenerateComponents,
          primary: !f.partFlowReady
        }];
      }
      if (!f.hasTriview) return [{ action: 'components', text: '先生成多视图', disabled: true, primary: false }];
      if (!f.baseReady) return [{ action: 'components', text: '先生成 3D 模型', disabled: true, primary: false }];
      return [{
        action: 'components',
        text: job.stage === 'component_split_completed' ? '重新生成部件输入图' : (f.isCharacter ? 'See-through 分层拆件' : '生成部件输入图'),
        disabled: !f.canRegenerateComponents,
        primary: false
      }];
    }
    if (key === 'component_prompts') {
      return [{
        action: 'components',
        text: f.componentPromptsReady ? '重新生成 GPT 拆件提示词' : '生成 GPT 拆件提示词',
        disabled: !f.canRegenerateComponents,
        primary: !f.componentPromptsReady
      }, {
        action: 'component_images',
        text: '一键补齐部件图',
        disabled: !f.componentPromptsReady || !f.canRegenerateComponents,
        primary: f.componentPromptsReady && !f.componentImagesReady
      }, {
        action: 'save_component_prompt',
        text: '保存全部提示词',
        disabled: !f.componentPromptsReady,
        primary: false
      }];
    }
    if (key === 'component_images') {
      return [{
        action: 'component_images',
        text: f.componentImagesReady ? '一键补齐缺失部件图' : '保存并补齐部件图',
        disabled: !f.componentPromptsReady || !f.canRegenerateComponents,
        primary: f.componentPromptsReady && !f.componentImagesReady
      }, {
        action: 'save_component_prompt',
        text: '保存全部提示词',
        disabled: !f.componentPromptsReady,
        primary: false
      }];
    }
    if (key === 'component_triview_prompts') {
      return [{
        action: 'component_triview_prompts',
        text: f.componentTriviewPromptsReady ? '重新生成三视图提示词' : '生成三视图提示词',
        disabled: !f.componentImagesReady || !f.canRegenerateComponents,
        primary: f.componentImagesReady && !f.componentTriviewPromptsReady
      }, {
        action: 'component_triviews',
        text: '一键补齐部件三视图',
        disabled: !f.componentTriviewPromptsReady || !f.canRegenerateComponents,
        primary: f.componentTriviewPromptsReady && !f.componentTriviewReady
      }, {
        action: 'save_component_prompt',
        text: '保存全部提示词',
        disabled: !f.componentTriviewPromptsReady,
        primary: false
      }];
    }
    if (key === 'component_triviews') {
      return [{
        action: 'component_triviews',
        text: f.componentTriviewReady ? '一键补齐缺失三视图' : '保存提示词并补齐三视图',
        disabled: !f.componentTriviewPromptsReady || !f.canRegenerateComponents,
        primary: f.componentTriviewPromptsReady && !f.componentTriviewReady
      }];
    }
    if (key === 'parts_3d') {
      if (f.componentSplitMode && !f.componentTriviewReady) return [{ action: 'parts', text: '先生成部件三视图', disabled: true, primary: false }];
      if (!f.partFlowReady) return [{ action: 'parts', text: '先生成部件输入图', disabled: true, primary: false }];
      if (!f.componentSplitMode && !f.baseReady) return [{ action: 'parts', text: '先生成 3D 模型', disabled: true, primary: false }];
      if (f.blockedPartBatch) return [{ action: 'parts', text: '拆件未通过质量门', disabled: true, primary: false }];
      var partActions = [{
        action: 'parts',
        text: f.componentSplitMode
          ? (f.partsReady ? '一键补齐缺失 3D 部件' : '生成 3D 部件')
          : (f.partsReady ? '重新生成/复用 3D 部件' : '生成 3D 部件'),
        disabled: !f.canPreprocessed || f.partBatchNeedsTriview || f.partBatchNeedsBase,
        primary: !f.partsReady
      }];
      if (f.partsReady) {
        partActions.push({
          action: '3mf_parts',
          text: '下载部件 3MF',
          disabled: false,
          primary: false
        });
      }
      return partActions;
    }
    if (key === 'assembly') {
      if (f.componentSplitMode) return [];
      if (!f.partFlowReady) return [];
      if (!f.partsReady) return [{ action: 'assemble', text: '先生成 3D 部件', disabled: true, primary: false }];
      return [{
        action: 'assemble',
        text: '合成最终模型',
        disabled: !f.canPreprocessed || f.blockedPartBatch || f.partBatchNeedsTriview || f.partBatchNeedsBase,
        primary: true
      }];
    }
    return [];
  }

  function renderStepActions(step, job) {
    var items = stepActionItems(step, job);
    if (!items.length) return '';
    return '<div class="ai3d-step-actions">' + items.map(function(item) {
      return stepActionButtonHtml(item, job && job.job_id ? job.job_id : '');
    }).join('') + '</div>';
  }

  function renderSteps(job) {
    var host = el('ai3dStepTimeline');
    if (!host) return;
    var steps = job && Array.isArray(job.steps) ? job.steps : [];
    if (!steps.length) {
      host.innerHTML = '<div class="ai3d-empty slim">提交任务后显示步骤进度。</div>';
      return;
    }
    host.innerHTML = steps.map(function(step, idx) {
      var cls = stepStatusClass(step.status);
      return '<div class="ai3d-step-row ' + escAttr(cls) + '">' +
        '<div class="ai3d-step-index">' + (idx + 1) + '</div>' +
        '<div class="ai3d-step-body"><div class="ai3d-step-title"><strong>' + esc(step.title || '') + '</strong>' +
        '<span class="ai3d-step-badge ' + escAttr(cls) + '">' + esc(stepStatusText(step.status)) + '</span></div>' +
        '<div class="ai3d-step-summary">' + esc(step.error || step.summary || '') + '</div>' +
        renderStepItems(step, job) + renderStepActions(step, job) + '</div></div>';
    }).join('');
  }

  function actionFacts(job) {
    var canPreprocessed = !!(job && job.job_id && (
      job.status === 'preprocessed' ||
      job.status === 'succeeded' ||
      job.status === 'failed' ||
      job.stage === 'failed' ||
      job.stage === 'triview_failed' ||
      job.stage === 'component_split_failed'
    ));
    var canRegenerateTriview = canPreprocessed && job && job.stage !== 'component_split_completed';
    var canRegenerateComponents = canPreprocessed;
    var preprocessing = job && job.preprocessing ? job.preprocessing : {};
    var workflow = String((job && job.workflow_mode) || preprocessing.workflow_mode || 'custom');
    var componentSplitMode = workflow === 'component_split';
    var isCharacter = !!(job && ['character_realistic', 'character_stylized'].indexOf(String(job.asset_template || '')) >= 0);
    var hasTriview = !!(preprocessing.triview_generated || (Array.isArray(preprocessing.triview_inputs) && preprocessing.triview_inputs.length >= 2));
    var triviewFromReferenceSheet = !!preprocessing.triview_from_reference_sheet;
    var requiresImageStage = !!(preprocessing.requires_image_stage_for_quality && !preprocessing.triview_generated && !preprocessing.component_split_generated);
    var cropReferenceOnly = preprocessing.component_reference_mode === 'crop_reference_only' || preprocessing.component_reference_mode === 'fidelity_crop';
    var failedComponents = !!((job && job.stage === 'component_split_failed') || preprocessing.component_quality_gate === 'failed');
    var blockedPartBatch = !!(job && job.strategy === 'part_batch' && (cropReferenceOnly || failedComponents) && !preprocessing.component_split_generated);
    var partBatchNeedsTriview = !!(job && job.strategy === 'part_batch' && !componentSplitMode && !hasTriview);
    var baseReady = hasBaseModel(job);
    var componentPromptsReady = !!(preprocessing.component_ai_plan && Array.isArray(preprocessing.component_ai_plan.parts) && preprocessing.component_ai_plan.parts.length);
    var componentImagesReady = !!preprocessing.component_split_generated;
    var componentTriviewPromptsReady = !!preprocessing.component_triview_prompts_ready;
    var componentTriviewReady = !!preprocessing.component_triview_generated;
    var partFlowReady = !!(job && job.strategy === 'part_batch' && preprocessing.component_split_generated);
    var partBatchNeedsBase = !!(partFlowReady && !componentSplitMode && !baseReady);
    var partsReady = has3dParts(job);
    var showBaseAction = !!(job && job.job_id && hasTriview && !baseReady);
    var showComponentAction = !!(job && job.job_id && hasTriview && baseReady);
    var showPartAction = !!(partFlowReady && baseReady);
    var showFinalAction = !!(partFlowReady && baseReady && partsReady);
    return {
      canPreprocessed: canPreprocessed,
      canRegenerateTriview: canRegenerateTriview,
      canRegenerateComponents: canRegenerateComponents,
      componentSplitMode: componentSplitMode,
      hasTriview: hasTriview,
      triviewFromReferenceSheet: triviewFromReferenceSheet,
      baseReady: baseReady,
      partFlowReady: partFlowReady,
      componentPromptsReady: componentPromptsReady,
      componentImagesReady: componentImagesReady,
      componentTriviewPromptsReady: componentTriviewPromptsReady,
      componentTriviewReady: componentTriviewReady,
      partsReady: partsReady,
      blockedPartBatch: blockedPartBatch,
      partBatchNeedsTriview: partBatchNeedsTriview,
      partBatchNeedsBase: partBatchNeedsBase,
      requiresImageStage: requiresImageStage,
      isCharacter: isCharacter,
      showBaseAction: showBaseAction,
      showComponentAction: showComponentAction,
      showPartAction: showPartAction,
      showFinalAction: showFinalAction
    };
  }

  function renderActions() {
    // Actions now live on step cards. This function remains as a compatibility no-op.
  }

  function flattenFiles(outputs) {
    if (!outputs) return [];
    var out = [];
    function push(file) {
      if (!file) return;
      var url = file.url || file.filename || JSON.stringify(file);
      for (var i = 0; i < out.length; i++) {
        if ((out[i].url || out[i].filename || '') === url) return;
      }
      out.push(file);
    }
    if (Array.isArray(outputs.files)) {
      outputs.files.forEach(function(file) {
        push(file);
      });
    }
    if (outputs.base && Array.isArray(outputs.base.files)) {
      outputs.base.files.forEach(function(file) {
        push(file);
      });
    }
    (outputs.parts || []).forEach(function(part) {
      (part.files || []).forEach(function(file) {
        push(Object.assign({ part_index: part.part_index, source: part.source }, file));
      });
    });
    return out;
  }

  function hasBaseModel(job) {
    var outputs = job && job.outputs ? job.outputs : {};
    var baseFiles = outputs.base && Array.isArray(outputs.base.files) ? outputs.base.files : [];
    if (baseFiles.some(function(file) { return isGlbFile(file); })) return true;
    var files = Array.isArray(outputs.files) ? outputs.files : [];
    return files.some(function(file) { return file && file.base_model && isGlbFile(file); });
  }

  function has3dParts(job) {
    var outputs = job && job.outputs ? job.outputs : {};
    var parts = Array.isArray(outputs.parts) ? outputs.parts : [];
    return parts.some(function(part) {
      return part && Array.isArray(part.files) && part.files.some(isGlbFile);
    });
  }

  function isGlbFile(file) {
    return String(file && file.format || '').toLowerCase() === 'glb' || /\.glb$/i.test(file && file.filename || '');
  }

  function modelFileLabel(file) {
    if (!file) return '3D 模型';
    if (file.label) return file.label;
    if (file.assembled) return '完整自动组装 GLB';
    return file.part_index ? '部件 ' + file.part_index : (file.filename || '3D 模型');
  }

  function previewForModel(files, glb) {
    if (!glb) return null;
    return files.filter(function(file) {
      return file && file.kind === 'preview' && String(file.part_index || '') === String(glb.part_index || '');
    })[0] || files.filter(function(file) { return file && file.kind === 'preview'; })[0] || null;
  }

  function modelPreviewButton(glb, files, active, job) {
    var poster = previewForModel(files, glb);
    var label = modelFileLabel(glb);
    var modelUrl = assetUrl(glb.url || '', job, glb);
    var posterUrl = poster && poster.url ? assetUrl(poster.url, job, poster) : '';
    return '<button type="button" class="ai3d-model-switch-btn' + (active ? ' active' : '') + '"' +
      ' data-ai3d-model-url="' + escAttr(modelUrl) + '"' +
      ' data-ai3d-model-raw-url="' + escAttr(glb.url || '') + '"' +
      ' data-ai3d-model-poster="' + escAttr(posterUrl) + '"' +
      ' data-ai3d-model-download="' + escAttr(modelUrl) + '"' +
      ' data-ai3d-model-label="' + escAttr(label) + '">' + esc(label) + '</button>';
  }

  function modelOutputCard(glb, files, job) {
    var poster = previewForModel(files, glb);
    var label = modelFileLabel(glb);
    var modelUrl = assetUrl(glb.url || '', job, glb);
    var posterUrl = poster && poster.url ? assetUrl(poster.url, job, poster) : '';
    return '<article class="ai3d-output-card">' +
      '<button type="button" class="ai3d-output-preview-btn" data-ai3d-model-url="' + escAttr(modelUrl) + '"' +
      ' data-ai3d-model-raw-url="' + escAttr(glb.url || '') + '"' +
      ' data-ai3d-model-poster="' + escAttr(posterUrl) + '"' +
      ' data-ai3d-model-download="' + escAttr(modelUrl) + '"' +
      ' data-ai3d-model-label="' + escAttr(label) + '">' +
      '<span>' + esc(label) + '</span><small>点击预览 3D</small></button>' +
      '<a class="ai3d-output-download" href="' + escAttr(modelUrl) + '" target="_blank" rel="noopener">下载 GLB</a>' +
      '</article>';
  }

  function selectedPreviewGlb(job, glbs) {
    if (!Array.isArray(glbs) || !glbs.length) return null;
    var jobId = job && job.job_id ? job.job_id : state.jobId;
    var selected = jobId ? state.previewModelByJob[jobId] : '';
    if (selected) {
      var found = glbs.filter(function(file) {
        var raw = file && file.url ? String(file.url) : '';
        return raw === selected || api(raw) === selected;
      })[0];
      if (found) return found;
    }
    return glbs[0];
  }

  function renderPreview(job) {
    var host = el('ai3dPreview');
    if (!host) return;
    host.hidden = true;
    var effectiveOutputs = job.outputs && Object.keys(job.outputs || {}).length ? job.outputs : { parts: job.subtasks || [] };
    var files = flattenFiles(effectiveOutputs);
    var glbs = files.filter(isGlbFile);
    var glb = selectedPreviewGlb(job, glbs);
    var preview = previewForModel(files, glb);
    if (glbs.length) {
      host.hidden = false;
      host.innerHTML = '<div class="ai3d-output-compact">' +
        '<div class="ai3d-output-compact-head"><strong>3D 输出</strong><span>' + esc(glbs.length + ' 个 GLB，点击部件弹窗预览') + '</span></div>' +
        '<div class="ai3d-output-card-grid">' + glbs.map(function(item) { return modelOutputCard(item, files, job); }).join('') + '</div>' +
        '</div>';
      return;
    }
    if (preview && preview.url) {
      host.hidden = false;
      host.innerHTML = '<div class="ai3d-output-compact">' +
        '<div class="ai3d-output-compact-head"><strong>预览图</strong><span>点击弹窗查看</span></div>' +
        previewImg(preview.url, '3D 预览', 'ai3d-previewable ai3d-hero-preview') +
        '</div>';
      return;
    }
    host.innerHTML = '';
  }

  function inputTitle(job) {
    if (job && job.strategy === 'part_batch' && job.preprocessing && job.preprocessing.component_split_generated) return '2D 部件输入图';
    if (job && job.stage === 'component_split_completed') return '2D 部件输入图';
    if (job && job.stage === 'component_references_ready') return '当前可生成输入';
    if (job && job.stage === 'triview_completed') return '多视图输入';
    return '当前主参考图';
  }

  function inputKindLabel(job, item) {
    if (job && job.strategy === 'part_batch' && job.preprocessing && job.preprocessing.component_split_generated) return '2D 部件输入图';
    if (job && job.stage === 'component_split_completed') return '2D 部件输入图';
    if (job && job.stage === 'component_references_ready') return (item && ['front', 'front_left_45', 'front_right_45', 'side', 'back'].indexOf(item.role) >= 0) ? 'AI 多视图' : '当前参考';
    if (job && job.stage === 'triview_completed') return 'AI 多视图';
    if (item && item.crop_applied) return '主体裁切';
    if (item && item.generated) return 'AI 理解候选';
    return '原始参考';
  }

  function startGeneratedJob(trigger, explicitJobId) {
    var ctx = actionContext(trigger, 'ai3dGenerateBtn', explicitJobId);
    var btn = ctx.btn;
    var jobId = ctx.jobId;
    if (!jobId) return;
    var current = (state.jobs || []).filter(function(job) { return job && job.job_id === jobId; })[0] || {};
    var prep = current.preprocessing || {};
    var isPartFinal = !!(current.strategy === 'part_batch' && prep.component_split_generated);
    setBusy(btn, true, '启动中...');
    setMsg(isPartFinal ?
      '正在读取完整 3D 模型和已有 3D 部件，合成最终模型；这一步不重新生成部件。' :
      '正在启动 Meshy 3D 生成：多视图会走 Multi-Image to 3D。',
      false);
    var endpoint = isPartFinal ? '/assemble' : '/generate';
    fetch(api('/api/ai-3d-model/jobs/' + encodeURIComponent(jobId) + endpoint), {
      method: 'POST',
      headers: headers({ 'Content-Type': 'application/json' }),
      body: '{}'
    })
      .then(function(resp) { return resp.json().then(function(data) { return { ok: resp.ok, data: data }; }); })
      .then(function(x) {
        if (!x.ok || !x.data || x.data.ok === false) throw new Error(parseError(x.data, '启动生成失败'));
        rememberJob(x.data.job && x.data.job.job_id);
        renderJob(x.data.job || {});
        loadJobs(false);
        startPolling();
      })
      .catch(function(err) {
        setMsg(err && err.message ? err.message : '启动生成失败', true);
      })
      .finally(function() {
        setBusy(btn, false);
      });
  }

  function startBaseModelJob(trigger, explicitJobId) {
    var ctx = actionContext(trigger, 'ai3dBaseBtn', explicitJobId);
    var btn = ctx.btn;
    var jobId = ctx.jobId;
    if (!jobId) return;
    setBusy(btn, true, '生成中...');
    setMsg('正在用多视图生成完整 3D 模型；如果后续需要增强局部，再进入部件生成和最终合成。', false);
    fetch(api('/api/ai-3d-model/jobs/' + encodeURIComponent(jobId) + '/base-model'), {
      method: 'POST',
      headers: headers({ 'Content-Type': 'application/json' }),
      body: '{}'
    })
      .then(function(resp) { return resp.json().then(function(data) { return { ok: resp.ok, data: data }; }); })
      .then(function(x) {
        if (!x.ok || !x.data || x.data.ok === false) throw new Error(parseError(x.data, '3D 模型生成启动失败'));
        rememberJob(x.data.job && x.data.job.job_id);
        renderJob(x.data.job || {});
        loadJobs(false);
        startPolling();
      })
      .catch(function(err) {
        setMsg(err && err.message ? err.message : '3D 模型生成启动失败', true);
      })
      .finally(function() {
        setBusy(btn, false);
      });
  }

  function startPartModelsJob(trigger, explicitJobId) {
    var ctx = actionContext(trigger, 'ai3dPartsBtn', explicitJobId);
    var btn = ctx.btn;
    var jobId = ctx.jobId;
    if (!jobId) return;
    var current = state.currentJob || {};
    var prep = current.preprocessing || {};
    var componentSplitMode = String(current.workflow_mode || prep.workflow_mode || '') === 'component_split';
    var fd = new FormData();
    fd.append('model', el('ai3dImageModel') ? el('ai3dImageModel').value : 'openai/gpt-image-2');
    setBusy(btn, true, '生成中...');
    setMsg(
      componentSplitMode
        ? '正在先生成每个部件三视图，再逐个送 Meshy 生成 3D 部件。'
        : '正在逐个生成 3D 部件；没有变化的部件会按输入指纹复用，生成完后再点“合成最终模型”。',
      false
    );
    fetch(api('/api/ai-3d-model/jobs/' + encodeURIComponent(jobId) + '/parts-3d'), {
      method: 'POST',
      headers: formHeaders(),
      body: fd
    })
      .then(function(resp) { return resp.json().then(function(data) { return { ok: resp.ok, data: data }; }); })
      .then(function(x) {
        if (!x.ok || !x.data || x.data.ok === false) throw new Error(parseError(x.data, '3D 部件生成启动失败'));
        rememberJob(x.data.job && x.data.job.job_id);
        renderJob(x.data.job || {});
        loadJobs(false);
        startPolling();
      })
      .catch(function(err) {
        setMsg(err && err.message ? err.message : '3D 部件生成启动失败', true);
      })
      .finally(function() {
        setBusy(btn, false);
      });
  }

  function startTriviewJob(trigger, explicitJobId) {
    var ctx = actionContext(trigger, 'ai3dTriviewBtn', explicitJobId);
    var btn = ctx.btn;
    var jobId = ctx.jobId;
    if (!jobId) return;
    var fd = new FormData();
    fd.append('model', el('ai3dImageModel') ? el('ai3dImageModel').value : 'openai/gpt-image-2');
    fd.append('resolution', '4K');
    fd.append('quality', 'high');
    fd.append('output_format', 'png');
    setBusy(btn, true, '生成中...');
    setMsg('正在用图片模型生成多视图；虚拟/提示词任务会生成正面、45°、侧面和背面，这一步不调用 Meshy。', false);
    fetch(api('/api/ai-3d-model/jobs/' + encodeURIComponent(jobId) + '/triview'), {
      method: 'POST',
      headers: formHeaders(),
      body: fd
    })
      .then(function(resp) { return resp.json().then(function(data) { return { ok: resp.ok, data: data }; }); })
      .then(function(x) {
        if (!x.ok || !x.data || x.data.ok === false) throw new Error(parseError(x.data, '多视图生成启动失败'));
        rememberJob(x.data.job && x.data.job.job_id);
        renderJob(x.data.job || {});
        loadJobs(false);
        startPolling();
      })
      .catch(function(err) {
        var msg = err && err.message ? err.message : '多视图生成启动失败';
        if (/超时|timeout|504/i.test(msg)) msg += '。任务进度已保留，可稍后用当前模型重试。';
        setMsg(msg, true);
      })
      .finally(function() {
        setBusy(btn, false);
      });
  }

  function collectComponentPromptPayload(roleFilter) {
    var byRole = {};
    var nodes = document.querySelectorAll('[data-ai3d-component-field][data-ai3d-role]');
    Array.prototype.forEach.call(nodes, function(node) {
      var role = node.getAttribute('data-ai3d-role') || '';
      var field = node.getAttribute('data-ai3d-component-field') || '';
      if (!role || !field) return;
      if (roleFilter && role !== roleFilter) return;
      if (!byRole[role]) byRole[role] = { role: role };
      byRole[role][field] = node.value || '';
      var fig = node.closest('[data-ai3d-part-role]');
      if (fig) {
        var title = fig.querySelector('figcaption strong');
        if (title) byRole[role].label = title.textContent || '';
      }
    });
    return Object.keys(byRole).map(function(role) { return byRole[role]; });
  }

  function saveComponentPrompts(jobId, roleFilter) {
    var parts = collectComponentPromptPayload(roleFilter || '');
    if (!parts.length) return Promise.resolve();
    var fd = new FormData();
    fd.append('parts_json', JSON.stringify(parts));
    return fetch(api('/api/ai-3d-model/jobs/' + encodeURIComponent(jobId) + '/component-prompts'), {
      method: 'POST',
      headers: formHeaders(),
      body: fd
    }).then(function(resp) {
      return resp.json().then(function(data) {
        if (!resp.ok || !data || data.ok === false) throw new Error(parseError(data, '保存拆件提示词失败'));
        if (data.job) renderJob(data.job);
        return data;
      });
    });
  }

  function saveComponentPromptsAction(trigger, explicitJobId) {
    var ctx = actionContext(trigger, '', explicitJobId);
    var btn = ctx.btn;
    var jobId = ctx.jobId;
    if (!jobId) return;
    var role = btn && btn.dataset ? (btn.dataset.ai3dRole || btn.getAttribute('data-ai3d-role') || '') : '';
    setBusy(btn, true, '保存中...');
    saveComponentPrompts(jobId, role).then(function(data) {
      if (data && data.job) {
        rememberJob(data.job.job_id || jobId);
        renderJob(data.job);
        upsertJob(data.job);
      }
      setMsg(role ? '已保存当前部件提示词。' : '已保存全部拆件提示词。', false);
    }).catch(function(err) {
      setMsg(err && err.message ? err.message : '保存拆件提示词失败', true);
    }).finally(function() {
      setBusy(btn, false);
    });
  }

  function deleteComponentRecord(trigger, explicitJobId) {
    var ctx = actionContext(trigger, '', explicitJobId);
    var btn = ctx.btn;
    var jobId = ctx.jobId;
    if (!jobId) return;
    var scope = btn && btn.dataset ? (btn.dataset.ai3dScope || btn.getAttribute('data-ai3d-scope') || '') : '';
    var role = btn && btn.dataset ? (btn.dataset.ai3dRole || btn.getAttribute('data-ai3d-role') || '') : '';
    var partIndex = btn && btn.dataset ? (btn.dataset.ai3dPartIndex || btn.getAttribute('data-ai3d-part-index') || '') : '';
    var fd = new FormData();
    fd.append('scope', scope);
    fd.append('role', role);
    fd.append('part_index', partIndex || '0');
    setBusy(btn, true, '删除中...');
    fetch(api('/api/ai-3d-model/jobs/' + encodeURIComponent(jobId) + '/component-record/delete'), {
      method: 'POST',
      headers: formHeaders(),
      body: fd
    }).then(function(resp) {
      return resp.json().then(function(data) { return { ok: resp.ok, data: data }; });
    }).then(function(x) {
      if (!x.ok || !x.data || x.data.ok === false) throw new Error(parseError(x.data, '删除记录失败'));
      rememberJob(x.data.job && x.data.job.job_id);
      renderJob(x.data.job || {});
      upsertJob(x.data.job || {});
      loadJobs(false);
      setMsg('已删除记录；一键生成会补齐这个缺失项。', false);
    }).catch(function(err) {
      setMsg(err && err.message ? err.message : '删除记录失败', true);
    }).finally(function() {
      setBusy(btn, false);
    });
  }

  function startComponentImagesJob(trigger, explicitJobId) {
    var ctx = actionContext(trigger, 'ai3dComponentsBtn', explicitJobId);
    var btn = ctx.btn;
    var jobId = ctx.jobId;
    if (!jobId) return;
    var role = btn && btn.dataset ? (btn.dataset.ai3dRole || btn.getAttribute('data-ai3d-role') || '') : '';
    var fd = new FormData();
    fd.append('model', el('ai3dImageModel') ? el('ai3dImageModel').value : 'openai/gpt-image-2');
    if (role) fd.append('role', role);
    setBusy(btn, true, '生成中...');
    setMsg(role ? '正在保存当前部件提示词，并生成该部件图片。' : '正在保存拆件提示词，并按提示词生成孤立部件图；这一步不调用 Meshy。', false);
    saveComponentPrompts(jobId, role).then(function() {
      return fetch(api('/api/ai-3d-model/jobs/' + encodeURIComponent(jobId) + '/component-images'), {
        method: 'POST',
        headers: formHeaders(),
        body: fd
      }).then(function(resp) { return resp.json().then(function(data) { return { ok: resp.ok, data: data }; }); });
    }).then(function(x) {
      if (!x.ok || !x.data || x.data.ok === false) throw new Error(parseError(x.data, '部件图生成启动失败'));
      rememberJob(x.data.job && x.data.job.job_id);
      renderJob(x.data.job || {});
      loadJobs(false);
      startPolling();
    }).catch(function(err) {
      setMsg(err && err.message ? err.message : '部件图生成启动失败', true);
    }).finally(function() {
      setBusy(btn, false);
    });
  }

  function startComponentTriviewPromptsJob(trigger, explicitJobId) {
    var ctx = actionContext(trigger, 'ai3dPartsBtn', explicitJobId);
    var btn = ctx.btn;
    var jobId = ctx.jobId;
    if (!jobId) return;
    var role = btn && btn.dataset ? (btn.dataset.ai3dRole || btn.getAttribute('data-ai3d-role') || '') : '';
    var fd = new FormData();
    if (role) fd.append('role', role);
    setBusy(btn, true, '规划中...');
    setMsg(role ? '正在让 GPT 为当前部件规划三视图提示词。' : '正在让 GPT 根据原图和部件图规划每个部件的三视图提示词。', false);
    fetch(api('/api/ai-3d-model/jobs/' + encodeURIComponent(jobId) + '/component-triview-prompts'), {
      method: 'POST',
      headers: formHeaders(),
      body: fd
    }).then(function(resp) {
      return resp.json().then(function(data) { return { ok: resp.ok, data: data }; });
    }).then(function(x) {
      if (!x.ok || !x.data || x.data.ok === false) throw new Error(parseError(x.data, '三视图提示词生成启动失败'));
      rememberJob(x.data.job && x.data.job.job_id);
      renderJob(x.data.job || {});
      loadJobs(false);
      startPolling();
    }).catch(function(err) {
      setMsg(err && err.message ? err.message : '三视图提示词生成启动失败', true);
    }).finally(function() {
      setBusy(btn, false);
    });
  }

  function startComponentTriviewsJob(trigger, explicitJobId) {
    var ctx = actionContext(trigger, 'ai3dPartsBtn', explicitJobId);
    var btn = ctx.btn;
    var jobId = ctx.jobId;
    if (!jobId) return;
    var role = btn && btn.dataset ? (btn.dataset.ai3dRole || btn.getAttribute('data-ai3d-role') || '') : '';
    var fd = new FormData();
    fd.append('model', el('ai3dImageModel') ? el('ai3dImageModel').value : 'openai/gpt-image-2');
    if (role) fd.append('role', role);
    setBusy(btn, true, '生成中...');
    setMsg(role ? '正在保存当前部件三视图提示词，并生成该部件三视图。' : '正在保存三视图提示词，并按提示词生成每个部件的正面/左前45/右前45图片；这一步不调用 Meshy。', false);
    saveComponentPrompts(jobId, role).then(function() {
      return fetch(api('/api/ai-3d-model/jobs/' + encodeURIComponent(jobId) + '/component-triviews'), {
        method: 'POST',
        headers: formHeaders(),
        body: fd
      }).then(function(resp) { return resp.json().then(function(data) { return { ok: resp.ok, data: data }; }); });
    }).then(function(x) {
      if (!x.ok || !x.data || x.data.ok === false) throw new Error(parseError(x.data, '部件三视图生成启动失败'));
      rememberJob(x.data.job && x.data.job.job_id);
      renderJob(x.data.job || {});
      loadJobs(false);
      startPolling();
    }).catch(function(err) {
      setMsg(err && err.message ? err.message : '部件三视图生成启动失败', true);
    }).finally(function() {
      setBusy(btn, false);
    });
  }

  function startComponentsJob(trigger, explicitJobId) {
    var ctx = actionContext(trigger, 'ai3dComponentsBtn', explicitJobId);
    var btn = ctx.btn;
    var jobId = ctx.jobId;
    if (!jobId) return;
    var fd = new FormData();
    fd.append('model', el('ai3dImageModel') ? el('ai3dImageModel').value : 'openai/gpt-image-2');
    fd.append('resolution', '4K');
    fd.append('quality', 'high');
    fd.append('output_format', 'png');
    var current = state.currentJob || {};
    var prep = current.preprocessing || {};
    var componentSplitMode = String(current.workflow_mode || prep.workflow_mode || '') === 'component_split';
    setBusy(btn, true, '分离中...');
    setMsg(
      componentSplitMode
        ? '正在让 GPT 规划拆件提示词，并用 GPT Image 2 生成孤立部件图；这一步不调用 Meshy。'
        : '正在生成 2D 部件输入图：角色优先走 see-through PSD 语义分层；通过后可单独生成 3D 部件，再合成最终模型。',
      false
    );
    fetch(api('/api/ai-3d-model/jobs/' + encodeURIComponent(jobId) + '/components'), {
      method: 'POST',
      headers: formHeaders(),
      body: fd
    })
      .then(function(resp) { return resp.json().then(function(data) { return { ok: resp.ok, data: data }; }); })
      .then(function(x) {
        if (!x.ok || !x.data || x.data.ok === false) throw new Error(parseError(x.data, 'AI 部件分离启动失败'));
        rememberJob(x.data.job && x.data.job.job_id);
        renderJob(x.data.job || {});
        loadJobs(false);
        startPolling();
      })
      .catch(function(err) {
        var msg = err && err.message ? err.message : 'AI 部件分离启动失败';
        if (/超时|timeout|504/i.test(msg)) msg += '。任务进度已保留，可稍后用当前模型重试。';
        setMsg(msg, true);
      })
      .finally(function() {
        setBusy(btn, false);
      });
  }

  function start3mfExport(trigger, explicitJobId, scope) {
    var ctx = actionContext(trigger, '', explicitJobId);
    var btn = ctx.btn;
    var jobId = ctx.jobId;
    if (!jobId) return;
    var scopeValue = scope || 'all';
    var fd = new FormData();
    fd.append('scope', scopeValue);
    setBusy(btn, true, '导出中...');
    setMsg('正在检查模型并导出 3MF；不合格时会下载检查报告。', false);
    fetch(api('/api/ai-3d-model/jobs/' + encodeURIComponent(jobId) + '/3mf'), {
      method: 'POST',
      headers: formHeaders(),
      body: fd
    })
      .then(function(resp) { return resp.json().then(function(data) { return { ok: resp.ok, data: data }; }); })
      .then(function(x) {
        if (!x.ok || !x.data || x.data.ok === false) throw new Error(parseError(x.data, '3MF 导出失败'));
        var job = x.data.job || {};
        if (job.job_id) {
          rememberJob(job.job_id);
          renderJob(job);
          upsertJob(job);
        }
        var localDir = x.data.local_dir || '';
        var openDir = Promise.resolve();
        if (x.data.open_dir_url) {
          var openFd = new FormData();
          openFd.append('scope', scopeValue);
          openDir = fetch(api('/api/ai-3d-model/jobs/' + encodeURIComponent(jobId) + '/3mf/open-dir'), {
            method: 'POST',
            headers: formHeaders(),
            body: openFd
          }).then(function(openResp) {
            return openResp.json().then(function(openData) {
              if (!openResp.ok || !openData || openData.ok === false) throw new Error(parseError(openData, '打开 3MF 目录失败'));
              return openData;
            });
          });
        }
        return openDir.then(function() {
          if (x.data.passed) {
            setMsg('3MF 已导出，已打开本地目录：' + localDir, false);
          } else {
            setMsg('3MF 检查未通过，已打开检查报告所在目录：' + localDir, true);
          }
        });
      })
      .catch(function(err) {
        setMsg(err && err.message ? err.message : '3MF 导出失败', true);
      })
      .finally(function() {
        setBusy(btn, false);
      });
  }

  function renderMetrics(job) {
    var host = el('ai3dMetrics');
    if (!host) return;
    var m = job.mesh_metrics || {};
    var rows = [];
    if (m.vertex_count) rows.push(['顶点', m.vertex_count]);
    if (m.triangle_count) rows.push(['三角面', m.triangle_count]);
    if (m.mesh_count) rows.push(['Mesh', m.mesh_count]);
    if (m.material_count != null) rows.push(['材质', m.material_count]);
    if (m.file_size) rows.push(['GLB 大小', formatSize(m.file_size)]);
    if (!rows.length) {
      host.innerHTML = '';
      return;
    }
    host.innerHTML = rows.map(function(row) {
      return '<div><strong>' + esc(row[1]) + '</strong><span>' + esc(row[0]) + '</span></div>';
    }).join('');
  }

  function renderOutputs(job) {
    var host = el('ai3dOutputs');
    if (!host) return;
    host.innerHTML = '';
  }

  function bind() {
    var input = el('ai3dFiles');
    if (input && !input._ai3dBound) {
      input._ai3dBound = true;
      input.addEventListener('change', handleGeneralFileChange);
    }
    var fileList = el('ai3dFileList');
    if (fileList && !fileList._ai3dBound) {
      fileList._ai3dBound = true;
      fileList.addEventListener('click', function(evt) {
        var removeFile = evt.target.closest('[data-ai3d-remove-file]');
        if (removeFile) {
          removeUploadedFile(removeFile.getAttribute('data-ai3d-remove-file'));
          return;
        }
        var removeSlot = evt.target.closest('[data-ai3d-remove-slot]');
        if (removeSlot) {
          clearRealObjectSlot(removeSlot.getAttribute('data-ai3d-remove-slot') || '');
        }
      });
    }
    var workflow = el('ai3dWorkflowMode');
    if (workflow && !workflow._ai3dBound) {
      workflow._ai3dBound = true;
      workflow.addEventListener('change', applyWorkflowModeDefaults);
    }
    Array.prototype.slice.call(document.querySelectorAll('#ai3dRealObjectSlots input[type="file"]')).forEach(function(slotInput) {
      if (slotInput._ai3dBound) return;
      slotInput._ai3dBound = true;
      slotInput.addEventListener('change', renderFiles);
    });
    var form = el('ai3dForm');
    if (form && !form._ai3dBound) {
      form._ai3dBound = true;
      form.addEventListener('submit', submitJob);
    }
    var back = el('ai3dBackBtn');
    if (back && !back._ai3dBound) {
      back._ai3dBound = true;
      back.addEventListener('click', function() {
        if (typeof window.showLobsterView === 'function') {
          window.showLobsterView('skill-store', document.querySelector('.nav-left-item[data-view="skill-store"]')).catch(function() {});
        } else {
          var nav = document.querySelector('.nav-left-item[data-view="skill-store"]');
          if (nav) nav.click();
        }
      });
    }
    var refresh = el('ai3dRefreshConfigBtn');
    if (refresh && !refresh._ai3dBound) {
      refresh._ai3dBound = true;
      refresh.addEventListener('click', function() {
        loadConfig();
        loadJobs(true);
        if (state.jobId) pollJob();
      });
    }
    var refreshJobs = el('ai3dRefreshJobsBtn');
    if (refreshJobs && !refreshJobs._ai3dBound) {
      refreshJobs._ai3dBound = true;
      refreshJobs.addEventListener('click', function() { loadJobs(true); });
    }
    var createBtn = el('ai3dCreateJobBtn');
    if (createBtn && !createBtn._ai3dBound) {
      createBtn._ai3dBound = true;
      createBtn.addEventListener('click', openCreateModal);
    }
    var historyBtn = el('ai3dHistoryBtn');
    if (historyBtn && !historyBtn._ai3dBound) {
      historyBtn._ai3dBound = true;
      historyBtn.addEventListener('click', openHistoryModal);
    }
    ['ai3dCreateModalClose', 'ai3dCreateModalCancel'].forEach(function(id) {
      var closeCreateBtn = el(id);
      if (closeCreateBtn && !closeCreateBtn._ai3dBound) {
        closeCreateBtn._ai3dBound = true;
        closeCreateBtn.addEventListener('click', closeCreateModal);
      }
    });
    var createModal = el('ai3dCreateModal');
    if (createModal && !createModal._ai3dBound) {
      createModal._ai3dBound = true;
      createModal.addEventListener('click', function(evt) {
        if (evt.target && evt.target.hasAttribute('data-ai3d-create-close')) closeCreateModal();
      });
    }
    var historyModal = el('ai3dHistoryModal');
    if (historyModal && !historyModal._ai3dBound) {
      historyModal._ai3dBound = true;
      historyModal.addEventListener('click', function(evt) {
        if (evt.target && evt.target.hasAttribute('data-ai3d-history-close')) closeHistoryModal();
      });
    }
    var closeHistoryBtn = el('ai3dHistoryModalClose');
    if (closeHistoryBtn && !closeHistoryBtn._ai3dBound) {
      closeHistoryBtn._ai3dBound = true;
      closeHistoryBtn.addEventListener('click', closeHistoryModal);
    }
    var currentDownload = el('ai3dDownloadCurrentBtn');
    if (currentDownload && !currentDownload._ai3dBound) {
      currentDownload._ai3dBound = true;
      currentDownload.addEventListener('click', function(evt) {
        if (currentDownload.getAttribute('aria-disabled') === 'true' || currentDownload.classList.contains('disabled')) {
          evt.preventDefault();
          setMsg('请先选择一个任务，再下载资源包。', true);
          return;
        }
        evt.preventDefault();
        triggerJobDownload(state.currentJob || {});
      });
    }
    Array.prototype.slice.call(document.querySelectorAll(
      '#ai3dWorkflowMode, #ai3dTemplate, #ai3dReferenceStrength, #ai3dStrategy, #ai3dQuality, #ai3dAutoDecompose, #ai3dMaxParts, #ai3dPreprocessOnly, #ai3dImageModel, input[name="format"]'
    )).forEach(function(inputNode) {
      if (inputNode._ai3dParamBound) return;
      inputNode._ai3dParamBound = true;
      inputNode.addEventListener('change', updateParamSummary);
    });
    var jobList = el('ai3dJobList');
    if (jobList && !jobList._ai3dBound) {
      jobList._ai3dBound = true;
      jobList.addEventListener('click', function(evt) {
        var btn = evt.target.closest('.ai3d-job-item[data-ai3d-job-id]');
        if (btn) selectJob(btn.getAttribute('data-ai3d-job-id') || '');
      });
    }
    var jobPager = el('ai3dJobPager');
    if (jobPager && !jobPager._ai3dBound) {
      jobPager._ai3dBound = true;
      jobPager.addEventListener('click', function(evt) {
        var btn = evt.target.closest('[data-ai3d-page]');
        if (!btn || btn.disabled) return;
        var dir = btn.getAttribute('data-ai3d-page') || '';
        if (dir === 'prev') state.jobPage -= 1;
        else if (dir === 'next') state.jobPage += 1;
        clampJobPage();
        renderJobList();
      });
    }
    var stepTimeline = el('ai3dStepTimeline');
    if (stepTimeline && !stepTimeline._ai3dBound) {
      stepTimeline._ai3dBound = true;
      stepTimeline.addEventListener('click', function(evt) {
        var infoBtn = evt.target.closest('[data-ai3d-info]');
        if (infoBtn) {
          evt.preventDefault();
          if (infoBtn.classList.contains('active')) closeInfoBubble();
          else openInfoBubble(infoBtn, true);
          return;
        }
        var actionBtn = evt.target.closest('[data-ai3d-action]');
        if (!actionBtn) return;
        evt.preventDefault();
        var action = actionBtn.getAttribute('data-ai3d-action') || '';
        var jobId = actionBtn.getAttribute('data-ai3d-job-id') || '';
        if (action === 'triview') startTriviewJob(actionBtn, jobId);
        else if (action === 'triview_prompt') editTriviewPrompt(jobId);
        else if (action === 'regen_view') regenerateTriviewView(jobId, actionBtn.getAttribute('data-ai3d-role') || '');
        else if (action === 'base') startBaseModelJob(actionBtn, jobId);
        else if (action === 'components') startComponentsJob(actionBtn, jobId);
        else if (action === 'save_component_prompt') saveComponentPromptsAction(actionBtn, jobId);
        else if (action === 'delete_component_record') deleteComponentRecord(actionBtn, jobId);
        else if (action === 'component_images') startComponentImagesJob(actionBtn, jobId);
        else if (action === 'component_triview_prompts') startComponentTriviewPromptsJob(actionBtn, jobId);
        else if (action === 'component_triviews') startComponentTriviewsJob(actionBtn, jobId);
        else if (action === 'parts') startPartModelsJob(actionBtn, jobId);
        else if (action === 'assemble') startGeneratedJob(actionBtn, jobId);
        else if (action === '3mf_base') start3mfExport(actionBtn, jobId, 'base');
        else if (action === '3mf_parts') start3mfExport(actionBtn, jobId, 'parts');
        else if (action === '3mf_final') start3mfExport(actionBtn, jobId, 'final');
      });
      stepTimeline.addEventListener('mouseover', function(evt) {
        var infoBtn = evt.target.closest('[data-ai3d-info]');
        if (infoBtn && !state.infoBubblePinned) openInfoBubble(infoBtn, false);
      });
      stepTimeline.addEventListener('mouseout', function(evt) {
        if (!state.infoBubblePinned && evt.target.closest('[data-ai3d-info]')) closeInfoBubble();
      });
      stepTimeline.addEventListener('focusin', function(evt) {
        var infoBtn = evt.target.closest('[data-ai3d-info]');
        if (infoBtn && !state.infoBubblePinned) openInfoBubble(infoBtn, false);
      });
    }
    var gen = el('ai3dGenerateBtn');
    if (gen && !gen._ai3dBound) {
      gen._ai3dBound = true;
      gen.addEventListener('click', startGeneratedJob);
    }
    var triview = el('ai3dTriviewBtn');
    if (triview && !triview._ai3dBound) {
      triview._ai3dBound = true;
      triview.addEventListener('click', startTriviewJob);
    }
    var baseBtn = el('ai3dBaseBtn');
    if (baseBtn && !baseBtn._ai3dBound) {
      baseBtn._ai3dBound = true;
      baseBtn.addEventListener('click', startBaseModelJob);
    }
    var partsBtn = el('ai3dPartsBtn');
    if (partsBtn && !partsBtn._ai3dBound) {
      partsBtn._ai3dBound = true;
      partsBtn.addEventListener('click', startPartModelsJob);
    }
    var components = el('ai3dComponentsBtn');
    if (components && !components._ai3dBound) {
      components._ai3dBound = true;
      components.addEventListener('click', startComponentsJob);
    }
    if (!document._ai3dLightboxBound) {
      document._ai3dLightboxBound = true;
      document.addEventListener('click', function(evt) {
        var target = evt.target;
        if (!(target && target.closest && (target.closest('[data-ai3d-info]') || target.closest('.ai3d-info-bubble')))) {
          closeInfoBubble();
        }
        var modelBtn = target && target.closest ? target.closest('[data-ai3d-model-url]') : null;
        if (modelBtn) {
          evt.preventDefault();
          var src = modelBtn.getAttribute('data-ai3d-model-url') || '';
          var rawSrc = modelBtn.getAttribute('data-ai3d-model-raw-url') || src;
          var poster = modelBtn.getAttribute('data-ai3d-model-poster') || '';
          var label = modelBtn.getAttribute('data-ai3d-model-label') || '3D 模型';
          if (state.jobId && rawSrc) state.previewModelByJob[state.jobId] = rawSrc;
          document.querySelectorAll('[data-ai3d-model-url]').forEach(function(btn) {
            btn.classList.toggle('active', (btn.getAttribute('data-ai3d-model-url') || '') === src);
          });
          openModelLightbox(src, label, poster, modelBtn.getAttribute('data-ai3d-model-download') || src);
          return;
        }
        var preview = target && target.closest ? target.closest('[data-ai3d-preview-src]') : null;
        if (preview) {
          evt.preventDefault();
          openLightbox(preview.getAttribute('data-ai3d-preview-src') || '', preview.getAttribute('data-ai3d-preview-title') || '');
          return;
        }
        if (
          (target && target.closest && target.closest('.ai3d-lightbox-close')) ||
          (target && target.classList && target.classList.contains('ai3d-lightbox'))
        ) {
          closeLightbox();
        }
      });
      document.addEventListener('keydown', function(evt) {
        if (evt.key === 'Escape') {
          closeInfoBubble();
          closeLightbox();
          closeParamModal();
          closeCreateModal();
          closeHistoryModal();
        }
      });
    }
    applyWorkflowModeDefaults();
  }

  window.initAi3dModelView = function() {
    ensureCss();
    refreshStaticCopy();
    bind();
    renderFiles();
    loadConfig();
    loadJobs(true);
  };
})();
