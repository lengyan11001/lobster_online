(function () {
  'use strict';

  var VIEW_ID = 'digital-human-koubo';
  var API = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? LOCAL_API_BASE : '';
  var state = { components: [], ready: false, polling: null, busy: false, quote: null };

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
    var url = API + path;
    return fetch(url, opts || { headers: headers() }).then(function (r) {
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
      + '#content-digital-human-koubo .koubo-grid{display:grid;grid-template-columns:minmax(340px,420px) minmax(0,1fr);gap:1rem;align-items:start;}'
      + '@media(max-width:900px){#content-digital-human-koubo .koubo-grid{grid-template-columns:1fr;}}'
      + '#content-digital-human-koubo .koubo-panel{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.09);border-radius:12px;padding:.9rem 1rem;}'
      + '#content-digital-human-koubo .koubo-panel h4{margin:0 0 .6rem;font-size:.92rem;}'
      + '#content-digital-human-koubo .koubo-dep{display:grid;grid-template-columns:1fr auto;gap:.35rem .6rem;padding:.55rem 0;border-bottom:1px dashed rgba(255,255,255,.08);}'
      + '#content-digital-human-koubo .koubo-dep:last-child{border-bottom:0;}'
      + '#content-digital-human-koubo .koubo-dep b{font-size:.84rem;font-weight:600;}'
      + '#content-digital-human-koubo .koubo-dep small{display:block;color:var(--text-muted);font-size:.74rem;margin-top:.12rem;line-height:1.45;}'
      + '#content-digital-human-koubo .koubo-dep-right{display:flex;align-items:center;gap:.45rem;}'
      + '#content-digital-human-koubo .koubo-pill{font-size:.72rem;padding:.14rem .45rem;border-radius:999px;border:1px solid transparent;white-space:nowrap;}'
      + '#content-digital-human-koubo .koubo-pill.ok{background:rgba(34,197,94,.12);color:#4ade80;border-color:rgba(34,197,94,.28);}'
      + '#content-digital-human-koubo .koubo-pill.missing{background:rgba(239,68,68,.1);color:#f87171;border-color:rgba(239,68,68,.28);}'
      + '#content-digital-human-koubo .koubo-pill.installing{background:rgba(59,130,246,.12);color:#60a5fa;border-color:rgba(59,130,246,.3);}'
      + '#content-digital-human-koubo .koubo-bar{grid-column:1/-1;height:3px;background:rgba(255,255,255,.08);border-radius:999px;overflow:hidden;display:none;}'
      + '#content-digital-human-koubo .koubo-bar.on{display:block;}'
      + '#content-digital-human-koubo .koubo-bar i{display:block;height:100%;background:#3b82f6;width:0;transition:width .3s;}'
      + '#content-digital-human-koubo textarea{width:100%;min-height:170px;box-sizing:border-box;padding:.6rem .7rem;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.14);border-radius:8px;color:var(--text);font-size:.86rem;line-height:1.6;resize:vertical;}'
      + '#content-digital-human-koubo .koubo-row{display:flex;gap:.5rem;align-items:center;margin-top:.6rem;flex-wrap:wrap;}'
      + '#content-digital-human-koubo table{width:100%;border-collapse:collapse;margin-top:.7rem;font-size:.8rem;}'
      + '#content-digital-human-koubo th,#content-digital-human-koubo td{text-align:left;padding:.35rem .4rem;border-bottom:1px solid rgba(255,255,255,.07);vertical-align:top;}'
      + '#content-digital-human-koubo th{color:var(--text-muted);font-weight:500;font-size:.74rem;}'
      + '#content-digital-human-koubo .koubo-total{margin-top:.6rem;font-size:.86rem;}'
      + '#content-digital-human-koubo .koubo-log{margin-top:1rem;padding:.6rem .75rem;background:rgba(0,0,0,.25);border:1px solid rgba(255,255,255,.07);border-radius:8px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.72rem;color:var(--text-muted);max-height:150px;overflow:auto;white-space:pre-wrap;}';
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
      + '    <button type="button" class="btn btn-ghost btn-sm" id="kouboRefresh">刷新状态</button>'
      + '  </div>'
      + '</div>'
      + '<div class="koubo-grid">'
      + '  <section class="koubo-panel">'
      + '    <h4>运行环境</h4>'
      + '    <div id="kouboReady" style="font-size:.78rem;color:var(--text-muted);margin-bottom:.5rem;">检测中…</div>'
      + '    <div id="kouboDeps"></div>'
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

  function renderDeps() {
    var box = $('kouboDeps');
    if (!box) return;
    box.innerHTML = state.components.map(function (c) {
      var btn = '';
      if (c.installable && c.status !== 'ok' && c.status !== 'installing') {
        btn = '<button type="button" class="btn btn-ghost btn-sm" data-koubo-install="' + esc(c.id) + '">安装</button>';
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
    }).join('');
    box.querySelectorAll('[data-koubo-install]').forEach(function (btn) {
      btn.addEventListener('click', function () { install(btn.getAttribute('data-koubo-install')); });
    });
    var ready = $('kouboReady');
    if (ready) {
      var missing = state.components.filter(function (c) { return c.status !== 'ok'; });
      ready.textContent = missing.length
        ? '还有 ' + missing.length + ' 项缺失：' + missing.map(function (c) { return c.name; }).join('、')
        : '全部就绪，可以进入生成阶段（本阶段不触发）。';
    }
  }

  function loadStatus() {
    return req('/api/skills/digital-human-koubo/status').then(function (d) {
      state.components = d.components || [];
      state.ready = !!d.ready;
      renderDeps();
      return d;
    }).catch(function (e) {
      log('状态读取失败：' + e.message);
      var box = $('kouboDeps');
      if (box) box.innerHTML = '<p style="color:#f87171;font-size:.8rem;">读取失败：' + esc(e.message) + '</p>';
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
          renderDeps();
        }
        if (job.status !== 'running') {
          clearInterval(state.polling);
          state.polling = null;
          if (job.status === 'done') log(component + ' 安装完成');
          else log(component + ' 安装失败：' + (job.error || job.message || '未知错误'));
          state.busy = false;
          loadStatus();
        }
      }).catch(function (e) {
        if (attempts > 5) {
          clearInterval(state.polling);
          state.polling = null;
          log('进度查询失败：' + e.message);
        }
      });
    }, 1200);
  }

  function install(component) {
    if (state.busy || state.polling) return;
    state.busy = true;
    log('开始安装 ' + component + ' …');
    req('/api/skills/digital-human-koubo/deps/install', {
      method: 'POST', headers: headers(), body: JSON.stringify({ component: component })
    }).then(function (d) {
      if (!d.job_id) throw new Error(d.message || '未返回任务');
      var c = state.components.filter(function (x) { return x.id === component; })[0];
      if (c) { c.status = 'installing'; c.progress = 0; renderDeps(); }
      poll(d.job_id, component);
    }).catch(function (e) {
      state.busy = false;
      log('安装启动失败：' + e.message);
    });
  }

  function installAll() {
    var next = state.components.filter(function (c) { return c.installable && c.status !== 'ok'; })[0];
    if (!next) { log('依赖已齐，无需安装'); return; }
    install(next.id);
    var timer = setInterval(function () {
      if (state.busy || (state.polling && state.polling !== null)) return;
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
