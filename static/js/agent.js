/* global API_BASE, authHeaders, escapeHtml */

(function () {
  'use strict';

  var _loaded = false;

  window.loadAgentSubUsers = function loadAgentSubUsers() {
    var listEl = document.getElementById('agentSubUserList');
    var countEl = document.getElementById('agentSubUserCount');
    if (!listEl) return;

    var base = (typeof API_BASE !== 'undefined' && API_BASE) ? String(API_BASE).replace(/\/$/, '') : '';
    if (!base) return;

    if (!_loaded) countEl.textContent = '下级用户：加载中…';

    fetch(base + '/auth/agent/sub-users', { headers: authHeaders() })
      .then(function (r) {
        if (r.status === 403) return { sub_users: [], count: 0, _forbidden: true };
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (d) {
        _loaded = true;
        if (d._forbidden) {
          countEl.textContent = '无权访问（非代理商）';
          listEl.innerHTML = '';
          return;
        }
        var list = d.sub_users || [];
        countEl.textContent = '下级用户：' + (d.count || list.length) + ' 人';
        if (!list.length) {
          listEl.innerHTML = '<p style="color:var(--text-muted);font-size:0.88rem;">暂无下级用户</p>';
          return;
        }
        var h = '<table style="width:100%;border-collapse:collapse;font-size:0.85rem;">'
          + '<thead><tr style="border-bottom:2px solid rgba(255,255,255,0.12);text-align:left;">'
          + '<th style="padding:0.55rem;">ID</th>'
          + '<th style="padding:0.55rem;">账号</th>'
          + '<th style="padding:0.55rem;text-align:right;">当前算力</th>'
          + '<th style="padding:0.55rem;text-align:right;">累计充值</th>'
          + '<th style="padding:0.55rem;">注册时间</th>'
          + '</tr></thead><tbody>';
        list.forEach(function (u) {
          var email = u.email || '-';
          var display = email.replace(/@sms\.lobster\.local$/, '');
          var created = (u.created_at || '').replace('T', ' ').substring(0, 19);
          h += '<tr style="border-bottom:1px solid rgba(255,255,255,0.06);">'
            + '<td style="padding:0.5rem;">' + u.id + '</td>'
            + '<td style="padding:0.5rem;">' + escapeHtml(display) + '</td>'
            + '<td style="padding:0.5rem;text-align:right;">' + (u.credits != null ? u.credits : '-') + '</td>'
            + '<td style="padding:0.5rem;text-align:right;">' + (u.total_recharged || 0) + '</td>'
            + '<td style="padding:0.5rem;">' + escapeHtml(created) + '</td>'
            + '</tr>';
        });
        h += '</tbody></table>';
        listEl.innerHTML = h;
      })
      .catch(function (e) {
        countEl.textContent = '加载失败';
        listEl.innerHTML = '<p style="color:#e74c3c;font-size:0.85rem;">' + escapeHtml(String(e)) + '</p>';
      });
  };

  var btn = document.getElementById('agentRefreshBtn');
  if (btn) btn.addEventListener('click', function () { _loaded = false; loadAgentSubUsers(); });
})();

(function () {
  'use strict';

  function base() {
    return (typeof API_BASE !== 'undefined' && API_BASE) ? String(API_BASE).replace(/\/$/, '') : '';
  }

  function h(s) {
    return typeof escapeHtml === 'function'
      ? escapeHtml(s == null ? '' : String(s))
      : String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
          return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
        });
  }

  function hdrs() {
    return Object.assign({ 'Content-Type': 'application/json' }, typeof authHeaders === 'function' ? authHeaders() : {});
  }

  function msg(text, bad) {
    var el = document.getElementById('agentTaskMsg');
    if (!el) return;
    el.style.display = text ? '' : 'none';
    el.style.color = bad ? '#e74c3c' : '#2f9e66';
    el.textContent = text || '';
  }

  function selectedDevices() {
    var el = document.getElementById('agentTaskDevices');
    if (!el) return [];
    return Array.prototype.slice.call(el.selectedOptions || []).map(function (o) { return o.value; }).filter(Boolean);
  }

  function parsePayload(raw) {
    raw = (raw || '').trim();
    if (!raw) return {};
    var obj = JSON.parse(raw);
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) throw new Error('JSON 必须是对象');
    return obj;
  }

  function togglePayload() {
    var kind = (document.getElementById('agentTaskKind') || {}).value || 'openclaw_message';
    var payloadWrap = document.getElementById('agentTaskPayloadWrap');
    var contentWrap = document.getElementById('agentTaskContentWrap');
    if (payloadWrap) payloadWrap.style.display = kind === 'capability' ? '' : 'none';
    if (contentWrap) contentWrap.style.display = kind === 'capability' ? 'none' : '';
  }

  function loadDevices() {
    var b = base();
    var userSelect = document.getElementById('agentTaskUserSelect');
    var sel = document.getElementById('agentTaskDevices');
    if (!b || !userSelect || !sel) return;
    var userId = userSelect.value;
    if (!userId) {
      sel.innerHTML = '<option value="">请选择下级用户</option>';
      return;
    }
    sel.innerHTML = '<option value="">加载中...</option>';
    fetch(b + '/api/scheduled-tasks/agent/devices?user_id=' + encodeURIComponent(userId), { headers: authHeaders() })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) {
          if (!r.ok) throw new Error((d && d.detail) || ('HTTP ' + r.status));
          return d;
        });
      })
      .then(function (d) {
        var devices = d.devices || [];
        if (!devices.length) {
          sel.innerHTML = '<option value="">暂无设备，可不选使用任意设备</option>';
          return;
        }
        sel.innerHTML = devices.map(function (x) {
          var label = (x.display_name || x.installation_id || '-') + (x.online ? ' · 在线' : ' · 离线');
          return '<option value="' + h(x.installation_id || '') + '">' + h(label) + '</option>';
        }).join('');
      })
      .catch(function (e) {
        sel.innerHTML = '<option value="">设备加载失败</option>';
        msg(e.message, true);
      });
  }

  function loadAgentTaskPanel() {
    var b = base();
    var card = document.getElementById('agentTaskDispatchCard');
    var userSelect = document.getElementById('agentTaskUserSelect');
    if (!b || !card || !userSelect) return;
    fetch(b + '/auth/agent/sub-users', { headers: authHeaders() })
      .then(function (r) {
        if (r.status === 403) return { sub_users: [], count: 0, agent_task_dispatch_enabled: false };
        return r.json().catch(function () { return {}; }).then(function (d) {
          if (!r.ok) throw new Error((d && d.detail) || ('HTTP ' + r.status));
          return d;
        });
      })
      .then(function (d) {
        if (!d.agent_task_dispatch_enabled) {
          card.style.display = 'none';
          return;
        }
        card.style.display = '';
        var list = d.sub_users || [];
        if (!list.length) {
          userSelect.innerHTML = '<option value="">暂无下级用户</option>';
          loadDevices();
          return;
        }
        userSelect.innerHTML = list.map(function (u) {
          var display = (u.email || ('#' + u.id)).replace(/@sms\.lobster\.local$/, '');
          return '<option value="' + u.id + '">' + h(display) + ' (#' + u.id + ')</option>';
        }).join('');
        loadDevices();
      })
      .catch(function (e) {
        card.style.display = 'none';
        msg(e.message, true);
      });
  }

  function buildBody() {
    var kind = (document.getElementById('agentTaskKind') || {}).value || 'openclaw_message';
    var scheduleType = (document.getElementById('agentTaskScheduleType') || {}).value || 'once';
    var intervalMin = parseInt((document.getElementById('agentTaskIntervalMinutes') || {}).value || '60', 10);
    var body = {
      user_id: parseInt((document.getElementById('agentTaskUserSelect') || {}).value || '0', 10),
      title: ((document.getElementById('agentTaskTitle') || {}).value || '').trim(),
      task_kind: kind,
      content: ((document.getElementById('agentTaskContent') || {}).value || '').trim(),
      payload: {},
      schedule_type: scheduleType,
      interval_seconds: Math.max(60, (isNaN(intervalMin) ? 60 : intervalMin) * 60),
      installation_ids: selectedDevices()
    };
    if (!body.user_id) throw new Error('请选择下级用户');
    if (kind === 'capability') {
      var parsed = parsePayload((document.getElementById('agentTaskPayload') || {}).value || '');
      body.payload = parsed.capability_id ? parsed : { capability_id: parsed.capability || parsed.id || '', payload: parsed.payload || parsed };
      body.content = body.content || ('调用能力 ' + (body.payload.capability_id || ''));
    }
    return body;
  }

  function submitTask() {
    var b = base();
    var btn = document.getElementById('agentTaskSubmitBtn');
    var body;
    try {
      body = buildBody();
    } catch (e) {
      msg(e.message, true);
      return;
    }
    if (btn) { btn.disabled = true; btn.textContent = '下发中...'; }
    msg('', false);
    fetch(b + '/api/scheduled-tasks/agent/tasks', {
      method: 'POST',
      headers: hdrs(),
      body: JSON.stringify(body)
    })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) {
          if (!r.ok) throw new Error((d && d.detail) || ('HTTP ' + r.status));
          return d;
        });
      })
      .then(function () { msg('已下发任务', false); })
      .catch(function (e) { msg(e.message, true); })
      .finally(function () {
        if (btn) { btn.disabled = false; btn.textContent = '下发任务'; }
      });
  }

  var userSelect = document.getElementById('agentTaskUserSelect');
  if (userSelect) userSelect.addEventListener('change', loadDevices);
  var kind = document.getElementById('agentTaskKind');
  if (kind) kind.addEventListener('change', togglePayload);
  var submit = document.getElementById('agentTaskSubmitBtn');
  if (submit) submit.addEventListener('click', submitTask);
  var oldLoad = window.loadAgentSubUsers;
  window.loadAgentSubUsers = function () {
    if (typeof oldLoad === 'function') oldLoad();
    loadAgentTaskPanel();
  };
  togglePayload();
})();
