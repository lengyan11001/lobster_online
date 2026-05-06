/* global API_BASE, authHeaders, escapeHtml */

(function () {
  'use strict';

  var state = { loaded: false, devicesLoaded: false };

  function base() {
    return (typeof API_BASE !== 'undefined' && API_BASE) ? String(API_BASE).replace(/\/$/, '') : '';
  }

  function headers() {
    return Object.assign({ 'Content-Type': 'application/json' }, typeof authHeaders === 'function' ? authHeaders() : {});
  }

  function html(s) {
    if (typeof escapeHtml === 'function') return escapeHtml(s == null ? '' : String(s));
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }

  function api(path, options) {
    var b = base();
    if (!b) return Promise.reject(new Error('API_BASE not configured'));
    options = options || {};
    options.headers = Object.assign(headers(), options.headers || {});
    return fetch(b + path, options).then(function (r) {
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

  function parsePayload(text) {
    var raw = (text || '').trim();
    if (!raw) return {};
    var obj = JSON.parse(raw);
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) throw new Error('JSON 必须是对象');
    return obj;
  }

  function buildPayload(prefix) {
    var kind = (document.getElementById(prefix + 'Kind') || {}).value || 'openclaw_message';
    var scheduleType = (document.getElementById(prefix + 'ScheduleType') || {}).value || 'once';
    var intervalMin = parseInt((document.getElementById(prefix + 'IntervalMinutes') || {}).value || '60', 10);
    var body = {
      title: ((document.getElementById(prefix + 'Title') || {}).value || '').trim(),
      task_kind: kind,
      content: ((document.getElementById(prefix + 'Content') || {}).value || '').trim(),
      payload: {},
      schedule_type: scheduleType,
      interval_seconds: Math.max(60, (isNaN(intervalMin) ? 60 : intervalMin) * 60),
      installation_ids: getSelected(prefix + 'Devices')
    };
    if (kind === 'capability') {
      var parsed = parsePayload((document.getElementById(prefix + 'Payload') || {}).value || '');
      if (parsed.capability_id) {
        body.payload = parsed;
      } else {
        body.payload = { capability_id: parsed.capability || parsed.id || '', payload: parsed.payload || parsed };
      }
      body.content = body.content || ('调用能力 ' + (body.payload.capability_id || ''));
    }
    return body;
  }

  function togglePayload(prefix) {
    var kind = (document.getElementById(prefix + 'Kind') || {}).value || 'openclaw_message';
    var payloadWrap = document.getElementById(prefix + 'PayloadWrap');
    var contentWrap = document.getElementById(prefix + 'ContentWrap');
    if (payloadWrap) payloadWrap.style.display = kind === 'capability' ? '' : 'none';
    if (contentWrap) contentWrap.style.display = kind === 'capability' ? 'none' : '';
  }

  function loadDevices(selectId) {
    var sel = document.getElementById(selectId);
    if (!sel) return Promise.resolve();
    sel.innerHTML = '<option value="">加载中...</option>';
    return api('/api/h5-chat/devices/status').then(function (d) {
      var devices = d.devices || [];
      if (!devices.length) {
        sel.innerHTML = '<option value="">暂无在线设备</option>';
        return;
      }
      sel.innerHTML = devices.map(function (x) {
        var label = (x.display_name || x.installation_id || '-');
        label += x.online ? ' · 在线' : ' · 离线';
        return '<option value="' + html(x.installation_id || '') + '">' + html(label) + '</option>';
      }).join('');
    }).catch(function () {
      sel.innerHTML = '<option value="">设备加载失败</option>';
    });
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
      h += '<tr style="border-bottom:1px solid rgba(255,255,255,0.08);">'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + html(fmtTime(r.created_at)) + '</td>'
        + '<td style="padding:0.5rem;">' + html(r.title || r.content || r.id) + '</td>'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + html(kindText(r.task_kind)) + '</td>'
        + '<td style="padding:0.5rem;font-size:0.75rem;color:var(--text-muted);">' + html(r.installation_id || '任意设备') + '</td>'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + html(statusText(r.status)) + '</td>'
        + '<td style="padding:0.5rem;max-width:26rem;white-space:pre-wrap;word-break:break-word;">' + html(result || '-') + '</td>'
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
      + '<th style="padding:0.5rem;">状态</th><th style="padding:0.5rem;">下次执行</th><th style="padding:0.5rem;">次数</th>'
      + '</tr></thead><tbody>';
    rows.forEach(function (t) {
      var interval = t.schedule_type === 'interval' ? ('每 ' + Math.round((t.interval_seconds || 0) / 60) + ' 分钟') : '一次性';
      h += '<tr style="border-bottom:1px solid rgba(255,255,255,0.08);">'
        + '<td style="padding:0.5rem;">' + html(t.title || t.content || t.id) + '</td>'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + html(kindText(t.task_kind)) + '</td>'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + html(interval) + '</td>'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + html(statusText(t.status)) + '</td>'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + html(fmtTime(t.next_run_at)) + '</td>'
        + '<td style="padding:0.5rem;">' + (t.run_count || 0) + '</td>'
        + '</tr>';
    });
    h += '</tbody></table></div>';
    el.innerHTML = h;
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
    api('/api/scheduled-tasks/tasks', { method: 'POST', body: JSON.stringify(body) })
      .then(function () {
        showMsg('scheduledTaskMsg', '已创建并下发', false);
        loadRuns();
        loadTasks();
      })
      .catch(function (e) { showMsg('scheduledTaskMsg', e.message, true); })
      .finally(function () {
        if (btn) { btn.disabled = false; btn.textContent = '添加并下发'; }
      });
  }

  function bind() {
    if (state.bound) return;
    state.bound = true;
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
    if (refresh) refresh.addEventListener('click', function () { loadRuns(); loadTasks(); loadDevices('scheduledTaskDevices'); });
    var create = document.getElementById('scheduledTaskCreateBtn');
    if (create) create.addEventListener('click', createTask);
    var kind = document.getElementById('scheduledTaskKind');
    if (kind) kind.addEventListener('change', function () { togglePayload('scheduledTask'); });
    togglePayload('scheduledTask');
  }

  window.initScheduledTasksView = function initScheduledTasksView() {
    bind();
    loadDevices('scheduledTaskDevices');
    loadRuns();
    loadTasks();
  };

  window.__scheduledTasksApi = {
    api: api,
    html: html,
    fmtTime: fmtTime,
    kindText: kindText,
    buildPayload: buildPayload,
    togglePayload: togglePayload,
    showMsg: showMsg
  };
})();
