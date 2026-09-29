(function hypitVideoStudioModule() {
  'use strict';

  var state = {
    initialized: false,
    file: null,
    previewUrl: '',
    jobId: '',
    busy: false,
    pollTimer: null,
    outputUrl: ''
  };

  function $(id) { return document.getElementById(id); }
  function localBase() {
    return String(typeof LOCAL_API_BASE !== 'undefined' ? (LOCAL_API_BASE || '') : '').replace(/\/$/, '');
  }
  function headers() {
    return typeof authHeaders === 'function' ? Object.assign({}, authHeaders() || {}) : {};
  }
  function errorText(value) {
    if (value == null) return '';
    if (typeof value === 'string') return value.trim();
    if (Array.isArray(value)) return value.map(errorText).filter(Boolean).join('；');
    if (typeof value === 'object') {
      var message = value.msg || value.message || value.detail || value.error;
      if (message != null) return errorText(message);
      try { return JSON.stringify(value); } catch (error) { return '未知错误'; }
    }
    return String(value);
  }
  function formatBytes(value) {
    var size = Number(value || 0);
    if (size < 1024 * 1024) return (size / 1024).toFixed(0) + ' KB';
    return (size / 1024 / 1024).toFixed(1) + ' MB';
  }
  function showMessage(text, error) {
    var box = $('hypitMessage');
    if (!box) return;
    box.textContent = errorText(text);
    box.hidden = !text;
    box.dataset.error = error ? '1' : '0';
  }
  function setBusy(busy, text) {
    state.busy = !!busy;
    var button = $('hypitSubmitButton');
    if (button) button.disabled = state.busy || !state.file;
    $('hypitProgress').hidden = !state.busy;
    if (text) $('hypitProgressText').textContent = text;
  }
  function setStatus(text, error) {
    $('hypitTaskStatus').textContent = text || '';
    $('hypitTaskStatus').dataset.error = error ? '1' : '0';
  }
  function showFacts(probe, fileSize) {
    var width = Number(probe.width || 0);
    var height = Number(probe.height || 0);
    var ratio = width && height ? (width > height ? '横屏' : (width < height ? '竖屏' : '方形')) : '画幅未知';
    $('hypitVideoFacts').innerHTML = [
      Number(probe.duration || 0).toFixed(1) + ' 秒',
      width && height ? width + ' × ' + height : '尺寸未知',
      ratio,
      probe.hasAudio ? '含音频' : '无音轨',
      formatBytes(fileSize)
    ].map(function(item) {
      var span = document.createElement('span');
      span.textContent = item;
      return span.outerHTML;
    }).join('');
  }
  function request(path, options) {
    options = options || {};
    options.headers = Object.assign({}, headers(), options.headers || {});
    return fetch(localBase() + path, options).then(function(response) {
      var contentType = response.headers.get('content-type') || '';
      if (contentType.indexOf('application/json') < 0) {
        throw new Error('本机服务返回异常（HTTP ' + response.status + '），请确认客户端本机后端已更新并重启。');
      }
      return response.json().then(function(data) {
        if (!response.ok || data.ok === false) {
          throw new Error(errorText(data.detail || data.message || data.error) || ('请求失败（HTTP ' + response.status + '）'));
        }
        return data;
      });
    });
  }
  function selectFile(file) {
    if (!file) return;
    if (!/\.(mp4|mov|webm|mkv)$/i.test(file.name || '')) {
      showMessage('请选择 MP4、MOV、WebM 或 MKV 视频。', true);
      return;
    }
    if (file.size > 1024 * 1024 * 1024) {
      showMessage('视频文件不能超过 1GB。', true);
      return;
    }
    if (state.pollTimer) clearTimeout(state.pollTimer);
    if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
    state.file = file;
    state.jobId = '';
    try { localStorage.removeItem('hypit-local-last-workflow'); } catch (error) {}
    state.previewUrl = URL.createObjectURL(file);
    $('hypitVideoPreview').src = state.previewUrl;
    $('hypitFilename').textContent = file.name;
    $('hypitFileMeta').textContent = formatBytes(file.size) + ' · 本机文件';
    $('hypitSelectedFile').hidden = false;
    $('hypitChooseVideo').hidden = true;
    $('hypitResultEmpty').hidden = true;
    $('hypitOutputSection').hidden = true;
    $('hypitVideoFacts').innerHTML = '';
    $('hypitChooseVideoTitle').textContent = file.name;
    setStatus('视频已选择，提交后自动完成全部处理。');
    showMessage('');
    setBusy(false);
  }
  function submitWorkflow() {
    if (!state.file || state.busy) return;
    var form = new FormData();
    form.append('file', state.file, state.file.name);
    setBusy(true, '正在上传视频并提交自动复刻任务…');
    setStatus('正在上传并创建任务…');
    showMessage('');
    var xhr = new XMLHttpRequest();
    xhr.open('POST', localBase() + '/api/local/hypit/workflows', true);
    xhr.timeout = 30 * 60 * 1000;
    Object.keys(headers()).forEach(function(name) {
      if (String(name).toLowerCase() !== 'content-type') xhr.setRequestHeader(name, headers()[name]);
    });
    xhr.upload.onprogress = function(event) {
      if (!event.lengthComputable) return;
      var percent = Math.max(1, Math.min(99, Math.round(event.loaded * 100 / event.total)));
      $('hypitProgressBar').style.width = percent + '%';
      $('hypitProgressText').textContent = '上传视频 ' + percent + '%';
    };
    xhr.onload = function() {
      var data = {};
      try { data = JSON.parse(xhr.responseText || '{}'); } catch (error) {}
      if (xhr.status < 200 || xhr.status >= 300 || data.ok === false) {
        setBusy(false);
        setStatus('任务提交失败', true);
        showMessage(errorText(data.detail) || ('任务提交失败（HTTP ' + xhr.status + '）'), true);
        return;
      }
      state.jobId = data.job_id;
      try { localStorage.setItem('hypit-local-last-workflow', state.jobId); } catch (error) {}
      showFacts((data.job && data.job.probe) || {}, (data.job && data.job.file_size) || state.file.size);
      setBusy(false);
      setStatus((data.workflow && data.workflow.stage) || '任务已提交，正在自动处理。');
      showMessage('任务已提交。后续分析、素材生成和本机合成都将自动完成。');
      pollWorkflow();
    };
    xhr.onerror = function() {
      setBusy(false);
      setStatus('无法连接本机服务', true);
      showMessage('连接本机服务失败，请确认客户端本机后端仍在运行。', true);
    };
    xhr.ontimeout = function() {
      setBusy(false);
      setStatus('视频上传超时', true);
      showMessage('视频上传超时；可重新提交。', true);
    };
    xhr.send(form);
  }
  function showOutput(path) {
    if (state.outputUrl) URL.revokeObjectURL(state.outputUrl);
    return fetch(localBase() + path, { headers: headers(), cache: 'no-store' }).then(function(response) {
      if (!response.ok) throw new Error('最终视频暂时无法读取（HTTP ' + response.status + '）');
      return response.blob();
    }).then(function(blob) {
      state.outputUrl = URL.createObjectURL(blob);
      $('hypitOutputVideo').src = state.outputUrl;
      $('hypitOutputSection').hidden = false;
    });
  }
  function pollWorkflow() {
    if (!state.jobId) return;
    if (state.pollTimer) clearTimeout(state.pollTimer);
    request('/api/local/hypit/jobs/' + encodeURIComponent(state.jobId) + '/workflow', { cache: 'no-store' })
      .then(function(data) {
        var workflow = data.workflow || {};
        setStatus(workflow.stage || '任务处理中…', workflow.status === 'failed');
        if (workflow.status === 'completed') {
          showMessage('视频复刻完成，可以直接播放最终成片。');
          return showOutput(workflow.video_url).catch(function(error) {
            showMessage(errorText(error.message), true);
          });
        }
        if (workflow.status === 'failed' || workflow.status === 'paused') {
          showMessage(workflow.error || workflow.stage || '任务未完成，请重新提交。', true);
          return;
        }
        state.pollTimer = setTimeout(pollWorkflow, 5000);
      })
      .catch(function(error) {
        setStatus('正在重试读取任务状态…');
        showMessage(errorText(error.message), true);
        state.pollTimer = setTimeout(pollWorkflow, 15000);
      });
  }
  function restoreWorkflow() {
    var jobId = '';
    try { jobId = localStorage.getItem('hypit-local-last-workflow') || ''; } catch (error) {}
    if (!/^[a-f0-9]{32}$/.test(jobId)) return;
    state.jobId = jobId;
    $('hypitChooseVideo').hidden = true;
    $('hypitSelectedFile').hidden = false;
    $('hypitVideoPreview').hidden = true;
    $('hypitFilename').textContent = '已提交任务 ' + jobId.slice(0, 8);
    $('hypitFileMeta').textContent = '本机任务记录';
    $('hypitResultEmpty').hidden = true;
    request('/api/local/hypit/jobs/' + encodeURIComponent(jobId), { cache: 'no-store' })
      .then(function(data) {
        showFacts((data.job && data.job.probe) || {}, (data.job && data.job.file_size) || 0);
        pollWorkflow();
      })
      .catch(function() {
        try { localStorage.removeItem('hypit-local-last-workflow'); } catch (error) {}
        state.jobId = '';
        $('hypitChooseVideo').hidden = false;
        $('hypitSelectedFile').hidden = true;
        $('hypitResultEmpty').hidden = false;
      });
  }
  function bind() {
    if (state.initialized) return;
    state.initialized = true;
    $('hypitChooseVideo').addEventListener('click', function() { $('hypitVideoInput').click(); });
    $('hypitReplaceVideo').addEventListener('click', function() { $('hypitVideoInput').click(); });
    $('hypitVideoInput').addEventListener('change', function(event) {
      selectFile(event.target.files && event.target.files[0]);
      event.target.value = '';
    });
    $('hypitSubmitButton').addEventListener('click', submitWorkflow);
    restoreWorkflow();
  }
  window.initHypitVideoStudioView = function() {
    bind();
    return Promise.resolve();
  };
})();
