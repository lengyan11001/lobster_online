function logsApiBase() {
  var lb = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? String(LOCAL_API_BASE).replace(/\/$/, '') : '';
  if (lb) return lb;
  return (typeof API_BASE !== 'undefined' ? String(API_BASE) : '').replace(/\/$/, '');
}

function ensureLogsBindings() {
  var refreshBtn = document.getElementById('logsRefreshBtn');
  var loadBtn = document.getElementById('logsLoadBtn');
  var exportBtn = document.getElementById('logsExportBtn');
  var uploadDiagnosticBtn = document.getElementById('logsUploadDiagnosticBtn');
  var tailEl = document.getElementById('logsTail');
  if (refreshBtn && !refreshBtn._logsBound) {
    refreshBtn._logsBound = true;
    refreshBtn.onclick = loadLogsView;
  }
  if (loadBtn && !loadBtn._logsBound) {
    loadBtn._logsBound = true;
    loadBtn.onclick = loadLogsView;
  }
  if (exportBtn && !exportBtn._logsBound) {
    exportBtn._logsBound = true;
    exportBtn.onclick = exportLogsView;
  }
  if (uploadDiagnosticBtn && !uploadDiagnosticBtn._logsBound) {
    uploadDiagnosticBtn._logsBound = true;
    uploadDiagnosticBtn.onclick = uploadDiagnosticLogs;
  }
  if (tailEl && !tailEl._logsBound) {
    tailEl._logsBound = true;
    tailEl.addEventListener('change', loadLogsView);
  }
}

function uploadDiagnosticLogs() {
  var btn = document.getElementById('logsUploadDiagnosticBtn');
  var msgEl = document.getElementById('logsDiagnosticUploadMsg');
  var pre = document.getElementById('logsContent');
  var base = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? String(LOCAL_API_BASE).replace(/\/$/, '') : '';
  if (!base) {
    if (msgEl) {
      msgEl.style.display = 'block';
      msgEl.style.color = '#d14343';
      msgEl.textContent = '上传诊断需要连接本机 lobster_online 后端，请先用 start.bat 启动并通过本机地址打开。';
    }
    return;
  }
  var url = base + '/api/logs/upload-diagnostics';
  if (msgEl) {
    msgEl.style.display = 'block';
    msgEl.style.color = 'var(--text-muted)';
    msgEl.textContent = '正在打包并上传诊断日志...';
  }
  if (btn) {
    btn.disabled = true;
    btn.textContent = '上传中...';
  }
  var opts = {
    method: 'POST',
    credentials: 'same-origin',
    headers: typeof authHeaders === 'function' ? authHeaders() : { 'Authorization': 'Bearer ' + (typeof token !== 'undefined' ? token : '') }
  };
  fetch(url, opts)
    .then(function(r) {
      return r.json().catch(function() { return {}; }).then(function(d) {
        return { ok: r.ok, status: r.status, data: d };
      });
    })
    .then(function(x) {
      if (!x.ok) {
        var detail = (x.data && (x.data.detail || x.data.message)) || ('HTTP ' + x.status);
        throw new Error(detail);
      }
      var id = (x.data && x.data.diagnostic_id) || '';
      var size = (x.data && x.data.bundle && x.data.bundle.size) ? Math.round(x.data.bundle.size / 1024) : 0;
      var text = '诊断日志已上传' + (id ? '，诊断ID：' + id : '') + (size ? '，大小约 ' + size + ' KB' : '');
      if (msgEl) {
        msgEl.style.color = '#0f9f6e';
        msgEl.textContent = text;
      }
      if (pre && id) {
        pre.textContent = text + '\n\n' + (pre.textContent || '');
      }
    })
    .catch(function(e) {
      var msg = (e && e.message) ? e.message : String(e);
      if (msgEl) {
        msgEl.style.color = '#d14343';
        msgEl.textContent = '上传诊断失败：' + msg;
      }
    })
    .finally(function() {
      if (btn) {
        btn.disabled = false;
        btn.textContent = '上传诊断';
      }
    });
}

function exportLogsView() {
  var btn = document.getElementById('logsExportBtn');
  var tailEl = document.getElementById('logsTail');
  var tail = (tailEl && tailEl.value) ? parseInt(tailEl.value, 10) : 2000;
  var base = logsApiBase();
  var url = (base ? base : '') + '/api/logs?tail=' + tail;
  if (btn) btn.disabled = true;
  var opts = {
    method: 'GET',
    credentials: 'same-origin',
    headers: typeof authHeaders === 'function' ? authHeaders() : { 'Authorization': 'Bearer ' + (typeof token !== 'undefined' ? token : '') }
  };
  fetch(url, opts)
    .then(function(r) {
      if (!r.ok) return r.text().then(function(txt) { throw new Error((txt || '').slice(0, 400) || String(r.status)); });
      return r.text();
    })
    .then(function(text) {
      var d = new Date();
      var pad = function(n) { return n < 10 ? '0' + n : String(n); };
      var fname = 'lobster-app-log-' + d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) + '-' + pad(d.getHours()) + pad(d.getMinutes()) + pad(d.getSeconds()) + '.txt';
      var blob = new Blob([text != null ? text : ''], { type: 'text/plain;charset=utf-8' });
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = fname;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(a.href);
    })
    .catch(function(e) {
      var msg = (e && e.message) ? e.message : String(e);
      if (typeof alert !== 'undefined') alert('导出失败：' + msg);
    })
    .finally(function() { if (btn) btn.disabled = false; });
}

function loadLogsView() {
  var pre = document.getElementById('logsContent');
  var tailEl = document.getElementById('logsTail');
  if (!pre) {
    if (typeof console !== 'undefined') console.warn('[日志] #logsContent 未找到');
    return;
  }
  var tail = (tailEl && tailEl.value) ? parseInt(tailEl.value, 10) : 2000;
  pre.textContent = '加载中…';
  var base = logsApiBase();
  var url = (base ? base : '') + '/api/logs?tail=' + tail;
  var timeout = 20000;
  var ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
  var t = ctrl ? setTimeout(function() { if (ctrl) ctrl.abort(); }, timeout) : null;
  var opts = {
    method: 'GET',
    credentials: 'same-origin',
    headers: typeof authHeaders === 'function' ? authHeaders() : { 'Authorization': 'Bearer ' + (typeof token !== 'undefined' ? token : '') }
  };
  if (ctrl) opts.signal = ctrl.signal;
  fetch(url, opts)
    .then(function(r) {
      if (t) clearTimeout(t);
      if (!r.ok) return r.text().then(function(txt) { throw new Error(txt || r.status); });
      return r.text();
    })
    .then(function(text) {
      pre.textContent = text || '(空)';
      pre.scrollTop = pre.scrollHeight;
    })
    .catch(function(e) {
      if (t) clearTimeout(t);
      var msg = (e && e.name === 'AbortError') ? '加载超时，请重试' : (e && e.message ? e.message : String(e));
      pre.textContent = '加载失败: ' + msg;
    });
  ensureLogsBindings();
}

