(function hypitVideoStudioModule() {
  'use strict';

  var state = {
    initialized: false,
    ready: null,
    dependencies: [],
    jobs: [],
    page: 1,
    pageSize: 8,
    pages: 1,
    total: 0,
    file: null,
    previewUrl: '',
    brief: '',
    jobId: '',
    busy: false,
    pollTimer: null,
    installTimer: null,
    refreshTimer: null,
    detailJobId: ''
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
  function isAbsoluteUrl(value) { return /^https?:\/\//i.test(String(value || '')); }
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
    if (!size) return '';
    if (size < 1024) return size + ' B';
    if (size < 1024 * 1024) return (size / 1024).toFixed(1) + ' KB';
    if (size < 1024 * 1024 * 1024) return (size / 1024 / 1024).toFixed(1) + ' MB';
    return (size / 1024 / 1024 / 1024).toFixed(2) + ' GB';
  }
  function formatTime(value) {
    var stamp = Number(value || 0) * 1000;
    if (!stamp) return '';
    var date = new Date(stamp);
    function pad(n) { return n < 10 ? '0' + n : '' + n; }
    return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate()) + ' ' +
      pad(date.getHours()) + ':' + pad(date.getMinutes());
  }
  function assetUrl(path) {
    var value = String(path || '');
    if (!value) return '';
    return isAbsoluteUrl(value) ? value : localBase() + value;
  }
  function showMessage(text, isError) {
    var box = $('hypitMessage');
    if (!box) return;
    if (!text) { box.hidden = true; box.textContent = ''; return; }
    box.hidden = false;
    box.textContent = text;
    box.setAttribute('data-error', isError ? '1' : '0');
  }
  function setBusy(busy) { state.busy = !!busy; }

  function request(path, options) {
    var init = Object.assign({ headers: headers(), cache: 'no-store' }, options || {});
    init.headers = Object.assign({}, headers(), (options && options.headers) || {});
    return fetch(localBase() + path, init).then(function (response) {
      return response.text().then(function (text) {
        var data = {};
        try { data = text ? JSON.parse(text) : {}; } catch (error) { data = { detail: text }; }
        if (!response.ok || data.ok === false) throw new Error(errorText(data.detail) || ('请求失败（HTTP ' + response.status + '）'));
        return data;
      });
    });
  }

  /* ---------------- 运行依赖（弹窗内） ---------------- */
  function renderDependencies(items) {
    var host = $('hypitRuntimeList');
    if (!host) return;
    host.innerHTML = (items || []).map(function (item) {
      var ok = !!item.ok;
      return '<div class="hypit-runtime-item" data-ok="' + (ok ? '1' : '0') + '">'
        + '<span class="hypit-runtime-dot" aria-hidden="true">' + (ok ? '✓' : '!') + '</span>'
        + '<div><strong>' + escapeHtml(item.label || item.key) + '</strong>'
        + '<span>' + escapeHtml(item.detail || '') + '</span></div></div>';
    }).join('') || '<div class="hypit-runtime-item"><span class="hypit-runtime-dot">?</span><div><strong>未知</strong></div></div>';
  }
  function renderDependencyChip() {
    var chip = $('hypitDepChip');
    if (!chip) return;
    var missing = (state.dependencies || []).filter(function (item) { return !item.ok; });
    if (state.ready === true) {
      chip.textContent = '运行依赖已就绪';
      chip.setAttribute('data-ok', '1');
    } else if (state.dependencies && state.dependencies.length) {
      chip.textContent = '缺少 ' + missing.length + ' 项运行依赖，点这里安装';
      chip.setAttribute('data-ok', '0');
    } else {
      chip.textContent = '运行依赖检查中…';
      chip.setAttribute('data-ok', '');
    }
  }
  function refreshRuntimeStatus() {
    return request('/api/local/hypit/runtime/status').then(function (data) {
      state.ready = !!data.ready;
      state.dependencies = data.dependencies || [];
      renderDependencies(state.dependencies);
      renderDependencyChip();
      var install = data.install || {};
      if (install.status === 'running') {
        renderInstall(install);
        pollInstall();
      } else if (install.status) {
        renderInstall(install);
      }
      var status = $('hypitRuntimeStatus');
      if (status) status.textContent = state.ready ? '本机运行依赖已就绪。' : '还有依赖没装好，点下面按钮安装 / 修复。';
      return data;
    });
  }
  function renderInstall(install) {
    var progress = $('hypitInstallProgress');
    var bar = $('hypitInstallBar');
    var stage = $('hypitInstallStage');
    var log = $('hypitInstallLog');
    var errorBox = $('hypitInstallError');
    var retry = $('hypitInstallRetry');
    var start = $('hypitInstallStart');
    if (!install) return;
    var ready = state.ready === true;
    // 依赖都就绪了：只显示状态，不再挂上一次安装的红色错误和旧日志
    if (progress) progress.hidden = ready;
    if (bar) bar.style.width = Math.max(0, Math.min(100, Number(install.percent || 0))) + '%';
    if (stage) stage.textContent = ready ? '依赖已就绪，无需安装。' : (install.stage || install.status || '');
    if (log) {
      log.hidden = ready;
      log.textContent = ready ? '' : (install.log || []).slice(-80).join('\n');
    }
    if (errorBox) {
      var error = String(install.error || '').trim();
      var showError = !!error && !ready;
      errorBox.hidden = !showError;
      errorBox.textContent = showError ? error : '';
    }
    if (retry) retry.hidden = ready || String(install.status || '') !== 'failed';
    if (start) start.disabled = String(install.status || '') === 'running';
  }
  function openInstallModal() {
    var modal = $('hypitInstallModal');
    if (modal) modal.hidden = false;
    refreshRuntimeStatus().catch(function (error) { showMessage(errorText(error.message), true); });
  }
  function closeInstallModal() {
    var modal = $('hypitInstallModal');
    if (modal) modal.hidden = true;
  }
  function pollInstall() {
    if (state.installTimer) clearTimeout(state.installTimer);
    state.installTimer = setTimeout(function () { refreshRuntimeStatus().catch(function () {}); }, 3000);
  }
  function startInstall() {
    var start = $('hypitInstallStart');
    if (start) { start.disabled = true; start.textContent = '安装中…'; }
    request('/api/local/hypit/runtime/install', { method: 'POST' }).then(function (data) {
      renderInstall(data.install || { status: 'running', stage: '正在启动安装', percent: 1 });
      pollInstall();
    }).catch(function (error) {
      showMessage(errorText(error.message), true);
    }).then(function () {
      if (start) { start.disabled = false; start.textContent = '安装 / 修复运行依赖'; }
    });
  }

  /* ---------------- 列表 ---------------- */
  function statusText(item) {
    var status = String((item && item.status) || '');
    if (status === 'completed') return '已完成';
    if (status === 'failed') return '失败';
    if (status === 'running') return '进行中';
    if (status === 'queued') return '排队中';
    if (status === 'paused') return '已中断';
    return status || '未知';
  }
  function previewHtml(item) {
    var reference = item.has_reference ? assetUrl('/api/local/hypit/jobs/' + encodeURIComponent(item.job_id) + '/asset/reference.mp4') : '';
    var sheet = assetUrl(item.contact_sheet_url || '');
    var original = reference
      ? '<video class="hypit-preview-video" src="' + escapeAttr(reference) + '" muted playsinline preload="metadata"></video>'
      : (sheet ? '<img class="hypit-preview-img" src="' + escapeAttr(sheet) + '" alt="">' : '<span class="hypit-preview-empty">原素材已删除</span>');
    var output = '';
    if (item.status === 'completed' && (item.video_url || item.video_local_url)) {
      output = '<video class="hypit-preview-video" src="' + escapeAttr(assetUrl(item.video_local_url || item.video_url)) + '" controls playsinline preload="metadata"></video>';
    } else if (item.video_url) {
      output = '<video class="hypit-preview-video" src="' + escapeAttr(assetUrl(item.video_local_url || item.video_url)) + '" controls playsinline preload="metadata"></video>';
    } else {
      output = '<span class="hypit-preview-empty">' + escapeHtml(item.stage || '尚未生成成片') + '</span>';
    }
    return '<div class="hypit-preview">'
      + '<div class="hypit-preview-box"><span class="hypit-preview-label">原素材</span>' + original + '</div>'
      + '<div class="hypit-preview-box"><span class="hypit-preview-label">生成素材</span>' + output + '</div>'
      + '</div>';
  }
  function renderCard(item) {
    var meta = [formatTime(item.created_at), item.duration ? (Math.round(item.duration * 10) / 10) + ' 秒' : '',
      formatBytes(item.file_size)].filter(Boolean).join(' · ');
    var stage = String(item.error || item.stage || '');
    return '<article class="hypit-record" data-status="' + escapeAttr(item.status) + '" data-job="' + escapeAttr(item.job_id) + '">'
      + previewHtml(item)
      + '<div class="hypit-record-main">'
      + '<div class="hypit-record-head"><strong>' + escapeHtml(item.filename || item.job_id) + '</strong>'
      + '<span class="hypit-history-badge">' + escapeHtml(statusText(item)) + '</span></div>'
      + '<span class="hypit-record-meta">' + escapeHtml(meta) + '</span>'
      + (stage ? '<em class="hypit-record-stage">' + escapeHtml(stage.slice(0, 160)) + '</em>' : '')
      + '</div>'
      + '<div class="hypit-record-actions">'
      + '<button type="button" class="hypit-text-button" data-hypit-detail="' + escapeAttr(item.job_id) + '">详情</button>'
      + (item.can_resume ? '<button type="button" class="hypit-primary-button hypit-compact-button" data-hypit-resume="' + escapeAttr(item.job_id) + '">继续</button>' : '')
      + '<button type="button" class="hypit-text-button" data-hypit-delete="' + escapeAttr(item.job_id) + '">删除</button>'
      + '</div></article>';
  }
  function renderPager() {
    var host = $('hypitPager');
    if (!host) return;
    if (state.pages <= 1) { host.innerHTML = state.total ? ('共 ' + state.total + ' 条记录') : ''; return; }
    var buttons = [];
    buttons.push('<button type="button" class="hypit-text-button" data-hypit-page="' + (state.page - 1) + '"' + (state.page <= 1 ? ' disabled' : '') + '>上一页</button>');
    buttons.push('<span>第 ' + state.page + ' / ' + state.pages + ' 页 · 共 ' + state.total + ' 条</span>');
    buttons.push('<button type="button" class="hypit-text-button" data-hypit-page="' + (state.page + 1) + '"' + (state.page >= state.pages ? ' disabled' : '') + '>下一页</button>');
    host.innerHTML = buttons.join('');
  }
  function loadList(page) {
    if (page) state.page = Math.max(1, Number(page));
    var host = $('hypitList');
    return request('/api/local/hypit/jobs?page=' + state.page + '&page_size=' + state.pageSize).then(function (data) {
      state.jobs = data.jobs || [];
      state.total = Number(data.total || 0);
      state.pages = Number(data.pages || 1);
      if (host) {
        host.innerHTML = state.jobs.length ? state.jobs.map(renderCard).join('')
          : '<div class="hypit-empty"><span aria-hidden="true">▧</span><strong>还没有复刻记录</strong><p>点右上角「添加复刻」上传参考视频。</p></div>';
      }
      renderPager();
      return state.jobs;
    }).catch(function (error) {
      if (host) host.innerHTML = '<div class="hypit-empty"><strong>记录读取失败</strong><p>' + escapeHtml(errorText(error.message)) + '</p></div>';
      return [];
    });
  }

  /* ---------------- 详情 ---------------- */
  function sceneHtml(scene) {
    var parts = [];
    if (scene.frame) parts.push('<figure><img src="' + escapeAttr(assetUrl(scene.frame)) + '" alt=""><figcaption>关键帧</figcaption></figure>');
    if (scene.image) parts.push('<figure><img src="' + escapeAttr(assetUrl(scene.image)) + '" alt=""><figcaption>生成图片</figcaption></figure>');
    if (scene.take) parts.push('<figure><video src="' + escapeAttr(assetUrl(scene.take)) + '" controls playsinline preload="metadata"></video><figcaption>生成视频段</figcaption></figure>');
    if (!parts.length) parts.push('<span class="hypit-preview-empty">这一段的素材还没生成</span>');
    return '<div class="hypit-scene"><div class="hypit-scene-head">分镜 ' + scene.index + (scene.stage ? ' · ' + escapeHtml(scene.stage) : '') + '</div>'
      + '<div class="hypit-scene-assets">' + parts.join('') + '</div></div>';
  }
  function openDetail(jobId) {
    var modal = $('hypitDetailModal');
    var body = $('hypitDetailBody');
    var foot = $('hypitDetailFoot');
    if (!modal || !body) return;
    state.detailJobId = jobId;
    modal.hidden = false;
    body.innerHTML = '<div class="hypit-empty"><strong>正在读取详情…</strong></div>';
    if (foot) foot.innerHTML = '';
    request('/api/local/hypit/jobs/' + encodeURIComponent(jobId) + '/detail').then(function (data) {
      var job = data.job || {};
      var workflow = data.workflow || {};
      var assets = data.assets || {};
      var final = data.final || {};
      var probe = job.probe || {};
      var title = $('hypitDetailTitle');
      if (title) title.textContent = String(job.filename || jobId);
      var blocks = [];
      blocks.push('<div class="hypit-detail-meta">'
        + '<span>状态：' + escapeHtml(statusText(workflow)) + '</span>'
        + '<span>时间：' + escapeHtml(formatTime(job.created_at)) + '</span>'
        + '<span>时长：' + escapeHtml(String(probe.duration || '')) + ' 秒</span>'
        + '<span>画面：' + escapeHtml((probe.width || '') + '×' + (probe.height || '')) + '</span>'
        + '<span>大小：' + escapeHtml(formatBytes(job.file_size)) + '</span>'
        + '</div>');
      if (workflow.error) blocks.push('<div class="hypit-install-error">' + escapeHtml(String(workflow.error).slice(0, 400)) + '</div>');
      else if (workflow.stage) blocks.push('<div class="hypit-detail-stage">' + escapeHtml(String(workflow.stage)) + '</div>');
      if (assets.reference) {
        blocks.push('<h4>原素材</h4><video class="hypit-detail-video" src="' + escapeAttr(assetUrl(assets.reference)) + '" controls playsinline preload="metadata"></video>');
      }
      if (assets.contact_sheet) {
        blocks.push('<h4>关键帧联系表</h4><img class="hypit-detail-sheet" src="' + escapeAttr(assetUrl(assets.contact_sheet)) + '" alt="">');
      }
      if (assets.speech) blocks.push('<h4>口播转写</h4><p class="hypit-detail-speech">' + escapeHtml(String(assets.speech).slice(0, 600)) + '</p>');
      blocks.push('<h4>过程素材</h4>');
      blocks.push((assets.scenes || []).length
        ? (assets.scenes || []).map(sceneHtml).join('')
        : '<div class="hypit-empty"><strong>还没有过程素材</strong></div>');
      if (final.video_url || final.video_local_url) {
        blocks.push('<h4>最终成片</h4><video class="hypit-detail-video" src="' +
          escapeAttr(assetUrl(final.video_local_url || final.video_url)) + '" controls playsinline preload="metadata"></video>');
        if (String(final.video_url || '').indexOf('https://') === 0) {
          blocks.push('<p class="hypit-detail-link"><a href="' + escapeAttr(final.video_url) + '" target="_blank" rel="noopener">打开线上成片地址</a></p>');
        }
      }
      body.innerHTML = blocks.join('');
      if (foot) {
        foot.innerHTML = '<button type="button" class="hypit-primary-button hypit-compact-button" data-hypit-resume="' + escapeAttr(jobId) + '">继续 / 重跑</button>'
          + '<button type="button" class="hypit-text-button" data-hypit-delete="' + escapeAttr(jobId) + '">删除记录</button>'
          + '<button type="button" class="hypit-text-button" data-hypit-detail-close="1">关闭</button>';
      }
    }).catch(function (error) {
      body.innerHTML = '<div class="hypit-empty"><strong>详情读取失败</strong><p>' + escapeHtml(errorText(error.message)) + '</p></div>';
    });
  }
  function closeDetail() {
    var modal = $('hypitDetailModal');
    if (modal) modal.hidden = true;
    state.detailJobId = '';
  }

  /* ---------------- 添加复刻 ---------------- */
  function openAddModal() {
    var modal = $('hypitAddModal');
    if (modal) modal.hidden = false;
    if (state.ready === false) openInstallModal();
  }
  function closeAddModal() {
    var modal = $('hypitAddModal');
    if (modal) modal.hidden = true;
  }
  function selectFile(file) {
    if (!file) return;
    if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
    state.file = file;
    state.previewUrl = URL.createObjectURL(file);
    var preview = $('hypitVideoPreview');
    if (preview) preview.src = state.previewUrl;
    var selected = $('hypitSelectedFile');
    if (selected) selected.hidden = false;
    var choose = $('hypitChooseVideo');
    if (choose) choose.hidden = true;
    var name = $('hypitFilename');
    if (name) name.textContent = file.name;
    var meta = $('hypitFileMeta');
    if (meta) meta.textContent = formatBytes(file.size);
    var submit = $('hypitSubmitButton');
    if (submit) submit.disabled = false;
  }
  function submitWorkflow() {
    if (!state.file || state.busy) return;
    var form = new FormData();
    form.append('file', state.file, state.file.name);
    var brief = $('hypitBrief');
    form.append('brief', brief ? String(brief.value || '') : '');
    setBusy(true);
    showMessage('');
    var progress = $('hypitProgress');
    if (progress) progress.hidden = false;
    var bar = $('hypitProgressBar');
    var text = $('hypitProgressText');
    var xhr = new XMLHttpRequest();
    xhr.open('POST', localBase() + '/api/local/hypit/workflows', true);
    xhr.timeout = 30 * 60 * 1000;
    Object.keys(headers()).forEach(function (name) {
      if (String(name).toLowerCase() !== 'content-type') xhr.setRequestHeader(name, headers()[name]);
    });
    xhr.upload.onprogress = function (event) {
      if (!event.lengthComputable) return;
      var percent = Math.max(1, Math.min(99, Math.round(event.loaded * 100 / event.total)));
      if (bar) bar.style.width = percent + '%';
      if (text) text.textContent = '上传视频 ' + percent + '%';
    };
    xhr.onload = function () {
      var data = {};
      try { data = JSON.parse(xhr.responseText || '{}'); } catch (error) {}
      setBusy(false);
      if (xhr.status < 200 || xhr.status >= 300 || data.ok === false) {
        showMessage(errorText(data.detail) || ('提交失败（HTTP ' + xhr.status + '）'), true);
        return;
      }
      if (bar) bar.style.width = '100%';
      if (text) text.textContent = '已创建，正在自动处理…';
      state.jobId = data.job_id || '';
      try { localStorage.setItem('hypit-local-last-workflow', state.jobId); } catch (error) {}
      closeAddModal();
      showMessage('已创建记录，后续分析、素材生成和本机合成都自动完成。');
      loadList(1);
      pollJob(state.jobId);
    };
    xhr.onerror = function () { setBusy(false); showMessage('连接本机服务失败。', true); };
    xhr.ontimeout = function () { setBusy(false); showMessage('视频上传超时，可重新提交。', true); };
    xhr.send(form);
  }
  function pollJob(jobId) {
    if (state.pollTimer) clearTimeout(state.pollTimer);
    if (!jobId) return;
    request('/api/local/hypit/jobs/' + encodeURIComponent(jobId) + '/workflow').then(function (data) {
      var workflow = data.workflow || {};
      if (workflow.status === 'completed') {
        showMessage('视频复刻完成，可在列表里看成片。');
        loadList(state.page);
        return;
      }
      if (workflow.status === 'failed' || workflow.status === 'paused') {
        showMessage(workflow.error || workflow.stage || '任务未完成，可在记录里点继续。', true);
        loadList(state.page);
        return;
      }
      showMessage('正在处理：' + (workflow.stage || '') + '（记录已生成，可离开本页）');
      state.pollTimer = setTimeout(function () { pollJob(jobId); loadList(state.page); }, 8000);
    }).catch(function () {
      state.pollTimer = setTimeout(function () { pollJob(jobId); }, 15000);
    });
  }
  function resumeJob(jobId) {
    if (!jobId) return;
    request('/api/local/hypit/jobs/' + encodeURIComponent(jobId) + '/resume', { method: 'POST' }).then(function (data) {
      state.jobId = jobId;
      showMessage((data.workflow && data.workflow.stage) || '已继续，正在自动处理…');
      closeDetail();
      loadList(state.page);
      pollJob(jobId);
    }).catch(function (error) { showMessage(errorText(error.message), true); });
  }
  function deleteJob(jobId) {
    if (!jobId) return;
    if (!window.confirm('删除这条记录？本机任务目录（参考视频和中间素材）会被删掉。')) return;
    request('/api/local/hypit/jobs/' + encodeURIComponent(jobId) + '/delete', { method: 'POST' }).then(function () {
      showMessage('已删除这条记录。');
      closeDetail();
      if (state.jobId === jobId) state.jobId = '';
      loadList(state.page);
    }).catch(function (error) { showMessage(errorText(error.message), true); });
  }

  /* ---------------- 绑定 ---------------- */
  function bind() {
    if (state.initialized) return;
    state.initialized = true;

    var add = $('hypitAddButton');
    if (add) add.addEventListener('click', openAddModal);
    var addClose = $('hypitAddClose');
    if (addClose) addClose.addEventListener('click', closeAddModal);
    var addCancel = $('hypitAddCancel');
    if (addCancel) addCancel.addEventListener('click', closeAddModal);
    var addModal = $('hypitAddModal');
    if (addModal) addModal.addEventListener('click', function (event) { if (event.target === addModal) closeAddModal(); });

    var depChip = $('hypitDepChip');
    if (depChip) depChip.addEventListener('click', openInstallModal);
    var installClose = $('hypitInstallClose');
    if (installClose) installClose.addEventListener('click', closeInstallModal);
    var installModal = $('hypitInstallModal');
    if (installModal) installModal.addEventListener('click', function (event) { if (event.target === installModal) closeInstallModal(); });
    var installStart = $('hypitInstallStart');
    if (installStart) installStart.addEventListener('click', startInstall);
    var installRetry = $('hypitInstallRetry');
    if (installRetry) installRetry.addEventListener('click', startInstall);

    var choose = $('hypitChooseVideo');
    if (choose) choose.addEventListener('click', function () { $('hypitVideoInput').click(); });
    var replace = $('hypitReplaceVideo');
    if (replace) replace.addEventListener('click', function () { $('hypitVideoInput').click(); });
    var input = $('hypitVideoInput');
    if (input) input.addEventListener('change', function (event) {
      selectFile(event.target.files && event.target.files[0]);
      event.target.value = '';
    });
    var submit = $('hypitSubmitButton');
    if (submit) submit.addEventListener('click', submitWorkflow);

    var detailClose = $('hypitDetailClose');
    if (detailClose) detailClose.addEventListener('click', closeDetail);
    var detailModal = $('hypitDetailModal');
    if (detailModal) detailModal.addEventListener('click', function (event) { if (event.target === detailModal) closeDetail(); });

    var list = $('hypitList');
    if (list) list.addEventListener('click', function (event) {
      var target = event.target;
      if (!target || !target.getAttribute) return;
      var detail = target.getAttribute('data-hypit-detail');
      if (detail) { openDetail(detail); return; }
      var resume = target.getAttribute('data-hypit-resume');
      if (resume) { resumeJob(resume); return; }
      var remove = target.getAttribute('data-hypit-delete');
      if (remove) { deleteJob(remove); return; }
    });
    var foot = $('hypitDetailFoot');
    if (foot) foot.addEventListener('click', function (event) {
      var target = event.target;
      if (!target || !target.getAttribute) return;
      if (target.getAttribute('data-hypit-detail-close')) { closeDetail(); return; }
      var resume = target.getAttribute('data-hypit-resume');
      if (resume) { resumeJob(resume); return; }
      var remove = target.getAttribute('data-hypit-delete');
      if (remove) { deleteJob(remove); return; }
    });
    var pager = $('hypitPager');
    if (pager) pager.addEventListener('click', function (event) {
      var target = event.target;
      if (!target || !target.getAttribute) return;
      var page = target.getAttribute('data-hypit-page');
      if (page) loadList(page);
    });

    refreshRuntimeStatus().catch(function () {});
    loadList(1);
    if (state.refreshTimer) clearInterval(state.refreshTimer);
    state.refreshTimer = setInterval(function () {
      if (document.hidden) return;
      loadList(state.page);
    }, 20000);
  }

  window.initHypitVideoStudioView = function () {
    bind();
    return Promise.resolve();
  };
})();
