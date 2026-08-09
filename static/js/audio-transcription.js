(function audioTranscriptionModule() {
  'use strict';

  var AUDIO_PATTERN = /\.(mp3|wav|m4a|aac|ogg|flac|amr|wma|opus|webm)$/i;
  var MAX_AUDIO_BYTES = 200 * 1024 * 1024;
  var state = {
    root: null,
    tab: 'records',
    localFiles: [],
    records: [],
    page: 1,
    pageSize: 20,
    total: 0,
    uploadJobs: [],
    detail: null,
    audioUrl: '',
    poller: null,
    uploadPoller: null,
    loading: false
  };

  function el(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function recordTimestamp(row) {
    var value = row && (row.recorded_at || row.created_at || row.updated_at || row.queued_at);
    var parsed = Date.parse(String(value || ''));
    return Number.isFinite(parsed) ? parsed : 0;
  }
  function newestFirst(left, right) {
    return recordTimestamp(right) - recordTimestamp(left) || Number(right && right.id || 0) - Number(left && left.id || 0);
  }
  function cloudBase() {
    return String((typeof API_BASE !== 'undefined' && API_BASE) || window.__API_BASE || '').replace(/\/$/, '');
  }
  function localBase() {
    return String((typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) || '').replace(/\/$/, '');
  }
  function requestHeaders(json) {
    var headers = typeof authHeaders === 'function' ? Object.assign({}, authHeaders() || {}) : {};
    if (json === false) {
      delete headers['Content-Type'];
      delete headers['content-type'];
    } else {
      headers['Content-Type'] = 'application/json';
    }
    return headers;
  }
  function parseError(data, fallback) {
    if (!data) return fallback || '请求失败';
    if (typeof data === 'string') return data;
    var detail = data.detail || data.message || data.error;
    if (typeof detail === 'string') return detail;
    try { return JSON.stringify(detail || data); } catch (err) { return fallback || '请求失败'; }
  }
  function requestJson(path, options) {
    options = options || {};
    var request = {
      method: options.method || 'GET',
      headers: requestHeaders(options.json !== false),
      cache: options.cache || 'no-store'
    };
    if (options.body !== undefined) request.body = JSON.stringify(options.body || {});
    return fetch(cloudBase() + path, request).then(function(response) {
      return response.json().catch(function() { return {}; }).then(function(data) {
        if (!response.ok || data.ok === false) throw new Error(parseError(data, '请求失败'));
        return data;
      });
    });
  }
  function requestLocalJson(path, options) {
    var base = localBase();
    if (!base) return Promise.resolve({ items: [] });
    options = options || {};
    var request = {
      method: options.method || 'GET',
      headers: requestHeaders(options.json !== false),
      cache: options.cache || 'no-store'
    };
    if (options.body !== undefined) request.body = JSON.stringify(options.body || {});
    return fetch(base + path, request).then(function(response) {
      return response.json().catch(function() { return {}; }).then(function(data) {
        if (!response.ok || data.ok === false) throw new Error(parseError(data, '本机任务请求失败'));
        return data;
      });
    });
  }
  function uploadForm(url, form, button, timeoutMs, localUpload) {
    return new Promise(function(resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open('POST', url, true);
      xhr.timeout = timeoutMs || 20 * 60 * 1000;
      var headers = requestHeaders(false);
      Object.keys(headers).forEach(function(name) {
        if (String(name).toLowerCase() !== 'content-type') xhr.setRequestHeader(name, headers[name]);
      });
      xhr.upload.onprogress = function(event) {
        if (!event.lengthComputable || !button) return;
        var percent = Math.max(1, Math.min(99, Math.round(event.loaded * 100 / event.total)));
        button.textContent = (localUpload ? '写入本机 ' : '上传 ') + percent + '%';
      };
      xhr.onload = function() {
        var data = {};
        try { data = JSON.parse(xhr.responseText || '{}'); } catch (error) { data = {}; }
        if (xhr.status < 200 || xhr.status >= 300 || data.ok === false) {
          reject(new Error(parseError(data, '音频上传失败（HTTP ' + xhr.status + '）')));
          return;
        }
        resolve(data);
      };
      xhr.onerror = function() { reject(new Error('音频上传连接中断，请检查网络后重试')); };
      xhr.ontimeout = function() { reject(new Error('音频上传超时，已停止本次上传，请重试')); };
      xhr.onabort = function() { reject(new Error('音频上传已取消')); };
      xhr.send(form);
    });
  }
  function setMessage(message, isError) {
    var target = el('atMessage');
    if (!target) return;
    target.textContent = message || '';
    target.classList.toggle('is-error', !!isError);
  }
  function setButtonBusy(button, busy, label) {
    if (!button) return;
    if (busy) {
      button.dataset.oldText = button.textContent || '';
      button.textContent = label || '处理中...';
      button.disabled = true;
    } else {
      button.textContent = button.dataset.oldText || button.textContent || '';
      button.disabled = false;
      delete button.dataset.oldText;
    }
  }
  function formatBytes(value) {
    var size = Math.max(0, Number(value || 0));
    if (size < 1024) return size + ' B';
    if (size < 1024 * 1024) return (size / 1024).toFixed(1) + ' KB';
    return (size / 1024 / 1024).toFixed(1) + ' MB';
  }
  function formatDate(value) {
    return String(value || '').replace('T', ' ').slice(0, 16);
  }
  function statusLabel(value) {
    return ({ processing: '秘书正在整理', completed: '已整理', failed: '整理失败' })[value] || value || '等待整理';
  }
  function speakerLabel(value) {
    var speaker = String(value || '').trim();
    if (!speaker || speaker === '未知') return '说话人';
    return /^[A-Z]$/.test(speaker) ? '说话人 ' + speaker : speaker;
  }
  function progressInfo(row) {
    if (row && row.status === 'completed') return { percent: 100, label: '秘书整理已完成' };
    if (row && row.status === 'failed') return { percent: 100, label: '整理失败' };
    if (row && row.process_stage === 'summarizing') return { percent: 85, label: '正在提炼摘要和待办' };
    var chunkMatch = /^transcribing:(\d+)\/(\d+)$/.exec(String(row && row.process_stage || ''));
    if (chunkMatch) {
      var current = Math.max(1, Number(chunkMatch[1] || 1));
      var total = Math.max(current, Number(chunkMatch[2] || 1));
      var percent = total === 1 ? 55 : Math.min(80, 35 + Math.round(45 * (current - 1) / total));
      return { percent: percent, label: '正在识别第 ' + current + ' / ' + total + ' 段音频' };
    }
    if (row && row.process_stage === 'transcribing') return { percent: 55, label: '正在识别语音和区分说话人' };
    return { percent: 20, label: '音频已上传，等待处理' };
  }

  function fileKey(file) {
    return [file && file.name || '', file && file.size || 0, file && file.lastModified || 0].join('|');
  }
  function isAudioFile(file) {
    return !!file && (String(file.type || '').toLowerCase().indexOf('audio/') === 0 || AUDIO_PATTERN.test(file.name || ''));
  }
  function addLocalFiles(files) {
    var seen = {};
    state.localFiles.forEach(function(file) { seen[fileKey(file)] = true; });
    var rejected = [];
    Array.prototype.forEach.call(files || [], function(file) {
      if (!isAudioFile(file) || Number(file.size || 0) > MAX_AUDIO_BYTES) {
        rejected.push(file.name || '未命名文件');
        return;
      }
      var key = fileKey(file);
      if (!seen[key]) {
        seen[key] = true;
        state.localFiles.push(file);
      }
    });
    renderLocalFiles();
    if (rejected.length) setMessage('以下文件无法转写：' + rejected.join('、'), true);
  }
  function renderLocalFiles() {
    var host = el('atLocalList');
    if (!host) return;
    host.innerHTML = state.localFiles.length ? state.localFiles.map(function(file, index) {
      return '<div class="at-row"><div class="at-row-main"><strong>' + esc(file.name || '未命名音频') +
        '</strong><div class="at-row-meta"><span>' + esc(formatBytes(file.size)) + '</span><span>本地音频</span></div></div>' +
        '<div class="at-row-actions"><button type="button" class="at-button" data-at-upload="' + index + '">交给秘书整理</button>' +
        '<button type="button" class="at-button at-button-danger" data-at-local-remove="' + index + '">移除</button></div></div>';
    }).join('') : '<div class="at-empty">尚未选择音频文件</div>';
  }
  function uploadLocalFile(index, button) {
    var file = state.localFiles[Number(index)];
    if (!file) return Promise.resolve();
    var form = new FormData();
    form.append('file', file, file.name || 'audio.wav');
    form.append('source_type', 'local');
    form.append('source_name', 'Online 本地音频');
    if (typeof getOrCreateInstallationId === 'function') form.append('installation_id', getOrCreateInstallationId());
    setButtonBusy(button, true, '上传中...');
    setMessage('正在上传“' + (file.name || '音频') + '”');
    var local = localBase();
    var target = local ? local + '/api/audio-transcription/local-uploads' : cloudBase() + '/api/h5/recorder/files';
    return uploadForm(target, form, button, local ? 5 * 60 * 1000 : 20 * 60 * 1000, !!local).then(function(data) {
      var key = fileKey(file);
      state.localFiles = state.localFiles.filter(function(item) { return fileKey(item) !== key; });
      renderLocalFiles();
      setMessage(local ? '音频已保存到本机，正在后台上传并转写，可以继续操作其他功能' : '已上传，正在后台转写');
      setTab('records');
      return data;
    }).catch(function(error) {
      setMessage(error.message || '音频上传失败', true);
    }).then(function(result) {
      setButtonBusy(button, false);
      return result;
    });
  }

  function localUploadStatus(job) {
    var status = String(job && job.status || 'queued');
    if (status === 'receiving') return '正在保存到本机';
    if (status === 'queued') return '等待后台上传';
    if (status === 'uploading') return '正在后台上传';
    if (status === 'failed') return '上传失败';
    return '已提交转写';
  }
  function localUploadRow(job) {
    var status = String(job.status || 'queued');
    return '<div class="at-row"><div class="at-row-main"><strong>' + esc(job.file_name || '本机音频') + '</strong>' +
      '<div class="at-row-meta"><span>' + esc(formatDate(job.created_at)) + '</span><span>' + esc(formatBytes(job.file_size)) +
      '</span><span class="at-record-status ' + esc(status) + '">' + esc(localUploadStatus(job)) + '</span></div>' +
      (status === 'queued' || status === 'uploading' ? '<div class="at-progress-track"><i style="width:' + (status === 'uploading' ? 62 : 28) + '%"></i></div>' : '') +
      (job.error ? '<div class="at-local-upload-error">' + esc(job.error) + '</div>' : '') + '</div>' +
      '<div class="at-row-actions">' + (status === 'failed' ? '<button type="button" class="at-button" data-at-local-retry="' + esc(job.job_id) + '">重试</button>' : '') +
      (status === 'failed' ? '<button type="button" class="at-button at-button-danger" data-at-local-delete="' + esc(job.job_id) + '">删除</button>' : '') + '</div></div>';
  }

  function recordRow(row) {
    var progress = progressInfo(row);
    return '<div class="at-row"><div class="at-row-main"><strong>' + esc(row.display_name || row.file_name || '音频转写') + '</strong>' +
      '<div class="at-row-meta"><span>' + esc(formatDate(row.recorded_at || row.created_at)) + '</span><span>' + esc(row.source_label || '音频文件') +
      '</span><span>' + esc(formatBytes(row.file_size)) + '</span><span class="at-record-status ' + esc(row.status || '') + '">' + esc(statusLabel(row.status)) + '</span></div>' +
      (row.status === 'processing' ? '<div class="at-progress-track"><i style="width:' + progress.percent + '%"></i></div>' : '') + '</div>' +
      '<div class="at-row-actions"><button type="button" class="at-button at-button-quiet" data-at-detail="' + Number(row.id) + '">' + (row.status === 'completed' ? '查看结果' : '查看进度') + '</button>' +
      (row.status === 'failed' ? '<button type="button" class="at-button" data-at-retry="' + Number(row.id) + '">重新处理</button>' : '') +
      '<button type="button" class="at-button at-button-quiet" data-at-rename="' + Number(row.id) + '" data-at-name="' + esc(row.display_name || row.file_name || '') + '">改名</button>' +
      '<button type="button" class="at-button at-button-danger" data-at-delete="' + Number(row.id) + '">删除</button></div></div>';
  }
  function currentListHost() {
    return state.tab === 'mobile' ? el('atMobileList') : el('atRecordList');
  }
  function currentPagerHost() {
    return state.tab === 'mobile' ? el('atMobilePager') : el('atRecordPager');
  }
  function renderRecords() {
    var host = currentListHost();
    if (!host) return;
    var pending = state.tab === 'records' ? state.uploadJobs.filter(function(job) { return job.status !== 'completed'; }).slice().sort(newestFirst) : [];
    var rows = pending.map(localUploadRow).join('') + state.records.map(recordRow).join('');
    host.innerHTML = rows || '<div class="at-empty">暂无记录</div>';
    if (el('atOverviewTotal')) el('atOverviewTotal').textContent = String(state.total || 0);
    if (el('atOverviewCompleted')) el('atOverviewCompleted').textContent = String(state.records.filter(function(row) { return row.status === 'completed'; }).length);
    if (el('atOverviewPending')) el('atOverviewPending').textContent = String(state.records.filter(function(row) { return row.status === 'processing'; }).length + pending.length);
    var pager = currentPagerHost();
    if (!pager) return;
    var totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
    pager.innerHTML = '<button type="button" class="at-button at-button-quiet" data-at-page="' + (state.page - 1) + '"' + (state.page <= 1 ? ' disabled' : '') + '>上一页</button>' +
      '<span>第 ' + state.page + ' / ' + totalPages + ' 页，共 ' + state.total + ' 条</span>' +
      '<button type="button" class="at-button at-button-quiet" data-at-page="' + (state.page + 1) + '"' + (state.page >= totalPages ? ' disabled' : '') + '>下一页</button>';
    clearTimeout(state.uploadPoller);
    if (state.tab === 'records' && state.uploadJobs.some(function(job) { return ['receiving', 'queued', 'uploading'].indexOf(job.status) >= 0; })) {
      state.uploadPoller = setTimeout(function() { loadRecords({ silent: true }); }, 2500);
    }
  }
  function loadRecords(options) {
    options = options || {};
    if (state.loading || state.tab === 'local') return Promise.resolve();
    state.loading = true;
    var host = currentListHost();
    if (host && !options.silent) host.innerHTML = '<div class="at-empty">正在读取记录...</div>';
    var query = '?page=' + state.page + '&page_size=' + state.pageSize + (state.tab === 'mobile' ? '&source_type=device' : '');
    var cloudRequest = requestJson('/api/h5/recorder/files' + query)
      .then(function(data) { return { data: data }; })
      .catch(function(error) { return { error: error }; });
    var localRequest = state.tab === 'records' && localBase()
      ? requestLocalJson('/api/audio-transcription/local-uploads').catch(function() { return { items: [] }; })
      : Promise.resolve({ items: [] });
    return Promise.all([cloudRequest, localRequest]).then(function(results) {
      var cloud = results[0];
      var local = results[1];
      if (cloud.error) throw cloud.error;
      state.records = (Array.isArray(cloud.data.items) ? cloud.data.items : []).slice().sort(newestFirst);
      state.total = Number(cloud.data.total || 0);
      state.page = Number(cloud.data.page || state.page);
      var cloudRecordIds = {};
      state.records.forEach(function(row) {
        var recordId = Number(row && row.id || 0);
        if (recordId > 0) cloudRecordIds[String(recordId)] = true;
      });
      // Once the cloud record exists, it is the source of truth. Do not render
      // the local upload snapshot on top of a newer cloud status.
      state.uploadJobs = (Array.isArray(local.items) ? local.items : []).filter(function(job) {
        var recordId = Number(job && job.record && job.record.id || 0);
        return !(recordId > 0 && cloudRecordIds[String(recordId)]);
      }).sort(newestFirst);
      renderRecords();
      setMessage('');
    }).catch(function(error) {
      state.records = [];
      state.total = 0;
      renderRecords();
      setMessage(error.message || '记录加载失败', true);
    }).then(function() { state.loading = false; });
  }
  function setTab(tab) {
    state.tab = ['records', 'local', 'mobile'].indexOf(tab) >= 0 ? tab : 'records';
    state.page = 1;
    state.root.querySelectorAll('[data-at-tab]').forEach(function(button) {
      var active = button.dataset.atTab === state.tab;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    state.root.querySelectorAll('[data-at-panel]').forEach(function(panel) {
      panel.hidden = panel.dataset.atPanel !== state.tab;
    });
    if (state.tab !== 'records') clearTimeout(state.uploadPoller);
    if (state.tab !== 'local') loadRecords();
  }

  function retryLocalUpload(jobId, button) {
    setButtonBusy(button, true, '提交中...');
    return requestLocalJson('/api/audio-transcription/local-uploads/' + encodeURIComponent(jobId) + '/retry', { method: 'POST' })
      .then(function() { setMessage('已重新加入本机后台上传队列'); return loadRecords(); })
      .catch(function(error) { setMessage(error.message || '重试失败', true); })
      .then(function() { setButtonBusy(button, false); });
  }
  function deleteLocalUpload(jobId) {
    if (!window.confirm('删除这条本机上传任务？')) return Promise.resolve();
    return requestLocalJson('/api/audio-transcription/local-uploads/' + encodeURIComponent(jobId), { method: 'DELETE' })
      .then(function() { setMessage('本机上传任务已删除'); return loadRecords(); });
  }

  function releaseAudio() {
    var audio = el('atDetailAudio');
    if (audio) {
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
    }
    if (state.audioUrl) URL.revokeObjectURL(state.audioUrl);
    state.audioUrl = '';
  }
  function loadAudio(recordId) {
    releaseAudio();
    return fetch(cloudBase() + '/api/h5/recorder/files/' + encodeURIComponent(recordId) + '/audio', {
      headers: requestHeaders(false),
      cache: 'no-store'
    }).then(function(response) {
      if (!response.ok) return response.json().catch(function() { return {}; }).then(function(data) { throw new Error(parseError(data, '音频加载失败')); });
      return response.blob();
    }).then(function(blob) {
      if (!state.detail || Number(state.detail.id) !== Number(recordId)) return;
      state.audioUrl = URL.createObjectURL(blob);
      var audio = el('atDetailAudio');
      if (audio) audio.src = state.audioUrl;
    }).catch(function(error) { setMessage(error.message || '音频加载失败', true); });
  }
  function summaryText(row) {
    if (!row) return '';
    var points = Array.isArray(row.key_points) ? row.key_points.map(function(item) { return String(item || '').trim(); }).filter(Boolean) : [];
    return [
      row.display_name || row.file_name || '秘书记录',
      row.summary_text ? '秘书摘要\n' + String(row.summary_text).trim() : '',
      points.length ? '秘书待办\n' + points.map(function(item, index) { return (index + 1) + '. ' + item; }).join('\n') : ''
    ].filter(Boolean).join('\n\n');
  }
  function transcriptText(row) {
    if (!row) return '';
    var segments = Array.isArray(row.segments) ? row.segments : [];
    var body = segments.length ? segments.map(function(item) {
      return speakerLabel(item.speaker) + '：' + String(item.text || '').trim();
    }).filter(function(line) { return !/：$/.test(line); }).join('\n') : String(row.transcript_text || '').trim();
    return [row.display_name || row.file_name || '秘书记录', '完整转写', body].filter(Boolean).join('\n\n');
  }
  function renderDetail(row) {
    state.detail = row;
    el('atDetailTitle').textContent = row.display_name || row.file_name || '转写详情';
    el('atDetailMeta').innerHTML = [formatDate(row.recorded_at || row.created_at), row.source_label || '音频文件', formatBytes(row.file_size)]
      .filter(Boolean).map(function(item) { return '<span>' + esc(item) + '</span>'; }).join('');
    var status = el('atDetailStatus');
    status.textContent = statusLabel(row.status);
    status.className = 'at-status ' + esc(row.status || '');
    state.root.querySelectorAll('[data-at-copy], [data-at-export]').forEach(function(button) {
      button.disabled = row.status !== 'completed';
    });
    var progress = progressInfo(row);
    el('atDetailProgress').innerHTML = '<span>' + esc(progress.label) + '</span><div class="at-progress-track"><i style="width:' + progress.percent + '%"></i></div>' +
      (row.error_message ? '<p>' + esc(row.error_message) + '</p>' : '');
    el('atSummary').textContent = row.status === 'completed' ? (row.summary_text || '暂无总结') : '处理完成后显示 AI 摘要';
    var points = Array.isArray(row.key_points) ? row.key_points : [];
    el('atKeyPoints').innerHTML = points.length ? points.map(function(item, index) {
      return '<div class="at-key-point"><span>' + (index + 1) + '</span><p>' + esc(item) + '</p></div>';
    }).join('') : '<div class="at-empty">暂无重点事项</div>';
    var segments = Array.isArray(row.segments) ? row.segments : [];
    el('atDialogue').innerHTML = segments.length ? segments.map(function(item) {
      return '<div class="at-line"><button type="button" class="at-speaker" data-at-speaker="' + esc(item.speaker || '未知') + '" data-at-speaker-id="' + esc(item.speaker_id || '') + '">' + esc(speakerLabel(item.speaker)) + '</button><p>' + esc(item.text || '') + '</p></div>';
    }).join('') : '<div class="at-empty">' + esc(row.transcript_text || (row.status === 'processing' ? '正在生成完整转写...' : '暂无转写')) + '</div>';
  }
  function setResultTab(tab) {
    var value = tab === 'transcript' ? 'transcript' : 'summary';
    state.root.querySelectorAll('[data-at-result-tab]').forEach(function(button) {
      button.classList.toggle('is-active', button.dataset.atResultTab === value);
    });
    state.root.querySelectorAll('[data-at-result-panel]').forEach(function(panel) {
      panel.hidden = panel.dataset.atResultPanel !== value;
    });
  }
  function showMain() {
    clearTimeout(state.poller);
    releaseAudio();
    state.root.querySelector('[data-at-screen="main"]').hidden = false;
    state.root.querySelector('[data-at-screen="detail"]').hidden = true;
    if (state.tab !== 'local') loadRecords();
  }
  function showDetail(recordId, preserveAudio) {
    clearTimeout(state.poller);
    return requestJson('/api/h5/recorder/files/' + encodeURIComponent(recordId)).then(function(row) {
      renderDetail(row);
      state.root.querySelector('[data-at-screen="main"]').hidden = true;
      state.root.querySelector('[data-at-screen="detail"]').hidden = false;
      if (!preserveAudio) loadAudio(recordId);
      if (row.status === 'processing') {
        state.poller = setTimeout(function() { showDetail(recordId, true); }, 4000);
      }
    }).catch(function(error) { setMessage(error.message || '详情加载失败', true); });
  }

  function copyText(value) {
    value = String(value || '');
    if (!value) return Promise.resolve(false);
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(value).then(function() { return true; }).catch(function() { return fallbackCopy(value); });
    return Promise.resolve(fallbackCopy(value));
  }
  function fallbackCopy(value) {
    var textarea = document.createElement('textarea');
    textarea.value = value;
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    document.body.appendChild(textarea);
    textarea.select();
    var copied = false;
    try { copied = document.execCommand('copy'); } catch (err) { copied = false; }
    textarea.remove();
    return copied;
  }
  function exportText(kind) {
    var value = kind === 'transcript' ? transcriptText(state.detail) : summaryText(state.detail);
    if (!value) return setMessage('当前没有可导出的内容', true);
    var blob = new Blob(['\uFEFF' + value], { type: 'text/plain;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var anchor = document.createElement('a');
    var fileName = String(state.detail && (state.detail.display_name || state.detail.file_name) || '秘书记录').replace(/[\\/:*?"<>|]+/g, '-').slice(0, 80);
    anchor.href = url;
    anchor.download = fileName + '-' + (kind === 'transcript' ? '完整转写' : '秘书摘要') + '.txt';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
  }
  function renameSpeaker(speaker, speakerId) {
    if (!state.detail) return Promise.resolve();
    var current = String(speaker || '').trim();
    var stableSpeakerId = String(speakerId || '').trim();
    var next = window.prompt('输入说话人姓名，本条记录中该说话人的所有片段会一起修改', speakerLabel(current));
    if (next === null || !next.trim() || next.trim() === current || next.trim() === speakerLabel(current)) return Promise.resolve();
    var body = { speaker: current, display_name: next.trim() };
    if (stableSpeakerId) body.speaker_id = stableSpeakerId;
    return requestJson('/api/h5/recorder/files/' + encodeURIComponent(state.detail.id) + '/speakers', {
      method: 'PATCH',
      body: body
    }).then(function() {
      setMessage('说话人名称已批量更新');
      return showDetail(state.detail.id, true);
    });
  }
  function renameRecord(id, oldName) {
    var next = window.prompt('输入新的录音名称', oldName || '');
    if (next === null || !next.trim() || next.trim() === oldName) return Promise.resolve();
    return requestJson('/api/h5/recorder/files/' + encodeURIComponent(id), { method: 'PATCH', body: { display_name: next.trim() } }).then(loadRecords);
  }
  function retryRecord(id, button) {
    setButtonBusy(button, true, '提交中...');
    return requestJson('/api/h5/recorder/files/' + encodeURIComponent(id) + '/retry', { method: 'POST' })
      .then(function() { setMessage('已重新开始转写'); return showDetail(id); })
      .catch(function(error) { setMessage(error.message || '重新处理失败', true); })
      .then(function() { setButtonBusy(button, false); });
  }
  function deleteRecord(id) {
    if (!window.confirm('删除这条音频及转写结果？删除后不可恢复。')) return Promise.resolve();
    return requestJson('/api/h5/recorder/files/' + encodeURIComponent(id), { method: 'DELETE' }).then(function() {
      setMessage('已删除');
      return loadRecords();
    });
  }

  function bind(root) {
    if (root.dataset.atBound === '1') return;
    root.dataset.atBound = '1';
    el('atLocalFiles').addEventListener('change', function(event) {
      addLocalFiles(event.target.files || []);
      event.target.value = '';
    });
    root.addEventListener('click', function(event) {
      var tab = event.target.closest('[data-at-tab]');
      var create = event.target.closest('[data-at-new]');
      var upload = event.target.closest('[data-at-upload]');
      var remove = event.target.closest('[data-at-local-remove]');
      var refresh = event.target.closest('[data-at-refresh]');
      var page = event.target.closest('[data-at-page]');
      var detail = event.target.closest('[data-at-detail]');
      var rename = event.target.closest('[data-at-rename]');
      var retry = event.target.closest('[data-at-retry]');
      var localRetry = event.target.closest('[data-at-local-retry]');
      var localDelete = event.target.closest('[data-at-local-delete]');
      var removeRecord = event.target.closest('[data-at-delete]');
      var resultTab = event.target.closest('[data-at-result-tab]');
      var speaker = event.target.closest('[data-at-speaker]');
      var copy = event.target.closest('[data-at-copy]');
      var exportButton = event.target.closest('[data-at-export]');
      if (tab) return setTab(tab.dataset.atTab);
      if (create) return setTab('local');
      if (upload) return uploadLocalFile(upload.dataset.atUpload, upload);
      if (remove) {
        state.localFiles.splice(Number(remove.dataset.atLocalRemove), 1);
        return renderLocalFiles();
      }
      if (refresh) return loadRecords();
      if (page && !page.disabled) { state.page = Number(page.dataset.atPage || 1); return loadRecords(); }
      if (detail) return showDetail(detail.dataset.atDetail);
      if (rename) return renameRecord(rename.dataset.atRename, rename.dataset.atName || '').catch(function(error) { setMessage(error.message || '改名失败', true); });
      if (retry) return retryRecord(retry.dataset.atRetry, retry);
      if (localRetry) return retryLocalUpload(localRetry.dataset.atLocalRetry, localRetry);
      if (localDelete) return deleteLocalUpload(localDelete.dataset.atLocalDelete).catch(function(error) { setMessage(error.message || '删除失败', true); });
      if (removeRecord) return deleteRecord(removeRecord.dataset.atDelete).catch(function(error) { setMessage(error.message || '删除失败', true); });
      if (resultTab) return setResultTab(resultTab.dataset.atResultTab);
      if (speaker) return renameSpeaker(speaker.dataset.atSpeaker, speaker.dataset.atSpeakerId || '').catch(function(error) { setMessage(error.message || '说话人改名失败', true); });
      if (copy) {
        var text = copy.dataset.atCopy === 'transcript' ? transcriptText(state.detail) : summaryText(state.detail);
        return copyText(text).then(function(ok) { setMessage(ok ? '已复制' : '复制失败', !ok); });
      }
      if (exportButton) return exportText(exportButton.dataset.atExport || 'summary');
    });
    el('atDetailBack').addEventListener('click', showMain);
  }

  window.initAudioTranscriptionView = function initAudioTranscriptionView(root) {
    state.root = root || el('content-audio-transcription');
    if (!state.root) return Promise.resolve();
    bind(state.root);
    renderLocalFiles();
    setResultTab('summary');
    state.root.querySelector('[data-at-screen="main"]').hidden = false;
    state.root.querySelector('[data-at-screen="detail"]').hidden = true;
    setTab('records');
    return Promise.resolve();
  };
})();
