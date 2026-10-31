(function () {
  'use strict';

  var VIEW_ID = 'digital-human-koubo';
  var API = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? LOCAL_API_BASE : '';
  var state = {
    options: null,
    form: { host: 'tank', shot: 'full', ratio: '', resolution: '', language: 'Mandarin Chinese', model: '', scene: 'none', scene_prompt: '', voice: '', compose_scene: false, packaging: [] },
    upload: null,
    quote: null,
    project: null,
    projects: [],
    components: [],
    job: null,
    render: { preset: '标准', components: ['caption', 'keyword', 'cta', 'lowerthird', 'punch', 'chapter', 'stack', 'counter'], job: null, packaged: false },
    qa: null,
    timer: null
  };

  function $(id) { return document.getElementById(id); }
  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function authOnly() {
    var h = {};
    if (typeof authHeaders === 'function') { try { h = Object.assign({}, authHeaders() || {}); } catch (e) { h = {}; } }
    Object.keys(h).forEach(function (k) { if (k.toLowerCase() === 'content-type') delete h[k]; });
    return h;
  }
  function headers() {
    var h = { 'Content-Type': 'application/json' };
    Object.assign(h, authOnly());
    return h;
  }
  function req(path, opts) {
    return fetch(API + path, opts || { headers: headers() }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) {
          var msg = d && d.detail ? (typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail)) : ('HTTP ' + r.status);
          throw new Error(msg);
        }
        return d;
      });
    });
  }
  function log(msg) {
    var el = $('kbLog');
    if (!el) return;
    var t = new Date().toTimeString().slice(0, 8);
    el.textContent = '[' + t + '] ' + msg + '\n' + el.textContent;
  }

  function injectCss() {
    if ($('kbStyle')) return;
    var css = ''
      + '#content-digital-human-koubo{background:#f8fafc;color:#334155;min-height:100%;padding:16px 20px 28px;box-sizing:border-box;font-size:13px;}'
      + '#content-digital-human-koubo .kb-root{display:grid;grid-template-columns:340px minmax(0,1fr);gap:20px;align-items:start;max-width:1600px;margin:0 auto;}'
      + '@media(max-width:980px){#content-digital-human-koubo .kb-root{grid-template-columns:1fr;}}'
      + '#content-digital-human-koubo .kb-side{display:flex;flex-direction:column;gap:12px;position:sticky;top:12px;max-height:calc(100vh - 24px);overflow-y:auto;padding-right:2px;}'
      + '#content-digital-human-koubo .kb-side-head{display:flex;align-items:center;justify-content:space-between;padding:2px 4px 6px;}'
      + '#content-digital-human-koubo .kb-side-head h3{margin:0;font-size:20px;font-weight:500;letter-spacing:-.02em;color:#0f172a;}'
      + '#content-digital-human-koubo .kb-pill{font-size:11px;padding:3px 9px;border-radius:999px;background:#eef2ff;color:#4f46e5;border:1px solid #e0e7ff;}'
      + '#content-digital-human-koubo .kb-pill.ok{background:#ecfdf5;color:#059669;border-color:#a7f3d0;}'
      + '#content-digital-human-koubo .kb-pill.warn{background:#fffbeb;color:#b45309;border-color:#fde68a;}'
      + '#content-digital-human-koubo .kb-card{background:#fff;border:1px solid #eef2f7;border-radius:16px;padding:14px;box-shadow:0 4px 20px -4px rgba(15,23,42,.04);}'
      + '#content-digital-human-koubo .kb-sec-title{display:flex;align-items:center;justify-content:space-between;font-size:13px;font-weight:500;color:#0f172a;margin-bottom:10px;}'
      + '#content-digital-human-koubo .kb-sec-title small{font-weight:400;font-size:11px;color:#94a3b8;}'
      + '#content-digital-human-koubo .kb-drop{border:1.5px dashed #dbe3ef;border-radius:14px;background:#fbfdff;padding:18px 12px;text-align:center;cursor:pointer;transition:.15s;}'
      + '#content-digital-human-koubo .kb-drop:hover{border-color:#93c5fd;background:#f5f9ff;}'
      + '#content-digital-human-koubo .kb-drop b{display:block;font-size:13px;color:#334155;font-weight:500;}'
      + '#content-digital-human-koubo .kb-drop small{display:block;margin-top:4px;font-size:11px;color:#94a3b8;}'
      + '#content-digital-human-koubo .kb-photo{width:100%;max-height:230px;object-fit:contain;border-radius:12px;background:#0f172a;display:block;}'
      + '#content-digital-human-koubo .kb-checks{margin:8px 0 0;padding:0;list-style:none;font-size:11px;color:#64748b;line-height:1.8;}'
      + '#content-digital-human-koubo .kb-checks .ok{color:#059669;}'
      + '#content-digital-human-koubo .kb-checks .warn{color:#b45309;}'
      + '#content-digital-human-koubo textarea{width:100%;min-height:120px;box-sizing:border-box;padding:10px 12px;background:#fbfdff;border:1px solid #e2e8f0;border-radius:12px;color:#0f172a;font-size:13px;line-height:1.7;resize:vertical;outline:none;}'
      + '#content-digital-human-koubo textarea:focus{border-color:#93c5fd;background:#fff;}'
      + '#content-digital-human-koubo select,#content-digital-human-koubo input[type=text]{width:100%;box-sizing:border-box;padding:7px 9px;background:#fff;border:1px solid #e2e8f0;border-radius:9px;color:#334155;font-size:12px;outline:none;}'
      + '#content-digital-human-koubo select:focus,#content-digital-human-koubo input[type=text]:focus{border-color:#93c5fd;}'
      + '#content-digital-human-koubo .kb-field{display:flex;flex-direction:column;gap:5px;font-size:11px;color:#64748b;}'
      + '#content-digital-human-koubo .kb-grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px;}'
      + '#content-digital-human-koubo .kb-collapse{display:flex;align-items:center;justify-content:space-between;width:100%;background:none;border:0;padding:0;cursor:pointer;font-size:13px;font-weight:500;color:#0f172a;}'
      + '#content-digital-human-koubo .kb-collapse span{font-weight:400;font-size:11px;color:#94a3b8;}'
      + '#content-digital-human-koubo .kb-params{display:none;margin-top:12px;padding-top:12px;border-top:1px solid #f1f5f9;flex-direction:column;gap:10px;}'
      + '#content-digital-human-koubo .kb-params.open{display:flex;}'
      + '#content-digital-human-koubo .kb-pack{display:flex;flex-wrap:wrap;gap:6px 12px;margin-top:6px;}'
      + '#content-digital-human-koubo .kb-pack label{display:inline-flex;align-items:center;gap:4px;font-size:11px;color:#475569;}'
      + '#content-digital-human-koubo .kb-btn{border:1px solid #e2e8f0;background:#fff;color:#475569;border-radius:9px;padding:7px 12px;font-size:12px;cursor:pointer;transition:.15s;}'
      + '#content-digital-human-koubo .kb-btn:hover{border-color:#93c5fd;color:#2563eb;}'
      + '#content-digital-human-koubo .kb-btn:disabled{opacity:.5;cursor:default;}'
      + '#content-digital-human-koubo .kb-btn-primary{width:100%;border:0;border-radius:12px;padding:11px 14px;font-size:13px;font-weight:500;color:#fff;background:linear-gradient(135deg,#3b82f6,#2563eb);cursor:pointer;box-shadow:0 6px 16px -6px rgba(37,99,235,.6);}'
      + '#content-digital-human-koubo .kb-btn-primary:disabled{background:#cbd5e1;box-shadow:none;cursor:default;}'
      + '#content-digital-human-koubo .kb-cta{display:flex;flex-direction:column;gap:8px;position:sticky;bottom:0;padding:10px 0 2px;background:linear-gradient(to top,#f8fafc 72%,rgba(248,250,252,0));}'
      + '#content-digital-human-koubo .kb-cta-row{display:flex;gap:8px;}'
      + '#content-digital-human-koubo .kb-cta-row .kb-btn{flex:1;}'
      + '#content-digital-human-koubo .kb-hint{font-size:11px;color:#94a3b8;text-align:center;}'
      + '#content-digital-human-koubo .kb-stage{display:flex;flex-direction:column;gap:14px;min-width:0;}'
      + '#content-digital-human-koubo .kb-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:2px 2px 0;}'
      + '#content-digital-human-koubo .kb-crumb{font-size:12px;color:#94a3b8;}'
      + '#content-digital-human-koubo .kb-crumb b{color:#0f172a;font-weight:500;}'
      + '#content-digital-human-koubo .kb-tools{display:flex;gap:8px;flex-wrap:wrap;}'
      + '#content-digital-human-koubo .kb-preview{position:relative;background:#0f172a;border-radius:18px;min-height:380px;display:flex;align-items:center;justify-content:center;overflow:hidden;}'
      + '#content-digital-human-koubo .kb-preview video{width:100%;height:100%;max-height:520px;object-fit:contain;background:#000;}'
      + '#content-digital-human-koubo .kb-empty{color:#64748b;font-size:13px;text-align:center;line-height:1.9;padding:24px;}'
      + '#content-digital-human-koubo .kb-empty b{display:block;color:#e2e8f0;font-size:15px;font-weight:500;margin-bottom:4px;}'
      + '#content-digital-human-koubo .kb-progress{position:absolute;left:16px;right:16px;bottom:14px;background:rgba(15,23,42,.72);backdrop-filter:blur(6px);border:1px solid rgba(148,163,184,.25);border-radius:10px;padding:8px 12px;color:#e2e8f0;font-size:12px;}'
      + '#content-digital-human-koubo .kb-progress .bar{height:4px;background:rgba(148,163,184,.25);border-radius:999px;overflow:hidden;margin-top:6px;}'
      + '#content-digital-human-koubo .kb-progress .bar i{display:block;height:100%;background:#60a5fa;width:0;transition:width .3s;}'
      + '#content-digital-human-koubo .kb-meta{display:flex;gap:8px;flex-wrap:wrap;font-size:11px;color:#64748b;}'
      + '#content-digital-human-koubo .kb-chip{background:#fff;border:1px solid #eef2f7;border-radius:999px;padding:4px 10px;}'
      + '#content-digital-human-koubo table{width:100%;border-collapse:collapse;font-size:12px;}'
      + '#content-digital-human-koubo th,#content-digital-human-koubo td{text-align:left;padding:7px 8px;border-bottom:1px solid #f1f5f9;vertical-align:top;color:#475569;}'
      + '#content-digital-human-koubo th{color:#94a3b8;font-weight:400;font-size:11px;}'
      + '#content-digital-human-koubo .kb-total{display:flex;justify-content:space-between;align-items:center;margin-top:10px;font-size:12px;color:#64748b;}'
      + '#content-digital-human-koubo .kb-total b{color:#0f172a;font-size:14px;}'
      + '#content-digital-human-koubo .kb-packbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:12px;color:#64748b;}'
      + '#content-digital-human-koubo .kb-packbar select{padding:5px 8px;border:1px solid #e2e8f0;border-radius:8px;font-size:12px;color:#334155;background:#fff;}'
      + '#content-digital-human-koubo .kb-packbar label{display:inline-flex;align-items:center;gap:4px;font-size:11px;}'
      + '#content-digital-human-koubo .kb-proj{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:9px 2px;border-bottom:1px solid #f1f5f9;font-size:12px;}'
      + '#content-digital-human-koubo .kb-proj:last-child{border-bottom:0;}'
      + '#content-digital-human-koubo .kb-proj b{color:#0f172a;font-weight:500;}'
      + '#content-digital-human-koubo .kb-proj small{color:#94a3b8;font-size:11px;}'
      + '#content-digital-human-koubo .kb-log{margin-top:2px;padding:10px 12px;background:#fff;border:1px solid #eef2f7;border-radius:12px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px;color:#94a3b8;max-height:120px;overflow:auto;white-space:pre-wrap;}'
      + '#kbDepsModal{position:fixed;inset:0;z-index:3200;}'
      + '#kbDepsModal .kb-modal-backdrop{position:absolute;inset:0;background:rgba(15,23,42,.45);}'
      + '#kbDepsModal .kb-modal-card{position:relative;width:min(92vw,560px);margin:10vh auto 0;border-radius:20px;background:#fff;color:#0f172a;box-shadow:0 32px 80px rgba(15,23,42,.28);overflow:hidden;}'
      + '#kbDepsModal .kb-modal-head{display:flex;align-items:center;justify-content:space-between;padding:18px 22px 10px;}'
      + '#kbDepsModal .kb-modal-head h4{margin:0;font-size:17px;font-weight:500;}'
      + '#kbDepsModal .kb-modal-close{border:none;background:transparent;font-size:22px;line-height:1;cursor:pointer;color:#94a3b8;}'
      + '#kbDepsModal .kb-modal-body{padding:0 22px 12px;max-height:min(66vh,600px);overflow:auto;}'
      + '#kbDepsModal .kb-modal-sub{font-size:12px;color:#64748b;margin:0 0 10px;line-height:1.6;}'
      + '#kbDepsModal .kb-dep{display:grid;grid-template-columns:1fr auto;gap:3px 10px;padding:8px 0;border-bottom:1px dashed #eef2f7;}'
      + '#kbDepsModal .kb-dep:last-child{border-bottom:0;}'
      + '#kbDepsModal .kb-dep b{font-size:13px;font-weight:500;}'
      + '#kbDepsModal .kb-dep small{display:block;color:#94a3b8;font-size:11px;margin-top:2px;line-height:1.5;word-break:break-all;}'
      + '#kbDepsModal .kb-dep-right{display:flex;align-items:center;gap:6px;}'
      + '#kbDepsModal .kb-pill{font-size:11px;padding:2px 8px;border-radius:999px;border:1px solid transparent;}'
      + '#kbDepsModal .kb-pill.ok{background:#ecfdf5;color:#059669;border-color:#a7f3d0;}'
      + '#kbDepsModal .kb-pill.missing{background:#fef2f2;color:#dc2626;border-color:#fecaca;}'
      + '#kbDepsModal .kb-pill.installing{background:#eff6ff;color:#2563eb;border-color:#bfdbfe;}'
      + '#kbDepsModal .kb-bar{grid-column:1/-1;height:4px;background:#eef2f7;border-radius:999px;overflow:hidden;display:none;}'
      + '#kbDepsModal .kb-bar.on{display:block;}'
      + '#kbDepsModal .kb-bar i{display:block;height:100%;background:#3b82f6;width:0;transition:width .3s;}'
      + '#kbDepsModal .kb-modal-foot{display:flex;justify-content:space-between;gap:10px;padding:12px 22px 18px;border-top:1px solid #f1f5f9;}'
      + '#kbDepsModal .kb-btn{font-size:13px;padding:7px 14px;border-radius:9px;border:1px solid #e2e8f0;background:#fff;cursor:pointer;}'
      + '#kbDepsModal .kb-btn.primary{background:linear-gradient(135deg,#3b82f6,#2563eb);border-color:transparent;color:#fff;}'
      + '#kbDepsModal .kb-install{font-size:12px;padding:4px 10px;border-radius:7px;border:1px solid #e2e8f0;background:#fff;cursor:pointer;}';
    var style = document.createElement('style');
    style.id = 'kbStyle';
    style.textContent = css;
    document.head.appendChild(style);
  }

  function shell() {
    return '<div class="kb-root">'
      + '<aside class="kb-side">'
      + '  <div class="kb-side-head"><h3>数字人3.0</h3><span class="kb-pill" id="kbStatus">未保存</span></div>'
      + '  <div class="kb-card"><div class="kb-sec-title">形象<small>一张正脸照</small></div><div id="kbAvatar"></div></div>'
      + '  <div class="kb-card"><div class="kb-sec-title">口播稿<small id="kbCount">0 字</small></div><textarea id="kbText" placeholder="粘贴口播稿。按句切分，每段不超过约 15 秒。"></textarea></div>'
      + '  <div class="kb-card"><button type="button" class="kb-collapse" id="kbParamToggle">创作参数<span id="kbParamSummary">默认</span></button><div class="kb-params" id="kbParams"></div></div>'
      + '  <div class="kb-cta"><button type="button" class="kb-btn-primary" id="kbGenerate">生成第 1 段</button>'
      + '    <div class="kb-cta-row"><button type="button" class="kb-btn" id="kbQuote">只看分段报价</button><button type="button" class="kb-btn" id="kbGenerateAll">全部生成</button></div>'
      + '    <div class="kb-hint">按次扣算力，建议先出第 1 段确认口型与音色</div></div>'
      + '</aside>'
      + '<section class="kb-stage">'
      + '  <div class="kb-toolbar"><div class="kb-crumb">工作台 / <b id="kbProjName">未命名项目</b></div>'
      + '    <div class="kb-tools"><button type="button" class="kb-btn" id="kbSave">保存项目</button><button type="button" class="kb-btn" id="kbExport">导出 tasks.json</button><button type="button" class="kb-btn" id="kbDeps">安装依赖</button><button type="button" class="kb-btn" id="kbBack">返回商店</button></div></div>'
      + '  <div class="kb-preview" id="kbPreview"><div class="kb-empty"><b>还没有成片</b>左侧填好形象与口播稿，点击「生成第 1 段」</div></div>'
      + '  <div class="kb-meta" id="kbMeta"></div>'
      + '  <div id="kbPack"></div>'
      + '  <div class="kb-card"><div class="kb-sec-title">分段<small id="kbSegCount">0 段</small></div><div id="kbSegments"><span style="font-size:12px;color:#94a3b8;">还没有分段，先点「只看分段报价」或直接生成。</span></div></div>'
      + '  <div class="kb-card"><div class="kb-sec-title">项目记录<small id="kbProjCount"></small></div><div id="kbProjects"></div></div>'
      + '  <div class="kb-log" id="kbLog">准备就绪</div>'
      + '</section></div>';
  }

  function optList(key, value) {
    var arr = (state.options && state.options[key]) || [];
    return arr.map(function (o) {
      var v = (o && o.id != null) ? o.id : o;
      var label = (o && o.name) ? o.name : v;
      return '<option value="' + esc(v) + '"' + (String(v) === String(value) ? ' selected' : '') + '>' + esc(label) + '</option>';
    }).join('');
  }

  function renderAvatar() {
    var box = $('kbAvatar');
    if (!box) return;
    if (!state.upload) {
      box.innerHTML = '<div class="kb-drop" id="kbDrop"><b>点击或拖入人物照片</b><small>jpg / png / webp，≤ 15MB，短边 ≥ 512</small><input type="file" id="kbFile" accept="image/jpeg,image/png,image/webp" style="display:none;"></div>';
      bindUpload();
      return;
    }
    var c = state.upload.checks || {};
    var hasChecks = !!(c && c.width);
    box.innerHTML = '<img class="kb-photo" src="' + esc(API + state.upload.url) + '" alt="形象照">'
      + (hasChecks
        ? '<ul class="kb-checks">'
          + '<li class="' + (Math.min(c.width || 0, c.height || 0) >= 512 ? 'ok' : 'warn') + '">分辨率 ' + (c.width || 0) + '×' + (c.height || 0) + '</li>'
          + '<li class="' + ((c.sharpness || 0) >= 25 ? 'ok' : 'warn') + '">清晰度 ' + (c.sharpness || 0) + '</li>'
          + '<li class="' + ((c.brightness || 0) >= 60 && (c.brightness || 0) <= 215 ? 'ok' : 'warn') + '">亮度 ' + (c.brightness || 0) + '</li>'
          + (c.warnings || []).map(function (w) { return '<li class="warn">⚠ ' + esc(w) + '</li>'; }).join('')
          + '</ul>'
        : '<ul class="kb-checks"><li>已随项目保存</li></ul>')
      + '<div style="margin-top:8px;"><button type="button" class="kb-btn" id="kbReplace">换一张</button></div>';
    var rep = $('kbReplace');
    if (rep) rep.onclick = function () { state.upload = null; renderAvatar(); };
  }

  function bindUpload() {
    var drop = $('kbDrop'), file = $('kbFile');
    if (!drop || !file) return;
    drop.addEventListener('click', function () { file.click(); });
    drop.addEventListener('dragover', function (e) { e.preventDefault(); drop.style.borderColor = '#93c5fd'; });
    drop.addEventListener('dragleave', function () { drop.style.borderColor = ''; });
    drop.addEventListener('drop', function (e) {
      e.preventDefault(); drop.style.borderColor = '';
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]);
    });
    file.addEventListener('change', function () { if (file.files && file.files[0]) upload(file.files[0]); });
  }

  function renderParams() {
    var box = $('kbParams');
    if (!box) return;
    var f = state.form;
    var isCustom = f.scene === 'custom';
    var packs = (state.options && state.options.packaging) || [];
    var groups = {};
    packs.forEach(function (o) { (groups[o.group] = groups[o.group] || []).push(o); });
    box.innerHTML = ''
      + '<div class="kb-grid2">'
      + '  <label class="kb-field">主播<select id="kbHost">' + optList('hosts', f.host) + '</select></label>'
      + '  <label class="kb-field">景别<select id="kbShot">' + optList('shots', f.shot) + '</select></label>'
      + '  <label class="kb-field">比例<select id="kbRatio"><option value="">跟随主播</option>' + ((state.options && state.options.ratios) || ['9:16', '16:9']).map(function (r) { return '<option value="' + esc(r) + '"' + (f.ratio === r ? ' selected' : '') + '>' + esc(r) + '</option>'; }).join('') + '</select></label>'
      + '  <label class="kb-field">分辨率<select id="kbResolution"><option value="">跟随景别</option>' + ((state.options && state.options.resolutions) || ['720p', '480p']).map(function (r) { return '<option value="' + esc(r) + '"' + (f.resolution === r ? ' selected' : '') + '>' + esc(r) + '</option>'; }).join('') + '</select></label>'
      + '  <label class="kb-field">语言<select id="kbLanguage">' + optList('languages', f.language) + '</select></label>'
      + '  <label class="kb-field">模型<select id="kbModel">' + optList('models', f.model) + '</select></label>'
      + '  <label class="kb-field">场景<select id="kbScene">' + optList('scenes', f.scene) + '</select></label>'
      + '  <label class="kb-field">音色描述<input type="text" id="kbVoice" value="' + esc(f.voice || '') + '" placeholder="留空用默认"></label>'
      + '</div>'
      + '<label class="kb-field" id="kbSceneWrap" style="display:' + (isCustom ? 'flex' : 'none') + ';">场景描述<input type="text" id="kbScenePrompt" value="' + esc(f.scene_prompt || '') + '" placeholder="例如：夜晚霓虹街头，浅景深"></label>'
      + '<label style="display:flex;align-items:center;gap:6px;font-size:11px;color:#64748b;"><input type="checkbox" id="kbCompose"' + (f.compose_scene ? ' checked' : '') + '> 先合成场景图（额外 1 次图片生成，失败回退原图）</label>'
      + '<div><div style="font-size:12px;font-weight:500;color:#0f172a;margin-bottom:2px;">后期包装技巧</div>'
      + Object.keys(groups).map(function (g) {
          return '<div style="margin-top:6px;font-size:11px;color:#94a3b8;">' + esc(g) + '</div><div class="kb-pack">'
            + groups[g].map(function (o) {
                return '<label><input type="checkbox" data-pack="' + esc(o.id) + '"' + ((state.form.packaging || []).indexOf(o.id) >= 0 ? ' checked' : '') + '> ' + esc(o.name) + '</label>';
              }).join('') + '</div>';
        }).join('')
      + '</div>';
    bindParams();
    updateParamSummary();
  }

  function readParams() {
    state.form = {
      host: ($('kbHost') || {}).value || 'tank',
      shot: ($('kbShot') || {}).value || 'full',
      ratio: ($('kbRatio') || {}).value || '',
      resolution: ($('kbResolution') || {}).value || '',
      language: ($('kbLanguage') || {}).value || 'Mandarin Chinese',
      model: ($('kbModel') || {}).value || '',
      scene: ($('kbScene') || {}).value || 'none',
      scene_prompt: ($('kbScenePrompt') || {}).value || '',
      voice: ($('kbVoice') || {}).value || '',
      compose_scene: !!($('kbCompose') && $('kbCompose').checked),
      packaging: Array.prototype.slice.call(document.querySelectorAll('[data-pack]:checked')).map(function (el) { return el.getAttribute('data-pack'); })
    };
    updateParamSummary();
  }

  function bindParams() {
    var box = $('kbParams');
    if (!box) return;
    box.addEventListener('change', function (ev) {
      if (ev && ev.target && ev.target.id === 'kbHost') {
        var h = ((state.options && state.options.hosts) || []).filter(function (x) { return x.id === ($('kbHost') || {}).value; })[0];
        if (h && !(($('kbRatio') || {}).value)) { var sel = $('kbRatio'); if (sel) sel.value = h.ratio || ''; }
      }
      readParams();
      var wrap = $('kbSceneWrap');
      if (wrap) wrap.style.display = state.form.scene === 'custom' ? 'flex' : 'none';
    });
    box.addEventListener('input', function () { readParams(); });
  }

  function updateParamSummary() {
    var el = $('kbParamSummary');
    if (!el) return;
    var f = state.form || {};
    var shot = ((state.options && state.options.shots) || []).filter(function (x) { return x.id === f.shot; })[0];
    el.textContent = (shot ? shot.name : '全屏') + (f.ratio ? ' · ' + f.ratio : '') + (f.packaging && f.packaging.length ? ' · 包装 ' + f.packaging.length : '');
  }

  function renderMeta() {
    var el = $('kbMeta');
    if (!el) return;
    var q = state.quote || (state.project && state.project.segments ? state.project : null);
    if (!q) { el.innerHTML = ''; return; }
    var f = state.form || {};
    var host = ((state.options && state.options.hosts) || []).filter(function (x) { return x.id === f.host; })[0];
    var scene = ((state.options && state.options.scenes) || []).filter(function (x) { return x.id === f.scene; })[0];
    var chips = [];
    chips.push('<span class="kb-chip">' + esc(host ? host.name : '白背心') + '</span>');
    chips.push('<span class="kb-chip">' + (q.total_seconds || 0) + ' 秒</span>');
    chips.push('<span class="kb-chip">' + (q.total_credits || 0) + ' credits</span>');
    if (f.language) chips.push('<span class="kb-chip">' + esc(f.language) + '</span>');
    if (scene && f.scene !== 'none') chips.push('<span class="kb-chip">场景 · ' + esc(scene.name) + (f.compose_scene ? '（合成）' : '') + '</span>');
    el.innerHTML = chips.join('');
  }

  function renderSegments() {
    var box = $('kbSegments');
    if (!box) return;
    var q = state.quote || (state.project && state.project.segments ? state.project : null);
    if (!q || !q.segments || !q.segments.length) {
      box.innerHTML = '<span style="font-size:12px;color:#94a3b8;">还没有分段，先点「只看分段报价」或直接生成。</span>';
      if ($('kbSegCount')) $('kbSegCount').textContent = '0 段';
      return;
    }
    var rows = q.segments.map(function (s) {
      return '<tr><td>' + esc(s.id) + '</td><td>' + esc(s.text) + '</td><td>' + s.seconds + 's</td><td>' + esc(s.resolution) + '</td><td>' + s.credits + '</td></tr>';
    }).join('');
    box.innerHTML = '<table><thead><tr><th>段</th><th>台词</th><th>时长</th><th>分辨率</th><th>credits</th></tr></thead><tbody>' + rows + '</tbody></table>'
      + '<div class="kb-total"><span>共 ' + q.segments.length + ' 段 · ' + (q.total_seconds || 0) + ' 秒</span><b>' + (q.total_credits || 0) + ' credits</b></div>';
    if ($('kbSegCount')) $('kbSegCount').textContent = q.segments.length + ' 段';
  }

  function renderQaResult(q) {
    if (!q.ok) return '<div style="margin-top:8px;font-size:11px;color:#dc2626;">' + esc(q.error || '质检失败') + '</div>';
    var rows = (q.segments || []).map(function (x) {
      return '<tr><td>' + esc(x.id) + '</td><td>' + esc(x.text) + '</td><td style="color:' + (x.warn ? '#dc2626' : '#059669') + ';">' + Math.round(x.acc * 100) + '%</td><td style="font-size:11px;color:#64748b;">' + esc(x.heard || '') + '</td></tr>';
    }).join('');
    return '<div style="margin-top:10px;"><div style="font-size:12px;color:#0f172a;margin-bottom:6px;">质检结果 · 平均 ' + Math.round((q.overall || 0) * 100) + '%</div>'
      + '<table><thead><tr><th>段</th><th>文稿</th><th>准确率</th><th>听到</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
  }

  function runQa() {
    if (!state.project) return;
    var btn = $('kbQa');
    if (btn) { btn.disabled = true; btn.textContent = '质检中…'; }
    log('开始质检（whisper 回听）…');
    req('/api/skills/digital-human-koubo/projects/' + state.project.id + '/qa', { method: 'POST', headers: headers() })
      .then(function (d) {
        state.qa = d;
        log(d.ok ? '质检完成，平均 ' + Math.round((d.overall || 0) * 100) + '%' : '质检失败：' + d.error);
        renderPack();
      })
      .catch(function (e) { log('质检失败：' + e.message); if (btn) { btn.disabled = false; btn.textContent = '开始质检'; } });
  }

  function renderPack() {
    var box = $('kbPack');
    if (!box) return;
    if (!state.project || state.project.status !== 'generated') { box.innerHTML = ''; return; }
    var r = state.render;
    var job = r.job && r.job.status === 'running' ? r.job : null;
    var nPacks = (state.form.packaging || []).length;
    box.innerHTML = '<div class="kb-card"><div class="kb-sec-title">包装<small>客户端本地渲染 · 不用 Remotion</small></div>'
      + '<div class="kb-packbar">'
      + '<select id="kbPreset"><option' + (r.preset === '克制' ? ' selected' : '') + '>克制</option><option' + (r.preset === '标准' ? ' selected' : '') + '>标准</option><option' + (r.preset === '热闹' ? ' selected' : '') + '>热闹</option></select>'
      + '<span style="font-size:11px;color:#64748b;">已选 ' + nPacks + ' 个包装组件（在左侧「创作参数」里改）</span>'
      + '<span style="flex:1"></span>'
      + (r.packaged ? '<a class="kb-btn" href="' + esc(API + '/api/skills/digital-human-koubo/projects/' + state.project.id + '/rendered') + '" download>下载包装成片</a>' : '')
      + '<button type="button" class="kb-btn" id="kbRender"' + (job ? ' disabled' : '') + '>' + (job ? '渲染中 ' + (job.progress || 0) + '%' : (r.packaged ? '重新包装' : '生成包装成片')) + '</button>'
      + '</div>'
      + (job ? '<div style="margin-top:8px;font-size:11px;color:#64748b;">' + esc(job.message || '') + '<div style="height:4px;background:#eef2f7;border-radius:999px;overflow:hidden;margin-top:4px;"><i style="display:block;height:100%;width:' + Math.max(3, job.progress || 0) + '%;background:#3b82f6;"></i></div></div>' : '')
      + '<div class="kb-packbar" style="margin-top:10px;border-top:1px solid #f1f5f9;padding-top:10px;">'
      + '<button type="button" class="kb-btn" id="kbQa">开始质检</button><span style="font-size:11px;color:#94a3b8;">whisper 回听，逐段比对文稿（低于 85% 建议重拍）</span></div>'
      + (state.qa ? renderQaResult(state.qa) : '')
      + '</div>';
    var sel = $('kbPreset');
    if (sel) sel.onchange = function () { state.render.preset = sel.value; };
    var btn = $('kbRender');
    if (btn) btn.onclick = function () { startRender(); };
    var qaBtn = $('kbQa');
    if (qaBtn) qaBtn.onclick = runQa;
  }

  function startRender() {
    if (!state.project) return;
    state.render.job = { status: 'running', progress: 0, message: '准备渲染' };
    renderPack();
    req('/api/skills/digital-human-koubo/projects/' + state.project.id + '/render', {
      method: 'POST', headers: headers(),
      body: JSON.stringify({
        preset: state.render.preset,
        components: ['caption', 'cta', 'lowerthird', 'punch'].concat(state.form.packaging || [])
      })
    }).then(function (d) {
      log('包装任务已提交 ' + d.job_id);
      pollRender(d.job_id);
    }).catch(function (e) {
      state.render.job = null;
      renderPack();
      log('包装失败：' + e.message);
    });
  }

  function pollRender(jobId) {
    var t = setInterval(function () {
      req('/api/skills/digital-human-koubo/render/jobs/' + encodeURIComponent(jobId)).then(function (job) {
        state.render.job = job;
        renderPack();
        if (job.status !== 'running') {
          clearInterval(t);
          if (job.status === 'done') {
            state.render.packaged = true;
            state.render.job = null;
            log('包装完成');
            req('/api/skills/digital-human-koubo/projects/' + state.project.id).then(function (p) { state.project = p; renderAll(); });
          } else {
            log('包装失败：' + (job.error || job.message || ''));
          }
        }
      }).catch(function () {});
    }, 1500);
  }

  function renderPreview() {
    var box = $('kbPreview');
    if (!box) return;
    if (state.job) {
      var pct = state.job.total ? Math.round((state.job.done || 0) * 100 / state.job.total) : (state.job.progress || 0);
      if (state.job.status === 'running') {
        box.innerHTML = '<div class="kb-empty"><b>' + esc(state.job.message || '生成中') + '</b>任务在服务器排队，完成后自动出现在这里</div>'
          + '<div class="kb-progress">' + esc(state.job.message || '') + ' · ' + pct + '%<div class="bar"><i style="width:' + Math.max(3, pct) + '%"></i></div></div>';
        return;
      }
      if (state.job.status === 'error') {
        box.innerHTML = '<div class="kb-empty"><b>生成失败</b>' + esc(state.job.error || state.job.message || '未知错误') + '</div>';
        return;
      }
    }
    if (state.project && state.project.status === 'generated') {
      var packaged = state.render.packaged || state.project.rendered;
      var src = API + '/api/skills/digital-human-koubo/projects/' + state.project.id + (packaged ? '/rendered' : '/video');
      box.innerHTML = '<video controls playsinline src="' + esc(src) + '"></video>'
        + (packaged ? '<div style="position:absolute;left:14px;top:12px;background:rgba(15,23,42,.7);color:#e2e8f0;font-size:11px;padding:4px 9px;border-radius:999px;">包装版</div><a href="' + esc(API + '/api/skills/digital-human-koubo/projects/' + state.project.id + '/video') + '" target="_blank" style="position:absolute;right:14px;top:12px;color:#cbd5e1;font-size:11px;text-decoration:underline;">看原片</a>' : '');
      return;
    }
    box.innerHTML = '<div class="kb-empty"><b>还没有成片</b>左侧填好形象与口播稿，点击「生成第 1 段」</div>';
  }

  function renderStatus() {
    var el = $('kbStatus');
    if (!el) return;
    if (state.job && state.job.status === 'running') { el.textContent = '生成中'; el.className = 'kb-pill warn'; return; }
    if (state.project && state.project.status === 'generated') { el.textContent = '已出片'; el.className = 'kb-pill ok'; return; }
    if (state.project && state.project.id) { el.textContent = '已保存'; el.className = 'kb-pill'; return; }
    el.textContent = '未保存'; el.className = 'kb-pill';
  }

  function renderProjects() {
    var box = $('kbProjects');
    if (!box) return;
    if ($('kbProjCount')) $('kbProjCount').textContent = state.projects.length ? state.projects.length + ' 个' : '';
    if (!state.projects.length) {
      box.innerHTML = '<span style="font-size:12px;color:#94a3b8;">还没有项目。生成或保存后会出现在这里。</span>';
      return;
    }
    box.innerHTML = state.projects.map(function (p) {
      return '<div class="kb-proj"><div><b>' + esc(p.name) + '</b><br><small>' + esc(String(p.created_at || '').replace('T', ' ')) + ' · ' + (p.total_credits || 0) + ' credits · ' + esc(p.status || '') + '</small></div>'
        + '<div style="display:flex;gap:6px;"><button type="button" class="kb-btn" data-open="' + esc(p.id) + '">打开</button><button type="button" class="kb-btn" data-del="' + esc(p.id) + '">删除</button></div></div>';
    }).join('');
    box.querySelectorAll('[data-open]').forEach(function (btn) {
      btn.onclick = function () { openProject(btn.getAttribute('data-open')); };
    });
    box.querySelectorAll('[data-del]').forEach(function (btn) {
      btn.onclick = function () {
        var pid = btn.getAttribute('data-del');
        if (!window.confirm('删除这个项目？')) return;
        fetch(API + '/api/skills/digital-human-koubo/projects/' + encodeURIComponent(pid), { method: 'DELETE', headers: authOnly() })
          .then(function () { log('已删除 ' + pid); if (state.project && state.project.id === pid) { state.project = null; renderAll(); } loadProjects(); });
      };
    });
  }

  function renderAll() {
    renderAvatar(); renderParams(); renderSegments(); renderPreview(); renderStatus(); renderMeta(); renderPack(); renderProjects();
  }

  function updateCount() {
    var ta = $('kbText'), el = $('kbCount');
    if (!ta || !el) return;
    var n = (ta.value || '').replace(/\s/g, '').length;
    el.textContent = n + ' 字 · 约 ' + Math.max(1, Math.ceil(n / 5.4)) + ' 秒';
  }

  function upload(f) {
    var fd = new FormData();
    fd.append('file', f);
    log('上传形象照 ' + f.name);
    fetch(API + '/api/skills/digital-human-koubo/uploads', { method: 'POST', headers: authOnly(), body: fd })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail)); return d; }); })
      .then(function (d) { state.upload = d; log('形象照已上传 ' + d.checks.width + '×' + d.checks.height); renderAvatar(); })
      .catch(function (e) { log('上传失败：' + e.message); });
  }

  function quote(goNext) {
    var text = ($('kbText') || {}).value || '';
    if (!text.trim()) { log('请先粘贴口播稿'); return; }
    readParams();
    log('生成分段与报价…');
    return req('/api/skills/digital-human-koubo/quote', {
      method: 'POST', headers: headers(),
      body: JSON.stringify({ text: text, shot: state.form.shot, resolution: state.form.resolution || '' })
    }).then(function (d) {
      state.quote = d;
      state.script = text;
      renderSegments(); renderMeta();
      log('报价：' + d.segments.length + ' 段 / ' + d.total_credits + ' credits');
      return d;
    }).catch(function (e) { log('报价失败：' + e.message); throw e; });
  }

  function saveProject() {
    if (!state.quote) return Promise.reject(new Error('先报价'));
    var text = ($('kbText') || {}).value || '';
    return req('/api/skills/digital-human-koubo/projects', {
      method: 'POST', headers: headers(),
      body: JSON.stringify({
        name: '', script: text, shot: state.form.shot, host: state.form.host,
        photo_id: state.upload ? state.upload.id : '', options: state.form
      })
    }).then(function (p) {
      state.project = p;
      if ($('kbProjName')) $('kbProjName').textContent = p.name;
      renderStatus(); renderProjects(); loadProjects();
      log('项目已保存 ' + p.id);
      return p;
    });
  }

  function generate(all) {
    var btn = $('kbGenerate');
    if (btn) { btn.disabled = true; }
    var prep = state.project ? Promise.resolve(state.project) : quote(true).then(saveProject);
    prep.then(function (p) {
      log('提交生成（' + (all ? '全部' : '第 1 段') + '）…');
      return req('/api/skills/digital-human-koubo/projects/' + p.id + '/generate', {
        method: 'POST', headers: headers(), body: JSON.stringify({ all: !!all })
      });
    }).then(function (d) {
      state.job = { status: 'running', message: '准备提交', progress: 0, done: 0, total: all ? (state.quote ? state.quote.segments.length : 1) : 1 };
      renderAll();
      log('任务已提交 ' + d.job_id);
      pollJob(d.job_id);
    }).catch(function (e) {
      if (btn) btn.disabled = false;
      var msg = e && e.message ? e.message : '提交失败';
      if (/连接正在自动恢复/.test(msg)) msg = '本地服务刚重启，请 3 秒后重试';
      log('提交失败：' + msg);
      if (state.job) { state.job.status = 'error'; state.job.error = msg; }
      renderPreview();
    });
  }

  function pollJob(jobId) {
    if (state.timer) clearInterval(state.timer);
    state.timer = setInterval(function () {
      req('/api/skills/digital-human-koubo/generate/jobs/' + encodeURIComponent(jobId)).then(function (job) {
        state.job = job;
        renderPreview(); renderStatus();
        if (job.status !== 'running') {
          clearInterval(state.timer); state.timer = null;
          var btn = $('kbGenerate'); if (btn) btn.disabled = false;
          if (job.status === 'done') {
            log('生成完成');
            req('/api/skills/digital-human-koubo/projects/' + state.project.id).then(function (p) {
              state.project = p; renderAll();
            });
          } else {
            log('生成失败：' + (job.error || job.message || ''));
          }
        }
      }).catch(function () {});
    }, 2500);
  }

  function openProject(pid) {
    req('/api/skills/digital-human-koubo/projects/' + encodeURIComponent(pid)).then(function (p) {
      state.project = p;
      state.script = p.script || '';
      state.form = Object.assign({}, state.form, p.options || {});
      state.quote = { segments: p.segments || [], total_seconds: p.total_seconds || 0, total_credits: p.total_credits || 0 };
      state.job = null;
      state.render.packaged = !!p.rendered;
      if (p.photo_id) {
        state.upload = { id: p.photo_id, url: '/api/skills/digital-human-koubo/uploads/' + p.photo_id, checks: {} };
      }
      if ($('kbText')) $('kbText').value = p.script || '';
      if ($('kbProjName')) $('kbProjName').textContent = p.name;
      renderAll(); updateCount();
      log('打开项目 ' + p.id);
    }).catch(function (e) { log('打开失败：' + e.message); });
  }

  function exportTasks() {
    if (!state.project) { log('先保存项目'); return; }
    req('/api/skills/digital-human-koubo/projects/' + encodeURIComponent(state.project.id) + '/tasks').then(function (d) {
      var blob = new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' });
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'koubo-tasks-' + state.project.id + '.json';
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
      log('已导出 tasks.json');
    }).catch(function (e) { log('导出失败：' + e.message); });
  }

  function loadOptions() {
    return req('/api/skills/digital-human-koubo/options').then(function (d) {
      state.options = d;
      if (!state.form.model && d.models && d.models.length) state.form.model = d.models[0].id;
      renderParams(); updateParamSummary(); renderSegments(); renderMeta();
      return d;
    }).catch(function (e) { log('参数读取失败：' + e.message); });
  }

  function loadProjects() {
    req('/api/skills/digital-human-koubo/projects').then(function (d) {
      state.projects = d.projects || [];
      renderProjects();
    }).catch(function () {});
  }

  function loadStatus() {
    return req('/api/skills/digital-human-koubo/status').then(function (d) { state.components = d.components || []; return d; })
      .catch(function () {});
  }

  function pill(c) {
    if (c.status === 'ok') return '<span class="kb-pill ok">就绪' + (c.version ? ' ' + esc(c.version) : '') + '</span>';
    if (c.status === 'installing') return '<span class="kb-pill installing">安装中 ' + (c.progress || 0) + '%</span>';
    if (c.status === 'error') return '<span class="kb-pill missing">异常</span>';
    return '<span class="kb-pill missing">缺失</span>';
  }

  function renderModalRows() {
    var box = $('kbDepsBody');
    if (!box) return;
    box.innerHTML = state.components.map(function (c) {
      var btn = (c.installable && c.status !== 'ok' && c.status !== 'installing') ? '<button type="button" class="kb-install" data-dep="' + esc(c.id) + '">安装</button>' : '';
      var bar = c.status === 'installing' ? '<div class="kb-bar on"><i style="width:' + (c.progress || 0) + '%"></i></div>' : '';
      return '<div class="kb-dep"><div><b>' + esc(c.name) + '</b><small>' + esc(c.purpose) + ' · ' + esc(c.size) + '</small><small>' + esc(c.detail || '') + '</small></div><div class="kb-dep-right">' + pill(c) + btn + '</div>' + bar + '</div>';
    }).join('');
    box.querySelectorAll('[data-dep]').forEach(function (btn) {
      btn.onclick = function () { installDep(btn.getAttribute('data-dep')); };
    });
  }

  function openDeps() {
    if ($('kbDepsModal')) return;
    var m = document.createElement('div');
    m.id = 'kbDepsModal';
    m.innerHTML = '<div class="kb-modal-backdrop"></div><div class="kb-modal-card"><div class="kb-modal-head"><h4>运行环境与依赖</h4><button type="button" class="kb-modal-close">×</button></div>'
      + '<div class="kb-modal-body"><p class="kb-modal-sub">ffmpeg / Node / Python 已内置；whisper 只在回听与逐字对齐时使用，不装也能出片。下载走自有 TOS。</p><div id="kbDepsBody">加载中…</div></div>'
      + '<div class="kb-modal-foot"><button type="button" class="kb-btn" data-close>关闭</button></div></div>';
    document.body.appendChild(m);
    m.querySelector('.kb-modal-backdrop').onclick = closeDeps;
    m.querySelector('.kb-modal-close').onclick = closeDeps;
    m.querySelector('[data-close]').onclick = closeDeps;
    loadStatus().then(renderModalRows);
  }
  function closeDeps() { var m = $('kbDepsModal'); if (m && m.parentNode) m.parentNode.removeChild(m); }

  function installDep(id) {
    req('/api/skills/digital-human-koubo/deps/install', { method: 'POST', headers: headers(), body: JSON.stringify({ component: id }) })
      .then(function (d) { pollDep(d.job_id, id); })
      .catch(function (e) { log('安装失败：' + e.message); });
  }
  function pollDep(jobId, id) {
    var t = setInterval(function () {
      req('/api/skills/digital-human-koubo/deps/jobs/' + encodeURIComponent(jobId)).then(function (job) {
        var c = state.components.filter(function (x) { return x.id === id; })[0];
        if (c) { c.status = job.status === 'running' ? 'installing' : (job.status === 'done' ? 'ok' : 'error'); c.progress = job.progress; c.detail = job.message; renderModalRows(); }
        if (job.status !== 'running') { clearInterval(t); loadStatus().then(renderModalRows); }
      }).catch(function () {});
    }, 1200);
  }

  function bind() {
    var ta = $('kbText');
    if (ta) ta.addEventListener('input', updateCount);
    var tgl = $('kbParamToggle');
    if (tgl) tgl.onclick = function () { $('kbParams').classList.toggle('open'); };
    var q = $('kbQuote'); if (q) q.onclick = function () { quote(false); };
    var g = $('kbGenerate'); if (g) g.onclick = function () { generate(false); };
    var ga = $('kbGenerateAll'); if (ga) ga.onclick = function () { generate(true); };
    var sv = $('kbSave'); if (sv) sv.onclick = function () { quote(false).then(saveProject).catch(function () {}); };
    var ex = $('kbExport'); if (ex) ex.onclick = exportTasks;
    var dp = $('kbDeps'); if (dp) dp.onclick = openDeps;
    var bk = $('kbBack'); if (bk) bk.onclick = function () {
      if (typeof window._switchToHiddenView === 'function') window._switchToHiddenView('skill-store');
      else if (typeof showLobsterView === 'function') showLobsterView('skill-store');
    };
  }

  function ensureShell() {
    var root = document.getElementById('content-' + VIEW_ID);
    if (!root) return null;
    if (!root.dataset.kouboBuilt) {
      root.innerHTML = shell();
      root.dataset.kouboBuilt = '1';
      injectCss();
      bind();
      renderAll();
      updateCount();
    }
    return root;
  }

  window._openDigitalHumanKouboView = function () {
    if (typeof window._switchToHiddenView === 'function') window._switchToHiddenView(VIEW_ID);
    else if (typeof showLobsterView === 'function') showLobsterView(VIEW_ID);
    ensureShell();
    loadOptions();
    loadProjects();
    log('进入数字人3.0 工作台');
  };

  document.addEventListener('DOMContentLoaded', function () {
    var root = document.getElementById('content-' + VIEW_ID);
    if (root && !root.dataset.kouboBuilt) { ensureShell(); }
  });
})();
