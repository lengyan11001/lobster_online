(function () {
  'use strict';

  var VIEW_ID = 'digital-human-koubo';
  var API = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? LOCAL_API_BASE : '';
  var state = { components: [], ready: false, polling: null, busy: false };

  function $(id) { return document.getElementById(id); }
  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function headers() {
    var h = { 'Content-Type': 'application/json' };
    if (typeof authHeaders === 'function') {
      try { Object.assign(h, authHeaders() || {}); } catch (e) {}
    }
    return h;
  }
  function log(msg) {
    var el = $('kouboLog');
    if (!el) return;
    var t = new Date().toTimeString().slice(0, 8);
    el.textContent = '[' + t + '] ' + msg + '\n' + el.textContent;
  }
  function req(path, opts) {
    return fetch(API + path, opts || { headers: headers() }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error((d && (d.detail || d.message)) || ('HTTP ' + r.status));
        return d;
      });
    });
  }

  function injectCss() {
    if ($('kouboStyle')) return;
    var css = ''
      + '#content-digital-human-koubo{padding:1rem 1.1rem 2rem;}'
      + '#content-digital-human-koubo .koubo-hero{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap;margin-bottom:1rem;}'
      + '#content-digital-human-koubo .koubo-hero h3{margin:0;font-size:1.15rem;}'
      + '#content-digital-human-koubo .koubo-hero p{margin:.3rem 0 0;color:var(--text-muted);font-size:.82rem;line-height:1.55;}'
      + '#content-digital-human-koubo .koubo-grid{display:grid;grid-template-columns:minmax(320px,380px) minmax(0,1fr);gap:1rem;align-items:start;}'
      + '@media(max-width:900px){#content-digital-human-koubo .koubo-grid{grid-template-columns:1fr;}}'
      + '#content-digital-human-koubo .koubo-panel{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.09);border-radius:12px;padding:.9rem 1rem;}'
      + '#content-digital-human-koubo .koubo-panel h4{margin:0 0 .6rem;font-size:.92rem;}'
      + '#content-digital-human-koubo .koubo-sum{font-size:.8rem;line-height:1.7;color:var(--text-muted);}'
      + '#content-digital-human-koubo .koubo-sum b{color:var(--text);font-weight:600;}'
      + '#content-digital-human-koubo textarea{width:100%;min-height:170px;box-sizing:border-box;padding:.6rem .7rem;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.14);border-radius:8px;color:var(--text);font-size:.86rem;line-height:1.6;resize:vertical;}'
      + '#content-digital-human-koubo .koubo-row{display:flex;gap:.5rem;align-items:center;margin-top:.6rem;flex-wrap:wrap;}'
      + '#content-digital-human-koubo table{width:100%;border-collapse:collapse;margin-top:.7rem;font-size:.8rem;}'
      + '#content-digital-human-koubo th,#content-digital-human-koubo td{text-align:left;padding:.35rem .4rem;border-bottom:1px solid rgba(255,255,255,.07);vertical-align:top;}'
      + '#content-digital-human-koubo th{color:var(--text-muted);font-weight:500;font-size:.74rem;}'
      + '#content-digital-human-koubo .koubo-total{margin-top:.6rem;font-size:.86rem;}'
      + '#content-digital-human-koubo .koubo-log{margin-top:1rem;padding:.6rem .75rem;background:rgba(0,0,0,.25);border:1px solid rgba(255,255,255,.07);border-radius:8px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.72rem;color:var(--text-muted);max-height:150px;overflow:auto;white-space:pre-wrap;}'
      + '#kouboDepsModal{position:fixed;inset:0;z-index:3200;}'
      + '#kouboDepsModal .koubo-modal-backdrop{position:absolute;inset:0;background:rgba(15,23,42,.58);}'
      + '#kouboDepsModal .koubo-modal-card{position:relative;width:min(92vw,560px);margin:10vh auto 0;border-radius:20px;background:#fff;color:#0f172a;box-shadow:0 32px 80px rgba(15,23,42,.32);overflow:hidden;font-family:inherit;}'
      + '#kouboDepsModal .koubo-modal-head{display:flex;align-items:center;justify-content:space-between;padding:18px 22px 10px;}'
      + '#kouboDepsModal .koubo-modal-head h4{margin:0;font-size:17px;}'
      + '#kouboDepsModal .koubo-modal-close{border:none;background:transparent;font-size:22px;line-height:1;cursor:pointer;color:#6b7280;}'
      + '#kouboDepsModal .koubo-modal-body{padding:0 22px 12px;max-height:min(66vh,600px);overflow:auto;}'
      + '#kouboDepsModal .koubo-modal-sub{font-size:12px;color:#6b7280;margin:0 0 10px;line-height:1.6;}'
      + '#kouboDepsModal .koubo-dep{display:grid;grid-template-columns:1fr auto;gap:3px 10px;padding:8px 0;border-bottom:1px dashed #e5e7eb;}'
      + '#kouboDepsModal .koubo-dep:last-child{border-bottom:0;}'
      + '#kouboDepsModal .koubo-dep b{font-size:13px;font-weight:600;}'
      + '#kouboDepsModal .koubo-dep small{display:block;color:#6b7280;font-size:11.5px;margin-top:2px;line-height:1.5;word-break:break-all;}'
      + '#kouboDepsModal .koubo-dep-right{display:flex;align-items:center;gap:6px;}'
      + '#kouboDepsModal .koubo-pill{font-size:11px;padding:2px 8px;border-radius:999px;border:1px solid transparent;white-space:nowrap;}'
      + '#kouboDepsModal .koubo-pill.ok{background:#ecfdf5;color:#059669;border-color:#a7f3d0;}'
      + '#kouboDepsModal .koubo-pill.missing{background:#fef2f2;color:#dc2626;border-color:#fecaca;}'
      + '#kouboDepsModal .koubo-pill.installing{background:#eff6ff;color:#2563eb;border-color:#bfdbfe;}'
      + '#kouboDepsModal .koubo-bar{grid-column:1/-1;height:4px;background:#e5e7eb;border-radius:999px;overflow:hidden;display:none;}'
      + '#kouboDepsModal .koubo-bar.on{display:block;}'
      + '#kouboDepsModal .koubo-bar i{display:block;height:100%;background:#3b82f6;width:0;transition:width .3s;}'
      + '#kouboDepsModal .koubo-modal-foot{display:flex;justify-content:space-between;gap:10px;padding:12px 22px 18px;border-top:1px solid #f1f5f9;}'
      + '#kouboDepsModal .koubo-btn{font-size:13px;padding:7px 14px;border-radius:8px;border:1px solid #d1d5db;background:#fff;cursor:pointer;}'
      + '#kouboDepsModal .koubo-btn.primary{background:linear-gradient(135deg,#f97316,#ea580c);border-color:transparent;color:#fff;}'
      + '#kouboDepsModal .koubo-btn:disabled{opacity:.55;cursor:default;}'
      + '#kouboDepsModal .koubo-install{font-size:12px;padding:4px 10px;border-radius:6px;border:1px solid #d1d5db;background:#fff;cursor:pointer;}';
    var style = document.createElement('style');
    style.id = 'kouboStyle';
    style.textContent = css;
    document.head.appendChild(style);
  }

  function shell() {
    return ''
      + '<div class="koubo-hero">'
      + '  <div><h3>数字人koubo</h3><p>第一阶段 · 运行环境自检 + 口播稿分段报价。生成仍走现有 Renoise / Grok 链路，本页不扣算力。</p></div>'
      + '  <div style="display:flex;gap:.5rem;flex-wrap:wrap;">'
      + '    <button type="button" class="btn btn-ghost btn-sm" id="kouboBack">返回技能商店</button>'
      + '    <button type="button" class="btn btn-ghost btn-sm" id="kouboOpenDeps">安装依赖</button>'
      + '    <button type="button" class="btn btn-ghost btn-sm" id="kouboRefresh">刷新状态</button>'
      + '  </div>'
      + '</div>'
      + '<div class="koubo-grid">'
      + '  <section class="koubo-panel">'
      + '    <h4>运行环境</h4>'
      + '    <div class="koubo-sum" id="kouboSummary">检测中…</div>'
      + '    <div class="koubo-row"><button type="button" class="btn btn-primary btn-sm" id="kouboInstallAll">一键安装缺失项</button></div>'
      + '  </section>'
      + '  <section class="koubo-panel">'
      + '    <h4>口播稿 → 分段与报价</h4>'
      + '    <textarea id="kouboText" placeholder="粘贴口播稿。按句切分、每段不超过约 15 秒（中文 5.4 字/秒）。"></textarea>'
      + '    <div class="koubo-row">'
      + '      <label style="font-size:.78rem;color:var(--text-muted);">景别</label>'
      + '      <select id="kouboShot" class="btn btn-ghost btn-sm" style="padding:.25rem .5rem;">'
      + '        <option value="full">全屏出镜（720p / 17 积分每秒）</option>'
      + '        <option value="pip">画中画（480p / 10 积分每秒）</option>'
      + '        <option value="vlog">自拍口吻（720p）</option>'
      + '        <option value="depth">中景留背景（720p，可加景深字）</option>'
      + '      </select>'
      + '      <button type="button" class="btn btn-primary btn-sm" id="kouboQuote">生成分段与报价</button>'
      + '    </div>'
      + '    <div id="kouboQuoteResult"></div>'
      + '  </section>'
      + '</div>'
      + '<div class="koubo-log" id="kouboLog">等待操作…</div>';
  }

  function pill(c) {
    if (c.status === 'ok') return '<span class="koubo-pill ok">就绪' + (c.version ? ' ' + esc(c.version) : '') + '</span>';
    if (c.status === 'installing') return '<span class="koubo-pill installing">安装中 ' + (c.progress || 0) + '%</span>';
    if (c.status === 'error') return '<span class="koubo-pill missing">异常</span>';
    return '<span class="koubo-pill missing">缺失</span>';
  }

  function depRow(c, compact) {
    var btn = '';
    if (c.installable && c.status !== 'ok' && c.status !== 'installing') {
      btn = '<button type="button" class="koubo-install" data-koubo-install="' + esc(c.id) + '">安装</button>';
    }
    var bar = c.status === 'installing'
      ? '<div class="koubo-bar on"><i style="width:' + (c.progress || 0) + '%"></i></div>'
      : '';
    return '<div class="koubo-dep" data-dep="' + esc(c.id) + '">'
      + '<div><b>' + esc(c.name) + '</b><small>' + esc(c.purpose) + ' · ' + esc(c.size) + '</small>'
      + '<small>' + esc(c.detail || '') + '</small></div>'
      + '<div class="koubo-dep-right">' + pill(c) + btn + '</div>'
      + bar
      + '</div>';
  }

  function renderSummary() {
    var el = $('kouboSummary');
    if (!el) return;
    var missing = state.components.filter(function (c) { return c.status !== 'ok'; });
    var ready = state.components.filter(function (c) { return c.status === 'ok'; }).length;
    el.innerHTML = '<div><b>' + ready + ' / ' + state.components.length + '</b> 项就绪</div>'
      + (missing.length
        ? '<div>缺失：' + missing.map(function (c) { return esc(c.name); }).join('、') + '</div>'
        : '<div>全部就绪，可以进入生成阶段（本阶段不触发）。</div>');
  }

  function renderModalRows() {
    var box = $('kouboDepsBody');
    if (!box) return;
    box.innerHTML = state.components.map(function (c) { return depRow(c); }).join('');
    box.querySelectorAll('[data-koubo-install]').forEach(function (btn) {
      btn.addEventListener('click', function () { install(btn.getAttribute('data-koubo-install')); });
    });
    var all = $('kouboModalInstallAll');
    if (all) {
      var anyMissing = state.components.some(function (c) { return c.installable && c.status !== 'ok'; });
      all.disabled = !anyMissing || !!state.busy || !!state.polling;
      all.textContent = anyMissing ? '一键安装缺失项' : '依赖已齐';
    }
  }

  function closeDepsModal() {
    var m = $('kouboDepsModal');
    if (m && m.parentNode) m.parentNode.removeChild(m);
  }

  function openDepsModal() {
    if ($('kouboDepsModal')) return;
    var modal = document.createElement('div');
    modal.id = 'kouboDepsModal';
    modal.innerHTML = ''
      + '<div class="koubo-modal-backdrop"></div>'
      + '<div class="koubo-modal-card" role="dialog" aria-modal="true" aria-labelledby="kouboDepsTitle">'
      + '  <div class="koubo-modal-head"><h4 id="kouboDepsTitle">运行环境与依赖</h4><button type="button" class="koubo-modal-close" aria-label="关闭">×</button></div>'
      + '  <div class="koubo-modal-body">'
      + '    <p class="koubo-modal-sub">ffmpeg / Node / Python 客户端已内置；whisper 只在「回听 + 逐字对齐」时用，不装也能生成，只是没有自动质检。安装会下载到客户端 deps 目录，可随时关闭弹窗，后台继续。</p>'
      + '    <div id="kouboDepsBody">加载中…</div>'
      + '  </div>'
      + '  <div class="koubo-modal-foot">'
      + '    <button type="button" class="koubo-btn" data-koubo-close>关闭</button>'
      + '    <button type="button" class="koubo-btn primary" id="kouboModalInstallAll">一键安装缺失项</button>'
      + '  </div>'
      + '</div>';
    document.body.appendChild(modal);
    modal.querySelector('.koubo-modal-backdrop').addEventListener('click', closeDepsModal);
    modal.querySelector('.koubo-modal-close').addEventListener('click', closeDepsModal);
    modal.querySelector('[data-koubo-close]').addEventListener('click', closeDepsModal);
    modal.querySelector('#kouboModalInstallAll').addEventListener('click', installAll);
    renderModalRows();
    loadStatus();
  }

  function loadStatus() {
    return req('/api/skills/digital-human-koubo/status').then(function (d) {
      state.components = d.components || [];
      state.ready = !!d.ready;
      renderSummary();
      renderModalRows();
      return d;
    }).catch(function (e) {
      log('状态读取失败：' + e.message);
      var el = $('kouboSummary');
      if (el) el.innerHTML = '<span style="color:#f87171;">读取失败：' + esc(e.message) + '</span>';
    });
  }

  function poll(jobId, component) {
    var attempts = 0;
    if (state.polling) clearInterval(state.polling);
    state.polling = setInterval(function () {
      attempts++;
      req('/api/skills/digital-human-koubo/deps/jobs/' + encodeURIComponent(jobId)).then(function (job) {
        var c = state.components.filter(function (x) { return x.id === component; })[0];
        if (c) {
          c.status = job.status === 'running' ? 'installing' : (job.status === 'done' ? 'ok' : 'error');
          c.progress = job.progress || 0;
          c.detail = job.message || c.detail;
          renderSummary();
          renderModalRows();
        }
        if (job.status !== 'running') {
          clearInterval(state.polling);
          state.polling = null;
          state.busy = false;
          if (job.status === 'done') log(component + ' 安装完成');
          else log(component + ' 安装失败：' + (job.error || job.message || '未知错误'));
          loadStatus();
        }
      }).catch(function (e) {
        if (attempts > 5) {
          clearInterval(state.polling);
          state.polling = null;
          state.busy = false;
          log('进度查询失败：' + e.message);
        }
      });
    }, 1200);
  }

  function install(component) {
    if (state.busy || state.polling) return;
    state.busy = true;
    log('开始安装 ' + component + ' …');
    renderModalRows();
    req('/api/skills/digital-human-koubo/deps/install', {
      method: 'POST', headers: headers(), body: JSON.stringify({ component: component })
    }).then(function (d) {
      if (!d.job_id) throw new Error(d.message || '未返回任务');
      var c = state.components.filter(function (x) { return x.id === component; })[0];
      if (c) { c.status = 'installing'; c.progress = 0; }
      renderSummary();
      renderModalRows();
      poll(d.job_id, component);
    }).catch(function (e) {
      state.busy = false;
      log('安装启动失败：' + e.message);
      renderModalRows();
    });
  }

  function installAll() {
    var next = state.components.filter(function (c) { return c.installable && c.status !== 'ok'; })[0];
    if (!next) { log('依赖已齐，无需安装'); return; }
    install(next.id);
    var timer = setInterval(function () {
      if (state.busy || state.polling) return;
      var again = state.components.filter(function (c) { return c.installable && c.status !== 'ok'; })[0];
      if (!again) { clearInterval(timer); log('缺失项已全部处理'); return; }
      install(again.id);
    }, 2500);
  }

  function renderQuote(d) {
    var box = $('kouboQuoteResult');
    if (!box) return;
    var rows = (d.segments || []).map(function (s) {
      return '<tr><td>' + esc(s.id) + '</td><td>' + esc(s.text) + '</td><td>' + s.seconds + 's</td><td>' + esc(s.resolution) + '</td><td>' + s.credits + '</td></tr>';
    }).join('');
    box.innerHTML = '<table><thead><tr><th>段</th><th>台词</th><th>时长</th><th>分辨率</th><th>credits</th></tr></thead><tbody>'
      + rows + '</tbody></table>'
      + '<div class="koubo-total">共 ' + d.segments.length + ' 段 · ' + d.total_seconds + ' 秒 · <b>' + d.total_credits + ' credits</b></div>'
      + '<p style="font-size:.74rem;color:var(--text-muted);margin:.45rem 0 0;">' + esc(d.note || '') + '</p>';
  }

  function quote() {
    var text = ($('kouboText') || {}).value || '';
    if (!text.trim()) { log('请先粘贴口播稿'); return; }
    var shot = ($('kouboShot') || {}).value || 'full';
    log('生成分段与报价…');
    req('/api/skills/digital-human-koubo/quote', {
      method: 'POST', headers: headers(), body: JSON.stringify({ text: text, shot: shot })
    }).then(function (d) {
      renderQuote(d);
      log('报价完成：' + d.segments.length + ' 段 / ' + d.total_credits + ' credits');
    }).catch(function (e) { log('报价失败：' + e.message); });
  }

  function bind() {
    var back = $('kouboBack'); if (back) back.onclick = function () { if (typeof window._switchToHiddenView === 'function') window._switchToHiddenView('skill-store'); else if (typeof showLobsterView === 'function') showLobsterView('skill-store'); };
    var refresh = $('kouboRefresh'); if (refresh) refresh.onclick = function () { log('刷新状态'); loadStatus(); };
    var deps = $('kouboOpenDeps'); if (deps) deps.onclick = openDepsModal;
    var all = $('kouboInstallAll'); if (all) all.onclick = installAll;
    var q = $('kouboQuote'); if (q) q.onclick = quote;
  }

  function ensureShell() {
    var root = document.getElementById('content-' + VIEW_ID);
    if (!root) return null;
    if (!root.dataset.kouboBuilt) {
      root.innerHTML = shell();
      root.dataset.kouboBuilt = '1';
      injectCss();
      bind();
    }
    return root;
  }

  window._openDigitalHumanKouboView = function () {
    if (typeof window._switchToHiddenView === 'function') {
      window._switchToHiddenView(VIEW_ID);
    } else if (typeof showLobsterView === 'function') {
      showLobsterView(VIEW_ID);
    }
    ensureShell();
    loadStatus();
    log('进入数字人 koubo 工作台');
  };

  document.addEventListener('DOMContentLoaded', function () {
    var root = document.getElementById('content-' + VIEW_ID);
    if (root && !root.dataset.kouboBuilt) { ensureShell(); }
  });
})();
