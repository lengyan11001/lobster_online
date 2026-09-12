(function initBatchCreativeVideoModule() {
  'use strict';

  var STORAGE_KEY = 'lobster-batch-creative-video-v1';
  var POLL_INTERVAL_MS = 5000;
  var SUBMIT_CONCURRENCY = 5;
  var SUBMIT_BATCH_DELAY_MS = 1000;
  var MIN_BATCH_COUNT = 1;
  var MAX_BATCH_COUNT = 50;
  var state = {
    initialized: false,
    selectedCount: 10,
    selectedAspectRatio: '9:16',
    selectedResolution: '720P',
    selectedDuration: 10,
    selectedImage: null,
    autoRewritePrompt: false,
    rewritePrompts: [],
    tasks: [],
    batchId: '',
    createdAt: 0,
    polling: {},
    submitting: false,
    imageObjectUrl: ''
  };

  function $(id) {
    return document.getElementById(id);
  }

  function localBase() {
    return String(typeof LOCAL_API_BASE !== 'undefined' ? (LOCAL_API_BASE || '') : '').replace(/\/$/, '');
  }

  function cloudBase() {
    // The local pipeline is intentionally hosted by lobster_online, while the
    // authenticated chat proxy is hosted by lobster_server. A saved local
    // `?api=` override must not send the rewrite request back to the local
    // server, where this route does not exist.
    var candidates = [
      typeof API_BASE !== 'undefined' ? API_BASE : '',
      typeof window !== 'undefined' && window.__API_BASE ? window.__API_BASE : '',
      typeof LOBSTER_SERVER_PUBLIC !== 'undefined' ? LOBSTER_SERVER_PUBLIC : ''
    ];
    for (var i = 0; i < candidates.length; i += 1) {
      var value = String(candidates[i] || '').trim().replace(/\/$/, '');
      if (!value) continue;
      try {
        var parsed = new URL(value, window.location.href);
        var host = String(parsed.hostname || '').toLowerCase();
        var isLocalHost = host === 'localhost'
          || host === '127.0.0.1'
          || host === '::1'
          || /^10\./.test(host)
          || /^192\.168\./.test(host)
          || /^172\.(1[6-9]|2\d|3[0-1])\./.test(host);
        if (!isLocalHost) return value;
      } catch (e) {
        continue;
      }
    }
    return 'https://bhzn.top';
  }

  function authHeadersSafe() {
    if (typeof authHeaders === 'function') {
      return Object.assign({}, authHeaders() || {});
    }
    return {};
  }

  function jsonHeaders() {
    return Object.assign({ 'Content-Type': 'application/json' }, authHeadersSafe());
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function(ch) {
      return {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }[ch];
    });
  }

  function messageText(data, fallback) {
    if (!data) return fallback || '请求失败';
    if (typeof data === 'string') return data;
    if (data.detail) {
      if (typeof data.detail === 'string') return data.detail;
      try { return JSON.stringify(data.detail); } catch (e) {}
    }
    if (data.error) {
      if (typeof data.error === 'string') return data.error;
      if (data.error.message) return String(data.error.message);
    }
    if (data.message) return String(data.message);
    return fallback || '请求失败';
  }

  function showMessage(text, isError) {
    var el = $('batchCreativeMsg');
    if (!el) return;
    el.textContent = text || '';
    el.classList.toggle('is-error', !!isError);
    el.style.display = text ? 'block' : 'none';
  }

  function getPrompt() {
    return String(($('batchCreativePrompt') || {}).value || '').trim();
  }

  function isUsableRemoteAssetUrl(value) {
    var url = String(value || '').trim();
    if (!/^https?:\/\//i.test(url)) return false;
    try {
      var parsed = new URL(url, window.location.href);
      var host = String(parsed.hostname || '').toLowerCase();
      var path = String(parsed.pathname || '').toLowerCase();
      if (!host || host === 'localhost' || host === '127.0.0.1' || host === '0.0.0.0' || host === '::1') {
        return false;
      }
      if (/^10\./.test(host) || /^192\.168\./.test(host) || /^172\.(1[6-9]|2\d|3[0-1])\./.test(host)) {
        return false;
      }
      // /api/assets/file is the local signed preview chain, not a URL that
      // the cloud chat proxy can fetch from the user's computer.
      if (path.indexOf('/api/assets/file/') >= 0) return false;
      return true;
    } catch (e) {
      return false;
    }
  }

  function getImageSourceUrl() {
    if (!state.selectedImage) return '';
    var candidates = [
      state.selectedImage.sourceUrl,
      state.selectedImage.publicUrl
    ];
    for (var i = 0; i < candidates.length; i += 1) {
      if (isUsableRemoteAssetUrl(candidates[i])) return String(candidates[i]).trim();
    }
    return '';
  }

  function clampBatchCount(value) {
    var count = Number(value);
    if (!isFinite(count)) count = 10;
    count = Math.round(count);
    return Math.min(MAX_BATCH_COUNT, Math.max(MIN_BATCH_COUNT, count));
  }

  function normalizeAspectRatio(value) {
    return String(value || '').trim() === '16:9' ? '16:9' : '9:16';
  }

  function normalizeResolution(value) {
    return String(value || '').trim().toUpperCase() === '1080P' ? '1080P' : '720P';
  }

  function normalizeDuration(value) {
    var duration = Number(value);
    if (!isFinite(duration)) duration = 10;
    duration = Math.round(duration);
    return Math.min(30, Math.max(5, duration));
  }

  function getSettings() {
    var aspectSelect = $('batchCreativeAspectSelect');
    var resolutionSelect = $('batchCreativeResolutionSelect');
    var durationSelect = $('batchCreativeDurationSelect');
    state.selectedAspectRatio = normalizeAspectRatio(
      aspectSelect ? aspectSelect.value : state.selectedAspectRatio
    );
    state.selectedResolution = normalizeResolution(
      resolutionSelect ? resolutionSelect.value : state.selectedResolution
    );
    state.selectedDuration = normalizeDuration(
      durationSelect ? durationSelect.value : state.selectedDuration
    );
    return {
      prompt: getPrompt(),
      count: clampBatchCount(state.selectedCount),
      duration: state.selectedDuration,
      aspectRatio: state.selectedAspectRatio,
      resolution: state.selectedResolution,
      autoRewritePrompt: !!state.autoRewritePrompt
    };
  }

  function formatTime(timestamp) {
    var value = Number(timestamp || 0);
    if (!value) return '';
    var date = new Date(value);
    if (!isFinite(date.getTime())) return '';
    var now = new Date();
    var sameDay = date.getFullYear() === now.getFullYear()
      && date.getMonth() === now.getMonth()
      && date.getDate() === now.getDate();
    var hh = String(date.getHours()).padStart(2, '0');
    var mm = String(date.getMinutes()).padStart(2, '0');
    if (sameDay) return hh + ':' + mm;
    return String(date.getMonth() + 1).padStart(2, '0') + '-' + String(date.getDate()).padStart(2, '0') + ' ' + hh + ':' + mm;
  }

  function normalizeStatus(status) {
    var value = String(status || '').toLowerCase().trim();
    if (value === 'success' || value === 'done' || value === 'complete' || value === 'succeeded') return 'completed';
    if (value === 'pending' || value === 'queued' || value === 'submitted') return 'running';
    if (value === 'submitting') return 'submitting';
    if (value === 'error' || value === 'cancelled' || value === 'canceled') return 'failed';
    return value === 'completed' || value === 'failed' || value === 'running' ? value : 'waiting';
  }

  function statusLabel(status) {
    if (status === 'completed') return '已完成';
    if (status === 'failed') return '失败';
    if (status === 'running') return '生成中';
    if (status === 'submitting') return '提交中';
    return '等待提交';
  }

  function statusTone(status) {
    if (status === 'completed') return 'completed';
    if (status === 'failed') return 'failed';
    return 'running';
  }

  function looksLikeVideoUrl(url) {
    return /\.(mp4|mov|m4v|webm|mkv)(?:$|[?#])/i.test(String(url || ''));
  }

  function extractVideoUrl(data) {
    if (!data || typeof data !== 'object') return '';
    var result = data.result && typeof data.result === 'object' ? data.result : {};
    var finalVideo = result.final_video && typeof result.final_video === 'object' ? result.final_video : {};
    var direct = String(
      data.video_url
      || data.videoUrl
      || finalVideo.url
      || finalVideo.preview_url
      || finalVideo.local_preview_url
      || ''
    ).trim();
    if (direct) return direct;

    var saved = Array.isArray(data.saved_assets) ? data.saved_assets : [];
    for (var i = 0; i < saved.length; i += 1) {
      var row = saved[i] && saved[i].asset && typeof saved[i].asset === 'object'
        ? saved[i].asset
        : (saved[i] || {});
      var url = String(row.source_url || row.open_url || row.preview_url || row.url || '').trim();
      var mediaType = String(row.media_type || row.mediaType || row.kind || '').toLowerCase();
      if (url && (mediaType === 'video' || looksLikeVideoUrl(url))) return url;
    }

    var groups = [result.completed_segments, result.completed_shots, result.shots];
    for (var g = 0; g < groups.length; g += 1) {
      var list = Array.isArray(groups[g]) ? groups[g] : [];
      for (var j = 0; j < list.length; j += 1) {
        var item = list[j] || {};
        var raw = item.video_raw || item.raw || {};
        var content = raw.content && typeof raw.content === 'object' ? raw.content : {};
        var nested = raw.data && typeof raw.data === 'object' ? raw.data : {};
        var segmentUrl = String(
          item.video_url
          || item.mp4url
          || item.url
          || content.video_url
          || content.url
          || nested.video_url
          || nested.output
          || ''
        ).trim();
        if (segmentUrl) return segmentUrl;
      }
    }
    return '';
  }

  function normalizeTask(task, index) {
    task = task && typeof task === 'object' ? task : {};
    return {
      index: Number(task.index != null ? task.index : index) || index,
      jobId: String(task.jobId || task.job_id || '').trim(),
      status: normalizeStatus(task.status),
      videoUrl: String(task.videoUrl || task.video_url || '').trim(),
      prompt: String(task.prompt || task.task_text || '').trim(),
      error: String(task.error || '').trim(),
      createdAt: Number(task.createdAt || task.created_at_ts || 0) || 0,
      updatedAt: Number(task.updatedAt || task.updated_at_ts || 0) || 0,
      progress: task.progress != null ? task.progress : null
    };
  }

  function currentSnapshot() {
    var settings = getSettings();
    return {
      batchId: state.batchId,
      createdAt: Number(state.createdAt || Date.now()),
      prompt: settings.prompt,
      count: settings.count,
      duration: settings.duration,
      aspectRatio: settings.aspectRatio,
      resolution: settings.resolution,
      autoRewritePrompt: !!state.autoRewritePrompt,
      rewritePrompts: state.rewritePrompts.slice(),
      image: state.selectedImage ? {
        assetId: state.selectedImage.assetId || '',
        name: state.selectedImage.name || '',
        sourceUrl: state.selectedImage.sourceUrl || '',
        previewUrl: state.selectedImage.sourceUrl || ''
      } : null,
      tasks: state.tasks
    };
  }

  function saveSnapshot() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(currentSnapshot()));
    } catch (e) {
      console.warn('[batch-creative-video] save snapshot failed', e);
    }
  }

  function readSnapshot() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      var data = JSON.parse(raw);
      if (!data || !Array.isArray(data.tasks)) return null;
      return data;
    } catch (e) {
      return null;
    }
  }

  function clearSnapshot() {
    try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
  }

  function renderSelectedImage() {
    var empty = $('batchCreativeImageEmpty');
    var preview = $('batchCreativeImagePreview');
    var image = $('batchCreativeImage');
    var name = $('batchCreativeImageName');
    var status = $('batchCreativeImageStatus');
    if (!empty || !preview || !image) return;
    if (!state.selectedImage) {
      empty.hidden = false;
      preview.hidden = true;
      return;
    }
    var url = state.selectedImage.previewUrl || state.selectedImage.sourceUrl || '';
    empty.hidden = true;
    preview.hidden = false;
    image.src = url;
    if (name) name.textContent = state.selectedImage.name || '参考图片';
    if (status) status.textContent = state.selectedImage.assetId ? '已上传到素材库' : '等待上传';
  }

  function renderCountChoice() {
    state.selectedCount = clampBatchCount(state.selectedCount);
    var range = $('batchCreativeCountRange');
    if (range) range.value = String(state.selectedCount);
    var count = $('batchCreativeSubmitCount');
    if (count) count.textContent = String(state.selectedCount) + ' 条';
    var value = $('batchCreativeCountValue');
    if (value) value.textContent = '当前生成 ' + String(state.selectedCount) + ' 条';
  }

  function renderVideoSettings() {
    state.selectedAspectRatio = normalizeAspectRatio(state.selectedAspectRatio);
    state.selectedResolution = normalizeResolution(state.selectedResolution);
    state.selectedDuration = normalizeDuration(state.selectedDuration);
    var aspectSelect = $('batchCreativeAspectSelect');
    var resolutionSelect = $('batchCreativeResolutionSelect');
    var durationSelect = $('batchCreativeDurationSelect');
    if (aspectSelect) aspectSelect.value = state.selectedAspectRatio;
    if (resolutionSelect) resolutionSelect.value = state.selectedResolution;
    if (durationSelect) durationSelect.value = String(state.selectedDuration);
    var durationValue = $('batchCreativeDurationValue');
    if (durationValue) durationValue.textContent = '当前时长 ' + String(state.selectedDuration) + ' 秒';
    var page = $('content-batch-creative-video');
    if (page) {
      page.style.setProperty(
        '--batch-video-aspect',
        state.selectedAspectRatio === '16:9' ? '16 / 9' : '9 / 16'
      );
    }
  }

  function taskCounts() {
    return state.tasks.reduce(function(result, task) {
      var status = normalizeStatus(task.status);
      if (status === 'completed') result.completed += 1;
      else if (status === 'failed') result.failed += 1;
      else if (status === 'running' || status === 'submitting') result.running += 1;
      return result;
    }, { running: 0, completed: 0, failed: 0 });
  }

  function taskMediaHtml(task) {
    if (task.videoUrl) {
      return '<video controls preload="metadata" src="' + escapeHtml(task.videoUrl) + '"></video>';
    }
    if (state.selectedImage && (state.selectedImage.previewUrl || state.selectedImage.sourceUrl)) {
      return '<img src="' + escapeHtml(state.selectedImage.previewUrl || state.selectedImage.sourceUrl) + '" alt="参考图片">';
    }
    if (task.status === 'failed') {
      return '<div class="batch-creative-placeholder"><strong>生成失败</strong><span>请查看失败原因</span></div>';
    }
    return '<div class="batch-creative-placeholder"><strong>' + escapeHtml(statusLabel(task.status)) + '</strong><span>任务结果生成后会显示在这里</span><div class="batch-creative-card-progress"><span></span></div></div>';
  }

  function taskActionHtml(task) {
    var actions = [];
    if (task.videoUrl) {
      actions.push('<button type="button" class="btn btn-primary" data-batch-open="' + escapeHtml(task.videoUrl) + '">打开</button>');
      actions.push('<button type="button" class="btn btn-ghost" data-batch-download="' + escapeHtml(task.videoUrl) + '" data-batch-index="' + escapeHtml(task.index) + '">下载</button>');
    }
    if (task.status === 'failed') {
      actions.push('<button type="button" class="btn btn-ghost" data-batch-retry="' + escapeHtml(task.index) + '">重试</button>');
    }
    return actions.join('');
  }

  function renderTasks() {
    var grid = $('batchCreativeResultGrid');
    var empty = $('batchCreativeResultEmpty');
    var summary = $('batchCreativeResultSummary');
    var counts = taskCounts();
    if ($('batchCreativeRunningCount')) $('batchCreativeRunningCount').textContent = String(counts.running);
    if ($('batchCreativeCompletedCount')) $('batchCreativeCompletedCount').textContent = String(counts.completed);
    if ($('batchCreativeFailedCount')) $('batchCreativeFailedCount').textContent = String(counts.failed);
    if ($('batchCreativeStartBtn')) {
      $('batchCreativeStartBtn').textContent = state.submitting ? '正在提交...' : '开始批量生成';
      $('batchCreativeStartBtn').disabled = state.submitting;
    }
    if (summary) {
      if (!state.tasks.length) summary.textContent = '还没有提交任务';
      else summary.textContent = '共 ' + state.tasks.length + ' 条，完成 ' + counts.completed + ' 条，生成中 ' + counts.running + ' 条';
    }
    if (!grid || !empty) return;
    empty.hidden = !!state.tasks.length;
    if (!state.tasks.length) {
      grid.innerHTML = '';
      return;
    }
    grid.innerHTML = state.tasks.map(function(task) {
      var status = normalizeStatus(task.status);
      var error = task.error ? '<div class="batch-creative-card-error" title="' + escapeHtml(task.error) + '">' + escapeHtml(task.error) + '</div>' : '';
      var actions = taskActionHtml(task);
      var prompt = task.prompt
        ? '<div class="batch-creative-card-prompt" title="' + escapeHtml(task.prompt) + '"><span>提示词</span>' + escapeHtml(task.prompt) + '</div>'
        : '';
      return [
        '<article class="batch-creative-card" data-batch-task-index="' + escapeHtml(task.index) + '">',
        '<div class="batch-creative-card-media">',
        taskMediaHtml(task),
        '<span class="batch-creative-card-badge" data-status="' + escapeHtml(statusTone(status)) + '">' + escapeHtml(statusLabel(status)) + '</span>',
        '</div>',
        '<div class="batch-creative-card-body">',
        '<div class="batch-creative-card-head"><strong>视频 ' + escapeHtml(Number(task.index) + 1) + '</strong><span>' + escapeHtml(formatTime(task.updatedAt || task.createdAt)) + '</span></div>',
        prompt,
        error,
        task.jobId ? '<div class="batch-creative-card-foot"><span>任务 ' + escapeHtml(task.jobId.slice(0, 10)) + '</span><span>' + escapeHtml(task.status === 'completed' ? '已入库' : '') + '</span></div>' : '',
        actions ? '<div class="batch-creative-card-actions">' + actions + '</div>' : '',
        '</div>',
        '</article>'
      ].join('');
    }).join('');
  }

  function setTask(index, patch) {
    var task = state.tasks.filter(function(item) { return Number(item.index) === Number(index); })[0];
    if (!task) return null;
    Object.assign(task, patch || {});
    task.status = normalizeStatus(task.status);
    task.updatedAt = Date.now();
    saveSnapshot();
    renderTasks();
    return task;
  }

  function uploadSelectedImage(file) {
    var base = localBase();
    if (!base) return Promise.reject(new Error('当前未检测到本机后端地址'));
    if (!file || !String(file.type || '').toLowerCase().startsWith('image/')) {
      return Promise.reject(new Error('请选择图片文件'));
    }
    if (file.size > 50 * 1024 * 1024) {
      return Promise.reject(new Error('图片不能超过 50MB'));
    }
    showMessage('正在上传参考图片...', false);
    var form = new FormData();
    form.append('file', file);
    var headers = authHeadersSafe();
    delete headers['Content-Type'];
    delete headers['content-type'];
    return fetch(base + '/api/assets/upload', {
      method: 'POST',
      headers: headers,
      body: form
    })
      .then(function(response) {
        return response.json().catch(function() { return {}; }).then(function(data) {
          return { ok: response.ok, data: data || {} };
        });
      })
      .then(function(result) {
        if (!result.ok || !result.data.asset_id) {
          throw new Error(messageText(result.data, '参考图片上传失败'));
        }
        var sourceUrl = String(
          result.data.source_url
          || result.data.public_url
          || (result.data.url && isUsableRemoteAssetUrl(result.data.url) ? result.data.url : '')
          || ''
        ).trim();
        var previewUrl = String(result.data.preview_url || result.data.local_preview_url || '').trim();
        state.selectedImage = {
          assetId: String(result.data.asset_id),
          name: file.name,
          sourceUrl: sourceUrl,
          previewUrl: previewUrl,
          file: file
        };
        if (state.imageObjectUrl) URL.revokeObjectURL(state.imageObjectUrl);
        state.imageObjectUrl = URL.createObjectURL(file);
        state.selectedImage.previewUrl = state.imageObjectUrl || previewUrl || sourceUrl;
        renderSelectedImage();
        saveSnapshot();
        if (getImageSourceUrl()) {
          showMessage('参考图片已上传并同步到云端。', false);
          return state.selectedImage;
        }
        showMessage('参考图片已上传，正在同步到云端...', false);
        return waitForPublicAssetUrl(state.selectedImage.assetId)
          .then(function(publicUrl) {
            state.selectedImage.sourceUrl = publicUrl;
            renderSelectedImage();
            saveSnapshot();
            showMessage('参考图片已上传并同步到云端。', false);
            return state.selectedImage;
          });
      });
  }

  function waitForPublicAssetUrl(assetId) {
    var id = String(assetId || '').trim();
    var base = localBase();
    if (!id || !base) return Promise.reject(new Error('参考图片没有可用的云端地址'));
    var maxAttempts = 6;
    var attempt = 0;

    function check() {
      attempt += 1;
      var endpoint = base + '/api/assets/' + encodeURIComponent(id);
      return fetch(endpoint, { headers: authHeadersSafe() })
        .then(function(response) {
          return response.json().catch(function() { return {}; }).then(function(data) {
            return { ok: response.ok, status: response.status, data: data || {} };
          });
        })
        .then(function(result) {
          if (!result.ok) {
            throw new Error('参考图片云端同步查询失败（HTTP ' + String(result.status || 0) + '）');
          }
          var publicUrl = String(
            result.data.source_url
            || result.data.public_url
            || (result.data.url && isUsableRemoteAssetUrl(result.data.url) ? result.data.url : '')
            || ''
          ).trim();
          if (isUsableRemoteAssetUrl(publicUrl)) return publicUrl;
          if (attempt >= maxAttempts) {
            throw new Error('参考图片尚未同步到云端，请稍后重试');
          }
          return waitMs(1000).then(check);
        });
    }
    return check();
  }

  function buildPayload(settings, prompt) {
    var payload = {
      task_text: String(prompt || settings.prompt || '').trim(),
      workflow_mode: 'direct_video',
      segment_count: 1,
      segment_duration_seconds: settings.duration,
      total_duration_seconds: settings.duration,
      merge_clips: false,
      auto_save: true,
      video_model: 'wan3.0',
      video_channel: 'dashscope',
      aspect_ratio: settings.aspectRatio,
      ratio: settings.aspectRatio,
      resolution: settings.resolution,
      visual_tone: 'clean_bright',
      rhythm: 'dynamic',
      generate_audio: false,
      watermark: false
    };
    if (state.selectedImage && state.selectedImage.assetId) {
      payload.asset_id = state.selectedImage.assetId;
    }
    return payload;
  }

  function submitOne(task, settings, prompt) {
    var base = localBase();
    if (!base) return Promise.reject(new Error('当前未检测到本机后端地址'));
    var taskPrompt = String(prompt || task.prompt || settings.prompt || '').trim();
    setTask(task.index, { status: 'submitting', error: '', prompt: taskPrompt });
    var endpoint = base + '/api/comfly-seedance-tvc/pipeline/start';
    return fetch(endpoint, {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify({ payload: buildPayload(settings, taskPrompt) })
    })
      .then(function(response) {
        return response.json().catch(function() { return {}; }).then(function(data) {
          return { ok: response.ok, status: response.status, data: data || {} };
        });
      })
      .then(function(result) {
        var jobId = result.data && result.data.job_id;
        if (!result.ok || !jobId) {
          throw new Error('视频任务提交失败（HTTP ' + String(result.status || 0) + '）：'
            + messageText(result.data, '未知错误') + '。请求地址：' + endpoint);
        }
        setTask(task.index, {
          jobId: String(jobId),
          status: 'running',
          error: '',
          createdAt: Date.now()
        });
        pollTask(task.index);
        return task;
      })
      .catch(function(error) {
        setTask(task.index, {
          status: 'failed',
          error: error && error.message ? error.message : '视频任务提交失败'
        });
        return task;
      });
  }

  function waitMs(milliseconds) {
    return new Promise(function(resolve) {
      window.setTimeout(resolve, milliseconds);
    });
  }

  function runSubmitPool(tasks, settings) {
    var batches = [];
    for (var start = 0; start < tasks.length; start += SUBMIT_CONCURRENCY) {
      batches.push(tasks.slice(start, start + SUBMIT_CONCURRENCY));
    }

    return batches.reduce(function(chain, batch, batchIndex) {
      return chain
        .then(function() {
          if (batchIndex === 0) return null;
          return waitMs(SUBMIT_BATCH_DELAY_MS);
        })
        .then(function() {
          return Promise.all(batch.map(function(task) {
            return submitOne(task, settings, task.prompt || settings.prompt);
          }));
        });
    }, Promise.resolve());
  }

  function pollTask(index) {
    var task = state.tasks.filter(function(item) { return Number(item.index) === Number(index); })[0];
    if (!task || !task.jobId || task.status === 'completed') return;
    if (state.polling[index]) return;
    state.polling[index] = true;

    function once() {
      var current = state.tasks.filter(function(item) { return Number(item.index) === Number(index); })[0];
      if (!current || !current.jobId) {
        delete state.polling[index];
        return;
      }
      var base = localBase();
      var endpoint = base + '/api/comfly-seedance-tvc/pipeline/jobs/' + encodeURIComponent(current.jobId) + '?compact=false';
      fetch(endpoint, {
        headers: authHeadersSafe()
      })
        .then(function(response) {
          return response.json().catch(function() { return {}; }).then(function(data) {
            return { ok: response.ok, status: response.status, data: data || {} };
          });
        })
        .then(function(result) {
          if (!result.ok) {
            throw new Error('任务状态查询失败（HTTP ' + String(result.status || 0) + '）：'
              + messageText(result.data, '未知错误') + '。请求地址：' + endpoint);
          }
          var data = result.data || {};
          var status = normalizeStatus(data.status);
          var videoUrl = extractVideoUrl(data);
          var error = data.error ? messageText({ error: data.error }, '') : '';
          setTask(index, {
            status: status,
            videoUrl: videoUrl || current.videoUrl || '',
            error: status === 'failed' ? (error || '视频任务执行失败') : '',
            progress: data.progress || null
          });
          if (status === 'completed' || status === 'failed') {
            delete state.polling[index];
            return;
          }
          window.setTimeout(once, POLL_INTERVAL_MS);
        })
        .catch(function(error) {
          var currentTask = state.tasks.filter(function(item) { return Number(item.index) === Number(index); })[0];
          if (!currentTask) {
            delete state.polling[index];
            return;
          }
          setTask(index, { error: error && error.message ? error.message : '状态查询失败' });
          window.setTimeout(once, 8000);
        });
    }
    once();
  }

  function pollAllActiveTasks() {
    state.tasks.forEach(function(task) {
      if (task.jobId && task.status !== 'completed' && task.status !== 'failed') pollTask(task.index);
    });
  }

  function extractChatText(data) {
    var choices = data && Array.isArray(data.choices) ? data.choices : [];
    var message = choices[0] && choices[0].message ? choices[0].message : {};
    var content = message.content;
    if (Array.isArray(content)) {
      return content.map(function(item) {
        return item && typeof item === 'object' ? String(item.text || '') : String(item || '');
      }).join('');
    }
    return String(content || data.content || '').trim();
  }

  function parseRewriteResponse(text) {
    var source = String(text || '').trim();
    source = source.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/i, '').trim();
    var candidates = [source];
    var objectStart = source.indexOf('{');
    var objectEnd = source.lastIndexOf('}');
    if (objectStart >= 0 && objectEnd > objectStart) candidates.push(source.slice(objectStart, objectEnd + 1));
    var arrayStart = source.indexOf('[');
    var arrayEnd = source.lastIndexOf(']');
    if (arrayStart >= 0 && arrayEnd > arrayStart) candidates.push(source.slice(arrayStart, arrayEnd + 1));
    for (var i = 0; i < candidates.length; i += 1) {
      try {
        var parsed = JSON.parse(candidates[i]);
        var values = Array.isArray(parsed) ? parsed : (parsed && (parsed.prompts || parsed.variants || parsed.items));
        if (!Array.isArray(values)) continue;
        var prompts = values.map(function(item) {
          return String(item && typeof item === 'object' ? (item.prompt || item.text || '') : item || '').trim();
        }).filter(Boolean);
        if (prompts.length >= 5) return prompts.slice(0, 5);
      } catch (e) {}
    }
    return [];
  }

  function rewritePromptsWithAI(settings) {
    // 本机后端不挂载 comfly-proxy；提示词改写走认证/计费云端，
    // 批量视频提交和任务轮询仍然走本机 pipeline。
    var base = cloudBase();
    if (!base) return Promise.reject(new Error('未检测到云端 AI 服务地址'));
    var sourcePrompt = String(settings.prompt || '').trim();
    var content = [{
      type: 'text',
      text: [
        '请为批量视频生成任务改写提示词。',
        '根据用户原始提示词，生成 5 条明显不同的创作方向；每条都必须保留用户的核心主体、动作和目标，不要改变用户意图。',
        '如果有参考图片，请结合图片中的主体、场景、产品或人物特征，但不要凭空添加品牌信息。',
        '5 条提示词要在镜头、动作、场景氛围、叙事方式或视觉重点上有明显区别，适合直接提交给文生视频或图生视频模型。',
        '只返回 JSON，不要 Markdown、解释或编号，格式必须是：{"prompts":["提示词1","提示词2","提示词3","提示词4","提示词5"]}',
        '用户原始提示词：' + sourcePrompt
      ].join('\n')
    }];
    var imageUrl = getImageSourceUrl();
    if (state.selectedImage && !imageUrl) {
      return waitForPublicAssetUrl(state.selectedImage.assetId)
        .then(function(publicUrl) {
          state.selectedImage.sourceUrl = publicUrl;
          saveSnapshot();
          return rewritePromptsWithAI(settings);
        });
    }
    if (imageUrl) content.push({ type: 'image_url', image_url: { url: imageUrl } });
    var models = ['gpt-5.5', 'gpt-5.4'];
    var lastError = '';

    function attempt(index) {
      if (index >= models.length) {
        return Promise.reject(new Error(lastError || 'AI 改写未返回有效的 5 条提示词'));
      }
      var endpoint = base + '/api/comfly-proxy/v1/chat/completions';
      return fetch(endpoint, {
        method: 'POST',
        headers: jsonHeaders(),
        body: JSON.stringify({
          model: models[index],
          stream: false,
          messages: [
            { role: 'system', content: '你是批量短视频提示词导演，严格输出用户要求的 JSON。' },
            { role: 'user', content: content }
          ],
          max_tokens: 3000
        })
      })
        .then(function(response) {
          return response.json().catch(function() { return {}; }).then(function(data) {
            return { ok: response.ok, status: response.status, data: data || {} };
          });
        })
        .then(function(result) {
          if (!result.ok) {
            var detail = messageText(result.data, 'HTTP ' + String(result.status || 0));
            throw new Error('AI 改写请求失败（' + result.status + '）：' + detail + '。请求地址：' + endpoint);
          }
          var prompts = parseRewriteResponse(extractChatText(result.data));
          if (prompts.length < 5) throw new Error('AI 改写未返回完整的 5 条提示词');
          return prompts;
        })
        .catch(function(error) {
          lastError = error && error.message ? error.message : 'AI 改写请求失败';
          return attempt(index + 1);
        });
    }
    return attempt(0);
  }

  function startBatch() {
    if (state.submitting) return;
    var settings = getSettings();
    if (!settings.prompt) {
      showMessage('请先输入视频提示词。', true);
      return;
    }
    state.submitting = true;
    renderTasks();
    var promptPromise = settings.autoRewritePrompt
      ? (showMessage('正在根据提示词' + (getImageSourceUrl() ? '和参考图片' : '') + '生成 5 个创作方向...', false), rewritePromptsWithAI(settings))
      : Promise.resolve([settings.prompt]);
    promptPromise
      .then(function(promptVariants) {
        state.rewritePrompts = promptVariants.slice();
        state.batchId = 'batch-' + Date.now().toString(36);
        state.createdAt = Date.now();
        state.tasks = Array.from({ length: settings.count }, function(_, index) {
          return normalizeTask({
            index: index,
            prompt: promptVariants[index % promptVariants.length],
            status: 'waiting',
            createdAt: Date.now()
          }, index);
        });
        saveSnapshot();
        renderTasks();
        showMessage(
          settings.autoRewritePrompt
            ? '已生成 5 个提示词方向，正在提交 ' + settings.count + ' 条独立视频任务，每批最多 5 条，批次间隔 1 秒...'
            : '正在提交 ' + settings.count + ' 条独立视频任务，每批最多 5 条，批次间隔 1 秒...',
          false
        );
        return runSubmitPool(state.tasks.slice(), settings);
      })
      .then(function() {
        var counts = taskCounts();
        showMessage('批量提交完成：已完成 ' + counts.completed + ' 条，生成中 ' + counts.running + ' 条，失败 ' + counts.failed + ' 条。', counts.failed > 0);
      })
      .catch(function(error) {
        showMessage(error && error.message ? error.message : '批量任务提交失败', true);
      })
      .finally(function() {
        state.submitting = false;
        renderTasks();
      });
  }

  function retryTask(index) {
    if (state.submitting) return;
    var task = state.tasks.filter(function(item) { return Number(item.index) === Number(index); })[0];
    if (!task) return;
    var settings = getSettings();
    state.submitting = true;
    setTask(index, { status: 'waiting', jobId: '', videoUrl: '', error: '' });
    showMessage('正在重试视频 ' + (Number(index) + 1) + '...', false);
    submitOne(task, settings, task.prompt || settings.prompt).finally(function() {
      state.submitting = false;
      renderTasks();
    });
  }

  function openVideo(url) {
    if (!url) return;
    try { window.open(url, '_blank', 'noopener'); } catch (e) { window.location.href = url; }
  }

  function downloadVideo(url, index) {
    if (!url) return;
    var link = document.createElement('a');
    link.href = url;
    link.download = 'batch-creative-video-' + (Number(index) + 1) + '.mp4';
    link.target = '_blank';
    link.rel = 'noopener';
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  function clearResults() {
    Object.keys(state.polling).forEach(function(key) { delete state.polling[key]; });
    state.tasks = [];
    state.batchId = '';
    state.createdAt = 0;
    clearSnapshot();
    renderTasks();
    showMessage('已清空当前页面的结果记录。', false);
  }

  function restoreSnapshot() {
    var snapshot = readSnapshot();
    if (!snapshot) {
      renderSelectedImage();
      renderCountChoice();
      renderTasks();
      return;
    }
    state.batchId = String(snapshot.batchId || '');
    state.createdAt = Number(snapshot.createdAt || 0);
    state.selectedCount = clampBatchCount(snapshot.count);
    state.selectedAspectRatio = normalizeAspectRatio(snapshot.aspectRatio);
    state.selectedResolution = normalizeResolution(snapshot.resolution);
    state.selectedDuration = normalizeDuration(snapshot.duration);
    state.autoRewritePrompt = !!snapshot.autoRewritePrompt;
    state.rewritePrompts = Array.isArray(snapshot.rewritePrompts)
      ? snapshot.rewritePrompts.map(function(item) { return String(item || '').trim(); }).filter(Boolean).slice(0, 5)
      : [];
    state.tasks = snapshot.tasks.map(normalizeTask);
    if (snapshot.prompt && $('batchCreativePrompt')) $('batchCreativePrompt').value = snapshot.prompt;
    var rewrite = $('batchCreativeAutoRewrite');
    if (rewrite) rewrite.checked = state.autoRewritePrompt;
    if (snapshot.image && snapshot.image.assetId) {
      state.selectedImage = {
        assetId: String(snapshot.image.assetId),
        name: String(snapshot.image.name || '参考图片'),
        sourceUrl: String(snapshot.image.sourceUrl || ''),
        previewUrl: String(snapshot.image.sourceUrl || snapshot.image.previewUrl || '')
      };
    }
    renderSelectedImage();
    renderCountChoice();
    renderVideoSettings();
    renderTasks();
    pollAllActiveTasks();
  }

  function bindEvents() {
    var imageBtn = $('batchCreativeImageBtn');
    var imageInput = $('batchCreativeImageInput');
    var imageRemove = $('batchCreativeImageRemove');
    var drop = $('batchCreativeImageDrop');
    if (imageBtn && imageInput) imageBtn.addEventListener('click', function() { imageInput.click(); });
    if (imageRemove && imageInput) imageRemove.addEventListener('click', function() { imageInput.click(); });
    if (drop && imageInput) {
      drop.addEventListener('dragover', function(event) {
        event.preventDefault();
        drop.classList.add('is-dragover');
      });
      drop.addEventListener('dragleave', function() { drop.classList.remove('is-dragover'); });
      drop.addEventListener('drop', function(event) {
        event.preventDefault();
        drop.classList.remove('is-dragover');
        var file = event.dataTransfer && event.dataTransfer.files ? event.dataTransfer.files[0] : null;
        if (file) uploadSelectedImage(file).catch(function(error) { showMessage(error.message || '上传失败', true); });
      });
    }
    if (imageInput) {
      imageInput.addEventListener('change', function() {
        var file = imageInput.files && imageInput.files[0];
        if (!file) return;
        uploadSelectedImage(file).catch(function(error) { showMessage(error.message || '上传失败', true); });
        imageInput.value = '';
      });
    }
    var countRange = $('batchCreativeCountRange');
    if (countRange) {
      var updateCount = function() {
        state.selectedCount = clampBatchCount(countRange.value);
        renderCountChoice();
        saveSnapshot();
      };
      countRange.addEventListener('input', updateCount);
      countRange.addEventListener('change', updateCount);
    }
    var aspectSelect = $('batchCreativeAspectSelect');
    if (aspectSelect) {
      aspectSelect.addEventListener('change', function() {
        state.selectedAspectRatio = normalizeAspectRatio(aspectSelect.value);
        renderVideoSettings();
        saveSnapshot();
        renderTasks();
      });
    }
    var resolutionSelect = $('batchCreativeResolutionSelect');
    if (resolutionSelect) {
      resolutionSelect.addEventListener('change', function() {
        state.selectedResolution = normalizeResolution(resolutionSelect.value);
        renderVideoSettings();
        saveSnapshot();
      });
    }
    var durationSelect = $('batchCreativeDurationSelect');
    if (durationSelect) {
      var updateDuration = function() {
        state.selectedDuration = normalizeDuration(durationSelect.value);
        renderVideoSettings();
        saveSnapshot();
      };
      durationSelect.addEventListener('input', updateDuration);
      durationSelect.addEventListener('change', updateDuration);
    }
    var rewrite = $('batchCreativeAutoRewrite');
    if (rewrite) {
      rewrite.addEventListener('change', function() {
        state.autoRewritePrompt = !!rewrite.checked;
        saveSnapshot();
      });
    }
    var start = $('batchCreativeStartBtn');
    if (start) start.addEventListener('click', startBatch);
    var clear = $('batchCreativeClearBtn');
    if (clear) clear.addEventListener('click', clearResults);
    var refresh = $('batchCreativeRefreshBtn');
    if (refresh) refresh.addEventListener('click', function() {
      pollAllActiveTasks();
      showMessage('正在刷新未完成任务状态...', false);
    });
    var back = $('batchCreativeBackBtn');
    if (back) back.addEventListener('click', function() {
      if (typeof window.showAppView === 'function') {
        window.showAppView('chat').catch(function() {});
      } else {
        window.location.hash = 'chat';
      }
    });
    var grid = $('batchCreativeResultGrid');
    if (grid) {
      grid.addEventListener('click', function(event) {
        var target = event.target;
        if (!target || !target.closest) return;
        var open = target.closest('[data-batch-open]');
        if (open) {
          openVideo(open.getAttribute('data-batch-open'));
          return;
        }
        var download = target.closest('[data-batch-download]');
        if (download) {
          downloadVideo(download.getAttribute('data-batch-download'), download.getAttribute('data-batch-index'));
          return;
        }
        var retry = target.closest('[data-batch-retry]');
        if (retry) retryTask(Number(retry.getAttribute('data-batch-retry')));
      });
    }
  }

  window.initBatchCreativeVideoView = function() {
    if (state.initialized) {
      renderSelectedImage();
      renderCountChoice();
      renderTasks();
      pollAllActiveTasks();
      return;
    }
    state.initialized = true;
    bindEvents();
    restoreSnapshot();
    renderVideoSettings();
  };
})();
