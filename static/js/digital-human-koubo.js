(function () {
  'use strict';

  var VIEW_ID = 'digital-human-koubo';
  var API = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? LOCAL_API_BASE : '';
  var state = {
    options: null,
    form: { host: 'photo', shot: 'full', ratio: '', resolution: '', language: 'Mandarin Chinese', model: '', scene: 'none', scene_prompt: '', voice: '', compose_scene: false, packaging: [] },
    upload: null,
    quote: null,
    project: null,
    projects: [],
    preview: null,
    components: [],
    job: null,
    dirty: false,
    render: { preset: '标准', components: ['caption', 'keyword', 'cta', 'lowerthird', 'punch', 'chapter', 'stack', 'counter'], job: null, packaged: false, previewReady: false, showPreview: false, runningMode: '', qa: null, templateId: 'premium_kinetic_v1', templates: [], title: '', templateCategory: 'all', templateQuery: '', showAllTemplates: false, featuredPage: 0, recentTemplateIds: [], favoriteTemplateIds: [], recommendations: [], recommendFor: '', broll: null, brollOpen: false },
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
      + '#content-digital-human-koubo .kb-templates{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;}'
      + '#content-digital-human-koubo .kb-tpl{text-align:left;background:#fff;border:1px solid #eef2f7;border-radius:14px;padding:9px;cursor:pointer;transition:border-color .15s,box-shadow .15s,transform .15s;}'
      + '#content-digital-human-koubo .kb-tpl:hover{transform:translateY(-1px);box-shadow:0 8px 20px -10px rgba(15,23,42,.25);}'
      + '#content-digital-human-koubo .kb-tpl.on{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.12);}'
      + '#content-digital-human-koubo .kb-tpl-preview{display:block;height:58px;border-radius:10px;margin-bottom:8px;position:relative;overflow:hidden;}'
      + '#content-digital-human-koubo .kb-tpl-cover{display:block;width:100%;height:74px;object-fit:cover;border-radius:10px;margin-bottom:8px;background:#eef2f7;}'
      + '#content-digital-human-koubo .kb-tpl-coverwrap{position:relative;margin-bottom:8px;}'
      + '#content-digital-human-koubo .kb-tpl-coverwrap .kb-tpl-cover{margin-bottom:0;}'
      + '#content-digital-human-koubo .kb-tpl-play{position:absolute;right:6px;bottom:6px;border:0;border-radius:999px;padding:3px 8px;background:rgba(5,9,18,.72);color:#fff;font-size:10px;cursor:pointer;backdrop-filter:blur(6px);}'
      + '#content-digital-human-koubo .kb-tpl-play:hover{background:rgba(37,99,235,.9);}'
      + '#content-digital-human-koubo .kb-tpl-fav{position:absolute;right:6px;top:6px;width:22px;height:22px;border:1px solid rgba(255,255,255,.2);border-radius:50%;background:rgba(5,9,18,.58);color:#fff;font-size:13px;line-height:1;cursor:pointer;backdrop-filter:blur(6px);padding:0;}'
      + '#content-digital-human-koubo .kb-tpl-fav.on{color:#FFD84D;background:rgba(5,9,18,.8);}'
      + '#content-digital-human-koubo .kb-broll{border-top:1px solid #f1f5f9;margin-top:10px;padding-top:10px;}'
      + '#content-digital-human-koubo .kb-broll-head{display:flex;align-items:center;gap:8px;font-size:12px;color:#334155;}'
      + '#content-digital-human-koubo .kb-broll-head small{font-size:10px;color:#94a3b8;}'
      + '#content-digital-human-koubo .kb-broll-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:8px;font-size:11px;color:#475569;}'
      + '#content-digital-human-koubo .kb-broll-row label{display:inline-flex;align-items:center;gap:5px;}'
      + '#content-digital-human-koubo .kb-broll-row select,#content-digital-human-koubo .kb-broll-row input[type=number]{border:1px solid #e2e8f0;border-radius:7px;padding:5px 7px;font-size:11px;color:#334155;background:#fff;}'
      + '#content-digital-human-koubo .kb-broll-row input[type=number]{width:58px;}'
      + '#content-digital-human-koubo .kb-broll-name{font-size:11px;color:#64748b;max-width:190px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}'
      + '#content-digital-human-koubo .kb-broll-note{font-size:10px;color:#94a3b8;margin-top:6px;}'
      + '#content-digital-human-koubo .kb-tpl-bottom-actions{display:flex;gap:8px;margin-top:10px;}'
      + '#content-digital-human-koubo .kb-tpl-bottom-actions .kb-tpl-more{flex:1;margin-top:0;}'
      + '#kbSampleModal{position:fixed;inset:0;z-index:10050;background:rgba(5,9,18,.76);display:flex;align-items:center;justify-content:center;padding:24px;}'
      + '#kbSampleModal .kb-sample-card{width:min(92vw,520px);background:#0b1220;border-radius:18px;padding:12px;box-shadow:0 30px 80px rgba(0,0,0,.45);}'
      + '#kbSampleModal .kb-sample-head{display:flex;align-items:center;justify-content:space-between;color:#e2e8f0;font-size:12px;margin-bottom:8px;}'
      + '#kbSampleModal video{width:100%;max-height:78vh;border-radius:12px;background:#000;}'
      + '#kbSampleModal .kb-sample-close{border:1px solid rgba(255,255,255,.2);background:transparent;color:#e2e8f0;border-radius:999px;padding:4px 9px;font-size:11px;cursor:pointer;}'
      + '#content-digital-human-koubo .kb-tpl-preview:after{content:"";position:absolute;left:10%;right:10%;top:20%;height:12px;border-radius:6px;background:rgba(255,255,255,.82);box-shadow:0 10px 0 -2px rgba(255,255,255,.32);}'
      + '#content-digital-human-koubo .kb-tpl b{display:block;font-size:12px;color:#0f172a;margin-bottom:3px;}'
      + '#content-digital-human-koubo .kb-tpl p{font-size:11px;line-height:1.45;color:#64748b;margin:0 0 5px;min-height:31px;}'
      + '#content-digital-human-koubo .kb-tpl small{font-size:10px;color:#94a3b8;}'
      + '#content-digital-human-koubo .kb-tpl-tools{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:0 0 10px;}'
      + '#content-digital-human-koubo .kb-tpl-search{flex:1;min-width:130px;border:1px solid #e2e8f0;border-radius:999px;padding:6px 11px;font-size:11px;color:#0f172a;background:#fff;}'
      + '#content-digital-human-koubo .kb-tpl-chip{border:1px solid #e2e8f0;background:#fff;border-radius:999px;padding:4px 9px;font-size:10.5px;color:#475569;cursor:pointer;white-space:nowrap;}'
      + '#content-digital-human-koubo .kb-tpl-chip.on{background:#eff6ff;border-color:#93c5fd;color:#1d4ed8;}'
      + '#content-digital-human-koubo .kb-tpl-more{width:100%;border:1px dashed #bfdbfe;background:#f8fbff;color:#2563eb;border-radius:10px;padding:8px;font-size:11px;cursor:pointer;margin-top:10px;}'
      + '#content-digital-human-koubo .kb-visual-qa{display:flex;gap:8px;flex-wrap:wrap;align-items:flex-start;margin:8px 0 2px;padding:8px 10px;border-radius:10px;background:#f8fafc;border:1px solid #eef2f7;}'
      + '#content-digital-human-koubo .kb-visual-qa b{font-size:11px;color:#334155;white-space:nowrap;}'
      + '#content-digital-human-koubo .kb-visual-qa .kb-qa-pill{font-size:10.5px;padding:2px 7px;border-radius:999px;background:#e0f2fe;color:#0369a1;white-space:nowrap;}'
      + '#content-digital-human-koubo .kb-visual-qa .kb-qa-pill.warn{background:#fef3c7;color:#92400e;}'
      + '#content-digital-human-koubo .kb-visual-qa small{font-size:10.5px;color:#64748b;line-height:1.5;}'
      + '#content-digital-human-koubo .kb-title-input{display:flex;align-items:center;gap:8px;margin-bottom:10px;}'
      + '#content-digital-human-koubo .kb-title-input label{font-size:12px;color:#334155;white-space:nowrap;}'
      + '#content-digital-human-koubo .kb-title-input input{flex:1;min-width:0;border:1px solid #e2e8f0;border-radius:9px;padding:8px 10px;font-size:12px;color:#0f172a;background:#fff;}'
      + '#content-digital-human-koubo .kb-title-input small{font-size:10px;color:#94a3b8;white-space:nowrap;}'
      + '#content-digital-human-koubo .kb-recommend{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:0 0 10px;padding:8px 10px;background:#f8fbff;border:1px solid #e0f2fe;border-radius:10px;}'
      + '#content-digital-human-koubo .kb-recommend b{font-size:11px;color:#0369a1;white-space:nowrap;}'
      + '#content-digital-human-koubo .kb-rec-chip{border:1px solid #bae6fd;background:#fff;color:#0c4a6e;border-radius:999px;padding:4px 9px;font-size:10.5px;cursor:pointer;}'
      + '#content-digital-human-koubo .kb-rec-chip:hover{background:#e0f2fe;}'
      + '#content-digital-human-koubo #kbQuote,#content-digital-human-koubo #kbSave,#content-digital-human-koubo #kbExport{display:none;}'
      + '#content-digital-human-koubo .kb-log{max-height:52px;overflow:auto;}'
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
      + '  <div class="kb-card"><button type="button" class="kb-collapse" id="kbParamToggle">高级设置<span id="kbParamSummary">一般不用改</span></button><div class="kb-params" id="kbParams"></div></div>'
      + '  <div class="kb-cta"><button type="button" class="kb-btn-primary" id="kbGenerate">生成第 1 段</button>'
      + '    <div class="kb-cta-row"><button type="button" class="kb-btn" id="kbGenerateAll">生成全部</button></div>'
      + '    <div class="kb-hint">先出第 1 段确认效果，再生成全部</div></div>'
      + '</aside>'
      + '<section class="kb-stage">'
      + '  <div class="kb-toolbar"><div class="kb-crumb">工作台 / <b id="kbProjName">未命名项目</b></div>'
      + '    <div class="kb-tools"><button type="button" class="kb-btn" id="kbSave">保存项目</button><button type="button" class="kb-btn" id="kbExport">导出 tasks.json</button><button type="button" class="kb-btn" id="kbDeps">运行环境</button><button type="button" class="kb-btn" id="kbBack">返回商店</button></div></div>'
      + '  <div class="kb-preview" id="kbPreview"><div class="kb-empty"><b>还没有成片</b>左侧填好形象与口播稿，点击「生成第 1 段」</div></div>'
      + '  <div class="kb-meta" id="kbMeta"></div>'
      + '  <div id="kbPack"></div>'
      + '  <div class="kb-card"><div class="kb-sec-title">项目记录<span><small id="kbProjCount"></small> <button type="button" class="kb-btn" id="kbProjRefresh" style="margin-left:8px;">刷新</button></span></div><div id="kbProjects"></div></div>'
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
    if (rep) rep.onclick = function () { state.upload = null; markDirty(); renderAvatar(); };
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
      + '  <label class="kb-field">音色描述<input type="text" id="kbVoice" value="' + esc(f.voice || '') + '" placeholder="留空按照片自动匹配"></label>'
      + '</div>'
      + '<div style="font-size:10.5px;color:#94a3b8;margin-top:6px;line-height:1.5;">「白背心」是固定女性形象；上传自己的照片请选「照片本人（自动匹配）」，否则提示词会按女性生成。</div>'
      + '<label class="kb-field" id="kbSceneWrap" style="display:' + (isCustom ? 'flex' : 'none') + ';">场景描述<input type="text" id="kbScenePrompt" value="' + esc(f.scene_prompt || '') + '" placeholder="例如：夜晚霓虹街头，浅景深"></label>'
      + '<label style="display:flex;align-items:center;gap:6px;font-size:11px;color:#64748b;"><input type="checkbox" id="kbCompose"' + (f.compose_scene ? ' checked' : '') + '> 先合成场景图（只生成背景，人物用原图抠像；特写照片会像贴片，建议半身照）</label>'
      ;
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
    box.onchange = function (ev) {
      if (ev && ev.target && ev.target.id === 'kbHost') {
        var h = ((state.options && state.options.hosts) || []).filter(function (x) { return x.id === ($('kbHost') || {}).value; })[0];
        if (h && !(($('kbRatio') || {}).value)) { var sel = $('kbRatio'); if (sel) sel.value = h.ratio || ''; }
      }
      readParams();
      markDirty();
      var wrap = $('kbSceneWrap');
      if (wrap) wrap.style.display = state.form.scene === 'custom' ? 'flex' : 'none';
    };
    box.oninput = function () { readParams(); markDirty(); };
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

  function renderVisualQa(q) {
    if (!q) return '';
    var warnings = q.warnings || [];
    var ok = !!q.ok;
    var score = q.score || 0;
    return '<div class="kb-visual-qa"><b>画面质检</b><span class="kb-qa-pill' + (ok ? '' : ' warn') + '">' + (ok ? '通过' : '需注意') + ' · ' + score + ' 分</span>'
      + (warnings.length ? '<small>' + warnings.slice(0, 3).map(function (w) { return esc(w); }).join('；') + (warnings.length > 3 ? '…' : '') + '</small>' : '<small>黑帧、音轨、帧率、安全区均正常</small>')
      + '</div>';
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

  function loadTemplatePrefs() {
    try {
      var raw = localStorage.getItem('koubo_template_prefs_v1');
      var data = raw ? JSON.parse(raw) : {};
      state.render.recentTemplateIds = Array.isArray(data.recent) ? data.recent.slice(0, 12) : [];
      state.render.favoriteTemplateIds = Array.isArray(data.favorites) ? data.favorites.slice(0, 50) : [];
    } catch (e) {
      state.render.recentTemplateIds = [];
      state.render.favoriteTemplateIds = [];
    }
  }

  function saveTemplatePrefs() {
    try {
      localStorage.setItem('koubo_template_prefs_v1', JSON.stringify({
        recent: state.render.recentTemplateIds || [],
        favorites: state.render.favoriteTemplateIds || []
      }));
    } catch (e) {}
  }

  function touchTemplate(id) {
    if (!id) return;
    var list = (state.render.recentTemplateIds || []).filter(function (x) { return x !== id; });
    list.unshift(id);
    state.render.recentTemplateIds = list.slice(0, 12);
    saveTemplatePrefs();
  }

  function toggleFavoriteTemplate(id) {
    if (!id) return;
    var list = (state.render.favoriteTemplateIds || []).slice();
    var idx = list.indexOf(id);
    if (idx >= 0) list.splice(idx, 1);
    else list.unshift(id);
    state.render.favoriteTemplateIds = list.slice(0, 50);
    saveTemplatePrefs();
  }

  function renderBrollBlock() {
    var b = state.render.broll;
    var open = !!state.render.brollOpen;
    return '<div class="kb-broll"><div class="kb-broll-head"><b>插入素材</b><small>可选 · B-roll / 画中画</small><span style="flex:1"></span><button type="button" class="kb-btn" id="kbBrollToggle">' + (open ? '收起' : '展开') + '</button></div>'
      + '<div id="kbBrollBody" style="display:' + (open ? 'block' : 'none') + ';">'
      + '<div class="kb-broll-row"><button type="button" class="kb-btn" id="kbBrollPick">上传图片/视频</button><span class="kb-broll-name" id="kbBrollName">' + esc(b ? (b.name || b.asset_id || '已选素材') : '未选择素材') + '</span>' + (b ? '<button type="button" class="kb-btn" id="kbBrollRemove">移除</button>' : '') + '</div>'
      + '<div class="kb-broll-row"><label>方式<select id="kbBrollMode"><option value="pip"' + (b && b.mode === 'pip' ? ' selected' : '') + '>画中画</option><option value="full"' + (b && b.mode === 'full' ? ' selected' : '') + '>全屏插入</option></select></label><label>开始<input id="kbBrollStart" type="number" min="0" step="0.5" value="' + esc(b ? (b.start || 2) : 2) + '"> 秒</label><label>时长<input id="kbBrollDuration" type="number" min="0.8" step="0.5" value="' + esc(b ? (b.duration || 2.5) : 2.5) + '"> 秒</label></div>'
      + '<input type="file" id="kbBrollFile" accept="video/mp4,video/quicktime,video/webm,image/jpeg,image/png,image/webp" style="display:none;">'
      + '<div class="kb-broll-note">素材插在底片上，原口播声音不变；画中画放在右下角，全屏插入会暂时盖住人物。</div>'
      + '</div></div>';
  }

  function bindBrollHandlers() {
    var toggle = $('kbBrollToggle');
    if (toggle) toggle.onclick = function () { state.render.brollOpen = !state.render.brollOpen; renderPack(); };
    var pick = $('kbBrollPick');
    var file = $('kbBrollFile');
    if (pick && file) pick.onclick = function () { file.click(); };
    if (file) file.onchange = function () { if (file.files && file.files[0]) uploadBroll(file.files[0]); };
    var mode = $('kbBrollMode');
    if (mode) mode.onchange = function () { if (state.render.broll) state.render.broll.mode = mode.value; };
    var start = $('kbBrollStart');
    if (start) start.oninput = function () { if (state.render.broll) state.render.broll.start = Number(start.value || 2); };
    var dur = $('kbBrollDuration');
    if (dur) dur.oninput = function () { if (state.render.broll) state.render.broll.duration = Number(dur.value || 2.5); };
    var remove = $('kbBrollRemove');
    if (remove) remove.onclick = function () { state.render.broll = null; state.render.brollOpen = true; renderPack(); };
  }

  function uploadBroll(f) {
    var fd = new FormData();
    fd.append('file', f);
    log('上传插入素材 ' + f.name + '…');
    fetch(API + '/api/skills/digital-human-koubo/broll-uploads', { method: 'POST', headers: authOnly(), body: fd })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail)); return d; }); })
      .then(function (d) {
        state.render.broll = { asset_id: d.id, url: d.url, name: d.name || d.id, kind: d.kind || 'image', mode: 'pip', start: 2, duration: 2.5 };
        state.render.brollOpen = true;
        renderPack();
        log('插入素材已上传 ' + (d.name || d.id));
      })
      .catch(function (e) { log('上传插入素材失败：' + e.message); });
  }

  function openTemplateSample(url, name) {
    if (!url) return;
    var old = document.getElementById('kbSampleModal');
    if (old && old.parentNode) old.parentNode.removeChild(old);
    var src = String(url).indexOf('http') === 0 ? url : API + url;
    var modal = document.createElement('div');
    modal.id = 'kbSampleModal';
    modal.innerHTML = '<div class="kb-sample-card"><div class="kb-sample-head"><b>' + esc(name || '模板样片') + '</b><button type="button" class="kb-sample-close">关闭</button></div><video controls autoplay playsinline src="' + esc(src) + '"></video></div>';
    document.body.appendChild(modal);
    function close() {
      var v = modal.querySelector('video'); if (v) { try { v.pause(); } catch (e) {} }
      if (modal.parentNode) modal.parentNode.removeChild(modal);
    }
    modal.addEventListener('click', function (e) { if (e.target === modal) close(); });
    modal.querySelector('.kb-sample-close').onclick = close;
  }

  function selectTemplate(id) {
    if (!id) return;
    state.render.templateId = id;
    touchTemplate(id);
    state.render.packaged = false;
    state.render.previewReady = false;
    state.render.showPreview = false;
    state.render.qa = null;
    state.render.job = null;
    if (state.project) { state.project.rendered = false; state.project.preview_rendered = false; }
    renderPack(); renderPreview();
    var found = (state.render.templates || []).filter(function (t) { return t.id === id; })[0];
    log('已选模板：' + ((found && found.name) || id));
  }

  function loadRecommendations() {
    var text = (state.project && state.project.script) || '';
    text = String(text).trim();
    if (!text) { state.render.recommendations = []; state.render.recommendFor = ''; return; }
    var key = text.slice(0, 1200);
    if (state.render.recommendFor === key) return;
    state.render.recommendFor = key;
    req('/api/skills/digital-human-koubo/recommend-templates', {
      method: 'POST', headers: headers(), body: JSON.stringify({ text: text, limit: 3 })
    }).then(function (d) {
      state.render.recommendations = (d && d.items) || [];
      renderPack();
    }).catch(function () { state.render.recommendations = []; });
  }

  function templateCardHtml(t) {
    var r = state.render;
    var on = t.id === r.templateId;
    var colors = t.colors || ['#FFD84D', '#FF4D8D', '#4DE1FF'];
    var cover = t.cover ? (String(t.cover).indexOf('http') === 0 ? t.cover : API + t.cover) : '';
    var sample = t.sample_video_url || '';
    var visual = cover
      ? '<img class="kb-tpl-cover" src="' + esc(cover) + '" alt="">'
      : '<span class="kb-tpl-preview" style="background:linear-gradient(135deg,' + esc(colors[0] || '#FFD84D') + ',' + esc(colors[1] || '#FF4D8D') + ',' + esc(colors[2] || '#4DE1FF') + ')"></span>';
    var play = sample ? '<button type="button" class="kb-tpl-play" data-sample="' + esc(sample) + '" data-name="' + esc(t.name || '') + '">▶ 看样片</button>' : '';
    var fav = (state.render.favoriteTemplateIds || []).indexOf(t.id) >= 0;
    var favBtn = '<button type="button" class="kb-tpl-fav' + (fav ? ' on' : '') + '" data-fav="' + esc(t.id) + '" title="收藏">' + (fav ? '★' : '☆') + '</button>';
    return '<div class="kb-tpl' + (on ? ' on' : '') + '" data-tpl="' + esc(t.id) + '">'
      + '<div class="kb-tpl-coverwrap">' + visual + favBtn + play + '</div>'
      + '<b>' + esc(t.name || t.id) + '</b>'
      + '<p>' + esc(t.description || '') + '</p>'
      + '<small>' + esc(t.category || '') + (t.tags && t.tags.length ? ' · ' + esc((t.tags || []).slice(0, 2).join(' · ')) : '') + '</small>'
      + '</div>';
  }

  function paintTemplateGrid() {
    var grid = $('kbTplGrid');
    if (!grid) return;
    var r = state.render;
    var all = r.templates || [];
    var byId = {};
    all.forEach(function (t) { byId[t.id] = t; });
    var list = [];
    var cat = r.templateCategory || 'all';
    if (cat === 'recent') {
      list = (r.recentTemplateIds || []).map(function (id) { return byId[id]; }).filter(Boolean);
    } else if (cat === 'favorites') {
      list = (r.favoriteTemplateIds || []).map(function (id) { return byId[id]; }).filter(Boolean);
    } else {
      list = all.slice();
      if (!r.showAllTemplates) {
        var pageSize = 6;
        var page = Math.max(0, r.featuredPage || 0);
        var start = (page * pageSize) % Math.max(1, list.length);
        var paged = list.slice(start, start + pageSize);
        if (paged.length < pageSize) paged = paged.concat(list.slice(0, pageSize - paged.length));
        list = paged;
      }
      if (cat !== 'all') {
        list = list.filter(function (t) { return t.category === cat; });
      }
    }
    var q = (r.templateQuery || '').trim().toLowerCase();
    if (q) {
      list = list.filter(function (t) {
        return [t.name, t.description, t.category, (t.tags || []).join(' ')].join(' ').toLowerCase().indexOf(q) >= 0;
      });
    }
    var emptyText = cat === 'recent' ? '最近还没有用过模板' : (cat === 'favorites' ? '还没有收藏模板' : '没有匹配的模板');
    grid.innerHTML = list.map(templateCardHtml).join('') || '<span style="font-size:12px;color:#94a3b8;padding:10px 0;">' + emptyText + '</span>';
    grid.querySelectorAll('[data-tpl]').forEach(function (card) {
      card.onclick = function () { selectTemplate(card.getAttribute('data-tpl')); };
    });
    grid.querySelectorAll('[data-sample]').forEach(function (btn) {
      btn.onclick = function (e) {
        e.stopPropagation();
        openTemplateSample(btn.getAttribute('data-sample'), btn.getAttribute('data-name'));
      };
    });
    grid.querySelectorAll('[data-fav]').forEach(function (btn) {
      btn.onclick = function (e) {
        e.stopPropagation();
        toggleFavoriteTemplate(btn.getAttribute('data-fav'));
        renderPack();
      };
    });
  }

  function renderPack() {
    var box = $('kbPack');
    if (!box) return;
    if (!state.project || state.project.status !== 'generated') { box.innerHTML = ''; return; }
    var r = state.render;
    var job = r.job && r.job.status === 'running' ? r.job : null;
    var tpls = r.templates || [];
    var hasPreview = !!r.previewReady || !!(state.project && state.project.preview_rendered);
    var hasFinal = !!r.packaged || !!(state.project && state.project.rendered);
    var primaryMode = hasPreview ? 'final' : 'preview';
    var primaryLabel = job ? ('生成中 ' + (job.progress || 0) + '%') : (!hasPreview ? '预览效果' : (hasFinal ? '重新生成高清' : '生成高清成片'));
    var cats = [];
    tpls.forEach(function (t) { if (t.category && cats.indexOf(t.category) < 0) cats.push(t.category); });
    var chips = '<button type="button" class="kb-tpl-chip' + ((r.templateCategory || 'all') === 'all' ? ' on' : '') + '" data-cat="all">全部</button>'
      + '<button type="button" class="kb-tpl-chip' + (r.templateCategory === 'recent' ? ' on' : '') + '" data-cat="recent">最近</button>'
      + '<button type="button" class="kb-tpl-chip' + (r.templateCategory === 'favorites' ? ' on' : '') + '" data-cat="favorites">收藏</button>'
      + cats.map(function (c) { return '<button type="button" class="kb-tpl-chip' + (r.templateCategory === c ? ' on' : '') + '" data-cat="' + esc(c) + '">' + esc(c) + '</button>'; }).join('');
    box.innerHTML = '<div class="kb-card"><div class="kb-sec-title">选个模板，直接出片<small>' + (tpls.length || 0) + ' 个模板</small></div>'
      + '<div class="kb-title-input"><label>标题</label><input id="kbTemplateTitle" maxlength="24" placeholder="留空自动从口播稿提取" value="' + esc(r.title || '') + '"><small>可选</small></div>'
      + ((r.recommendations && r.recommendations.length) ? '<div class="kb-recommend"><b>推荐</b>' + r.recommendations.map(function (x) { return '<button type="button" class="kb-rec-chip" data-rec="' + esc(x.template_id) + '" title="' + esc(x.reason || '') + '">' + esc(x.name) + '</button>'; }).join('') + '</div>' : '')
      + '<div class="kb-packbar" style="margin-top:2px;">'
      + (hasFinal ? '<a class="kb-btn" href="' + esc(API + '/api/skills/digital-human-koubo/projects/' + state.project.id + '/rendered') + '" download>下载成片</a>' : '')
      + '<span style="flex:1"></span>'
      + (hasPreview && !job ? '<button type="button" class="kb-btn" id="kbPreviewAgain">重新预览</button>' : '')
      + '<button type="button" class="kb-btn-primary" id="kbRender" data-mode="' + primaryMode + '" style="width:auto;min-width:150px;"' + (job ? ' disabled' : '') + '>' + primaryLabel + '</button>'
      + '</div>'
      + '<div class="kb-hint" style="margin:6px 0 10px;">先看 480p 快速预览，满意再出高清；同一段底片换模板不会重新生成，也不会再扣底片算力。</div>'
      + (job ? '<div style="margin-bottom:10px;font-size:11px;color:#64748b;">' + esc(job.message || '') + '<div style="height:4px;background:#eef2f7;border-radius:999px;overflow:hidden;margin-top:4px;"><i style="display:block;height:100%;width:' + Math.max(3, job.progress || 0) + '%;background:linear-gradient(90deg,#3b82f6,#8b5cf6);"></i></div></div>' : '')
      + renderVisualQa(r.qa)
      + (r.showAllTemplates ? '<div class="kb-tpl-tools"><input id="kbTplSearch" class="kb-tpl-search" placeholder="搜模板、场景、标签" value="' + esc(r.templateQuery || '') + '">' + chips + '</div>' : '')
      + '<div class="kb-templates" id="kbTplGrid"></div>'
      + (tpls.length > 6 ? '<div class="kb-tpl-bottom-actions">'
          + (!r.showAllTemplates && (r.templateCategory || 'all') === 'all' && !(r.templateQuery || '').trim() ? '<button type="button" class="kb-tpl-more" id="kbTplRefresh">换一批</button>' : '')
          + '<button type="button" class="kb-tpl-more" id="kbTplMore">' + (r.showAllTemplates ? '收起，只看推荐' : '全部模板（' + tpls.length + '）') + '</button></div>' : '')
      + renderBrollBlock()
      + '<div class="kb-packbar" style="margin-top:10px;border-top:1px solid #f1f5f9;padding-top:10px;"><button type="button" class="kb-btn" id="kbQa">口播质检</button><span style="font-size:11px;color:#94a3b8;">回听比对文稿，低于 85% 建议重拍底片</span></div>'
      + (state.qa ? renderQaResult(state.qa) : '')
      + '</div>';
    paintTemplateGrid();
    box.querySelectorAll('[data-cat]').forEach(function (chip) {
      chip.onclick = function () {
        state.render.templateCategory = chip.getAttribute('data-cat') || 'all';
        state.render.featuredPage = 0;
        renderPack();
      };
    });
    var search = $('kbTplSearch');
    if (search) search.oninput = function () { state.render.templateQuery = search.value || ''; state.render.featuredPage = 0; paintTemplateGrid(); };
    var more = $('kbTplMore');
    if (more) more.onclick = function () { state.render.showAllTemplates = !state.render.showAllTemplates; if (!state.render.showAllTemplates) { state.render.templateCategory = 'all'; state.render.templateQuery = ''; state.render.featuredPage = 0; } renderPack(); };
    var refresh = $('kbTplRefresh');
    if (refresh) refresh.onclick = function () { state.render.featuredPage = (state.render.featuredPage || 0) + 1; renderPack(); };
    var titleInput = $('kbTemplateTitle');
    if (titleInput) titleInput.oninput = function () { state.render.title = titleInput.value || ''; };
    var btn = $('kbRender');
    if (btn) btn.onclick = function () { startRender(btn.getAttribute('data-mode') || 'preview'); };
    var again = $('kbPreviewAgain');
    if (again) again.onclick = function () { startRender('preview'); };
    box.querySelectorAll('[data-rec]').forEach(function (chip) {
      chip.onclick = function () { selectTemplate(chip.getAttribute('data-rec')); };
    });
    var qaBtn = $('kbQa');
    if (qaBtn) qaBtn.onclick = runQa;
    bindBrollHandlers();
    loadRecommendations();
  }

  function startRender(mode) {
    if (!state.project) return;
    state.render.runningMode = mode || 'preview';
    state.render.job = { status: 'running', progress: 0, message: state.render.runningMode === 'preview' ? '准备预览' : '准备生成高清' };
    renderPack();
    req('/api/skills/digital-human-koubo/projects/' + state.project.id + '/render', {
      method: 'POST', headers: headers(),
      body: JSON.stringify({ template_id: state.render.templateId, title: state.render.title || '', quality: state.render.runningMode, broll: state.render.broll ? { asset_id: state.render.broll.asset_id, mode: state.render.broll.mode || 'pip', start: Number(state.render.broll.start || 2), duration: Number(state.render.broll.duration || 2.5) } : null })
    }).then(function (d) {
      log((state.render.runningMode === 'preview' ? '预览任务已提交 ' : '高清任务已提交 ') + d.job_id);
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
            var mode = state.render.runningMode || 'final';
            if (mode === 'preview') { state.render.previewReady = true; state.render.showPreview = true; }
            else { state.render.packaged = true; state.render.showPreview = false; }
            state.render.job = null;
            state.render.qa = job.qa || null;
            log(mode === 'preview' ? '预览完成' : '高清成片完成');
            req('/api/skills/digital-human-koubo/projects/' + state.project.id).then(function (p) { state.project = p; renderAll(); });
          } else {
            log((state.render.runningMode === 'preview' ? '预览失败：' : '高清生成失败：') + (job.error || job.message || ''));
          }
        }
      }).catch(function () {});
    }, 1500);
  }

  function renderPreview() {
    var box = $('kbPreview');
    if (!box) return;
    var proj = state.preview || state.project;
    var isPreview = !!state.preview;
    if (state.job && !isPreview) {
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
    if (proj && proj.status === 'generated' && proj.has_video !== false) {
      var usePreview = !isPreview && state.render.showPreview && !!proj.preview_rendered;
      var packaged = isPreview ? !!proj.rendered : (!usePreview && (state.render.packaged || proj.rendered));
      var ver = usePreview ? (proj.preview_v || 0) : (packaged ? (proj.rendered_v || 0) : (proj.video_v || 0));
      var kind = usePreview ? '/preview' : (packaged ? '/rendered' : '/video');
      var src = API + '/api/skills/digital-human-koubo/projects/' + proj.id + kind + '?v=' + (ver || Date.now());
      var badge = usePreview ? '预览版 · 480p' : (packaged ? '高清成片' : '底片');
      box.innerHTML = '<video controls playsinline preload="metadata" src="' + esc(src) + '"></video>'
        + '<div style="position:absolute;left:14px;top:12px;background:rgba(15,23,42,.72);color:#e2e8f0;font-size:11px;padding:4px 9px;border-radius:999px;">'
        + (isPreview ? '历史预览：' : '当前项目：') + esc(proj.name) + '</div>'
        + (isPreview ? '<button type="button" class="kb-btn" id="kbPreviewBack" style="position:absolute;right:14px;top:10px;">返回当前</button>' : '')
        + '<div style="position:absolute;left:14px;bottom:12px;background:rgba(15,23,42,.72);color:#e2e8f0;font-size:11px;padding:4px 9px;border-radius:999px;">' + esc(badge) + '</div>'
        + ((packaged || usePreview) ? '<a href="' + esc(API + '/api/skills/digital-human-koubo/projects/' + proj.id + (usePreview ? '/preview' : '/rendered')) + '" target="_blank" style="position:absolute;right:14px;bottom:12px;color:#cbd5e1;font-size:11px;text-decoration:underline;">下载' + (usePreview ? '预览' : '成片') + '</a>' : '')
        + (packaged ? '<a href="' + esc(API + '/api/skills/digital-human-koubo/projects/' + proj.id + '/video') + '" target="_blank" style="position:absolute;right:90px;bottom:12px;color:#cbd5e1;font-size:11px;text-decoration:underline;">看原片</a>' : '');
      var vid = box.querySelector('video');
      if (vid) { try { vid.load(); } catch (e) {} }
      var back = $('kbPreviewBack');
      if (back) back.onclick = function () { state.preview = null; renderPreview(); renderProjects(); };
      return;
    }
    if (isPreview) {
      box.innerHTML = '<div class="kb-empty"><b>' + esc(proj.name) + '</b>这个项目还没有成片</div>'
        + '<button type="button" class="kb-btn" id="kbPreviewBack" style="position:absolute;right:14px;top:10px;">返回当前</button>';
      var back2 = $('kbPreviewBack');
      if (back2) back2.onclick = function () { state.preview = null; renderPreview(); renderProjects(); };
      return;
    }
    box.innerHTML = '<div class="kb-empty"><b>还没有成片</b>左侧填好形象与口播稿，点击「生成第 1 段」</div>';
  }

  function renderStatus() {
    var el = $('kbStatus');
    if (!el) return;
    if (state.job && state.job.status === 'running') { el.textContent = '生成中'; el.className = 'kb-pill warn'; return; }
    if (state.dirty) { el.textContent = '有未保存修改'; el.className = 'kb-pill warn'; return; }
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
    var activeId = (state.preview && state.preview.id) || (state.project && state.project.id);
    box.innerHTML = state.projects.map(function (p) {
      var st = p.status || 'ready';
      var badge = st === 'generated' ? (p.rendered ? '已包装' : '已出片') : (st === 'generating' ? '生成中' : '未生成');
      var cls = st === 'generated' ? 'ok' : (st === 'generating' ? 'warn' : '');
      var active = p.id === activeId;
      return '<div class="kb-proj" style="' + (active ? 'background:#f0f7ff;border-radius:8px;padding-left:8px;padding-right:8px;' : '') + '">'
        + '<div><b>' + esc(p.name) + '</b> <span class="kb-pill ' + cls + '" style="margin-left:6px;">' + badge + '</span><br>'
        + '<small>' + esc(String(p.created_at || '').replace('T', ' ')) + ' · ' + (p.segments_count || 0) + ' 段 · ' + (p.total_credits || 0) + ' credits' + (active ? ' · 当前' : '') + '</small></div>'
        + '<div style="display:flex;gap:6px;">'
        + (p.has_video ? '<button type="button" class="kb-btn" data-preview="' + esc(p.id) + '">预览</button>' : '')
        + '<button type="button" class="kb-btn" data-open="' + esc(p.id) + '">打开</button>'
        + '<button type="button" class="kb-btn" data-del="' + esc(p.id) + '">删除</button>'
        + '</div></div>';
    }).join('');
    box.querySelectorAll('[data-preview]').forEach(function (btn) {
      btn.onclick = function () { previewProject(btn.getAttribute('data-preview')); };
    });
    box.querySelectorAll('[data-open]').forEach(function (btn) {
      btn.onclick = function () { openProject(btn.getAttribute('data-open')); };
    });
    box.querySelectorAll('[data-del]').forEach(function (btn) {
      btn.onclick = function () {
        var pid = btn.getAttribute('data-del');
        if (!window.confirm('删除这个项目？')) return;
        fetch(API + '/api/skills/digital-human-koubo/projects/' + encodeURIComponent(pid), { method: 'DELETE', headers: authOnly() })
          .then(function () {
            log('已删除 ' + pid);
            if (state.preview && state.preview.id === pid) state.preview = null;
            if (state.project && state.project.id === pid) state.project = null;
            state.render.packaged = false;
            loadProjects(); renderPreview(); renderStatus();
          });
      };
    });
  }

  function previewProject(pid) {
    req('/api/skills/digital-human-koubo/projects/' + encodeURIComponent(pid)).then(function (p) {
      state.preview = p;
      renderPreview();
      renderProjects();
      log('预览历史项目 ' + p.name);
    }).catch(function (e) { log('预览失败：' + e.message); });
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

  function markDirty() {
    state.dirty = true;
    renderStatus();
  }

  function upload(f) {
    var fd = new FormData();
    fd.append('file', f);
    log('上传形象照 ' + f.name);
    fetch(API + '/api/skills/digital-human-koubo/uploads', { method: 'POST', headers: authOnly(), body: fd })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail)); return d; }); })
      .then(function (d) { state.upload = d; markDirty(); log('形象照已上传 ' + d.checks.width + '×' + d.checks.height); renderAvatar(); })
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
      state.dirty = false;
      state.render.packaged = false;
      state.render.job = null;
      state.qa = null;
      if ($('kbProjName')) $('kbProjName').textContent = p.name;
      renderStatus(); renderPreview(); renderProjects(); loadProjects();
      log('项目已保存 ' + p.id);
      return p;
    });
  }

  function generate(all) {
    state.preview = null;
    var btn = $('kbGenerate');
    if (btn) { btn.disabled = true; }
    var needsNewProject = !state.project || state.dirty;
    if (state.project && state.dirty) log('检测到形象/文案/参数已修改，先创建新项目…');
    var prep = needsNewProject ? quote(true).then(saveProject) : Promise.resolve(state.project);
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
        if (job.status === 'running') loadProjects();
        if (job.status !== 'running') {
          clearInterval(state.timer); state.timer = null;
          var btn = $('kbGenerate'); if (btn) btn.disabled = false;
          if (job.status === 'done') {
            log('生成完成');
            loadProjects();
            req('/api/skills/digital-human-koubo/projects/' + state.project.id).then(function (p) {
              state.project = p; state.render.packaged = !!p.rendered; renderAll();
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
      state.preview = null;
      state.project = p;
      state.dirty = false;
      state.script = p.script || '';
      state.form = Object.assign({}, state.form, p.options || {});
      state.quote = { segments: p.segments || [], total_seconds: p.total_seconds || 0, total_credits: p.total_credits || 0 };
      state.job = null;
      if (p.template_id) state.render.templateId = p.template_id;
      else if (p.preview_template_id) state.render.templateId = p.preview_template_id;
      state.render.title = p.template_title || p.preview_template_title || '';
      state.render.packaged = !!p.rendered;
      state.render.previewReady = !!p.preview_rendered;
      state.render.showPreview = !!p.preview_rendered && !p.rendered;
      state.render.qa = p.rendered ? (p.qa_final || null) : (p.qa_preview || null);
      state.render.broll = (p.broll && p.broll.asset_id) ? p.broll : null;
      state.render.brollOpen = !!state.render.broll;
      state.render.recommendFor = '';
      state.render.recommendations = [];
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

  function loadTemplates() {
    return req('/api/skills/digital-human-koubo/templates').then(function (d) {
      state.render.templates = (d && d.templates) || [];
      var has = state.render.templates.some(function (t) { return t.id === state.render.templateId; });
      if (!has) {
        var def = state.render.templates.filter(function (t) { return t.default; })[0] || state.render.templates[0];
        if (def) state.render.templateId = def.id;
      }
      renderPack();
      return state.render.templates;
    }).catch(function (e) { log('模板读取失败：' + e.message); });
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
    req('/api/skills/digital-human-koubo/projects/summary').then(function (d) {
      state.projects = d.projects || [];
      if (state.project && state.project.id) {
        var cur = state.projects.filter(function (x) { return x.id === state.project.id; })[0];
        if (cur) Object.assign(state.project, cur);
      }
      if (state.preview && state.preview.id) {
        var pv = state.projects.filter(function (x) { return x.id === state.preview.id; })[0];
        if (pv) Object.assign(state.preview, pv);
      }
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
    if (ta) ta.addEventListener('input', function () { updateCount(); markDirty(); });
    var tgl = $('kbParamToggle');
    if (tgl) tgl.onclick = function () { $('kbParams').classList.toggle('open'); };
    var q = $('kbQuote'); if (q) q.onclick = function () { quote(false); };
    var g = $('kbGenerate'); if (g) g.onclick = function () { generate(false); };
    var ga = $('kbGenerateAll'); if (ga) ga.onclick = function () { generate(true); };
    var sv = $('kbSave'); if (sv) sv.onclick = function () { quote(false).then(saveProject).catch(function () {}); };
    var ex = $('kbExport'); if (ex) ex.onclick = exportTasks;
    var dp = $('kbDeps'); if (dp) dp.onclick = openDeps;
    var pr = $('kbProjRefresh'); if (pr) pr.onclick = function () { loadProjects(); log('已刷新项目列表'); };
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
    var switched = false;
    try {
      if (typeof window._switchToHiddenView === 'function') { window._switchToHiddenView(VIEW_ID); switched = true; }
      else if (typeof showLobsterView === 'function') { showLobsterView(VIEW_ID); switched = true; }
    } catch (e) {
      console.warn('[koubo] 切换视图失败，改为直接显示容器:', e);
      switched = false;
    }
    if (!switched) {
      try {
        var root = document.getElementById('content-' + VIEW_ID);
        if (root) {
          document.querySelectorAll('.content-block').forEach(function (el) { el.style.display = 'none'; });
          root.style.display = '';
          root.classList.add('active');
        }
      } catch (e2) {}
    }
    ensureShell();
    loadOptions();
    loadTemplatePrefs();
    loadTemplates();
    loadProjects();
    log('进入数字人3.0 工作台');
  };

  document.addEventListener('DOMContentLoaded', function () {
    var root = document.getElementById('content-' + VIEW_ID);
    if (root && !root.dataset.kouboBuilt) { ensureShell(); }
  });
})();
