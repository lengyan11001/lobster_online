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
    detail: null,
    audioUrl: '',
    poller: null,
    loading: false
  };

  function el(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function cloudBase() {
    return String((typeof API_BASE !== 'undefined' && API_BASE) || window.__API_BASE || '').replace(/\/$/, '');
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
    return ({ processing: '正在转写和总结', completed: '已完成', failed: '处理失败' })[value] || value || '等待处理';
  }
  function speakerLabel(value) {
    var speaker = String(value || '').trim();
    if (!speaker || speaker === '未知') return '说话人';
    return /^[A-Z]$/.test(speaker) ? '说话人 ' + speaker : speaker;
  }
  function progressInfo(row) {
    if (row && row.status === 'completed') return { percent: 100, label: '转写与 AI 总结已完成' };
    if (row && row.status === 'failed') return { percent: 100, label: '处理失败' };
    if (row && row.process_stage === 'summarizing') return { percent: 85, label: '正在生成 AI 总结' };
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
        '<div class="at-row-actions"><button type="button" class="at-button" data-at-upload="' + index + '">上传并后台转写</button>' +
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
    return fetch(cloudBase() + '/api/h5/recorder/files', {
      method: 'POST',
      headers: requestHeaders(false),
      body: form
    }).then(function(response) {
      return response.json().catch(function() { return {}; }).then(function(data) {
        if (!response.ok || data.ok === false) throw new Error(parseError(data, '音频上传失败'));
        return data;
      });
    }).then(function(data) {
      var key = fileKey(file);
      state.localFiles = state.localFiles.filter(function(item) { return fileKey(item) !== key; });
      renderLocalFiles();
      setMessage('已上传，正在后台转写');
      setTab('records');
      return data;
    }).catch(function(error) {
      setMessage(error.message || '音频上传失败', true);
    }).then(function(result) {
      setButtonBusy(button, false);
      return result;
    });
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
    host.innerHTML = state.records.length ? state.records.map(recordRow).join('') : '<div class="at-empty">暂无记录</div>';
    var pager = currentPagerHost();
    if (!pager) return;
    var totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
    pager.innerHTML = '<button type="button" class="at-button at-button-quiet" data-at-page="' + (state.page - 1) + '"' + (state.page <= 1 ? ' disabled' : '') + '>上一页</button>' +
      '<span>第 ' + state.page + ' / ' + totalPages + ' 页，共 ' + state.total + ' 条</span>' +
      '<button type="button" class="at-button at-button-quiet" data-at-page="' + (state.page + 1) + '"' + (state.page >= totalPages ? ' disabled' : '') + '>下一页</button>';
  }
  function loadRecords() {
    if (state.loading || state.tab === 'local') return Promise.resolve();
    state.loading = true;
    var host = currentListHost();
    if (host) host.innerHTML = '<div class="at-empty">正在读取记录...</div>';
    var query = '?page=' + state.page + '&page_size=' + state.pageSize + (state.tab === 'mobile' ? '&source_type=device' : '');
    return requestJson('/api/h5/recorder/files' + query).then(function(data) {
      state.records = Array.isArray(data.items) ? data.items : [];
      state.total = Number(data.total || 0);
      state.page = Number(data.page || state.page);
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
    if (state.tab !== 'local') loadRecords();
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
      row.display_name || row.file_name || '音频转写',
      row.summary_text ? 'AI 摘要\n' + String(row.summary_text).trim() : '',
      points.length ? '重点事项\n' + points.map(function(item, index) { return (index + 1) + '. ' + item; }).join('\n') : ''
    ].filter(Boolean).join('\n\n');
  }
  function transcriptText(row) {
    if (!row) return '';
    var segments = Array.isArray(row.segments) ? row.segments : [];
    var body = segments.length ? segments.map(function(item) {
      return speakerLabel(item.speaker) + '：' + String(item.text || '').trim();
    }).filter(function(line) { return !/：$/.test(line); }).join('\n') : String(row.transcript_text || '').trim();
    return [row.display_name || row.file_name || '音频转写', '完整转写', body].filter(Boolean).join('\n\n');
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
      return '<div class="at-line"><button type="button" class="at-speaker" data-at-speaker="' + esc(item.speaker || '未知') + '">' + esc(speakerLabel(item.speaker)) + '</button><p>' + esc(item.text || '') + '</p></div>';
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
    var fileName = String(state.detail && (state.detail.display_name || state.detail.file_name) || '音频转写').replace(/[\\/:*?"<>|]+/g, '-').slice(0, 80);
    anchor.href = url;
    anchor.download = fileName + '-' + (kind === 'transcript' ? '完整转写' : 'AI摘要') + '.txt';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
  }
  function renameSpeaker(speaker) {
    if (!state.detail) return Promise.resolve();
    var current = String(speaker || '').trim();
    var next = window.prompt('输入说话人姓名，本条记录中所有同名说话人会一起修改', speakerLabel(current));
    if (next === null || !next.trim() || next.trim() === current || next.trim() === speakerLabel(current)) return Promise.resolve();
    return requestJson('/api/h5/recorder/files/' + encodeURIComponent(state.detail.id) + '/speakers', {
      method: 'PATCH',
      body: { speaker: current, display_name: next.trim() }
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
      var upload = event.target.closest('[data-at-upload]');
      var remove = event.target.closest('[data-at-local-remove]');
      var refresh = event.target.closest('[data-at-refresh]');
      var page = event.target.closest('[data-at-page]');
      var detail = event.target.closest('[data-at-detail]');
      var rename = event.target.closest('[data-at-rename]');
      var retry = event.target.closest('[data-at-retry]');
      var removeRecord = event.target.closest('[data-at-delete]');
      var resultTab = event.target.closest('[data-at-result-tab]');
      var speaker = event.target.closest('[data-at-speaker]');
      var copy = event.target.closest('[data-at-copy]');
      var exportButton = event.target.closest('[data-at-export]');
      if (tab) return setTab(tab.dataset.atTab);
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
      if (removeRecord) return deleteRecord(removeRecord.dataset.atDelete).catch(function(error) { setMessage(error.message || '删除失败', true); });
      if (resultTab) return setResultTab(resultTab.dataset.atResultTab);
      if (speaker) return renameSpeaker(speaker.dataset.atSpeaker).catch(function(error) { setMessage(error.message || '说话人改名失败', true); });
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
    setTab(state.tab || 'records');
    return Promise.resolve();
  };
})();
