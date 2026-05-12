/* global API_BASE, LOCAL_API_BASE, authHeaders, escapeHtml, getOrCreateInstallationId */

(function () {
  'use strict';

  var CAPABILITIES = {
    'goal.video.pipeline': {
      label: '目标成片',
      description: '根据记忆和目标自动生成图片/视频成片。'
    },
    'hifly.video.create_by_tts': {
      label: '飞影数字人',
      description: '选择本机已有数字人/模板数字人和声音，生成数字人口播视频。'
    }
  };

  var state = {
    bound: false,
    avatarRows: [],
    voiceRows: [],
    hiflyLoaded: false,
    hiflyLoading: false
  };

  function base() {
    var local = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? String(LOCAL_API_BASE).replace(/\/$/, '') : '';
    var remote = (typeof API_BASE !== 'undefined' && API_BASE) ? String(API_BASE).replace(/\/$/, '') : '';
    return local || remote;
  }

  function headers(withBody) {
    var h = Object.assign({}, typeof authHeaders === 'function' ? authHeaders() : {});
    if (withBody) h['Content-Type'] = 'application/json';
    else {
      delete h['Content-Type'];
      delete h['content-type'];
    }
    return h;
  }

  function authHeadersNoContentType() {
    var h = Object.assign({}, typeof authHeaders === 'function' ? authHeaders() : {});
    delete h['Content-Type'];
    delete h['content-type'];
    return h;
  }

  function currentInstallationId() {
    return typeof getOrCreateInstallationId === 'function' ? String(getOrCreateInstallationId() || '').trim() : '';
  }

  function friendlyError(err) {
    var msg = err && err.message ? String(err.message) : String(err || '');
    var name = err && err.name ? String(err.name) : '';
    if (name === 'AbortError' || /aborted|abort/i.test(msg)) {
      return '请求超时，请确认本机盒子和云端服务可访问。';
    }
    if (msg === 'Failed to fetch' || /NetworkError/i.test(msg)) {
      return '无法连接定时任务服务，请确认本机盒子已启动并能访问云端。';
    }
    return msg || '请求失败';
  }

  function fetchWithTimeout(url, options, timeoutMs) {
    options = options || {};
    if (typeof AbortController === 'undefined') return fetch(url, options);
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, timeoutMs || 12000);
    var opts = Object.assign({}, options, { signal: controller.signal });
    return fetch(url, opts).then(function (r) {
      clearTimeout(timer);
      return r;
    }, function (e) {
      clearTimeout(timer);
      throw e;
    });
  }

  function html(s) {
    if (typeof escapeHtml === 'function') return escapeHtml(s == null ? '' : String(s));
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }

  function optionHtml(value, label) {
    return '<option value="' + html(value || '') + '">' + html(label || value || '-') + '</option>';
  }

  function api(path, options) {
    var b = base();
    if (!b) return Promise.reject(new Error('任务服务地址未配置'));
    options = options || {};
    var hasBody = options.body != null;
    options.headers = Object.assign(headers(hasBody), options.headers || {});
    return fetchWithTimeout(b + path, options).catch(function (e) {
      throw new Error(friendlyError(e));
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error((d && d.detail) || ('HTTP ' + r.status));
        return d;
      });
    });
  }

  function postLocal(path, body) {
    return api(path, { method: 'POST', body: JSON.stringify(body || {}) });
  }

  function cloudBase() {
    return (typeof API_BASE !== 'undefined' && API_BASE) ? String(API_BASE).replace(/\/$/, '') : '';
  }

  function getCloud(path) {
    var b = cloudBase();
    if (!b) return Promise.reject(new Error('云端服务地址未配置'));
    return fetchWithTimeout(b + path, { method: 'GET', headers: headers(false) }).catch(function (e) {
      throw new Error(friendlyError(e));
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error((d && d.detail) || ('HTTP ' + r.status));
        return d;
      });
    });
  }

  function fmtTime(v) {
    if (!v) return '-';
    var d = new Date(v);
    if (isNaN(d.getTime())) return String(v).replace('T', ' ').slice(0, 19);
    return d.toLocaleString('zh-CN', { hour12: false });
  }

  function statusText(s) {
    return {
      pending: '等待中',
      processing: '执行中',
      completed: '完成',
      failed: '失败',
      cancelled: '已取消',
      active: '启用',
      paused: '暂停'
    }[s] || s || '-';
  }

  function kindText(k) {
    return {
      openclaw_message: 'OpenClaw 消息',
      chat_message: '本地对话',
      capability: '能力调用'
    }[k] || k || '-';
  }

  function capabilityText(id) {
    return (CAPABILITIES[id] && CAPABILITIES[id].label) || id || '';
  }

  function resultPayload(row) {
    return row && row.result_payload && typeof row.result_payload === 'object' ? row.result_payload : {};
  }

  function collectMediaUrls(row) {
    var payload = resultPayload(row);
    var refs = payload.result_refs && typeof payload.result_refs === 'object' ? payload.result_refs : {};
    var raw = [];
    if (Array.isArray(payload.media_urls)) raw = raw.concat(payload.media_urls);
    if (Array.isArray(refs.urls)) raw = raw.concat(refs.urls);
    var seen = {};
    return raw.map(function (u) { return String(u || '').trim(); }).filter(function (u) {
      if (!/^https?:\/\//i.test(u) || seen[u]) return false;
      seen[u] = true;
      return true;
    }).slice(0, 4);
  }

  function mediaPreviewHtml(urls) {
    if (!urls || !urls.length) return '';
    return '<div style="display:flex;gap:0.45rem;flex-wrap:wrap;margin-top:0.45rem;">' + urls.map(function (u) {
      var low = u.toLowerCase();
      if (/\.(mp4|webm|mov)(\?|#|$)/.test(low)) {
        return '<video controls src="' + html(u) + '" style="width:140px;max-width:100%;height:86px;object-fit:cover;border-radius:8px;background:#111;"></video>';
      }
      if (/\.(png|jpe?g|webp|gif)(\?|#|$)/.test(low)) {
        return '<a href="' + html(u) + '" target="_blank" rel="noopener noreferrer"><img src="' + html(u) + '" style="width:86px;height:86px;object-fit:cover;border-radius:8px;border:1px solid var(--border);"></a>';
      }
      return '<a href="' + html(u) + '" target="_blank" rel="noopener noreferrer" style="display:inline-flex;align-items:center;min-height:2rem;">打开预览</a>';
    }).join('') + '</div>';
  }

  function taskActionHtml(task) {
    var status = String((task && task.status) || '').toLowerCase();
    if (status === 'cancelled' || status === 'completed') return '';
    var next = status === 'paused' ? 'active' : 'paused';
    var label = status === 'paused' ? '恢复' : '暂停';
    return '<button type="button" class="btn btn-ghost btn-sm scheduled-task-status-btn" data-task-id="'
      + html(task.id)
      + '" data-next-status="' + html(next) + '">'
      + html(label)
      + '</button>';
  }

  function rowCapabilityId(row) {
    var payload = row && row.payload && typeof row.payload === 'object' ? row.payload : {};
    return String(payload.capability_id || '');
  }

  function getSelected(selectId) {
    var el = document.getElementById(selectId);
    if (!el) return [];
    return Array.prototype.slice.call(el.selectedOptions || []).map(function (o) { return o.value; }).filter(Boolean);
  }

  function showMsg(id, text, bad) {
    var el = document.getElementById(id);
    if (!el) return;
    el.style.display = text ? '' : 'none';
    el.style.color = bad ? '#e74c3c' : '#2f9e66';
    el.textContent = text || '';
  }

  function val(id) {
    var el = document.getElementById(id);
    return el ? String(el.value || '').trim() : '';
  }

  function setVal(id, value) {
    var el = document.getElementById(id);
    if (el) el.value = value == null ? '' : String(value);
  }

  function parseAssetIds(text) {
    var seen = {};
    var out = [];
    String(text || '').split(/[\s,，;；]+/).forEach(function (raw) {
      var v = String(raw || '').trim();
      if (!v || seen[v]) return;
      seen[v] = true;
      out.push(v);
    });
    return out.slice(0, 20);
  }

  function mergeAssetIds(baseIds, extraIds) {
    var seen = {};
    var out = [];
    (baseIds || []).concat(extraIds || []).forEach(function (raw) {
      var v = String(raw || '').trim();
      if (!v || seen[v]) return;
      seen[v] = true;
      out.push(v);
    });
    return out.slice(0, 20);
  }

  function applyAttachmentAssetIds(body, assetIds) {
    var ids = mergeAssetIds([], assetIds || []);
    if (!ids.length) return body;
    body.payload = body.payload && typeof body.payload === 'object' && !Array.isArray(body.payload) ? body.payload : {};
    body.payload.attachment_asset_ids = mergeAssetIds(body.payload.attachment_asset_ids || [], ids);
    var capPayload = body.payload.payload;
    if (!capPayload || typeof capPayload !== 'object' || Array.isArray(capPayload)) {
      capPayload = {};
      body.payload.payload = capPayload;
    }
    capPayload.attachment_asset_ids = mergeAssetIds(capPayload.attachment_asset_ids || [], ids);
    capPayload.asset_ids = mergeAssetIds(capPayload.asset_ids || [], ids);
    return body;
  }

  function uploadOneAsset(file) {
    var fd = new FormData();
    fd.append('file', file);
    return fetch(base() + '/api/assets/upload', {
      method: 'POST',
      headers: authHeadersNoContentType(),
      body: fd
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error((d && d.detail) || ('上传素材失败: HTTP ' + r.status));
        if (!d || !d.asset_id) throw new Error('上传素材失败：未返回 asset_id');
        return String(d.asset_id);
      });
    });
  }

  function collectAttachmentAssetIds(prefix) {
    var typed = parseAssetIds(((document.getElementById(prefix + 'AssetIds') || {}).value || ''));
    var input = document.getElementById(prefix + 'AssetUpload');
    var files = input && input.files ? Array.prototype.slice.call(input.files) : [];
    if (!files.length) return Promise.resolve(typed);
    showMsg(prefix + 'Msg', '正在上传素材…', false);
    return files.reduce(function (p, file) {
      return p.then(function (ids) {
        return uploadOneAsset(file).then(function (assetId) {
          ids.push(assetId);
          showMsg(prefix + 'Msg', '已上传素材 ' + ids.length + '/' + files.length, false);
          return ids;
        });
      });
    }, Promise.resolve([])).then(function (uploaded) {
      return mergeAssetIds(typed, uploaded);
    });
  }

  function compactGrid(inner) {
    return '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:0.5rem 0.65rem;align-items:start;">' + inner + '</div>';
  }

  function fieldHtml(label, control, full) {
    return '<div class="modal-field" style="margin:0;' + (full ? 'grid-column:1/-1;' : '') + '">'
      + '<label>' + html(label) + '</label>' + control + '</div>';
  }

  function inputHtml(id, type, attrs) {
    return '<input id="' + html(id) + '" type="' + html(type || 'text') + '" ' + (attrs || '') + ' style="width:100%;box-sizing:border-box;">';
  }

  function selectHtml(id, options) {
    return '<select id="' + html(id) + '" style="width:100%;padding:0.45rem;">' + options + '</select>';
  }

  function textareaHtml(id, rows, placeholder) {
    return '<textarea id="' + html(id) + '" rows="' + html(rows || 3) + '" placeholder="' + html(placeholder || '') + '"></textarea>';
  }

  function renderGoalFields(host) {
    host.innerHTML = '';
  }

  function avatarLabel(row) {
    var parts = [];
    if (row.section_label) parts.push(row.section_label);
    parts.push(row.title || row.avatar || '-');
    return parts.join(' · ');
  }

  function normalizeAvatarRows(rows) {
    var seen = {};
    var out = [];
    (rows || []).forEach(function (row) {
      var id = String(row && row.avatar || '').trim();
      if (!id || seen[id]) return;
      seen[id] = true;
      out.push(row);
    });
    return out;
  }

  function normalizeVoiceRows(rows) {
    var out = [];
    var seen = {};
    (rows || []).forEach(function (row) {
      if (!row) return;
      var styles = Array.isArray(row.styles) && row.styles.length ? row.styles : [row];
      styles.forEach(function (style) {
        var id = String((style && style.voice) || row.voice || '').trim();
        if (!id || /^consumer_/i.test(id) || seen[id]) return;
        seen[id] = true;
        var groupTitle = String(row.title || '').trim();
        var styleLabel = String((style && (style.label || style.title)) || '').trim();
        var label = groupTitle || styleLabel || id;
        if (styleLabel && styleLabel !== '默认风格' && styleLabel !== groupTitle) label += ' · ' + styleLabel;
        if (row.section_label) label = row.section_label + ' · ' + label;
        out.push({ voice: id, label: label });
      });
    });
    return out;
  }

  function fillHiflySelects() {
    var avatarSel = document.getElementById('scheduledTaskHiflyAvatar');
    var voiceSel = document.getElementById('scheduledTaskHiflyVoice');
    if (avatarSel) {
      avatarSel.innerHTML = state.avatarRows.length
        ? state.avatarRows.map(function (row) { return optionHtml(row.avatar, avatarLabel(row)); }).join('')
        : optionHtml('', '暂无可用数字人');
    }
    if (voiceSel) {
      voiceSel.innerHTML = state.voiceRows.length
        ? state.voiceRows.map(function (row) { return optionHtml(row.voice, row.label); }).join('')
        : optionHtml('', '暂无可用声音');
    }
  }

  function loadHiflyLibraries() {
    if (state.hiflyLoaded || state.hiflyLoading) return Promise.resolve();
    state.hiflyLoading = true;
    var avatarSel = document.getElementById('scheduledTaskHiflyAvatar');
    var voiceSel = document.getElementById('scheduledTaskHiflyVoice');
    if (avatarSel) avatarSel.innerHTML = optionHtml('', '加载中...');
    if (voiceSel) voiceSel.innerHTML = optionHtml('', '加载中...');
    return Promise.all([
      getCloud('/api/hifly/my/avatar/list?page=1&size=100').catch(function () { return { items: [] }; }),
      postLocal('/api/hifly/avatar/library', { page: 1, size: 100, include_mine: true }).catch(function () { return { public: [] }; }),
      getCloud('/api/hifly/my/voice/list?page=1&size=100').catch(function () { return { items: [] }; }),
      postLocal('/api/hifly/voice/library', {}).catch(function () { return { public: [] }; })
    ]).then(function (results) {
      var myAvatarData = results[0] || {};
      var avatarData = results[1] || {};
      var myVoiceData = results[2] || {};
      var voiceData = results[3] || {};
      state.avatarRows = normalizeAvatarRows([].concat(myAvatarData.items || [], avatarData.mine || [], avatarData.public || []));
      state.voiceRows = normalizeVoiceRows([].concat(myVoiceData.items || [], voiceData.mine || [], voiceData.public || []));
      state.hiflyLoaded = true;
      fillHiflySelects();
    }).catch(function (e) {
      if (avatarSel) avatarSel.innerHTML = optionHtml('', '数字人加载失败');
      if (voiceSel) voiceSel.innerHTML = optionHtml('', '声音加载失败');
      showMsg('scheduledTaskMsg', e.message || '飞影资源加载失败', true);
    }).then(function () {
      state.hiflyLoading = false;
    }, function (e) {
      state.hiflyLoading = false;
      throw e;
    });
  }

  function renderHiflyFields(host) {
    host.innerHTML = compactGrid(
      fieldHtml('数字人', selectHtml('scheduledTaskHiflyAvatar', optionHtml('', '加载中...')))
      + fieldHtml('声音', selectHtml('scheduledTaskHiflyVoice', optionHtml('', '加载中...')))
    );
    fillHiflySelects();
    loadHiflyLibraries();
  }

  function renderParamFields() {
    var host = document.getElementById('scheduledTaskParamFields');
    if (!host) return;
    var capabilityId = val('scheduledTaskCapability') || 'goal.video.pipeline';
    if (capabilityId === 'hifly.video.create_by_tts') renderHiflyFields(host);
    else renderGoalFields(host);
  }

  function loadCapabilities() {
    var sel = document.getElementById('scheduledTaskCapability');
    if (!sel) return Promise.resolve();
    var current = sel.value;
    sel.innerHTML = optionHtml('goal.video.pipeline', '目标成片') + optionHtml('hifly.video.create_by_tts', '飞影数字人');
    sel.value = CAPABILITIES[current] ? current : 'goal.video.pipeline';
    toggleCapability();
    return Promise.resolve();
  }

  function toggleCapability() {
    var capabilityId = val('scheduledTaskCapability') || 'goal.video.pipeline';
    var hint = document.getElementById('scheduledTaskCapabilityHint');
    if (hint) hint.textContent = (CAPABILITIES[capabilityId] || {}).description || '';
    var paramBlock = document.getElementById('scheduledTaskParamBlock');
    if (paramBlock) paramBlock.style.display = capabilityId === 'hifly.video.create_by_tts' ? '' : 'none';
    var autoFill = document.getElementById('scheduledTaskAutoFillBtn');
    if (autoFill) autoFill.style.display = capabilityId === 'hifly.video.create_by_tts' ? '' : 'none';
    renderParamFields();
  }

  function autoFillParams() {
    var capabilityId = val('scheduledTaskCapability') || 'goal.video.pipeline';
    if (capabilityId === 'hifly.video.create_by_tts') {
      if (!val('scheduledTaskHiflyAvatar') && state.avatarRows[0]) setVal('scheduledTaskHiflyAvatar', state.avatarRows[0].avatar);
      if (!val('scheduledTaskHiflyVoice') && state.voiceRows[0]) setVal('scheduledTaskHiflyVoice', state.voiceRows[0].voice);
      loadHiflyLibraries();
    }
  }

  function collectCapabilityPayload() {
    var capabilityId = val('scheduledTaskCapability') || 'goal.video.pipeline';
    if (capabilityId === 'hifly.video.create_by_tts') {
      var avatar = val('scheduledTaskHiflyAvatar');
      var voice = val('scheduledTaskHiflyVoice');
      if (!avatar) throw new Error('请选择数字人');
      if (!voice) throw new Error('请选择声音');
      return {
        avatar: avatar,
        voice: voice
      };
    }
    return {};
  }

  function buildPayload(prefix) {
    var kind = 'capability';
    var capabilityId = val(prefix + 'Capability') || 'goal.video.pipeline';
    var scheduleType = (document.getElementById(prefix + 'ScheduleType') || {}).value || 'once';
    var intervalMin = parseInt((document.getElementById(prefix + 'IntervalMinutes') || {}).value || '60', 10);
    var installationIds = prefix === 'scheduledTask' ? [currentInstallationId()].filter(Boolean) : getSelected(prefix + 'Devices');
    var title = val(prefix + 'Title') || (capabilityText(capabilityId) || '能力定时任务');
    var capPayload = collectCapabilityPayload();
    return {
      title: title,
      task_kind: kind,
      content: '定时调用能力 ' + capabilityId,
      payload: { capability_id: capabilityId, payload: capPayload },
      schedule_type: scheduleType,
      interval_seconds: Math.max(60, (isNaN(intervalMin) ? 60 : intervalMin) * 60),
      installation_ids: installationIds
    };
  }

  function renderRuns(rows) {
    var el = document.getElementById('scheduledTaskRunsList');
    if (!el) return;
    if (!rows || !rows.length) {
      el.innerHTML = '<p class="meta">暂无执行记录。</p>';
      return;
    }
    var h = '<div style="overflow:auto;"><table style="width:100%;border-collapse:collapse;font-size:0.82rem;">'
      + '<thead><tr style="text-align:left;border-bottom:1px solid var(--border);">'
      + '<th style="padding:0.5rem;">时间</th><th style="padding:0.5rem;">任务</th><th style="padding:0.5rem;">类型</th>'
      + '<th style="padding:0.5rem;">设备</th><th style="padding:0.5rem;">状态</th><th style="padding:0.5rem;">结果/错误</th>'
      + '</tr></thead><tbody>';
    rows.forEach(function (r) {
      var result = r.error || r.result_text || (r.progress && (r.progress.text || r.progress.message)) || '';
      var previews = mediaPreviewHtml(collectMediaUrls(r));
      h += '<tr style="border-bottom:1px solid rgba(255,255,255,0.08);">'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + html(fmtTime(r.created_at)) + '</td>'
        + '<td style="padding:0.5rem;">' + html(r.title || r.content || r.id) + '</td>'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + html(capabilityText(rowCapabilityId(r)) || kindText(r.task_kind)) + '</td>'
        + '<td style="padding:0.5rem;font-size:0.75rem;color:var(--text-muted);">' + html(r.installation_id || '任意设备') + '</td>'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + html(statusText(r.status)) + '</td>'
        + '<td style="padding:0.5rem;max-width:26rem;white-space:pre-wrap;word-break:break-word;">' + html(result || '-') + previews + '</td>'
        + '</tr>';
    });
    h += '</tbody></table></div>';
    el.innerHTML = h;
  }

  function renderTasks(rows) {
    var el = document.getElementById('scheduledTaskList');
    if (!el) return;
    if (!rows || !rows.length) {
      el.innerHTML = '<p class="meta">暂无任务。</p>';
      return;
    }
    var h = '<div style="overflow:auto;"><table style="width:100%;border-collapse:collapse;font-size:0.82rem;">'
      + '<thead><tr style="text-align:left;border-bottom:1px solid var(--border);">'
      + '<th style="padding:0.5rem;">任务</th><th style="padding:0.5rem;">类型</th><th style="padding:0.5rem;">调度</th>'
      + '<th style="padding:0.5rem;">状态</th><th style="padding:0.5rem;">下次执行</th><th style="padding:0.5rem;">次数</th><th style="padding:0.5rem;">操作</th>'
      + '</tr></thead><tbody>';
    rows.forEach(function (t) {
      var interval = t.schedule_type === 'interval' ? ('每 ' + Math.round((t.interval_seconds || 0) / 60) + ' 分钟') : '一次性';
      h += '<tr style="border-bottom:1px solid rgba(255,255,255,0.08);">'
        + '<td style="padding:0.5rem;">' + html(t.title || t.content || t.id) + '</td>'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + html(capabilityText(rowCapabilityId(t)) || kindText(t.task_kind)) + '</td>'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + html(interval) + '</td>'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + html(statusText(t.status)) + '</td>'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + html(fmtTime(t.next_run_at)) + '</td>'
        + '<td style="padding:0.5rem;">' + (t.run_count || 0) + '</td>'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + taskActionHtml(t) + '</td>'
        + '</tr>';
    });
    h += '</tbody></table></div>';
    el.innerHTML = h;
  }

  function updateTaskStatus(taskId, status, btn) {
    if (!taskId || !status) return;
    if (btn) {
      btn.disabled = true;
      btn.textContent = status === 'paused' ? '暂停中...' : '恢复中...';
    }
    api('/api/scheduled-tasks/tasks/' + encodeURIComponent(taskId), {
      method: 'PATCH',
      body: JSON.stringify({ status: status })
    }).then(function () {
      loadTasks();
      loadRuns();
    }).catch(function (e) {
      showMsg('scheduledTaskMsg', e.message, true);
      if (btn) {
        btn.disabled = false;
        btn.textContent = status === 'paused' ? '暂停' : '恢复';
      }
    });
  }

  function loadRuns() {
    var el = document.getElementById('scheduledTaskRunsList');
    if (el) el.innerHTML = '<p class="meta">加载中...</p>';
    return api('/api/scheduled-tasks/runs?limit=80').then(function (d) {
      renderRuns(d.runs || []);
    }).catch(function (e) {
      if (el) el.innerHTML = '<p class="meta" style="color:#e74c3c;">' + html(e.message) + '</p>';
    });
  }

  function loadTasks() {
    var el = document.getElementById('scheduledTaskList');
    if (el) el.innerHTML = '<p class="meta">加载中...</p>';
    return api('/api/scheduled-tasks/tasks?limit=80').then(function (d) {
      renderTasks(d.tasks || []);
    }).catch(function (e) {
      if (el) el.innerHTML = '<p class="meta" style="color:#e74c3c;">' + html(e.message) + '</p>';
    });
  }

  function createTask() {
    var btn = document.getElementById('scheduledTaskCreateBtn');
    if (btn) { btn.disabled = true; btn.textContent = '提交中...'; }
    showMsg('scheduledTaskMsg', '', false);
    var body;
    try {
      body = buildPayload('scheduledTask');
    } catch (e) {
      showMsg('scheduledTaskMsg', e.message, true);
      if (btn) { btn.disabled = false; btn.textContent = '添加并下发'; }
      return;
    }
    if (!body.installation_ids || !body.installation_ids.length) {
      showMsg('scheduledTaskMsg', '未获取到本机设备标识，请刷新页面或重启本机盒子后再试。', true);
      if (btn) { btn.disabled = false; btn.textContent = '添加并下发'; }
      return;
    }
    collectAttachmentAssetIds('scheduledTask').then(function (assetIds) {
      applyAttachmentAssetIds(body, assetIds);
      return api('/api/scheduled-tasks/tasks', { method: 'POST', body: JSON.stringify(body) });
    }).then(function () {
      showMsg('scheduledTaskMsg', '已创建并下发', false);
      var upload = document.getElementById('scheduledTaskAssetUpload');
      if (upload) upload.value = '';
      loadRuns();
      loadTasks();
    }).catch(function (e) {
      showMsg('scheduledTaskMsg', e.message, true);
    }).then(function () {
      if (btn) { btn.disabled = false; btn.textContent = '添加并下发'; }
    }, function () {
      if (btn) { btn.disabled = false; btn.textContent = '添加并下发'; }
    });
  }

  function bind() {
    if (state.bound) return;
    state.bound = true;
    var deviceField = document.getElementById('scheduledTaskDevices');
    if (deviceField && deviceField.closest) {
      deviceField.closest('.modal-field').style.display = 'none';
    }
    document.querySelectorAll('.sched-tab').forEach(function (tab) {
      tab.addEventListener('click', function () {
        var name = tab.getAttribute('data-sched-tab');
        document.querySelectorAll('.sched-tab').forEach(function (x) { x.classList.toggle('active', x === tab); });
        var runs = document.getElementById('schedTabRuns');
        var tasks = document.getElementById('schedTabTasks');
        if (runs) runs.style.display = name === 'runs' ? '' : 'none';
        if (tasks) tasks.style.display = name === 'tasks' ? '' : 'none';
        if (name === 'runs') loadRuns(); else loadTasks();
      });
    });
    var refresh = document.getElementById('scheduledTaskRefreshBtn');
    if (refresh) refresh.addEventListener('click', function () { loadRuns(); loadTasks(); });
    var create = document.getElementById('scheduledTaskCreateBtn');
    if (create) create.addEventListener('click', createTask);
    document.addEventListener('click', function (evt) {
      var btn = evt.target && evt.target.closest ? evt.target.closest('.scheduled-task-status-btn') : null;
      if (!btn) return;
      updateTaskStatus(btn.getAttribute('data-task-id'), btn.getAttribute('data-next-status'), btn);
    });
    var kind = document.getElementById('scheduledTaskKind');
    if (kind) kind.value = 'capability';
    var capability = document.getElementById('scheduledTaskCapability');
    if (capability) capability.addEventListener('change', toggleCapability);
    var autoFill = document.getElementById('scheduledTaskAutoFillBtn');
    if (autoFill) autoFill.addEventListener('click', autoFillParams);
  }

  window.initScheduledTasksView = function initScheduledTasksView() {
    bind();
    loadCapabilities();
    loadRuns();
    loadTasks();
  };

  window.__scheduledTasksApi = {
    api: api,
    buildPayload: buildPayload,
    loadCapabilities: loadCapabilities,
    loadHiflyLibraries: loadHiflyLibraries,
    autoFillParams: autoFillParams,
    parseAssetIds: parseAssetIds,
    applyAttachmentAssetIds: applyAttachmentAssetIds
  };
})();
