/* Online 教程 → 系统模板：查看 + 下载 */
(function () {
  var loadedOnce = false;

  function apiBase() {
    try {
      if (typeof API_BASE !== 'undefined' && API_BASE) return String(API_BASE).replace(/\/$/, '');
      if (window.__API_BASE) return String(window.__API_BASE).replace(/\/$/, '');
    } catch (e) {}
    return '';
  }

  function headers() {
    var h = { 'Accept': 'application/json' };
    try {
      if (typeof authHeaders === 'function') {
        var ah = authHeaders();
        if (ah && ah.Authorization) h.Authorization = ah.Authorization;
        if (ah && ah['X-Installation-Id']) h['X-Installation-Id'] = ah['X-Installation-Id'];
        if (ah && ah['X-Lobster-Brand']) h['X-Lobster-Brand'] = ah['X-Lobster-Brand'];
      }
    } catch (e) {}
    return h;
  }

  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function shortText(v, n) {
    var t = String(v || '').replace(/\s+/g, ' ').trim();
    if (!t) return '（未填写）';
    return t.length > n ? t.slice(0, n) + '…' : t;
  }

  async function downloadTemplate(id, fallbackName) {
    var base = apiBase();
    if (!base) { alert('未配置服务器地址，无法下载'); return; }
    try {
      var res = await fetch(base + '/api/ip-content/system-templates/' + id + '/download', { headers: headers() });
      if (!res.ok) {
        var detail = '下载失败';
        try { detail = (await res.json()).detail || detail; } catch (e) {}
        throw new Error(detail);
      }
      var blob = await res.blob();
      var disp = res.headers.get('Content-Disposition') || '';
      var name = (fallbackName || ('system-template-' + id)) + '.txt';
      var m = /filename\*=UTF-8''([^;]+)/i.exec(disp);
      if (m) { try { name = decodeURIComponent(m[1]); } catch (e) {} }
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url; a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
    } catch (e) {
      alert('下载失败：' + (e && e.message ? e.message : e));
    }
  }

  window.downloadTutorialSystemTemplate = downloadTemplate;

  window.initTutorialView = async function () {
    var host = document.getElementById('tutorialSystemTemplates');
    if (!host) return;
    var hint = document.getElementById('tutorialSystemTemplatesHint');
    var refresh = document.getElementById('tutorialSystemTemplatesRefresh');
    if (refresh && !refresh.dataset.bound) {
      refresh.dataset.bound = '1';
      refresh.addEventListener('click', function () { loadedOnce = false; window.initTutorialView(); });
    }
    var base = apiBase();
    if (!base) {
      host.innerHTML = '<div class="empty">未配置服务器地址</div>';
      if (hint) hint.textContent = '';
      return;
    }
    if (loadedOnce && host.dataset.loaded === '1') return;
    host.innerHTML = '<div class="empty">加载中…</div>';
    try {
      var res = await fetch(base + '/api/ip-content/system-templates', { headers: headers() });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      var data = await res.json();
      var items = (data && data.items) || [];
      loadedOnce = true;
      host.dataset.loaded = '1';
      if (hint) hint.textContent = '共 ' + items.length + ' 个系统模板';
      if (!items.length) {
        host.innerHTML = '<div class="empty">暂无系统模板</div>';
        return;
      }
      host.innerHTML = items.map(function (it, idx) {
        return '<article class="app-tutorial-template-card">'
          + '<div class="app-tutorial-template-head"><strong>' + esc(it.name || ('模板 #' + it.id)) + '</strong>'
          + '<span class="app-tutorial-hint">' + esc(it.updated_at ? new Date(it.updated_at).toLocaleString('zh-CN') : '') + '</span></div>'
          + '<div class="app-tutorial-template-body">'
          + '<div><b>口播要求</b><p>' + esc(shortText(it.oral, 160)) + '</p></div>'
          + '<div><b>朋友圈文案</b><p>' + esc(shortText(it.moments, 160)) + '</p></div>'
          + '<div><b>出图要求</b><p>' + esc(shortText(it.image, 160)) + '</p></div>'
          + '</div>'
          + '<div class="app-tutorial-template-actions">'
          + '<button type="button" class="btn btn-outline btn-sm" data-toggle-detail="' + idx + '">展开全文</button>'
          + '<button type="button" class="btn btn-primary btn-sm" data-download-template="' + it.id + '" data-download-name="' + esc(it.name || '') + '">下载模板文件</button>'
          + '</div>'
          + '<div class="app-tutorial-template-detail" data-detail="' + idx + '" style="display:none">'
          + '<pre>' + esc([('口播要求：\n' + (it.oral || '')), ('朋友圈文案要求：\n' + (it.moments || '')), ('出图要求：\n' + (it.image || ''))].join('\n\n')) + '</pre>'
          + '</div>'
          + '</article>';
      }).join('');
      host.querySelectorAll('[data-toggle-detail]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          var idx = btn.getAttribute('data-toggle-detail');
          var box = host.querySelector('[data-detail="' + idx + '"]');
          if (!box) return;
          var show = box.style.display === 'none';
          box.style.display = show ? '' : 'none';
          btn.textContent = show ? '收起' : '展开全文';
        });
      });
      host.querySelectorAll('[data-download-template]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          downloadTemplate(btn.getAttribute('data-download-template'), btn.getAttribute('data-download-name') || '');
        });
      });
    } catch (e) {
      host.innerHTML = '<div class="empty" style="color:#dc2626">系统模板加载失败：' + esc(e && e.message ? e.message : e) + '</div>';
      if (hint) hint.textContent = '';
    }
  };

  if (document.readyState !== 'loading') {
    setTimeout(function () { window.initTutorialView(); }, 0);
  } else {
    document.addEventListener('DOMContentLoaded', function () { window.initTutorialView(); });
  }
})();
