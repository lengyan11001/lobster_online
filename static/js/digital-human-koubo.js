(function () {
  'use strict';

  var VIEW_ID = 'digital-human-koubo';
  var API = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? LOCAL_API_BASE : '';
  var state = {
    step: 1,
    components: [],
    upload: null,
    quote: null,
    project: null,
    projects: [],
    polling: null,
    busy: false
  };

  function $(id) { return document.getElementById(id); }
  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function authOnly() {
    var h = {};
    if (typeof authHeaders === 'function') {
      try { h = Object.assign({}, authHeaders() || {}); } catch (e) { h = {}; }
    }
    Object.keys(h).forEach(function (k) { if (k.toLowerCase() === 'content-type') delete h[k]; });
    return h;
  }
  function headers() {
    var h = { 'Content-Type': 'application/json' };
    Object.assign(h, authOnly());
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
      + '#content-digital-human-koubo .koubo-hero{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap;margin-bottom:.9rem;}'
      + '#content-digital-human-koubo .koubo-hero h3{margin:0;font-size:1.15rem;}'
      + '#content-digital-human-koubo .koubo-hero p{margin:.3rem 0 0;color:var(--text-muted);font-size:.82rem;line-height:1.55;}'
      + '#content-digital-human-koubo .koubo-steps{display:flex;gap:.5rem;margin:0 0 1rem;flex-wrap:wrap;}'
      + '#content-digital-human-koubo .koubo-step{flex:1 1 160px;display:flex;align-items:center;gap:.5rem;padding:.55rem .7rem;border:1px solid rgba(255,255,255,.09);border-radius:10px;background:rgba(255,255,255,.02);font-size:.8rem;color:var(--text-muted);}'
      + '#content-digital-human-koubo .koubo-step b{width:20px;height:20px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;background:rgba(255,255,255,.08);font-size:.72rem;}'
      + '#content-digital-human-koubo .koubo-step.on{border-color:rgba(249,115,22,.45);color:var(--text);background:rgba(249,115,22,.07);}'
      + '#content-digital-human-koubo .koubo-step.on b{background:#f97316;color:#fff;}'
      + '#content-digital-human-koubo .koubo-step.done b{background:rgba(34,197,94,.2);color:#4ade80;}'
      + '#content-digital-human-koubo .koubo-card{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.09);border-radius:12px;padding:.9rem 1rem;}'
      + '#content-digital-human-koubo .koubo-grid{display:grid;grid-template-columns:minmax(300px,360px) minmax(0,1fr);gap:1rem;align-items:start;}'
      + '@media(max-width:900px){#content-digital-human-koubo .koubo-grid{grid-template-columns:1fr;}}'
      + '#content-digital-human-koubo h4{margin:0 0 .6rem;font-size:.92rem;}'
      + '#content-digital-human-koubo .koubo-drop{border:1.5px dashed rgba(255,255,255,.18);border-radius:12px;padding:1rem;text-align:center;cursor:pointer;transition:border-color .2s,background .2s;}'
      + '#content-digital-human-koubo .koubo-drop:hover,#content-digital-human-koubo .koubo-drop.over{border-color:#f97316;background:rgba(249,115,22,.06);}'
      + '#content-digital-human-koubo .koubo-drop small{display:block;color:var(--text-muted);font-size:.75rem;margin-top:.3rem;}'
      + '#content-digital-human-koubo .koubo-photo{width:100%;max-height:260px;object-fit:contain;border-radius:8px;background:rgba(0,0,0,.25);display:block;}'
      + '#content-digital-human-koubo .koubo-checks{margin:.6rem 0 0;padding:0;list-style:none;font-size:.76rem;color:var(--text-muted);}'
      + '#content-digital-human-koubo .koubo-checks li{padding:.12rem 0;}'
      + '#content-digital-human-koubo .koubo-checks .ok{color:#4ade80;}'
      + '#content-digital-human-koubo .koubo-checks .warn{color:#fbbf24;}'
      + '#content-digital-human-koubo textarea{width:100%;min-height:180px;box-sizing:border-box;padding:.6rem .7rem;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.14);border-radius:8px;color:var(--text);font-size:.86rem;line-height:1.6;resize:vertical;}'
      + '#content-digital-human-koubo select,#content-digital-human-koubo input[type=text]{padding:.35rem .5rem;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.14);border-radius:6px;color:var(--text);font-size:.82rem;}'
      + '#content-digital-human-koubo .koubo-row{display:flex;gap:.5rem;align-items:center;margin-top:.65rem;flex-wrap:wrap;}'
      + '#content-digital-human-koubo .koubo-row .spacer{flex:1;}'
      + '#content-digital-human-koubo table{width:100%;border-collapse:collapse;margin-top:.6rem;font-size:.8rem;}'
      + '#content-digital-human-koubo th,#content-digital-human-koubo td{text-align:left;padding:.35rem .4rem;border-bottom:1px solid rgba(255,255,255,.07);vertical-align:top;}'
      + '#content-digital-human-koubo th{color:var(--text-muted);font-weight:500;font-size:.74rem;}'
      + '#content-digital-human-koubo .koubo-total{margin-top:.6rem;font-size:.88rem;}'
      + '#content-digital-human-koubo .koubo-history{margin-top:1rem;}'
      + '#content-digital-human-koubo .koubo-proj{display:flex;justify-content:space-between;gap:.75rem;align-items:center;padding:.5rem 0;border-bottom:1px dashed rgba(255,255,255,.08);font-size:.8rem;}'
      + '#content-digital-human-koubo .koubo-log{margin-top:1rem;padding:.6rem .75rem;background:rgba(0,0,0,.25);border:1px solid rgba(255,255,255,.07);border-radius:8px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.72rem;color:var(--text-muted);max-height:130px;overflow:auto;white-space:pre-wrap;}'
      + '#kouboDepsModal{position:fixed;inset:0;z-index:3200;}'
      + '#kouboDepsModal .koubo-modal-backdrop{position:absolute;inset:0;background:rgba(15,23,42,.58);}'
      + '#kouboDepsModal .koubo-modal-card{position:relative;width:min(92vw,560px);margin:10vh auto 0;border-radius:20px;background:#fff;color:#0f172a;box-shadow:0 32px 80px rgba(15,23,42,.32);overflow:hidden;}'
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
      + '  <div><h3>数字人koubo</h3><p>一张照片 + 一句台词 → 口播成片。第一阶段：素材、分段、任务包（生成待接入 Renoise）。</p></div>'
      + '  <div style="display:flex;gap:.5rem;flex-wrap:wrap;">'
      + '    <button type="button" class="btn btn-ghost btn-sm" id="kouboBack">返回技能商店</button>'
      + '    <button type="button" class="btn btn-ghost btn-sm" id="kouboOpenDeps">安装依赖</button>'
      + '    <button type="button" class="btn btn-ghost btn-sm" id="kouboRefresh">刷新</button>'
      + '  </div>'
      + '</div>'
      + '<div class="koubo-steps" id="kouboSteps">'
      + '  <div class="koubo-step on" data-step="1"><b>1</b><span>人物素材</span></div>'
      + '  <div class="koubo-step" data-step="2"><b>2</b><span>口播文案</span></div>'
      + '  <div class="koubo-step" data-step="3"><b>3</b><span>分段与任务包</span></div>'
      + '</div>'
      + '<div id="kouboStepBody"></div>'
      + '<div class="koubo-card koubo-history"><h4>项目记录</h4><div id="kouboProjects"><span style="font-size:.78rem;color:var(--text-muted);">加载中…</span></div></div>'
      + '<div class="koubo-log" id="kouboLog">等待操作…</div>';
  }

  function stepBar() {
    var bar = $('kouboSteps');
    if (!bar) return;
    bar.querySelectorAll('.koubo-step').forEach(function (el) {
      var n = Number(el.getAttribute('data-step'));
      el.classList.toggle('on', n === state.step);
      el.classList.toggle('done', n < state.step);
    });
  }

  function photoBox() {
    if (!state.upload) {
      return '<div class="koubo-drop" id="kouboDrop">'
        + '<div style="font-size:.86rem;">点击或拖入人物照片</div>'
        + '<small>jpg / png / webp，≤ 15MB，建议短边 ≥ 512px</small>'
        + '<input type="file" id="kouboFile" accept="image/jpeg,image/png,image/webp" style="display:none;">'
        + '</div>';
    }
    var c = state.upload.checks || {};
    var warns = c.warnings || [];
    return '<img class="koubo-photo" src="' + esc(API + state.upload.url) + '" alt="形象照">'
      + '<ul class="koubo-checks">'
      + '<li class="' + (Math.min(c.width || 0, c.height || 0) >= 512 ? 'ok' : 'warn') + '">分辨率 ' + (c.width || 0) + '×' + (c.height || 0) + '</li>'
      + '<li class="' + ((c.sharpness || 0) >= 25 ? 'ok' : 'warn') + '">清晰度 ' + (c.sharpness || 0) + (c.sharpness >= 25 ? ' · 合格' : ' · 偏低') + '</li>'
      + '<li class="' + ((c.brightness || 0) >= 60 && (c.brightness || 0) <= 215 ? 'ok' : 'warn') + '">亮度 ' + (c.brightness || 0) + '</li>'
      + warns.map(function (w) { return '<li class="warn">⚠ ' + esc(w) + '</li>'; }).join('')
      + '</ul>'
      + '<div class="koubo-row"><button type="button" class="btn btn-ghost btn-sm" id="kouboReplace">换一张</button></div>';
  }

  function renderStep1() {
    return '<div class="koubo-grid">'
      + '<section class="koubo-card"><h4>人物素材</h4>' + photoBox() + '</section>'
      + '<section class="koubo-card"><h4>拍摄要求</h4>'
      + '<ul class="koubo-checks">'
      + '<li>正脸、平视，嘴巴闭合（口型从闭嘴开始最稳）</li>'
      + '<li>头发不挡脸，光线均匀，不要逆光</li>'
      + '<li>背景干净，人物占画面 50%–70%，头顶留白</li>'
      + '<li>只用本人或已获授权的人像；成片会带「内容由 AI 数字人演绎」</li>'
      + '</ul>'
      + '<div class="koubo-row"><span class="spacer"></span><button type="button" class="btn btn-primary btn-sm" id="kouboNext1">下一步：写文案</button></div>'
      + '</section></div>';
  }

  function renderStep2() {
    var text = state.project ? state.project.script : (state.script || '');
    var shot = (state.project && state.project.shot) || state.shot || 'full';
    return '<div class="koubo-card"><h4>口播文案</h4>'
      + '<div class="koubo-row" style="margin-top:0;">'
      + '<label style="font-size:.78rem;color:var(--text-muted);">主播</label>'
      + '<select id="kouboHost"><option value="tank"' + (state.host === 'me' ? '' : ' selected') + '>白背心数字人（9:16）</option><option value="me"' + (state.host === 'me' ? ' selected' : '') + '>用户本人（16:9）</option></select>'
      + '<label style="font-size:.78rem;color:var(--text-muted);">景别</label>'
      + '<select id="kouboShot">'
      + '<option value="full"' + (shot === 'full' ? ' selected' : '') + '>全屏出镜 720p / 17</option>'
      + '<option value="pip"' + (shot === 'pip' ? ' selected' : '') + '>画中画 480p / 10</option>'
      + '<option value="vlog"' + (shot === 'vlog' ? ' selected' : '') + '>自拍口吻 720p</option>'
      + '<option value="depth"' + (shot === 'depth' ? ' selected' : '') + '>中景留背景 720p</option>'
      + '</select>'
      + '</div>'
      + '<textarea id="kouboText" style="margin-top:.6rem;" placeholder="粘贴口播稿。按句切分，每段不超过约 15 秒（中文 5.4 字/秒）。">' + esc(text) + '</textarea>'
      + '<div class="koubo-row"><small id="kouboCount" style="color:var(--text-muted);font-size:.75rem;">0 字</small><span class="spacer"></span>'
      + '<button type="button" class="btn btn-ghost btn-sm" id="kouboPrev2">上一步</button>'
      + '<button type="button" class="btn btn-primary btn-sm" id="kouboQuote">生成分段与报价</button></div>'
      + '</div>';
  }

  function segmentsTable(segs, totalSec, totalCredits) {
    var rows = (segs || []).map(function (s) {
      return '<tr><td>' + esc(s.id) + '</td><td>' + esc(s.text) + '</td><td>' + s.seconds + 's</td><td>' + esc(s.resolution) + '</td><td>' + s.credits + '</td></tr>';
    }).join('');
    return '<table><thead><tr><th>段</th><th>台词</th><th>时长</th><th>分辨率</th><th>credits</th></tr></thead><tbody>' + rows + '</tbody></table>'
      + '<div class="koubo-total">共 ' + (segs || []).length + ' 段 · ' + totalSec + ' 秒 · <b>' + totalCredits + ' credits</b></div>';
  }

  function renderStep3() {
    var q = state.quote;
    if (!q) return '<div class="koubo-card"><h4>还没有分段</h4><p style="font-size:.8rem;color:var(--text-muted);">请先回到上一步生成分段。</p></div>';
    var saved = '';
    if (state.project && state.project.id) {
      var isDone = state.project.status === 'generated';
      saved = '<div class="koubo-card" style="margin-top:1rem;"><h4>生成</h4>'
        + '<p style="font-size:.8rem;color:var(--text-muted);">项目：<b>' + esc(state.project.name) + '</b>（' + esc(state.project.id) + '）</p>';
      if (isDone) {
        saved += '<video controls playsinline style="width:100%;max-height:420px;border-radius:10px;background:#000;margin:.4rem 0;" src="' + esc(API + '/api/skills/digital-human-koubo/projects/' + state.project.id + '/video') + '"></video>'
          + '<div class="koubo-row"><a class="btn btn-primary btn-sm" href="' + esc(API + '/api/skills/digital-human-koubo/projects/' + state.project.id + '/video') + '" download>下载成片</a>'
          + '<button type="button" class="btn btn-ghost btn-sm" id="kouboRegen">重新生成</button>'
          + '<button type="button" class="btn btn-ghost btn-sm" id="kouboExport">下载 tasks.json</button></div>';
      } else {
        saved += '<div class="koubo-row"><button type="button" class="btn btn-primary btn-sm" id="kouboGenFirst">提交生成第 1 段</button>'
          + '<button type="button" class="btn btn-ghost btn-sm" id="kouboGenAll">全部生成（' + (state.quote ? state.quote.segments.length : 0) + ' 段）</button>'
          + '<button type="button" class="btn btn-ghost btn-sm" id="kouboExport">下载 tasks.json</button>'
          + '<button type="button" class="btn btn-ghost btn-sm" id="kouboCopyScript">复制文案</button></div>'
          + '<p style="font-size:.74rem;color:var(--text-muted);margin:.45rem 0 0;">生成走本机 MCP 的 Grok 1.5 图生视频，按次扣算力（每段 1 次）。建议先出第 1 段确认口型和音色。</p>'
          + '<div id="kouboGenStatus" style="font-size:.78rem;color:var(--text-muted);margin-top:.35rem;"></div>';
      }
      saved += '</div>';
    } else {
      saved = '<div class="koubo-row" style="margin-top:.8rem;"><button type="button" class="btn btn-primary btn-sm" id="kouboSave">保存项目</button></div>';
    }
    return '<div class="koubo-card"><h4>分段与报价</h4>'
      + segmentsTable(q.segments, q.total_seconds, q.total_credits)
      + '<p style="font-size:.74rem;color:var(--text-muted);margin:.5rem 0 0;">' + esc(q.note || '') + '</p>'
      + '<div class="koubo-row"><button type="button" class="btn btn-ghost btn-sm" id="kouboPrev3">上一步</button><span class="spacer"></span>'
      + '<span style="font-size:.76rem;color:var(--text-muted);">第一阶段只出任务包，不提交生成、不扣算力</span></div>'
      + '</div>' + saved;
  }

  function renderStep() {
    var body = $('kouboStepBody');
    if (!body) return;
    stepBar();
    body.innerHTML = state.step === 1 ? renderStep1() : state.step === 2 ? renderStep2() : renderStep3();
    bindStep();
  }

  function bindStep() {
    if (state.step === 1) {
      var drop = $('kouboDrop');
      var file = $('kouboFile');
      if (drop && file) {
        drop.addEventListener('click', function () { file.click(); });
        drop.addEventListener('dragover', function (e) { e.preventDefault(); drop.classList.add('over'); });
        drop.addEventListener('dragleave', function () { drop.classList.remove('over'); });
        drop.addEventListener('drop', function (e) {
          e.preventDefault(); drop.classList.remove('over');
          if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]);
        });
        file.addEventListener('change', function () { if (file.files && file.files[0]) upload(file.files[0]); });
      }
      var rep = $('kouboReplace'); if (rep) rep.onclick = function () { state.upload = null; renderStep(); };
      var next = $('kouboNext1'); if (next) next.onclick = function () { state.step = 2; renderStep(); };
    }
    if (state.step === 2) {
      var ta = $('kouboText');
      var cnt = $('kouboCount');
      function refreshCount() {
        if (!ta || !cnt) return;
        var n = (ta.value || '').replace(/\s/g, '').length;
        cnt.textContent = n + ' 字 · 约 ' + Math.max(1, Math.ceil(n / 5.4)) + ' 秒';
      }
      if (ta) { ta.addEventListener('input', refreshCount); refreshCount(); }
      var prev = $('kouboPrev2'); if (prev) prev.onclick = function () { state.step = 1; renderStep(); };
      var q = $('kouboQuote'); if (q) q.onclick = quote;
    }
    if (state.step === 3) {
      var p3 = $('kouboPrev3'); if (p3) p3.onclick = function () { state.step = 2; renderStep(); };
      var save = $('kouboSave'); if (save) save.onclick = saveProject;
      var exp = $('kouboExport'); if (exp) exp.onclick = exportTasks;
      var gf = $('kouboGenFirst'); if (gf) gf.onclick = function () { submitGenerate(false); };
      var ga = $('kouboGenAll'); if (ga) ga.onclick = function () { submitGenerate(true); };
      var rg = $('kouboRegen'); if (rg) rg.onclick = function () { submitGenerate(false); };
      var cp = $('kouboCopyScript'); if (cp) cp.onclick = function () {
        var txt = (state.project && state.project.script) || '';
        if (navigator.clipboard) navigator.clipboard.writeText(txt).then(function () { log('文案已复制'); });
      };
    }
  }

  function upload(f) {
    var fd = new FormData();
    fd.append('file', f);
    log('上传形象照 ' + f.name + '（' + Math.round(f.size / 1024) + ' KB）');
    fetch(API + '/api/skills/digital-human-koubo/uploads', { method: 'POST', headers: authOnly(), body: fd })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) { var msg = d && d.detail ? (typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail)) : ('HTTP ' + r.status); throw new Error(msg); } return d; }); })
      .then(function (d) {
        state.upload = d;
        log('上传完成：' + d.checks.width + '×' + d.checks.height + '，' + (d.checks.warnings || []).length + ' 条提示');
        renderStep();
      })
      .catch(function (e) { log('上传失败：' + e.message); });
  }

  function quote() {
    var text = ($('kouboText') || {}).value || '';
    if (!text.trim()) { log('请先粘贴口播稿'); return; }
    var shot = ($('kouboShot') || {}).value || 'full';
    var host = ($('kouboHost') || {}).value || 'tank';
    state.script = text;
    state.shot = shot;
    state.host = host;
    log('生成分段与报价…');
    req('/api/skills/digital-human-koubo/quote', {
      method: 'POST', headers: headers(), body: JSON.stringify({ text: text, shot: shot })
    }).then(function (d) {
      state.quote = d;
      state.project = null;
      state.step = 3;
      renderStep();
      log('报价完成：' + d.segments.length + ' 段 / ' + d.total_credits + ' credits');
    }).catch(function (e) { log('报价失败：' + e.message); });
  }

  function saveProject() {
    if (!state.quote) return;
    var body = {
      name: '',
      script: state.script,
      shot: state.shot,
      host: state.host,
      photo_id: state.upload ? state.upload.id : ''
    };
    req('/api/skills/digital-human-koubo/projects', {
      method: 'POST', headers: headers(), body: JSON.stringify(body)
    }).then(function (p) {
      state.project = p;
      log('项目已保存：' + p.id);
      renderStep();
      loadProjects();
    }).catch(function (e) { log('保存失败：' + e.message); });
  }

  function exportTasks() {
    if (!state.project) return;
    req('/api/skills/digital-human-koubo/projects/' + encodeURIComponent(state.project.id) + '/tasks')
      .then(function (d) {
        var blob = new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' });
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'koubo-tasks-' + state.project.id + '.json';
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
        log('已导出 tasks.json（' + (d.tasks || []).length + ' 条）');
      }).catch(function (e) { log('导出失败：' + e.message); });
  }


  function submitGenerate(all) {
    if (!state.project || !state.project.id) return;
    var status = $('kouboGenStatus');
    if (status) status.textContent = '提交中…';
    req('/api/skills/digital-human-koubo/projects/' + encodeURIComponent(state.project.id) + '/generate', {
      method: 'POST', headers: headers(), body: JSON.stringify({ all: !!all })
    }).then(function (d) {
      log('已提交生成（' + (d.mode === 'all' ? '全部' : '仅第 1 段') + '），job=' + d.job_id);
      pollGenerate(d.job_id);
    }).catch(function (e) {
      var msg = e && e.message ? e.message : '提交失败';
      if (/连接正在自动恢复/.test(msg)) msg = '本地服务刚重启，请 3 秒后重试';
      if (status) status.textContent = '提交失败：' + msg;
      log('提交生成失败：' + msg);
    });
  }

  function pollGenerate(jobId) {
    var timer = setInterval(function () {
      req('/api/skills/digital-human-koubo/generate/jobs/' + encodeURIComponent(jobId)).then(function (job) {
        var el = $('kouboGenStatus');
        if (el) {
          var pct = job.total ? Math.round((job.done || 0) * 100 / job.total) : (job.progress || 0);
          el.innerHTML = '<div style="margin-bottom:.25rem;">' + esc(job.message || '') + ' · ' + pct + '%</div>'
            + '<div style="height:4px;background:rgba(255,255,255,.08);border-radius:999px;overflow:hidden;">'
            + '<i style="display:block;height:100%;width:' + Math.max(3, pct) + '%;background:#f97316;"></i></div>';
        }
        if (job.status !== 'running') {
          clearInterval(timer);
          if (job.status === 'done') {
            log('生成完成');
            req('/api/skills/digital-human-koubo/projects/' + encodeURIComponent(state.project.id)).then(function (p) {
              state.project = p;
              renderStep();
            });
          } else {
            log('生成失败：' + (job.error || job.message || '未知错误'));
            if (el) el.innerHTML = '<span style="color:#f87171;">' + esc(job.error || '生成失败') + '</span>';
          }
        }
      }).catch(function () {});
    }, 3000);
    state.genTimer = timer;
  }

  function loadProjects() {
    req('/api/skills/digital-human-koubo/projects').then(function (d) {
      state.projects = d.projects || [];
      renderProjects();
    }).catch(function (e) {
      var box = $('kouboProjects');
      if (box) box.innerHTML = '<span style="color:#f87171;">读取失败：' + esc(e.message) + '</span>';
    });
  }

  function renderProjects() {
    var box = $('kouboProjects');
    if (!box) return;
    if (!state.projects.length) {
      box.innerHTML = '<span style="font-size:.78rem;color:var(--text-muted);">还没有项目。完成一次分段并保存后会出现在这里。</span>';
      return;
    }
    box.innerHTML = state.projects.map(function (p) {
      return '<div class="koubo-proj" data-pid="' + esc(p.id) + '">'
        + '<div><b>' + esc(p.name) + '</b><div style="color:var(--text-muted);font-size:.72rem;margin-top:.15rem;">'
        + esc(p.created_at.replace('T', ' ')) + ' · ' + (p.segments || []).length + ' 段 · ' + p.total_credits + ' credits · ' + esc(p.status) + '</div></div>'
        + '<div style="display:flex;gap:.4rem;"><button type="button" class="btn btn-ghost btn-sm" data-koubo-open="' + esc(p.id) + '">打开</button>'
        + '<button type="button" class="btn btn-ghost btn-sm" data-koubo-del="' + esc(p.id) + '">删除</button></div>'
        + '</div>';
    }).join('');
    box.querySelectorAll('[data-koubo-open]').forEach(function (btn) {
      btn.onclick = function () {
        var pid = btn.getAttribute('data-koubo-open');
        req('/api/skills/digital-human-koubo/projects/' + encodeURIComponent(pid)).then(function (p) {
          state.project = p;
          state.script = p.script;
          state.shot = p.shot;
          state.host = p.host;
          state.quote = {
            segments: p.segments,
            total_seconds: p.total_seconds,
            total_credits: p.total_credits,
            note: '来自已保存项目 ' + p.id
          };
          state.step = 3;
          renderStep();
          log('打开项目 ' + p.id);
        });
      };
    });
    box.querySelectorAll('[data-koubo-del]').forEach(function (btn) {
      btn.onclick = function () {
        var pid = btn.getAttribute('data-koubo-del');
        if (!window.confirm('删除这个项目？')) return;
        fetch(API + '/api/skills/digital-human-koubo/projects/' + encodeURIComponent(pid), { method: 'DELETE', headers: authOnly() })
          .then(function () { log('已删除 ' + pid); loadProjects(); });
      };
    });
  }

  function pill(c) {
    if (c.status === 'ok') return '<span class="koubo-pill ok">就绪' + (c.version ? ' ' + esc(c.version) : '') + '</span>';
    if (c.status === 'installing') return '<span class="koubo-pill installing">安装中 ' + (c.progress || 0) + '%</span>';
    if (c.status === 'error') return '<span class="koubo-pill missing">异常</span>';
    return '<span class="koubo-pill missing">缺失</span>';
  }

  function renderModalRows() {
    var box = $('kouboDepsBody');
    if (!box) return;
    box.innerHTML = state.components.map(function (c) {
      var btn = (c.installable && c.status !== 'ok' && c.status !== 'installing')
        ? '<button type="button" class="koubo-install" data-koubo-install="' + esc(c.id) + '">安装</button>' : '';
      var bar = c.status === 'installing'
        ? '<div class="koubo-bar on"><i style="width:' + (c.progress || 0) + '%"></i></div>' : '';
      return '<div class="koubo-dep"><div><b>' + esc(c.name) + '</b><small>' + esc(c.purpose) + ' · ' + esc(c.size) + '</small><small>' + esc(c.detail || '') + '</small></div>'
        + '<div class="koubo-dep-right">' + pill(c) + btn + '</div>' + bar + '</div>';
    }).join('');
    box.querySelectorAll('[data-koubo-install]').forEach(function (btn) {
      btn.onclick = function () { install(btn.getAttribute('data-koubo-install')); };
    });
    var all = $('kouboModalInstallAll');
    if (all) {
      var anyMissing = state.components.some(function (c) { return c.installable && c.status !== 'ok'; });
      all.disabled = !anyMissing || !!state.busy || !!state.polling;
      all.textContent = anyMissing ? '一键安装缺失项' : '依赖已齐';
    }
  }

  function openDepsModal() {
    if ($('kouboDepsModal')) return;
    var m = document.createElement('div');
    m.id = 'kouboDepsModal';
    m.innerHTML = '<div class="koubo-modal-backdrop"></div>'
      + '<div class="koubo-modal-card" role="dialog" aria-modal="true">'
      + '<div class="koubo-modal-head"><h4>运行环境与依赖</h4><button type="button" class="koubo-modal-close" aria-label="关闭">×</button></div>'
      + '<div class="koubo-modal-body"><p class="koubo-modal-sub">ffmpeg / Node / Python 客户端已内置；whisper 只在「回听 + 逐字对齐」时用，不装也能出片，只是没有自动质检。安装会下载到客户端 deps 目录，可关闭弹窗后台继续。</p><div id="kouboDepsBody">加载中…</div></div>'
      + '<div class="koubo-modal-foot"><button type="button" class="koubo-btn" data-koubo-close>关闭</button><button type="button" class="koubo-btn primary" id="kouboModalInstallAll">一键安装缺失项</button></div>'
      + '</div>';
    document.body.appendChild(m);
    m.querySelector('.koubo-modal-backdrop').onclick = closeDepsModal;
    m.querySelector('.koubo-modal-close').onclick = closeDepsModal;
    m.querySelector('[data-koubo-close]').onclick = closeDepsModal;
    m.querySelector('#kouboModalInstallAll').onclick = installAll;
    renderModalRows();
    loadStatus();
  }

  function closeDepsModal() {
    var m = $('kouboDepsModal');
    if (m && m.parentNode) m.parentNode.removeChild(m);
  }

  function loadStatus() {
    return req('/api/skills/digital-human-koubo/status').then(function (d) {
      state.components = d.components || [];
      renderModalRows();
      return d;
    }).catch(function (e) { log('依赖状态读取失败：' + e.message); });
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
          renderModalRows();
        }
        if (job.status !== 'running') {
          clearInterval(state.polling); state.polling = null; state.busy = false;
          if (job.status === 'done') log(component + ' 安装完成');
          else log(component + ' 安装失败：' + (job.error || job.message || '未知错误'));
          loadStatus();
        }
      }).catch(function () {
        if (attempts > 5) { clearInterval(state.polling); state.polling = null; state.busy = false; }
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
      if (c) { c.status = 'installing'; c.progress = 0; }
      renderModalRows();
      poll(d.job_id, component);
    }).catch(function (e) { state.busy = false; log('安装启动失败：' + e.message); });
  }

  function installAll() {
    var next = state.components.filter(function (c) { return c.installable && c.status !== 'ok'; })[0];
    if (!next) { log('依赖已齐，无需安装'); return; }
    install(next.id);
    var timer = setInterval(function () {
      if (state.busy || state.polling) return;
      var again = state.components.filter(function (c) { return c.installable && c.status !== 'ok'; })[0];
      if (!again) { clearInterval(timer); return; }
      install(again.id);
    }, 2500);
  }

  function ensureShell() {
    var root = document.getElementById('content-' + VIEW_ID);
    if (!root) return null;
    if (!root.dataset.kouboBuilt) {
      root.innerHTML = shell();
      root.dataset.kouboBuilt = '1';
      injectCss();
      $('kouboBack').onclick = function () {
        if (typeof window._switchToHiddenView === 'function') window._switchToHiddenView('skill-store');
        else if (typeof showLobsterView === 'function') showLobsterView('skill-store');
      };
      $('kouboOpenDeps').onclick = openDepsModal;
      $('kouboRefresh').onclick = function () { loadStatus(); loadProjects(); };
      renderStep();
    }
    return root;
  }

  window._openDigitalHumanKouboView = function () {
    if (typeof window._switchToHiddenView === 'function') window._switchToHiddenView(VIEW_ID);
    else if (typeof showLobsterView === 'function') showLobsterView(VIEW_ID);
    ensureShell();
    loadStatus();
    loadProjects();
    log('进入数字人 koubo 工作台');
  };

  document.addEventListener('DOMContentLoaded', function () {
    var root = document.getElementById('content-' + VIEW_ID);
    if (root && !root.dataset.kouboBuilt) { ensureShell(); }
  });
})();
