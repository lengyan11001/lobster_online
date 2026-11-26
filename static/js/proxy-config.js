(function() {
  function base() {
    return (typeof LOCAL_API_BASE !== 'undefined' ? LOCAL_API_BASE : '') || '';
  }
  function api(path, opts) {
    var headers = Object.assign({ 'Content-Type': 'application/json' }, typeof authHeaders === 'function' ? authHeaders() : {});
    return fetch((base() || '') + path, Object.assign({ headers: headers }, opts || {}));
  }
  function byId(id) { return document.getElementById(id); }
  function statusMeta(st) {
    if (st === 'ok') return { text: '通畅', color: '#34d399' };
    if (st === 'fail') return { text: '不通', color: '#f87171' };
    return { text: '未检测', color: '#94a3b8' };
  }
  var cache = [];

  function render(items) {
    cache = items || [];
    var box = byId('proxyList');
    if (!box) return;
    if (!cache.length) {
      box.innerHTML = '<p class="meta">还没有代理，点「添加代理」或「批量导入」。</p>';
      return;
    }
    box.innerHTML = cache.map(function(p) {
      var st = statusMeta(p.status);
      var latency = p.latency_ms != null ? (p.latency_ms + 'ms') : '—';
      return '<div style="display:flex;gap:0.6rem;align-items:center;padding:0.55rem 0;border-bottom:1px solid var(--border);">' +
        '<span style="width:8px;height:8px;border-radius:50%;background:' + st.color + ';flex:0 0 8px;"></span>' +
        '<div style="flex:1;min-width:0;">' +
        '<div style="font-weight:600;font-size:0.85rem;">' + escapeHtml(p.label || p.server) + '</div>' +
        '<div class="meta" style="font-size:0.75rem;word-break:break-all;">' + escapeHtml(p.server) +
        ' · ' + st.text + ' · ' + latency + ' · 已用 ' + (p.used_by_count || 0) + ' 个账号' +
        (p.last_error ? ' · <span style="color:#f87171;">' + escapeHtml(p.last_error) + '</span>' : '') +
        '</div></div>' +
        '<button type="button" class="btn btn-ghost btn-sm" data-proxy-test="' + p.id + '">测试</button>' +
        '<button type="button" class="btn btn-ghost btn-sm" data-proxy-del="' + p.id + '">删除</button>' +
        '</div>';
    }).join('');
    box.querySelectorAll('[data-proxy-test]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        btn.disabled = true; btn.textContent = '测试中…';
        api('/api/proxies/' + btn.getAttribute('data-proxy-test') + '/test', { method: 'POST' })
          .then(function(r) { return r.json(); }).then(function() { load(); })
          .catch(function() { btn.disabled = false; btn.textContent = '测试'; });
      });
    });
    box.querySelectorAll('[data-proxy-del]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        if (!confirm('删除这个代理？已配置到账号上的代理不会被自动清除。')) return;
        api('/api/proxies/' + btn.getAttribute('data-proxy-del'), { method: 'DELETE' })
          .then(function() { load(); });
      });
    });
  }

  function load() {
    api('/api/proxies').then(function(r) { return r.json(); }).then(function(d) {
      render(d.items || []);
    }).catch(function(e) {
      var box = byId('proxyList');
      if (box) box.innerHTML = '<p class="msg err">加载失败：' + escapeHtml(e.message || e) + '</p>';
    });
  }

  function showBox(which) {
    var add = byId('proxyAddBox');
    var imp = byId('proxyImportBox');
    if (add) add.style.display = which === 'add' ? 'block' : 'none';
    if (imp) imp.style.display = which === 'import' ? 'block' : 'none';
  }

  function open() {
    var m = byId('proxyConfigModal');
    if (m) m.style.display = 'flex';
    load();
  }

  function bind() {
    var entry = byId('proxyConfigEntry');
    if (entry) entry.addEventListener('click', function(e) {
      e.preventDefault();
      open();
    });
    var close = byId('proxyConfigClose');
    if (close) close.addEventListener('click', function() { byId('proxyConfigModal').style.display = 'none'; });
    var mask = byId('proxyConfigModal');
    if (mask) mask.addEventListener('click', function(e) { if (e.target === mask) mask.style.display = 'none'; });
    var addToggle = byId('proxyAddToggle');
    if (addToggle) addToggle.addEventListener('click', function() { showBox('add'); });
    var impToggle = byId('proxyImportToggle');
    if (impToggle) impToggle.addEventListener('click', function() { showBox('import'); });
    var refresh = byId('proxyRefreshBtn');
    if (refresh) refresh.addEventListener('click', load);

    var save = byId('proxyAddSave');
    if (save) save.addEventListener('click', function() {
      var msg = byId('proxyAddMsg');
      var body = {
        label: (byId('proxyAddLabel').value || '').trim(),
        server: (byId('proxyAddServer').value || '').trim(),
        username: (byId('proxyAddUser').value || '').trim(),
        password: byId('proxyAddPass').value || ''
      };
      if (!body.server) { if (msg) msg.textContent = '请填写代理地址'; return; }
      save.disabled = true;
      api('/api/proxies', { method: 'POST', body: JSON.stringify(body) })
        .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, d: d }; }); })
        .then(function(x) {
          if (!x.ok) { if (msg) msg.textContent = (x.d && x.d.detail) || '保存失败'; return; }
          if (msg) msg.textContent = '已保存';
          byId('proxyAddServer').value = '';
          byId('proxyAddUser').value = '';
          byId('proxyAddPass').value = '';
          byId('proxyAddLabel').value = '';
          showBox('');
          load();
        })
        .finally(function() { save.disabled = false; });
    });

    var impSave = byId('proxyImportSave');
    if (impSave) impSave.addEventListener('click', function() {
      var msg = byId('proxyImportMsg');
      var text = byId('proxyImportText').value || '';
      if (!text.trim()) { if (msg) msg.textContent = '请粘贴代理'; return; }
      impSave.disabled = true;
      api('/api/proxies/import', { method: 'POST', body: JSON.stringify({ text: text }) })
        .then(function(r) { return r.json(); }).then(function(d) {
          if (msg) msg.textContent = '导入 ' + (d.imported || 0) + ' 条，跳过 ' + (d.skipped || 0) + ' 条';
          byId('proxyImportText').value = '';
          load();
        }).finally(function() { impSave.disabled = false; });
    });
  }

  window.openProxyConfig = open;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
  else bind();
})();
