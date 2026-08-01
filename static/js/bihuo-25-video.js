(function() {
  'use strict';

  var STORAGE_KEY = 'lobster_bihuo_25_current_task_v1';
  var state = {
    initialized: false,
    mode: 'reference',
    references: [],
    slots: { first: null, last: null, video: null },
    uploadTarget: 'reference',
    pickerTarget: 'reference',
    pickerType: 'image',
    assetCache: {},
    task: null,
    pollTimer: null,
    polling: false,
    submitting: false,
    resultUrl: ''
  };

  function $(id) { return document.getElementById(id); }
  function baseUrl() { return String(typeof LOCAL_API_BASE !== 'undefined' ? (LOCAL_API_BASE || '') : '').replace(/\/$/, ''); }
  function headers(json) {
    var out = typeof authHeaders === 'function' ? Object.assign({}, authHeaders() || {}) : {};
    if (json) out['Content-Type'] = 'application/json';
    else { delete out['Content-Type']; delete out['content-type']; }
    return out;
  }
  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function(ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }
  function compact(value, max) {
    var text = String(value || '').replace(/\s+/g, ' ').trim();
    return text.length > max ? text.slice(0, Math.max(1, max - 1)) + '…' : text;
  }
  function apiError(data, fallback) {
    var detail = data && (data.detail || data.error || data.message);
    if (detail && typeof detail === 'object') detail = detail.message || detail.detail || JSON.stringify(detail);
    return String(detail || fallback || '请求失败');
  }
  function requestJson(path, options) {
    return fetch(baseUrl() + path, options).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok) throw new Error(apiError(data, '请求失败'));
        return data || {};
      });
    });
  }
  function mediaLabel(type) { return type === 'image' ? '图片' : (type === 'video' ? '视频' : '音频'); }
  function normalizeAsset(raw) {
    raw = raw || {};
    var type = String(raw.media_type || '').toLowerCase();
    var url = String(raw.source_url || raw.open_url || raw.url || '').trim();
    if (!url || ['image', 'video', 'audio'].indexOf(type) < 0) return null;
    return {
      asset_id: String(raw.asset_id || ''),
      media_type: type,
      source_url: url,
      preview_url: String(raw.preview_url || raw.open_url || url),
      filename: String(raw.filename || mediaLabel(type)),
      prompt: String(raw.prompt || '')
    };
  }
  function setMessage(text, ok) {
    var el = $('b25Message');
    if (!el) return;
    el.hidden = !text;
    el.textContent = text || '';
    el.classList.toggle('is-ok', !!ok);
  }
  function setStatus(text, tone) {
    var el = $('b25HeaderStatus');
    if (!el) return;
    el.textContent = text || '等待创作';
    el.setAttribute('data-tone', tone || 'idle');
  }
  function setBusy(busy) {
    state.submitting = !!busy;
    var btn = $('b25GenerateBtn');
    if (btn) {
      btn.disabled = !!busy;
      var label = btn.querySelector('span');
      if (label) label.textContent = busy ? '正在提交' : '开始生成';
    }
  }

  function referenceToken(item) {
    var index = 0;
    for (var i = 0; i < state.references.length; i += 1) {
      if (state.references[i].media_type === item.media_type) index += 1;
      if (state.references[i] === item) break;
    }
    return '【@' + mediaLabel(item.media_type) + index + '】';
  }
  function referenceHtml(item, index) {
    var preview = item.media_type === 'image'
      ? '<img src="' + escapeHtml(item.preview_url || item.source_url) + '" alt="">'
      : '<div class="b25-media-fallback"><span>' + (item.media_type === 'video' ? '▶' : '♪') + '</span><span>' + mediaLabel(item.media_type) + '</span></div>';
    return '<div class="b25-reference-item" title="' + escapeHtml(item.filename) + '">' + preview +
      '<button type="button" class="b25-remove" data-b25-remove-ref="' + index + '" title="移除">×</button>' +
      '<em>' + escapeHtml(referenceToken(item)) + '</em></div>';
  }
  function renderReferences() {
    var list = $('b25ReferenceList');
    if (list) list.innerHTML = state.references.map(referenceHtml).join('');
    var counts = { image: 0, video: 0, audio: 0 };
    state.references.forEach(function(item) { counts[item.media_type] += 1; });
    var hint = $('b25ReferenceTokenHint');
    if (hint) hint.textContent = state.references.length
      ? '已选 ' + counts.image + ' 图 · ' + counts.video + ' 视频 · ' + counts.audio + ' 音频'
      : '';
  }
  function slotPreviewHtml(item, kind) {
    if (!item) return kind === 'video'
      ? '<span class="b25-video-glyph">▶</span><span>选择一个视频</span>'
      : '<span>+</span>';
    if (kind === 'video') {
      return '<video src="' + escapeHtml(item.preview_url || item.source_url) + '" controls preload="metadata"></video>' +
        '<button type="button" class="b25-remove" data-b25-clear-slot="video" title="移除">×</button>';
    }
    return '<img src="' + escapeHtml(item.preview_url || item.source_url) + '" alt="">' +
      '<button type="button" class="b25-remove" data-b25-clear-slot="' + kind + '" title="移除">×</button>';
  }
  function renderSlots() {
    var first = $('b25FirstPreview');
    var last = $('b25LastPreview');
    var edit = $('b25EditPreview');
    var extend = $('b25ExtendPreview');
    if (first) first.innerHTML = slotPreviewHtml(state.slots.first, 'first');
    if (last) last.innerHTML = slotPreviewHtml(state.slots.last, 'last');
    [edit, extend].forEach(function(el) {
      if (!el) return;
      el.innerHTML = slotPreviewHtml(state.slots.video, 'video');
      el.classList.toggle('has-video', !!state.slots.video);
    });
  }
  function addAsset(target, asset) {
    asset = normalizeAsset(asset);
    if (!asset) throw new Error('素材没有可用的公网地址');
    if ((target === 'first' || target === 'last') && asset.media_type !== 'image') throw new Error('首尾帧只能选择图片');
    if (target === 'video' && asset.media_type !== 'video') throw new Error('这里只能选择视频');
    if (target === 'reference') {
      var same = state.references.some(function(item) { return item.source_url === asset.source_url; });
      if (same) return;
      var counts = { image: 0, video: 0, audio: 0 };
      state.references.forEach(function(item) { counts[item.media_type] += 1; });
      if (state.references.length >= 50) throw new Error('参考素材总数不能超过50个');
      if (counts[asset.media_type] >= (asset.media_type === 'image' ? 30 : 10)) {
        throw new Error(mediaLabel(asset.media_type) + '参考素材已达到上限');
      }
      state.references.push(asset);
      renderReferences();
      return;
    }
    state.slots[target] = asset;
    renderSlots();
  }

  function switchMode(mode) {
    if (['reference', 'first_last', 'edit', 'extend'].indexOf(mode) < 0) return;
    state.mode = mode;
    document.querySelectorAll('[data-b25-mode]').forEach(function(btn) {
      btn.classList.toggle('is-active', btn.getAttribute('data-b25-mode') === mode);
    });
    document.querySelectorAll('[data-b25-panel]').forEach(function(panel) {
      panel.classList.toggle('is-active', panel.getAttribute('data-b25-panel') === mode);
    });
    var durationField = $('b25DurationField');
    if (durationField) durationField.style.display = mode === 'edit' ? 'none' : '';
    var label = $('b25DurationLabel');
    if (label) label.textContent = mode === 'extend' ? '延长时长' : '视频时长';
  }

  function fileKind(file) {
    var type = String(file.type || '').toLowerCase();
    var name = String(file.name || '').toLowerCase();
    if (type.indexOf('image/') === 0 || /\.(png|jpe?g|webp|gif)$/.test(name)) return 'image';
    if (type.indexOf('video/') === 0 || /\.(mp4|mov|webm|mkv|avi)$/.test(name)) return 'video';
    if (type.indexOf('audio/') === 0 || /\.(mp3|wav|m4a|aac|ogg|flac)$/.test(name)) return 'audio';
    return '';
  }
  function prepareFileInput(target) {
    var input = $('b25FileInput');
    if (!input) return;
    state.uploadTarget = target;
    input.multiple = target === 'reference';
    input.accept = target === 'reference' ? 'image/*,video/*,audio/*' : (target === 'video' ? 'video/*' : 'image/*');
    input.value = '';
    input.click();
  }
  function uploadOne(file) {
    var form = new FormData();
    form.append('file', file, file.name || 'upload');
    return requestJson('/api/assets/upload', { method: 'POST', headers: headers(false), body: form });
  }
  function uploadFiles(files) {
    var target = state.uploadTarget;
    var list = Array.prototype.slice.call(files || []);
    if (!list.length) return;
    list.forEach(function(file) {
      var kind = fileKind(file);
      if (!kind) throw new Error('仅支持图片、视频或音频文件');
      if ((target === 'first' || target === 'last') && kind !== 'image') throw new Error('首尾帧只能上传图片');
      if (target === 'video' && kind !== 'video') throw new Error('这里只能上传视频');
    });
    setMessage('正在上传 ' + list.length + ' 个素材…', true);
    var chain = Promise.resolve();
    list.forEach(function(file) {
      chain = chain.then(function() { return uploadOne(file); }).then(function(data) { addAsset(target, data); });
    });
    chain.then(function() {
      state.assetCache = {};
      setMessage('素材已添加', true);
    }).catch(function(err) { setMessage(err.message || '素材上传失败', false); });
  }

  function pickerTypes(target) {
    return target === 'reference' ? ['image', 'video', 'audio'] : (target === 'video' ? ['video'] : ['image']);
  }
  function openPicker(target) {
    state.pickerTarget = target;
    state.pickerType = pickerTypes(target)[0];
    var picker = $('b25AssetPicker');
    if (picker) picker.hidden = false;
    renderPickerTabs();
    loadPickerAssets(state.pickerType);
  }
  function closePicker() { var picker = $('b25AssetPicker'); if (picker) picker.hidden = true; }
  function renderPickerTabs() {
    var tabs = $('b25PickerTabs');
    if (!tabs) return;
    tabs.innerHTML = pickerTypes(state.pickerTarget).map(function(type) {
      return '<button type="button" class="' + (type === state.pickerType ? 'is-active' : '') + '" data-b25-picker-type="' + type + '">' + mediaLabel(type) + '</button>';
    }).join('');
  }
  function loadPickerAssets(type) {
    var grid = $('b25PickerGrid');
    if (grid) grid.innerHTML = '<div class="b25-picker-empty">正在加载…</div>';
    var cached = state.assetCache[type];
    var promise = cached ? Promise.resolve(cached) : requestJson('/api/assets?media_type=' + encodeURIComponent(type) + '&limit=120', { headers: headers(false) }).then(function(data) {
      var rows = (Array.isArray(data.assets) ? data.assets : []).map(normalizeAsset).filter(Boolean);
      state.assetCache[type] = rows;
      return rows;
    });
    promise.then(renderPickerGrid).catch(function(err) {
      var message = err && String(err.message || '').toLowerCase();
      var publicMessage = message && message.indexOf('not authenticated') < 0
        ? (err.message || '素材加载失败')
        : '素材读取失败，请重新登录后重试';
      if (grid) grid.innerHTML = '<div class="b25-picker-empty">' + escapeHtml(publicMessage) + '</div>';
    });
  }
  function renderPickerGrid(items) {
    var grid = $('b25PickerGrid');
    if (!grid) return;
    if (!items.length) {
      grid.innerHTML = '<div class="b25-picker-empty">暂无可用' + mediaLabel(state.pickerType) + '</div>';
      return;
    }
    grid.innerHTML = items.map(function(item, index) {
      var thumb = item.media_type === 'image'
        ? '<img src="' + escapeHtml(item.preview_url || item.source_url) + '" alt="">'
        : (item.media_type === 'video'
          ? '<video src="' + escapeHtml(item.preview_url || item.source_url) + '" muted preload="metadata"></video>'
          : '<span style="font-size:28px;">♪</span>');
      return '<button type="button" class="b25-picker-item" data-b25-picker-index="' + index + '"><span class="b25-picker-thumb">' + thumb + '</span><span class="b25-picker-name">' + escapeHtml(compact(item.filename, 24)) + '</span></button>';
    }).join('');
    grid._b25Items = items;
  }

  function buildPayload() {
    var prompt = String(($('b25Prompt') || {}).value || '').trim();
    if (!prompt) throw new Error('请输入视频画面和动作要求');
    var durationInput = $('b25Duration');
    var durationRaw = String((durationInput || {}).value || '').trim();
    var duration = durationRaw ? Number(durationRaw) : NaN;
    if (state.mode !== 'edit' && (!Number.isInteger(duration) || duration < 4 || duration > 30)) {
      throw new Error((state.mode === 'extend' ? '延长时长' : '视频时长') + '请输入 4-30 秒的整数');
    }
    var payload = {
      mode: state.mode,
      prompt: prompt,
      ratio: String(($('b25Ratio') || {}).value || '9:16'),
      resolution: String(($('b25Resolution') || {}).value || '720p'),
      duration: state.mode === 'edit' ? 0 : duration,
      image_urls: [], video_urls: [], audio_urls: [], first_image_url: '', last_image_url: ''
    };
    if (state.mode === 'reference') {
      state.references.forEach(function(item) { payload[item.media_type + '_urls'].push(item.source_url); });
      if (!state.references.length) throw new Error('请至少添加一个参考素材');
    } else if (state.mode === 'first_last') {
      if (!state.slots.first || !state.slots.last) throw new Error('请同时选择首帧图和尾帧图');
      payload.first_image_url = state.slots.first.source_url;
      payload.last_image_url = state.slots.last.source_url;
    } else {
      if (!state.slots.video) throw new Error('请选择一个要处理的视频');
      payload.video_urls = [state.slots.video.source_url];
    }
    return payload;
  }
  function persistTask() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state.task || {})); } catch (e) {}
  }
  function clearPoll() {
    if (state.pollTimer) clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }
  function schedulePoll(delay) {
    clearPoll();
    state.pollTimer = setTimeout(pollTask, delay == null ? 12000 : delay);
  }
  function renderProcessing(text) {
    var stage = $('b25ResultStage');
    if (stage) stage.innerHTML = '<div class="b25-empty-result"><span class="b25-empty-mark">···</span><strong>' + escapeHtml(text || '视频正在生成') + '</strong></div>';
    var progress = $('b25Progress');
    if (progress) progress.hidden = false;
    var p = $('b25ProgressText');
    if (p) p.textContent = text || '任务已提交，正在生成';
    var actions = $('b25ResultActions');
    if (actions) actions.hidden = true;
  }
  function renderFailure(message) {
    clearPoll();
    setStatus('生成失败', 'error');
    setMessage(message || '视频生成失败', false);
    var progress = $('b25Progress');
    if (progress) progress.hidden = true;
    var stage = $('b25ResultStage');
    if (stage) stage.innerHTML = '<div class="b25-empty-result"><span class="b25-empty-mark">!</span><strong>' + escapeHtml(message || '视频生成失败') + '</strong></div>';
    var retry = $('b25RetryBtn');
    if (retry) retry.hidden = false;
  }
  function renderCompleted(url) {
    clearPoll();
    state.resultUrl = url;
    setStatus('生成完成', 'ok');
    var progress = $('b25Progress');
    if (progress) progress.hidden = true;
    var stage = $('b25ResultStage');
    if (stage) stage.innerHTML = '<video src="' + escapeHtml(url) + '" controls playsinline preload="metadata"></video>';
    var actions = $('b25ResultActions');
    if (actions) actions.hidden = false;
    var retry = $('b25RetryBtn');
    if (retry) retry.hidden = false;
  }
  function saveCompletedAsset(url) {
    if (!state.task || state.task.asset_id || state.task.saving) return Promise.resolve();
    state.task.saving = true;
    persistTask();
    return requestJson('/api/assets/save-url', {
      method: 'POST', headers: headers(true), body: JSON.stringify({
        url: url,
        media_type: 'video',
        name: '必火2.5-' + String(state.task.task_id || '').slice(0, 16),
        tags: 'auto,bihuo.25,generated',
        prompt: String((state.task.payload || {}).prompt || ''),
        model: '必火2.5',
        generation_task_id: state.task.task_id || ''
      })
    }).then(function(data) {
      state.task.asset_id = data.asset_id || '';
      state.task.saving = false;
      persistTask();
      setMessage('视频已生成并保存到素材库', true);
    }).catch(function(err) {
      state.task.saving = false;
      persistTask();
      setMessage('视频已生成，自动保存到素材库失败：' + (err.message || '请稍后重试'), false);
    });
  }
  function handleTaskResult(data) {
    var taskId = data.task_id || (state.task && state.task.task_id) || '';
    var taskEl = $('b25TaskId');
    if (taskEl) taskEl.textContent = taskId ? '任务 ' + taskId : '';
    if (data.status === 'failed' || data.ok === false) {
      if (state.task) { state.task.status = 'failed'; state.task.error = data.error || ''; persistTask(); }
      renderFailure(data.error || '视频生成失败');
      return;
    }
    var url = Array.isArray(data.video_urls) ? String(data.video_urls[0] || '') : '';
    if (url) {
      if (state.task) { state.task.status = 'completed'; state.task.video_url = url; persistTask(); }
      renderCompleted(url);
      saveCompletedAsset(url);
      return;
    }
    if (state.task) { state.task.status = 'processing'; persistTask(); }
    setStatus('正在生成', 'busy');
    renderProcessing(data.upstream_status ? '当前状态：' + data.upstream_status : '任务已提交，正在生成');
    schedulePoll();
  }
  function pollTask() {
    if (state.polling || !state.task || !state.task.task_id) return;
    state.polling = true;
    requestJson('/api/bihuo-25-video/poll', {
      method: 'POST', headers: headers(true), body: JSON.stringify({ task_id: state.task.task_id })
    }).then(handleTaskResult).catch(function(err) {
      setMessage('查询暂时中断，将继续重试：' + (err.message || '网络异常'), false);
      schedulePoll(18000);
    }).finally(function() { state.polling = false; });
  }
  function startGeneration() {
    if (state.submitting) return;
    var payload;
    try { payload = buildPayload(); } catch (err) { setMessage(err.message, false); return; }
    setMessage('', false);
    setBusy(true);
    setStatus('正在提交', 'busy');
    renderProcessing('正在提交生成任务');
    clearPoll();
    requestJson('/api/bihuo-25-video/start', {
      method: 'POST', headers: headers(true), body: JSON.stringify(payload)
    }).then(function(data) {
      state.task = {
        task_id: data.task_id || '',
        status: data.status || 'processing',
        payload: payload,
        created_at: new Date().toISOString(),
        video_url: '',
        asset_id: ''
      };
      persistTask();
      handleTaskResult(data);
    }).catch(function(err) {
      state.task = { status: 'failed', payload: payload, error: err.message || '任务提交失败' };
      persistTask();
      renderFailure(err.message || '任务提交失败');
    }).finally(function() { setBusy(false); });
  }

  function restorePayload(payload) {
    if (!payload || typeof payload !== 'object') return;
    switchMode(payload.mode || 'reference');
    if ($('b25Prompt')) $('b25Prompt').value = payload.prompt || '';
    if ($('b25Ratio')) $('b25Ratio').value = payload.ratio || '9:16';
    if ($('b25Resolution')) $('b25Resolution').value = payload.resolution || '720p';
    if ($('b25Duration')) $('b25Duration').value = payload.duration || 10;
    state.references = [];
    ['image', 'video', 'audio'].forEach(function(type) {
      (payload[type + '_urls'] || []).forEach(function(url) {
        state.references.push({ media_type: type, source_url: url, preview_url: url, filename: mediaLabel(type) + '参考' });
      });
    });
    state.slots.first = payload.first_image_url ? { media_type: 'image', source_url: payload.first_image_url, preview_url: payload.first_image_url, filename: '首帧图' } : null;
    state.slots.last = payload.last_image_url ? { media_type: 'image', source_url: payload.last_image_url, preview_url: payload.last_image_url, filename: '尾帧图' } : null;
    state.slots.video = payload.video_urls && payload.video_urls[0] ? { media_type: 'video', source_url: payload.video_urls[0], preview_url: payload.video_urls[0], filename: '源视频' } : null;
    renderReferences(); renderSlots(); updatePromptCount();
  }
  function restoreTask() {
    try { state.task = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null'); } catch (e) { state.task = null; }
    if (!state.task) return;
    restorePayload(state.task.payload || {});
    var taskEl = $('b25TaskId');
    if (taskEl && state.task.task_id) taskEl.textContent = '任务 ' + state.task.task_id;
    if (state.task.status === 'completed' && state.task.video_url) renderCompleted(state.task.video_url);
    else if (state.task.status === 'failed') renderFailure(state.task.error || '上次任务生成失败');
    else if (state.task.task_id) { setStatus('正在生成', 'busy'); renderProcessing('正在恢复任务进度'); schedulePoll(500); }
  }
  function updatePromptCount() {
    var value = String(($('b25Prompt') || {}).value || '');
    var el = $('b25PromptCount');
    if (el) el.textContent = value.length + ' / 6000';
  }
  function openResult() { if (state.resultUrl) window.open(state.resultUrl, '_blank', 'noopener'); }
  function goAssets() {
    var nav = document.querySelector('.nav-left-item[data-view="assets"]');
    if (nav) nav.click();
  }
  function backToStore() {
    var nav = document.querySelector('.nav-left-item[data-view="skill-store"]');
    if (nav) nav.click();
  }

  function bindEvents() {
    var root = $('content-bihuo-25-video');
    if (!root || root.dataset.b25Bound === '1') return;
    root.dataset.b25Bound = '1';
    root.addEventListener('click', function(event) {
      var mode = event.target.closest('[data-b25-mode]');
      if (mode) { switchMode(mode.getAttribute('data-b25-mode')); return; }
      var upload = event.target.closest('[data-b25-upload-target]');
      if (upload) { prepareFileInput(upload.getAttribute('data-b25-upload-target')); return; }
      var library = event.target.closest('[data-b25-library-target]');
      if (library) { openPicker(library.getAttribute('data-b25-library-target')); return; }
      var remove = event.target.closest('[data-b25-remove-ref]');
      if (remove) { state.references.splice(Number(remove.getAttribute('data-b25-remove-ref')), 1); renderReferences(); return; }
      var clear = event.target.closest('[data-b25-clear-slot]');
      if (clear) { state.slots[clear.getAttribute('data-b25-clear-slot')] = null; renderSlots(); return; }
      if (event.target.closest('[data-b25-picker-close]')) { closePicker(); return; }
      var tab = event.target.closest('[data-b25-picker-type]');
      if (tab) { state.pickerType = tab.getAttribute('data-b25-picker-type'); renderPickerTabs(); loadPickerAssets(state.pickerType); return; }
      var itemBtn = event.target.closest('[data-b25-picker-index]');
      if (itemBtn) {
        var grid = $('b25PickerGrid');
        var item = grid && grid._b25Items ? grid._b25Items[Number(itemBtn.getAttribute('data-b25-picker-index'))] : null;
        try { addAsset(state.pickerTarget, item); closePicker(); setMessage('素材已添加', true); } catch (err) { setMessage(err.message, false); }
      }
    });
    $('b25FileInput').addEventListener('change', function(event) {
      try { uploadFiles(event.target.files); } catch (err) { setMessage(err.message, false); }
    });
    $('b25Prompt').addEventListener('input', updatePromptCount);
    $('b25GenerateBtn').addEventListener('click', startGeneration);
    $('b25RetryBtn').addEventListener('click', startGeneration);
    $('b25OpenResultBtn').addEventListener('click', openResult);
    $('b25GoAssetsBtn').addEventListener('click', goAssets);
    $('b25BackBtn').addEventListener('click', backToStore);
  }

  window.initBihuo25VideoView = function() {
    bindEvents();
    if (!state.initialized) {
      state.initialized = true;
      switchMode('reference');
      renderReferences(); renderSlots(); updatePromptCount(); restoreTask();
    } else if (state.task && state.task.status === 'processing' && state.task.task_id && !state.pollTimer) {
      schedulePoll(500);
    }
  };
})();
