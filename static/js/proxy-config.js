(function() {
  var cache = [];
  var initialized = false;

  function base() {
    return (typeof LOCAL_API_BASE !== 'undefined' ? LOCAL_API_BASE : '') || '';
  }
  function api(path, opts) {
    var headers = Object.assign({ 'Content-Type': 'application/json' }, typeof authHeaders === 'function' ? authHeaders() : {});
    return fetch((base() || '') + path, Object.assign({ headers: headers }, opts || {}));
  }
  function byId(id) { return document.getElementById(id); }
  function msg(text) {
    var el = byId('proxyPageMsg');
    if (el) el.textContent = text || '';
  }
  function statusMeta(st) {
    if (st === 'ok') return { text: '通畅', color: '#34d399' };
    if (st === 'fail') return { text: '不通', color: '#f87171' };
    return { text: '未检测', color: '#94a3b8' };
  }
  function maskUser(u) {
    u = String(u || '');
    if (!u) return '无认证';
    if (u.length <= 3) return u + '***';
    return u.slice(0, 2) + '***' + u.slice(-1);
  }

  function gradeColor(g) {
    if (g === 'A') return '#34d399';
    if (g === 'B') return '#60a5fa';
    if (g === 'C') return '#fb923c';
    return '#f87171';
  }

  function ipTypeMeta(t) {
    if (t === 'datacenter') return { label: '机房 IP', color: '#fb923c' };
    if (t === 'residential') return { label: '住宅 IP', color: '#34d399' };
    if (t === 'mobile') return { label: '移动 IP', color: '#34d399' };
    if (t === 'proxy') return { label: '代理/VPN', color: '#fb923c' };
    return { label: 'IP 类型未知', color: '#94a3b8' };
  }

  function updateSummary(items) {
    var el = byId('proxyPageSummary');
    if (!el) return;
    var ok = items.filter(function(x) { return x.status === 'ok'; }).length;
    var high = items.filter(function(x) { return x.risk_level === 'high'; }).length;
    var dc = items.filter(function(x) { return x.ip_type === 'datacenter'; }).length;
    var avg = items.length ? Math.round(items.reduce(function(s, x) { return s + Number(x.score || 0); }, 0) / items.length) : 0;
    el.innerHTML = '<span>共 <strong>' + items.length + '</strong></span>' +
      '<span style="color:#34d399;">通畅 <strong>' + ok + '</strong></span>' +
      '<span style="color:#f87171;">高风险 <strong>' + high + '</strong></span>' +
      '<span style="color:#fb923c;">机房 IP <strong>' + dc + '</strong></span>' +
      '<span>平均分 <strong>' + avg + '</strong></span>';
  }

  function render(items) {
    cache = items || [];
    updateSummary(cache);
    var box = byId('proxyList');
    if (!box) return;
    if (!cache.length) {
      box.innerHTML = '<div class="page-empty-card" style="text-align:center;padding:2rem;">还没有代理，点右上角「+ 添加代理」。</div>';
      return;
    }
    box.innerHTML = cache.map(function(p) {
      var st = statusMeta(p.status);
      var latency = p.latency_ms != null ? (p.latency_ms + 'ms') : '—';
      var gc = gradeColor(p.grade || 'D');
      var reasons = (p.score_reasons || []).join('；');
      var err = p.last_error ? ('<div class="meta" style="font-size:0.72rem;color:#f87171;word-break:break-all;">' + escapeHtml(p.last_error) + '</div>') : '';
      var risk = p.risk_level === 'high' ? '<span style="color:#f87171;">高风险</span>' : (p.risk_level === 'medium' ? '<span style="color:#fb923c;">中风险</span>' : '<span style="color:#34d399;">低风险</span>');
      var tm = ipTypeMeta(p.ip_type);
      var ipLine = '<span style="color:' + tm.color + ';">' + tm.label + '</span>' + (p.org ? (' · ' + escapeHtml(p.org)) : '') + (p.asn ? (' · ' + escapeHtml(p.asn)) : '');
      return '<div style="display:flex;gap:0.7rem;align-items:center;padding:0.65rem 0.2rem;border-bottom:1px solid var(--border);">' +
        '<span style="width:8px;height:8px;border-radius:50%;background:' + st.color + ';flex:0 0 8px;"></span>' +
        '<div style="flex:1;min-width:0;">' +
        '<div style="font-weight:600;font-size:0.86rem;">' + escapeHtml(p.label || p.server) + '</div>' +
        '<div class="meta" style="font-size:0.75rem;word-break:break-all;">' + escapeHtml(p.server) +
        ' · ' + escapeHtml(maskUser(p.username)) +
        ' · ' + st.text + ' · ' + latency +
        ' · 已用 ' + (p.used_by_count || 0) + ' 个账号</div>' +
        '<div style="font-size:0.75rem;margin-top:0.15rem;">' + ipLine + '</div>' +
        '<div style="font-size:0.75rem;margin-top:0.2rem;color:' + gc + ';" title="' + escapeHtml(reasons) + '">' +
        '<strong>' + escapeHtml(p.grade || '-') + ' · ' + Number(p.score || 0) + ' 分</strong>' +
        ' · 建议：' + escapeHtml(p.recommendation || '-') + ' · ' + risk + '</div>' + err + '</div>' +
        '<button type="button" class="btn btn-ghost btn-sm" data-proxy-test="' + p.id + '">测试</button>' +
        '<button type="button" class="btn btn-ghost btn-sm" data-proxy-del="' + p.id + '">删除</button>' +
        '</div>';
    }).join('');
    box.querySelectorAll('[data-proxy-test]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        btn.disabled = true; btn.textContent = '测试中…';
        api('/api/proxies/' + btn.getAttribute('data-proxy-test') + '/test', { method: 'POST' })
          .then(function(r) { return r.json(); })
          .then(function() { load(); })
          .catch(function() { btn.disabled = false; btn.textContent = '测试'; });
      });
    });
    box.querySelectorAll('[data-proxy-del]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        if (!confirm('删除这个代理？已配置到账号上的代理不会被自动清除。')) return;
        api('/api/proxies/' + btn.getAttribute('data-proxy-del'), { method: 'DELETE' }).then(function() { load(); });
      });
    });
  }

  function load() {
    return api('/api/proxies').then(function(r) { return r.json(); }).then(function(d) {
      render(d.items || []);
    }).catch(function(e) {
      var box = byId('proxyList');
      if (box) box.innerHTML = '<p class="msg err">加载失败：' + escapeHtml(e.message || e) + '</p>';
    });
  }

  function testAll() {
    var btn = byId('proxyPageTestAll');
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = '测试中…';
    msg('正在批量测试 ' + cache.length + ' 条代理…');
    api('/api/proxies/test-all', { method: 'POST' })
      .then(function(r) {
        if (!r.ok) return r.text().then(function(t) { throw new Error('HTTP ' + r.status + ' ' + String(t || '').slice(0, 120)); });
        return r.json();
      })
      .then(function(d) {
        var items = (d && d.items) || [];
        if (!items.length && cache.length) {
          msg('批量测试返回为空，请确认后端已升级并重启');
          load();
          return;
        }
        render(items);
        var ok = items.filter(function(x) { return x.status === 'ok'; }).length;
        var avg = items.length ? Math.round(items.reduce(function(s, x) { return s + Number(x.score || 0); }, 0) / items.length) : 0;
        msg('批量测试完成：通畅 ' + ok + ' / ' + items.length + ' · 平均分 ' + avg);
      })
      .catch(function(e) { msg('批量测试失败：' + (e.message || e)); })
      .finally(function() { btn.disabled = false; btn.textContent = '批量测试'; });
  }

  function showTab(which) {
    var single = byId('proxyAddSingle');
    var batch = byId('proxyAddBatch');
    if (single) single.style.display = which === 'single' ? '' : 'none';
    if (batch) batch.style.display = which === 'batch' ? '' : 'none';
  }

  function openAdd() {
    var m = byId('proxyAddModal');
    if (m) m.style.display = 'flex';
    showTab('single');
    var msgEl = byId('proxyAddMsg'); if (msgEl) msgEl.textContent = '';
    var imp = byId('proxyImportMsg'); if (imp) imp.textContent = '';
  }

  function bind() {
    var refresh = byId('proxyPageRefresh');
    if (refresh) refresh.addEventListener('click', load);
    var testBtn = byId('proxyPageTestAll');
    if (testBtn) testBtn.addEventListener('click', testAll);
    var addBtn = byId('proxyPageAdd');
    if (addBtn) addBtn.addEventListener('click', openAdd);
    var close = byId('proxyAddModalClose');
    if (close) close.addEventListener('click', function() { byId('proxyAddModal').style.display = 'none'; });
    var mask = byId('proxyAddModal');
    if (mask) mask.addEventListener('click', function(e) { if (e.target === mask) mask.style.display = 'none'; });
    var tabSingle = byId('proxyAddTabSingle');
    if (tabSingle) tabSingle.addEventListener('click', function() { showTab('single'); });
    var tabBatch = byId('proxyAddTabBatch');
    if (tabBatch) tabBatch.addEventListener('click', function() { showTab('batch'); });

    var save = byId('proxyAddSave');
    if (save) save.addEventListener('click', function() {
      var m = byId('proxyAddMsg');
      var body = {
        label: (byId('proxyAddLabel').value || '').trim(),
        server: (byId('proxyAddServer').value || '').trim(),
        username: (byId('proxyAddUser').value || '').trim(),
        password: byId('proxyAddPass').value || ''
      };
      if (!body.server) { if (m) m.textContent = '请填写代理地址'; return; }
      save.disabled = true;
      api('/api/proxies', { method: 'POST', body: JSON.stringify(body) })
        .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, d: d }; }); })
        .then(function(x) {
          if (!x.ok) { if (m) m.textContent = (x.d && x.d.detail) || '保存失败'; return; }
          if (m) m.textContent = '已保存';
          ['proxyAddLabel','proxyAddServer','proxyAddUser','proxyAddPass'].forEach(function(id) { var el = byId(id); if (el) el.value = ''; });
          load();
        })
        .finally(function() { save.disabled = false; });
    });

    var impSave = byId('proxyImportSave');
    if (impSave) impSave.addEventListener('click', function() {
      var m = byId('proxyImportMsg');
      var text = byId('proxyImportText').value || '';
      if (!text.trim()) { if (m) m.textContent = '请粘贴代理'; return; }
      impSave.disabled = true;
      api('/api/proxies/import', { method: 'POST', body: JSON.stringify({ text: text }) })
        .then(function(r) { return r.json(); })
        .then(function(d) {
          if (m) m.textContent = '导入 ' + (d.imported || 0) + ' 条，跳过 ' + (d.skipped || 0) + ' 条';
          byId('proxyImportText').value = '';
          load();
        })
        .finally(function() { impSave.disabled = false; });
    });
  }

  window.initProxyConfigView = function() {
    if (!initialized) { bind(); initialized = true; }
    load();
  };
})();
