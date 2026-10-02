(function hypitVideoStudioModule() {
  'use strict';

  var state = {
    initialized: false,
    ready: null,
    dependencies: [],
    installState: {},
    installTimer: null,
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
  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }
  function escapeAttr(value) { return escapeHtml(value); }
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
      loadHistory();
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
          loadHistory();
          return showOutput(workflow.video_url).catch(function(error) {
            showMessage(errorText(error.message), true);
          });
        }
        if (workflow.status === 'failed' || workflow.status === 'paused') {
          showMessage(workflow.error || workflow.stage || '任务未完成，可从下方历史记录点“继续”。', true);
          loadHistory();
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
  function runtimeBase() { return localBase() + '/api/local/hypit'; }
  function renderDependencies(list) {
    var host = $('hypitRuntimeList');
    if (!host) return;
    host.innerHTML = (list || []).map(function(item) {
      var ok = !!item.ok;
      return '<div class="hypit-runtime-item" data-ok="' + (ok ? '1' : '0') + '">'
        + '<span class="hypit-runtime-dot" aria-hidden="true">' + (ok ? '✓' : '✕') + '</span>'
        + '<span class="hypit-runtime-label">' + errorText(item.label) + '</span>'
        + '<span class="hypit-runtime-detail">' + errorText(item.detail) + '</span>'
        + '</div>';
    }).join('');
  }
  function refreshRuntimeStatus() {
    return request('/api/local/hypit/runtime/status', { cache: 'no-store' }).then(function(data) {
      state.ready = !!data.ready;
      state.dependencies = data.dependencies || [];
      state.installState = data.install || {};
      renderDependencies(state.dependencies);
      var button = $('hypitInstallButton');
      if (button) button.hidden = state.ready;
      var status = $('hypitRuntimeStatus');
      if (status) {
        if (state.ready) {
          status.textContent = '已就绪，可以提交视频复刻。';
          status.dataset.error = '0';
        } else {
          var missing = state.dependencies.filter(function(item) { return !item.ok; })
            .map(function(item) { return errorText(item.label); }).join('、');
          status.textContent = missing ? ('缺少：' + missing + '（点右上「安装运行依赖」）') : '未就绪，请安装运行依赖。';
          status.dataset.error = '1';
        }
      }
      updateSubmitState();
      return data;
    }).catch(function(error) {
      var status = $('hypitRuntimeStatus');
      if (status) { status.textContent = '依赖检查失败：' + errorText(error.message); status.dataset.error = '1'; }
      throw error;
    });
  }
  function updateSubmitState() {
    var button = $('hypitSubmitButton');
    if (button) button.disabled = state.busy || !state.file || state.ready === false;
  }
  function renderInstall(stateData) {
    var install = stateData || {};
    var percent = Math.max(0, Math.min(100, Number(install.percent || 0)));
    if ($('hypitInstallBar')) $('hypitInstallBar').style.width = percent + '%';
    if ($('hypitInstallStage')) $('hypitInstallStage').textContent = errorText(install.stage) || '安装中…';
    if ($('hypitInstallLog')) {
      var lines = Array.isArray(install.log) ? install.log : [];
      var log = $('hypitInstallLog');
      log.textContent = lines.join('\n');
      log.scrollTop = log.scrollHeight;
    }
    var error = errorText(install.error);
    var box = $('hypitInstallError');
    if (box) { box.hidden = !error; box.textContent = error; }
    var running = !!install.running;
    if ($('hypitInstallStart')) $('hypitInstallStart').hidden = running || install.status === 'completed';
    if ($('hypitInstallRetry')) $('hypitInstallRetry').hidden = !(install.status === 'failed');
  }
  function openInstallModal() {
    var modal = $('hypitInstallModal');
    if (modal) modal.hidden = false;
    renderInstall(state.installState || {});
  }
  function closeInstallModal() {
    var modal = $('hypitInstallModal');
    if (modal) modal.hidden = true;
  }
  function pollInstall() {
    if (state.installTimer) clearTimeout(state.installTimer);
    request('/api/local/hypit/runtime/status', { cache: 'no-store' }).then(function(data) {
      state.installState = data.install || {};
      renderInstall(state.installState);
      state.ready = !!data.ready;
      renderDependencies(data.dependencies || []);
      updateSubmitState();
      var button = $('hypitInstallButton');
      if (button) button.hidden = state.ready;
      if (state.ready) {
        closeInstallModal();
        showMessage('运行依赖已就绪，可以提交视频复刻了。');
        var status = $('hypitRuntimeStatus');
        if (status) { status.textContent = '已就绪，可以提交视频复刻。'; status.dataset.error = '0'; }
        return;
      }
      if ((state.installState || {}).running) {
        state.installTimer = setTimeout(pollInstall, 1500);
      }
    }).catch(function(error) {
      renderInstall({ status: 'failed', stage: '读取安装进度失败', error: errorText(error.message) });
    });
  }
  function startInstall() {
    openInstallModal();
    if ($('hypitInstallStart')) $('hypitInstallStart').disabled = true;
    request('/api/local/hypit/runtime/install', { method: 'POST', json: {} })
      .then(function(data) {
        state.installState = data.install || {};
        renderInstall(state.installState);
        if ($('hypitInstallStart')) $('hypitInstallStart').disabled = false;
        pollInstall();
      })
      .catch(function(error) {
        if ($('hypitInstallStart')) $('hypitInstallStart').disabled = false;
        renderInstall({ status: 'failed', stage: '安装启动失败', error: errorText(error.message) });
      });
  }

  function historyTimeText(value) {
    var stamp = Number(value || 0) * 1000;
    if (!stamp) return '';
    var date = new Date(stamp);
    function pad(n) { return n < 10 ? '0' + n : '' + n; }
    return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate()) + ' ' + pad(date.getHours()) + ':' + pad(date.getMinutes());
  }
  function historyStatusText(item) {
    var status = String((item && item.status) || '');
    if (status === 'completed') return '已完成';
    if (status === 'failed') return '失败';
    if (status === 'running') return '进行中';
    if (status === 'queued') return '排队中';
    if (status === 'paused') return '已中断';
    return status || '未知';
  }
  function renderHistory(items) {
    var host = $('hypitHistoryList');
    if (!host) return;
    if (!items || !items.length) {
      host.innerHTML = '<span class="hypit-history-empty">还没有提交记录。</span>';
      return;
    }
    host.innerHTML = items.map(function (item) {
      var size = item.file_size ? formatBytes(item.file_size) : '';
      var duration = item.duration ? (Math.round(item.duration * 10) / 10) + ' 秒' : '';
      var meta = [historyTimeText(item.created_at), duration, size].filter(Boolean).join(' · ');
      var stage = item.stage || item.error || '';
      var videoUrl = String(item.video_url || item.video_local_url || '');
      return '<div class="hypit-history-item" data-status="' + escapeAttr(item.status) + '">'
        + '<div class="hypit-history-main">'
        + '<strong>' + escapeHtml(item.filename || item.job_id) + '</strong>'
        + '<span class="hypit-history-badge">' + escapeHtml(historyStatusText(item)) + '</span>'
        + '<span class="hypit-history-meta">' + escapeHtml(meta) + '</span>'
        + (stage ? '<em class="hypit-history-stage">' + escapeHtml(String(stage).slice(0, 160)) + '</em>' : '')
        + '</div>'
        + '<div class="hypit-history-actions">'
        + (videoUrl ? '<button type="button" class="hypit-text-button" data-hypit-history-open="' + escapeAttr(item.job_id) + '">看成片</button>' : '')
        + (item.can_resume ? '<button type="button" class="hypit-primary-button hypit-compact-button" data-hypit-history-resume="' + escapeAttr(item.job_id) + '">继续</button>' : '')
        + '</div>'
        + '</div>';
    }).join('');
  }
  function loadHistory() {
    return request('/api/local/hypit/jobs?limit=20', { cache: 'no-store' })
      .then(function (data) { renderHistory(data.jobs || []); })
      .catch(function () {
        var host = $('hypitHistoryList');
        if (host) host.innerHTML = '<span class="hypit-history-empty">历史记录读取失败（本机服务是否在运行）。</span>';
      });
  }
  function resumeJob(jobId) {
    if (!jobId || state.busy) return;
    setBusy(true, '正在从历史记录继续…');
    return fetch(localBase() + '/api/local/hypit/jobs/' + encodeURIComponent(jobId) + '/resume', {
      method: 'POST', headers: headers(), cache: 'no-store'
    }).then(function (response) {
      return response.json().then(function (data) { return { ok: response.ok, data: data }; });
    }).then(function (result) {
      if (!result.ok || result.data.ok === false) throw new Error(errorText(result.data.detail) || '继续失败');
      state.jobId = jobId;
      try { localStorage.setItem('hypit-local-last-workflow', jobId); } catch (error) {}
      $('hypitChooseVideo').hidden = true;
      $('hypitSelectedFile').hidden = false;
      $('hypitResultEmpty').hidden = true;
      setStatus((result.data.workflow && result.data.workflow.stage) || '已继续，正在自动处理…');
      showMessage('已从历史记录继续：原参考视频、关键帧、口播和已生成的素材都会复用。');
      pollWorkflow();
      loadHistory();
    }).catch(function (error) {
      showMessage(errorText(error.message), true);
    }).then(function () { setBusy(false); });
  }
  function bindHistoryPanel() {
    var refresh = $('hypitHistoryRefresh');
    if (refresh) refresh.addEventListener('click', function () { loadHistory(); });
    var host = $('hypitHistoryList');
    if (!host) return;
    host.addEventListener('click', function (event) {
      var target = event.target;
      if (!target || !target.getAttribute) return;
      var resumeId = target.getAttribute('data-hypit-history-resume');
      if (resumeId) { resumeJob(resumeId); return; }
      var openId = target.getAttribute('data-hypit-history-open');
      if (openId) {
        state.jobId = openId;
        pollWorkflow();
      }
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
    var installButton = $('hypitInstallButton');
    if (installButton) installButton.addEventListener('click', openInstallModal);
    var installStart = $('hypitInstallStart');
    if (installStart) installStart.addEventListener('click', startInstall);
    var installRetry = $('hypitInstallRetry');
    if (installRetry) installRetry.addEventListener('click', startInstall);
    var installClose = $('hypitInstallClose');
    if (installClose) installClose.addEventListener('click', closeInstallModal);
    var installModal = $('hypitInstallModal');
    if (installModal) installModal.addEventListener('click', function(event) { if (event.target === installModal) closeInstallModal(); });
    bindHistoryPanel();
    refreshRuntimeStatus().catch(function() {});
    loadHistory();
    restoreWorkflow();
  }
  window.initHypitVideoStudioView = function() {
    bind();
    return Promise.resolve();
  };
})();
