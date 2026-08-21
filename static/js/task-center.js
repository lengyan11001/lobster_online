(function () {
  'use strict';

  var state = { rows: [], open: false, seenActive: {}, polling: false };

  function esc(value) {
    var text = String(value == null ? '' : value);
    return text.replace(/[&<>"']/g, function (character) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character];
    });
  }

  function base() {
    if (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) {
      return String(LOCAL_API_BASE).replace(/\/$/, '');
    }
    return typeof API_BASE !== 'undefined' ? String(API_BASE || '').replace(/\/$/, '') : '';
  }

  function headers() {
    var result = typeof authHeaders === 'function' ? authHeaders() : {};
    if (typeof getOrCreateInstallationId === 'function') {
      result['X-Installation-Id'] = getOrCreateInstallationId();
    }
    return result;
  }

  function currentInstallationId() {
    return typeof getOrCreateInstallationId === 'function'
      ? String(getOrCreateInstallationId() || '').trim()
      : '';
  }

  function belongsToCurrentDevice(row) {
    var iid = currentInstallationId();
    if (!iid) return true;
    return String(row.installation_id || '').trim() === iid
      || String(row.claimed_by_installation_id || '').trim() === iid;
  }

  function active(row) {
    return ['pending', 'processing', 'running', 'claimed', 'queued'].indexOf(String(row.status || '').toLowerCase()) >= 0;
  }

  function taskTitle(row) {
    return row.title || row.task_title || row.name || '正在执行的任务';
  }

  function taskMessage(row) {
    var progress = row.progress || row.result_payload || {};
    return row.message || row.status_text || progress.message || progress.text || row.last_error
      || (active(row) ? '正在处理，请保持设备在线' : '任务已结束');
  }

  function taskPercent(row) {
    var progress = row.progress || row.result_payload || {};
    var total = Number(row.total || progress.total || 0);
    var done = Number(row.processed || progress.processed || progress.current || 0);
    if (total > 0) return Math.max(3, Math.min(100, Math.round(done * 100 / total)));
    return active(row) ? 32 : 100;
  }

  function mount() {
    if (document.getElementById('lobsterTaskFab')) return;

    var root = document.createElement('div');
    root.innerHTML =
      '<button id="lobsterTaskFab" class="lobster-task-fab" type="button" aria-label="执行任务" title="执行任务">' +
        '<svg viewBox="0 0 24 24" aria-hidden="true">' +
          '<path d="M5 6h9M5 12h7M5 18h8"></path>' +
          '<path d="m17 10 3 2-3 2z"></path>' +
        '</svg><i></i>' +
      '</button>' +
      '<section id="lobsterTaskPanel" class="lobster-task-panel" hidden>' +
        '<button class="lobster-task-close" type="button" aria-label="关闭">&times;</button>' +
        '<div id="lobsterTaskList" class="lobster-task-list"></div>' +
      '</section>';

    document.body.appendChild(root);
    root.querySelector('#lobsterTaskFab').onclick = function () {
      state.open = !state.open;
      render();
    };
    root.querySelector('.lobster-task-close').onclick = function () {
      state.open = false;
      render();
    };
    root.querySelector('#lobsterTaskList').onclick = stop;
  }

  function render() {
    var panel = document.getElementById('lobsterTaskPanel');
    var fab = document.getElementById('lobsterTaskFab');
    var list = document.getElementById('lobsterTaskList');
    if (!panel || !fab || !list) return;

    var running = state.rows.filter(active);
    fab.classList.toggle('is-active', !!running.length);
    panel.hidden = !state.open;
    list.innerHTML = running.length
      ? running.slice(0, 5).map(function (row) {
          var percent = taskPercent(row);
          return '<article class="lobster-task-card" style="--task-progress:' + percent + '%">' +
            '<div class="lobster-task-row">' +
              '<div class="lobster-task-copy">' +
                '<div class="lobster-task-title">' + esc(taskTitle(row)) + '</div>' +
                '<div class="lobster-task-message">' + esc(taskMessage(row)) + '</div>' +
                '<div class="lobster-task-meta">执行中 · ' + percent + '%</div>' +
              '</div>' +
              (row.id ? '<button class="lobster-task-stop" data-run-id="' + esc(row.id) + '" type="button">停止</button>' : '') +
            '</div>' +
            '<div class="lobster-task-track"><i></i></div>' +
          '</article>';
        }).join('')
      : '<div class="lobster-task-empty">当前没有正在执行的任务</div>';
  }

  async function refresh() {
    if (state.polling) return;
    state.polling = true;
    try {
      var iid = currentInstallationId();
      var query = '/api/scheduled-tasks/runs?limit=40';
      if (iid) query += '&installation_id=' + encodeURIComponent(iid);
      var response = await fetch(base() + query, { headers: headers() });
      if (!response.ok) throw new Error();

      var data = await response.json();
      var rows = (Array.isArray(data.runs) ? data.runs : []).filter(belongsToCurrentDevice);
      var running = rows.filter(active);
      var newActive = running.some(function (row) {
        return row.id && !state.seenActive[row.id];
      });

      state.seenActive = {};
      running.forEach(function (row) {
        if (row.id) state.seenActive[row.id] = true;
      });
      state.rows = rows;
      if (newActive) state.open = true;
      render();
    } catch (_) {
      // The host page keeps working when the scheduling API is unavailable.
    } finally {
      state.polling = false;
    }
  }

  async function stop(event) {
    var button = event.target.closest('[data-run-id]');
    if (!button) return;

    var id = button.getAttribute('data-run-id');
    button.disabled = true;
    button.textContent = '停止中';
    try {
      var response = await fetch(
        base() + '/api/scheduled-tasks/runs/' + encodeURIComponent(id),
        { method: 'DELETE', headers: headers() }
      );
      if (!response.ok) throw new Error();
      await refresh();
    } catch (_) {
      button.disabled = false;
      button.textContent = '停止失败';
    }
  }

  function start() {
    mount();
    refresh();
    setInterval(refresh, 4000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
