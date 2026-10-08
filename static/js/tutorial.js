/* Online 教程 → 系统模板：条条列表 + 点击详情弹窗（复制内容 / 带入我的模板 / 下载资料文件） */
(function () {
  var cache = { items: [], loaded: false };

  function apiBase() {
    try {
      if (typeof API_BASE !== 'undefined' && API_BASE) return String(API_BASE).replace(/\/$/, '');
      if (window.__API_BASE) return String(window.__API_BASE).replace(/\/$/, '');
    } catch (e) {}
    return '';
  }

  function headers(extra) {
    var h = Object.assign({ 'Accept': 'application/json' }, extra || {});
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

  function fmtTime(v) {
    if (!v) return '';
    try { return new Date(v).toLocaleString('zh-CN'); } catch (e) { return String(v); }
  }

  function templateText(it) {
    return [
      '模板名称：' + (it.name || ''),
      '',
      '【口播要求】', (it.oral || '（未填写）'),
      '',
      '【朋友圈文案要求】', (it.moments || '（未填写）'),
      '',
      '【出图要求】', (it.image || '（未填写）'),
    ].join('\n');
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      try {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
        resolve();
      } catch (e) { reject(e); }
    });
  }

  function saveBlob(blob, filename) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
  }

  function filenameFromDisposition(disp, fallback) {
    var name = fallback;
    var m = /filename\*=UTF-8''([^;]+)/i.exec(disp || '');
    if (m) { try { name = decodeURIComponent(m[1]); } catch (e) {} }
    return name;
  }

  async function downloadTemplateFile(it) {
    var base = apiBase();
    if (!base) { alert('未配置服务器地址，无法下载'); return; }
    try {
      var res = await fetch(base + '/api/ip-content/system-templates/' + it.id + '/download', { headers: headers() });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      saveBlob(await res.blob(), filenameFromDisposition(res.headers.get('Content-Disposition'), (it.name || 'template') + '.txt'));
    } catch (e) { alert('下载失败：' + (e && e.message ? e.message : e)); }
  }

  async function downloadTemplateMemory(templateId, docId, fallbackName) {
    var base = apiBase();
    if (!base) { alert('未配置服务器地址，无法下载'); return; }
    try {
      var res = await fetch(base + '/api/ip-content/system-templates/' + templateId + '/memory/' + encodeURIComponent(docId) + '/download', { headers: headers() });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      saveBlob(await res.blob(), filenameFromDisposition(res.headers.get('Content-Disposition'), (fallbackName || docId) + '.md'));
    } catch (e) { alert('下载失败：' + (e && e.message ? e.message : e)); }
  }

  async function applySystemTemplate(it) {
    var base = apiBase();
    if (!base) { alert('未配置服务器地址，无法带入'); return; }
    if (!confirm('把系统模板「' + (it.name || it.id) + '」带入我的模板？\n（会在「我的模板」里新增一条记录，不覆盖你现有的模板，也不会改你当前启用的模板）')) return;
    try {
      var res = await fetch(base + '/api/ip-content/schedule-templates/' + it.id + '/copy', {
        method: 'POST',
        headers: headers({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({}),
      });
      var data = {};
      try { data = await res.json(); } catch (e) {}
      if (!res.ok) throw new Error((data && data.detail) || ('HTTP ' + res.status));
      alert('已在「我的模板」里新增一条记录：' + ((data.item && data.item.name) || it.name || '') + '\n到「个人设置 → 我的模板」里点「设为当前」即可启用。\n（系统模板不带原作者的资料调查，启用前请在模板里选一个你自己的资料调查）');
    } catch (e) { alert('带入失败：' + (e && e.message ? e.message : e)); }
  }

  function openModal(it) {
    var modal = document.getElementById('tutorialSystemTemplateModal');
    if (!modal) {
      alert('详情弹窗未加载（页面资源未更新），请重启客户端或刷新页面后再试。');
      return;
    }
    modal.dataset.templateId = String(it.id);
    document.getElementById('tutorialSystemTemplateModalTitle').textContent = it.name || ('系统模板 #' + it.id);
    document.getElementById('tutorialSystemTemplateModalMeta').textContent = '更新时间：' + (fmtTime(it.updated_at) || '-');
    var files = it.memory_files || [];
    document.getElementById('tutorialSystemTemplateModalBody').innerHTML =
      '<div class="tutorial-st-block"><b>口播要求</b><pre>' + esc(it.oral || '（未填写）') + '</pre></div>'
      + '<div class="tutorial-st-block"><b>朋友圈文案要求</b><pre>' + esc(it.moments || '（未填写）') + '</pre></div>'
      + '<div class="tutorial-st-block"><b>出图要求</b><pre>' + esc(it.image || '（未填写）') + '</pre></div>'
      + (files.length
          ? '<div class="tutorial-st-block"><b>资料文件</b><div class="tutorial-st-files">'
            + files.map(function (f) {
                return '<div class="tutorial-st-file"><span>' + esc(f.title || f.filename || f.doc_id) + '</span>'
                  + '<button type="button" class="btn btn-outline btn-sm" data-download-memory="' + esc(f.doc_id) + '" data-download-name="' + esc(f.title || f.filename || f.doc_id) + '">下载</button></div>';
              }).join('')
            + '</div></div>'
          : '')
      + '<div class="modal-actions" style="justify-content:flex-start;margin-top:10px">'
      + '<button type="button" class="btn btn-outline btn-sm" id="tutorialSystemTemplateFileBtn">下载模板文件</button>'
      + '</div>';
    document.getElementById('tutorialSystemTemplateModalBody').querySelectorAll('[data-download-memory]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        downloadTemplateMemory(it.id, btn.getAttribute('data-download-memory'), btn.getAttribute('data-download-name') || '');
      });
    });
    var fileBtn = document.getElementById('tutorialSystemTemplateFileBtn');
    if (fileBtn) fileBtn.addEventListener('click', function () { downloadTemplateFile(it); });
    var copyBtn = document.getElementById('tutorialSystemTemplateCopyBtn');
    copyBtn.onclick = function () {
      copyText(templateText(it)).then(function () { copyBtn.textContent = '已复制'; setTimeout(function () { copyBtn.textContent = '复制内容'; }, 1500); })
        .catch(function () { alert('复制失败，请手动选择文本复制'); });
    };
    var applyBtn = document.getElementById('tutorialSystemTemplateApplyBtn');
    applyBtn.onclick = function () { applySystemTemplate(it); };
    modal.classList.add('visible');
    modal.classList.add('show');
    modal.style.display = 'flex';
  }

  window.closeTutorialSystemTemplateModal = function () {
    var modal = document.getElementById('tutorialSystemTemplateModal');
    if (!modal) return;
    modal.classList.remove('show');
    modal.classList.remove('visible');
    modal.style.display = '';
  };

  function renderList(host, hint, items) {
    cache.items = items;
    if (hint) hint.textContent = '共 ' + items.length + ' 个系统模板';
    if (!items.length) { host.innerHTML = '<div class="empty">暂无系统模板</div>'; return; }
    host.innerHTML = items.map(function (it) {
      var files = (it.memory_files || []).length;
      return '<div class="tutorial-st-row" data-open-template="' + it.id + '">'
        + '<div class="tutorial-st-row-main"><strong>' + esc(it.name || ('模板 #' + it.id)) + '</strong>'
        + '<span class="app-tutorial-hint">' + (fmtTime(it.updated_at) || '') + (files ? ' · 资料文件 ' + files + ' 个' : '') + '</span></div>'
        + '<div class="tutorial-st-row-actions">'
        + '<button type="button" class="btn btn-outline btn-sm" data-open-template-btn="' + it.id + '">详情</button>'
        + '<button type="button" class="btn btn-primary btn-sm" data-apply-template="' + it.id + '">带入</button>'
        + '</div></div>';
    }).join('');
    host.querySelectorAll('[data-open-template],[data-open-template-btn]').forEach(function (el) {
      el.addEventListener('click', function (ev) {
        ev.stopPropagation();
        var id = Number(el.getAttribute('data-open-template') || el.getAttribute('data-open-template-btn'));
        var it = cache.items.filter(function (x) { return Number(x.id) === id; })[0];
        if (it) openModal(it);
      });
    });
    host.querySelectorAll('[data-apply-template]').forEach(function (btn) {
      btn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        var id = Number(btn.getAttribute('data-apply-template'));
        var it = cache.items.filter(function (x) { return Number(x.id) === id; })[0];
        if (it) applySystemTemplate(it);
      });
    });
  }

  window.initTutorialView = async function () {
    var host = document.getElementById('tutorialSystemTemplates');
    if (!host) return;
    var hint = document.getElementById('tutorialSystemTemplatesHint');
    var refresh = document.getElementById('tutorialSystemTemplatesRefresh');
    if (refresh && !refresh.dataset.bound) {
      refresh.dataset.bound = '1';
      refresh.addEventListener('click', function () { cache.loaded = false; window.initTutorialView(); });
    }
    var base = apiBase();
    if (!base) { host.innerHTML = '<div class="empty">未配置服务器地址</div>'; return; }
    if (cache.loaded && cache.items.length) { renderList(host, hint, cache.items); return; }
    host.innerHTML = '<div class="empty">加载中…</div>';
    try {
      var res = await fetch(base + '/api/ip-content/system-templates', { headers: headers() });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      var data = await res.json();
      var items = (data && data.items) || [];
      cache.loaded = true;
      renderList(host, hint, items);
    } catch (e) {
      host.innerHTML = '<div class="empty" style="color:#dc2626">系统模板加载失败：' + esc(e && e.message ? e.message : e) + '</div>';
      if (hint) hint.textContent = '';
    }
  };

  if (document.readyState !== 'loading') setTimeout(function () { window.initTutorialView(); }, 0);
  else document.addEventListener('DOMContentLoaded', function () { window.initTutorialView(); });
})();
